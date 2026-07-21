import hashlib
import json
import mimetypes
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from cyclonedx.model import ExternalReference, ExternalReferenceType, HashAlgorithm, HashType, Property
from cyclonedx.model.bom import Bom, BomMetaData
from cyclonedx.model.bom_ref import BomRef
from cyclonedx.model.component import Component, ComponentType
from cyclonedx.model.contact import OrganizationalContact, OrganizationalEntity
from cyclonedx.model.dependency import Dependency
from cyclonedx.model.lifecycle import LifecyclePhase, PredefinedLifecycle
from cyclonedx.model.license import DisjunctiveLicense, LicenseAcknowledgement, LicenseExpression
from cyclonedx.model.tool import ToolRepository
from cyclonedx.output.json import JsonV1Dot7
from cyclonedx.schema import SchemaVersion
from cyclonedx.validation.json import JsonStrictValidator
from packageurl import PackageURL
import yaml

from builders.build_inputs import validate_build_inputs
from builders.bundle_evidence import (
    canonical_digest,
    is_safe_relative_path,
    is_sha256_hex,
    sha256_hex,
)
from builders.wasm_link_evidence import validate_wasm_link_evidence


SBOM_FILE_NAME = "sbom.cdx.json"
BIOCHEF_ORG = "BioChef"
BIOCHEF_PROPERTY_PREFIX = "biochef"


class SbomGenerationError(RuntimeError):
    pass


@dataclass
class SbomSummary:
    scanned: int = 0
    written: int = 0
    failures: list[str] = field(default_factory=list)
    outputs: list[Path] = field(default_factory=list)


def generate_sboms(registry_dir: str | Path = "registry", recipes_dir: str | Path = "recipes") -> SbomSummary:
    registry_path = Path(registry_dir).resolve()
    if not registry_path.is_dir():
        raise SbomGenerationError(f"Registry directory does not exist: {registry_path}")

    recipes_path = _resolve_recipes_dir(recipes_dir)
    recipe_index = _index_recipes(recipes_path)

    summary = SbomSummary()
    for bundle_path in sorted(registry_path.glob("*/*/bundle.json")):
        summary.scanned += 1
        try:
            output_path = _generate_bundle_sbom(bundle_path=bundle_path, recipe_index=recipe_index)
            summary.written += 1
            summary.outputs.append(output_path)
        except Exception as exc:
            summary.failures.append(f"{bundle_path}: {exc}")

    if summary.scanned == 0:
        raise SbomGenerationError(f"No bundle.json files found under {registry_path}")

    if summary.failures:
        raise SbomGenerationError(
            "SBOM generation failed:\n" + "\n".join(f"  - {failure}" for failure in summary.failures)
        )

    return summary


def _generate_bundle_sbom(bundle_path: Path, recipe_index: dict[tuple[str, str], dict[str, Any]]) -> Path:
    bundle_dir = bundle_path.parent
    operation_id = bundle_dir.parent.name
    version = bundle_dir.name
    bundle = _read_json(bundle_path)

    if bundle.get("id") != operation_id:
        raise SbomGenerationError(
            f"bundle id {bundle.get('id')!r} does not match registry path {operation_id!r}"
        )

    recipe_info = recipe_index.get((operation_id, version))
    if not recipe_info:
        raise SbomGenerationError(
            f"no recipe metadata found for {operation_id}@{version}; pass --recipes-dir with the matching recipes"
        )

    recipe = recipe_info["recipe"]
    operation = recipe_info["operation"]
    recipe_path = recipe_info["path"]
    recipe_display_path = recipe_info["display_path"]
    _validate_bundle_recipe_alignment(bundle, recipe, operation)
    components: list[Component] = []

    root_ref = str(_bundle_purl(operation_id, version))

    build_evidence_path = bundle_dir / "build-evidence.json"
    if not build_evidence_path.is_file():
        raise SbomGenerationError(f"missing required build evidence: {build_evidence_path}")
    build_evidence = _read_json(build_evidence_path)
    _validate_build_evidence(
        build_evidence,
        bundle_dir,
        bundle,
        operation_id,
        version,
        recipe_path,
        recipe,
        operation,
    )

    license_path = bundle_dir / "LICENSE"
    license_evidence = (build_evidence or {}).get("license") or {}
    license_files = _license_evidence_files(license_evidence)

    root_dependencies: set[str] = set()
    bundle_ref = "file:bundle.json"
    components.append(_file_component(bundle_ref, "bundle.json", bundle_path, "bundle.json", "BioCHEF bundle manifest"))
    root_dependencies.add(bundle_ref)

    has_license_file = license_path.exists()
    root_component = _bundle_component(root_ref, bundle, version, recipe, operation, has_license_file, license_evidence)
    for license_file in license_files:
        relative_path = _safe_bundle_relative_path(license_file.get("path"))
        license_file_path = bundle_dir / relative_path
        license_ref = f"file:{relative_path}"
        components.append(
            _file_component(
                license_ref,
                Path(relative_path).name,
                license_file_path,
                relative_path,
                _license_file_description(license_file),
                extra_properties=_license_file_properties(license_evidence, license_file),
            )
        )
        root_dependencies.add(license_ref)

    if recipe_path:
        recipe_ref = "file:recipe/biochef.yaml"
        components.append(_recipe_component(recipe_ref, recipe_path, recipe_display_path, recipe, operation, operation_id))
        root_dependencies.add(recipe_ref)
    else:
        recipe_ref = None

    source = recipe.get("source") or {}

    if source:
        source_ref = "source:upstream"
        components.append(_source_component(source_ref, recipe, source, build_evidence))
        root_dependencies.add(source_ref)
    else:
        source_ref = None

    build_config = recipe.get("build") or {}
    if build_config:
        build_ref = "build:recipe-strategy"
        components.append(_build_component(build_ref, recipe, build_evidence))
    else:
        build_ref = None

    toolchain_refs = _toolchain_components(build_evidence)
    components.extend(toolchain_refs["components"])

    build_input_refs = _build_input_components(build_evidence)
    components.extend(build_input_refs["components"])

    build_evidence_ref = "file:build-evidence.json"
    components.append(
        _file_component(
            build_evidence_ref,
            "build-evidence.json",
            build_evidence_path,
            "build-evidence.json",
            "BioCHEF build evidence",
        )
    )
    root_dependencies.add(build_evidence_ref)

    runtime_refs = _runtime_components(bundle_dir, bundle)
    components.extend(runtime_refs["components"])
    root_dependencies.update(runtime_refs["refs"])

    runtime_dependency_refs = _runtime_dependency_components(build_evidence)
    components.extend(runtime_dependency_refs["components"])

    dependency_map = {root_ref: root_dependencies}
    for component in components:
        dependency_map.setdefault(str(component.bom_ref), set())
    if build_ref and build_evidence_ref:
        dependency_map[build_ref].add(build_evidence_ref)
    if build_ref:
        dependency_map[build_ref].update(toolchain_refs["refs"])
        dependency_map[build_ref].update(build_input_refs["refs"])
    for runtime_ref in runtime_refs["refs"]:
        if source_ref:
            dependency_map[runtime_ref].add(source_ref)
        if runtime_ref.startswith("file:runtime/wasm/"):
            dependency_map[runtime_ref].update(
                runtime_dependency_refs["refs_by_runtime"].get("wasm", set())
            )
        elif runtime_ref.startswith("file:runtime/native/"):
            dependency_map[runtime_ref].update(
                runtime_dependency_refs["refs_by_runtime"].get("native", set())
            )

    bom = Bom(
        serial_number=uuid.uuid4(),
        metadata=BomMetaData(
            timestamp=datetime.now(timezone.utc),
            tools=ToolRepository(
                components=_tool_components()
            ),
            authors=[OrganizationalContact(name=BIOCHEF_ORG)],
            supplier=OrganizationalEntity(name=BIOCHEF_ORG),
            lifecycles=[PredefinedLifecycle(phase=LifecyclePhase.POST_BUILD)],
            component=root_component,
            properties=_metadata_properties(),
        ),
        components=components,
        dependencies=_dependencies_from_map(dependency_map),
    )
    output = JsonV1Dot7(bom).output_as_string(indent=2) + "\n"
    output = _normalize_output_json(
        output,
        root_ref,
        formulation_refs={
            *toolchain_refs["refs"],
            *build_input_refs["refs"],
            *({build_ref} if build_ref else set()),
        },
        composition_complete=runtime_dependency_refs["complete"],
    )

    validation_errors = JsonStrictValidator(SchemaVersion.V1_7).validate_str(output, all_errors=True)
    if validation_errors:
        raise SbomGenerationError(
            "CycloneDX validation failed:\n"
            + "\n".join(f"  - {error}" for error in validation_errors)
        )

    output_path = bundle_dir / SBOM_FILE_NAME
    if output_path.is_symlink():
        raise SbomGenerationError(f"refusing to overwrite symlinked SBOM output: {output_path}")
    output_path.write_text(output, encoding="utf-8")
    return output_path


