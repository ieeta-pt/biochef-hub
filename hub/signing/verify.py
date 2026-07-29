import base64
import binascii
import json
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from builders.container import is_sha256_hex, sha256_hex
from publish.publish import read_publish_results
from signing.provenance import (
    BIOCHEF_BUILD_TYPE,
    SLSA_PREDICATE_TYPE,
    ProvenanceError,
    normalise_slsa_timestamp,
    resolved_dependencies_from_evidence,
)


CYCLONEDX_PREDICATE_TYPE = "https://cyclonedx.org/bom"
IN_TOTO_STATEMENT_TYPES = frozenset(
    {
        "https://in-toto.io/Statement/v0.1",
        "https://in-toto.io/Statement/v1",
    }
)


class VerificationError(RuntimeError):
    pass


@dataclass
class VerificationIssue:
    artifact: str
    code: str
    message: str


@dataclass
class VerificationSummary:
    scanned: int = 0
    failures: list[VerificationIssue] = field(default_factory=list)

    @property
    def failed(self):
        return bool(self.failures)


def verify_published_artifacts(
    registry_dir="registry",
    publish_results_path=None,
    policy_path=None,
    cosign_bin="cosign",
    operation_id=None,
    version=None,
):
    return check_or_verify(
        registry_dir,
        publish_results_path,
        policy_path,
        cosign_bin,
        verify_cosign=True,
        operation_id=operation_id,
        version=version,
    )


def check_published_evidence(
    registry_dir="registry",
    publish_results_path=None,
    policy_path=None,
    operation_id=None,
    version=None,
):
    return check_or_verify(
        registry_dir,
        publish_results_path,
        policy_path,
        "cosign",
        verify_cosign=False,
        operation_id=operation_id,
        version=version,
    )


def verify_cosign_signature(digest_reference, policy_path, cosign_bin="cosign"):
    policy = load_policy(policy_path)
    summary = VerificationSummary(scanned=1)
    run_cosign(
        [cosign_bin, "verify", *identity_args(policy), digest_reference],
        summary,
        digest_reference,
        "SIGNATURE_VERIFY_FAILED",
    )
    return not summary.failed


def verify_cosign_attestation(
    digest_reference,
    policy_path,
    cosign_predicate_type,
    local_predicate_path,
    cosign_bin="cosign",
):
    policy = load_policy(policy_path)
    summary = VerificationSummary(scanned=1)
    output = run_cosign(
        [
            cosign_bin,
            "verify-attestation",
            "--type",
            cosign_predicate_type,
            *identity_args(policy),
            digest_reference,
        ],
        summary,
        digest_reference,
        "ATTESTATION_VERIFY_FAILED",
    )
    if output is not None:
        verify_attestation_payload(
            output,
            Path(local_predicate_path).resolve(),
            (
                CYCLONEDX_PREDICATE_TYPE
                if cosign_predicate_type == "cyclonedx"
                else cosign_predicate_type
            ),
            digest_reference,
            summary,
            digest_reference,
            "ATTESTATION_PAYLOAD_MISMATCH",
        )
    return not summary.failed


def check_or_verify(
    registry_dir,
    publish_results_path,
    policy_path,
    cosign_bin,
    *,
    verify_cosign,
    operation_id,
    version,
):
    registry_path = Path(registry_dir).resolve()
    if not registry_path.is_dir():
        raise VerificationError(
            f"Registry directory does not exist: {registry_path}"
        )
    if not policy_path:
        raise VerificationError("A signing verification policy is required")
    policy = load_policy(policy_path)
    results_path = Path(
        publish_results_path or registry_path / "publish-results.json"
    ).resolve()
    try:
        publish_results = read_publish_results(results_path)
    except RuntimeError as exc:
        raise VerificationError(str(exc)) from exc
    artifacts = [
        artifact
        for artifact in publish_results["artifacts"]
        if (not operation_id or artifact["operation_id"] == operation_id)
        and (not version or artifact["version"] == version)
    ]
    if not artifacts:
        raise VerificationError("No published artifact matched the requested filter")

    summary = VerificationSummary()
    for artifact in artifacts:
        summary.scanned += 1
        verify_artifact(
            registry_path,
            publish_results["registry"],
            artifact,
            policy,
            cosign_bin,
            verify_cosign,
            summary,
        )
    return summary


