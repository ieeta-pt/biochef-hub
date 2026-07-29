import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from builders.container import is_sha256_digest, sha256_hex
from publish.publish import read_publish_results


BIOCHEF_BUILD_TYPE = "https://biochef.dev/buildtypes/hub-bundle/v1"
SLSA_PREDICATE_TYPE = "https://slsa.dev/provenance/v1"
PROVENANCE_FILE_NAME = "provenance.slsa.json"
COMMIT = re.compile(r"^[0-9a-f]{40}$")


class ProvenanceError(RuntimeError):
    pass


@dataclass
class ProvenanceSummary:
    scanned: int = 0
    written: int = 0
    outputs: list[Path] = field(default_factory=list)


def generate_provenance_predicates(
    registry_dir="registry",
    publish_results_path=None,
    hub_repository=None,
    hub_ref=None,
    workflow_path=None,
):
    registry_path = Path(registry_dir).resolve()
    if not registry_path.is_dir():
        raise ProvenanceError(
            f"Registry directory does not exist: {registry_path}"
        )
    results_path = Path(
        publish_results_path or registry_path / "publish-results.json"
    ).resolve()
    try:
        publish_results = read_publish_results(results_path)
    except RuntimeError as exc:
        raise ProvenanceError(str(exc)) from exc
    if not hub_repository or not hub_ref:
        raise ProvenanceError("BioCHEF Hub repository and ref are required")

    current_hub_commit = git_output(["rev-parse", "HEAD"])
    if not COMMIT.fullmatch(current_hub_commit or ""):
        raise ProvenanceError("Could not resolve the BioCHEF Hub commit")
    if git_output(["status", "--short"]):
        raise ProvenanceError(
            "Refusing to generate release provenance from a dirty Hub checkout"
        )

    context = github_context(workflow_path)
    prepared = []
    for artifact in publish_results["artifacts"]:
        bundle_dir = artifact_bundle_dir(registry_path, artifact)
        evidence = read_json(require_file(bundle_dir, "build-evidence.json"))
        if (evidence.get("hub") or {}).get("commit") != current_hub_commit:
            raise ProvenanceError(
                f"{bundle_dir} was built by a different BioCHEF Hub commit"
            )
        predicate = predicate_for_artifact(
            artifact,
            bundle_dir,
            evidence,
            publish_results["registry"],
            hub_repository,
            hub_ref,
            context,
        )
        output = bundle_dir / PROVENANCE_FILE_NAME
        if output.is_symlink():
            raise ProvenanceError(
                f"Refusing to overwrite symlinked provenance: {output}"
            )
        prepared.append((output, predicate))

    summary = ProvenanceSummary(scanned=len(prepared))
    for output, predicate in prepared:
        output.write_text(
            json.dumps(predicate, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        summary.written += 1
        summary.outputs.append(output)
    return summary


def predicate_for_artifact(
    artifact,
    bundle_dir,
    evidence,
    registry,
    hub_repository,
    hub_ref,
    context,
):
    bundle = read_json(require_file(bundle_dir, "bundle.json"))
    recipe = evidence.get("recipe") or {}
    operation = evidence.get("operation") or {}
    for actual, expected, label in (
        (bundle.get("id"), artifact["operation_id"], "bundle id"),
        (bundle.get("version"), artifact["version"], "bundle version"),
        (operation.get("id"), artifact["operation_id"], "evidence operation id"),
        (recipe.get("version"), artifact["version"], "evidence recipe version"),
    ):
        if actual != expected:
            raise ProvenanceError(
                f"{bundle_dir} {label} does not match the published artifact"
            )

    external_parameters = compact(
        {
            "registry": registry,
            "package": artifact["package"],
            "operationId": artifact["operation_id"],
            "version": artifact["version"],
            "recipePath": recipe.get("path"),
            "hubRepository": hub_repository,
            "hubRef": hub_ref,
        }
    )
    dependencies = resolved_dependencies_from_evidence(
        evidence,
        recipes_repository=context.get("repository"),
        recipes_commit=context.get("sha"),
        hub_repository=hub_repository,
    )
    return {
        "buildDefinition": {
            "buildType": BIOCHEF_BUILD_TYPE,
            "externalParameters": external_parameters,
            "resolvedDependencies": dependencies,
        },
        "runDetails": {
            "builder": {
                "id": workflow_identity(
                    hub_repository,
                    hub_ref,
                    context.get("server_url"),
                    context.get("workflow_path"),
                )
            },
            "metadata": compact(
                {
                    "invocationId": context.get("invocation_id"),
                    "finishedOn": normalise_slsa_timestamp(
                        evidence.get("generated_at")
                    ),
                }
            ),
            "byproducts": byproducts(bundle_dir, evidence),
        },
    }


def resolved_dependencies_from_evidence(
    evidence,
    *,
    recipes_repository,
    recipes_commit,
    hub_repository,
):
    recipe = evidence.get("recipe") or {}
    hub = evidence.get("hub") or {}
    dependencies = []

    if bool(recipes_repository) != bool(recipes_commit):
        raise ProvenanceError(
            "Recipe repository and commit must be provided together"
        )
    if recipes_repository:
        require_commit(recipes_commit, "recipes repository")
        dependencies.append(
            git_descriptor(
                "biochef-recipes",
                recipes_repository,
                recipes_commit,
            )
        )

    recipe_digest = recipe.get("digest")
    if not recipe.get("path") or not is_sha256_digest(recipe_digest):
        raise ProvenanceError("Build evidence has no recipe path and digest")
    dependencies.append(
        {
            "name": "recipe/biochef.yaml",
            "uri": f"file:{recipe['path']}",
            "digest": {"sha256": recipe_digest[7:]},
        }
    )

    if not hub_repository:
        raise ProvenanceError("BioCHEF Hub repository identity is missing")
    require_commit(hub.get("commit"), "BioCHEF Hub")
    dependencies.append(
        git_descriptor(
            "biochef-hub",
            hub_repository,
            hub["commit"],
        )
    )

    declared = recipe.get("source") or {}
    actual = first_actual_source(evidence)
    source_descriptor = source_dependency(declared, actual)
    if source_descriptor:
        dependencies.append(source_descriptor)

    for runtime, runtime_data in sorted((evidence.get("runtimes") or {}).items()):
        build = runtime_data.get("build") or {}
        image = build.get("builder_image") or {}
        if image.get("image_id"):
            image_digest = image.get("manifest_digest") or image["image_id"]
            dependencies.append(
                {
                    "name": f"{runtime}-builder-image",
                    "uri": image.get("requested_reference")
                    or "docker-image:local",
                    "digest": digest_object(image_digest),
                }
            )
        framework = build.get("framework") or {}
        if framework.get("repo") and framework.get("commit"):
            dependencies.append(
                git_descriptor(
                    "biowasm",
                    framework["repo"],
                    framework["commit"],
                )
            )
        toolchain = build.get("toolchain") or {}
        if toolchain.get("commit"):
            dependencies.append(
                {
                    "name": toolchain.get("name") or f"{runtime}-toolchain",
                    "uri": f"pkg:generic/{quote(toolchain.get('name') or 'toolchain')}@{quote(toolchain.get('version') or toolchain['commit'])}",
                    "digest": {"gitCommit": toolchain["commit"]},
                }
            )
        if toolchain.get("emsdk_commit"):
            dependencies.append(
                git_descriptor(
                    "emsdk",
                    "https://github.com/emscripten-core/emsdk.git",
                    toolchain["emsdk_commit"],
                )
            )
        for dependency in build.get("dependencies") or []:
            descriptor = material_descriptor(dependency)
            if descriptor:
                dependencies.append(descriptor)

    return deduplicate_descriptors(dependencies)


def source_dependency(declared, actual):
    if declared.get("repo") and (actual or {}).get("commit"):
        return git_descriptor(
            "upstream-source",
            (actual or {}).get("repo") or declared["repo"],
            actual["commit"],
        )
    if declared.get("url") and declared.get("sha256"):
        return {
            "name": "upstream-source",
            "uri": declared["url"],
            "digest": {"sha256": declared["sha256"]},
        }
    if (actual or {}).get("tree_digest"):
        return {
            "name": "upstream-source",
            "uri": "file:vendored-source",
            "digest": digest_object(actual["tree_digest"]),
        }
    return None


def material_descriptor(dependency):
    source = dependency.get("source") or {}
    name = dependency.get("name") or "build-dependency"
    if source.get("repo") and source.get("commit"):
        return git_descriptor(name, source["repo"], source["commit"])
    if source.get("tree_digest"):
        return {
            "name": name,
            "uri": f"file:{dependency.get('path') or name}",
            "digest": digest_object(source["tree_digest"]),
        }
    return None


def byproducts(bundle_dir, evidence):
    paths = {"bundle.json", "build-evidence.json", "sbom.cdx.json"}
    for item in (evidence.get("license") or {}).get("files") or []:
        paths.add(item["path"])
    for runtime_data in (evidence.get("runtimes") or {}).values():
        for item in (runtime_data.get("artifacts") or {}).get("files") or []:
            paths.add(item["path"])
    return [
        {
            "name": relative,
            "uri": f"file:{relative}",
            "digest": {
                "sha256": sha256_hex(require_file(bundle_dir, relative))
            },
        }
        for relative in sorted(paths)
    ]


def github_context(workflow_path):
    repository = os.getenv("GITHUB_REPOSITORY")
    sha = os.getenv("GITHUB_SHA")
    ref = os.getenv("GITHUB_REF")
    server = os.getenv("GITHUB_SERVER_URL", "https://github.com")
    if os.getenv("GITHUB_ACTIONS") == "true":
        if not repository or not COMMIT.fullmatch(sha or "") or not ref:
            raise ProvenanceError(
                "GitHub Actions provenance requires repository, SHA, and ref"
            )
    run_id = os.getenv("GITHUB_RUN_ID")
    run_attempt = os.getenv("GITHUB_RUN_ATTEMPT")
    return {
        "repository": repository,
        "sha": sha,
        "ref": ref,
        "server_url": server,
        "workflow_path": workflow_path,
        "invocation_id": (
            f"{server}/{repository}/actions/runs/{run_id}/attempts/{run_attempt}"
            if repository and run_id and run_attempt
            else None
        ),
    }


def workflow_identity(repository, ref, server_url, workflow_path):
    if not repository or not ref or not workflow_path:
        return "local://biochef-hub"
    if ref.startswith("refs/"):
        resolved_ref = ref
    elif COMMIT.fullmatch(ref):
        resolved_ref = ref
    else:
        resolved_ref = f"refs/heads/{ref}"
    return (
        f"{server_url or 'https://github.com'}/{repository}/"
        f".github/workflows/{Path(workflow_path).name}@{resolved_ref}"
    )


def artifact_bundle_dir(registry_path, artifact):
    path = (
        registry_path / artifact["operation_id"] / artifact["version"]
    ).resolve()
    try:
        path.relative_to(registry_path)
    except ValueError as exc:
        raise ProvenanceError("Published artifact path escapes registry") from exc
    if not path.is_dir() or path.is_symlink():
        raise ProvenanceError(f"Bundle directory is missing or unsafe: {path}")
    return path


def first_actual_source(evidence):
    for runtime_data in (evidence.get("runtimes") or {}).values():
        source = (runtime_data.get("build") or {}).get("source") or {}
        actual = source.get("actual") or source
        if isinstance(actual, dict) and actual.get("kind"):
            return actual
    return None


def git_descriptor(name, repository, commit):
    require_commit(commit, name)
    return {
        "name": name,
        "uri": f"git+{repository}@{commit}",
        "digest": {"gitCommit": commit},
    }


def digest_object(value):
    if not is_sha256_digest(value):
        raise ProvenanceError(f"Invalid SHA-256 digest: {value!r}")
    return {"sha256": value[7:]}


def deduplicate_descriptors(descriptors):
    unique = {}
    for descriptor in descriptors:
        key = json.dumps(descriptor, sort_keys=True, separators=(",", ":"))
        unique.setdefault(key, descriptor)
    return list(unique.values())


def normalise_slsa_timestamp(value):
    if not isinstance(value, str):
        raise ProvenanceError("Build evidence has no generation timestamp")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProvenanceError(f"Invalid build timestamp: {value}") from exc
    if timestamp.tzinfo is None:
        raise ProvenanceError("Build timestamp must include a timezone")
    return timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def require_commit(value, label):
    if not COMMIT.fullmatch(str(value or "")):
        raise ProvenanceError(f"{label} has no full Git commit")


def require_file(bundle_dir, relative):
    if (
        not isinstance(relative, str)
        or not relative
        or "\\" in relative
        or Path(relative).is_absolute()
        or ".." in Path(relative).parts
    ):
        raise ProvenanceError(f"Unsafe bundle path: {relative!r}")
    root = Path(bundle_dir).resolve()
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ProvenanceError(f"Bundle path escapes directory: {relative}") from exc
    if path.is_symlink() or not path.is_file():
        raise ProvenanceError(f"Missing or unsafe bundle file: {relative}")
    return path


def read_json(path):
    if Path(path).is_symlink():
        raise ProvenanceError(f"Refusing to read symlinked JSON: {path}")
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProvenanceError(f"Could not read JSON {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise ProvenanceError(f"JSON document must be an object: {path}")
    return document


def git_output(arguments):
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=Path(__file__).resolve().parents[2],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def compact(values):
    return {
        key: value
        for key, value in values.items()
        if value not in (None, "", [], {})
    }
