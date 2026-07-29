from pathlib import Path
import docker
import os
import tempfile
import re
import json

from builders.container import (
    ContainerEvidenceError,
    canonical_digest,
    extract_container_archive,
    file_digest,
    image_evidence,
    safe_child_path,
    safe_segment,
)


BUILDERS_DIR = Path(__file__).resolve().parent
DOCKERFILE = BUILDERS_DIR / "biowasm.Dockerfile"
LOCK_FILE = BUILDERS_DIR / "biowasm-builder.lock.json"
RUNNER_FILE = BUILDERS_DIR / "biowasm_runner.py"
IMAGE_NAME = "biochef-biowasm-builder"
COMMIT = re.compile(r"^[0-9a-f]{40}$")
DIGEST_REFERENCE = re.compile(r"^.+@sha256:[0-9a-f]{64}$")


def image_exists(client, reference):
    try:
        return client.images.get(reference)
    except docker.errors.ImageNotFound:
        return None


def build_image(client, reference, lock):
    print(f"Building BioWASM Docker image {reference}...")
    image, logs = client.images.build(
        path=str(BUILDERS_DIR),
        dockerfile=DOCKERFILE.name,
        tag=reference,
        buildargs={
            "EMSCRIPTEN_IMAGE": lock["base_image"],
            "BIOWASM_REPOSITORY": lock["biowasm_repository"],
            "BIOWASM_COMMIT": lock["biowasm_commit"],
        },
        rm=True,
    )
    for chunk in logs:
        if "stream" in chunk:
            print(chunk["stream"], end="")
    return image


def build(tool_name, version, output_dir="build", declared_source=None, license_files=(), evidence_files=()):
    package = safe_segment(tool_name, "BioWASM package name")
    package_version = safe_segment(version, "BioWASM package version")
    lock = load_lock()
    client = docker.from_env()
    image, requested_reference, context_digest = resolve_image(client, lock)
    command = [
        "/usr/local/bin/biochef-biowasm-build",
        "--tool",
        package,
        "--version",
        package_version,
    ]
    for path in license_files:
        command.extend(("--license-file", str(path)))
    for path in evidence_files:
        command.extend(("--evidence-file", str(path)))

    evidence = {
        "builder": "biowasm",
        "package": package,
        "version": package_version,
        "source": {"declared": declared_source or {}},
        "builder_image": image_evidence(
            image,
            requested_reference=requested_reference,
            context_digest=context_digest,
        ),
        "isolation": {
            "containerized": True,
            "capabilities_dropped": ["ALL"],
            "no_new_privileges": True,
            "network": "default",
        },
    }

    container = client.containers.run(
        image=image.id,
        working_dir="/biowasm",
        command=command,
        cap_drop=["ALL"],
        security_opt=["no-new-privileges:true"],
        detach=True,
    )
    try:
        for line in container.logs(stream=True):
            print(line.decode(errors="replace"), end="")
        status = container.wait()
        observations, license_payload = read_observations(container)
        if observations.get("compile_exit_code") != status.get("StatusCode"):
            raise ContainerEvidenceError(
                "container observation does not match the build exit code"
            )
        validate_observations(observations, lock)
        evidence.update(
            {
                "framework": observations["framework"],
                "source": {
                    "declared": declared_source or {},
                    "actual": observations["source"],
                },
                "configuration": observations["configuration"],
                "dependencies": observations["dependencies"],
                "toolchain": observations["toolchain"],
                "status": {"exit_code": status.get("StatusCode")},
            }
        )
        if status.get("StatusCode") != 0:
            return {"output_dir": "", "evidence": evidence}

        destination = Path(output_dir).resolve() / package
        stream, _ = container.get_archive(
            f"/biowasm/build/{package}/{package_version}"
        )
        extract_container_archive(
            stream,
            destination,
            expected_top_level=package_version,
        )
        evidence["license_files"] = materialize_license_files(
            destination,
            observations["license_files"],
            license_payload,
        )
        return {"output_dir": str(destination), "evidence": evidence}
    except (docker.errors.DockerException, ContainerEvidenceError) as exc:
        raise RuntimeError(f"BioWASM container build failed: {exc}") from exc
    finally:
        container.remove(force=True)