def verify_artifact(
    registry_path,
    registry,
    artifact,
    policy,
    cosign_bin,
    verify_cosign,
    summary,
):
    operation_id = artifact["operation_id"]
    version = artifact["version"]
    name = f"{operation_id}@{version}"
    expected_package = (
        f"{policy['registry_prefix']}{operation_id}".lower()
    )
    expected_digest_ref = (
        f"{expected_package}@{artifact.get('version_digest')}"
    )
    require(
        summary,
        name,
        artifact.get("package") == expected_package.rsplit("/", 1)[1],
        "PACKAGE_MISMATCH",
        "Published package does not match the policy namespace",
    )
    require(
        summary,
        name,
        artifact.get("digest_reference") == expected_digest_ref
        and digest_reference(artifact.get("digest_reference")),
        "DIGEST_REFERENCE",
        "Published artifact does not have the expected immutable digest reference",
    )
    if summary.failures and summary.failures[-1].artifact == name:
        return

    bundle_dir = safe_bundle_dir(
        registry_path,
        operation_id,
        version,
    )
    verify_local_provenance(
        bundle_dir,
        artifact,
        registry,
        policy,
        summary,
        name,
    )
    if any(issue.artifact == name for issue in summary.failures):
        return
    if not verify_cosign:
        return

    reference = artifact["digest_reference"]
    args = identity_args(policy)
    run_cosign(
        [cosign_bin, "verify", *args, reference],
        summary,
        name,
        "SIGNATURE_VERIFY_FAILED",
    )
    for cosign_type, predicate_type, filename, code in (
        (
            "cyclonedx",
            CYCLONEDX_PREDICATE_TYPE,
            "sbom.cdx.json",
            "CYCLONEDX_ATTESTATION",
        ),
        (
            SLSA_PREDICATE_TYPE,
            SLSA_PREDICATE_TYPE,
            "provenance.slsa.json",
            "SLSA_ATTESTATION",
        ),
    ):
        output = run_cosign(
            [
                cosign_bin,
                "verify-attestation",
                "--type",
                cosign_type,
                *args,
                reference,
            ],
            summary,
            name,
            f"{code}_VERIFY_FAILED",
        )
        if output is not None:
            verify_attestation_payload(
                output,
                bundle_dir / filename,
                predicate_type,
                reference,
                summary,
                name,
                f"{code}_PAYLOAD_MISMATCH",
            )


