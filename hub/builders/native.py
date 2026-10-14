import subprocess
import os
import shutil
import hashlib
from pathlib import Path
from urllib.parse import urlparse

import requests

from builders.bundle_evidence import (
    collect_git_dependencies,
    command_output,
    file_digest,
    git_source_evidence,
)
from builders.container import (
    is_sha256_hex,
    safe_child_path,
    safe_segment,
)

MAX_SOURCE_BYTES = 100 * 1024 * 1024
BUILD_TOOLS = {
    "make": ["make", "--version"],
    "gcc": ["gcc", "--version"],
}


def observe_build_tools(names):
    observations = []
    for name in names:
        command = BUILD_TOOLS.get(name)
        if not command:
            raise RuntimeError(f"Unsupported native build tool: {name}")
        if not shutil.which(name):
            raise RuntimeError(f"Native build tool is not installed: {name}")
        output = command_output(command)
        if not output:
            raise RuntimeError(
                f"Could not determine the version of native build tool: {name}"
            )
        observations.append({
            "name": name,
            "version": output.splitlines()[0],
            "observation_scope": "declared-tool-available-in-builder",
        })
    return observations


def download_source_file(source, source_dir):
    url = source.get("url")
    expected = source.get("sha256")
    if not isinstance(url, str) or not is_sha256_hex(expected):
        raise RuntimeError("Native file sources require source.url + source.sha256")

    parsed = urlparse(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise RuntimeError("Native source URL must be a credential-free HTTP(S) URL")

    filename = safe_segment(Path(parsed.path).name, "native source filename")
    target = source_dir / filename
    temporary = target.with_name(f"{target.name}.part")
    digest = hashlib.sha256()
    size = 0
    try:
        with requests.get(url, timeout=30, stream=True) as response:
            response.raise_for_status()
            with temporary.open("wb") as handle:
                for chunk in response.iter_content(64 * 1024):
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > MAX_SOURCE_BYTES:
                        raise RuntimeError("Native source file exceeds the size limit")
                    digest.update(chunk)
                    handle.write(chunk)
        actual = digest.hexdigest()
        if actual.lower() != expected.lower():
            raise RuntimeError("Downloaded native source digest does not match source.sha256")
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()

    files = [{"path": filename, "digest": f"sha256:{actual.lower()}"}]
    return {
        "kind": "file",
        "url": url,
        "files": files,
    }


def build(tool_name, recipe_dir, settings, source, output_dir="build"):
    evidence = {
        "builder": "native",
        "source": {"declared": source},
        "isolation": {"containerized": False},
    }

    try:
        safe_tool_name = safe_segment(tool_name, "native tool name")
        base_dir = Path.cwd()
        source_dir = (
            Path(output_dir).resolve() / "_sources" / "native" / safe_tool_name
        )
        if source_dir.exists():
            shutil.rmtree(source_dir)
        source_dir.parent.mkdir(parents=True, exist_ok=True)
        repo_url = source.get("repo")
        if repo_url:
            tag = source.get("tag")
            commit = source.get("commit")
            subprocess.run(["git", "clone", repo_url, str(source_dir)], check=True)
            if commit:
                subprocess.run(
                    ["git", "-C", str(source_dir), "checkout", "--detach", commit],
                    check=True,
                )
            elif tag:
                subprocess.run(
                    ["git", "-C", str(source_dir), "checkout", f"tags/{tag}"],
                    check=True,
                )
            actual_source = None
            dependencies = None
        elif source.get("url"):
            source_dir.mkdir(parents=True)
            actual_source = download_source_file(source, source_dir)
            dependencies = []
        else:
            raise RuntimeError(
                "Native builds require source.repo + source.commit or source.url + source.sha256"
            )

        build_script = settings["buildScript"]
        recipe_script = safe_child_path(recipe_dir, build_script, "buildScript")
        if not recipe_script.is_file() or recipe_script.is_symlink():
            raise RuntimeError(f"Build script is missing or unsafe: {recipe_script}")
        source_script = source_dir / recipe_script.name
        if source_script.exists():
            raise RuntimeError(
                f"Build script would replace an upstream file: {source_script}"
            )
        shutil.copyfile(recipe_script, source_script)

        script_digest = file_digest(source_script)
        subprocess.run(["bash", "-n", str(source_script)], check=True)
        evidence["scripts"] = [
            {"path": build_script, "digest": script_digest}
        ]
        evidence["build_tools"] = observe_build_tools(settings["buildTools"])

        subprocess.run(
            ["bash", f"./{source_script.name}"],
            cwd=source_dir,
            check=True,
        )
        if file_digest(source_script) != script_digest:
            raise RuntimeError("Native build script changed during the build")

        if actual_source and actual_source.get("kind") == "file":
            source_file = source_dir / actual_source["files"][0]["path"]
            if file_digest(source_file) != actual_source["files"][0]["digest"]:
                raise RuntimeError("Native source file changed during the build")

        output_name = settings.get("outputDir", ".")
        source_output = safe_child_path(
            source_dir,
            output_name or ".",
            "native outputDir",
        )
        destination = base_dir / output_dir / safe_tool_name
        shutil.copytree(
            source_output,
            destination,
            dirs_exist_ok=True,
            symlinks=True,
            ignore=shutil.ignore_patterns(".*"),
        )

        if repo_url:
            actual_source = git_source_evidence(
                source_dir,
                repo_url,
                tag=tag,
                commit=commit,
            )["actual"]
            dependencies = collect_git_dependencies(source_dir)
        evidence["source"]["actual"] = actual_source
        evidence["dependencies"] = dependencies
        return {
            "output_dir": str(destination),
            "source_dir": str(source_dir),
            "evidence": evidence,
        }
    except (
        OSError,
        requests.RequestException,
        subprocess.SubprocessError,
        RuntimeError,
        ValueError,
    ) as exc:
        print(f"Error building native binary: {exc}")
        evidence["error"] = str(exc)
        return {"output_dir": "", "evidence": evidence}
