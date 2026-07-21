import copy
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests


HUB_REPO_DIR = Path(__file__).resolve().parents[2]
DEFAULT_LICENSE_FILES = [
    "LICENSE",
    "LICENSE.txt",
    "LICENSE.md",
    "COPYING",
    "COPYING.txt",
    "COPYING.md",
]
MAX_LICENSE_BYTES = 1024 * 1024
LICENSE_EVIDENCE_DIR = "license-evidence"


def generate_digest(file_path: str | Path) -> str:
    return f"sha256:{sha256_hex(file_path)}"


def sha256_hex(file_path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(file_path).open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_license_evidence(recipe, target_path: str | Path, runtime_results=None):
    source = recipe.get("source") or {}
    license_config = recipe.get("license") or {}
    target_path = Path(target_path)
    plugin_dir = target_path.parent
    evidence = {
        "spdx": license_config.get("spdx"),
        "available": False,
        "exact": False,
        "verified": False,
        "files": [],
    }

    source_checkout_evidence = collect_license_from_runtime_sources(
        runtime_results or {},
        target_path,
        license_candidates(license_config.get("files")),
        role="license",
    )
    if source_checkout_evidence.get("available"):
        evidence.update(source_checkout_evidence)

    source_header_evidence = collect_license_from_runtime_sources(
        runtime_results or {},
        plugin_dir / LICENSE_EVIDENCE_DIR,
        list(license_config.get("evidence_files") or []),
        role="source-header",
    )
    if source_header_evidence.get("files"):
        evidence["files"].extend(source_header_evidence["files"])
        evidence["available"] = True
        evidence["exact"] = True
        evidence["verified"] = True

    if evidence["available"]:
        return evidence

    if license_config.get("url"):
        url_evidence = download_declared_license_url(
            license_config,
            target_path,
            source=source,
        )
        return url_evidence | {"files": url_evidence.get("files", [])}

    source_repo = source.get("repo", "")
    source_ref = source.get("commit")
    parsed_repo = urlparse(source_repo)
    if parsed_repo.netloc in {"github.com", "www.github.com"} and source_ref:
        try:
            return download_github_license(
                source_repo,
                target_path,
                license_files=license_config.get("files"),
                ref=source_ref,
            ) | {"spdx": license_config.get("spdx")}
        except Exception as exc:
            evidence["reason"] = str(exc)

    if parsed_repo.netloc not in {"github.com", "www.github.com"}:
        evidence["reason"] = (
            "No exact build-source license, declared license URL, or exact GitHub "
            "source license is available."
        )
    elif not source_ref:
        evidence["reason"] = (
            "Recipe source does not declare an immutable commit to use for "
            "exact-version license fetch."
        )
    return evidence


def download_github_license(repo_url: str, target_path: str | Path, license_files=None, ref=None):
    parsed = urlparse(repo_url)
    parts = parsed.path.strip("/").split("/")
    if parsed.netloc not in {"github.com", "www.github.com"} or len(parts) < 2:
        raise ValueError("Invalid GitHub repo URL")
    owner, repo = parts[0], parts[1].removesuffix(".git")
    candidates = license_candidates(license_files)

    last_error = None
    for source_ref in [ref] if ref else []:
        for filename in candidates:
            if not is_safe_relative_path(filename):
                continue
            raw_url = (
                f"https://raw.githubusercontent.com/{owner}/{repo}/"
                f"{source_ref}/{filename}"
            )
            try:
                download = download_license_file(raw_url, target_path)
                digest = f"sha256:{download['sha256']}"
                source = {
                    "kind": "exact-github-source-ref",
                    "repo": repo_url,
                    "ref": source_ref,
                    "path": filename,
                    "url": raw_url,
                }
                return {
                    "available": True,
                    "exact": True,
                    "verified": True,
                    "source": source,
                    "path": "LICENSE",
                    "digest": digest,
                    "files": [
                        {
                            "role": "license",
                            "path": "LICENSE",
                            "digest": digest,
                            "exact": True,
                            "verified": True,
                            "source": source,
                        }
                    ],
                }
            except requests.HTTPError as exc:
                last_error = (
                    exc.response.status_code
                    if exc.response is not None
                    else str(exc)
                )
            except Exception as exc:
                last_error = str(exc)

    raise Exception(
        f"Failed to fetch license from exact source ref {ref!r} "
        f"(tried {candidates}): {last_error}"
    )


def collect_license_from_runtime_sources(runtime_results, target_path: str | Path, candidates, role="license"):
    copied_files = []
    for runtime, result in sorted(runtime_results.items(), key=runtime_license_priority):
        build_evidence = (result or {}).get("evidence") or {}
        source_evidence = build_evidence.get("source") or {}
        actual_source = source_evidence.get("actual") or {}
        source_dir = actual_source_directory(build_evidence, actual_source)
        if not source_dir:
            continue

        dirty_paths = dirty_status_paths(actual_source.get("dirty_files"))
        mutation = source_evidence.get("mutation") or {}
        for license_name in candidates:
            if not is_safe_relative_path(license_name):
                continue
            relative_license_path = Path(license_name)
            candidate = safe_child_path(source_dir, relative_license_path)
            if candidate is None:
                continue
            dirty_at_collection = (
                candidate.relative_to(Path(source_dir).resolve()) in dirty_paths
            )
            if dirty_at_collection and (
                role != "source-header" or not mutation.get("recorded")
            ):
                continue
            if not candidate.is_file() or candidate.is_symlink():
                continue

            target_file = license_target_path(
                target_path,
                relative_license_path,
                role,
            )
            target_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(candidate, target_file)
            evidence_path = evidence_relative_path_to_bundle(target_file)

            source = {
                "kind": "build-source-checkout",
                "runtime": runtime,
                "source_kind": actual_source.get("kind"),
                "path": relative_license_path.as_posix(),
                "copied_from": evidence_relative_path(candidate),
            }
            if actual_source.get("repo"):
                source["repo"] = actual_source.get("repo")
            if actual_source.get("resolved_commit"):
                source["ref"] = actual_source.get("resolved_commit")
            if actual_source.get("directory_digest"):
                source["directory_digest"] = actual_source.get("directory_digest")
            if dirty_at_collection:
                source["dirty_at_collection"] = True
                source["mutation_recorded"] = True
                if mutation.get("kind"):
                    source["mutation_kind"] = mutation.get("kind")
                if mutation.get("status_digest"):
                    source["mutation_status_digest"] = mutation.get("status_digest")
                if mutation.get("git_diff_digest"):
                    source["mutation_git_diff_digest"] = mutation.get("git_diff_digest")

            copied_files.append(
                {
                    "role": role,
                    "path": evidence_path,
                    "digest": generate_digest(target_file),
                    "exact": True,
                    "verified": True,
                    "source": source,
                }
            )
            if role == "license":
                return {
                    "available": True,
                    "exact": True,
                    "verified": True,
                    "source": source,
                    "path": evidence_path,
                    "digest": generate_digest(target_file),
                    "files": copied_files,
                }

    if copied_files:
        return {
            "available": True,
            "exact": True,
            "verified": True,
            "files": copied_files,
        }
    return {
        "available": False,
        "exact": False,
        "verified": False,
        "reason": "No configured license file was found in the exact build source checkout.",
        "files": [],
    }


def runtime_license_priority(item):
    runtime, _result = item
    priority = {"native": 0, "wasm": 1}
    return priority.get(runtime, 50), runtime


def download_declared_license_url(license_config, target_path: str | Path, source):
    evidence = {
        "spdx": license_config.get("spdx"),
        "available": False,
        "exact": False,
        "verified": False,
    }
    url = license_config.get("url")
    expected_sha256 = license_config.get("sha256")
    if not expected_sha256:
        evidence["reason"] = "License URL is declared but license.sha256 is missing."
        return evidence

    try:
        download = download_license_file(
            url,
            target_path,
            expected_sha256=expected_sha256,
        )
    except requests.RequestException as exc:
        evidence["reason"] = f"Failed to fetch declared license URL: {exc}"
        return evidence
    except ValueError as exc:
        evidence["reason"] = f"Declared license URL verification failed: {exc}"
        return evidence

    source_url = source.get("url")
    source_sha256 = source.get("sha256")
    is_source_file_evidence = bool(
        source_url
        and source_sha256
        and url == source_url
        and expected_sha256.lower() == source_sha256.lower()
    )
    source_kind = (
        "source-file-license-evidence"
        if is_source_file_evidence
        else "declared-license-url"
    )
    source_metadata = {
        "kind": source_kind,
        "url": url,
        "sha256": expected_sha256,
        "source_url": source_url,
        "source_sha256": source_sha256,
        "source_version": source.get("version"),
    }
    return {
        "spdx": license_config.get("spdx"),
        "available": True,
        "exact": is_source_file_evidence,
        "verified": True,
        "description": (
            "Exact source file containing the license notice"
            if is_source_file_evidence
            else "Hash-verified recipe-declared license evidence URL"
        ),
        "source": source_metadata,
        "path": "LICENSE",
        "digest": f"sha256:{download['sha256']}",
        "files": [
            {
                "role": "license",
                "path": "LICENSE",
                "digest": f"sha256:{download['sha256']}",
                "exact": is_source_file_evidence,
                "verified": True,
                "source": source_metadata.copy(),
            }
        ],
    }


def download_license_file(
    url: str,
    target_path: str | Path,
    expected_sha256: str | None = None,
):
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"unsupported license URL scheme: {parsed.scheme!r}")
    if expected_sha256 and not is_sha256_hex(expected_sha256):
        raise ValueError(f"invalid expected sha256 digest: {expected_sha256!r}")

    target_file = Path(target_path)
    target_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = target_file.with_name(f"{target_file.name}.{uuid.uuid4().hex}.tmp")
    digest = hashlib.sha256()
    total = 0

    try:
        with requests.get(url, timeout=30, stream=True) as response:
            response.raise_for_status()
            with tmp_file.open("wb") as file:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_LICENSE_BYTES:
                        raise ValueError(
                            f"license file exceeds {MAX_LICENSE_BYTES} bytes"
                        )
                    digest.update(chunk)
                    file.write(chunk)

        actual_sha256 = digest.hexdigest()
        if expected_sha256 and actual_sha256.lower() != expected_sha256.lower():
            raise ValueError(
                f"expected sha256:{expected_sha256}, got sha256:{actual_sha256}"
            )
        tmp_file.replace(target_file)
        return {"sha256": actual_sha256, "size": total}
    except Exception:
        if tmp_file.exists():
            tmp_file.unlink()
        raise


