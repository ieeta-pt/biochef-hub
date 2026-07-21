import base64
import binascii
import json
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from signing.provenance import (
    BIOCHEF_BUILD_TYPE,
    SLSA_PREDICATE_TYPE,
    ProvenanceError,
    normalise_slsa_timestamp,
    resolved_dependencies_from_evidence,
)


CYCLONEDX_PREDICATE_TYPE = "https://cyclonedx.org/bom"


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
    def failed(self) -> bool:
        return bool(self.failures)


def verify_published_artifacts(registry_dir: str | Path = "registry", publish_results_path: str | Path | None = None, policy_path: str | Path | None = None, cosign_bin: str = "cosign", operation_id: str | None = None, version: str | None = None) -> VerificationSummary:
    return _check_or_verify_published_artifacts(
        registry_dir=registry_dir,
        publish_results_path=publish_results_path,
        policy_path=policy_path,
        cosign_bin=cosign_bin,
        verify_cosign=True,
        operation_id=operation_id,
        version=version,
    )


def check_published_evidence(registry_dir: str | Path = "registry", publish_results_path: str | Path | None = None, policy_path: str | Path | None = None, operation_id: str | None = None, version: str | None = None) -> VerificationSummary:
    return _check_or_verify_published_artifacts(
        registry_dir=registry_dir,
        publish_results_path=publish_results_path,
        policy_path=policy_path,
        cosign_bin="cosign",
        verify_cosign=False,
        operation_id=operation_id,
        version=version,
    )


def write_verification_report(
    report_path: str | Path,
    registry_dir: str | Path,
    publish_results_path: str | Path | None,
    policy_path: str | Path,
    summary: VerificationSummary,
) -> None:
    registry_path = Path(registry_dir).resolve()
    results_path = Path(
        publish_results_path or registry_path / "publish-results.json"
    ).resolve()
    policy = Path(policy_path).resolve()
    publish_results = _read_json(results_path)
    artifacts = publish_results.get("artifacts")
    if not isinstance(artifacts, list) or any(
        not isinstance(artifact, dict) for artifact in artifacts
    ):
        raise VerificationError(f"Invalid publish results file: {results_path}")
    if summary.scanned != len(artifacts):
        raise VerificationError(
            "Cannot write a release verification report from a partial verification"
        )

    failures_by_artifact: dict[str, list[dict[str, str]]] = {}
    for issue in summary.failures:
        failures_by_artifact.setdefault(issue.artifact, []).append(
            {"code": issue.code, "message": issue.message}
        )

    report_artifacts = []
    for artifact in artifacts:
        operation_id = artifact.get("operation_id")
        version = artifact.get("version")
        artifact_name = f"{operation_id}@{version}"
        bundle_dir = _report_bundle_dir(
            registry_path, operation_id, version, artifact_name
        )
        failures = failures_by_artifact.get(artifact_name, [])
        report_artifacts.append(
            {
                "operation_id": operation_id,
                "version": version,
                "package": artifact.get("package"),
                "digest_reference": artifact.get("digest_reference"),
                "status": "failed" if failures else "passed",
                "failures": failures,
                "evidence": verification_evidence_digests(bundle_dir),
            }
        )

    report = {
        "schema": "biochef.signing-verification-report.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "failed" if summary.failed else "passed",
        "scanned": summary.scanned,
        "policy": {
            "path": str(policy),
            "digest": f"sha256:{_sha256_hex(policy)}",
        },
        "publish_results": {
            "digest": f"sha256:{_sha256_hex(results_path)}",
        },
        "artifacts": report_artifacts,
    }
    output_path = Path(report_path)
    if output_path.is_symlink():
        raise VerificationError(
            f"Refusing to write symlinked verification report: {output_path}"
        )
    output_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _report_bundle_dir(
    registry_path: Path,
    operation_id: Any,
    version: Any,
    artifact_name: str,
) -> Path:
    operation_path = _safe_bundle_relative_path(operation_id)
    version_path = _safe_bundle_relative_path(version)
    if operation_path is None or version_path is None:
        raise VerificationError(
            f"Cannot report evidence for unsafe artifact path: {artifact_name}"
        )
    bundle_dir = (registry_path / operation_path / version_path).resolve()
    try:
        bundle_dir.relative_to(registry_path)
    except ValueError as exc:
        raise VerificationError(
            f"Cannot report evidence outside the registry directory: {artifact_name}"
        ) from exc
    if not bundle_dir.is_dir():
        raise VerificationError(f"Missing bundle directory for {artifact_name}")
    return bundle_dir