def verify_local_provenance(
    bundle_dir,
    artifact,
    registry,
    policy,
    summary,
    name,
):
    required = (
        "bundle.json",
        "build-evidence.json",
        "sbom.cdx.json",
        "provenance.slsa.json",
    )
    for filename in required:
        path = bundle_dir / filename
        require(
            summary,
            name,
            path.is_file() and not path.is_symlink(),
            "LOCAL_EVIDENCE",
            f"Missing or unsafe local evidence file: {filename}",
        )
    if any(issue.artifact == name for issue in summary.failures):
        return

    evidence = read_json(bundle_dir / "build-evidence.json")
    provenance = read_json(bundle_dir / "provenance.slsa.json")
    definition = provenance.get("buildDefinition") or {}
    details = provenance.get("runDetails") or {}
    require(
        summary,
        name,
        definition.get("buildType") == policy["slsa_build_type"],
        "SLSA_BUILD_TYPE",
        "SLSA build type does not match policy",
    )
    require(
        summary,
        name,
        (details.get("builder") or {}).get("id")
        == policy["certificate_identity"],
        "SLSA_BUILDER",
        "SLSA builder does not match signer identity",
    )

    external = definition.get("externalParameters")
    if not isinstance(external, dict):
        fail(summary, name, "SLSA_PARAMETERS", "External parameters are not an object")
        external = {}
    allowed = set(policy["allowed_external_parameters"])
    require(
        summary,
        name,
        set(external) == allowed,
        "SLSA_PARAMETERS",
        "SLSA external parameters do not exactly match policy",
    )
    recipe = evidence.get("recipe") or {}
    expected = {
        **policy["expected_external_parameters"],
        "operationId": artifact["operation_id"],
        "version": artifact["version"],
        "package": artifact["package"],
        "recipePath": recipe.get("path"),
    }
    require(
        summary,
        name,
        registry == policy["expected_external_parameters"]["registry"],
        "SLSA_REGISTRY",
        "Publish registry does not match policy",
    )
    for key in allowed:
        require(
            summary,
            name,
            key in expected and external.get(key) == expected.get(key),
            "SLSA_PARAMETERS",
            f"SLSA external parameter does not match policy: {key}",
        )

    metadata = details.get("metadata") or {}
    try:
        expected_time = normalise_slsa_timestamp(evidence.get("generated_at"))
        actual_time = normalise_slsa_timestamp(metadata.get("finishedOn"))
        require(
            summary,
            name,
            actual_time == expected_time,
            "SLSA_FINISHED_TIME",
            "SLSA finishedOn does not match build evidence",
        )
    except ProvenanceError as exc:
        fail(summary, name, "SLSA_FINISHED_TIME", str(exc))

    recipes_repository, recipes_commit = recipes_identity(
        definition.get("resolvedDependencies") or []
    )
    if os.getenv("GITHUB_ACTIONS") == "true":
        require(
            summary,
            name,
            recipes_repository == os.getenv("GITHUB_REPOSITORY")
            and recipes_commit == os.getenv("GITHUB_SHA"),
            "SLSA_RECIPES_IDENTITY",
            "Recipe repository identity does not match the workflow context",
        )
    try:
        expected_dependencies = resolved_dependencies_from_evidence(
            evidence,
            recipes_repository=recipes_repository,
            recipes_commit=recipes_commit,
            hub_repository=policy["expected_external_parameters"]["hubRepository"],
        )
    except ProvenanceError as exc:
        fail(summary, name, "SLSA_DEPENDENCIES", str(exc))
        expected_dependencies = []
    actual_dependencies = definition.get("resolvedDependencies")
    if not isinstance(actual_dependencies, list):
        fail(summary, name, "SLSA_DEPENDENCIES", "resolvedDependencies is not a list")
        actual_dependencies = []
    require(
        summary,
        name,
        actual_dependencies == expected_dependencies,
        "SLSA_DEPENDENCIES",
        "SLSA resolved dependencies do not exactly match build evidence",
    )

    expected_byproducts = expected_byproduct_digests(bundle_dir, evidence)
    actual_byproducts = {}
    for item in details.get("byproducts") or []:
        if isinstance(item, dict) and isinstance(item.get("uri"), str):
            actual_byproducts[item["uri"]] = (item.get("digest") or {}).get("sha256")
    require(
        summary,
        name,
        actual_byproducts == expected_byproducts,
        "SLSA_BYPRODUCTS",
        "SLSA byproducts do not exactly match local bundle files",
    )


def expected_byproduct_digests(bundle_dir, evidence):
    paths = {"bundle.json", "build-evidence.json", "sbom.cdx.json"}
    for item in (evidence.get("license") or {}).get("files") or []:
        paths.add(item["path"])
    for runtime_data in (evidence.get("runtimes") or {}).values():
        for item in (runtime_data.get("artifacts") or {}).get("files") or []:
            paths.add(item["path"])
    return {
        f"file:{relative}": sha256_hex(require_file(bundle_dir, relative))
        for relative in sorted(paths)
    }


def recipes_identity(dependencies):
    matches = [
        item
        for item in dependencies
        if isinstance(item, dict) and item.get("name") == "biochef-recipes"
    ]
    if len(matches) != 1:
        raise VerificationError(
            "SLSA provenance must contain one recipe repository dependency"
        )
    descriptor = matches[0]
    commit = (descriptor.get("digest") or {}).get("gitCommit")
    uri = descriptor.get("uri")
    if not isinstance(uri, str) or not uri.startswith("git+") or not commit:
        raise VerificationError("Recipe repository dependency is malformed")
    suffix = f"@{commit}"
    if not uri.endswith(suffix):
        raise VerificationError("Recipe repository URI and commit differ")
    return uri[4:-len(suffix)], commit