def is_sha256_hex(value):
    return isinstance(value, str) and bool(re.fullmatch(r"^[a-fA-F0-9]{64}$", value))


def is_sha256_digest(value):
    return (
        isinstance(value, str)
        and value.startswith("sha256:")
        and is_sha256_hex(value.removeprefix("sha256:"))
    )


def actual_source_directory(build_evidence, actual_source):
    actual_path = actual_source.get("path")
    if not actual_path:
        return None

    biowasm_dir = (build_evidence.get("biowasm") or {}).get("directory")
    if biowasm_dir:
        candidate = safe_child_path(Path(biowasm_dir), actual_path)
        if candidate and candidate.is_dir():
            return candidate

    if not is_safe_relative_path(actual_path):
        return None
    candidate = Path(actual_path)
    return candidate if candidate.is_dir() else None


def license_candidates(license_files=None):
    candidates = list(license_files or []) + DEFAULT_LICENSE_FILES
    return list(dict.fromkeys(candidates))


def license_target_path(target_path, relative_license_path, role):
    if role == "license":
        return Path(target_path)
    return Path(target_path) / relative_license_path


def evidence_relative_path_to_bundle(path):
    path = Path(path)
    if LICENSE_EVIDENCE_DIR in path.parts:
        evidence_index = path.parts.index(LICENSE_EVIDENCE_DIR)
        return Path(*path.parts[evidence_index:]).as_posix()
    return path.name


