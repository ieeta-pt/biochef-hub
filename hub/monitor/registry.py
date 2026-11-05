"""Check, from outside and without credentials, that the registry still serves
what the signed catalog says it should.

Every check here is a read a browser could make, plus one unauthenticated
write that must be refused. Nothing needs registry credentials, so this can run
on a schedule from anywhere.
"""

import hashlib
import json
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePublicKey

from builders.container import is_sha256_digest
from catalog.generate import (
    CATALOG_MEDIA_TYPE,
    CATALOG_SIGNATURE_MEDIA_TYPE,
    OCI_MANIFEST_MEDIA_TYPE,
    CatalogError,
    _b64url_decode,
    _validate_existing_catalog,
    _verify_catalog_signature,
)
from signing.verify import VerificationError, load_policy, verify_cosign_signature


MANIFEST_ACCEPT = ",".join(
    [
        OCI_MANIFEST_MEDIA_TYPE,
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    ]
)
REFERRERS_TAG_PREFIX = "sha256-"
TIMEOUT_SECONDS = 30


class MonitorError(RuntimeError):
    pass


@dataclass
class MonitorIssue:
    subject: str
    code: str
    message: str


@dataclass
class MonitorReport:
    catalog_digest: str | None = None
    catalog_version: str | None = None
    catalog_sequence: int | None = None
    packages: int = 0
    tags_checked: int = 0
    signatures_checked: int = 0
    issues: list[MonitorIssue] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return bool(self.issues)

    def fail(self, subject: str, code: str, message: str) -> None:
        self.issues.append(MonitorIssue(subject, code, message))

    def as_dict(self) -> dict[str, Any]:
        return {
            "catalog_digest": self.catalog_digest,
            "catalog_version": self.catalog_version,
            "catalog_sequence": self.catalog_sequence,
            "packages": self.packages,
            "tags_checked": self.tags_checked,
            "signatures_checked": self.signatures_checked,
            "issues": [issue.__dict__ for issue in self.issues],
        }


class _Registry:
    """Anonymous reads against one registry, every manifest checked against
    the digest of the bytes that came back rather than a header."""

    def __init__(self, registry_url: str):
        self.registry_url = registry_url
        self.base = f"https://{registry_url}"
        self.session = requests.Session()

    def get(self, path: str, accept: str | None = None) -> requests.Response:
        headers = {"Accept": accept} if accept else {}
        return self.session.get(
            f"{self.base}{path}", headers=headers, timeout=TIMEOUT_SECONDS
        )

    def manifest(self, repository: str, reference: str) -> tuple[str, dict[str, Any]] | None:
        response = self.get(f"/v2/{repository}/manifests/{reference}", MANIFEST_ACCEPT)
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise MonitorError(
                f"{repository}:{reference}: manifest returned HTTP {response.status_code}"
            )
        digest = f"sha256:{hashlib.sha256(response.content).hexdigest()}"
        if is_sha256_digest(reference) and digest != reference:
            raise MonitorError(f"{repository}@{reference}: served bytes hash to {digest}")
        try:
            document = response.json()
        except ValueError as exc:
            raise MonitorError(f"{repository}:{reference}: manifest is not JSON") from exc
        return digest, document

    def blob(self, repository: str, descriptor: dict[str, Any]) -> bytes:
        digest = descriptor.get("digest")
        if not is_sha256_digest(digest):
            raise MonitorError(f"{repository}: layer descriptor has no sha256 digest")
        response = self.get(f"/v2/{repository}/blobs/{digest}")
        if response.status_code != 200:
            raise MonitorError(f"{repository}@{digest}: blob returned HTTP {response.status_code}")
        actual = f"sha256:{hashlib.sha256(response.content).hexdigest()}"
        if actual != digest or len(response.content) != descriptor.get("size"):
            raise MonitorError(f"{repository}@{digest}: blob does not match its descriptor")
        return response.content

    def tags(self, repository: str) -> list[str]:
        response = self.get(f"/v2/{repository}/tags/list", "application/json")
        if response.status_code != 200:
            raise MonitorError(f"{repository}: tag list returned HTTP {response.status_code}")
        tags = response.json().get("tags") or []
        if not all(isinstance(tag, str) for tag in tags):
            raise MonitorError(f"{repository}: tag list is malformed")
        return tags


def load_public_jwk(path: str | Path) -> tuple[EllipticCurvePublicKey, dict[str, Any]]:
    try:
        jwk = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MonitorError(f"Could not read catalog public JWK: {exc}") from exc
    if not isinstance(jwk, dict) or jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
        raise MonitorError("Catalog public key must be an EC P-256 JWK")
    if "d" in jwk:
        raise MonitorError("Catalog public JWK must not contain a private key")
    for key in ("x", "y", "kid"):
        if not isinstance(jwk.get(key), str) or not jwk[key]:
            raise MonitorError(f"Catalog public JWK is missing {key}")
    x, y = (_b64url_decode(jwk[key]) for key in ("x", "y"))
    if len(x) != 32 or len(y) != 32:
        raise MonitorError("Catalog public JWK x and y must be 32-byte values")
    try:
        public_key = ec.EllipticCurvePublicNumbers(
            x=int.from_bytes(x, "big"), y=int.from_bytes(y, "big"), curve=ec.SECP256R1()
        ).public_key()
    except ValueError as exc:
        raise MonitorError("Catalog public JWK is not a valid P-256 point") from exc
    return public_key, {"kid": jwk["kid"]}


