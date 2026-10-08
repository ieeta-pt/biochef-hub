import hashlib
import json
import os
import re
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any


_SHA256_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
MAX_ARCHIVE_MEMBERS = 10_000
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024


class ContainerEvidenceError(RuntimeError):
    pass


def safe_segment(value: Any, field: str) -> str:
    text = str(value)
    if not _SAFE_SEGMENT.fullmatch(text) or text in {".", ".."}:
        raise ContainerEvidenceError(f"{field} is not a safe path segment: {value!r}")
    return text


def safe_child_path(parent: str | Path, child: Any, field: str) -> Path:
    if not isinstance(child, (str, os.PathLike)):
        raise ContainerEvidenceError(f"{field} is not a relative path: {child!r}")
    text = os.fspath(child)
    relative = Path(text)
    if (
        not text
        or "\\" in text
        or relative.is_absolute()
        or ".." in relative.parts
    ):
        raise ContainerEvidenceError(f"{field} is not a safe relative path: {child!r}")
    root = Path(parent).resolve()
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ContainerEvidenceError(f"{field} escapes its directory: {child!r}") from exc
    return target


def canonical_digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def file_digest(path: str | Path) -> str:
    return f"sha256:{sha256_hex(path)}"


def sha256_hex(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_sha256_hex(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"^[0-9a-fA-F]{64}$", value))


def is_sha256_digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("sha256:")
        and is_sha256_hex(value.removeprefix("sha256:"))
    )


def image_evidence(image: Any, requested_reference: str, context_digest: str | None) -> dict[str, Any]:
    image.reload()
    attrs = image.attrs
    labels = ((attrs.get("Config") or {}).get("Labels") or {})
    repo_digests = sorted(attrs.get("RepoDigests") or [])
    image_id = image.id
    if not _SHA256_DIGEST.fullmatch(image_id):
        raise ContainerEvidenceError(f"Docker returned an invalid image ID: {image_id!r}")
    manifest_digest = None
    if "@sha256:" in requested_reference:
        manifest_digest = f"sha256:{requested_reference.rsplit('@sha256:', 1)[1]}"
        if not _SHA256_DIGEST.fullmatch(manifest_digest):
            raise ContainerEvidenceError(
                f"Builder image reference has an invalid digest: {requested_reference!r}"
            )

    return {
        "requested_reference": requested_reference,
        "image_id": image_id,
        "manifest_digest": manifest_digest,
        "repo_digests": repo_digests,
        "context_digest": context_digest,
        "labels": {
            key: labels[key]
            for key in sorted(labels)
            if key.startswith("dev.biochef.")
        },
    }


def extract_container_archive(
    stream: Any,
    destination: str | Path,
    *,
    expected_top_level: str,
) -> None:
    with tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024, mode="w+b") as buffer:
        archive_size = 0
        for chunk in stream:
            archive_size += len(chunk)
            if archive_size > MAX_ARCHIVE_BYTES:
                raise ContainerEvidenceError("archive exceeds the extraction size limit")
            buffer.write(chunk)
        buffer.seek(0)

        target_root = Path(destination).resolve()
        target_root.mkdir(parents=True, exist_ok=True)
        top_level = safe_segment(expected_top_level, "archive top-level directory")

        with tarfile.open(fileobj=buffer, mode="r:*") as archive:
            members = archive.getmembers()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise ContainerEvidenceError(
                    f"archive contains too many members: {len(members)}"
                )
            if sum(member.size for member in members) > MAX_ARCHIVE_BYTES:
                raise ContainerEvidenceError("archive exceeds the extraction size limit")

            indexed = {}
            for member in members:
                relative = _normalized_member_path(member.name, top_level)
                if relative in indexed:
                    raise ContainerEvidenceError(
                        f"archive contains a duplicate path: {member.name}"
                    )
                indexed[relative] = member
            for relative, member in indexed.items():
                if relative is None:
                    continue
                target = _child_path(target_root, relative)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if member.isreg():
                    _write_regular_file(archive, member, target)
                    continue
                if member.issym() or member.islnk():
                    resolved = _resolve_archive_link(relative, member, top_level)
                    linked_member = indexed.get(resolved)
                    if linked_member is None or not linked_member.isreg():
                        raise ContainerEvidenceError(
                            f"archive link does not resolve to a regular file: {member.name}"
                        )
                    _write_regular_file(archive, linked_member, target)
                    continue
                raise ContainerEvidenceError(
                    f"archive contains unsupported special file: {member.name}"
                )


def _normalized_member_path(name: str, top_level: str) -> str | None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise ContainerEvidenceError(f"unsafe archive path: {name!r}")
    parts = tuple(part for part in path.parts if part not in {"", "."})
    if not parts or parts[0] != top_level:
        raise ContainerEvidenceError(
            f"archive member is outside expected top-level directory: {name!r}"
        )
    if len(parts) == 1:
        return None
    return PurePosixPath(*parts[1:]).as_posix()


def _resolve_archive_link(relative: str, member: tarfile.TarInfo, top_level: str) -> str:
    link = PurePosixPath(member.linkname)
    if link.is_absolute():
        raise ContainerEvidenceError(f"archive contains an absolute link: {member.name}")

    if member.issym():
        combined = PurePosixPath(relative).parent / link
    else:
        link_parts = link.parts
        combined = (
            PurePosixPath(*link_parts[1:])
            if link_parts and link_parts[0] == top_level
            else link
        )

    parts = []
    for part in combined.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                raise ContainerEvidenceError(
                    f"archive link escapes its top-level directory: {member.name}"
                )
            parts.pop()
        else:
            parts.append(part)
    if not parts:
        raise ContainerEvidenceError(f"archive link has an empty target: {member.name}")
    return PurePosixPath(*parts).as_posix()


def _child_path(root: Path, relative: str) -> Path:
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ContainerEvidenceError(
            f"archive output escapes destination: {relative!r}"
        ) from exc
    return target


def _write_regular_file(
    archive: tarfile.TarFile,
    member: tarfile.TarInfo,
    target: Path,
) -> None:
    source = archive.extractfile(member)
    if source is None:
        raise ContainerEvidenceError(f"could not read archive file: {member.name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.is_dir():
        raise ContainerEvidenceError(f"archive file conflicts with directory: {target}")
    with source, target.open("wb") as destination:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            destination.write(chunk)
    os.chmod(target, member.mode & 0o755)