def verification_evidence_digests(bundle_dir: Path) -> dict[str, str]:
    evidence_files = {
        "bundle_json": bundle_dir / "bundle.json",
        "sbom_cdx_json": bundle_dir / "sbom.cdx.json",
        "build_evidence_json": bundle_dir / "build-evidence.json",
        "provenance_slsa_json": bundle_dir / "provenance.slsa.json",
    }
    for path in evidence_files.values():
        if path.is_symlink() or not path.is_file():
            raise VerificationError(f"Missing or unsafe verified evidence file: {path}")
    return {
        name: f"sha256:{_sha256_hex(path)}"
        for name, path in evidence_files.items()
    }


def _check_or_verify_published_artifacts(registry_dir: str | Path, publish_results_path: str | Path | None, policy_path: str | Path | None, cosign_bin: str, verify_cosign: bool, operation_id: str | None, version: str | None) -> VerificationSummary:
    registry_path = Path(registry_dir).resolve()
    if not registry_path.is_dir():
        raise VerificationError(f"Registry directory does not exist: {registry_path}")
    if not policy_path:
        raise VerificationError("A signing verification policy is required")

    results_path = Path(publish_results_path or registry_path / "publish-results.json").resolve()
    publish_results = _read_json(results_path)
    policy = _read_json(Path(policy_path).resolve())
    _validate_policy(policy)
    all_artifacts = publish_results.get("artifacts")
    if (
        publish_results.get("schema") != "biochef.publish-results.v1"
        or not isinstance(all_artifacts, list)
        or not all_artifacts
        or any(not isinstance(artifact, dict) for artifact in all_artifacts)
    ):
        raise VerificationError(f"Invalid or empty publish results file: {results_path}")
    _validate_unique_artifacts(all_artifacts)
    registry = publish_results.get("registry")
    if not isinstance(registry, str) or not registry:
        raise VerificationError(f"Publish results lack registry identity: {results_path}")
    artifacts = _filter_artifacts(all_artifacts, operation_id, version)
    if not artifacts:
        raise VerificationError(
            "No publish result artifact matched "
            f"operation_id={operation_id!r}, version={version!r}"
        )

    summary = VerificationSummary()
    for artifact in artifacts:
        summary.scanned += 1
        _verify_artifact(
            registry_path,
            artifact,
            registry,
            policy,
            cosign_bin,
            verify_cosign,
            summary,
        )
    return summary


def _filter_artifacts(artifacts: list[dict[str, Any]], operation_id: str | None, version: str | None) -> list[dict[str, Any]]:
    if not operation_id and not version:
        return artifacts
    return [
        artifact
        for artifact in artifacts
        if (not operation_id or artifact.get("operation_id") == operation_id)
        and (not version or artifact.get("version") == version)
    ]


def _validate_unique_artifacts(artifacts: list[dict[str, Any]]) -> None:
    identities = set()
    digest_references = set()
    for artifact in artifacts:
        operation_id = artifact.get("operation_id")
        version = artifact.get("version")
        if isinstance(operation_id, str) and isinstance(version, str):
            identity = (operation_id, version)
            if identity in identities:
                raise VerificationError(
                    f"Publish results contain duplicate artifact {operation_id}@{version}"
                )
            identities.add(identity)

        digest_reference = artifact.get("digest_reference")
        if isinstance(digest_reference, str) and digest_reference in digest_references:
            raise VerificationError(
                f"Publish results contain duplicate digest reference: {digest_reference}"
            )
        if isinstance(digest_reference, str):
            digest_references.add(digest_reference)