def check_registry(
    registry_url: str,
    package_prefix: str,
    policy_path: str | Path,
    public_jwk_path: str | Path,
    channel: str = "latest",
    closed_ports: list[int] | None = None,
    cosign_bin: str = "cosign",
) -> MonitorReport:
    report = MonitorReport()
    policy_path = Path(policy_path).resolve()
    try:
        policy = load_policy(policy_path)
    except VerificationError as exc:
        raise MonitorError(str(exc)) from exc
    if policy["registry_prefix"] != f"{registry_url}/{package_prefix}":
        raise MonitorError("The signing policy is for a different registry or prefix")
    public_key, public_jwk = load_public_jwk(public_jwk_path)
    registry = _Registry(registry_url)

    catalog = _check_catalog(
        registry, report, registry_url, package_prefix, channel,
        policy, policy_path, public_key, public_jwk, cosign_bin,
    )
    if catalog is not None:
        for package, entry in sorted(catalog["packages"].items()):
            _check_package(registry, report, registry_url, package, entry, policy_path, cosign_bin)
    _check_write_refused(registry, report, package_prefix)
    _check_plain_http(registry_url, report)
    for port in closed_ports or []:
        _check_port_closed(registry_url, port, report)
    return report


def _check_catalog(
    registry: _Registry,
    report: MonitorReport,
    registry_url: str,
    package_prefix: str,
    channel: str,
    policy: dict[str, Any],
    policy_path: Path,
    public_key: EllipticCurvePublicKey,
    public_jwk: dict[str, Any],
    cosign_bin: str,
) -> dict[str, Any] | None:
    """The catalog the channel tag points at must carry a valid catalog-key
    signature and the publisher's sign/v1, and no other catalog this registry
    still serves under the same key may be newer -- or the channel was rolled
    back to an older, still validly signed catalog."""
    repository = f"{package_prefix}index"
    subject = f"{repository}:{channel}"
    try:
        current = _read_signed_catalog(
            registry, repository, channel, public_key, public_jwk
        )
        if current is None:
            report.fail(subject, "CATALOG_MISSING", "the catalog channel tag does not exist")
            return None
        digest, catalog = current
        _validate_existing_catalog(
            catalog,
            registry_url=registry_url,
            package_prefix=package_prefix,
            channel=channel,
            signing_policy=policy,
            policy_path=policy_path,
        )
    except (CatalogError, MonitorError) as exc:
        report.fail(subject, "CATALOG_INVALID", str(exc))
        return None

    report.catalog_digest = digest
    report.catalog_version = catalog.get("version")
    report.catalog_sequence = catalog["sequence"]
    report.packages = len(catalog["packages"])
    digest_reference = f"{registry_url}/{repository}@{digest}"
    report.signatures_checked += 1
    if not verify_cosign_signature(digest_reference, policy_path, cosign_bin):
        report.fail(digest_reference, "CATALOG_SIGNATURE", "the catalog has no valid sign/v1 signature from the publisher")

    try:
        tags = registry.tags(repository)
    except MonitorError as exc:
        report.fail(repository, "TAGS_UNREADABLE", str(exc))
        return catalog
    for tag in tags:
        if tag == channel or tag.startswith(REFERRERS_TAG_PREFIX):
            continue
        report.tags_checked += 1
        try:
            other = _read_signed_catalog(registry, repository, tag, public_key, public_jwk)
        except (CatalogError, MonitorError):
            # Catalogs signed before the current key, or before signing, cannot
            # be served as the channel to a client that pins this key, so they
            # cannot roll it back either.
            continue
        if other is None:
            continue
        other_digest, other_catalog = other
        if other_catalog.get("sequence", 0) > catalog["sequence"]:
            report.fail(
                f"{repository}:{tag}",
                "CATALOG_ROLLBACK",
                f"{channel} has sequence {catalog['sequence']} but {tag} "
                f"({other_digest}) is a newer validly signed catalog",
            )
    if catalog.get("version") and catalog["version"] not in tags:
        report.fail(subject, "CATALOG_VERSION_TAG", f"no tag {catalog['version']} for the current catalog")
    return catalog


