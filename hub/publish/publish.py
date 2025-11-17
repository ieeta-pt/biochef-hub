from pathlib import Path
from typing import List
from pathlib import Path
import oras.client
import os

class RegistryFile:
    def __init__(self, path: str | Path, media_type: str):
        self.path = Path(path)
        self.media_type = media_type

    def __str__(self):
        return f"{self.path}:{self.media_type}"


def publish_plugin(registry_url, plugin_id, plugin_version, files: List[RegistryFile]):
    if "localhost" in registry_url: 
        client = oras.client.OrasClient(hostname=registry_url, insecure=True)
    else:
        username = os.getenv("GHCR_USERNAME")
        token = os.getenv("GHCR_TOKEN")
        print(username, token)
        
        if not username or not token:
            raise Exception("Username and password missing for GHCR")
        
        client = oras.client.OrasClient()
        client.login(username=username, password=token)
    
    # Manifest-level annotations (equivalent to --annotation flags)
    annotations = {
        "org.opencontainers.image.title": f"BioChef Plugin {plugin_id}",
        "biochef.plugin.id": plugin_id,
        "biochef.plugin.version": plugin_version,
        # "biochef.plugin.category": "info",
        # "biochef.bundle.format": "v1",
        # "biochef.bundle.sbom": "sbom.json"
    }

    target = f"{registry_url}/biochef-plugins-{plugin_id}"
    client.push(
        target=f'{target}:{plugin_version}',
        files=files,
        manifest_annotations=annotations,
    )

    # TODO figure out a way to tag without pushing
    client.push(
        target=f'{target}:latest',
        files=files,
        manifest_annotations=annotations,
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

            publish_plugin(registry_url, plugin_id, plugin_version, files)
