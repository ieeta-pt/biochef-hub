#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path, PurePosixPath


BIOWASM_ROOT = Path("/biowasm")
EVIDENCE_DIR = Path("/tmp/biochef-builder-evidence")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
MAX_EVIDENCE_BYTES = 1024 * 1024


def node_compile_driver():
    # Use temporary drivers so BioWASM's git stash cannot discard Node support.
    runtime_dir = EVIDENCE_DIR / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=False)
    shared = BIOWASM_ROOT / "bin/shared.sh"
    if shared.read_text().count('-s ENVIRONMENT="web,worker"') != 1:
        raise RuntimeError("Unexpected BioWASM runtime flags")

    replacements = {
        "compile.sh": [
            (". bin/shared.sh\n", '. bin/shared.sh\nexport EM_FLAGS="${EM_FLAGS/web,worker/web,worker,node}"\n'),
        ],
        "compile.py": [
            ('DIR_ROOT = Path(__file__).parent / "../"', f"DIR_ROOT = Path({str(BIOWASM_ROOT)!r})"),
            ("bin/compile.sh tools/{tool} {branch}", f"{runtime_dir}/compile.sh tools/{{tool}} {{branch}}"),
        ],
    }
    scripts = []
    for name, edits in replacements.items():
        source = BIOWASM_ROOT / "bin" / name
        text = source.read_text()
        for original, replacement in edits:
            if text.count(original) != 1:
                raise RuntimeError(f"Unexpected BioWASM driver: {name}")
            text = text.replace(original, replacement)
        target = runtime_dir / name
        target.write_text(text)
        target.chmod(0o700)
        scripts.append({"path": f"bin/{name}", "source_digest": file_digest(source),
                        "adapted_digest": file_digest(target)})
    return runtime_dir / "compile.py", {
        "environment": ["web", "worker", "node"],
        "shared_flags_digest": file_digest(shared),
        "scripts": scripts,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--license-file", action="append", default=[])
    parser.add_argument("--evidence-file", action="append", default=[])
    args = parser.parse_args()

    os.chdir(BIOWASM_ROOT)
    driver, runtime_override = node_compile_driver()
    command = [
        "python3",
        str(driver),
        "--tools",
        args.tool,
        "--versions",
        args.version,
    ]
    result = subprocess.run(command, check=False)

    source_root = BIOWASM_ROOT / "tools" / args.tool / "src"
    framework = git_identity(BIOWASM_ROOT)
    source = source_identity(source_root, include_files=True)
    configuration, dependencies = configuration_evidence(args.tool, args.version)
    configuration["runtime_override"] = runtime_override

    observations = {
        "compile_exit_code": result.returncode,
        "framework": framework,
        "source": source,
        "configuration": configuration,
        "dependencies": dependency_evidence(source_root, dependencies),
        "toolchain": emscripten_evidence(),
        "license_files": collect_license_files(
            source_root,
            source,
            args.license_file,
            args.evidence_file,
        ),
    }
    write_observations(observations)
    return result.returncode


def command_output(command, required=True):
    result = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    output = result.stdout.rstrip("\r\n")
    if result.returncode == 0:
        return output
    if required:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(command)}: {output}"
        )
    return None


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def canonical_digest(value):
    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def tree_digest(root):
    root = Path(root).resolve()
    files = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if ".git" in relative.parts or path.is_symlink() or not path.is_file():
            continue
        files.append(
            {
                "path": relative.as_posix(),
                "digest": file_digest(path),
            }
        )
    return canonical_digest(files), len(files)


def git_identity(path):
    path = Path(path).resolve()
    top_level = command_output(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        required=False,
    )
    if not top_level or Path(top_level).resolve() != path:
        return None
    commit = command_output(["git", "-C", str(path), "rev-parse", "HEAD"])
    if not COMMIT.fullmatch(commit):
        raise RuntimeError(f"invalid Git commit observed for {path}")
    status = command_output(["git", "-C", str(path), "status", "--short"])
    return {
        "kind": "git",
        "repo": command_output(
            ["git", "-C", str(path), "config", "--get", "remote.origin.url"]
        ),
        "commit": commit,
        "dirty": bool(status),
        "final_tree_digest": tree_digest(path)[0],
    }


def source_identity(path, include_files=False):
    identity = git_identity(path)
    if identity:
        return identity
    path = Path(path)
    if not path.is_dir():
        raise RuntimeError(f"BioWASM source directory is missing: {path}")
    files = [
        {
            "path": item.relative_to(path).as_posix(),
            "digest": file_digest(item),
        }
        for item in sorted(path.rglob("*"))
        if item.is_file()
        and not item.is_symlink()
        and ".git" not in item.relative_to(path).parts
    ]
    evidence = {
        "kind": "vendored",
        "tree_digest": canonical_digest(files),
        "file_count": len(files),
    }
    if include_files:
        evidence["files"] = files
    return evidence


