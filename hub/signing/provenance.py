import hashlib
import json
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from builders.build_inputs import validate_build_inputs
from builders.wasm_link_evidence import validate_wasm_link_evidence


BIOCHEF_BUILD_TYPE = "https://biochef.dev/buildtypes/hub-bundle/v1"
SLSA_PREDICATE_TYPE = "https://slsa.dev/provenance/v1"
PROVENANCE_FILE_NAME = "provenance.slsa.json"


class ProvenanceError(RuntimeError):
    pass


@dataclass
class ProvenanceSummary:
    scanned: int = 0
    written: int = 0
    outputs: list[Path] = field(default_factory=list)


def generate_provenance_predicates(registry_dir: str | Path = "registry", publish_results_path: str | Path | None = None, hub_repository: str | None = None, hub_ref: str | None = None, workflow_path: str | None = None) -> ProvenanceSummary:
    registry_path = Path(registry_dir).resolve()
    if not registry_path.is_dir():
        raise ProvenanceError(f"Registry directory does not exist: {registry_path}")

    results_path = Path(publish_results_path or registry_path / "publish-results.json").resolve()
    publish_results = _read_json(results_path)
    artifacts = publish_results.get("artifacts")
    if (
        publish_results.get("schema") != "biochef.publish-results.v1"
        or not isinstance(artifacts, list)
        or not artifacts
        or any(not isinstance(artifact, dict) for artifact in artifacts)
    ):
        raise ProvenanceError(f"Invalid or empty publish results file: {results_path}")
    registry = publish_results.get("registry")
    if not isinstance(registry, str) or not registry:
        raise ProvenanceError(f"Publish results are missing the registry identity: {results_path}")
    if not isinstance(hub_repository, str) or not hub_repository:
        raise ProvenanceError("BioCHEF Hub repository identity is required")
    if not isinstance(hub_ref, str) or not hub_ref:
        raise ProvenanceError("BioCHEF Hub ref is required")

    seen_artifacts = set()
    for artifact in artifacts:
        missing_fields = [
            field
            for field in ("operation_id", "version", "package")
            if not isinstance(artifact.get(field), str) or not artifact[field]
        ]
        if missing_fields:
            raise ProvenanceError(
                "Publish results artifact is missing required fields: "
                + ", ".join(missing_fields)
            )
        identity = (artifact.get("operation_id"), artifact.get("version"))
        if identity in seen_artifacts:
            raise ProvenanceError(
                f"Publish results contain duplicate artifact {identity[0]}@{identity[1]}"
            )
        seen_artifacts.add(identity)

    context = _github_context(
        workflow_path=workflow_path,
        builder_repository=hub_repository,
        builder_ref=hub_ref,
    )
    _ensure_clean_hub_checkout()
    prepared = []
    for artifact in artifacts:
        bundle_dir = _bundle_dir(registry_path, artifact)
        predicate = _predicate_for_artifact(
            artifact=artifact,
            bundle_dir=bundle_dir,
            context=context,
            hub_repository=hub_repository,
            hub_ref=hub_ref,
            registry=registry,
        )
        output_path = bundle_dir / PROVENANCE_FILE_NAME
        if output_path.is_symlink():
            raise ProvenanceError(f"Refusing to write symlinked provenance file: {output_path}")
        prepared.append((output_path, predicate))

    summary = ProvenanceSummary(scanned=len(prepared))
    for output_path, predicate in prepared:
        output_path.write_text(json.dumps(predicate, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        summary.written += 1
        summary.outputs.append(output_path)
    return summary


def _predicate_for_artifact(artifact: dict[str, Any], bundle_dir: Path, context: dict[str, str | None], hub_repository: str | None, hub_ref: str | None, registry: str | None) -> dict[str, Any]:
    bundle = _read_json(_required_bundle_file(bundle_dir, "bundle.json"))
    evidence = _read_json(_required_bundle_file(bundle_dir, "build-evidence.json"))
    recipe = evidence.get("recipe") or {}
    operation = evidence.get("operation") or {}
    hub = evidence.get("hub") or {}
    if not all(isinstance(value, dict) for value in (recipe, operation, hub)):
        raise ProvenanceError(f"{bundle_dir} build evidence has invalid identity objects")

    operation_id = artifact.get("operation_id")
    version = artifact.get("version")
    expected_identities = (
        (bundle.get("id"), operation_id, "bundle id"),
        (bundle.get("version"), version, "bundle version"),
        (operation.get("id"), operation_id, "build evidence operation id"),
        (recipe.get("version"), version, "build evidence recipe version"),
    )
    for actual, expected, label in expected_identities:
        if actual != expected:
            raise ProvenanceError(
                f"{bundle_dir} {label} does not match published artifact: {actual!r} != {expected!r}"
            )

    current_hub_commit = _current_hub_commit()
    if hub.get("commit") != current_hub_commit:
        raise ProvenanceError(
            "Build evidence Hub commit does not match the Hub checkout generating provenance: "
            f"{hub.get('commit')!r} != {current_hub_commit!r}"
        )

    recipe_path = recipe.get("path")
    if not _safe_relative(recipe_path):
        raise ProvenanceError(f"{bundle_dir} build-evidence.json has an unsafe recipe path")
    generated_at = normalise_slsa_timestamp(evidence.get("generated_at"))

    external_parameters = {
        "registry": registry,
        "package": artifact.get("package"),
        "operationId": artifact.get("operation_id"),
        "version": artifact.get("version"),
        "recipePath": recipe_path,
        "hubRepository": hub_repository,
        "hubRef": hub_ref,
    }

    github_parameters = _without_empty(
        {
            "repository": context.get("repository"),
            "ref": context.get("ref"),
            "sha": context.get("sha"),
            "workflow": context.get("workflow"),
            "workflowRef": context.get("caller_workflow_ref"),
            "builderWorkflowRef": context.get("builder_workflow_ref"),
            "runId": context.get("run_id"),
            "runAttempt": context.get("run_attempt"),
        }
    )

    build_definition = {
        "buildType": BIOCHEF_BUILD_TYPE,
        "externalParameters": _without_empty(external_parameters),
        "resolvedDependencies": _resolved_dependencies(
            evidence=evidence,
            context=context,
            hub_repository=hub_repository,
        ),
    }
    if github_parameters:
        build_definition["internalParameters"] = {"github": github_parameters}

    return {
        "buildDefinition": build_definition,
        "runDetails": {
            "builder": {
                "id": context.get("workflow_identity") or "local://unknown-builder",
            },
            "metadata": _without_empty(
                {
                    "invocationId": context.get("invocation_id"),
                    "finishedOn": generated_at,
                }
            ),
            "byproducts": _byproducts(
                bundle_dir,
                evidence,
            ),
        },
    }


def _resolved_dependencies(evidence: dict[str, Any], context: dict[str, str | None], hub_repository: str | None) -> list[dict[str, Any]]:
    return resolved_dependencies_from_evidence(
        evidence,
        recipes_repository=context.get("repository"),
        recipes_commit=context.get("sha"),
        hub_repository=hub_repository,
    )


def resolved_dependencies_from_evidence(
    evidence: dict[str, Any],
    *,
    recipes_repository: str | None,
    recipes_commit: str | None,
    hub_repository: str | None,
) -> list[dict[str, Any]]:
    dependencies: list[dict[str, Any]] = []
    recipe = evidence.get("recipe") or {}
    hub = evidence.get("hub") or {}
    source = recipe.get("source") or {}

    if bool(recipes_repository) != bool(recipes_commit):
        raise ProvenanceError(
            "Recipe repository and commit identity must either both be present or both be absent"
        )
    if recipes_repository and recipes_commit:
        _require_git_commit(recipes_commit, "recipe repository")
        dependencies.append(
            {
                "name": "biochef-recipes",
                "uri": _git_uri(recipes_repository, recipes_commit),
                "digest": {"gitCommit": recipes_commit},
            }
        )

    if not recipe.get("path") or not recipe.get("digest"):
        raise ProvenanceError("Build evidence is missing recipe path or digest")
    dependencies.append(
        {
            "name": "recipe/biochef.yaml",
            "uri": f"file:{recipe['path']}",
            "digest": _digest_object(recipe["digest"]),
        }
    )

    if not hub_repository or not hub.get("commit"):
        raise ProvenanceError("Build evidence or policy is missing BioCHEF Hub identity")
    _require_git_commit(hub["commit"], "BioCHEF Hub")
    dependencies.append(
        {
            "name": "biochef-hub",
            "uri": _git_uri(hub_repository, hub["commit"]),
            "digest": {"gitCommit": hub["commit"]},
        }
    )

    dependencies.extend(_source_dependencies(source, evidence))
    dependencies.extend(_toolchain_dependencies(evidence))
    dependencies.extend(build_input_dependencies(evidence))
    dependencies.extend(_runtime_link_dependencies(evidence))
    return _dedupe_descriptors(dependencies)


def _source_dependencies(source: dict[str, Any], evidence: dict[str, Any]) -> list[dict[str, Any]]:
    dependencies: list[dict[str, Any]] = []
    repo = source.get("repo") or source.get("url")
    commit = source.get("commit")
    source_sha256 = source.get("sha256")
    if repo and commit:
        _require_git_commit(commit, "declared upstream source")
        dependencies.append(
            {
                "name": "declared-upstream-source",
                "uri": _git_uri(repo, commit),
                "digest": {"gitCommit": commit},
            }
        )
    elif repo and source_sha256:
        dependencies.append(
            {
                "name": "declared-upstream-source",
                "uri": repo,
                "digest": _digest_object(source_sha256),
            }
        )
    elif source_sha256:
        dependencies.append(
            {
                "name": "declared-upstream-source",
                "uri": f"urn:biochef:vendored-source:sha256:{source_sha256}",
                "digest": _digest_object(source_sha256),
            }
        )
    else:
        raise ProvenanceError(f"Recipe source {repo!r} is missing immutable commit or sha256")

    for runtime_name, runtime_data in (evidence.get("runtimes") or {}).items():
        actual = (((runtime_data.get("build") or {}).get("source") or {}).get("actual") or {})
        actual_kind = actual.get("kind")
        actual_repo = actual.get("repo")
        actual_commit = actual.get("resolved_commit")
        if actual_kind == "git" and actual_repo and actual_commit:
            _require_git_commit(actual_commit, f"{runtime_name} actual source")
            dependencies.append(
                {
                    "name": f"{runtime_name}-actual-source",
                    "uri": _git_uri(actual_repo, actual_commit),
                    "digest": {"gitCommit": actual_commit},
                }
            )
        elif actual_kind == "git":
            raise ProvenanceError(f"{runtime_name} actual git source is missing repo or resolved commit")
        for file_entry in actual.get("files") or []:
            if file_entry.get("path") and file_entry.get("sha256"):
                if not _safe_relative(file_entry["path"]):
                    raise ProvenanceError(f"{runtime_name} actual source file path is unsafe: {file_entry['path']}")
                dependencies.append(
                    {
                        "name": f"{runtime_name}-actual-source-file",
                        "uri": f"file:{file_entry['path']}",
                        "digest": _digest_object(file_entry["sha256"]),
                    }
                )
    return dependencies


def _toolchain_dependencies(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    dependencies: list[dict[str, Any]] = []
    for runtime_name, runtime_data in (evidence.get("runtimes") or {}).items():
        build = runtime_data.get("build") or {}
        builder = build.get("builder")
        biowasm = build.get("biowasm") or {}
        biowasm_commit = biowasm.get("resolved_commit") or biowasm.get("commit")
        if biowasm.get("repo") and biowasm_commit:
            _require_git_commit(biowasm_commit, f"{runtime_name} BioWASM")
            dependencies.append(
                {
                    "name": f"{runtime_name}-biowasm",
                    "uri": _git_uri(biowasm["repo"], biowasm_commit),
                    "digest": {"gitCommit": biowasm_commit},
                }
            )
        elif biowasm:
            raise ProvenanceError(f"{runtime_name} BioWASM toolchain evidence is missing repo or resolved commit")
        elif builder == "biowasm":
            raise ProvenanceError(f"{runtime_name} BioWASM toolchain evidence is missing")

        toolchain = build.get("toolchain") or {}
        emsdk_commit = toolchain.get("emsdk_resolved_commit")
        if emsdk_commit:
            _require_git_commit(emsdk_commit, f"{runtime_name} emsdk")
            dependencies.append(
                {
                    "name": f"{runtime_name}-emsdk",
                    "uri": _git_uri("https://github.com/emscripten-core/emsdk", emsdk_commit),
                    "digest": {"gitCommit": emsdk_commit},
                }
            )
        elif builder in {"biowasm", "emscripten"} or any(
            toolchain.get(key)
            for key in (
                "emcc_version",
                "emmake_path",
                "emsdk_directory",
                "emsdk_requested_commit",
            )
        ):
            raise ProvenanceError(f"{runtime_name} Emscripten toolchain evidence is missing resolved emsdk commit")

    return dependencies


def build_input_dependencies(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    dependencies = []
    for runtime_name, runtime_data in (evidence.get("runtimes") or {}).items():
        build_inputs = (runtime_data.get("build") or {}).get("build_inputs")
        errors = validate_build_inputs(build_inputs)
        if errors:
            raise ProvenanceError(
                f"{runtime_name} build input evidence is invalid: " + "; ".join(errors)
            )

        for submodule in build_inputs.get("git_submodules") or []:
            commit = submodule["resolved_commit"]
            _require_git_commit(commit, f"{runtime_name} fetched submodule {submodule['path']}")
            dependencies.append(
                {
                    "name": f"{runtime_name}-fetched-git-submodule:{submodule['path']}",
                    "uri": _git_uri(submodule["repo"], commit),
                    "digest": {"gitCommit": commit},
                }
            )
            dependencies.append(
                {
                    "name": f"{runtime_name}-post-build-git-submodule-tree:{submodule['path']}",
                    "uri": (
                        "urn:biochef:post-build-tree:"
                        f"{runtime_name}:{submodule['path']}"
                    ),
                    "digest": {"sha256": submodule["post_build_tree_sha256"]},
                }
            )

    return dependencies


def _runtime_link_dependencies(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    dependencies = []
    for runtime_name, runtime_data in (evidence.get("runtimes") or {}).items():
        build = runtime_data.get("build") or {}
        if runtime_name == "wasm":
            link = build.get("wasm_link")
            if not isinstance(link, dict) or not link.get("complete"):
                continue
            errors = validate_wasm_link_evidence(link)
            if errors:
                raise ProvenanceError(
                    "invalid complete WebAssembly linker evidence: " + "; ".join(errors)
                )
            identities = []
            for output in link.get("outputs") or []:
                identities.extend(
                    (archive.get("identity") or {})
                    for archive in output.get("selected_archives") or []
                )
                identities.extend(
                    (item.get("identity") or {})
                    for item in output.get("direct_inputs") or []
                )
            for identity in identities:
                descriptor = _acquired_source_descriptor(runtime_name, identity)
                if descriptor:
                    dependencies.append(descriptor)

    return dependencies


def _acquired_source_descriptor(runtime_name: str, identity: dict[str, Any]) -> dict[str, Any] | None:
    if identity.get("kind") not in {"downloaded-source", "emscripten-port"}:
        return None
    source_url = identity.get("source_url")
    source_sha256 = identity.get("source_sha256")
    source_sha512 = identity.get("source_archive_sha512")
    if not source_url or not (source_sha256 or source_sha512):
        raise ProvenanceError(
            f"{runtime_name} linked {identity.get('kind')} is missing source URL or immutable digest"
        )
    if source_sha256:
        _require_hex_digest(source_sha256, 64, "SHA-256")
        algorithm, digest = "sha256", source_sha256
    else:
        _require_hex_digest(source_sha512, 128, "SHA-512")
        algorithm, digest = "sha512", source_sha512
    return {
        "name": f"{runtime_name}-{identity.get('kind')}:{identity.get('name') or 'source'}",
        "uri": source_url,
        "digest": {algorithm: digest},
    }


def _byproducts(bundle_dir: Path, evidence: dict[str, Any]) -> list[dict[str, Any]]:
    byproducts = [
        _file_descriptor(bundle_dir, "bundle.json", "bundle.json"),
        _file_descriptor(bundle_dir, "sbom.cdx.json", "sbom.cdx.json"),
        _file_descriptor(bundle_dir, "build-evidence.json", "build-evidence.json"),
    ]

    for license_file in ((evidence.get("license") or {}).get("files") or []):
        rel_path = license_file.get("path")
        if not rel_path:
            raise ProvenanceError("License evidence entry is missing path")
        if not _safe_relative(rel_path):
            raise ProvenanceError(f"License evidence path is unsafe: {rel_path}")
        byproducts.append(_file_descriptor(bundle_dir, rel_path, f"license:{rel_path}"))

    for runtime_name, runtime_data in (evidence.get("runtimes") or {}).items():
        files = ((runtime_data.get("artifacts") or {}).get("files") or [])
        for artifact in files:
            rel_path = artifact.get("path")
            if not rel_path:
                raise ProvenanceError(f"{runtime_name} runtime artifact entry is missing path")
            if not _safe_relative(rel_path):
                raise ProvenanceError(f"{runtime_name} runtime artifact path is unsafe: {rel_path}")
            byproducts.append(_file_descriptor(bundle_dir, rel_path, f"{runtime_name}:{rel_path}"))

    return _dedupe_descriptors(byproducts)


def _file_descriptor(bundle_dir: Path, rel_path: str, name: str) -> dict[str, Any]:
    path = _required_bundle_file(bundle_dir, rel_path)
    return {
        "name": name,
        "uri": f"file:{rel_path}",
        "digest": {"sha256": _sha256_hex(path)},
    }


def _bundle_dir(registry_path: Path, artifact: dict[str, Any]) -> Path:
    operation_id = artifact.get("operation_id")
    version = artifact.get("version")
    if not operation_id or not version:
        raise ProvenanceError(f"Publish results artifact is missing operation_id/version: {artifact}")
    if not _safe_relative(operation_id) or not _safe_relative(version):
        raise ProvenanceError(f"Publish results artifact has unsafe operation_id/version: {operation_id}@{version}")
    root = registry_path.resolve()
    unresolved_bundle_dir = root / operation_id / version
    for path in (root / operation_id, unresolved_bundle_dir):
        if path.is_symlink():
            raise ProvenanceError(f"Unsafe symlinked bundle path for {operation_id}@{version}")
    try:
        bundle_dir = unresolved_bundle_dir.resolve(strict=True)
    except (FileNotFoundError, OSError, RuntimeError) as exc:
        raise ProvenanceError(f"Missing bundle directory for {operation_id}@{version}") from exc
    try:
        bundle_dir.relative_to(root)
    except ValueError as exc:
        raise ProvenanceError(f"Unsafe bundle path for {operation_id}@{version}") from exc
    if not bundle_dir.is_dir():
        raise ProvenanceError(f"Missing bundle directory for {operation_id}@{version}")
    for required in ("bundle.json", "sbom.cdx.json", "build-evidence.json"):
        _required_bundle_file(bundle_dir, required)
    return bundle_dir


def _github_context(workflow_path: str | None, builder_repository: str | None, builder_ref: str | None) -> dict[str, str | None]:
    server_url = os.getenv("GITHUB_SERVER_URL", "https://github.com")
    repository = os.getenv("GITHUB_REPOSITORY")
    caller_workflow_ref = os.getenv("GITHUB_WORKFLOW_REF")
    github_ref = os.getenv("GITHUB_REF")
    if not caller_workflow_ref and repository and workflow_path and github_ref:
        caller_workflow_ref = f"{repository}/{workflow_path}@{github_ref}"

    builder_workflow_ref = _workflow_ref(builder_repository, workflow_path, builder_ref)
    if not builder_workflow_ref:
        builder_workflow_ref = caller_workflow_ref

    workflow_identity = f"{server_url}/{builder_workflow_ref}" if builder_workflow_ref else None
    run_id = os.getenv("GITHUB_RUN_ID")
    run_attempt = os.getenv("GITHUB_RUN_ATTEMPT")
    invocation_id = None
    if repository and run_id:
        invocation_id = f"{server_url}/{repository}/actions/runs/{run_id}"
        if run_attempt:
            invocation_id = f"{invocation_id}/attempts/{run_attempt}"

    return {
        "repository": repository,
        "ref": github_ref,
        "sha": os.getenv("GITHUB_SHA"),
        "workflow": os.getenv("GITHUB_WORKFLOW"),
        "caller_workflow_ref": caller_workflow_ref,
        "builder_workflow_ref": builder_workflow_ref,
        "workflow_identity": workflow_identity,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "invocation_id": invocation_id,
    }


def _workflow_ref(repository: str | None, workflow_path: str | None, ref: str | None) -> str | None:
    if not repository or not workflow_path or not ref:
        return None
    return f"{repository}/{workflow_path}@{_normalise_github_ref(ref)}"


def _normalise_github_ref(ref: str) -> str:
    if ref.startswith("refs/") or _is_git_sha(ref):
        return ref
    return f"refs/heads/{ref}"


def _is_git_sha(ref: str) -> bool:
    return len(ref) == 40 and all(char in "0123456789abcdefABCDEF" for char in ref)


def _git_uri(repository: str, ref: str | None) -> str:
    if repository.startswith("http://") or repository.startswith("https://") or repository.startswith("git+"):
        base = repository if repository.startswith("git+") else f"git+{repository}"
    elif "/" in repository:
        base = f"git+https://github.com/{repository}"
    else:
        base = repository
    return f"{base}@{ref}" if ref else base


def _dedupe_descriptors(descriptors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    identities = {}
    deduped = []
    for descriptor in descriptors:
        identity = (descriptor.get("name"), descriptor.get("uri"))
        previous = identities.get(identity)
        if previous is not None and previous != descriptor:
            raise ProvenanceError(
                f"Conflicting provenance descriptors for {identity[0]!r} at {identity[1]!r}"
            )
        identities[identity] = descriptor
        key = json.dumps(descriptor, sort_keys=True, separators=(",", ":"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(descriptor)
    return deduped


def _digest_object(digest: str | None) -> dict[str, str] | None:
    if not digest:
        return None
    if not isinstance(digest, str):
        raise ProvenanceError(f"Invalid SHA-256 digest value: {digest!r}")
    value = digest.split(":", 1)[1] if digest.startswith("sha256:") else digest
    _require_hex_digest(value, 64, "SHA-256")
    return {"sha256": value}


def _sha256_hex(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _current_hub_commit() -> str:
    hub_root = Path(__file__).resolve().parents[2]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=hub_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProvenanceError(
            f"Could not resolve the BioCHEF Hub checkout commit at {hub_root}"
        ) from exc
    commit = result.stdout.strip()
    if not _is_git_sha(commit):
        raise ProvenanceError(f"BioCHEF Hub checkout returned an invalid commit: {commit!r}")
    return commit


def _ensure_clean_hub_checkout() -> None:
    if os.getenv("GITHUB_ACTIONS") != "true":
        return
    hub_root = Path(__file__).resolve().parents[2]
    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            cwd=hub_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProvenanceError(
            f"Could not check the BioCHEF Hub checkout state at {hub_root}"
        ) from exc
    if result.stdout.strip():
        raise ProvenanceError("Refusing to generate release provenance from a dirty Hub checkout")


def normalise_slsa_timestamp(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ProvenanceError("build-evidence.json is missing a valid generated_at timestamp")
    timestamp = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError as exc:
        raise ProvenanceError(f"Invalid build evidence timestamp: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProvenanceError(f"Build evidence timestamp is not timezone-aware: {value!r}")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _required_bundle_file(bundle_dir: Path, relative_path: str) -> Path:
    if not _safe_relative(relative_path):
        raise ProvenanceError(f"Unsafe bundle file path: {relative_path!r}")
    root = bundle_dir.resolve()
    candidate = root / relative_path
    current = root
    for part in Path(relative_path).parts:
        current /= part
        if current.is_symlink():
            raise ProvenanceError(f"Refusing to read symlinked bundle file: {relative_path}")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
        raise ProvenanceError(f"Bundle file is missing or unsafe: {relative_path}") from exc
    if not resolved.is_file():
        raise ProvenanceError(f"Bundle file is not a regular file: {relative_path}")
    return resolved


def _require_git_commit(value: Any, label: str) -> None:
    if not isinstance(value, str) or not _is_git_sha(value):
        raise ProvenanceError(f"{label} evidence has an invalid Git commit: {value!r}")


def _require_hex_digest(value: Any, length: int, label: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != length
        or any(char not in "0123456789abcdefABCDEF" for char in value)
    ):
        raise ProvenanceError(f"Invalid {label} digest value: {value!r}")


def _safe_relative(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = Path(value)
    return path != Path(".") and not path.is_absolute() and ".." not in path.parts


def _read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise ProvenanceError(f"Refusing to read symlinked JSON file: {path}")
    try:
        with path.open(encoding="utf-8") as file:
            document = json.load(file)
    except FileNotFoundError as exc:
        raise ProvenanceError(f"Missing JSON file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ProvenanceError(f"Invalid JSON file {path}: {exc}") from exc
    except OSError as exc:
        raise ProvenanceError(f"Could not read JSON file {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise ProvenanceError(f"JSON file must contain an object: {path}")
    return document


def _without_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, "", [], {})}
