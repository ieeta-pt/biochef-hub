import json
from pathlib import Path
from urllib.parse import urlparse
import yaml
import os
import shutil
import hashlib
import requests

from builders.biowasm import build as build_biowasm
from builders.emscripten import build as build_emscripten

def generate_digest(file_path: str) -> str:
    sha256_hash = hashlib.sha256()
    
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            sha256_hash.update(chunk)
    
    return f"sha256:{sha256_hash.hexdigest()}"

def download_github_license(repo_url: str, target_path: str):
    parts = urlparse(repo_url).path.strip("/").split("/")
    if len(parts) < 2:
        raise ValueError("Invalid GitHub repo URL")
    owner, repo = parts[0], parts[1]

    last_error = None
    for branch in ["main","master"]:
        raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/LICENSE"
        response = requests.get(raw_url)
        if response.status_code == 200:
            target_file = Path(target_path)
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_text(response.text, encoding="utf-8")
            return
        else:
            last_error = response.status_code

    raise Exception(f"Failed to fetch LICENSE: {last_error}")

def build_plugins(file_paths, build_dir, registry_dir):
    print(f"Building recipes: {file_paths}")
        
    for path in file_paths:
        with open(path, 'r') as file:
            data = yaml.safe_load(file)
            wasm_settings = data['build']['wasm']
            wasm_strategy = wasm_settings['strategy']
            if not wasm_strategy: continue

            tool_name = data["name"]
        
            package_name = data["build"]["wasm"].get("biowasm",{}).get("package", "")
            if not package_name:
                package_name = tool_name
            
            print(f"Attempting to build: {tool_name}")
                        
            def build_biowasm_wrapper():
                return build_biowasm(package_name, data["version"].split("-")[0], output_dir=build_dir)

            def build_emscripten_wrapper():
                source = (
                    data["source"]["repo"],
                    data["source"]["tag"],
                    data["source"]["commit"]
                )
                return build_emscripten(tool_name, wasm_settings["emscripten"], source, output_dir=build_dir)

            strategies = {
                "biowasm": [build_biowasm_wrapper],
                "emscripten": [build_emscripten_wrapper],
                "auto": [build_biowasm_wrapper, build_emscripten_wrapper],
            }

            output_dir = None
            for builder in strategies.get(wasm_strategy, []):
                output_dir = builder()
                if output_dir: break
            
            download_github_license(data["source"]["repo"], f"{output_dir}/LICENSE")
            
            if data["kind"] == "suite":
                for operation in data["suite"]["operations"]:
                    plugin_dir = f"{registry_dir}/{operation['opId']}/{data['version']}"
                    os.makedirs(plugin_dir, exist_ok=True)

                    for runtime in data["runtime"]["modes"]:
                        os.makedirs(f"{plugin_dir}/runtime/{runtime}", exist_ok=True)
                    
                    # TODO: other runtimes
                    
                    bin_name = operation["bin"]
                    wasm_dir = f"{plugin_dir}/runtime/wasm"
                    
                    # TODO deal with shared binaries
                    shutil.copyfile(f"{output_dir}/{bin_name}.js", f"{wasm_dir}/{bin_name}.js")
                    shutil.copyfile(f"{output_dir}/{bin_name}.wasm", f"{wasm_dir}/{bin_name}.wasm")

                    bundle = {
                        "id": operation["opId"],
                        "name": operation["opId"].replace(".","_"),
                        "description": operation["description"],
                        "version": data["version"],
                        "manifest": {
                            "io": operation["io"],
                            "parameters": operation["parameters"]
                        },
                        "limits": {},
                        "runtime": {
                            "modes": data["runtime"]["modes"],
                            # TODO don't hardcode the modes
                            "wasm": {
                                "wasm_digest": generate_digest(f"{wasm_dir}/{bin_name}.wasm"),
                                "js_digest":generate_digest(f"{wasm_dir}/{bin_name}.js"),
                            },
                        }
                    }
                    
                    with open(f"{plugin_dir}/bundle.json", "w") as f:
                        json.dump(bundle, f, indent=4)
                    
                    shutil.copyfile(f"{output_dir}/LICENSE", f"{plugin_dir}/LICENSE")
                    #TODO sbom.json
        
    shutil.rmtree(build_dir)