def is_safe_relative_path(path):
    if not isinstance(path, (str, os.PathLike)):
        return False
    path_text = os.fspath(path)
    candidate = Path(path_text)
    return (
        "\\" not in path_text
        and not candidate.is_absolute()
        and ".." not in candidate.parts
    )


def safe_child_path(base, relative_path):
    try:
        if not is_safe_relative_path(relative_path):
            return None
        base_path = Path(base).resolve()
        candidate = (base_path / relative_path).resolve()
        candidate.relative_to(base_path)
        return candidate
    except (OSError, ValueError):
        return None


def dirty_status_paths(status):
    paths = set()
    for line in (status or "").splitlines():
        if not line:
            continue
        path = line[3:] if len(line) > 3 else ""
        if path:
            paths.add(Path(path))
    return paths


def command_output(args):
    try:
        result = subprocess.run(args, check=True, capture_output=True, text=True)
        return (result.stdout or result.stderr).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def emsdk_commit():
    emsdk_dir = os.getenv("EMSDK")
    if not emsdk_dir:
        return None
    return git_output(["-C", emsdk_dir, "rev-parse", "HEAD"])


def collect_biowasm_source_evidence(tool_name, version, biowasm_evidence):
    source_dir = Path("tools") / tool_name / "src"
    evidence = {
        "biowasm_package": tool_name,
        "biowasm_version": version,
        "biowasm_version_branch": _biowasm_version_branch(tool_name, version),
        "actual": {
            "path": source_dir.as_posix(),
        },
    }

    if not source_dir.is_dir():
        evidence["actual"].update(
            {
                "kind": "missing",
                "reason": "BioWASM source directory does not exist after build.",
            }
        )
        return evidence

    source_root = git_output(
        ["-C", source_dir.as_posix(), "rev-parse", "--show-toplevel"]
    )
    biowasm_root = git_output(["rev-parse", "--show-toplevel"])

    if (
        source_root
        and biowasm_root
        and os.path.abspath(source_root) != os.path.abspath(biowasm_root)
    ):
        status = git_output(["-C", source_dir.as_posix(), "status", "--short"])
        evidence["actual"].update(
            {
                "kind": "git",
                "repo": git_output(
                    [
                        "-C",
                        source_dir.as_posix(),
                        "config",
                        "--get",
                        "remote.origin.url",
                    ]
                ),
                "resolved_commit": git_output(
                    ["-C", source_dir.as_posix(), "rev-parse", "HEAD"]
                ),
                "dirty": bool(status),
                "dirty_files": status,
            }
        )
        if status:
            evidence["mutation"] = _git_source_mutation_evidence(
                tool_name=tool_name,
                version=version,
                source_dir=source_dir,
                status=status,
                biowasm_evidence=biowasm_evidence,
            )
        return evidence

    file_hashes = _directory_hashes(source_dir)
    evidence["actual"].update(
        {
            "kind": "vendored",
            "directory_digest": _directory_digest(file_hashes),
            "files": file_hashes,
            "dirty": False,
            "dirty_files": "",
        }
    )
    return evidence