def _bundle_component(root_ref: str, bundle: dict[str, Any], version: str, recipe: dict[str, Any], operation: dict[str, Any], has_license_file: bool, license_evidence: dict[str, Any]) -> Component:
    license_id = ((recipe.get("license") or {}).get("spdx") or "").strip()
    licenses = []
    if license_id:
        licenses.append(_cyclonedx_license(license_id))
    external_references = []
    if has_license_file:
        external_references.append(ExternalReference(type=ExternalReferenceType.LICENSE, url="file:LICENSE"))
    return Component(
        type=ComponentType.APPLICATION,
        bom_ref=BomRef(root_ref),
        name=bundle["id"],
        version=version,
        description=bundle.get("description", ""),
        purl=_bundle_purl(bundle["id"], version),
        licenses=licenses,
        external_references=external_references,
        properties=_properties(
            {
                "biochef.operation.id": bundle.get("id"),
                "biochef.operation.name": bundle.get("name"),
                "biochef.operation.category": bundle.get("category"),
                "biochef.operation.bin": bundle.get("bin"),
                "biochef.recipe.suite.id": recipe.get("id"),
                "biochef.recipe.status": recipe.get("status"),
                "biochef.recipe.operation.digest": canonical_digest(operation) if operation else None,
                "biochef.runtime.modes": _json_value((bundle.get("runtime") or {}).get("modes", [])),
                "biochef.license.evidence.available": bool(license_evidence.get("available")),
                "biochef.license.evidence.exact": bool(license_evidence.get("exact")),
                "biochef.license.evidence.verified": bool(
                    license_evidence.get("verified", license_evidence.get("exact"))
                ),
                "biochef.license.evidence.file_count": len(_license_evidence_files(license_evidence)),
                "biochef.license.evidence.reason": license_evidence.get("reason"),
            }
        ),
    )


def _bundle_purl(operation_id: str, version: str) -> PackageURL:
    return PackageURL(type="generic", namespace="biochef/bundle", name=operation_id, version=version)


def _recipe_component(ref: str, recipe_path: Path, recipe_display_path: str, recipe: dict[str, Any], operation: dict[str, Any], operation_id: str) -> Component:
    return Component(
        type=ComponentType.FILE,
        bom_ref=BomRef(ref),
        name=recipe_path.name,
        version=str(recipe.get("version", "")),
        description=f"BioCHEF recipe defining {operation_id}",
        hashes=[_sha256_hash(recipe_path)],
        properties=_properties(
            {
                "biochef.recipe.path": recipe_display_path,
                "biochef.recipe.apiVersion": recipe.get("apiVersion"),
                "biochef.recipe.digest": f"sha256:{_sha256_hex(recipe_path)}",
                "biochef.recipe.operation.id": operation_id,
                "biochef.recipe.operation.digest": canonical_digest(operation) if operation else None,
            }
        ),
    )


def _source_component(ref: str, recipe: dict[str, Any], source: dict[str, Any], build_evidence: dict[str, Any]) -> Component:
    commit = source.get("commit")
    source_hash = source.get("sha256")
    resolved_commit = _source_resolved_commit(build_evidence)
    immutable = _source_immutable_identity(source)
    build_evidence_available = True
    toolchain_evidence_available = _toolchain_evidence_available(build_evidence)
    mutation_recorded = _source_mutation_recorded(build_evidence)
    mutation_digest = _source_mutation_digest(build_evidence)
    external_references = []
    if source.get("repo"):
        external_references.append(ExternalReference(type=ExternalReferenceType.VCS, url=source.get("repo")))
    source_distribution = _source_distribution_url(source)
    if source_distribution:
        external_references.append(ExternalReference(type=ExternalReferenceType.SOURCE_DISTRIBUTION, url=source_distribution))
    homepage = recipe.get("homepage")
    if homepage:
        external_references.append(ExternalReference(type=ExternalReferenceType.WEBSITE, url=homepage))
    source_purl = _source_purl(source, resolved_commit)
    return Component(
        type=ComponentType.APPLICATION,
        bom_ref=BomRef(ref),
        name=recipe.get("name", "upstream-source"),
        version=str(source.get("version") or source.get("tag") or commit or "unknown"),
        description="Upstream source declared by the BioCHEF recipe",
        purl=source_purl,
        external_references=external_references,
        properties=_properties(
            {
                "biochef.source.repo": source.get("repo"),
                "biochef.source.url": source.get("url"),
                "biochef.source.version": source.get("version"),
                "biochef.source.tag": source.get("tag"),
                "biochef.source.commit": commit,
                "biochef.source.sha256": source_hash,
                "biochef.source.resolved_commit": resolved_commit,
                "biochef.source.immutable.available": immutable["available"],
                "biochef.source.immutable.kind": immutable["kind"],
                "biochef.source.immutable.reason": immutable["reason"],
                "biochef.build.strategy": _build_strategy(recipe),
                "biochef.build.evidence.available": build_evidence_available,
                "biochef.build.source.mutation.recorded": mutation_recorded,
                "biochef.build.source.mutation.digest": mutation_digest,
                "biochef.build.toolchain.evidence.available": toolchain_evidence_available,
            }
        ),
    )


