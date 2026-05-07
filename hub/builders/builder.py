import json
from pathlib import Path
from urllib.parse import urlparse
import yaml
import os
import shutil
import hashlib
import requests
import stat

from builders.biowasm import build as build_biowasm
from builders.emscripten import build as build_emscripten
from builders.native import build as build_native

def generate_digest(file_path: str) -> str:
    sha256_hash = hashlib.sha256()

    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            sha256_hash.update(chunk)

    return f"sha256:{sha256_hash.hexdigest()}"

def download_github_license(repo_url: str, target_path: str, license_files=None):
    parts = urlparse(repo_url).path.strip("/").split("/")
    if len(parts) < 2:
        raise ValueError("Invalid GitHub repo URL")
    owner, repo = parts[0], parts[1]

    # Try recipe-declared filenames first, then common fallbacks.
    candidates = list(license_files or []) + ["LICENSE", "LICENSE.txt", "LICENSE.md", "COPYING", "COPYING.txt"]
    seen = set()
    candidates = [c for c in candidates if not (c in seen or seen.add(c))]

    last_error = None
    for branch in ["main", "master"]:
        for filename in candidates:
            raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{filename}"
            response = requests.get(raw_url)
            if response.status_code == 200:
                target_file = Path(target_path)
                target_file.parent.mkdir(parents=True, exist_ok=True)
                target_file.write_text(response.text, encoding="utf-8")
                return
            else:
                last_error = response.status_code

    raise Exception(f"Failed to fetch license (tried {candidates}): {last_error}")

def build_wasm(recipe, build_dir):
    tool_name = recipe["name"]
    wasm_settings = recipe['build']['wasm']
    wasm_strategy = wasm_settings['strategy']

    def build_biowasm_wrapper():
        package_name = wasm_settings.get("biowasm",{}).get("package", "")
        if not package_name: package_name = tool_name
        return build_biowasm(package_name, recipe["source"].get("version"), output_dir=build_dir)

    def build_emscripten_wrapper():
        source = (
            recipe["source"]["repo"],
            recipe["source"].get("tag"),
            recipe["source"].get("commit") 
        )
        return build_emscripten(tool_name, wasm_settings["emscripten"], source, output_dir=build_dir)

    output_dir = None
    if wasm_strategy == "biowasm":
        output_dir = build_biowasm_wrapper()
    elif wasm_strategy == "emscripten":
        output_dir = build_emscripten_wrapper()
    elif wasm_strategy == "auto":
        output_dir = build_biowasm_wrapper()
        if not output_dir: output_dir = build_emscripten_wrapper()

    if not output_dir:
        raise RuntimeError("Failed to build WASM using selected strategy")

    return output_dir

def build_plugins(file_paths, build_dir, registry_dir):
    print(f"Building recipes: {file_paths}")

    if os.path.exists(registry_dir) and os.path.isdir(registry_dir):
        shutil.rmtree(registry_dir)

    for path in file_paths:
        with open(path, 'r') as file:
            recipe = yaml.safe_load(file)

        print(f"Attempting to build: {recipe["name"]}")

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

        for operation in recipe["operations"]:
            plugin_dir = f"{registry_dir}/{operation['id']}/{recipe['version']}"
            os.makedirs(plugin_dir, exist_ok=True)

            bundle = operation
            bundle["runtime"] = {
                "modes": recipe["runtime"]["modes"],
            }

            if "github" in recipe['source']["repo"]:
                license_files = recipe.get("license", {}).get("files")
                download_github_license(recipe["source"]["repo"], f"{plugin_dir}/LICENSE", license_files)

            for runtime in build_runtimes:
                runtime_dir = f"{plugin_dir}/runtime/{runtime}"
                os.makedirs(runtime_dir, exist_ok=True)

                # TODO deal with shared binaries
                bin_name = operation["bin"]
                output_dir = outputs[runtime]
                if not output_dir: continue

                if runtime == "wasm":
                    shutil.copyfile(f"{output_dir}/{bin_name}.js", f"{runtime_dir}/{bin_name}.js")
                    shutil.copyfile(f"{output_dir}/{bin_name}.wasm", f"{runtime_dir}/{bin_name}.wasm")
                elif runtime == "native":
                    shutil.copyfile(f"{output_dir}/{bin_name}", f"{runtime_dir}/{bin_name}")
                    st = os.stat(f"{runtime_dir}/{bin_name}")
                    os.chmod(f"{runtime_dir}/{bin_name}", st.st_mode | stat.S_IEXEC)

                if runtime == "wasm":
                    bundle["runtime"]["wasm"] = {
                        "wasm_digest": generate_digest(f"{runtime_dir}/{bin_name}.wasm"),
                        "js_digest":generate_digest(f"{runtime_dir}/{bin_name}.js"),
                    }

                elif runtime == "native":
                    bundle["runtime"]["native"] = {
                        "digest": generate_digest(f"{runtime_dir}/{bin_name}"),
                    }

            with open(f"{plugin_dir}/bundle.json", "w") as f:
                json.dump(bundle, f, indent=4)
            
            #TODO sbom.json

        print(f"Finished building {recipe["name"]}")

    shutil.rmtree(build_dir)