def verify_attestation_payload(
    output,
    local_predicate_path,
    predicate_type,
    digest_ref,
    summary,
    artifact_name,
    code,
):
    local = read_json(local_predicate_path)
    expected_digest = digest_ref.rsplit("@sha256:", 1)[1].lower()
    matches = False
    for statement in attestation_statements(output):
        if (
            statement.get("_type") in IN_TOTO_STATEMENT_TYPES
            and statement.get("predicateType") == predicate_type
            and statement.get("predicate") == local
            and any(
                subject_digest(subject) == expected_digest
                for subject in statement.get("subject") or []
                if isinstance(subject, dict)
            )
        ):
            matches = True
            break
    require(
        summary,
        artifact_name,
        matches,
        code,
        "Verified attestation does not bind the subject to the exact local predicate",
    )


def attestation_statements(output):
    decoder = json.JSONDecoder()
    documents = []
    position = 0
    while position < len(output):
        while position < len(output) and output[position].isspace():
            position += 1
        if position == len(output):
            break
        try:
            envelope, position = decoder.raw_decode(output, position)
            payload = envelope.get("payload")
            if (
                envelope.get("payloadType") != "application/vnd.in-toto+json"
                or not isinstance(payload, str)
            ):
                return []
            documents.append(json.loads(base64.b64decode(payload, validate=True)))
        except (
            AttributeError,
            binascii.Error,
            json.JSONDecodeError,
            UnicodeDecodeError,
        ):
            return []
    return [item for item in documents if isinstance(item, dict)]


def subject_digest(subject):
    value = (subject.get("digest") or {}).get("sha256")
    return value.lower() if isinstance(value, str) else None


def write_verification_report(
    report_path,
    registry_dir,
    publish_results_path,
    policy_path,
    summary,
):
    registry_path = Path(registry_dir).resolve()
    results_path = Path(
        publish_results_path or registry_path / "publish-results.json"
    ).resolve()
    try:
        results = read_publish_results(results_path)
    except RuntimeError as exc:
        raise VerificationError(str(exc)) from exc
    if summary.scanned != len(results["artifacts"]):
        raise VerificationError(
            "Cannot write a full release report from partial verification"
        )
    failures = {}
    for issue in summary.failures:
        failures.setdefault(issue.artifact, []).append(
            {"code": issue.code, "message": issue.message}
        )
    artifacts = []
    for artifact in results["artifacts"]:
        name = f"{artifact['operation_id']}@{artifact['version']}"
        bundle_dir = safe_bundle_dir(
            registry_path,
            artifact["operation_id"],
            artifact["version"],
        )
        artifact_failures = failures.get(name, [])
        artifacts.append(
            {
                "operation_id": artifact["operation_id"],
                "version": artifact["version"],
                "package": artifact["package"],
                "digest_reference": artifact["digest_reference"],
                "status": "failed" if artifact_failures else "passed",
                "failures": artifact_failures,
                "evidence": verification_evidence_digests(bundle_dir),
            }
        )
    report = {
        "schema": "biochef.signing-verification-report.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "failed" if summary.failed else "passed",
        "scanned": summary.scanned,
        "policy": {
            "digest": f"sha256:{sha256_hex(Path(policy_path).resolve())}"
        },
        "publish_results": {
            "digest": f"sha256:{sha256_hex(results_path)}"
        },
        "artifacts": artifacts,
    }
    output = Path(report_path)
    if output.is_symlink():
        raise VerificationError(f"Refusing to write symlinked report: {output}")
    output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def verification_evidence_digests(bundle_dir):
    files = {
        "bundle_json": "bundle.json",
        "sbom_cdx_json": "sbom.cdx.json",
        "build_evidence_json": "build-evidence.json",
        "provenance_slsa_json": "provenance.slsa.json",
    }
    return {
        key: f"sha256:{sha256_hex(require_file(bundle_dir, value))}"
        for key, value in files.items()
    }