def _toolchain_components(build_evidence: dict[str, Any]) -> dict[str, Any]:
    components = []
    refs = []
    for runtime, runtime_data in (build_evidence.get("runtimes") or {}).items():
        build = runtime_data.get("build") or {}

        biowasm = build.get("biowasm") or {}
        if biowasm.get("repo"):
            ref = f"toolchain:{runtime}:biowasm"
            component = _git_tool_component(
                ref=ref,
                name="BioWASM",
                description="BioWASM build tooling used to produce BioCHEF runtime artifacts",
                repo=biowasm.get("repo"),
                commit=biowasm.get("resolved_commit"),
                dirty=biowasm.get("dirty"),
                dirty_before_build=biowasm.get("dirty_before_build"),
                dirty_after_build=biowasm.get("dirty_after_build"),
                configuration=biowasm.get("configuration"),
                scripts=biowasm.get("scripts"),
            )
            components.append(component)
            refs.append(ref)

        toolchain = build.get("toolchain") or {}
        emcc_version = _first_line(toolchain.get("emcc_version"))
        emmake_path = _first_line(toolchain.get("emmake_path"))
        if emcc_version or emmake_path:
            ref = f"toolchain:{runtime}:emscripten"
            components.append(
                Component(
                    type=ComponentType.FRAMEWORK,
                    bom_ref=BomRef(ref),
                    name="Emscripten",
                    description="Emscripten compiler/toolchain evidence used by the BioCHEF build",
                    version=emcc_version,
                    external_references=[ExternalReference(type=ExternalReferenceType.WEBSITE, url="https://emscripten.org")],
                    properties=_properties(
                        {
                            "biochef.toolchain.kind": "compiler",
                            "biochef.toolchain.runtime": runtime,
                            "biochef.toolchain.emcc.version": emcc_version,
                            "biochef.toolchain.emsdk.version": toolchain.get("emsdk_version"),
                            "biochef.toolchain.emsdk.requested_commit": toolchain.get("emsdk_requested_commit"),
                            "biochef.toolchain.emsdk.resolved_commit": toolchain.get("emsdk_resolved_commit"),
                        }
                    ),
                )
            )
            refs.append(ref)

        make_version = _first_line(toolchain.get("make_version"))
        if make_version:
            ref = f"toolchain:{runtime}:make"
            components.append(
                Component(
                    type=ComponentType.FRAMEWORK,
                    bom_ref=BomRef(ref),
                    name="make",
                    description="Make build tool evidence used by the BioCHEF build",
                    version=make_version,
                    properties=_properties(
                        {
                            "biochef.toolchain.kind": "build-tool",
                            "biochef.toolchain.runtime": runtime,
                            "biochef.toolchain.make.version": make_version,
                        }
                    ),
                )
            )
            refs.append(ref)

    return {"components": components, "refs": refs}


def _build_input_components(build_evidence: dict[str, Any]) -> dict[str, Any]:
    components = []
    refs = []
    for runtime, runtime_data in (build_evidence.get("runtimes") or {}).items():
        build = runtime_data.get("build") or {}
        build_inputs = build.get("build_inputs") or {}
        for submodule in build_inputs.get("git_submodules") or []:
            path = submodule["path"]
            repo = submodule["repo"]
            commit = submodule["resolved_commit"]
            ref = f"build-input:{runtime}:git-submodule:{path}"
            components.append(
                Component(
                    type=ComponentType.LIBRARY,
                    bom_ref=BomRef(ref),
                    name=_repo_name(urlparse(repo).path) or Path(path).name,
                    version=commit,
                    description=(
                        "Fetched recursive Git submodule observed during the build; "
                        "runtime linkage is not asserted"
                    ),
                    purl=_source_purl({"repo": repo, "commit": commit}, commit),
                    hashes=[
                        HashType(
                            alg=HashAlgorithm.SHA_256,
                            content=submodule["post_build_tree_sha256"],
                        )
                    ],
                    external_references=[
                        ExternalReference(type=ExternalReferenceType.VCS, url=repo)
                    ],
                    properties=_properties(
                        {
                            "biochef.build.input.kind": "git-submodule",
                            "biochef.build.input.runtime": runtime,
                            "biochef.build.input.path": path,
                            "biochef.build.input.gitlink_match": submodule.get("gitlink_match"),
                            "biochef.build.input.post_build_dirty": submodule.get("post_build_dirty"),
                            "biochef.build.input.post_build_tree_sha256": submodule.get("post_build_tree_sha256"),
                            "biochef.build.input.post_build_tree_entries": submodule.get("post_build_tree_entries"),
                            "biochef.build.input.tree_digest_algorithm": "sha256(canonical-json(path,kind,mode,file-sha256-or-link-target))",
                            "biochef.build.input.runtime_inclusion": "not-asserted",
                        }
                    ),
                )
            )
            refs.append(ref)

    return {"components": components, "refs": refs}


def _git_tool_component(ref: str, name: str, description: str, repo: str, commit: str | None, dirty: bool | None, dirty_before_build: bool | None, dirty_after_build: bool | None, configuration: dict[str, Any] | None, scripts: list[dict[str, Any]] | None) -> Component:
    purl = _github_purl(repo, commit)
    script_digests = [
        f"{script.get('path')}={script.get('sha256')}"
        for script in (scripts or [])
        if script.get("path") and script.get("sha256")
    ]
    return Component(
        type=ComponentType.FRAMEWORK,
        bom_ref=BomRef(ref),
        name=name,
        version=commit,
        description=description,
        purl=purl,
        external_references=[ExternalReference(type=ExternalReferenceType.VCS, url=repo)],
        properties=_properties(
            {
                "biochef.toolchain.kind": "build-framework",
                "biochef.toolchain.repo": repo,
                "biochef.toolchain.resolved_commit": commit,
                "biochef.toolchain.dirty": dirty,
                "biochef.toolchain.dirty_before_build": dirty_before_build,
                "biochef.toolchain.dirty_after_build": dirty_after_build,
                "biochef.toolchain.biowasm.config.digest": (configuration or {}).get("sha256"),
                "biochef.toolchain.biowasm.selected_tool.digest": (configuration or {}).get("selected_tool_digest"),
                "biochef.toolchain.biowasm.selected_version.digest": (configuration or {}).get("selected_version_digest"),
                "biochef.toolchain.biowasm.scripts": script_digests,
            }
        ),
    )


def _build_component(ref: str, recipe: dict[str, Any], build_evidence: dict[str, Any]) -> Component:
    build_config = recipe.get("build") or {}
    return Component(
        type=ComponentType.FRAMEWORK,
        bom_ref=BomRef(ref),
        name="BioCHEF recipe build strategy",
        description="Build configuration declared by the BioCHEF recipe",
        properties=_properties(
            {
                "biochef.build.strategy": _build_strategy(recipe),
                "biochef.build.config.digest": canonical_digest(build_config),
                "biochef.build.evidence.digest": canonical_digest(build_evidence),
            }
        ),
    )


def _runtime_components(bundle_dir: Path, bundle: dict[str, Any]) -> dict[str, Any]:
    runtime = bundle.get("runtime") or {}
    bin_name = bundle.get("bin")
    if not bin_name:
        raise SbomGenerationError("bundle is missing required bin field")
    bin_name = _safe_path_segment(bin_name, "bundle bin")

    components = []
    refs = []

    for entry in _wasm_runtime_files(bundle, bin_name):
        relative_path = entry["path"]
        path = bundle_dir / relative_path
        expected_digest = entry["digest"]
        kind = path.suffix.removeprefix(".")
        ref = f"file:{relative_path}"
        components.append(
            _runtime_file_component(ref, path, expected_digest, relative_path, f"BioCHEF WASM runtime {kind} artifact")
        )
        refs.append(ref)

    native = runtime.get("native")
    if native:
        path = bundle_dir / "runtime" / "native" / bin_name
        relative_path = f"runtime/native/{bin_name}"
        ref = f"file:{relative_path}"
        components.append(_runtime_file_component(ref, path, native.get("digest"), relative_path, "BioCHEF native runtime artifact"))
        refs.append(ref)

    return {"components": components, "refs": refs}