def collect_biowasm_config_evidence(tool_name, version):
    config_path = Path("biowasm.json")
    evidence = _file_evidence(config_path)
    if not config_path.is_file():
        return evidence
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        evidence["error"] = str(exc)
        return evidence
    selected_tool, selected_version = _select_biowasm_tool_version(
        config, tool_name, version
    )
    if selected_tool:
        evidence["selected_tool_digest"] = canonical_digest(selected_tool)
    if selected_version:
        evidence["selected_version"] = selected_version
        evidence["selected_version_digest"] = canonical_digest(selected_version)
    return evidence


def collect_biowasm_script_evidence(tool_name, version=None):
    paths = [
        Path("bin") / "compile.py",
        Path("bin") / "compile.sh",
        Path("bin") / "shared.sh",
        Path("tools") / tool_name / "compile.sh",
        Path("tools") / tool_name / "compile.py",
    ]
    for dependency in _biowasm_dependency_tools(tool_name, version):
        paths.extend(
            [
                Path("tools") / dependency / "compile.sh",
                Path("tools") / dependency / "compile.py",
            ]
        )
    return [
        entry
        for entry in (_file_evidence(path) for path in paths)
        if entry.get("exists")
    ]


def _git_source_mutation_evidence(tool_name, version, source_dir, status, biowasm_evidence):
    status_entries = _status_entries(status)
    mutation = {
        "kind": "recorded-build-transformation",
        "status": status,
        "status_entries": status_entries,
        "status_digest": canonical_digest(status_entries),
        "git_diff_digest": _git_output_digest(
            ["-C", source_dir.as_posix(), "diff", "--binary", "HEAD", "--"]
        ),
        "untracked_files": _untracked_file_evidence(source_dir, status_entries),
        "patches": _patch_evidence(tool_name, version),
        "biowasm_config_digest": (biowasm_evidence.get("configuration") or {}).get(
            "selected_version_digest"
        ),
        "biowasm_scripts": biowasm_evidence.get("scripts") or [],
    }
    mutation["recorded"] = bool(
        mutation["status_digest"]
        and (
            mutation["git_diff_digest"]
            or mutation["untracked_files"]
            or mutation["patches"]
        )
        and mutation["biowasm_config_digest"]
        and mutation["biowasm_scripts"]
    )
    return mutation


