import subprocess
import os
import shutil
from pathlib import Path

from builders.build_inputs import collect_build_inputs
from builders.bundle_evidence import (
    command_output as _command_output,
    emsdk_commit as _emsdk_commit,
    git_output as _git_output,
)
from builders.wasm_link_evidence import collect_wasm_link_evidence, run_and_capture, traced_environment

def build(tool_name, emscripten_settings, source, output_dir="build"):
    buildsystem = emscripten_settings.get('buildsystem')
    repo_url, tag, commit = source
    evidence = {
        "builder": "emscripten",
        "strategy": "emscripten",
        "buildsystem": buildsystem,
        "source": {
            "repo": repo_url,
            "requested_tag": tag,
            "requested_commit": commit,
        },
        "toolchain": {
            "emcc_version": _command_output(["emcc", "--version"]),
            "emmake_path": _command_output(["which", "emmake"]),
            "emsdk_version": os.getenv("EMSDK_VERSION"),
            "emsdk_directory": os.getenv("EMSDK"),
            "emsdk_requested_commit": os.getenv("EMSDK_COMMIT"),
            "emsdk_resolved_commit": _emsdk_commit(),
        },
        "commands": [],
    }

    if buildsystem == "make":
        base_dir = os.getcwd()
        source_dir = os.path.abspath(os.path.join(output_dir, "_sources", "emscripten", tool_name))
        backup_file = None
        makefile_path = None
        try:
            if os.path.isdir(source_dir):
                shutil.rmtree(source_dir)
            os.makedirs(os.path.dirname(source_dir), exist_ok=True)
            subprocess.run(["git", "clone", repo_url, source_dir], check=True)

            os.chdir(source_dir)

            if commit:
                subprocess.run(["git", "checkout", "--detach", commit], check=True)
            elif tag:
                subprocess.run(["git", "checkout", "tags/" + tag], check=True)

            resolved = _git_output(["rev-parse", "HEAD"])
            if not resolved:
                raise RuntimeError("Failed to resolve git commit in source checkout")
            if commit and resolved != commit:
                raise RuntimeError(f"Resolved commit {resolved} does not match requested {commit}")

            evidence["source"]["resolved_commit"] = resolved
            evidence["source"]["actual"] = {
                "kind": "git",
                "path": _relative_path(source_dir, base_dir),
                "repo": _git_output(["config", "--get", "remote.origin.url"]),
                "resolved_commit": evidence["source"]["resolved_commit"],
            }
            _record_source_status(evidence, source_dir)

            env = os.environ.copy()
            # TODO: get the flags from biowasm instead of hardcoding them here
            env["EM_FLAGS"] = "-s USE_ZLIB=1 -s INVOKE_RUN=0 -s FORCE_FILESYSTEM=1 -s EXPORTED_RUNTIME_METHODS=['callMain','FS','PROXYFS','WORKERFS'] -s MODULARIZE=1 -s ENVIRONMENT=['web','worker'] -s ALLOW_MEMORY_GROWTH=1 -s EXIT_RUNTIME=1 -lworkerfs.js -lproxyfs.js"
            evidence["env"] = {
                "EM_FLAGS": env["EM_FLAGS"],
                "recipe_env": emscripten_settings.get("env", []),
            }

            workdir = emscripten_settings.get("workDir", ".")
            workdir_path = _safe_child_path(source_dir, workdir, "emscripten workDir")
            os.chdir(workdir_path)
            evidence["workdir"] = workdir

            makefile_path = Path.cwd() / "Makefile"
            backup_file = Path.cwd() / "Makefile.bak"
            shutil.copy(makefile_path, backup_file)

            commands = emscripten_settings.get("commands", [])
            for command in commands:
                evidence["commands"].append({"shell": command, "cwd": workdir})
                subprocess.run(command, shell=True, check=True)

            make_command = f"emmake make {" ".join(emscripten_settings.get("env", []))}"
            evidence["commands"].append({"shell": make_command, "cwd": workdir})
            link_trace = run_and_capture(
                make_command,
                traced_environment(env),
                shell=True,
            )

            evidence["build_inputs"] = collect_build_inputs(source_dir=source_dir)

            outputDir = emscripten_settings.get('outputDir', '')
            from_dir = _safe_child_path(source_dir, outputDir or ".", "emscripten outputDir")
            evidence["wasm_link"] = collect_wasm_link_evidence(
                link_trace,
                source_root=source_dir,
                expected_dir=from_dir,
                default_link_cwd=workdir_path,
                input_root=source_dir,
                build_inputs=evidence["build_inputs"],
                source_evidence=evidence["source"],
                framework_evidence=None,
                toolchain_evidence=evidence["toolchain"],
            )
            dest_dir = os.path.join(base_dir, output_dir, tool_name)
            shutil.copytree(from_dir, dest_dir, dirs_exist_ok=True)

            return {"output_dir": dest_dir, "evidence": evidence}
        except (OSError, subprocess.SubprocessError) as e:
            print(f"Error building with emscripten: {e}")
            evidence["error"] = {
                "type": type(e).__name__,
                "message": str(e),
            }
            if isinstance(e, subprocess.CalledProcessError):
                evidence["error"]["command"] = e.cmd
                evidence["error"]["returncode"] = e.returncode
            return {"output_dir": None, "evidence": evidence}
        finally:
            try:
                # Restore the original Makefile from the backup
                if makefile_path and backup_file and backup_file.exists():
                    shutil.copy(backup_file, makefile_path)
            finally:
                # Return to the correct dir
                os.chdir(base_dir)
                if os.path.isdir(source_dir):
                    _record_build_dirty_status(evidence, source_dir)
                    _restore_clean_checkout(evidence, source_dir)
                    _record_source_status(evidence, source_dir)

    return {"output_dir": "", "evidence": evidence}


def _relative_path(path, base_dir):
    return os.path.relpath(Path(path).resolve(), Path(base_dir).resolve())


def _safe_child_path(parent, child, label):
    parent_path = Path(parent).resolve()
    child_path = (parent_path / child).resolve()
    try:
        child_path.relative_to(parent_path)
    except ValueError as exc:
        raise ValueError(f"{label} escapes source directory: {child}") from exc
    return str(child_path)


def _record_source_status(evidence, source_dir):
    status = _git_output(["-C", source_dir, "status", "--short"])
    if status is None:
        raise RuntimeError(f"Failed to securely check git status in {source_dir}")
    evidence["source"]["dirty"] = bool(status)
    evidence["source"]["dirty_files"] = status
    actual = evidence["source"].setdefault("actual", {})
    actual["dirty"] = bool(status)
    actual["dirty_files"] = status


def _record_build_dirty_status(evidence, source_dir):
    status = _git_output(["-C", source_dir, "status", "--short"])
    if status is None:
        raise RuntimeError(f"Failed to securely check git status in {source_dir}")
    evidence["source"]["build_dirty"] = bool(status)
    evidence["source"]["build_dirty_files"] = status


def _restore_clean_checkout(evidence, source_dir):
    try:
        subprocess.run(["git", "-C", source_dir, "reset", "--hard", "HEAD"], check=True, capture_output=True, text=True)
        subprocess.run(["git", "-C", source_dir, "clean", "-fdx"], check=True, capture_output=True, text=True)
        evidence["source"]["cleaned_after_build"] = True
    except (OSError, subprocess.SubprocessError) as exc:
        evidence["source"]["cleaned_after_build"] = False
        evidence["source"]["cleanup_error"] = str(exc)
        raise RuntimeError(f"Failed to cleanly restore source checkout: {exc}")