def _runtime_dependency_components(build_evidence: dict[str, Any]) -> dict[str, Any]:
    components = []
    refs = set()
    refs_by_runtime = {}
    complete = True
    saw_runtime = False

    for runtime, runtime_data in (build_evidence.get("runtimes") or {}).items():
        saw_runtime = True
        build = runtime_data.get("build") or {}
        if runtime == "native":
            complete = False
            continue
        if runtime != "wasm":
            complete = False
            continue

        link = build.get("wasm_link") or {}
        outputs = link.get("outputs") or []
        if (
            validate_wasm_link_evidence(link)
            or len(outputs) != 1
        ):
            complete = False
            continue

        output = outputs[0]
        for archive in output.get("selected_archives") or []:
            identity = archive.get("identity") or {}
            if identity.get("kind") == "primary-source":
                continue
            component, ref = _linked_archive_component(runtime, archive)
            if ref not in refs:
                components.append(component)
                refs.add(ref)
            refs_by_runtime.setdefault(runtime, set()).add(ref)

        linked_inputs = {}
        for item in output.get("direct_inputs") or []:
            identity = item.get("identity") or {}
            if identity.get("kind") in {"primary-source", "emscripten-generated"}:
                continue
            key = json.dumps(identity, sort_keys=True, separators=(",", ":"))
            linked_inputs.setdefault(key, {"identity": identity, "inputs": []})["inputs"].append(
                {"path": item.get("path"), "sha256": item.get("sha256")}
            )
        for group in linked_inputs.values():
            component, ref = _linked_identity_component(runtime, group)
            if ref not in refs:
                components.append(component)
                refs.add(ref)
            refs_by_runtime.setdefault(runtime, set()).add(ref)

        toolchain = build.get("toolchain") or {}
        for library in output.get("javascript_libraries", link.get("javascript_libraries")) or []:
            component, ref = _emscripten_javascript_component(runtime, library, toolchain)
            if ref not in refs:
                components.append(component)
                refs.add(ref)
            refs_by_runtime.setdefault(runtime, set()).add(ref)

    return {
        "components": components,
        "refs": refs,
        "refs_by_runtime": refs_by_runtime,
        "complete": saw_runtime and complete,
    }


def _linked_archive_component(runtime: str, archive: dict[str, Any]) -> tuple[Component, str]:
    path = archive.get("path") or "unknown.a"
    identity = archive.get("identity") or {}
    name = identity.get("name") or path.rsplit("/", 1)[-1]
    ref = _runtime_dependency_ref(runtime, f"archive:{path}")
    repo = identity.get("repo")
    commit = identity.get("commit")
    source_url = identity.get("source_url")
    hashes = []
    if archive.get("sha256"):
        hashes.append(
            HashType(
                alg=HashAlgorithm.SHA_256,
                content=_digest_hex(archive["sha256"]),
            )
        )
    return (
        Component(
            type=ComponentType.LIBRARY,
            bom_ref=BomRef(ref),
            name=name,
            version=str(_linked_identity_version(identity) or "unknown"),
            description="Static library selected into the BioCHEF WebAssembly runtime",
            hashes=hashes,
            purl=_linked_identity_purl(identity, name),
            external_references=_linked_identity_external_references(identity),
            properties=_properties(
                {
                    "biochef.runtime.dependency.runtime": runtime,
                    "biochef.runtime.dependency.kind": identity.get("kind"),
                    "biochef.runtime.dependency.path": path,
                    "biochef.runtime.dependency.repo": repo,
                    "biochef.runtime.dependency.commit": commit,
                    "biochef.runtime.dependency.source_url": source_url,
                    "biochef.runtime.dependency.source_sha256": identity.get(
                        "source_sha256"
                    ),
                    "biochef.runtime.dependency.source_sha512": identity.get(
                        "source_sha512"
                    ),
                    "biochef.runtime.dependency.source_archive_sha512": identity.get(
                        "source_archive_sha512"
                    ),
                    "biochef.runtime.dependency.source_archive_verified": (
                        identity.get("source_archive_sha512")
                        == identity.get("source_sha512")
                        if identity.get("source_sha512")
                        else None
                    ),
                    "biochef.runtime.dependency.emsdk.version": identity.get(
                        "emsdk_version"
                    ),
                    "biochef.runtime.dependency.emsdk.commit": identity.get(
                        "emsdk_commit"
                    ),
                    "biochef.runtime.dependency.acquisition.repo": identity.get(
                        "acquisition_repo"
                    ),
                    "biochef.runtime.dependency.acquisition.commit": identity.get(
                        "acquisition_commit"
                    ),
                    "biochef.runtime.dependency.acquisition.file": identity.get(
                        "acquisition_file"
                    ),
                    "biochef.runtime.dependency.acquisition.file_sha256": identity.get(
                        "acquisition_file_sha256"
                    ),
                    "biochef.runtime.dependency.selected_member_count": archive.get(
                        "selected_member_count"
                    ),
                    "biochef.runtime.dependency.selected_members_digest": archive.get(
                        "selected_members_digest"
                    ),
                }
            ),
        ),
        ref,
    )


def _linked_identity_component(runtime: str, group: dict[str, Any]) -> tuple[Component, str]:
    identity = group["identity"]
    inputs = sorted(group["inputs"], key=lambda item: item.get("path") or "")
    repo = identity.get("repo")
    commit = identity.get("commit")
    name = (
        _repo_name(urlparse(repo).path)
        if repo
        else identity.get("name") or identity.get("kind", "linked-source")
    )
    ref = _runtime_dependency_ref(
        runtime,
        f"identity:{json.dumps(identity, sort_keys=True, separators=(',', ':'))}",
    )
    return (
        Component(
            type=ComponentType.LIBRARY,
            bom_ref=BomRef(ref),
            name=name or "linked-source",
            version=str(_linked_identity_version(identity) or "unknown"),
            description="Source identity with objects selected into the BioCHEF WebAssembly runtime",
            purl=_linked_identity_purl(identity, name or "linked-source"),
            external_references=_linked_identity_external_references(identity),
            properties=_properties(
                {
                    "biochef.runtime.dependency.runtime": runtime,
                    "biochef.runtime.dependency.kind": identity.get("kind"),
                    "biochef.runtime.dependency.repo": repo,
                    "biochef.runtime.dependency.commit": commit,
                    "biochef.runtime.dependency.selected_input_count": len(inputs),
                    "biochef.runtime.dependency.selected_inputs_digest": canonical_digest(inputs),
                }
            ),
        ),
        ref,
    )


