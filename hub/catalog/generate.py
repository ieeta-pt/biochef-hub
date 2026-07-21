import base64
import binascii
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey, EllipticCurvePublicKey
from cryptography.hazmat.primitives.hashes import SHA256

from builders.bundle_evidence import is_sha256_digest, sha256_hex
from publish.publish import RegistryFile, get_oras_client, response_manifest_digest
from signing.verify import VerificationError, verification_evidence_digests


CATALOG_SCHEMA = "biochef.verified-catalog.v1"
CATALOG_SIGNATURE_SCHEMA = "biochef.catalog-signature.v1"
CATALOG_RESULTS_SCHEMA = "biochef.catalog-publish-results.v1"
CATALOG_MEDIA_TYPE = "application/vnd.biochef.verified-catalog+json"
CATALOG_SIGNATURE_MEDIA_TYPE = "application/vnd.biochef.catalog-signature+json"
OCI_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"


class CatalogError(RuntimeError):
    pass


@dataclass
class CatalogSummary:
    entries: int
    catalog_path: Path
    signature_path: Path
    digest_reference: str
    latest_digest_reference: str


def generate_sign_and_publish_catalog(
    registry_dir: str | Path = "registry",
    publish_results_path: str | Path | None = None,
    verification_report_path: str | Path | None = None,
    signing_policy_path: str | Path | None = None,
    registry_url: str | None = None,
    package_prefix: str = "biochef-plugins-",
    catalog_version: str | None = None,
    channel: str = "latest",
    sequence: int | None = None,
    expires_days: int = 30,
    private_jwk_path: str | Path | None = None,
    private_jwk_json: str | None = None,
) -> CatalogSummary:
    registry_path = Path(registry_dir).resolve()
    if not registry_path.is_dir():
        raise CatalogError(f"Registry directory does not exist: {registry_path}")
    if not signing_policy_path:
        raise CatalogError("A signing policy is required")
    if expires_days < 1:
        raise CatalogError("expires_days must be at least 1")

    results_path = Path(publish_results_path or registry_path / "publish-results.json").resolve()
    report_path = Path(verification_report_path or registry_path / "signing-verification-report.json").resolve()
    policy_path = Path(signing_policy_path).resolve()

    publish_results = _read_json(results_path)
    verification_report = _read_json(report_path)
    signing_policy = _read_json(policy_path)
    _validate_publish_results(publish_results, results_path)
    _validate_verification_report(verification_report, report_path)
    _validate_verification_report_binding(verification_report, policy_path, results_path)

    private_key, public_jwk = _load_private_key(private_jwk_path, private_jwk_json)

    registry = registry_url or publish_results["registry"]
    if registry != publish_results["registry"]:
        raise CatalogError("Catalog registry does not match publish-results registry")

    existing_catalog = _pull_verified_catalog(
        registry_url=registry,
        package_prefix=package_prefix,
        channel=channel,
        public_key=private_key.public_key(),
        public_jwk=public_jwk,
        signing_policy=signing_policy,
        policy_path=policy_path,
    )

    version = catalog_version or _catalog_version_from_environment()
    generated_at = datetime.now(timezone.utc)
    next_sequence = sequence if sequence is not None else int(generated_at.timestamp())
    if existing_catalog:
        previous_sequence = existing_catalog["sequence"]
        if sequence is not None and sequence <= previous_sequence:
            raise CatalogError("Catalog sequence must be greater than the existing catalog sequence")
        next_sequence = max(next_sequence, previous_sequence + 1)
    catalog = {
        "schema": CATALOG_SCHEMA,
        "generated_at": generated_at.isoformat(),
        "expires_at": (generated_at + timedelta(days=expires_days)).isoformat(),
        "channel": channel,
        "version": version,
        "sequence": next_sequence,
        "registry": registry,
        "package_prefix": package_prefix,
        "signing_policy": {
            "digest": f"sha256:{sha256_hex(policy_path)}",
            "certificate_identity": signing_policy.get("certificate_identity"),
            "certificate_oidc_issuer": signing_policy.get("certificate_oidc_issuer"),
            "slsa_predicate_type": signing_policy.get("slsa_predicate_type"),
            "slsa_build_type": signing_policy.get("slsa_build_type"),
        },
        "source": {
            "recipes_repository": os.getenv("GITHUB_REPOSITORY"),
            "recipes_ref": os.getenv("GITHUB_REF"),
            "recipes_sha": os.getenv("GITHUB_SHA"),
            "workflow_ref": os.getenv("GITHUB_WORKFLOW_REF"),
            "run_id": os.getenv("GITHUB_RUN_ID"),
        },
        "packages": dict(existing_catalog["packages"]) if existing_catalog else {},
    }

    report_by_artifact = _verification_report_by_artifact(verification_report)
    for artifact in publish_results["artifacts"]:
        artifact_name = f"{artifact.get('operation_id')}@{artifact.get('version')}"
        report_entry = report_by_artifact.get(artifact_name)
        if not report_entry:
            raise CatalogError(f"Refusing to catalog unverified artifact: {artifact_name}")
        _validate_verified_artifact(artifact, report_entry)
        entry = _catalog_entry(registry_path, artifact, report_entry, signing_policy)
        catalog["packages"][entry["package"]] = entry

    catalog_path = registry_path / "index.json"
    catalog_bytes = _json_bytes(catalog)
    catalog_path.write_bytes(catalog_bytes)

    signature_path = registry_path / "index.sig.json"
    signature_path.write_bytes(
        _json_bytes(
            _signature_document(
                catalog_bytes=catalog_bytes,
                private_key=private_key,
                public_jwk=public_jwk,
            )
        )
    )

    publish_result = _publish_catalog(
        registry_url=registry,
        catalog_path=catalog_path,
        signature_path=signature_path,
        package_prefix=package_prefix,
        catalog_version=version,
        channel=channel,
    )
    results_path = registry_path / "catalog-publish-results.json"
    results_path.write_bytes(_json_bytes(publish_result) + b"\n")

    return CatalogSummary(
        entries=len(catalog["packages"]),
        catalog_path=catalog_path,
        signature_path=signature_path,
        digest_reference=publish_result["digest_reference"],
        latest_digest_reference=publish_result["latest_digest_reference"],
    )


