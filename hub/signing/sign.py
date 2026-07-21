import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from signing.provenance import SLSA_PREDICATE_TYPE
from signing.verify import VerificationIssue, check_published_evidence, verify_published_artifacts


class SigningError(RuntimeError):
    pass


@dataclass
class SigningSummary:
    scanned: int = 0
    signed: int = 0
    failures: list[VerificationIssue] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return bool(self.failures)


def sign_and_attest_published_artifacts(registry_dir: str | Path = "registry", publish_results_path: str | Path | None = None, policy_path: str | Path | None = None, cosign_bin: str = "cosign", max_attempts: int = 2, retry_delay_seconds: int = 5) -> SigningSummary:
    registry_path = Path(registry_dir).resolve()
    if not registry_path.is_dir():
        raise SigningError(f"Registry directory does not exist: {registry_path}")
    if not policy_path:
        raise SigningError("A signing verification policy is required")
    if max_attempts < 1:
        raise SigningError("max_attempts must be at least 1")
    if retry_delay_seconds < 0:
        raise SigningError("retry_delay_seconds cannot be negative")

    results_path = Path(publish_results_path or registry_path / "publish-results.json").resolve()
    publish_results = _read_json(results_path)
    artifacts = publish_results.get("artifacts")
    if (
        publish_results.get("schema") != "biochef.publish-results.v1"
        or not isinstance(artifacts, list)
        or not artifacts
        or any(not isinstance(artifact, dict) for artifact in artifacts)
    ):
        raise SigningError(f"Invalid or empty publish results file: {results_path}")

    summary = SigningSummary(scanned=len(artifacts))
    preflight = check_published_evidence(
        registry_dir=registry_path,
        publish_results_path=results_path,
        policy_path=policy_path,
    )
    if preflight.failed:
        summary.failures.extend(preflight.failures)
        return summary

    for artifact in artifacts:
        artifact_name = f"{artifact.get('operation_id')}@{artifact.get('version')}"
        ref = artifact["digest_reference"]
        bundle_dir = (
            registry_path / artifact["operation_id"] / artifact["version"]
        ).resolve()

        try:
            _sign_and_attest(
                cosign_bin,
                ref,
                bundle_dir,
                max_attempts=max_attempts,
                retry_delay_seconds=retry_delay_seconds,
            )
        except SigningError as exc:
            summary.failures.append(
                VerificationIssue(
                    artifact=artifact_name,
                    code="COSIGN_SIGN_ATTEST_FAILED",
                    message=str(exc),
                )
            )
            return summary

        for attempt in range(1, max_attempts + 1):
            verification = verify_published_artifacts(
                registry_dir=registry_path,
                publish_results_path=results_path,
                policy_path=policy_path,
                cosign_bin=cosign_bin,
                operation_id=artifact["operation_id"],
                version=artifact["version"],
            )
            if not verification.failed:
                summary.signed += 1
                break
            if attempt == max_attempts:
                summary.failures.extend(verification.failures)
                return summary
            time.sleep(retry_delay_seconds * attempt)

    return summary


def _sign_and_attest(
    cosign_bin: str,
    ref: str,
    bundle_dir: Path,
    *,
    max_attempts: int,
    retry_delay_seconds: int,
) -> None:
    _run(
        [cosign_bin, "sign", "--yes", ref],
        max_attempts=max_attempts,
        retry_delay_seconds=retry_delay_seconds,
    )
    _run(
        [
            cosign_bin,
            "attest",
            "--yes",
            "--type",
            "cyclonedx",
            "--predicate",
            str(bundle_dir / "sbom.cdx.json"),
            ref,
        ],
        max_attempts=max_attempts,
        retry_delay_seconds=retry_delay_seconds,
    )
    _run(
        [
            cosign_bin,
            "attest",
            "--yes",
            "--type",
            SLSA_PREDICATE_TYPE,
            "--predicate",
            str(bundle_dir / "provenance.slsa.json"),
            ref,
        ],
        max_attempts=max_attempts,
        retry_delay_seconds=retry_delay_seconds,
    )


def _run(
    command: list[str],
    *,
    max_attempts: int,
    retry_delay_seconds: int,
) -> None:
    for attempt in range(1, max_attempts + 1):
        try:
            result = subprocess.run(command, capture_output=True, text=True)
        except OSError as exc:
            raise SigningError(f"Could not execute Cosign: {exc}") from exc
        if result.returncode == 0:
            return
        message = (result.stderr or result.stdout or "cosign command failed").strip()
        if attempt == max_attempts or not _is_retryable_cosign_error(message):
            raise SigningError(f"{' '.join(command)} failed: {message}")
        time.sleep(retry_delay_seconds * attempt)


def _is_retryable_cosign_error(message: str) -> bool:
    normalized = message.lower()
    retryable_fragments = (
        "unexpected status code 408",
        "unexpected status code 425",
        "unexpected status code 429",
        "unexpected status code 500",
        "unexpected status code 502",
        "unexpected status code 503",
        "unexpected status code 504",
        "connection reset",
        "context deadline exceeded",
        "fetching ambient oidc credentials",
        "i/o timeout",
        "temporary failure",
        "tls handshake timeout",
    )
    return any(fragment in normalized for fragment in retryable_fragments)


def _read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise SigningError(f"Refusing to read symlinked JSON file: {path}")
    try:
        with path.open(encoding="utf-8") as file:
            document = json.load(file)
    except FileNotFoundError as exc:
        raise SigningError(f"Missing JSON file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SigningError(f"Invalid JSON file {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise SigningError(f"JSON file must contain an object: {path}")
    return document