def resolve_image(client, lock):
    configured = os.getenv("BIOCHEF_BIOWASM_BUILDER_IMAGE")
    if configured:
        if not DIGEST_REFERENCE.fullmatch(configured):
            raise RuntimeError(
                "BIOCHEF_BIOWASM_BUILDER_IMAGE must be an OCI digest reference"
            )
        image = image_exists(client, configured) or client.images.pull(configured)
        return image, configured, None

    context_digest = canonical_digest(
        {
            "dockerfile": file_digest(DOCKERFILE),
            "lock": lock,
            "runner": file_digest(RUNNER_FILE),
        }
    )
    reference = f"{IMAGE_NAME}:{context_digest[7:23]}"
    image = image_exists(client, reference)
    return (
        image or build_image(client, reference, lock),
        reference,
        context_digest,
    )


def load_lock():
    try:
        lock = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not read BioWASM builder lock: {exc}") from exc
    if not DIGEST_REFERENCE.fullmatch(str(lock.get("base_image", ""))):
        raise RuntimeError("BioWASM base image must be pinned by OCI digest")
    if not str(lock.get("biowasm_repository", "")).startswith("https://"):
        raise RuntimeError("BioWASM repository must be an HTTPS URL")
    if not COMMIT.fullmatch(str(lock.get("biowasm_commit", ""))):
        raise RuntimeError("BioWASM commit must be a full lowercase Git commit")
    return lock


def read_observations(container):
    with tempfile.TemporaryDirectory(prefix="biochef-builder-evidence-") as temporary:
        stream, _ = container.get_archive("/tmp/biochef-builder-evidence")
        extract_container_archive(
            stream,
            temporary,
            expected_top_level="biochef-builder-evidence",
        )
        root = Path(temporary)
        try:
            observations = json.loads(
                (root / "observations.json").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise ContainerEvidenceError(
                f"could not read BioWASM observations: {exc}"
            ) from exc
        payload = {}
        for item in observations.get("license_files") or []:
            archive_path = item.get("archive_path")
            source = root / archive_path if isinstance(archive_path, str) else None
            if (
                source is None
                or source.is_symlink()
                or not source.is_file()
                or file_digest(source) != item.get("digest")
            ):
                raise ContainerEvidenceError(
                    "container license evidence is missing or has an invalid digest"
                )
            payload[archive_path] = source.read_bytes()
        return observations, payload


def validate_observations(observations, lock):
    if not isinstance(observations, dict):
        raise ContainerEvidenceError("BioWASM observations must be an object")
    framework = observations.get("framework") or {}
    if (
        framework.get("kind") != "git"
        or framework.get("commit") != lock["biowasm_commit"]
    ):
        raise ContainerEvidenceError(
            "observed BioWASM framework does not match the builder lock"
        )
    source = observations.get("source")
    toolchain = observations.get("toolchain")
    if not isinstance(source, dict) or source.get("kind") not in {"git", "vendored"}:
        raise ContainerEvidenceError("BioWASM source identity is missing")
    if (
        not isinstance(toolchain, dict)
        or toolchain.get("name") != "emscripten"
        or not COMMIT.fullmatch(str(toolchain.get("commit", "")))
    ):
        raise ContainerEvidenceError("Emscripten toolchain identity is missing")
    for field in ("configuration", "dependencies", "license_files"):
        if not isinstance(observations.get(field), (dict, list)):
            raise ContainerEvidenceError(f"BioWASM observation is missing {field}")


def materialize_license_files(destination, files, payload):
    materialized = []
    root = Path(destination) / ".biochef-license-evidence"
    for item in files:
        content = payload[item["archive_path"]]
        role_root = root / safe_segment(item["role"], "license evidence role")
        target = safe_child_path(
            role_root,
            item["source_path"],
            "license evidence path",
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        materialized.append(
            {
                **item,
                "local_path": target.relative_to(destination).as_posix(),
            }
        )
    return materialized