def _catalog_entry(registry_path: Path, artifact: dict[str, Any], report_entry: dict[str, Any], signing_policy: dict[str, Any]) -> dict[str, Any]:
    operation_id = artifact["operation_id"]
    version = artifact["version"]
    package = artifact["package"]
    bundle_dir = (registry_path / operation_id / version).resolve()
    try:
        bundle_dir.relative_to(registry_path)
    except ValueError as exc:
        raise CatalogError(f"Unsafe bundle path for {operation_id}@{version}") from exc

    try:
        local_evidence = verification_evidence_digests(bundle_dir)
    except VerificationError as exc:
        raise CatalogError(str(exc)) from exc
    if report_entry.get("evidence") != local_evidence:
        raise CatalogError(
            f"Verified evidence files changed after verification for {operation_id}@{version}"
        )

    bundle_path = bundle_dir / "bundle.json"
    bundle = _read_json(bundle_path)
    if bundle.get("id") != operation_id or bundle.get("version") != version:
        raise CatalogError(
            f"Bundle identity does not match published artifact {operation_id}@{version}"
        )
    runtime = bundle.get("runtime") or {}
    wasm = runtime.get("wasm") or {}
    wasm_digest = wasm.get("wasm_digest")
    js_digest = wasm.get("js_digest")
    if not is_sha256_digest(wasm_digest) or not is_sha256_digest(js_digest):
        raise CatalogError(f"Bundle {operation_id}@{version} lacks valid JS/WASM digests")
    runtime_files = wasm.get("files") or [
        {"path": f"runtime/wasm/{bundle.get('bin')}.wasm", "digest": wasm_digest},
        {"path": f"runtime/wasm/{bundle.get('bin')}.js", "digest": js_digest},
    ]
    _validate_wasm_runtime_files(
        runtime_files,
        bin_name=bundle.get("bin"),
        wasm_digest=wasm_digest,
        js_digest=js_digest,
        bundle_name=f"{operation_id}@{version}",
    )

    digest_reference = artifact.get("digest_reference")
    if not isinstance(digest_reference, str) or "@sha256:" not in digest_reference:
        raise CatalogError(f"Artifact {operation_id}@{version} lacks immutable digest reference")

    return {
        "id": bundle.get("id"),
        "name": bundle.get("name"),
        "description": bundle.get("description"),
        "category": bundle.get("category"),
        "inputTypes": sorted({t for item in bundle.get("io", {}).get("inputs", []) for t in item.get("types", [])}),
        "outputTypes": sorted({t for item in bundle.get("io", {}).get("outputs", []) for t in item.get("types", [])}),
        "package": package,
        "version": version,
        "digest_reference": digest_reference,
        "manifest_digest": artifact.get("version_digest"),
        "runtime": {
            "modes": runtime.get("modes", []),
            "wasm": {
                "wasm_digest": wasm_digest,
                "js_digest": js_digest,
                "files": runtime_files,
            },
        },
        "evidence": {
            "bundle_json": local_evidence["bundle_json"],
            "sbom_cdx_json": local_evidence["sbom_cdx_json"],
            "build_evidence_json": local_evidence["build_evidence_json"],
            "attestations": {
                "discovery": "oci-referrers",
                "subject": digest_reference,
                "cyclonedx": {
                    "predicate_type": "https://cyclonedx.org/bom",
                },
                "slsa_provenance": {
                    "predicate_type": signing_policy["slsa_predicate_type"],
                    "build_type": signing_policy["slsa_build_type"],
                },
            },
        },
        "verification": {
            "status": report_entry["status"],
            "cosign_signature": "passed",
            "cyclonedx_attestation": "passed",
            "slsa_attestation": "passed",
            "certificate_identity": signing_policy["certificate_identity"],
            "certificate_oidc_issuer": signing_policy["certificate_oidc_issuer"],
            "slsa_predicate_type": signing_policy["slsa_predicate_type"],
            "slsa_build_type": signing_policy["slsa_build_type"],
        },
    }


