import subprocess
import os
import shutil
import re
from pathlib import Path

from builders.bundle_evidence import (
    collect_git_dependencies,
    command_output,
    emsdk_commit,
    file_digest,
    git_source_evidence,
)
from builders.container import safe_child_path


def activate_emscripten_version(emscripten_version):
    if not emscripten_version:
        return

    emsdk = os.environ.get("EMSDK", "/opt/emsdk")

    result = subprocess.run(
        [f"{emsdk}/emsdk", "list"],
        capture_output=True,
        text=True,
        check=True,
    )

    if emscripten_version not in result.stdout:
        print(
            f"Emscripten version {emscripten_version} does not exist\n"
        )
        return False

    subprocess.run(
        [f"{emsdk}/emsdk", "install", emscripten_version],
        check=True,
    )

    subprocess.run(
        [f"{emsdk}/emsdk", "activate", emscripten_version],
        check=True,
    )
    return True

def build(tool_name, recipe_dir, emscripten_settings, source, output_dir="build"):
    repo_url, tag, commit = source
    emscripten_version = emscripten_settings.get("emscriptenVersion")
    evidence = {
        "builder": "emscripten",
        "source": {},
        "toolchain": {
            "name": "emscripten",
            "requested_version": emscripten_version,
        },
        "isolation": {"containerized": False},
    }
    base_dir = Path.cwd()
    source_dir = (
        Path(output_dir).resolve() / "_sources" / "emscripten" / tool_name
    )

    try:
        if not activate_emscripten_version(emscripten_version):
            raise RuntimeError(
                f"Emscripten version is unavailable: {emscripten_version}"
            )

        emcc_version = command_output(["emcc", "--version"])
        if not emcc_version or emscripten_version not in emcc_version.splitlines()[0]:
            raise RuntimeError(
                f"Active emcc does not match requested version {emscripten_version}"
            )
        compiler_commit = re.search(r"\(([0-9a-f]{40})\)", emcc_version)
        if not compiler_commit:
            raise RuntimeError("Active emcc does not report an exact source commit")
        evidence["toolchain"].update(
            {
                "version": emscripten_version,
                "commit": compiler_commit.group(1),
                "emsdk_commit": emsdk_commit(),
                "emcc_version": emcc_version,
            }
        )

        if source_dir.exists():
            shutil.rmtree(source_dir)
        source_dir.parent.mkdir(parents=True, exist_ok=True)
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

        build_script = emscripten_settings["buildScript"]
        recipe_script = safe_child_path(recipe_dir, build_script, "buildScript")
        if not recipe_script.is_file() or recipe_script.is_symlink():
            raise RuntimeError(f"Build script is missing or unsafe: {recipe_script}")
        source_script = source_dir / recipe_script.name
        if source_script.exists():
            raise RuntimeError(
                f"Build script would replace an upstream file: {source_script}"
            )
        shutil.copyfile(recipe_script, source_script)

        env = os.environ.copy()
        env["EM_FLAGS"] = (
            "-s USE_ZLIB=1 "
            "-s INVOKE_RUN=0 "
            "-s FORCE_FILESYSTEM=1 "
            "-s EXPORTED_RUNTIME_METHODS=['callMain','FS','PROXYFS','WORKERFS'] "
            "-s MODULARIZE=1 "
            "-s ENVIRONMENT=web,worker "
            "-s ALLOW_MEMORY_GROWTH=1 "
            "-s EXIT_RUNTIME=1 "
            "-lworkerfs.js "
            "-lproxyfs.js"
        )
        subprocess.run(
            ["bash", f"./{source_script.name}"],
            cwd=source_dir,
            check=True,
            env=env,
        )

        output_name = emscripten_settings.get("outputDir", ".")
        source_output = safe_child_path(
            source_dir,
            output_name,
            "emscripten outputDir",
        )
        destination = base_dir / output_dir / tool_name
        shutil.copytree(
            source_output,
            destination,
            dirs_exist_ok=True,
            symlinks=True,
            ignore=shutil.ignore_patterns(".*"),
        )

        evidence["source"] = git_source_evidence(
            source_dir,
            repo_url,
            tag=tag,
            commit=commit,
        )
        evidence["dependencies"] = collect_git_dependencies(source_dir)
        evidence["scripts"] = [
            {
                "path": build_script,
                "digest": file_digest(recipe_script),
            }
        ]
        return {
            "output_dir": str(destination),
            "source_dir": str(source_dir),
            "evidence": evidence,
        }
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as exc:
        print(f"Error building with Emscripten: {exc}")
        evidence["error"] = str(exc)
        return {"output_dir": "", "evidence": evidence}