def _emscripten_javascript_component(
    runtime: str,
    library: str,
    toolchain: dict[str, Any],
) -> tuple[Component, str]:
    name = str(library)
    version = toolchain.get("emsdk_version") or toolchain.get("emsdk_resolved_commit")
    ref = _runtime_dependency_ref(runtime, f"emscripten-js:{name}")
    return (
        Component(
            type=ComponentType.LIBRARY,
            bom_ref=BomRef(ref),
            name=name,
            version=str(version or "unknown"),
            description="Emscripten JavaScript library selected into the generated runtime glue",
            purl=PackageURL(
                type="generic",
                namespace="emscripten/runtime",
                name=name,
                version=str(version) if version else None,
            ),
            properties=_properties(
                {
                    "biochef.runtime.dependency.runtime": runtime,
                    "biochef.runtime.dependency.kind": "emscripten-javascript-library",
                    "biochef.runtime.dependency.emsdk.commit": toolchain.get(
                        "emsdk_resolved_commit"
                    ),
                }
            ),
        ),
        ref,
    )


def _linked_identity_purl(identity: dict[str, Any], name: str) -> PackageURL | None:
    repo = identity.get("repo")
    commit = identity.get("commit")
    if identity.get("kind") == "downloaded-source":
        return PackageURL(
            type="generic",
            namespace="downloaded-source",
            name=identity.get("name") or name,
            version=str(identity["version"]) if identity.get("version") else None,
        )
    if repo:
        return _source_purl({"repo": repo, "commit": commit}, commit)
    version = identity.get("version") or commit
    if identity.get("kind") == "emscripten-port":
        source_repo = _github_archive_repo(identity.get("source_url"))
        if source_repo:
            return _github_purl(source_repo, str(version) if version else None)
        return PackageURL(
            type="generic",
            namespace="emscripten/port",
            name=identity.get("name") or name,
            version=str(version) if version else None,
        )
    if identity.get("kind") == "emscripten-sdk":
        return PackageURL(
            type="generic",
            namespace="emscripten/runtime",
            name=name,
            version=str(version) if version else None,
        )
    return None


def _linked_identity_version(identity: dict[str, Any]) -> str | None:
    if identity.get("kind") in {
        "downloaded-source",
        "emscripten-sdk",
        "emscripten-port",
    }:
        return identity.get("version")
    return identity.get("commit") or identity.get("version")


def _linked_identity_external_references(
    identity: dict[str, Any],
) -> list[ExternalReference]:
    if identity.get("kind") == "downloaded-source" and identity.get("source_url"):
        return [
            ExternalReference(
                type=ExternalReferenceType.SOURCE_DISTRIBUTION,
                url=identity["source_url"],
            )
        ]
    if identity.get("repo"):
        return [ExternalReference(type=ExternalReferenceType.VCS, url=identity["repo"])]
    if identity.get("source_url"):
        return [
            ExternalReference(
                type=ExternalReferenceType.SOURCE_DISTRIBUTION,
                url=identity["source_url"],
            )
        ]
    return []


def _github_archive_repo(source_url: str | None) -> str | None:
    parsed = urlparse(source_url or "")
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if parsed.netloc not in {"github.com", "www.github.com"} or len(parts) < 2:
        return None
    return f"https://github.com/{parts[0]}/{_strip_git_suffix(parts[1])}"


def _runtime_dependency_ref(runtime: str, identity: str) -> str:
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return f"runtime-dependency:{runtime}:{digest}"


def _runtime_file_component(ref: str, path: Path, expected_digest: str | None, relative_path: str, description: str) -> Component:
    if not path.is_file():
        raise SbomGenerationError(f"missing runtime artifact: {path}")

    actual_hash = _sha256_hex(path)
    expected_hash = _digest_hex(expected_digest)
    if expected_hash and actual_hash != expected_hash:
        raise SbomGenerationError(
            f"digest mismatch for {path}: bundle has sha256:{expected_hash}, file has sha256:{actual_hash}"
        )
    if not expected_hash:
        raise SbomGenerationError(f"missing expected digest for runtime artifact: {path}")

    return _file_component(ref, path.name, path, relative_path, description)