def _validate_wasm_runtime_files(runtime_files: Any, *, bin_name: Any, wasm_digest: str, js_digest: str, bundle_name: str) -> None:
    if not isinstance(bin_name, str) or not bin_name or bin_name in {".", ".."} or "/" in bin_name or "\\" in bin_name:
        raise CatalogError(f"Bundle {bundle_name} has an unsafe binary name")
    if not isinstance(runtime_files, list):
        raise CatalogError(f"Bundle {bundle_name} has invalid WASM runtime file evidence")

    allowed_paths = {
        f"runtime/wasm/{bin_name}.js",
        f"runtime/wasm/{bin_name}.wasm",
    }
    files_by_path = {}
    for item in runtime_files:
        if not isinstance(item, dict):
            raise CatalogError(f"Bundle {bundle_name} has invalid WASM runtime file evidence")
        path = item.get("path")
        digest = item.get("digest")
        candidate = Path(path) if isinstance(path, str) else None
        if (
            candidate is None
            or "\\" in path
            or candidate.is_absolute()
            or ".." in candidate.parts
            or candidate.as_posix() not in allowed_paths
            or not is_sha256_digest(digest)
            or path in files_by_path
        ):
            raise CatalogError(f"Bundle {bundle_name} has invalid WASM runtime file evidence")
        files_by_path[path] = digest

    expected = {
        f"runtime/wasm/{bin_name}.wasm": wasm_digest,
        f"runtime/wasm/{bin_name}.js": js_digest,
    }
    if files_by_path != expected:
        raise CatalogError(f"Bundle {bundle_name} WASM runtime files do not match declared digests")


def _pull_verified_catalog(
    registry_url: str,
    package_prefix: str,
    channel: str,
    public_key: EllipticCurvePublicKey,
    public_jwk: dict[str, Any],
    signing_policy: dict[str, Any],
    policy_path: Path,
) -> dict[str, Any] | None:
    client = get_oras_client(registry_url)
    package = f"{package_prefix}index".lower()
    target = f"{registry_url}/{package}:{channel}"
    container = client.get_container(target)
    client.auth.load_configs(container)
    manifest_url = f"{client.prefix}://{container.manifest_url()}"
    headers = {**getattr(client, "headers", {}), "Accept": OCI_MANIFEST_MEDIA_TYPE}
    response = client.do_request(manifest_url, "GET", headers=headers)
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise CatalogError(
            f"Could not retrieve existing catalog {target}: HTTP {response.status_code}"
        )
    try:
        manifest = response.json()
    except (TypeError, ValueError) as exc:
        raise CatalogError(f"Existing catalog manifest is not valid JSON: {target}") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("layers"), list):
        raise CatalogError(f"Existing catalog manifest is malformed: {target}")

    catalog_bytes = _catalog_layer_bytes(
        client, container, manifest, "index.json", CATALOG_MEDIA_TYPE
    )
    signature_bytes = _catalog_layer_bytes(
        client,
        container,
        manifest,
        "index.sig.json",
        CATALOG_SIGNATURE_MEDIA_TYPE,
    )
    catalog = _verify_catalog_signature(
        catalog_bytes, signature_bytes, public_key, public_jwk
    )
    _validate_existing_catalog(
        catalog,
        registry_url=registry_url,
        package_prefix=package_prefix,
        channel=channel,
        signing_policy=signing_policy,
        policy_path=policy_path,
    )
    return catalog


