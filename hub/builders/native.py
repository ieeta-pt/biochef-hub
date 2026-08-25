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


def build(tool_name, settings, source, output_dir="build"):
    buildsystem = settings['buildsystem']
    repo_url, tag, commit = source
    evidence = {
        "builder": "native",
        "buildsystem": buildsystem,
        "source": {},
        "toolchain": {
            "name": "make",
            "version": (command_output(["make", "--version"]) or "").splitlines()[0],
        },
        "isolation": {"containerized": False},
    }
    if buildsystem != "make":
        return {"output_dir": "", "evidence": evidence}

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

        workdir = settings.get("workDir", ".")
        workdir_path = safe_child_path(source_dir, workdir, "native workDir")
        subprocess.run(["make"], cwd=workdir_path, check=True)

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
