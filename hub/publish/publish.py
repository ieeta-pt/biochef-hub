from pathlib import Path
from typing import List
import json
import oras.client
import os


class RegistryFile:
    def __init__(self, path: str | Path, media_type: str):
        self.path = Path(path)
        self.media_type = media_type

    def __str__(self):
        return f"{self.path}:{self.media_type}"


def get_oras_client(registry_url):
    if "localhost" in registry_url:
        client = oras.client.OrasClient(hostname=registry_url, insecure=True)
    else:
        username = os.getenv("REGISTRY_USERNAME")
        token = os.getenv("REGISTRY_PASSWORD")

        if not username or not token:
            raise Exception("Registry username or password missing")

        client = oras.client.OrasClient()
        client.login(username=username, password=token)

    return client


def publish_plugin(registry_url, plugin_id, plugin_version, files: List[RegistryFile]):

    client = get_oras_client(registry_url)

    annotations = {
        "org.opencontainers.image.title": f"BioChef Plugin {plugin_id}",
        "biochef.plugin.id": plugin_id,
        "biochef.plugin.version": plugin_version,
        # "biochef.plugin.category": "info",
        # "biochef.bundle.format": "v1",
        # "biochef.bundle.sbom": "sbom.json"
    }

    target = f"biochef-plugins-{plugin_id}"
    target = target.lower()
    client.push(
        target=f'{registry_url}/{target}:{plugin_version}',
        files=files,
        manifest_annotations=annotations,
    )

    # TODO figure out a way to tag without pushing
    client.push(
        target=f'{registry_url}/{target}:latest',
        files=files,
        manifest_annotations=annotations,
    )

    return target


def publish_index(registry_url, plugin_dict):
    index = {}

    for package, bundle in plugin_dict.items():
        index[package] = {
            "name": bundle.get("name"),
            "description": bundle.get("description"),
            "category": bundle.get("category"),
            "inputTypes": [t for inp in bundle["manifest"]["io"]["inputs"] for t in inp["types"]],
            "outputTypes": [t for inp in bundle["manifest"]["io"]["outputs"] for t in inp["types"]]
        }

    index_path = Path("registry/index.json")
    index_path.write_text(json.dumps(index, indent=2))

    client = get_oras_client(registry_url)
    
    client.push(
        target=f"{registry_url}/biochef-plugins-index:index",
        files=[RegistryFile(index_path, media_type="application/json")],
        manifest_annotations={
            "org.opencontainers.image.title": "BioChef Plugin Index",
            "biochef.index.format": "v1"
        },
    )


media_types = {
    ".json": "application/json",
    ".wasm": "application/wasm",
    ".js": "text/javascript",
    ".txt": "text/plain",
    ".md": "text/markdown",
    "LICENSE": "text/plain",
    "sbom.json": "application/vnd.cyclonedx+json",
    "bundle.json": "application/vnd.biochef.bundle+json"
}


def get_media_type(file: Path) -> str:
    if file.name in media_types:
        return media_types[file.name]

    return media_types.get(file.suffix, "application/vnd.oci.image.layer.v1.tar")


def publish_plugins(registry_url, registry_dir):
    registry_path = Path(registry_dir)
    plugin_dict = {}

    for plugin_folder in registry_path.iterdir():
        if not plugin_folder.is_dir():
            continue

        plugin_id = plugin_folder.name

        for version_folder in plugin_folder.iterdir():
            if not version_folder.is_dir():
                continue
            plugin_version = version_folder.name

            files = [
                RegistryFile(file.resolve(), media_type=get_media_type(file))
                for file in version_folder.rglob("*")
                if file.is_file()
            ]

            bundle = next(
                (f for f in files if f.path.name == "bundle.json"), None)
            if not bundle:
                raise Exception(f"Plugin {plugin_id} is missing a bundle.json")

            package = publish_plugin(
                registry_url, plugin_id, plugin_version, files)

            with open(bundle.path) as f:
                plugin_dict[package] = json.load(f)

    publish_index(registry_url, plugin_dict)
