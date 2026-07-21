from pathlib import Path
from typing import Any, List
import json
import oras.client
import os
from dotenv import load_dotenv

from datetime import datetime, timezone

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
        load_dotenv()
        username = os.getenv("REGISTRY_USERNAME")
        token = os.getenv("REGISTRY_PASSWORD")
        oras_auth = os.getenv("ORAS_AUTH_BACKEND")
        oras_insecure = True if os.getenv("ORAS_INSECURE") == "true" else False

        if not username or not token:
            raise Exception("Registry username or password missing")
        if not oras_auth:
            raise Exception("Registry ORAS auth backend missing")

        client = oras.client.OrasClient(auth_backend=oras_auth, insecure=oras_insecure)
        client.login(username=username, password=token)

    return client


def publish_plugin(registry_url, plugin_id, plugin_version, files: List[RegistryFile], package_prefix="biochef-plugins-"):

    client = get_oras_client(registry_url)

    annotations = {
        "org.opencontainers.image.title": f"BioChef Plugin {plugin_id}",
        "biochef.plugin.id": plugin_id,
        "biochef.plugin.version": plugin_version,
        # "biochef.plugin.category": "info",
        # "biochef.bundle.format": "v1",
        # "biochef.bundle.sbom": "sbom.cdx.json"
    }

    target = _package_name(package_prefix, plugin_id)
    version_tag_ref = f"{registry_url}/{target}:{plugin_version}"
    latest_tag_ref = f"{registry_url}/{target}:latest"

    version_response = client.push(
        target=version_tag_ref,
        files=files,
        manifest_annotations=annotations,
    )
    version_digest = response_manifest_digest(version_response, version_tag_ref)

    # TODO figure out a way to tag without pushing
    latest_response = client.push(
        target=latest_tag_ref,
        files=files,
        manifest_annotations=annotations,
    )
    latest_digest = response_manifest_digest(latest_response, latest_tag_ref)
    if latest_digest != version_digest:
        raise RuntimeError(
            f"Registry returned different manifests for version and latest tags: "
            f"{version_digest} != {latest_digest}"
        )

    return {
        "operation_id": plugin_id,
        "version": plugin_version,
        "package": target,
        "version_tag_reference": version_tag_ref,
        "version_digest": version_digest,
        "digest_reference": f"{registry_url}/{target}@{version_digest}",
        "latest_tag_reference": latest_tag_ref,
        "latest_digest": latest_digest,
        "latest_digest_reference": f"{registry_url}/{target}@{latest_digest}",
    }


media_types = {
    ".json": "application/json",
    ".wasm": "application/wasm",
    ".js": "text/javascript",
    ".txt": "text/plain",
    ".md": "text/markdown",
    "LICENSE": "text/plain",
    "sbom.cdx.json": "application/vnd.cyclonedx+json",
    "bundle.json": "application/vnd.biochef.bundle+json"
}


def get_media_type(file: Path) -> str:
    if file.name in media_types:
        return media_types[file.name]

    return media_types.get(file.suffix, "application/octet-stream")


def publish_plugins(registry_url, registry_dir, package_prefix="biochef-plugins-"):
    registry_path = Path(registry_dir)
    if not registry_path.is_dir():
        raise RuntimeError(f"Registry directory does not exist: {registry_path}")

    prepared_artifacts = []
    package_owners = {}
    for plugin_folder in sorted(registry_path.iterdir()):
        if not plugin_folder.is_dir():
            continue

        plugin_id = plugin_folder.name
        package = _package_name(package_prefix, plugin_id)
        package_owner = package_owners.get(package)
        if package_owner is not None and package_owner != plugin_id:
            raise RuntimeError(
                f"Refusing to publish operation IDs {package_owner} and {plugin_id} "
                f"as the same package: {package}"
            )
        package_owners[package] = plugin_id

        for version_folder in sorted(plugin_folder.iterdir()):
            if not version_folder.is_dir():
                continue
            plugin_version = version_folder.name
            files = [
                RegistryFile(file.resolve(), media_type=get_media_type(file))
                for file in sorted(version_folder.rglob("*"))
                if file.is_file() and not file.is_symlink()
            ]
            file_paths = {
                file.path.relative_to(version_folder.resolve()).as_posix()
                for file in files
            }
            missing_files = [
                name
                for name in ("bundle.json", "build-evidence.json", "sbom.cdx.json")
                if name not in file_paths
            ]
            if missing_files:
                raise RuntimeError(
                    f"Plugin {plugin_id}@{plugin_version} is missing required publish files: "
                    f"{', '.join(missing_files)}"
                )
            prepared_artifacts.append((plugin_id, plugin_version, version_folder, files))

    if not prepared_artifacts:
        raise RuntimeError(f"No plugin bundles found under {registry_path}")

    publish_results: dict[str, Any] = {
        "schema": "biochef.publish-results.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "registry": registry_url,
        "package_prefix": package_prefix,
        "artifacts": [],
    }

    for plugin_id, plugin_version, version_folder, files in prepared_artifacts:
        artifact = publish_plugin(
            registry_url, plugin_id, plugin_version, files, package_prefix=package_prefix)
        artifact["bundle_path"] = str(version_folder)
        artifact["files"] = [
            {
                "path": str(file.path.relative_to(version_folder.resolve())),
                "media_type": file.media_type,
            }
            for file in files
        ]
        publish_results["artifacts"].append(artifact)

    results_path = registry_path / "publish-results.json"
    results_path.write_text(json.dumps(publish_results, indent=2, sort_keys=True) + "\n")
    return publish_results


def response_manifest_digest(response, target: str) -> str:
    digest = response.headers.get("Docker-Content-Digest") if response is not None else None
    if not digest:
        raise RuntimeError(f"Registry did not return Docker-Content-Digest for {target}")
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        raise RuntimeError(f"Registry returned unsupported manifest digest for {target}: {digest}")
    encoded = digest.removeprefix("sha256:")
    if len(encoded) != 64 or any(char not in "0123456789abcdefABCDEF" for char in encoded):
        raise RuntimeError(f"Registry returned invalid SHA-256 manifest digest for {target}: {digest}")
    return digest


def _package_name(package_prefix: str, plugin_id: str) -> str:
    return f"{package_prefix}{plugin_id}".lower()