def _catalog_layer_bytes(client: Any, container: Any, manifest: dict[str, Any], title: str, media_type: str) -> bytes:
    matches = [
        layer
        for layer in manifest["layers"]
        if isinstance(layer, dict)
        and layer.get("mediaType") == media_type
        and (layer.get("annotations") or {}).get("org.opencontainers.image.title") == title
    ]
    if len(matches) != 1:
        raise CatalogError(f"Existing catalog must contain exactly one {title} layer")
    descriptor = matches[0]
    digest = descriptor.get("digest")
    size = descriptor.get("size")
    if not is_sha256_digest(digest) or not isinstance(size, int) or size < 0:
        raise CatalogError(f"Existing catalog {title} layer descriptor is invalid")
    response = client.get_blob(container, digest)
    if response.status_code != 200:
        raise CatalogError(
            f"Could not retrieve existing catalog {title}: HTTP {response.status_code}"
        )
    content = response.content
    if len(content) != size:
        raise CatalogError(f"Existing catalog {title} layer size does not match its descriptor")
    actual_digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
    if actual_digest != digest.lower():
        raise CatalogError(f"Existing catalog {title} layer digest does not match its descriptor")
    return content


def _verify_catalog_signature(
    catalog_bytes: bytes,
    signature_bytes: bytes,
    public_key: EllipticCurvePublicKey,
    public_jwk: dict[str, Any],
) -> dict[str, Any]:
    signature_document = _json_object_bytes(signature_bytes, "catalog signature")
    if signature_document.get("schema") != CATALOG_SIGNATURE_SCHEMA:
        raise CatalogError("Existing catalog signature schema is invalid")
    if signature_document.get("alg") != "ES256":
        raise CatalogError("Existing catalog signature algorithm is invalid")
    if signature_document.get("keyid") != public_jwk["kid"]:
        raise CatalogError("Existing catalog was signed by a different catalog key")
    if signature_document.get("signed_media_type") != CATALOG_MEDIA_TYPE:
        raise CatalogError("Existing catalog signature media type is invalid")
    expected_digest = f"sha256:{hashlib.sha256(catalog_bytes).hexdigest()}"
    if signature_document.get("signed_digest") != expected_digest:
        raise CatalogError("Existing catalog bytes do not match the signed digest")
    encoded_signature = signature_document.get("signature")
    if not isinstance(encoded_signature, str):
        raise CatalogError("Existing catalog signature is missing")
    try:
        signature = _b64url_decode(encoded_signature)
    except CatalogError as exc:
        raise CatalogError("Existing catalog signature is not valid base64url") from exc
    if len(signature) != 64:
        raise CatalogError("Existing catalog signature has an invalid length")
    r = int.from_bytes(signature[:32], "big")
    s = int.from_bytes(signature[32:], "big")
    try:
        public_key.verify(
            utils.encode_dss_signature(r, s),
            catalog_bytes,
            ec.ECDSA(SHA256()),
        )
    except InvalidSignature as exc:
        raise CatalogError("Existing catalog signature verification failed") from exc
    return _json_object_bytes(catalog_bytes, "catalog")


