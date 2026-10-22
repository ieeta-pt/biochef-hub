import copy
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

from builders.container import canonical_digest, file_digest, is_sha256_hex, safe_child_path


HUB_REPO_DIR = Path(__file__).resolve().parents[2]
DEFAULT_LICENSE_FILES = (
    "LICENSE",
    "LICENSE.txt",
    "LICENSE.md",
    "COPYING",
    "COPYING.txt",
    "COPYING.md",
)
MAX_LICENSE_BYTES = 1024 * 1024


def license_candidates(configured=None):
    return list(dict.fromkeys([*(configured or []), *DEFAULT_LICENSE_FILES]))


def command_output(command, cwd=None):
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
        return (result.stdout or result.stderr).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def git_output(arguments, cwd=None):
    return command_output(["git", *arguments], cwd=cwd)


def emsdk_commit():
    directory = os.getenv("EMSDK")
    return git_output(["-C", directory, "rev-parse", "HEAD"]) if directory else None


def directory_tree_digest(root):
    root = Path(root).resolve()
    records = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if ".git" in relative.parts or path.is_symlink() or not path.is_file():
            continue
        records.append(
            {
                "path": relative.as_posix(),
                "digest": file_digest(path),
            }
        )
    return canonical_digest(records), len(records)


def git_source_evidence(source_dir, repo, tag=None, commit=None):
    resolved = git_output(["-C", str(source_dir), "rev-parse", "HEAD"])
    if not resolved:
        raise RuntimeError("Failed to resolve Git source commit")
    if commit and resolved != commit:
        raise RuntimeError(
            f"Resolved commit {resolved} does not match requested {commit}"
        )
    status = git_output(["-C", str(source_dir), "status", "--short"])
    tree_digest, file_count = directory_tree_digest(source_dir)
    return {
        "declared": {
            "repo": repo,
            "tag": tag,
            "commit": commit,
        },
        "actual": {
            "kind": "git",
            "repo": git_output(
                ["-C", str(source_dir), "config", "--get", "remote.origin.url"]
            ),
            "commit": resolved,
            "dirty": bool(status),
            "final_tree_digest": tree_digest,
            "file_count": file_count,
        },
    }


def collect_git_dependencies(source_dir):
    result = git_output(
        ["-C", str(source_dir), "submodule", "status", "--recursive"]
    )
    dependencies = []
    for line in (result or "").splitlines():
        fields = line.lstrip(" +-U").split()
        if len(fields) < 2:
            continue
        path = Path(source_dir) / fields[1]
        dependencies.append(
            {
                "name": path.name,
                "path": fields[1],
                "source": {
                    "kind": "git",
                    "repo": git_output(
                        ["-C", str(path), "config", "--get", "remote.origin.url"]
                    ),
                    "commit": fields[0],
                },
            }
        )
    return dependencies


def collect_license_evidence(recipe, target_path, runtime_results=None):
    config = recipe.get("license") or {}
    license_paths = list(config.get("files") or DEFAULT_LICENSE_FILES)
    header_paths = list(config.get("evidence_files") or [])
    target_path = Path(target_path)
    files = []

    for runtime, result in sorted((runtime_results or {}).items()):
        evidence = (result or {}).get("evidence") or {}
        output_dir = (result or {}).get("output_dir")
        for item in evidence.get("license_files") or []:
            role = item.get("role")
            source_path = item.get("source_path")
            if any(
                existing["role"] == role
                and existing["source_path"] == source_path
                for existing in files
            ):
                continue
            if role == "license" and source_path not in license_paths:
                continue
            if role == "source-header" and source_path not in header_paths:
                continue
            local_path = item.get("local_path")
            if not output_dir or not local_path:
                continue
            source_file = safe_child_path(output_dir, local_path, "license evidence")
            if (
                source_file.is_symlink()
                or not source_file.is_file()
                or file_digest(source_file) != item.get("digest")
            ):
                continue
            if role == "license":
                destination = target_path
            else:
                destination = (
                    target_path.parent
                    / "license-evidence"
                    / Path(source_path)
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_file, destination)
            files.append(
                {
                    "role": role,
                    "path": destination.relative_to(target_path.parent).as_posix(),
                    "digest": file_digest(destination),
                    "source_path": source_path,
                    "runtime": runtime,
                    "source": item.get("source"),
                }
            )

        source_dir = (result or {}).get("source_dir")
        if source_dir:
            source_root = Path(source_dir).resolve()
            source_identity = (evidence.get("source") or {}).get("actual") or (
                evidence.get("source") or {}
            )
            requested = (
                ("license", license_paths),
                ("source-header", header_paths),
            )
            for role, candidates in requested:
                if role == "license" and any(
                    item["role"] == "license" for item in files
                ):
                    continue
                for relative in candidates:
                    content = source_file_content(
                        source_root,
                        relative,
                        source_identity,
                    )
                    if content is None:
                        continue
                    destination = (
                        target_path
                        if role == "license"
                        else target_path.parent / "license-evidence" / relative
                    )
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(content)
                    files.append(
                        {
                            "role": role,
                            "path": destination.relative_to(
                                target_path.parent
                            ).as_posix(),
                            "digest": file_digest(destination),
                            "source_path": relative,
                            "runtime": runtime,
                            "source": source_identity,
                        }
                    )
                    if role == "license":
                        break

    if not any(item["role"] == "license" for item in files) and config.get("url"):
        files.append(download_declared_license(config, target_path))

    missing = {}
    has_license_file = any(item["role"] == "license" for item in files)
    if not has_license_file:
        missing["license"] = license_paths
    missing_headers = [
        path
        for path in header_paths
        if not any(
            item["role"] == "source-header" and item["source_path"] == path
            for item in files
        )
    ]
    if missing_headers:
        missing["source-header"] = missing_headers
    elif (
        header_paths
        and not has_license_file
        and not config.get("files")
        and not config.get("url")
    ):
        # A hash-pinned single-file source may carry its declared license notice
        # in the declared source header instead of shipping a separate license file.
        missing.pop("license", None)
    return {
        "spdx": config.get("spdx"),
        "verified": bool(files) and not missing,
        "files": files,
        "missing": missing,
    }