def _read_signed_catalog(
    registry: _Registry,
    repository: str,
    reference: str,
    public_key: EllipticCurvePublicKey,
    public_jwk: dict[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    found = registry.manifest(repository, reference)
    if found is None:
        return None
    digest, manifest = found
    layers = {
        (layer.get("annotations") or {}).get("org.opencontainers.image.title"): layer
        for layer in manifest.get("layers") or []
    }
    catalog_layer = layers.get("index.json")
    signature_layer = layers.get("index.sig.json")
    if not catalog_layer or catalog_layer.get("mediaType") != CATALOG_MEDIA_TYPE:
        raise MonitorError(f"{repository}:{reference}: no catalog layer")
    if not signature_layer or signature_layer.get("mediaType") != CATALOG_SIGNATURE_MEDIA_TYPE:
        raise MonitorError(f"{repository}:{reference}: no catalog signature layer")
    catalog = _verify_catalog_signature(
        registry.blob(repository, catalog_layer),
        registry.blob(repository, signature_layer),
        public_key,
        public_jwk,
    )
    return digest, catalog


def _check_package(
    registry: _Registry,
    report: MonitorReport,
    registry_url: str,
    package: str,
    entry: dict[str, Any],
    policy_path: Path,
    cosign_bin: str,
) -> None:
    """Every tag a client could resolve must lead to a manifest the publisher
    signed: latest and the catalog's version to exactly the catalog's digest,
    and any older version tag to something that still verifies."""
    expected = entry.get("manifest_digest")
    version = entry.get("version")
    expected_reference = f"{registry_url}/{package}@{expected}"
    if not is_sha256_digest(expected) or entry.get("digest_reference") != expected_reference:
        report.fail(package, "CATALOG_ENTRY", "the catalog entry has no consistent digest reference")
        return

    for tag in ("latest", version):
        report.tags_checked += 1
        try:
            found = registry.manifest(package, tag)
        except MonitorError as exc:
            report.fail(f"{package}:{tag}", "TAG_UNREADABLE", str(exc))
            continue
        if found is None:
            report.fail(f"{package}:{tag}", "TAG_MISSING", "the tag does not exist")
        elif found[0] != expected:
            report.fail(
                f"{package}:{tag}",
                "TAG_MOVED",
                f"points to {found[0]}, the signed catalog says {expected}",
            )

    report.signatures_checked += 1
    if not verify_cosign_signature(expected_reference, policy_path, cosign_bin):
        report.fail(expected_reference, "SIGNATURE", "no valid sign/v1 signature from the publisher")

    try:
        tags = registry.tags(package)
    except MonitorError as exc:
        report.fail(package, "TAGS_UNREADABLE", str(exc))
        return
    for tag in tags:
        # A sha256-<digest> tag is the referrers list the registry keeps for a
        # manifest's signatures and attestations; no client resolves it as a
        # tool, and republishing a version leaves such lists behind for the
        # manifests it replaced.
        if tag in ("latest", version) or tag.startswith(REFERRERS_TAG_PREFIX):
            continue
        report.tags_checked += 1
        try:
            found = registry.manifest(package, tag)
        except MonitorError as exc:
            report.fail(f"{package}:{tag}", "TAG_UNREADABLE", str(exc))
            continue
        if found is None:
            continue
        reference = f"{registry_url}/{package}@{found[0]}"
        report.signatures_checked += 1
        if not verify_cosign_signature(reference, policy_path, cosign_bin):
            report.fail(
                f"{package}:{tag}",
                "TAG_UNSIGNED",
                f"points to {found[0]}, which has no valid sign/v1 signature from the publisher",
            )


def _check_write_refused(registry: _Registry, report: MonitorReport, package_prefix: str) -> None:
    """An upload session is the first step of any push. It must be refused
    without credentials; if it is not, the session is cancelled at once."""
    repository = f"{package_prefix}monitor-write-check"
    response = registry.session.post(
        f"{registry.base}/v2/{repository}/blobs/uploads/", timeout=TIMEOUT_SECONDS
    )
    if response.status_code == 401:
        return
    report.fail(
        repository,
        "WRITE_NOT_REFUSED",
        f"an unauthenticated upload got HTTP {response.status_code}, expected 401",
    )
    location = response.headers.get("Location")
    if response.status_code in (201, 202) and location:
        registry.session.delete(
            location if location.startswith("http") else f"{registry.base}{location}",
            timeout=TIMEOUT_SECONDS,
        )


def _check_plain_http(registry_url: str, report: MonitorReport) -> None:
    """Port 80 may only send clients to HTTPS, never serve the registry."""
    try:
        response = requests.get(
            f"http://{registry_url}/v2/", allow_redirects=False, timeout=TIMEOUT_SECONDS
        )
    except requests.RequestException:
        return
    location = response.headers.get("Location", "")
    if response.status_code not in (301, 302, 307, 308) or not location.startswith(f"https://{registry_url}/"):
        report.fail(
            f"http://{registry_url}/v2/",
            "PLAIN_HTTP_SERVED",
            f"HTTP {response.status_code} without a redirect to HTTPS",
        )


def _check_port_closed(registry_url: str, port: int, report: MonitorReport) -> None:
    try:
        with socket.create_connection((registry_url, port), timeout=5):
            pass
    except OSError:
        return
    report.fail(f"{registry_url}:{port}", "PORT_OPEN", "accepts connections from outside")
