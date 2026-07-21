import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path
from typing import Any, Iterable

from builders.bundle_evidence import sha256_hex


BUILD_INPUTS_SCHEMA = "biochef.build-inputs.v1"

_GIT_COMMIT = re.compile(r"^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")


class BuildInputError(RuntimeError):
    pass


def collect_build_inputs(*, source_dir: str | Path, excluded_submodules: Iterable[str] = ()) -> dict[str, Any]:
    return {
        "schema": BUILD_INPUTS_SCHEMA,
        "git_submodules": collect_git_submodules(
            source_dir,
            exclude_paths=excluded_submodules,
        ),
    }


def collect_git_submodules(source_dir: str | Path, *, exclude_paths: Iterable[str] = ()) -> list[dict[str, Any]]:
    root = Path(source_dir).resolve()
    if not root.is_dir():
        raise BuildInputError(f"source checkout does not exist: {root}")

    top_level = _git(root, "rev-parse", "--show-toplevel")
    if not top_level or Path(top_level).resolve() != root:
        raise BuildInputError(
            f"refusing to collect submodules from a non-root git directory: {root}"
        )
    if not (root / ".gitmodules").is_file():
        return []

    exclusions = set(exclude_paths)
    if any(not _safe_relative_path(path) for path in exclusions):
        raise BuildInputError("submodule exclusions must be safe relative paths")

    entries = []
    status = _git(root, "submodule", "status", "--recursive", raw=True)
    for line in (status or "").splitlines():
        match = re.match(
            r"^([ +\-U])([0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?)\s+(\S+)(?:\s+.*)?$",
            line,
        )
        if not match:
            raise BuildInputError(f"could not parse recursive submodule status: {line!r}")

        state, status_commit, relative_path = match.groups()
        if state == "-":
            # An uninitialised gitlink was not fetched during this build.
            continue
        if relative_path in exclusions:
            continue
        if state == "U":
            raise BuildInputError(f"submodule has an unresolved conflict: {relative_path}")
        if not _safe_relative_path(relative_path):
            raise BuildInputError(f"unsafe submodule path: {relative_path!r}")

        checkout = (root / relative_path).resolve()
        try:
            checkout.relative_to(root)
        except ValueError as exc:
            raise BuildInputError(
                f"submodule escapes source checkout: {relative_path}"
            ) from exc
        if not checkout.is_dir():
            raise BuildInputError(
                f"initialised submodule checkout is missing: {relative_path}"
            )

        resolved_commit = _git(checkout, "rev-parse", "HEAD")
        repo = _git(checkout, "config", "--get", "remote.origin.url")
        if not repo or not _valid_git_commit(resolved_commit):
            raise BuildInputError(f"submodule identity is incomplete: {relative_path}")
        if resolved_commit != status_commit:
            raise BuildInputError(
                f"submodule status changed while evidence was collected: {relative_path}"
            )

        dirty_status = (_git(checkout, "status", "--short", raw=True) or "").splitlines()
        tree_digest, tree_entries = _directory_tree_digest(checkout)
        entries.append(
            {
                "path": relative_path,
                "repo": repo,
                "resolved_commit": resolved_commit,
                "gitlink_match": state == " ",
                "post_build_dirty": bool(dirty_status),
                "post_build_status": dirty_status,
                "post_build_tree_sha256": tree_digest,
                "post_build_tree_entries": tree_entries,
            }
        )

    return sorted(entries, key=lambda item: item["path"])


def validate_build_inputs(document: Any) -> list[str]:
    if not isinstance(document, dict):
        return ["build input evidence is missing"]

    errors = []
    if document.get("schema") != BUILD_INPUTS_SCHEMA:
        errors.append("build input evidence has an unsupported or missing schema")

    submodules = document.get("git_submodules")
    if not isinstance(submodules, list):
        errors.append("git_submodules must be a list")
        submodules = []

    seen_paths = set()
    for item in submodules:
        path = item.get("path") if isinstance(item, dict) else None
        if not _safe_relative_path(path) or path in seen_paths:
            errors.append(f"invalid or duplicate submodule path: {path!r}")
            continue
        seen_paths.add(path)
        if not isinstance(item.get("repo"), str) or not item["repo"]:
            errors.append(f"submodule {path!r} is missing its repository")
        if not _valid_git_commit(item.get("resolved_commit")):
            errors.append(f"submodule {path!r} is missing an exact commit")
        if not isinstance(item.get("gitlink_match"), bool):
            errors.append(f"submodule {path!r} is missing its gitlink alignment state")
        if not isinstance(item.get("post_build_dirty"), bool):
            errors.append(f"submodule {path!r} is missing its post-build dirty state")
        status = item.get("post_build_status")
        if not isinstance(status, list) or not all(
            isinstance(line, str) for line in status
        ):
            errors.append(f"submodule {path!r} has invalid post-build status evidence")
        if not _valid_sha256(item.get("post_build_tree_sha256")):
            errors.append(f"submodule {path!r} is missing its post-build tree digest")
        tree_entries = item.get("post_build_tree_entries")
        if (
            not isinstance(tree_entries, int)
            or isinstance(tree_entries, bool)
            or tree_entries < 0
        ):
            errors.append(f"submodule {path!r} has an invalid post-build tree entry count")

    return errors


def _git(cwd: Path, *args: str, raw: bool = False) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BuildInputError(
            f"git {' '.join(args)} failed for {cwd}: {exc}"
        ) from exc
    return result.stdout if raw else result.stdout.strip()


def _directory_tree_digest(root: Path) -> tuple[str, int]:
    records = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root)
        if ".git" in relative.parts:
            continue
        relative_path = relative.as_posix()
        mode = format(stat.S_IMODE(path.lstat().st_mode), "04o")
        if path.is_symlink():
            records.append(
                {
                    "kind": "symlink",
                    "mode": mode,
                    "path": relative_path,
                    "target": os.readlink(path),
                }
            )
        elif path.is_file():
            records.append(
                {
                    "kind": "file",
                    "mode": mode,
                    "path": relative_path,
                    "sha256": sha256_hex(path),
                }
            )
        elif path.is_dir():
            records.append(
                {
                    "kind": "directory",
                    "mode": mode,
                    "path": relative_path,
                }
            )
        else:
            raise BuildInputError(f"unsupported special file in submodule tree: {relative_path}")

    payload = json.dumps(records, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest(), len(records)


def _valid_git_commit(value: Any) -> bool:
    return isinstance(value, str) and bool(_GIT_COMMIT.fullmatch(value))


def _valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-fA-F]{64}", value))


def _safe_relative_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = Path(value)
    return not path.is_absolute() and path.as_posix() == value and ".." not in path.parts
