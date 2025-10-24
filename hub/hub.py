import argparse
import glob
import json
import yaml
import os

from builders.biowasm import build as build_biowasm
from builders.emscripten import build as build_emscripten

BUILD_FILE = ".build"

def validate_cmd(args):
    path = args.path
    paths = glob.glob(path)
    if not paths:
        raise argparse.ArgumentError(None, "Path provided does not exist")
        
    print(f"Validating files: {paths}")
    #TODO actual validation logic

    build_data = {
        "paths": paths
    }
    with open(BUILD_FILE, "w") as f:
        json.dump(build_data, f)

def build_cmd(args):
    if os.path.exists(BUILD_FILE):
        with open(BUILD_FILE) as f:
            build_data = json.load(f)
    else:
        print("No validated paths found. Run validation first.")

    paths = build_data["paths"]
    print(f"Building files: {paths}")
    
    for path in paths:
        with open(path, 'r') as file:
            data = yaml.safe_load(file)
            wasm_settings = data['build']['wasm']
            wasm_strategy = wasm_settings['strategy']
            if not wasm_strategy: continue

            tool_name = data["build"]["wasm"].get("biowasm",{}).get("package", "")
            if not tool_name:
                tool_name = data["name"]
            
            print(f"Attempting to compile {tool_name}")
            if wasm_strategy == "auto":
                if not build_biowasm(tool_name, data["version"].split("-")[0]):
                    print("Could not compile with biowasm, trying with emscripten...")
                    source = (data["source"]["repo"],data["source"]["tag"],data["source"]["commit"])
                    if not build_emscripten(tool_name, wasm_settings['emscripten'], source):
                        print("Could not compile with emscripten.")

def test_cmd(args):
    #TODO
    pass

def sbom_cmd(args):
    #TODO
    pass

def attest_cmd(args):
    #TODO
    pass

def publish_cmd(args):
    #TODO
    pass

def index_cmd(args):
    #TODO
    pass

def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("path", nargs="?", default="biochef.yaml", help="Path to the files to validate")
    validate_parser.set_defaults(func=validate_cmd)

    build_parser = subparsers.add_parser("build")
    build_parser.set_defaults(func=build_cmd)

    test_parser = subparsers.add_parser("test")
    test_parser.set_defaults(func=test_cmd)

    sbom_parser = subparsers.add_parser("sbom")
    sbom_parser.set_defaults(func=sbom_cmd)

    attest_parser = subparsers.add_parser("attest")
    attest_parser.set_defaults(func=attest_cmd)

    publish_parser = subparsers.add_parser("publish")
    publish_parser.set_defaults(func=publish_cmd)

    index_parser = subparsers.add_parser("index")
    index_parser.set_defaults(func=index_cmd)

    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
