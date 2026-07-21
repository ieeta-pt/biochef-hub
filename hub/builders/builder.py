import json
from pathlib import Path
import yaml
import os
import shutil
import stat
import copy

from builders.biowasm import build as build_biowasm
from builders.bundle_evidence import build_bundle_evidence, collect_license_evidence, generate_digest
from builders.emscripten import build as build_emscripten
from builders.native import build as build_native

def reset_dir(dir_to_reset):
    if os.path.exists(dir_to_reset):
        shutil.rmtree(dir_to_reset)
    os.makedirs(dir_to_reset, exist_ok=True)

def safe_path_segment(value, label):
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    candidate = Path(value)
    if not value or "\\" in value or candidate.name != value or candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"{label} must be a single safe path segment: {value!r}")
    return value


def build_wasm(recipe, build_dir):
    tool_name = recipe["name"]
    wasm_settings = recipe['build']['wasm']
    wasm_strategy = wasm_settings['strategy']

    def build_biowasm_wrapper():
        package_name = wasm_settings.get("biowasm",{}).get("package", "")
        if not package_name: package_name = tool_name
        package_name = safe_path_segment(package_name, "BioWASM package name")
        biowasm_dir = os.path.join(build_dir, "_biowasm", package_name)
        return build_biowasm(package_name, recipe["source"].get("version"), output_dir=build_dir, biowasm_dir=biowasm_dir, declared_source=recipe.get("source"))

    def build_emscripten_wrapper():
        source_repo = recipe.get("source", {}).get("repo")
        if not source_repo:
            raise RuntimeError("Emscripten builds require source.repo + source.commit")
        source = (
            source_repo,
            recipe["source"].get("tag"),
            recipe["source"].get("commit") 
        )
        return build_emscripten(
            tool_name,
            wasm_settings["emscripten"],
            source,
            output_dir=build_dir,
        )

    output_dir = None
    result = None
    if wasm_strategy == "biowasm":
        result = build_biowasm_wrapper()
        output_dir = result["output_dir"]
    elif wasm_strategy == "emscripten":
        result = build_emscripten_wrapper()
        output_dir = result["output_dir"]
    elif wasm_strategy == "auto":
        result = build_biowasm_wrapper()
        output_dir = result["output_dir"]
        if not output_dir:
            result = build_emscripten_wrapper()
            output_dir = result["output_dir"]

    if not output_dir:
        raise RuntimeError("Failed to build WASM using selected strategy")

    return result

def build_plugins(file_paths, build_dir, registry_dir):
    print(f"Building recipes: {file_paths}")

    if os.path.exists(registry_dir) and os.path.isdir(registry_dir):
        shutil.rmtree(registry_dir)

    for path in file_paths:
        with open(path, 'r') as file:
            recipe = yaml.safe_load(file)

        safe_path_segment(recipe["name"], "recipe name")
        print(f"Attempting to build: {recipe["name"]}")
        reset_dir(build_dir)

        outputs = {}
        build_runtimes = recipe['build'].keys()
        for runtime in build_runtimes:
            
            if runtime == "wasm":
                outputs["wasm"] = build_wasm(recipe, build_dir)
            elif runtime == "native":
                source = (
                    recipe["source"]["repo"],
                    recipe["source"].get("tag"),
                    recipe["source"].get("commit")
                )
                outputs["native"] = build_native(
                    recipe["name"],
                    recipe['build']["native"],
                    source,
                    output_dir=build_dir
                )

        for runtime in build_runtimes:
            result = outputs.get(runtime)
            output_dir = result.get("output_dir") if isinstance(result, dict) else None
            if not output_dir:
                error = ((result or {}).get("evidence") or {}).get("error") if isinstance(result, dict) else None
                detail = f": {error}" if error else ""
                raise RuntimeError(f"Failed to build declared {runtime} runtime for {recipe['name']}{detail}")

        recipe_version = safe_path_segment(recipe["version"], "recipe version")
        for operation in recipe["operations"]:
            operation_id = safe_path_segment(operation["id"], "operation id")
            plugin_dir = Path(registry_dir) / operation_id / recipe_version
            os.makedirs(plugin_dir, exist_ok=True)

            bundle = copy.deepcopy(operation)
            bundle["version"] = recipe_version
            bundle["runtime"] = {
                "modes": recipe["runtime"]["modes"],
            }
            runtime_artifacts = {}

            license_evidence = collect_license_evidence(recipe, plugin_dir / "LICENSE", runtime_results=outputs)

            for runtime in build_runtimes:
                runtime_dir = plugin_dir / "runtime" / runtime
                os.makedirs(runtime_dir, exist_ok=True)

                # TODO deal with shared binaries
                bin_name = safe_path_segment(operation["bin"], "operation binary name")
                output_dir = outputs[runtime]["output_dir"]
                output_path = Path(output_dir)

                if runtime == "wasm":
                    wasm_outputs = []
                    for extension in ("js", "wasm"):
                        source_artifact = output_path / f"{bin_name}.{extension}"
                        if not source_artifact.is_file() or source_artifact.is_symlink():
                            raise RuntimeError(f"WASM build output is missing or unsafe: {source_artifact}")
                        target_artifact = runtime_dir / source_artifact.name
                        shutil.copyfile(source_artifact, target_artifact)
                        wasm_outputs.append((extension, target_artifact))
                elif runtime == "native":
                    source_artifact = output_path / bin_name
                    if not source_artifact.is_file() or source_artifact.is_symlink():
                        raise RuntimeError(f"Native build output is missing or unsafe: {source_artifact}")
                    native_artifact = runtime_dir / bin_name
                    shutil.copyfile(source_artifact, native_artifact)
                    st = os.stat(native_artifact)
                    os.chmod(native_artifact, st.st_mode | stat.S_IEXEC)

                if runtime == "wasm":
                    wasm_files = []
                    wasm_metadata = {}
                    for extension, artifact_path in wasm_outputs:
                        relative_path = f"runtime/wasm/{artifact_path.name}"
                        digest = generate_digest(artifact_path)
                        wasm_metadata[f"{extension}_digest"] = digest
                        wasm_files.append({"path": relative_path, "digest": digest})
                    wasm_metadata["files"] = wasm_files
                    bundle["runtime"]["wasm"] = wasm_metadata
                    runtime_artifacts["wasm"] = {
                        "files": wasm_files
                    }

                elif runtime == "native":
                    bundle["runtime"]["native"] = {
                        "digest": generate_digest(runtime_dir / bin_name),
                    }
                    runtime_artifacts["native"] = {
                        "files": [
                            {
                                "path": f"runtime/native/{bin_name}",
                                "digest": bundle["runtime"]["native"]["digest"],
                            }
                        ]
                    }

            with open(plugin_dir / "bundle.json", "w") as f:
                json.dump(bundle, f, indent=4)

            build_evidence = build_bundle_evidence(
                recipe_path=path,
                recipe=recipe,
                operation=operation,
                runtime_results=outputs,
                runtime_artifacts=runtime_artifacts,
                license_evidence=license_evidence,
            )
            with open(plugin_dir / "build-evidence.json", "w") as f:
                json.dump(build_evidence, f, indent=4, sort_keys=True)

            # SBOMs are generated after build with `hub.py sbom`.

        print(f"Finished building {recipe["name"]}")

    shutil.rmtree(build_dir)
