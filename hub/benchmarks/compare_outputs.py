
import os
import json
import shutil
from pathlib import Path

import dotenv
import oras.client

from tests.test import test_tools


dotenv.load_dotenv()

REGISTRY_USERNAME = os.getenv("REGISTRY_USERNAME", "username")
REGISTRY_PASSWORD = os.getenv("REGISTRY_PASSWORD", "password")
REGISTRY_INSECURE = os.getenv("REGISTRY_INSECURE", "true").lower() == "true"
ORAS_AUTH_BACKEND = os.getenv("ORAS_AUTH_BACKEND", "token")
CATALOG_PACKAGE = os.getenv("CATALOG_PACKAGE", "biochef-plugins")

def download_bundles(registry_url, output_dir):
    output_dir.mkdir(parents=True)

    client = oras.client.OrasClient(
        hostname=registry_url,
        insecure=REGISTRY_INSECURE,
        auth_backend=ORAS_AUTH_BACKEND,
    )

    client.login(username=REGISTRY_USERNAME, password=REGISTRY_PASSWORD)

    client.pull(
        target=f"{registry_url}/{CATALOG_PACKAGE}-index:index",
        outdir=str(output_dir),
    )

    with open(output_dir / "index.json") as f:
        registry_index = json.load(f)

    for repo in registry_index:
        bundle_dir = output_dir / repo

        client.pull(
            target=f"{registry_url}/{repo}:latest",
            outdir=str(bundle_dir),
        )

        with open(bundle_dir / "bundle.json") as f:
            bundle = json.load(f)

        tool_id = bundle["id"]
        bin_name = bundle["bin"]
        version_dir = output_dir / tool_id / "latest"

        version_dir.mkdir(parents=True, exist_ok=True)

        for item in bundle_dir.iterdir():
            if item.name == "bundle.json":
                target = version_dir
            elif item.name == bin_name:
                target = version_dir / "runtime" / "native"
            elif item.name.startswith(bin_name + ".") and item.suffix in (".js", ".wasm"):
                target = version_dir / "runtime" / "wasm"
            elif item.name.startswith(bin_name + ".") and item.suffix == ".data":
                for runtime in ("native", "wasm"):
                    target = version_dir / "runtime" / runtime
                    target.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, target / item.name)
                continue
            else:
                target = version_dir

            target.mkdir(parents=True, exist_ok=True)
            shutil.move(str(item), target / item.name)

            if item.name == bin_name:
                (target / item.name).chmod((target / item.name).stat().st_mode | 0o111)

        shutil.rmtree(bundle_dir)

    (output_dir / "index.json").unlink()


def compare_outputs(registry_url, output_dir="comparison-bundles"):
    output_dir = Path(output_dir)

    if not output_dir.exists():
        download_bundles(registry_url, output_dir)

    failed = test_tools(output_dir)

    return failed