import subprocess
import os
import shutil
import re
import sys
from pathlib import Path

from builders.build_inputs import collect_build_inputs
from builders.bundle_evidence import (
    collect_biowasm_config_evidence as _biowasm_config_evidence,
    collect_biowasm_script_evidence as _compile_script_evidence,
    collect_biowasm_source_evidence as _actual_source_evidence,
    command_output as _command_output,
    emsdk_commit as _emsdk_commit,
    git_output as _git_output,
)
from builders.wasm_link_evidence import (
    collect_wasm_link_evidence,
    run_and_capture,
    traced_environment,
)


_GIT_COMMIT = re.compile(r"^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")


def build(tool_name, version, output_dir="build", biowasm_dir="biowasm", biowasm_repo="https://github.com/WildBunnie/biowasm", biowasm_commit=None, declared_source=None):
    biowasm_commit = biowasm_commit or os.getenv("BIOWASM_COMMIT")
    if biowasm_commit and not _GIT_COMMIT.fullmatch(biowasm_commit):
        raise RuntimeError("BIOWASM_COMMIT must be a full Git commit digest")
    if os.getenv("GITHUB_ACTIONS") == "true" and not biowasm_commit:
        raise RuntimeError("BIOWASM_COMMIT is required for CI builds")

    compile_command = [sys.executable, "./bin/compile.py", "--tools", tool_name, "--versions", version]
    evidence = {
        "builder": "biowasm",
        "strategy": "biowasm",
        "package": tool_name,
        "version": version,
        "source": {
            "declared": declared_source or {},
        },
        "biowasm": {
            "repo": biowasm_repo,
            "directory": biowasm_dir,
            "requested_commit": biowasm_commit,
        },
        "toolchain": {
            "emcc_version": _command_output(["emcc", "--version"]),
            "emmake_path": _command_output(["which", "emmake"]),
            "emsdk_version": os.getenv("EMSDK_VERSION"),
            "emsdk_directory": os.getenv("EMSDK"),
            "emsdk_requested_commit": os.getenv("EMSDK_COMMIT"),
            "emsdk_resolved_commit": _emsdk_commit(),
        },
        "commands": [
            {
                "argv": compile_command,
                "cwd": biowasm_dir,
            }
        ],
    }
    if os.path.isdir(biowasm_dir):
        shutil.rmtree(biowasm_dir)
    subprocess.run(["git", "clone", biowasm_repo, biowasm_dir], check=True)
    if biowasm_commit:
        subprocess.run(
            ["git", "-C", biowasm_dir, "checkout", "--detach", biowasm_commit],
            check=True,
        )
    
    base_dir = os.getcwd()
    os.chdir(biowasm_dir)
    try:
        evidence["biowasm"]["resolved_commit"] = _git_output(["rev-parse", "HEAD"])
        if biowasm_commit and evidence["biowasm"]["resolved_commit"] != biowasm_commit:
            raise RuntimeError("BioWASM checkout does not match BIOWASM_COMMIT")
        evidence["biowasm"]["configuration"] = _biowasm_config_evidence(
            tool_name, version
        )
        evidence["biowasm"]["scripts"] = _compile_script_evidence(tool_name, version)
        dirty_files_before_build = _git_output(["status", "--short"])
        evidence["biowasm"]["dirty"] = bool(dirty_files_before_build)
        evidence["biowasm"]["dirty_before_build"] = bool(dirty_files_before_build)
        evidence["biowasm"]["dirty_files_before_build"] = dirty_files_before_build
        try:
            link_trace = run_and_capture(compile_command, traced_environment())
        except subprocess.CalledProcessError as exc:
            evidence["error"] = {
                "command": compile_command,
                "returncode": exc.returncode,
            }
            return {"output_dir": "", "evidence": evidence}
        else:
            evidence["source"].update(
                _actual_source_evidence(tool_name, version, evidence["biowasm"])
            )
            actual_source = evidence["source"].get("actual") or {}
            primary_source_dir = Path("tools") / tool_name / "src"
            excluded_submodules = (
                [primary_source_dir.as_posix()]
                if actual_source.get("kind") == "git"
                else []
            )
            evidence["build_inputs"] = collect_build_inputs(
                source_dir=Path("."),
                excluded_submodules=excluded_submodules,
            )
            evidence["wasm_link"] = collect_wasm_link_evidence(
                link_trace,
                source_root=primary_source_dir,
                expected_dir=Path("build") / tool_name / str(version),
                default_link_cwd=primary_source_dir,
                input_root=Path("."),
                build_inputs=evidence["build_inputs"],
                source_evidence=evidence["source"],
                framework_evidence=evidence["biowasm"],
                toolchain_evidence=evidence["toolchain"],
            )
    finally:
        dirty_files_after_build = _git_output(["status", "--short"])
        evidence["biowasm"]["dirty_after_build"] = bool(dirty_files_after_build)
        evidence["biowasm"]["dirty_files_after_build"] = dirty_files_after_build
        os.chdir(base_dir)

    if os.path.isdir(f"{biowasm_dir}/build"):
        shutil.copytree(f"{biowasm_dir}/build/{tool_name}/{version}", f"{output_dir}/{tool_name}", dirs_exist_ok=True)
        shutil.rmtree(f"{biowasm_dir}/build")
        return {"output_dir": os.path.abspath(f"{output_dir}/{tool_name}"), "evidence": evidence}
    
    return {"output_dir": "", "evidence": evidence}