def _validate_existing_catalog(
    catalog: dict[str, Any],
    registry_url: str,
    package_prefix: str,
    channel: str,
    signing_policy: dict[str, Any],
    policy_path: Path,
) -> None:
    if catalog.get("schema") != CATALOG_SCHEMA:
        raise CatalogError("Existing catalog schema is invalid")
    if catalog.get("registry") != registry_url:
        raise CatalogError("Existing catalog registry does not match this publication")
    if catalog.get("package_prefix") != package_prefix:
        raise CatalogError("Existing catalog package prefix does not match this publication")
    if catalog.get("channel") != channel:
        raise CatalogError("Existing catalog channel does not match this publication")
    sequence = catalog.get("sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        raise CatalogError("Existing catalog sequence is invalid")
    expires_at = catalog.get("expires_at")
    try:
        expiry = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
    except ValueError as exc:
        raise CatalogError("Existing catalog expiry is invalid") from exc
    if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
        raise CatalogError("Existing catalog has expired and cannot be incrementally updated")

    expected_policy = {
        "digest": f"sha256:{sha256_hex(policy_path)}",
        "certificate_identity": signing_policy.get("certificate_identity"),
        "certificate_oidc_issuer": signing_policy.get("certificate_oidc_issuer"),
        "slsa_predicate_type": signing_policy.get("slsa_predicate_type"),
        "slsa_build_type": signing_policy.get("slsa_build_type"),
    }
    if catalog.get("signing_policy") != expected_policy:
        raise CatalogError("Existing catalog was generated under a different signing policy")
    packages = catalog.get("packages")
    if not isinstance(packages, dict) or not packages:
        raise CatalogError("Existing catalog contains no packages")
    for package, entry in packages.items():
        _validate_preserved_catalog_entry(
            package,
            entry,
            registry_url=registry_url,
            package_prefix=package_prefix,
            signing_policy=signing_policy,
        )


def _validate_preserved_catalog_entry(
    package: Any,
    entry: Any,
    registry_url: str,
    package_prefix: str,
    signing_policy: dict[str, Any],
) -> None:
    if not isinstance(package, str) or not isinstance(entry, dict):
        raise CatalogError("Existing catalog contains a malformed package entry")
    operation_id = entry.get("id")
    expected_package = f"{package_prefix}{operation_id}".lower() if isinstance(operation_id, str) else None
    if package != expected_package or entry.get("package") != package:
        raise CatalogError(f"Existing catalog package identity is invalid: {package!r}")
    if not isinstance(entry.get("version"), str) or not entry["version"]:
        raise CatalogError(f"Existing catalog package lacks a version: {package}")
    manifest_digest = entry.get("manifest_digest")
    digest_reference = entry.get("digest_reference")
    if not is_sha256_digest(manifest_digest) or digest_reference != f"{registry_url}/{package}@{manifest_digest}":
        raise CatalogError(f"Existing catalog package lacks a valid immutable reference: {package}")
    verification = entry.get("verification")
    expected_verification = {
        "status": "passed",
        "cosign_signature": "passed",
        "cyclonedx_attestation": "passed",
        "slsa_attestation": "passed",
        "certificate_identity": signing_policy.get("certificate_identity"),
        "certificate_oidc_issuer": signing_policy.get("certificate_oidc_issuer"),
        "slsa_predicate_type": signing_policy.get("slsa_predicate_type"),
        "slsa_build_type": signing_policy.get("slsa_build_type"),
    }
    if verification != expected_verification:
        raise CatalogError(f"Existing catalog package has invalid verification evidence: {package}")
    evidence = entry.get("evidence") or {}
    required_evidence = ("bundle_json", "sbom_cdx_json", "build_evidence_json")
    if any(not is_sha256_digest(evidence.get(name)) for name in required_evidence):
        raise CatalogError(f"Existing catalog package has invalid evidence digests: {package}")
    wasm = ((entry.get("runtime") or {}).get("wasm") or {})
    if not is_sha256_digest(wasm.get("wasm_digest")) or not is_sha256_digest(wasm.get("js_digest")):
        raise CatalogError(f"Existing catalog package has invalid runtime digests: {package}")


def _json_object_bytes(value: bytes, label: str) -> dict[str, Any]:
    try:
        document = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CatalogError(f"Existing {label} is not valid JSON") from exc
    if not isinstance(document, dict):
        raise CatalogError(f"Existing {label} must contain a JSON object")
    return document


def _publish_catalog(registry_url: str, catalog_path: Path, signature_path: Path, package_prefix: str, catalog_version: str, channel: str) -> dict[str, Any]:
    client = get_oras_client(registry_url)
    index_target = f"{package_prefix}index".lower()
    version_tag_ref = f"{registry_url}/{index_target}:{catalog_version}"
    latest_tag_ref = f"{registry_url}/{index_target}:{channel}"
    index_tag_ref = f"{registry_url}/{index_target}:index"
    files = [
        RegistryFile(catalog_path, media_type=CATALOG_MEDIA_TYPE),
        RegistryFile(signature_path, media_type=CATALOG_SIGNATURE_MEDIA_TYPE),
    ]
    annotations = {
        "org.opencontainers.image.title": "BioCHEF Verified Catalog",
        "biochef.catalog.format": CATALOG_SCHEMA,
        "biochef.catalog.version": catalog_version,
        "biochef.catalog.channel": channel,
    }
    version_response = client.push(target=version_tag_ref, files=files, manifest_annotations=annotations)
    version_digest = response_manifest_digest(version_response, version_tag_ref)
    latest_response = client.push(target=latest_tag_ref, files=files, manifest_annotations=annotations)
    latest_digest = response_manifest_digest(latest_response, latest_tag_ref)
    index_response = client.push(target=index_tag_ref, files=files, manifest_annotations=annotations)
    index_digest = response_manifest_digest(index_response, index_tag_ref)
    if len({version_digest, latest_digest, index_digest}) != 1:
        raise CatalogError(
            "Registry returned different catalog manifests for version, channel, and index tags"
        )
    return {
        "schema": CATALOG_RESULTS_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "registry": registry_url,
        "package": index_target,
        "version": catalog_version,
        "channel": channel,
        "version_tag_reference": version_tag_ref,
        "version_digest": version_digest,
        "digest_reference": f"{registry_url}/{index_target}@{version_digest}",
        "latest_tag_reference": latest_tag_ref,
        "latest_digest": latest_digest,
        "latest_digest_reference": f"{registry_url}/{index_target}@{latest_digest}",
        "index_tag_reference": index_tag_ref,
        "index_digest": index_digest,
        "index_digest_reference": f"{registry_url}/{index_target}@{index_digest}",
    }


def _signature_document(catalog_bytes: bytes, private_key: EllipticCurvePrivateKey, public_jwk: dict[str, Any]) -> dict[str, Any]:
    signature_der = private_key.sign(catalog_bytes, ec.ECDSA(SHA256()))
    r, s = utils.decode_dss_signature(signature_der)
    signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return {
        "schema": CATALOG_SIGNATURE_SCHEMA,
        "alg": "ES256",
        "keyid": public_jwk["kid"],
        "signed_media_type": "application/vnd.biochef.verified-catalog+json",
        "signed_digest": f"sha256:{hashlib.sha256(catalog_bytes).hexdigest()}",
        "signature": _b64url(signature),
    }


def _load_private_key(private_jwk_path: str | Path | None, private_jwk_json: str | None) -> tuple[EllipticCurvePrivateKey, dict[str, Any]]:
    jwk_text = private_jwk_json or os.getenv("BIOCHEF_CATALOG_SIGNING_PRIVATE_JWK")
    if private_jwk_path:
        try:
            jwk_text = Path(private_jwk_path).read_text(encoding="utf-8")
        except OSError as exc:
            raise CatalogError(f"Could not read catalog signing private JWK: {exc}") from exc
    if not jwk_text:
        raise CatalogError("Catalog signing private JWK is required")

    try:
        jwk = json.loads(jwk_text)
    except json.JSONDecodeError as exc:
        raise CatalogError(f"Catalog signing private JWK is invalid JSON: {exc}") from exc
    if not isinstance(jwk, dict):
        raise CatalogError("Catalog signing private JWK must be a JSON object")
    if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
        raise CatalogError("Catalog signing key must be an EC P-256 JWK")
    if jwk.get("alg") not in (None, "ES256") or jwk.get("use") not in (None, "sig"):
        raise CatalogError("Catalog signing private JWK must be usable for ES256 signatures")
    for key in ("x", "y", "d", "kid"):
        if not isinstance(jwk.get(key), str) or not jwk[key]:
            raise CatalogError(f"Catalog signing private JWK is missing {key}")

    decoded = {key: _b64url_decode(jwk[key]) for key in ("x", "y", "d")}
    if any(len(value) != 32 for value in decoded.values()):
        raise CatalogError("Catalog signing private JWK x, y, and d must be 32-byte values")
    try:
        public_numbers = ec.EllipticCurvePublicNumbers(
            x=int.from_bytes(decoded["x"], "big"),
            y=int.from_bytes(decoded["y"], "big"),
            curve=ec.SECP256R1(),
        )
        private_key = ec.EllipticCurvePrivateNumbers(
            int.from_bytes(decoded["d"], "big"), public_numbers
        ).private_key()
    except ValueError as exc:
        raise CatalogError("Catalog signing private JWK is not a valid P-256 key pair") from exc

    public_jwk = {
        "kty": "EC",
        "crv": "P-256",
        "x": jwk["x"],
        "y": jwk["y"],
        "kid": jwk["kid"],
        "alg": "ES256",
        "use": "sig",
    }
    return private_key, public_jwk


def _verification_report_by_artifact(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    entries = {}
    for artifact in report.get("artifacts") or []:
        if not isinstance(artifact, dict):
            raise CatalogError("Verification report contains a malformed artifact entry")
        name = f"{artifact.get('operation_id')}@{artifact.get('version')}"
        if name in entries:
            raise CatalogError(f"Verification report contains duplicate artifact {name}")
        entries[name] = artifact
    return entries


def _validate_verification_report_binding(
    report: dict[str, Any], policy_path: Path, publish_results_path: Path
) -> None:
    expected_policy_digest = f"sha256:{sha256_hex(policy_path)}"
    report_policy = report.get("policy") or {}
    if not isinstance(report_policy, dict) or report_policy.get("digest") != expected_policy_digest:
        raise CatalogError("Verification report was produced for a different signing policy")
    expected_results_digest = f"sha256:{sha256_hex(publish_results_path)}"
    report_results = report.get("publish_results") or {}
    if not isinstance(report_results, dict) or report_results.get("digest") != expected_results_digest:
        raise CatalogError("Verification report was produced for different publish results")


def _validate_verified_artifact(
    artifact: dict[str, Any], report_entry: dict[str, Any]
) -> None:
    artifact_name = f"{artifact.get('operation_id')}@{artifact.get('version')}"
    for field in ("operation_id", "version", "package", "digest_reference"):
        if report_entry.get(field) != artifact.get(field):
            raise CatalogError(
                f"Verification report {field} does not match publish results for {artifact_name}"
            )
    if report_entry.get("status") != "passed" or report_entry.get("failures") != []:
        raise CatalogError(f"Refusing to catalog unverified artifact: {artifact_name}")
    version_digest = artifact.get("version_digest")
    digest_reference = artifact.get("digest_reference")
    package = artifact.get("package")
    if not is_sha256_digest(version_digest):
        raise CatalogError(f"Publish results contain invalid version digest for {artifact_name}")
    if not isinstance(digest_reference, str) or not digest_reference.endswith(f"/{package}@{version_digest}"):
        raise CatalogError(f"Publish results digest reference is inconsistent for {artifact_name}")


def _validate_publish_results(publish_results: dict[str, Any], path: Path) -> None:
    if publish_results.get("schema") != "biochef.publish-results.v1":
        raise CatalogError(f"Invalid publish results schema: {path}")
    if not isinstance(publish_results.get("artifacts"), list) or not publish_results["artifacts"]:
        raise CatalogError(f"Publish results contain no artifacts: {path}")
    if not isinstance(publish_results.get("registry"), str) or not publish_results["registry"]:
        raise CatalogError(f"Publish results lack registry: {path}")
    seen_packages = set()
    for artifact in publish_results["artifacts"]:
        package = artifact.get("package") if isinstance(artifact, dict) else None
        if not isinstance(package, str) or not package or package in seen_packages:
            raise CatalogError(f"Publish results contain an invalid or duplicate package: {package!r}")
        seen_packages.add(package)


def _validate_verification_report(report: dict[str, Any], path: Path) -> None:
    if report.get("schema") != "biochef.signing-verification-report.v1":
        raise CatalogError(f"Invalid verification report schema: {path}")
    if report.get("status") != "passed":
        raise CatalogError(f"Refusing to generate catalog from failed verification report: {path}")
    if not isinstance(report.get("artifacts"), list) or not report["artifacts"]:
        raise CatalogError(f"Verification report contains no artifacts: {path}")
    if report.get("scanned") != len(report["artifacts"]):
        raise CatalogError(f"Verification report does not cover every artifact: {path}")


def _catalog_version_from_environment() -> str:
    run_id = os.getenv("GITHUB_RUN_ID")
    sha = os.getenv("GITHUB_SHA")
    if run_id and sha:
        return f"catalog-{run_id}-{sha[:12]}"
    return f"catalog-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"


def _read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise CatalogError(f"Refusing to read symlinked JSON file: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CatalogError(f"Missing JSON file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CatalogError(f"Invalid JSON file {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise CatalogError(f"JSON file must contain an object: {path}")
    return document


def _json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, indent=2, sort_keys=True, separators=(",", ": ")).encode("utf-8")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.b64decode(value + padding, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise CatalogError("Catalog signing private JWK contains invalid base64url") from exc