def _verify_artifact(registry_path: Path, artifact: dict[str, Any], registry: str, policy: dict[str, Any], cosign_bin: str, verify_cosign: bool, summary: VerificationSummary) -> None:
    artifact_name = f"{artifact.get('operation_id')}@{artifact.get('version')}"
    operation_id = artifact.get("operation_id")
    version = artifact.get("version")
    package = artifact.get("package")
    version_digest = artifact.get("version_digest")
    digest_ref = artifact.get("digest_reference")
    registry_prefix = policy["registry_prefix"]

    _require(summary, artifact_name, bool(digest_ref), "DIGEST_REFERENCE_MISSING", "publish results lack digest_reference")
    if not digest_ref:
        return
    immutable_reference = _is_digest_reference(digest_ref)
    allowed_registry = isinstance(digest_ref, str) and digest_ref.startswith(registry_prefix)
    _require(summary, artifact_name, immutable_reference, "MUTABLE_REFERENCE", "artifact verification requires an immutable @sha256 reference")
    _require(
        summary,
        artifact_name,
        allowed_registry,
        "REGISTRY_PREFIX_MISMATCH",
        f"artifact reference {digest_ref!r} does not match policy prefix {registry_prefix!r}",
    )
    registry_prefix_base, package_prefix = registry_prefix.rsplit("/", 1)
    expected_package = (
        f"{package_prefix}{operation_id}".lower()
        if isinstance(operation_id, str) and operation_id
        else None
    )
    expected_package_reference = (
        f"{registry_prefix_base}/{expected_package}"
        if expected_package
        else None
    )
    valid_version_digest = (
        isinstance(version_digest, str)
        and version_digest.startswith("sha256:")
        and _is_sha256_hex(version_digest.removeprefix("sha256:"))
    )
    _require(
        summary,
        artifact_name,
        package == expected_package,
        "PUBLISH_PACKAGE_MISMATCH",
        "publish results package does not match the policy prefix and operation id",
    )
    _require(
        summary,
        artifact_name,
        valid_version_digest,
        "PUBLISH_DIGEST_INVALID",
        "publish results version_digest is missing or invalid",
    )
    expected_digest_ref = (
        f"{expected_package_reference}@{version_digest}"
        if expected_package_reference and valid_version_digest
        else None
    )
    _require(
        summary,
        artifact_name,
        digest_ref == expected_digest_ref,
        "PUBLISH_REFERENCE_MISMATCH",
        "publish results digest reference does not match its package and manifest digest",
    )
    expected_version_ref = (
        f"{registry}/{package}:{version}"
        if isinstance(package, str)
        and package
        and isinstance(version, str)
        and version
        else None
    )
    _require(
        summary,
        artifact_name,
        artifact.get("version_tag_reference") == expected_version_ref,
        "PUBLISH_VERSION_REFERENCE_MISMATCH",
        "publish results version tag reference does not match its registry, package, and version",
    )
    if (
        not immutable_reference
        or not allowed_registry
        or package != expected_package
        or not valid_version_digest
        or digest_ref != expected_digest_ref
        or artifact.get("version_tag_reference") != expected_version_ref
    ):
        return

    bundle_dir = _bundle_dir(registry_path, artifact, summary, artifact_name)
    if not bundle_dir:
        return

    failure_count = len(summary.failures)
    _verify_local_provenance(
        bundle_dir,
        artifact,
        registry,
        policy,
        summary,
        artifact_name,
    )
    if len(summary.failures) != failure_count:
        return

    if not verify_cosign:
        return

    identity = policy["certificate_identity"]
    issuer = policy["certificate_oidc_issuer"]

    identity_args = [
        "--certificate-identity",
        identity,
        "--certificate-oidc-issuer",
        issuer,
    ]
    _run_cosign(
        [cosign_bin, "verify", *identity_args, digest_ref],
        summary,
        artifact_name,
        "SIGNATURE_VERIFY_FAILED",
    )
    cyclonedx_output = _run_cosign(
        [cosign_bin, "verify-attestation", "--type", "cyclonedx", *identity_args, digest_ref],
        summary,
        artifact_name,
        "CYCLONEDX_ATTESTATION_VERIFY_FAILED",
    )
    if cyclonedx_output is not None:
        _verify_attestation_payload(
            output=cyclonedx_output,
            local_predicate_path=bundle_dir / "sbom.cdx.json",
            predicate_type=CYCLONEDX_PREDICATE_TYPE,
            digest_reference=digest_ref,
            summary=summary,
            artifact_name=artifact_name,
            code="CYCLONEDX_ATTESTATION_PAYLOAD_MISMATCH",
        )

    slsa_output = _run_cosign(
        [cosign_bin, "verify-attestation", "--type", SLSA_PREDICATE_TYPE, *identity_args, digest_ref],
        summary,
        artifact_name,
        "SLSA_ATTESTATION_VERIFY_FAILED",
    )
    if slsa_output is not None:
        _verify_attestation_payload(
            output=slsa_output,
            local_predicate_path=bundle_dir / "provenance.slsa.json",
            predicate_type=SLSA_PREDICATE_TYPE,
            digest_reference=digest_ref,
            summary=summary,
            artifact_name=artifact_name,
            code="SLSA_ATTESTATION_PAYLOAD_MISMATCH",
        )