def _safe_path_segment(value: str, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise SbomGenerationError(f"{label} must be a non-empty string")
    if value in {".", ".."} or "/" in value or "\\" in value:
        raise SbomGenerationError(f"{label} must be a safe path segment: {value!r}")
    return value


def _validate_bundle_recipe_alignment(
    bundle: dict[str, Any],
    recipe: dict[str, Any],
    operation: dict[str, Any],
) -> None:
    bundle_operation = {
        key: value
        for key, value in bundle.items()
        if key not in {"runtime", "version"}
    }
    if bundle_operation != operation:
        raise SbomGenerationError(
            "bundle operation metadata does not match the selected recipe operation"
        )

    runtime = bundle.get("runtime")
    if not isinstance(runtime, dict):
        raise SbomGenerationError("bundle runtime must be an object")

    expected_modes = (recipe.get("runtime") or {}).get("modes")
    if runtime.get("modes") != expected_modes:
        raise SbomGenerationError("bundle runtime modes do not match the recipe")

    expected_runtimes = set((recipe.get("build") or {}).keys())
    bundle_runtimes = set(runtime) - {"modes"}
    if bundle_runtimes != expected_runtimes:
        raise SbomGenerationError(
            "bundle runtime artifacts do not match the recipe build runtimes"
        )
    for runtime_name in bundle_runtimes:
        if not isinstance(runtime.get(runtime_name), dict) or not runtime[runtime_name]:
            raise SbomGenerationError(
                f"bundle runtime {runtime_name!r} must contain artifact evidence"
            )


def _validate_build_evidence(
    evidence: dict[str, Any],
    bundle_dir: Path,
    bundle: dict[str, Any],
    operation_id: str,
    version: str,
    recipe_path: Path,
    recipe: dict[str, Any],
    operation: dict[str, Any],
) -> None:
    if evidence.get("schema") != "biochef.build-evidence.v1":
        raise SbomGenerationError("build evidence has an unsupported schema")

    if bundle.get("version") != version:
        raise SbomGenerationError(
            f"bundle version {bundle.get('version')!r} does not match registry version {version!r}"
        )

    evidence_operation = evidence.get("operation") or {}
    if evidence_operation.get("id") != operation_id:
        raise SbomGenerationError(
            f"build evidence operation id {evidence_operation.get('id')!r} does not match {operation_id!r}"
        )
    if evidence_operation.get("bin") != operation.get("bin"):
        raise SbomGenerationError("build evidence operation bin does not match the recipe operation")
    if evidence_operation.get("digest") != canonical_digest(operation):
        raise SbomGenerationError("build evidence operation digest does not match the recipe operation")

    evidence_recipe = evidence.get("recipe") or {}
    if evidence_recipe.get("version") != version:
        raise SbomGenerationError(
            f"build evidence recipe version {evidence_recipe.get('version')!r} does not match bundle version {version!r}"
        )
    if evidence_recipe.get("digest") != f"sha256:{_sha256_hex(recipe_path)}":
        raise SbomGenerationError("build evidence recipe digest does not match recipe file")

    license_evidence = evidence.get("license") or {}
    if license_evidence.get("available"):
        license_files = _license_evidence_files(license_evidence)
        if not license_files:
            raise SbomGenerationError("build evidence says license evidence is available, but no license evidence files are recorded")
        for license_file in license_files:
            evidence_path = license_file.get("path")
            evidence_digest = license_file.get("digest")
            if not evidence_path or not evidence_digest:
                raise SbomGenerationError("build evidence has an incomplete license evidence file entry")
            safe_path = _safe_bundle_relative_path(evidence_path)
            file_path = bundle_dir / safe_path
            if not file_path.is_file():
                raise SbomGenerationError(f"build evidence references missing license evidence file: {evidence_path}")
            if _digest_hex(evidence_digest) != _sha256_hex(file_path):
                raise SbomGenerationError(
                    f"build evidence license digest mismatch for {evidence_path}: evidence has {evidence_digest}, file has sha256:{_sha256_hex(file_path)}"
                )

    evidence_runtimes = evidence.get("runtimes")
    if not isinstance(evidence_runtimes, dict):
        raise SbomGenerationError("build evidence runtimes must be an object")
    expected_runtimes = set((recipe.get("build") or {}).keys())
    if set(evidence_runtimes) != expected_runtimes:
        raise SbomGenerationError(
            "build evidence runtimes do not match the recipe build runtimes"
        )

    expected_runtime_digests = _runtime_digest_map(bundle)
    evidence_runtime_digests = {}
    for runtime_name, runtime_data in evidence_runtimes.items():
        build = runtime_data.get("build") or {}
        build_input_errors = validate_build_inputs(
            build.get("build_inputs")
        )
        if build_input_errors:
            raise SbomGenerationError(
                f"build input evidence for runtime {runtime_name} is invalid:\n"
                + "\n".join(f"  - {error}" for error in build_input_errors)
            )
        wasm_link = build.get("wasm_link")
        if runtime_name == "wasm" and isinstance(wasm_link, dict) and wasm_link.get(
            "complete"
        ):
            link_errors = validate_wasm_link_evidence(wasm_link)
            if link_errors:
                raise SbomGenerationError(
                    "complete WebAssembly linker evidence is invalid:\n"
                    + "\n".join(f"  - {error}" for error in link_errors)
                )
        for artifact in ((runtime_data.get("artifacts") or {}).get("files") or []):
            artifact_path = artifact.get("path")
            artifact_digest = artifact.get("digest")
            if not artifact_path or not artifact_digest:
                raise SbomGenerationError(f"build evidence runtime {runtime_name} has incomplete artifact entry")
            safe_path = _safe_bundle_relative_path(artifact_path)
            if safe_path in evidence_runtime_digests:
                raise SbomGenerationError(f"build evidence has duplicate runtime artifact: {artifact_path}")
            evidence_runtime_digests[safe_path] = artifact_digest
            file_path = bundle_dir / safe_path
            if not file_path.is_file():
                raise SbomGenerationError(f"build evidence references missing artifact: {artifact_path}")
            actual_digest = _sha256_hex(file_path)
            if _digest_hex(artifact_digest) != actual_digest:
                raise SbomGenerationError(
                    f"build evidence digest mismatch for {artifact_path}: evidence has {artifact_digest}, file has sha256:{actual_digest}"
                )
            bundle_digest = expected_runtime_digests.get(safe_path)
            if bundle_digest and _digest_hex(bundle_digest) != actual_digest:
                raise SbomGenerationError(
                    f"build evidence digest for {artifact_path} does not match bundle.json digest"
                )
    if {
        path: _digest_hex(digest)
        for path, digest in evidence_runtime_digests.items()
    } != {
        path: _digest_hex(digest)
        for path, digest in expected_runtime_digests.items()
    }:
        raise SbomGenerationError("bundle.json and build evidence runtime inventories do not match")

    wasm_runtime = ((evidence.get("runtimes") or {}).get("wasm") or {})
    wasm_link = (wasm_runtime.get("build") or {}).get("wasm_link") or {}
    wasm_outputs = wasm_link.get("outputs") or []
    if wasm_link.get("complete"):
        if len(wasm_outputs) != 1:
            raise SbomGenerationError("complete WebAssembly linker evidence must contain exactly one operation output")
        linked_artifacts = {
            f"runtime/wasm/{item.get('name')}": item.get("digest")
            for item in wasm_outputs[0].get("artifacts") or []
            if item.get("name") and item.get("digest")
        }
        expected_wasm_artifacts = {
            path: digest
            for path, digest in evidence_runtime_digests.items()
            if path.startswith("runtime/wasm/")
        }
        if linked_artifacts != expected_wasm_artifacts:
            raise SbomGenerationError(
                "WebAssembly linker evidence artifact digests do not match the bundled runtime artifacts"
            )


def _runtime_digest_map(bundle: dict[str, Any]) -> dict[str, str]:
    runtime = bundle.get("runtime") or {}
    bin_name = bundle.get("bin")
    if not bin_name:
        return {}
    bin_name = _safe_path_segment(bin_name, "bundle bin")
    digests = {}
    for entry in _wasm_runtime_files(bundle, bin_name):
        digests[entry["path"]] = entry["digest"]
    if runtime.get("native"):
        digests[f"runtime/native/{bin_name}"] = runtime["native"].get("digest")
    return {path: digest for path, digest in digests.items() if digest}


def _wasm_runtime_files(bundle: dict[str, Any], bin_name: str) -> list[dict[str, str]]:
    runtime = bundle.get("runtime") or {}
    if "wasm" not in runtime:
        return []

    wasm = runtime.get("wasm")
    if not isinstance(wasm, dict) or not wasm:
        raise SbomGenerationError("bundle WASM runtime evidence must be an object")

    expected = {
        f"runtime/wasm/{bin_name}.js": wasm.get("js_digest"),
        f"runtime/wasm/{bin_name}.wasm": wasm.get("wasm_digest"),
    }
    for path, digest in expected.items():
        if not _digest_hex(digest):
            raise SbomGenerationError(f"bundle WASM runtime is missing a valid digest for {path}")

    runtime_files = wasm.get("files")
    if runtime_files is None:
        runtime_files = [
            {"path": path, "digest": digest}
            for path, digest in expected.items()
        ]
    if not isinstance(runtime_files, list):
        raise SbomGenerationError("bundle WASM runtime files must be a list")

    files_by_path = {}
    for entry in runtime_files:
        if not isinstance(entry, dict):
            raise SbomGenerationError("bundle WASM runtime file entry must be an object")
        path = _safe_bundle_relative_path(entry.get("path"))
        if path in files_by_path:
            raise SbomGenerationError(f"bundle WASM runtime has duplicate artifact: {path}")
        files_by_path[path] = entry.get("digest")

    if set(files_by_path) != set(expected):
        raise SbomGenerationError(
            "bundle WASM runtime must contain exactly its JavaScript and WebAssembly artifacts"
        )
    for path, expected_digest in expected.items():
        if _digest_hex(files_by_path[path]) != _digest_hex(expected_digest):
            raise SbomGenerationError(
                f"bundle WASM runtime file digest does not match {path}"
            )

    return [
        {"path": path, "digest": files_by_path[path]}
        for path in expected
    ]


def _safe_bundle_relative_path(path: str) -> str:
    if not isinstance(path, str) or not path or not is_safe_relative_path(path):
        raise SbomGenerationError(f"unsafe build evidence path: {path!r}")
    candidate = Path(path)
    if candidate.as_posix() == ".":
        raise SbomGenerationError(f"unsafe build evidence path: {path}")
    return candidate.as_posix()


def _file_component(ref: str, name: str, path: Path, relative_path: str, description: str, extra_properties: dict[str, Any] | None = None) -> Component:
    _ensure_regular_file(path, description)
    properties = {
        "biochef.file.path": relative_path,
        "biochef.file.size": str(path.stat().st_size),
    }
    if extra_properties:
        properties.update(extra_properties)
    return Component(
        type=ComponentType.FILE,
        bom_ref=BomRef(ref),
        name=name,
        description=description,
        mime_type=_mime_type(path),
        hashes=[_sha256_hash(path)],
        properties=_properties(properties),
    )


def _metadata_properties() -> list[Property]:
    return _properties(
        {
            "biochef.sbom.kind": "bundle",
            "biochef.sbom.lifecycle": "post-build",
            "biochef.sbom.recipe.metadata.available": True,
            "biochef.sbom.build.evidence.available": True,
        }
    )


def _index_recipes(recipes_dir: Path) -> dict[tuple[str, str], dict[str, Any]]:
    if not recipes_dir.is_dir():
        raise SbomGenerationError(f"Recipes directory does not exist: {recipes_dir}, use --recipes-dir to specify a different location")

    index = {}
    for recipe_path in sorted(recipes_dir.rglob("biochef.yaml")):
        recipe = _read_yaml(recipe_path)
        version = str(recipe.get("version", ""))
        display_path = _relative_to(recipe_path, recipes_dir)
        for operation in recipe.get("operations", []):
            operation_id = operation.get("id")
            if operation_id and version:
                key = (operation_id, version)
                if key in index:
                    existing = index[key]["path"]
                    raise SbomGenerationError(f"Duplicate recipe operation/version {operation_id}@{version}: {existing} and {recipe_path}")

                index[(operation_id, version)] = {
                    "path": recipe_path,
                    "display_path": display_path,
                    "recipe": recipe,
                    "operation": operation,
                }
    return index


def _resolve_recipes_dir(recipes_dir: str | Path) -> Path:
    recipes_path = Path(recipes_dir).resolve()
    if not recipes_path.is_dir():
        raise SbomGenerationError(f"Recipes directory does not exist: {recipes_path}; use --recipes-dir to specify a different location")
    return recipes_path


def _build_strategy(recipe: dict[str, Any]) -> str:
    strategies = []
    for runtime, settings in (recipe.get("build") or {}).items():
        strategy = settings.get("strategy", runtime) if isinstance(settings, dict) else runtime
        strategies.append(f"{runtime}:{strategy}")
    return ",".join(strategies)


def _source_immutable_identity(source: dict[str, Any]) -> dict[str, str | bool | None]:
    if source.get("commit"):
        return {"available": True, "kind": "commit", "reason": None}
    if source.get("sha256"):
        return {"available": True, "kind": "sha256", "reason": None}
    return {
        "available": False,
        "kind": "missing",
        "reason": "Recipe source is not pinned to an immutable commit or source hash.",
    }


def _license_evidence_files(license_evidence: dict[str, Any]) -> list[dict[str, Any]]:
    files = list(license_evidence.get("files") or [])
    if not files and license_evidence.get("path"):
        files.append(
            {
                "role": "license",
                "path": license_evidence.get("path"),
                "digest": license_evidence.get("digest"),
                "exact": license_evidence.get("exact"),
                "verified": license_evidence.get("verified", license_evidence.get("exact")),
                "source": license_evidence.get("source"),
            }
        )
    return files


def _license_file_description(license_file: dict[str, Any]) -> str:
    if license_file.get("role") == "source-header":
        return "Tool source/header license evidence"
    return "Tool license file"


def _license_file_properties(license_evidence: dict[str, Any], license_file: dict[str, Any]) -> dict[str, Any]:
    source = license_file.get("source") or {}
    return {
        "biochef.license.evidence.available": bool(license_evidence.get("available")),
        "biochef.license.evidence.role": license_file.get("role"),
        "biochef.license.evidence.exact": bool(license_file.get("exact")),
        "biochef.license.evidence.verified": bool(license_file.get("verified")),
        "biochef.license.evidence.digest": license_file.get("digest"),
        "biochef.license.evidence.spdx": license_evidence.get("spdx"),
        "biochef.license.evidence.source.kind": source.get("kind"),
        "biochef.license.evidence.source.repo": source.get("repo"),
        "biochef.license.evidence.source.ref": source.get("ref"),
        "biochef.license.evidence.source.path": source.get("path"),
        "biochef.license.evidence.source.url": source.get("url"),
        "biochef.license.evidence.source.sha256": source.get("sha256"),
        "biochef.license.evidence.reason": license_evidence.get("reason"),
    }


def _source_resolved_commit(build_evidence: dict[str, Any]) -> str | None:
    commits = []
    for runtime_data in (build_evidence.get("runtimes") or {}).values():
        source = ((runtime_data.get("build") or {}).get("source") or {})
        actual = source.get("actual") or {}
        commit = source.get("resolved_commit") or actual.get("resolved_commit")
        if commit and commit not in commits:
            commits.append(commit)
    if len(commits) == 1:
        return commits[0]
    if len(commits) > 1:
        return ",".join(commits)
    return None


def _source_purl(source: dict[str, Any], resolved_commit: str | None) -> PackageURL | None:
    repo = source.get("repo")
    if not repo:
        return None
    resolved_version = resolved_commit if resolved_commit and "," not in resolved_commit else None
    version = source.get("commit") or resolved_version or source.get("tag") or source.get("version")
    qualifiers = {"vcs_url": _vcs_url(repo, version)}
    github_purl = _github_purl(repo, version, qualifiers=qualifiers)
    if github_purl:
        return github_purl

    parsed = urlparse(repo)
    name = _repo_name(parsed.path)
    if not parsed.netloc or not name:
        return None
    namespace = "/".join(part for part in [parsed.netloc, *_repo_namespace_parts(parsed.path)] if part)
    return PackageURL(
        type="generic",
        namespace=namespace,
        name=name,
        version=version,
        qualifiers=qualifiers,
    )


def _github_purl(repo: str, version: str | None, qualifiers: dict[str, str] | None = None) -> PackageURL | None:
    github = _github_repo_parts(repo)
    if not github:
        return None
    owner, name = github
    return PackageURL(
        type="github",
        namespace=owner,
        name=name,
        version=version,
        qualifiers=qualifiers,
    )


def _github_repo_parts(repo: str) -> tuple[str, str] | None:
    parsed = urlparse(repo)
    if parsed.netloc not in {"github.com", "www.github.com"}:
        return None
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2:
        return None
    return parts[0], _strip_git_suffix(parts[1])


def _source_distribution_url(source: dict[str, Any]) -> str | None:
    if source.get("url"):
        return source.get("url")
    repo = source.get("repo")
    ref = source.get("commit") or source.get("tag")
    github = _github_repo_parts(repo or "")
    if not github or not ref:
        return None
    owner, name = github
    return f"https://github.com/{owner}/{name}/archive/{ref}.tar.gz"


def _vcs_url(repo: str, version: str | None) -> str:
    prefix = repo if repo.startswith("git+") else f"git+{repo}"
    return f"{prefix}@{version}" if version else prefix


def _repo_namespace_parts(path: str) -> list[str]:
    parts = [part for part in path.strip("/").split("/") if part]
    return parts[:-1]


def _repo_name(path: str) -> str | None:
    parts = [part for part in path.strip("/").split("/") if part]
    if not parts:
        return None
    return _strip_git_suffix(parts[-1])


def _strip_git_suffix(value: str) -> str:
    return value[:-4] if value.endswith(".git") else value


def _read_json(path: Path) -> dict[str, Any]:
    _ensure_regular_file(path, "JSON input")
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def _read_yaml(path: Path) -> dict[str, Any]:
    _ensure_regular_file(path, "YAML input")
    with path.open(encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def _sha256_hex(path: Path) -> str:
    _ensure_regular_file(path, "hash input")
    return sha256_hex(path)


def _sha256_hash(path: Path) -> HashType:
    return HashType(alg=HashAlgorithm.SHA_256, content=_sha256_hex(path))


def _digest_hex(value: str | None) -> str | None:
    if not value:
        return None
    if value.startswith("sha256:"):
        value = value.split(":", 1)[1]
    if not is_sha256_hex(value):
        raise SbomGenerationError(f"invalid SHA-256 digest: {value!r}")
    return value.lower()


def _properties(values: dict[str, Any]) -> list[Property]:
    properties = []
    for name, value in values.items():
        if value is None:
            continue
        if name.startswith(f"{BIOCHEF_PROPERTY_PREFIX}."):
            name = name.replace(f"{BIOCHEF_PROPERTY_PREFIX}.", f"{BIOCHEF_PROPERTY_PREFIX}:", 1)
        properties.append(Property(name=name, value=_json_value(value)))
    return properties


def _cyclonedx_license(value: str) -> DisjunctiveLicense | LicenseExpression:
    if any(operator in value for operator in (" AND ", " OR ", " WITH ")):
        return LicenseExpression(value=value, acknowledgement=LicenseAcknowledgement.CONCLUDED)
    return DisjunctiveLicense(id=value, acknowledgement=LicenseAcknowledgement.CONCLUDED)


def _toolchain_evidence_available(build_evidence: dict[str, Any]) -> bool:
    for runtime_data in (build_evidence.get("runtimes") or {}).values():
        build = runtime_data.get("build") or {}
        toolchain = build.get("toolchain") or {}
        biowasm = build.get("biowasm") or {}
        if any(
            (
                toolchain.get("emcc_version"),
                toolchain.get("emsdk_resolved_commit"),
                toolchain.get("make_version"),
                biowasm.get("resolved_commit"),
            )
        ):
            return True
    return False


def _source_mutation_recorded(build_evidence: dict[str, Any]) -> bool | None:
    saw_dirty_source = False
    for runtime_data in (build_evidence.get("runtimes") or {}).values():
        source = ((runtime_data.get("build") or {}).get("source") or {})
        actual = source.get("actual") or {}
        if not actual.get("dirty"):
            continue
        saw_dirty_source = True
        mutation = source.get("mutation") or {}
        if not mutation.get("recorded"):
            return False
    return True if saw_dirty_source else None


def _source_mutation_digest(build_evidence: dict[str, Any]) -> str | None:
    mutations = {}
    for runtime, runtime_data in (build_evidence.get("runtimes") or {}).items():
        source = (runtime_data.get("build") or {}).get("source") or {}
        mutation = source.get("mutation") or {}
        if mutation.get("recorded"):
            mutations[runtime] = mutation
    return canonical_digest(mutations) if mutations else None


def _first_line(value: str | None) -> str | None:
    if not value:
        return None
    return value.splitlines()[0]


def _tool_components() -> list[Component]:
    components = [
        Component(
            type=ComponentType.APPLICATION,
            name="biochef-hub",
            publisher=BIOCHEF_ORG,
            properties=_properties(
                {
                    "biochef.hub.version.available": "false",
                    "biochef.hub.version.reason": "No hub package version is currently declared.",
                }
            ),
        )
    ]
    try:
        cyclonedx_lib_version = package_version("cyclonedx-python-lib")
    except PackageNotFoundError:
        cyclonedx_lib_version = None
    if cyclonedx_lib_version:
        components.append(
            Component(
                type=ComponentType.LIBRARY,
                name="cyclonedx-python-lib",
                version=cyclonedx_lib_version,
                publisher="CycloneDX",
                purl=PackageURL(type="pypi", name="cyclonedx-python-lib", version=cyclonedx_lib_version),
            )
        )
    return components


def _dependencies_from_map(dependency_map: dict[str, set[str]]) -> list[Dependency]:
    dependencies = []
    for ref in sorted(dependency_map):
        depends_on = sorted(dependency_map[ref])
        dependencies.append(
            Dependency(
                ref=BomRef(ref),
                dependencies=[Dependency(ref=BomRef(child_ref)) for child_ref in depends_on],
            )
        )
    return dependencies


def _json_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value, sort_keys=True)
    return str(value)


def _relative_to(path: Path, base: Path) -> str:
    return path.resolve().relative_to(base.resolve()).as_posix()


def _ensure_regular_file(path: Path, description: str) -> None:
    if path.is_symlink():
        raise SbomGenerationError(f"refusing symlinked {description}: {path}")
    if not path.is_file():
        raise SbomGenerationError(f"missing {description}: {path}")


def _mime_type(path: Path) -> str | None:
    if path.suffix == ".wasm":
        return "application/wasm"
    if path.suffix == ".js":
        return "text/javascript"
    if path.name == "LICENSE":
        return "text/plain"
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed


def _normalize_output_json(
    output: str,
    root_ref: str,
    *,
    formulation_refs: set[str],
    composition_complete: bool,
) -> str:
    bom_json = json.loads(output)
    _normalize_mime_type_keys(bom_json)
    formulation_components = []
    runtime_components = []
    for component in bom_json.get("components") or []:
        if component.get("bom-ref") in formulation_refs:
            formulation_components.append(component)
        else:
            runtime_components.append(component)
    bom_json["components"] = runtime_components
    if formulation_components:
        bom_json["formulation"] = [
            {
                "bom-ref": "formulation:biochef-build",
                "components": formulation_components,
            }
        ]

    dependencies = []
    for dependency in bom_json.get("dependencies") or []:
        if dependency.get("ref") in formulation_refs:
            continue
        filtered = [
            ref
            for ref in dependency.get("dependsOn") or []
            if ref not in formulation_refs
        ]
        normalized = {"ref": dependency["ref"]}
        if filtered:
            normalized["dependsOn"] = filtered
        dependencies.append(normalized)
    bom_json["dependencies"] = dependencies
    bom_json["compositions"] = [
        {
            "aggregate": "complete" if composition_complete else "incomplete",
            "assemblies": [root_ref],
        }
    ]
    return json.dumps(bom_json, indent=2) + "\n"


def _normalize_mime_type_keys(value: Any) -> None:
    if isinstance(value, dict):
        if "mimeType" in value:
            value["mime-type"] = value.pop("mimeType")
        for child in value.values():
            _normalize_mime_type_keys(child)
    elif isinstance(value, list):
        for child in value:
            _normalize_mime_type_keys(child)
