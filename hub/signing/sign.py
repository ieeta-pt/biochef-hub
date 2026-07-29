import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from publish.publish import read_publish_results
from signing.provenance import SLSA_PREDICATE_TYPE
from signing.verify import (
    VerificationIssue,
    check_published_evidence,
    verify_cosign_attestation,
    verify_cosign_signature,
)


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

    policy = Path(policy_path).resolve()
    results_path = Path(publish_results_path or registry_path / "publish-results.json").resolve()
    try:
        publish_results = read_publish_results(results_path)
    except RuntimeError as exc:
        raise SigningError(str(exc)) from exc
    artifacts = publish_results["artifacts"]

    summary = SigningSummary(scanned=len(artifacts))
    preflight = check_published_evidence(
        registry_dir=registry_path,
        publish_results_path=results_path,
        policy_path=policy,
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
                policy,
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

        summary.signed += 1

    return summary


def _sign_and_attest(
    cosign_bin: str,
    ref: str,
    bundle_dir: Path,
    policy_path: Path,
    *,
    max_attempts: int,
    retry_delay_seconds: int,
) -> None:
    _run(
        [cosign_bin, "sign", "--yes", ref],
        max_attempts=max_attempts,
        retry_delay_seconds=retry_delay_seconds,
        postcondition=lambda: verify_cosign_signature(
            ref, policy_path, cosign_bin
        ),
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
        postcondition=lambda: verify_cosign_attestation(
            ref,
            policy_path,
            "cyclonedx",
            bundle_dir / "sbom.cdx.json",
            cosign_bin,
        ),
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
        postcondition=lambda: verify_cosign_attestation(
            ref,
            policy_path,
            SLSA_PREDICATE_TYPE,
            bundle_dir / "provenance.slsa.json",
            cosign_bin,
        ),
    )


def _run(
    command: list[str],
    *,
    max_attempts: int,
    retry_delay_seconds: int,
    postcondition: Callable[[], bool],
) -> None:
    for attempt in range(1, max_attempts + 1):
        if postcondition():
            return
        try:
            result = subprocess.run(command, capture_output=True, text=True)
        except OSError as exc:
            raise SigningError(f"Could not execute Cosign: {exc}") from exc
        if result.returncode == 0:
            if postcondition():
                return
            if attempt == max_attempts:
                raise SigningError(
                    f"{' '.join(command)} completed, but its verification postcondition failed"
                )
            time.sleep(retry_delay_seconds * attempt)
            continue
        message = (result.stderr or result.stdout or "cosign command failed").strip()
        if postcondition():
            return
        retryable = _is_retryable_cosign_error(message) or _is_rekor_conflict(message)
        if attempt == max_attempts or not retryable:
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


def _is_rekor_conflict(message: str) -> bool:
    normalized = message.lower()
    return (
        "createlogentryconflict" in normalized
        and "equivalent entry already exists" in normalized
    )