def _patch_evidence(tool_name, version):
    tool_root = Path("tools") / tool_name
    return [_file_evidence(path) for path in _patch_files(tool_root / "patches")]


def _patch_files(directory):
    if not directory.is_dir():
        return []
    patches = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        if path.suffix.lower() in {".patch", ".diff"}:
            patches.append(path)
    return patches


def _biowasm_version_branch(tool_name, version):
    config_path = Path("biowasm.json")
    if not config_path.is_file():
        return None
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    _selected_tool, selected_version = _select_biowasm_tool_version(
        config, tool_name, version
    )
    return selected_version.get("branch") if selected_version else None


def _biowasm_dependency_tools(tool_name, version):
    if version is None:
        return []
    config_path = Path("biowasm.json")
    if not config_path.is_file():
        return []
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    dependencies = []
    pending = [(tool_name, version)]
    seen = set()
    while pending:
        current_tool, current_version = pending.pop()
        key = (str(current_tool), str(current_version))
        if key in seen:
            continue
        seen.add(key)

        _selected_tool, selected_version = _select_biowasm_tool_version(
            config, current_tool, current_version
        )
        for dependency in (selected_version or {}).get("dependencies", []):
            dependency_name = dependency.get("name")
            dependency_version = dependency.get("version")
            if not dependency_name or dependency_version is None:
                continue
            dependency_key = (str(dependency_name), str(dependency_version))
            if dependency_key not in seen:
                dependencies.append(str(dependency_name))
                pending.append(dependency_key)
    return dependencies


def _select_biowasm_tool_version(config, tool_name, version):
    selected_tool = None
    selected_version = None
    for tool in config.get("tools", []):
        if tool.get("name") != tool_name:
            continue
        selected_tool = tool
        for version_info in tool.get("versions", []):
            if str(version_info.get("version")) == str(version):
                selected_version = version_info
                break
        break
    return selected_tool, selected_version


def _status_entries(status):
    entries = []
    for line in (status or "").splitlines():
        if not line:
            continue
        if len(line) >= 3 and line[2] == " ":
            status_code = line[:2]
            path = line[3:]
        elif len(line) >= 2 and line[1] == " ":
            status_code = line[:1]
            path = line[2:]
        else:
            status_code = line[:2]
            path = line[3:] if len(line) > 3 else ""
        entries.append(
            {
                "status": status_code,
                "path": path,
            }
        )
    return entries