def load_policy(path):
    policy = read_json(Path(path).resolve())
    required = (
        "registry_prefix",
        "certificate_identity",
        "certificate_oidc_issuer",
        "slsa_predicate_type",
        "slsa_build_type",
    )
    if policy.get("schema") != "biochef.signing-policy.v1":
        raise VerificationError("Unsupported signing policy")
    if any(not isinstance(policy.get(key), str) or not policy[key] for key in required):
        raise VerificationError("Signing policy is missing required identities")
    if (
        policy["slsa_predicate_type"] != SLSA_PREDICATE_TYPE
        or policy["slsa_build_type"] != BIOCHEF_BUILD_TYPE
    ):
        raise VerificationError("Signing policy requires unsupported SLSA semantics")
    allowed = policy.get("allowed_external_parameters")
    expected = policy.get("expected_external_parameters")
    required_allowed = {
        "registry",
        "package",
        "operationId",
        "version",
        "recipePath",
        "hubRepository",
        "hubRef",
    }
    if not isinstance(allowed, list) or set(allowed) != required_allowed:
        raise VerificationError(
            "Signing policy external parameters do not match the BioCHEF build type"
        )
    if not isinstance(expected, dict) or set(expected) != {
        "registry",
        "hubRepository",
        "hubRef",
    }:
        raise VerificationError(
            "Signing policy fixed external expectations are incomplete"
        )
    registry_prefix = policy["registry_prefix"]
    if not registry_prefix.startswith(f"{expected['registry'].rstrip('/')}/"):
        raise VerificationError("Signing policy registry prefix is inconsistent")
    return policy


def safe_bundle_dir(registry_path, operation_id, version):
    root = Path(registry_path).resolve()
    if any(
        not isinstance(value, str)
        or not value
        or "\\" in value
        or Path(value).is_absolute()
        or ".." in Path(value).parts
        for value in (operation_id, version)
    ):
        raise VerificationError("Published artifact path is unsafe")
    path = (root / operation_id / version).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise VerificationError("Published artifact escapes registry path") from exc
    if path.is_symlink() or not path.is_dir():
        raise VerificationError(f"Bundle directory is missing or unsafe: {path}")
    return path


def require_file(bundle_dir, relative):
    if (
        not isinstance(relative, str)
        or not relative
        or "\\" in relative
        or Path(relative).is_absolute()
        or ".." in Path(relative).parts
    ):
        raise VerificationError(f"Unsafe bundle path: {relative!r}")
    root = Path(bundle_dir).resolve()
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise VerificationError(f"Bundle path escapes directory: {relative}") from exc
    if path.is_symlink() or not path.is_file():
        raise VerificationError(f"Missing or unsafe bundle file: {relative}")
    return path


def digest_reference(value):
    if not isinstance(value, str) or "@sha256:" not in value:
        return False
    return is_sha256_hex(value.rsplit("@sha256:", 1)[1])


def identity_args(policy):
    return [
        "--certificate-identity",
        policy["certificate_identity"],
        "--certificate-oidc-issuer",
        policy["certificate_oidc_issuer"],
    ]


def run_cosign(command, summary, artifact, code):
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except OSError as exc:
        fail(summary, artifact, code, f"Could not execute Cosign: {exc}")
        return None
    if result.returncode == 0:
        return result.stdout
    fail(
        summary,
        artifact,
        code,
        (result.stderr or result.stdout or "Cosign command failed").strip(),
    )
    return None


def read_json(path):
    if Path(path).is_symlink():
        raise VerificationError(f"Refusing to read symlinked JSON: {path}")
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VerificationError(f"Could not read JSON {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise VerificationError(f"JSON document must be an object: {path}")
    return document


def require(summary, artifact, condition, code, message):
    if not condition:
        fail(summary, artifact, code, message)


def fail(summary, artifact, code, message):
    summary.failures.append(
        VerificationIssue(artifact=artifact, code=code, message=message)
    )