def _verify_local_provenance(bundle_dir: Path, artifact: dict[str, Any], registry: str, policy: dict[str, Any], summary: VerificationSummary, artifact_name: str) -> None:
    sbom_path = bundle_dir / "sbom.cdx.json"
    provenance_path = bundle_dir / "provenance.slsa.json"
    evidence_path = bundle_dir / "build-evidence.json"
    bundle_path = bundle_dir / "bundle.json"

    evidence_files = (bundle_path, sbom_path, evidence_path, provenance_path)
    for path in evidence_files:
        _require(summary, artifact_name, path.is_file() and not path.is_symlink(), "LOCAL_EVIDENCE_MISSING", f"missing or unsafe local evidence file {path.name}")
    if not all(path.is_file() and not path.is_symlink() for path in evidence_files):
        return

    provenance = _read_json(provenance_path)
    evidence = _read_json(evidence_path)
    build_definition = _object_or_failure(
        provenance.get("buildDefinition"), summary, artifact_name,
        "SLSA_STRUCTURE_INVALID", "SLSA provenance buildDefinition must be an object",
    )
    run_details = _object_or_failure(
        provenance.get("runDetails"), summary, artifact_name,
        "SLSA_STRUCTURE_INVALID", "SLSA provenance runDetails must be an object",
    )
    _require(
        summary,
        artifact_name,
        build_definition.get("buildType") == policy["slsa_build_type"],
        "SLSA_BUILD_TYPE_MISMATCH",
        "SLSA provenance buildType does not match policy",
    )

    builder = _object_or_failure(
        run_details.get("builder"), summary, artifact_name,
        "SLSA_STRUCTURE_INVALID", "SLSA provenance runDetails.builder must be an object",
    )
    _require(
        summary,
        artifact_name,
        builder.get("id") == policy["certificate_identity"],
        "SLSA_BUILDER_IDENTITY_MISMATCH",
        "SLSA provenance builder identity does not match policy certificate identity",
    )

    expected_external = set(policy["allowed_external_parameters"])
    external_parameters = _object_or_failure(
        build_definition.get("externalParameters"), summary, artifact_name,
        "SLSA_STRUCTURE_INVALID", "SLSA provenance externalParameters must be an object",
    )
    actual_external = set(external_parameters.keys())
    _require(
        summary,
        artifact_name,
        actual_external == expected_external,
        "SLSA_EXTERNAL_PARAMETERS_UNEXPECTED",
        "SLSA provenance externalParameters do not exactly match policy: "
        f"missing={sorted(expected_external - actual_external)}, "
        f"unexpected={sorted(actual_external - expected_external)}",
    )
    recipe_evidence = _object_or_failure(
        evidence.get("recipe"), summary, artifact_name,
        "SLSA_BUILD_INPUT_EVIDENCE_INVALID", "Build evidence recipe must be an object",
    )
    expected_values = {
        **policy["expected_external_parameters"],
        "operationId": artifact.get("operation_id"),
        "version": artifact.get("version"),
        "package": artifact.get("package"),
        "recipePath": recipe_evidence.get("path"),
    }
    _require(
        summary,
        artifact_name,
        registry == policy["expected_external_parameters"]["registry"],
        "SLSA_EXTERNAL_PARAMETER_MISMATCH",
        "Publish results registry does not match policy expectation",
    )
    for key in expected_external:
        expected_value = expected_values.get(key)
        _require(
            summary,
            artifact_name,
            expected_value is not None,
            "SLSA_EXTERNAL_PARAMETER_EXPECTATION_MISSING",
            f"Verifier has no expectation for SLSA externalParameters.{key}",
        )
        _require(
            summary,
            artifact_name,
            external_parameters.get(key) == expected_value,
            "SLSA_EXTERNAL_PARAMETER_MISMATCH",
            f"SLSA provenance externalParameters.{key} does not match policy expectation",
        )

    metadata = _object_or_failure(
        run_details.get("metadata"), summary, artifact_name,
        "SLSA_STRUCTURE_INVALID", "SLSA provenance runDetails.metadata must be an object",
    )
    try:
        expected_finished_on = normalise_slsa_timestamp(evidence.get("generated_at"))
    except ProvenanceError as exc:
        _require(
            summary, artifact_name, False, "SLSA_FINISHED_TIME_INVALID", str(exc),
        )
        expected_finished_on = None
    try:
        actual_finished_on = normalise_slsa_timestamp(metadata.get("finishedOn"))
    except ProvenanceError as exc:
        _require(
            summary, artifact_name, False, "SLSA_FINISHED_TIME_INVALID", str(exc),
        )
        actual_finished_on = None
    _require(
        summary,
        artifact_name,
        expected_finished_on is not None and actual_finished_on == expected_finished_on,
        "SLSA_FINISHED_TIME_MISMATCH",
        "SLSA provenance runDetails.metadata.finishedOn does not match build evidence in UTC",
    )

    internal_parameters = _object_or_failure(
        build_definition.get("internalParameters"), summary, artifact_name,
        "SLSA_STRUCTURE_INVALID", "SLSA provenance internalParameters must be an object",
    )
    github_parameters = _object_or_failure(
        internal_parameters.get("github"), summary, artifact_name,
        "SLSA_STRUCTURE_INVALID", "SLSA provenance internalParameters.github must be an object",
    )
    recipes_repository = github_parameters.get("repository")
    recipes_commit = github_parameters.get("sha")
    if os.getenv("GITHUB_ACTIONS") == "true":
        _require(
            summary,
            artifact_name,
            recipes_repository == os.getenv("GITHUB_REPOSITORY"),
            "SLSA_RECIPES_REPOSITORY_MISMATCH",
            "SLSA recipe repository does not match the trusted workflow context",
        )
        _require(
            summary,
            artifact_name,
            recipes_commit == os.getenv("GITHUB_SHA"),
            "SLSA_RECIPES_COMMIT_MISMATCH",
            "SLSA recipe commit does not match the trusted workflow context",
        )

    try:
        expected_dependencies = resolved_dependencies_from_evidence(
            evidence,
            recipes_repository=recipes_repository,
            recipes_commit=recipes_commit,
            hub_repository=policy["expected_external_parameters"]["hubRepository"],
        )
    except (ProvenanceError, AttributeError, KeyError, TypeError, ValueError) as exc:
        _require(
            summary,
            artifact_name,
            False,
            "SLSA_BUILD_INPUT_EVIDENCE_INVALID",
            str(exc),
        )
        expected_dependencies = []
    resolved_dependencies = build_definition.get("resolvedDependencies")
    if not isinstance(resolved_dependencies, list) or any(
        not isinstance(descriptor, dict) for descriptor in resolved_dependencies
    ):
        _require(
            summary, artifact_name, False, "SLSA_STRUCTURE_INVALID",
            "SLSA provenance resolvedDependencies must be a list of objects",
        )
        resolved_dependencies = []
    for descriptor in expected_dependencies:
        _require(
            summary,
            artifact_name,
            descriptor in resolved_dependencies,
            "SLSA_RESOLVED_DEPENDENCY_MISSING",
            f"SLSA provenance is missing required resolved dependency {descriptor.get('name')}",
        )

    byproduct_items = run_details.get("byproducts")
    if not isinstance(byproduct_items, list) or any(
        not isinstance(item, dict) for item in byproduct_items
    ):
        _require(
            summary, artifact_name, False, "SLSA_STRUCTURE_INVALID",
            "SLSA provenance byproducts must be a list of objects",
        )
        byproduct_items = []
    byproducts = {}
    for item in byproduct_items:
        uri = item.get("uri")
        if uri in byproducts:
            _require(
                summary, artifact_name, False, "SLSA_BYPRODUCT_DUPLICATE",
                f"SLSA provenance contains duplicate byproduct URI {uri!r}",
            )
            continue
        byproducts[uri] = item

    required_byproducts = _required_byproduct_paths(evidence, summary, artifact_name)
    for rel_path in sorted(required_byproducts):
        descriptor = byproducts.get(f"file:{rel_path}")
        _require(
            summary,
            artifact_name,
            descriptor is not None,
            "SLSA_BYPRODUCT_MISSING",
            f"SLSA provenance does not reference {rel_path}",
        )

    for uri, descriptor in byproducts.items():
        if not isinstance(uri, str) or not uri.startswith("file:"):
            _require(summary, artifact_name, False, "SLSA_BYPRODUCT_URI_UNSUPPORTED", f"SLSA byproduct URI is unsupported: {uri!r}")
            continue
        rel_path = uri.removeprefix("file:")
        safe_path = _safe_bundle_relative_path(rel_path)
        if safe_path is None:
            _require(summary, artifact_name, False, "SLSA_BYPRODUCT_PATH_UNSAFE", f"SLSA byproduct path is unsafe: {rel_path!r}")
            continue
        local_file = _safe_bundle_file(bundle_dir, safe_path)
        if local_file is None:
            _require(
                summary, artifact_name, False, "SLSA_BYPRODUCT_PATH_UNSAFE",
                f"SLSA byproduct is missing, symlinked, or outside the bundle: {rel_path!r}",
            )
            continue
        _require(
            summary,
            artifact_name,
            _descriptor_matches_file(descriptor, local_file),
            "SLSA_BYPRODUCT_DIGEST_MISMATCH",
            f"SLSA byproduct digest does not match {rel_path}",
        )