def source_file_content(source_root, relative, source_identity):
    if (source_identity or {}).get("kind") == "git":
        try:
            result = subprocess.run(
                ["git", "-C", str(source_root), "show", f"HEAD:{relative}"],
                check=True,
                capture_output=True,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return result.stdout if len(result.stdout) <= MAX_LICENSE_BYTES else None

    candidate = safe_child_path(source_root, relative, "license evidence")
    if (
        candidate.is_symlink()
        or not candidate.is_file()
        or candidate.stat().st_size > MAX_LICENSE_BYTES
    ):
        return None
    return candidate.read_bytes()


def download_declared_license(config, target):
    expected = config.get("sha256")
    if not is_sha256_hex(expected):
        raise RuntimeError("license.url requires a valid license.sha256")
    parsed = urlparse(config["url"])
    if parsed.scheme not in {"http", "https"}:
        raise RuntimeError("license.url must use HTTP or HTTPS")

    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
    digest = hashlib.sha256()
    size = 0
    try:
        with requests.get(config["url"], timeout=30, stream=True) as response:
            response.raise_for_status()
            with temporary.open("wb") as file:
                for chunk in response.iter_content(64 * 1024):
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > MAX_LICENSE_BYTES:
                        raise RuntimeError("license evidence exceeds the size limit")
                    digest.update(chunk)
                    file.write(chunk)
        if digest.hexdigest().lower() != expected.lower():
            raise RuntimeError("downloaded license evidence digest does not match")
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {
        "role": "license",
        "path": target.name,
        "source_path": config["url"],
        "digest": file_digest(target),
        "source": {
            "kind": "declared-url",
            "url": config["url"],
            "sha256": expected.lower(),
        },
    }


def build_bundle_evidence(
    recipe_path,
    recipe,
    operation,
    runtime_results,
    runtime_artifacts,
    license_evidence,
):
    hub_status = git_output(["status", "--short"], cwd=HUB_REPO_DIR)
    return {
        "schema": "biochef.build-evidence.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "hub": {
            "commit": git_output(["rev-parse", "HEAD"], cwd=HUB_REPO_DIR),
            "dirty": bool(hub_status),
        },
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "recipe": {
            "path": os.path.relpath(Path(recipe_path).resolve(), Path.cwd()),
            "digest": file_digest(recipe_path),
            "id": recipe.get("id"),
            "name": recipe.get("name"),
            "version": recipe.get("version"),
            "source": recipe.get("source"),
            "build": recipe.get("build"),
        },
        "operation": {
            "id": operation.get("id"),
            "bin": operation.get("bin"),
            "digest": canonical_digest(operation),
        },
        "license": license_evidence,
        "runtimes": {
            runtime: {
                "build": copy.deepcopy(result.get("evidence") or {}),
                "artifacts": runtime_artifacts.get(runtime) or {},
            }
            for runtime, result in runtime_results.items()
        },
    }