def configuration_evidence(tool_name, version):
    path = BIOWASM_ROOT / "biowasm.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    selected_tool = next(
        (tool for tool in document.get("tools", []) if tool.get("name") == tool_name),
        None,
    )
    if not selected_tool:
        raise RuntimeError(f"BioWASM configuration has no tool named {tool_name}")
    selected_version = next(
        (
            item
            for item in selected_tool.get("versions", [])
            if str(item.get("version")) == str(version)
        ),
        None,
    )
    if not selected_version:
        raise RuntimeError(
            f"BioWASM configuration has no {tool_name} version {version}"
        )

    dependencies = []
    pending = list(selected_version.get("dependencies") or [])
    seen = set()
    while pending:
        dependency = pending.pop(0)
        name = dependency.get("name")
        dependency_version = dependency.get("version")
        if not name or dependency_version is None:
            continue
        key = (str(name), str(dependency_version))
        if key in seen:
            continue
        seen.add(key)
        dependencies.append({"name": key[0], "version": key[1]})
        dependency_tool = next(
            (
                tool
                for tool in document.get("tools", [])
                if tool.get("name") == key[0]
            ),
            None,
        )
        dependency_version = next(
            (
                item
                for item in (dependency_tool or {}).get("versions", [])
                if str(item.get("version")) == key[1]
            ),
            None,
        )
        pending.extend((dependency_version or {}).get("dependencies") or [])

    return {
        "path": "biowasm.json",
        "digest": file_digest(path),
        "selected_version_digest": canonical_digest(selected_version),
    }, dependencies


def dependency_evidence(source_root, configured):
    dependencies = []
    for item in configured:
        path = BIOWASM_ROOT / "tools" / item["name"] / "src"
        if not path.is_dir():
            continue
        dependencies.append({**item, "source": source_identity(path)})

    result = command_output(
        ["git", "-C", str(source_root), "submodule", "status", "--recursive"],
        required=False,
    )
    for line in (result or "").splitlines():
        fields = line.lstrip(" +-U").split()
        if len(fields) < 2 or not COMMIT.fullmatch(fields[0]):
            continue
        path = source_root / fields[1]
        identity = git_identity(path)
        if identity:
            dependencies.append(
                {
                    "name": path.name,
                    "path": fields[1],
                    "source": identity,
                }
            )

    unique = {}
    for dependency in dependencies:
        source = dependency["source"]
        key = (
            source.get("repo"),
            source.get("commit"),
            source.get("tree_digest"),
        )
        unique.setdefault(key, dependency)
    return list(unique.values())


def emscripten_evidence():
    output = command_output(["emcc", "--version"])
    commit = re.search(r"\(([0-9a-f]{40})\)", output)
    version = re.search(r"\)\s+([0-9]+(?:\.[0-9]+)+)\s+\(", output)
    if not commit or not version:
        raise RuntimeError("Emscripten version output has no version and commit")
    return {
        "name": "emscripten",
        "version": version.group(1),
        "commit": commit.group(1),
    }


def safe_relative_path(value):
    if not isinstance(value, str) or not value or "\\" in value:
        raise RuntimeError(f"unsafe declared evidence path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise RuntimeError(f"unsafe declared evidence path: {value!r}")
    return path


def collect_license_files(source_root, source, license_files, evidence_files):
    collected = []
    for role, paths in (
        ("license", license_files),
        ("source-header", evidence_files),
    ):
        for value in paths:
            relative = safe_relative_path(value)
            content = source_file_content(source_root, relative, source)
            if content is None:
                continue
            archive_path = PurePosixPath("license-files") / role / relative
            destination = EVIDENCE_DIR / archive_path
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            destination.write_bytes(content)
            collected.append(
                {
                    "role": role,
                    "source_path": relative.as_posix(),
                    "archive_path": archive_path.as_posix(),
                    "digest": file_digest(destination),
                    "source": source,
                }
            )
    return collected


def source_file_content(source_root, relative, source):
    if source.get("kind") == "git":
        result = subprocess.run(
            ["git", "-C", str(source_root), "show", f"HEAD:{relative.as_posix()}"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            return None
        content = result.stdout
    else:
        candidate = source_root / relative
        if not candidate.is_file() or candidate.is_symlink():
            return None
        content = candidate.read_bytes()
    return content if len(content) <= MAX_EVIDENCE_BYTES else None


def write_observations(observations):
    EVIDENCE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = EVIDENCE_DIR / "observations.json"
    path.write_text(
        json.dumps(observations, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
