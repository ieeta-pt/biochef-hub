import argparse
import json
import os

BUILD_FILE = ".build" # file containing the validation results
BUILD_DIR = "build" # directory where the builders should output the results
REGISTRY_DIR = "registry"

def get_valid_recipes():
    if os.path.exists(BUILD_FILE):
        with open(BUILD_FILE) as f:
            build_data = json.load(f)
            return build_data["paths"]
    else:
        print("No validated paths found. Run validation first.")
        return None

def validate_cmd(args):
    paths = args.paths
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
    from builders.builder import build_plugins

    recipes = get_valid_recipes()
    if not recipes: return
    build_plugins(recipes, BUILD_DIR, REGISTRY_DIR)

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
    from publish.publish import publish_plugins

    registry_url = args.registry
    publish_plugins(registry_url, REGISTRY_DIR)

def index_cmd(args):
    #TODO
    pass

def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("paths", nargs="+", default="biochef.yaml", help="Path to the files to validate")
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
    publish_parser.add_argument('--registry', required=True, help="URL of the registry to publish to")
    publish_parser.set_defaults(func=publish_cmd)

    index_parser = subparsers.add_parser("index")
    index_parser.set_defaults(func=index_cmd)

    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
