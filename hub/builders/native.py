import subprocess
import os
import shutil
from pathlib import Path

from builders.build_inputs import collect_build_inputs
from builders.bundle_evidence import (
    command_output as _command_output,
    git_output as _git_output,
)

def build(tool_name, settings, source, output_dir="build"):
    buildsystem = settings['buildsystem']
    repo_url, tag, commit = source
    evidence = {
        "builder": "native",
        "strategy": "native",
        "buildsystem": buildsystem,
        "source": {
            "repo": repo_url,
            "requested_tag": tag,
            "requested_commit": commit,
        },
        "toolchain": {
            "make_version": _command_output(["make", "--version"]),
        },
        "commands": [],
    }

    if buildsystem == "make":
        base_dir = os.getcwd()
        source_dir = os.path.abspath(os.path.join(output_dir, "_sources", "native", tool_name))
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

            workdir = settings.get("workDir", ".")
            workdir_path = _safe_child_path(source_dir, workdir, "native workDir")
            os.chdir(workdir_path)
            evidence["workdir"] = workdir

            evidence["commands"].append({"argv": ["make"], "cwd": workdir})
            subprocess.run(["make"], check=True)

            evidence["build_inputs"] = collect_build_inputs(source_dir=source_dir)

            outputDir = settings.get('outputDir', '')
            from_dir = _safe_child_path(source_dir, outputDir or ".", "native outputDir")
            dest_dir = os.path.join(base_dir, output_dir, tool_name)
            shutil.copytree(from_dir, dest_dir, dirs_exist_ok=True)
            
            return {"output_dir": dest_dir, "evidence": evidence}
        except (OSError, subprocess.SubprocessError) as e:
            print(f"Error building native binary: {e}")
            evidence["error"] = {
                "type": type(e).__name__,
                "message": str(e),
            }
            if isinstance(e, subprocess.CalledProcessError):
                evidence["error"]["command"] = e.cmd
                evidence["error"]["returncode"] = e.returncode
            return {"output_dir": None, "evidence": evidence}
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
