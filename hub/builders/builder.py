import json
import yaml
import os
import shutil
from datetime import datetime
import hashlib

from builders.biowasm import build as build_biowasm
from builders.emscripten import build as build_emscripten

def generate_digest(file_path: str) -> str:
    sha256_hash = hashlib.sha256()
    
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            sha256_hash.update(chunk)
    
    return f"sha256:{sha256_hash.hexdigest()}"

def build_registry(file_paths, build_dir):
    print(f"Building recipes: {file_paths}")
    
    os.makedirs("registry/plugins", exist_ok=True)
    plugins_dir = "registry/plugins"
    
    index_file = "registry/index.json"
    if os.path.exists(index_file):
        with open(index_file, "r") as f:
            index = json.load(f)
    else:
        index = {
            "version": 1,
            "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "signature": "",
            "plugins": []
        }
        
    for path in file_paths:
        with open(path, 'r') as file:
            data = yaml.safe_load(file)
            wasm_settings = data['build']['wasm']
            wasm_strategy = wasm_settings['strategy']
            if not wasm_strategy: continue

            tool_name = data["build"]["wasm"].get("biowasm",{}).get("package", "")
            if not tool_name:
                tool_name = data["name"]
            
            print(f"Attempting to build: {tool_name}")
            
            def build_biowasm_wrapper():
                return build_biowasm(tool_name, data["version"].split("-")[0], output_dir=build_dir)

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
            
            if data["kind"] == "suite":
                for operation in data["suite"]["operations"]:
                    plugin_dir = f"{plugins_dir}/{operation["opId"]}/{data["version"]}"
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
                                "wasm_url": f"{wasm_dir}/{bin_name}.wasm",
                                "js_url": f"{wasm_dir}/{bin_name}.js",
                                "sri": {
                                    "wasm": generate_digest(f"{wasm_dir}/{bin_name}.wasm"),
                                    "js":generate_digest(f"{wasm_dir}/{bin_name}.js"),
                                }
                            },
                        }
                    }
                    
                    with open(f"{plugin_dir}/bundle.json", "w") as f:
                        json.dump(bundle, f, indent=4)

                    if not any(p["id"] == operation["opId"] for p in index["plugins"]):
                        plugin = {
                            "id": operation["opId"],
                            "version": data["version"],
                            "bundle_url": f"{plugin_dir}/bundle.json",
                            "digest": generate_digest(f"{plugin_dir}/bundle.json"),
                            "status": data["status"]
                        }
                        index["plugins"].append(plugin)

    index["version"] += 1
    index["generated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(index_file, "w") as f:
        json.dump(index, f, indent=4)
        
    shutil.rmtree(build_dir)