def _bundle_dir(registry_path: Path, artifact: dict[str, Any], summary: VerificationSummary, artifact_name: str) -> Path | None:
    operation_id = artifact.get("operation_id")
    version = artifact.get("version")
    if not operation_id or not version:
        _require(summary, artifact_name, False, "PUBLISH_RESULT_INCOMPLETE", "artifact lacks operation_id/version")
        return None
    if _safe_bundle_relative_path(operation_id) is None or _safe_bundle_relative_path(version) is None:
        _require(summary, artifact_name, False, "BUNDLE_PATH_UNSAFE", "artifact operation_id/version is unsafe")
        return None
    root = registry_path.resolve()
    bundle_dir = (root / operation_id / version).resolve()
    try:
        bundle_dir.relative_to(root)
    except ValueError:
        _require(summary, artifact_name, False, "BUNDLE_PATH_UNSAFE", "artifact resolves outside registry directory")
        return None
    _require(summary, artifact_name, bundle_dir.is_dir(), "BUNDLE_DIR_MISSING", "artifact bundle directory is missing")
    return bundle_dir if bundle_dir.is_dir() else None


def _object_or_failure(value: Any, summary: VerificationSummary, artifact_name: str, code: str, message: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    _require(summary, artifact_name, False, code, message)
    return {}


def _required_byproduct_paths(evidence: dict[str, Any], summary: VerificationSummary, artifact_name: str) -> set[str]:
    paths = {"bundle.json", "sbom.cdx.json", "build-evidence.json"}
    license_evidence = evidence.get("license")
    if license_evidence is not None:
        if not isinstance(license_evidence, dict):
            _require(
                summary, artifact_name, False, "SLSA_BYPRODUCT_EVIDENCE_INVALID",
                "Build license evidence must be an object",
            )
        else:
            _add_evidence_paths(
                paths, license_evidence.get("files", []), summary, artifact_name,
                "license evidence",
            )

    runtimes = evidence.get("runtimes")
    if not isinstance(runtimes, dict):
        _require(
            summary, artifact_name, False, "SLSA_BYPRODUCT_EVIDENCE_INVALID",
            "Build runtime evidence must be an object",
        )
        return paths
    for runtime_name, runtime_evidence in runtimes.items():
        if not isinstance(runtime_evidence, dict):
            _require(
                summary, artifact_name, False, "SLSA_BYPRODUCT_EVIDENCE_INVALID",
                f"Build evidence for runtime {runtime_name!r} must be an object",
            )
            continue
        artifacts = runtime_evidence.get("artifacts")
        if artifacts is None:
            continue
        if not isinstance(artifacts, dict):
            _require(
                summary, artifact_name, False, "SLSA_BYPRODUCT_EVIDENCE_INVALID",
                f"Artifact evidence for runtime {runtime_name!r} must be an object",
            )
            continue
        _add_evidence_paths(
            paths, artifacts.get("files", []), summary, artifact_name,
            f"runtime {runtime_name!r} artifact evidence",
        )
    return paths


def _add_evidence_paths(paths: set[str], entries: Any, summary: VerificationSummary, artifact_name: str, label: str) -> None:
    if not isinstance(entries, list):
        _require(
            summary, artifact_name, False, "SLSA_BYPRODUCT_EVIDENCE_INVALID",
            f"{label} files must be a list",
        )
        return
    for entry in entries:
        rel_path = entry.get("path") if isinstance(entry, dict) else None
        if _safe_bundle_relative_path(rel_path) is None:
            _require(
                summary, artifact_name, False, "SLSA_BYPRODUCT_EVIDENCE_INVALID",
                f"{label} contains an invalid path: {rel_path!r}",
            )
            continue
        paths.add(rel_path)


def _safe_bundle_file(bundle_dir: Path, relative_path: Path) -> Path | None:
    root = bundle_dir.resolve()
    candidate = root / relative_path
    current = root
    for part in relative_path.parts:
        current /= part
        if current.is_symlink():
            return None
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (FileNotFoundError, OSError, RuntimeError, ValueError):
        return None
    return resolved if resolved.is_file() else None


def _run_cosign(command: list[str], summary: VerificationSummary, artifact_name: str, code: str) -> str | None:
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except OSError as exc:
        _require(summary, artifact_name, False, code, f"Could not execute Cosign: {exc}")
        return None
    if result.returncode == 0:
        return result.stdout
    message = (result.stderr or result.stdout or "cosign command failed").strip()
    _require(summary, artifact_name, False, code, message)
    return None


def _verify_attestation_payload(*, output: str, local_predicate_path: Path, predicate_type: str, digest_reference: str, summary: VerificationSummary, artifact_name: str, code: str) -> None:
    local_predicate = _read_json(local_predicate_path)
    subject_digest = digest_reference.rsplit("@sha256:", 1)[1].lower()
    statements = _attestation_statements(output)
    matches = any(
        statement.get("predicateType") == predicate_type
        and statement.get("predicate") == local_predicate
        and any(
            _subject_has_digest(subject, subject_digest)
            for subject in statement.get("subject") or []
            if isinstance(subject, dict)
        )
        for statement in statements
    )
    _require(
        summary,
        artifact_name,
        matches,
        code,
        "verified attestation does not bind the published digest to the expected local predicate",
    )


def _attestation_statements(output: str) -> list[dict[str, Any]]:
    documents = _json_documents(output)
    if documents is None:
        return []
    statements = []
    for document in documents:
        statement = _decode_dsse_statement(document)
        if statement is None:
            return []
        statements.append(statement)
    return statements


def _json_documents(output: str) -> list[Any] | None:
    if not isinstance(output, str):
        return None
    decoder = json.JSONDecoder()
    documents = []
    position = 0
    while position < len(output):
        while position < len(output) and output[position].isspace():
            position += 1
        if position == len(output):
            break
        try:
            document, end = decoder.raw_decode(output, position)
        except json.JSONDecodeError:
            return None
        documents.append(document)
        position = end
    return documents


def _decode_dsse_statement(envelope: Any) -> dict[str, Any] | None:
    if not isinstance(envelope, dict):
        return None
    if envelope.get("payloadType") != "application/vnd.in-toto+json":
        return None
    payload = envelope.get("payload")
    if not isinstance(payload, str):
        return None
    try:
        decoded = base64.b64decode(payload, validate=True)
        statement = json.loads(decoded)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return statement if isinstance(statement, dict) else None


def _subject_has_digest(subject: dict[str, Any], expected: str) -> bool:
    digests = subject.get("digest")
    if not isinstance(digests, dict):
        return False
    digest = digests.get("sha256")
    return isinstance(digest, str) and digest.lower() == expected


def _descriptor_matches_file(descriptor: dict[str, Any], path: Path) -> bool:
    if not isinstance(descriptor, dict) or path.is_symlink():
        return False
    digest = descriptor.get("digest") or {}
    if not isinstance(digest, dict):
        return False
    expected = digest.get("sha256")
    return path.is_file() and isinstance(expected, str) and _is_sha256_hex(expected) and expected.lower() == _sha256_hex(path)


def _validate_policy(policy: dict[str, Any]) -> None:
    if policy.get("schema") != "biochef.signing-policy.v1":
        raise VerificationError("Signing policy schema must be biochef.signing-policy.v1")
    required_strings = {
        "registry_prefix": "registry prefix",
        "certificate_identity": "certificate identity",
        "certificate_oidc_issuer": "certificate OIDC issuer",
        "slsa_predicate_type": "SLSA predicate type",
        "slsa_build_type": "SLSA build type",
    }
    for key, label in required_strings.items():
        if not isinstance(policy.get(key), str) or not policy[key]:
            raise VerificationError(f"Signing policy is missing {label}")
    if policy["slsa_predicate_type"] != SLSA_PREDICATE_TYPE:
        raise VerificationError("Signing policy must require SLSA provenance v1")
    if policy["slsa_build_type"] != BIOCHEF_BUILD_TYPE:
        raise VerificationError("Signing policy must require the BioCHEF hub bundle build type")
    allowed_external = policy.get("allowed_external_parameters")
    if not isinstance(allowed_external, list) or not all(isinstance(item, str) and item for item in allowed_external):
        raise VerificationError("Signing policy allowed_external_parameters must be a list of strings")
    if len(set(allowed_external)) != len(allowed_external):
        raise VerificationError("Signing policy allowed_external_parameters must not contain duplicates")
    required_external = {
        "registry",
        "package",
        "operationId",
        "version",
        "recipePath",
        "hubRepository",
        "hubRef",
    }
    if set(allowed_external) != required_external:
        raise VerificationError(
            "Signing policy allowed_external_parameters must exactly match the BioCHEF build type"
        )
    expected_external = policy.get("expected_external_parameters")
    if not isinstance(expected_external, dict):
        raise VerificationError("Signing policy expected_external_parameters must be an object")
    required_fixed = {"registry", "hubRepository", "hubRef"}
    if set(expected_external) != required_fixed:
        raise VerificationError(
            "Signing policy expected_external_parameters must define exactly registry, hubRepository, and hubRef"
        )
    if not all(isinstance(expected_external[key], str) and expected_external[key] for key in required_fixed):
        raise VerificationError("Signing policy fixed external parameter expectations must be non-empty strings")
    expected_registry = expected_external["registry"].rstrip("/")
    if not policy["registry_prefix"].startswith(f"{expected_registry}/"):
        raise VerificationError(
            "Signing policy registry_prefix must be a package prefix under the expected registry"
        )
    if policy["registry_prefix"] == f"{expected_registry}/":
        raise VerificationError("Signing policy registry_prefix must include a non-empty package prefix")


def _is_digest_reference(ref: Any) -> bool:
    if not isinstance(ref, str):
        return False
    if "@sha256:" not in ref:
        return False
    digest = ref.rsplit("@sha256:", 1)[1]
    return _is_sha256_hex(digest)


def _is_sha256_hex(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdefABCDEF" for char in value)


def _safe_bundle_relative_path(value: str | None) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    if path == Path(".") or path.is_absolute() or ".." in path.parts:
        return None
    return path


def _read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise VerificationError(f"Refusing to read symlinked JSON file: {path}")
    try:
        with path.open(encoding="utf-8") as file:
            document = json.load(file)
    except FileNotFoundError as exc:
        raise VerificationError(f"Missing JSON file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise VerificationError(f"Invalid JSON file {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise VerificationError(f"JSON file must contain an object: {path}")
    return document


def _sha256_hex(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require(summary: VerificationSummary, artifact: str, condition: bool, code: str, message: str) -> None:
    if condition:
        return
    summary.failures.append(VerificationIssue(artifact=artifact, code=code, message=message))
