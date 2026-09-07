import subprocess
import os
import shutil
from pathlib import Path

from builders.bundle_evidence import (
    collect_git_dependencies,
    command_output,
    git_source_evidence,
)
from builders.container import safe_child_path


def build(tool_name, recipe_dir, settings, source, output_dir="build"):
    # buildsystem = settings['buildsystem']
    repo_url, tag, commit = source
    evidence = {
        "builder": "native",
        # "buildsystem": buildsystem,
        "source": {},
        "toolchain": {
            "name": "make",
            "version": (command_output(["make", "--version"]) or "").splitlines()[0],
        },
        "isolation": {"containerized": False},
    }
    # if buildsystem != "make":
    #     return {"output_dir": "", "evidence": evidence}

    base_dir = Path.cwd()
    source_dir = Path(output_dir).resolve() / "_sources" / "native" / tool_name
    try:
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

        subprocess.run(
            ["bash", f"./{source_script.name}"],
            cwd=source_dir,
            check=True,
        )

        output_name = settings.get("outputDir", ".")
        source_output = safe_child_path(
            source_dir,
            output_name or ".",
            "native outputDir",
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
        return {
            "output_dir": str(destination),
            "source_dir": str(source_dir),
            "evidence": evidence,
        }
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as exc:
        print(f"Error building native binary: {exc}")
        evidence["error"] = str(exc)
        return {"output_dir": "", "evidence": evidence}