def _untracked_file_evidence(source_dir, status_entries):
    evidence = []
    source_root = source_dir.resolve()
    for entry in status_entries:
        if entry.get("status") != "??":
            continue
        entry_path = entry.get("path", "")
        candidate = (source_dir / entry_path).resolve()
        try:
            candidate.relative_to(source_root)
        except ValueError:
            evidence.append({"path": entry_path, "kind": "unsafe"})
            continue
        if candidate.is_symlink():
            evidence.append({"path": entry_path, "kind": "symlink"})
        elif candidate.is_file():
            evidence.append(
                {"path": entry_path, "kind": "file", "sha256": sha256_hex(candidate)}
            )
        elif candidate.is_dir():
            file_hashes = _directory_hashes(candidate)
            evidence.append(
                {
                    "path": entry_path,
                    "kind": "directory",
                    "directory_digest": _directory_digest(file_hashes),
                    "files": file_hashes,
                }
            )
        else:
            evidence.append({"path": entry_path, "kind": "missing"})
    return evidence


def _file_evidence(path):
    evidence = {
        "path": path.as_posix(),
        "exists": path.is_file() and not path.is_symlink(),
    }
    if evidence["exists"]:
        evidence["sha256"] = sha256_hex(path)
    return evidence


def _git_output_digest(args):
    try:
        result = subprocess.run(["git", *args], check=True, capture_output=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return f"sha256:{hashlib.sha256(result.stdout).hexdigest()}"


def _directory_hashes(source_dir):
    hashes = []
    source_root = source_dir.resolve()
    for path in sorted(source_dir.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        resolved_path = path.resolve()
        try:
            relative_path = resolved_path.relative_to(source_root)
        except ValueError:
            continue
        hashes.append(
            {
                "path": relative_path.as_posix(),
                "sha256": sha256_hex(resolved_path),
            }
        )
    return hashes


def _directory_digest(file_hashes):
    digest = hashlib.sha256()
    for entry in file_hashes:
        digest.update(entry["path"].encode("utf-8"))
        digest.update(b"\0")
        digest.update(entry["sha256"].encode("ascii"))
        digest.update(b"\n")
    return f"sha256:{digest.hexdigest()}"


def build_bundle_evidence(recipe_path, recipe, operation, runtime_results, runtime_artifacts, license_evidence):
    hub_status = git_output(["status", "--short"], cwd=HUB_REPO_DIR)
    return {
        "schema": "biochef.build-evidence.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "hub": {
            "worktree": evidence_relative_path(HUB_REPO_DIR),
            "commit": git_output(["rev-parse", "HEAD"], cwd=HUB_REPO_DIR),
            "dirty": bool(hub_status),
            "dirty_files": hub_status,
        },
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "recipe": {
            "path": evidence_relative_path(recipe_path),
            "digest": generate_digest(recipe_path),
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
                "build": operation_build_evidence(
                    runtime_results[runtime].get("evidence", {}),
                    runtime,
                    operation,
                ),
                "artifacts": runtime_artifacts.get(runtime, {}),
            }
            for runtime in runtime_results
        },
    }


def operation_build_evidence(build_evidence, runtime, operation):
    evidence = copy.deepcopy(build_evidence)
    link_key = "wasm_link" if runtime == "wasm" else None
    if not link_key or not isinstance(evidence.get(link_key), dict):
        return evidence

    link = evidence[link_key]
    output_name = f"{operation.get('bin')}.wasm"
    outputs = [
        item
        for item in link.get("outputs", [])
        if item.get("output") == output_name
    ]
    link["expected_outputs"] = [output_name]

    link["outputs"] = outputs
    link["errors"] = [
        message
        for message in link.get("errors", [])
        if message.startswith(f"{output_name}:")
    ]
    if len(outputs) != 1:
        link["errors"].append(
            f"expected one structured linker record for {output_name}, "
            f"found {len(outputs)}"
        )
    link["complete"] = len(outputs) == 1 and bool(outputs[0].get("complete"))
    return evidence


def canonical_digest(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def evidence_relative_path(path):
    return os.path.relpath(Path(path).resolve(), Path.cwd().resolve())


def git_output(args, cwd=None):
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
