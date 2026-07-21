import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from cyclonedx.schema import SchemaVersion
from cyclonedx.validation.json import JsonStrictValidator
from license_expression import ExpressionError, get_spdx_licensing

from builders.build_inputs import validate_build_inputs
from builders.bundle_evidence import canonical_digest, is_safe_relative_path, sha256_hex
from builders.wasm_link_evidence import validate_wasm_link_evidence

SCANCODE_MIN_LICENSE_SCORE = "90"
SCANCODE_TIMEOUT_SECONDS = 60
SPDX_LICENSING = get_spdx_licensing()
LICENSE_SCAN_CACHE: dict[str, str | None] = {}


class SbomCheckError(RuntimeError):
    pass


@dataclass
class CheckIssue:
    bundle: str
    code: str
    message: str


@dataclass
class CheckSummary:
    scanned: int = 0
    failures: list[CheckIssue] = field(default_factory=list)
    warnings: list[CheckIssue] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return bool(self.failures)


def check_registry(registry_dir: str | Path = "registry") -> CheckSummary:
    registry_path = Path(registry_dir).resolve()
    if not registry_path.is_dir():
        raise SbomCheckError(f"Registry directory does not exist: {registry_path}")

    summary = CheckSummary()
    for bundle_path in sorted(registry_path.glob("*/*/bundle.json")):
        summary.scanned += 1
        _check_bundle(bundle_path, summary)

    if summary.scanned == 0:
        raise SbomCheckError(f"No bundle.json files found under {registry_path}")
    return summary


def _check_bundle(bundle_path: Path, summary: CheckSummary) -> None:
    bundle_dir = bundle_path.parent
    bundle_name = f"{bundle_dir.parent.name}@{bundle_dir.name}"
    sbom_path = bundle_dir / "sbom.cdx.json"
    evidence_path = bundle_dir / "build-evidence.json"

    bundle = _read_json(bundle_path, summary, bundle_name, "bundle.json")
    sbom = _read_json(sbom_path, summary, bundle_name, "sbom.cdx.json")
    evidence = _read_json(evidence_path, summary, bundle_name, "build-evidence.json")
    if bundle is None or sbom is None or evidence is None:
        return

    validation_errors = JsonStrictValidator(SchemaVersion.V1_7).validate_str(sbom_path.read_text(encoding="utf-8"), all_errors=True)
    if validation_errors:
        for error in validation_errors:
            _require(summary, bundle_name, False, "SBOM_SCHEMA", f"CycloneDX schema validation failed: {error}")
        return
    if not _check_document_shapes(bundle, evidence, summary, bundle_name):
        return

    component_entries = sbom.get("components") or []
    component_refs = [component.get("bom-ref") for component in component_entries]
    _require(
        summary,
        bundle_name,
        len(component_refs) == len(set(component_refs)),
        "DUPLICATE_COMPONENT_REF",
        "SBOM contains duplicate top-level component bom-ref values",
    )
    components = {component.get("bom-ref"): component for component in component_entries}
    formulation_entries = [
        component
        for formulation in sbom.get("formulation") or []
        for component in formulation.get("components") or []
    ]
    formulation_refs = [component.get("bom-ref") for component in formulation_entries]
    _require(
        summary,
        bundle_name,
        len(formulation_refs) == len(set(formulation_refs)),
        "DUPLICATE_COMPONENT_REF",
        "SBOM contains duplicate formulation component bom-ref values",
    )
    formulation_components = {
        component.get("bom-ref"): component
        for component in formulation_entries
    }
    metadata = sbom.get("metadata") or {}
    root_component = metadata.get("component") or {}
    root_properties = _properties(root_component)
    source_properties = _properties(components.get("source:upstream") or {})

    _require(summary, bundle_name, sbom.get("bomFormat") == "CycloneDX", "SBOM_FORMAT", "SBOM is not CycloneDX")
    _require(summary, bundle_name, sbom.get("specVersion") == "1.7", "SBOM_SPEC", "SBOM is not CycloneDX 1.7")
    _require(
        summary,
        bundle_name,
        root_component.get("name") == bundle_dir.parent.name,
        "ROOT_NAME",
        "SBOM root component does not match registry operation id",
    )
    _require(
        summary,
        bundle_name,
        root_component.get("version") == bundle_dir.name,
        "ROOT_VERSION",
        "SBOM root component does not match registry version",
    )
    _require(
        summary,
        bundle_name,
        bundle.get("id") == bundle_dir.parent.name,
        "BUNDLE_ID",
        "bundle.json operation id does not match registry path",
    )
    _require(
        summary,
        bundle_name,
        bundle.get("version") == bundle_dir.name,
        "BUNDLE_VERSION",
        "bundle.json version does not match registry version",
    )

    compositions = sbom.get("compositions") or []
    root_compositions = [
        composition
        for composition in compositions
        if root_component.get("bom-ref") in (composition.get("assemblies") or [])
    ]
    evidence_complete, link_errors = _runtime_composition_evidence(evidence)
    for error in link_errors:
        _require(
            summary,
            bundle_name,
            False,
            "RUNTIME_LINK_EVIDENCE_INVALID",
            error,
        )
    expected_aggregate = "complete" if evidence_complete else "incomplete"
    matching_composition = next(
        (
            composition
            for composition in root_compositions
            if composition.get("aggregate") == expected_aggregate
        ),
        None,
    )
    _require(
        summary,
        bundle_name,
        len(root_compositions) == 1 and matching_composition is not None,
        "SBOM_COMPLETENESS_MISMATCH",
        f"SBOM must declare the bundle assembly inventory {expected_aggregate} based on runtime evidence",
    )

    for required_ref in (
        "file:bundle.json",
        "file:build-evidence.json",
        "file:recipe/biochef.yaml",
        "source:upstream",
    ):
        _require(
            summary,
            bundle_name,
            required_ref in components,
            "MISSING_COMPONENT",
            f"SBOM is missing required component {required_ref}",
        )
    _require(
        summary,
        bundle_name,
        "build:recipe-strategy" in formulation_components,
        "MISSING_COMPONENT",
        "SBOM formulation is missing required component build:recipe-strategy",
    )

    evidence_operation = evidence.get("operation") or {}
    evidence_recipe = evidence.get("recipe") or {}
    _require(
        summary,
        bundle_name,
        evidence.get("schema") == "biochef.build-evidence.v1",
        "EVIDENCE_SCHEMA",
        "build-evidence.json has unsupported or missing schema",
    )
    _require(
        summary,
        bundle_name,
        evidence_operation.get("id") == bundle_dir.parent.name,
        "EVIDENCE_OPERATION",
        "build evidence operation id does not match registry path",
    )
    _require(
        summary,
        bundle_name,
        evidence_recipe.get("version") == bundle_dir.name,
        "EVIDENCE_VERSION",
        "build evidence recipe version does not match registry path",
    )

    _check_file_hash(bundle_path, components.get("file:bundle.json"), summary, bundle_name, "BUNDLE_HASH")
    _check_file_hash(evidence_path, components.get("file:build-evidence.json"), summary, bundle_name, "EVIDENCE_HASH")
    _check_identity_bindings(components, root_properties, bundle, evidence, summary, bundle_name)
    _check_license_evidence(bundle_dir, components, root_component, root_properties, evidence, summary, bundle_name)
    _check_runtime_artifacts(bundle_dir, components, bundle, evidence, summary, bundle_name)
    _check_build_inputs(formulation_components, evidence, summary, bundle_name)

    _check_source_and_toolchain_evidence(
        formulation_components,
        source_properties,
        evidence,
        summary,
        bundle_name,
    )

    hub = evidence.get("hub")
    _require(
        summary,
        bundle_name,
        isinstance(hub, dict) and _is_git_commit(hub.get("commit")),
        "HUB_IDENTITY_MISSING",
        "build evidence is missing the exact BioCHEF Hub commit",
    )
    _require(
        summary,
        bundle_name,
        isinstance(hub, dict) and hub.get("dirty") is False,
        "HUB_WORKTREE_DIRTY",
        "bundle was built from a dirty BioCHEF Hub worktree or the clean state was not recorded",
    )

    _check_builder_dirty_state(evidence, summary, bundle_name)
    _check_declared_source_resolution(evidence, summary, bundle_name)
    _check_build_source_alignment(evidence, summary, bundle_name)
    _check_dependency_graph(sbom, components, root_component, evidence, summary, bundle_name)


def _check_document_shapes(bundle: dict[str, Any], evidence: dict[str, Any], summary: CheckSummary, bundle_name: str) -> bool:
    valid = True
    expected_objects = (
        (bundle.get("runtime"), "bundle.json runtime"),
        (evidence.get("recipe"), "build evidence recipe"),
        (evidence.get("operation"), "build evidence operation"),
        (evidence.get("license"), "build evidence license"),
        (evidence.get("runtimes"), "build evidence runtimes"),
    )
    for value, label in expected_objects:
        if not isinstance(value, dict):
            valid = False
            _require(summary, bundle_name, False, "INVALID_EVIDENCE_TYPE", f"{label} must be an object")
    if not valid:
        return False
    runtime = bundle.get("runtime") or {}
    if not isinstance(runtime.get("modes"), list):
        valid = False
        _require(summary, bundle_name, False, "INVALID_BUNDLE_TYPE", "bundle.json runtime modes must be a list")
    for runtime_name, runtime_config in runtime.items():
        if runtime_name != "modes" and not isinstance(runtime_config, dict):
            valid = False
            _require(summary, bundle_name, False, "INVALID_BUNDLE_TYPE", f"bundle.json {runtime_name} runtime must be an object")

    recipe_source = (evidence.get("recipe") or {}).get("source")
    if not isinstance(recipe_source, dict):
        valid = False
        _require(summary, bundle_name, False, "INVALID_EVIDENCE_TYPE", "build evidence recipe source must be an object")
    license_files = (evidence.get("license") or {}).get("files")
    if license_files is not None and (not isinstance(license_files, list) or not all(isinstance(item, dict) for item in license_files)):
        valid = False
        _require(summary, bundle_name, False, "INVALID_EVIDENCE_TYPE", "build evidence license files must be a list of objects")

    for runtime_name, runtime_data in evidence["runtimes"].items():
        if not isinstance(runtime_data, dict) or not isinstance(runtime_data.get("build"), dict) or not isinstance(runtime_data.get("artifacts"), dict):
            valid = False
            _require(summary, bundle_name, False, "INVALID_EVIDENCE_TYPE", f"{runtime_name} runtime build and artifacts evidence must be objects")
            continue
        build = runtime_data["build"]
        for field in ("source", "biowasm", "toolchain", "build_inputs"):
            if field in build and not isinstance(build[field], dict):
                valid = False
                _require(summary, bundle_name, False, "INVALID_EVIDENCE_TYPE", f"{runtime_name} build {field} evidence must be an object")
        source = build.get("source") or {}
        for field in ("actual", "declared", "mutation"):
            if field in source and not isinstance(source[field], dict):
                valid = False
                _require(summary, bundle_name, False, "INVALID_EVIDENCE_TYPE", f"{runtime_name} source {field} evidence must be an object")
        files = runtime_data["artifacts"].get("files")
        if not isinstance(files, list):
            valid = False
            _require(summary, bundle_name, False, "INVALID_EVIDENCE_TYPE", f"{runtime_name} artifact files must be a list")
    return valid


def _check_identity_bindings(components: dict[str, dict[str, Any]], root_properties: dict[str, str], bundle: dict[str, Any], evidence: dict[str, Any], summary: CheckSummary, bundle_name: str) -> None:
    operation = evidence.get("operation") or {}
    recipe = evidence.get("recipe") or {}
    recipe_component = components.get("file:recipe/biochef.yaml") or {}
    recipe_properties = _properties(recipe_component)
    operation_digest = operation.get("digest")
    recipe_digest = recipe.get("digest")

    _require(summary, bundle_name, bundle.get("bin") == operation.get("bin"), "OPERATION_BIN_MISMATCH", "bundle.json and build evidence operation binaries do not match")
    _require(summary, bundle_name, root_properties.get("biochef:operation.id") == operation.get("id"), "OPERATION_ID_MISMATCH", "SBOM root operation id does not match build evidence")
    _require(summary, bundle_name, root_properties.get("biochef:operation.bin") == operation.get("bin"), "OPERATION_BIN_MISMATCH", "SBOM root operation binary does not match build evidence")
    _require(summary, bundle_name, _normalize_digest(operation_digest) != "", "OPERATION_DIGEST_MISSING", "build evidence operation digest is missing or invalid")
    _require(summary, bundle_name, root_properties.get("biochef:recipe.operation.digest") == operation_digest, "OPERATION_DIGEST_MISMATCH", "SBOM root operation digest does not match build evidence")
    _require(summary, bundle_name, recipe_properties.get("biochef:recipe.operation.digest") == operation_digest, "OPERATION_DIGEST_MISMATCH", "SBOM recipe component operation digest does not match build evidence")
    _require(summary, bundle_name, _normalize_digest(recipe_digest) != "", "RECIPE_DIGEST_MISSING", "build evidence recipe digest is missing or invalid")
    _require(summary, bundle_name, _component_has_sha256(recipe_component, recipe_digest), "RECIPE_HASH_MISMATCH", "SBOM recipe component hash does not match build evidence")
    _require(summary, bundle_name, recipe_properties.get("biochef:recipe.digest") == recipe_digest, "RECIPE_HASH_MISMATCH", "SBOM recipe digest property does not match build evidence")

    runtime = bundle.get("runtime") or {}
    modes = runtime.get("modes") or []
    try:
        sbom_modes = json.loads(root_properties.get("biochef:runtime.modes", ""))
    except json.JSONDecodeError:
        sbom_modes = None
    _require(summary, bundle_name, isinstance(modes, list) and sbom_modes == modes, "RUNTIME_MODES_MISMATCH", "SBOM runtime modes do not match bundle.json")

    evidence_runtimes = evidence.get("runtimes")
    bundle_runtimes = {key for key in runtime if key != "modes"}
    _require(
        summary,
        bundle_name,
        isinstance(evidence_runtimes, dict) and set(evidence_runtimes) == bundle_runtimes,
        "RUNTIME_EVIDENCE_MISMATCH",
        "build evidence runtimes do not match bundle.json runtime artifacts",
    )


def _check_source_and_toolchain_evidence(formulation_components: dict[str, dict[str, Any]], source_properties: dict[str, str], evidence: dict[str, Any], summary: CheckSummary, bundle_name: str) -> None:
    recipe_source = ((evidence.get("recipe") or {}).get("source") or {})
    source_repo = recipe_source.get("repo")
    source_commit = recipe_source.get("commit")
    source_sha256 = recipe_source.get("sha256")
    immutable_available = _is_git_commit(source_commit) or _normalize_digest(source_sha256) != ""
    _require(summary, bundle_name, immutable_available, "SOURCE_IMMUTABLE_IDENTITY_MISSING", "recipe source is not pinned to an immutable commit or source hash")
    _require(summary, bundle_name, source_properties.get("biochef:source.immutable.available") == str(immutable_available).lower(), "SOURCE_IMMUTABLE_IDENTITY_MISMATCH", "SBOM source immutability property does not match build evidence")
    if source_repo:
        _require(summary, bundle_name, _same_repo(source_properties.get("biochef:source.repo"), source_repo), "SOURCE_IDENTITY_MISMATCH", "SBOM upstream repository does not match build evidence")
    if source_commit:
        _require(summary, bundle_name, source_properties.get("biochef:source.commit") == source_commit, "SOURCE_IDENTITY_MISMATCH", "SBOM upstream commit does not match build evidence")
    if source_sha256:
        _require(summary, bundle_name, _normalize_digest(source_properties.get("biochef:source.sha256")) == _normalize_digest(source_sha256), "SOURCE_IDENTITY_MISMATCH", "SBOM upstream source hash does not match build evidence")

    resolved_commits = {
        actual.get("resolved_commit")
        for runtime_data in (evidence.get("runtimes") or {}).values()
        for actual in [(((runtime_data.get("build") or {}).get("source") or {}).get("actual") or {})]
        if actual.get("kind") == "git" and actual.get("resolved_commit")
    }
    if len(resolved_commits) == 1:
        _require(summary, bundle_name, source_properties.get("biochef:source.resolved_commit") in resolved_commits, "SOURCE_IDENTITY_MISMATCH", "SBOM resolved upstream commit does not match observed build source")

    build_component = formulation_components.get("build:recipe-strategy") or {}
    build_properties = _properties(build_component)
    _require(summary, bundle_name, build_properties.get("biochef:build.evidence.digest") == canonical_digest(evidence), "BUILD_EVIDENCE_DIGEST_MISMATCH", "SBOM build formulation digest does not match build evidence")
    _require(summary, bundle_name, build_properties.get("biochef:build.config.digest") == canonical_digest((evidence.get("recipe") or {}).get("build") or {}), "BUILD_CONFIG_DIGEST_MISMATCH", "SBOM build configuration digest does not match build evidence")

    toolchain_complete = True
    runtimes = evidence.get("runtimes") or {}
    if not isinstance(runtimes, dict) or not runtimes:
        toolchain_complete = False
    else:
        for runtime_name, runtime_data in runtimes.items():
            if not isinstance(runtime_data, dict):
                toolchain_complete = False
                continue
            build = runtime_data.get("build") or {}
            builder = build.get("builder")
            toolchain = build.get("toolchain") or {}
            if builder == "biowasm":
                biowasm = build.get("biowasm") or {}
                biowasm_commit = biowasm.get("resolved_commit")
                valid_biowasm = bool(biowasm.get("repo")) and _is_git_commit(biowasm_commit)
                valid_emscripten = bool(toolchain.get("emcc_version")) and _is_git_commit(toolchain.get("emsdk_resolved_commit"))
                toolchain_complete &= valid_biowasm and valid_emscripten
                _check_git_toolchain_component(formulation_components.get(f"toolchain:{runtime_name}:biowasm"), biowasm.get("repo"), biowasm_commit, summary, bundle_name, f"{runtime_name} BioWASM")
                _check_emscripten_component(formulation_components.get(f"toolchain:{runtime_name}:emscripten"), toolchain, summary, bundle_name, runtime_name)
            elif builder == "emscripten":
                valid_emscripten = bool(toolchain.get("emcc_version")) and _is_git_commit(toolchain.get("emsdk_resolved_commit"))
                toolchain_complete &= valid_emscripten
                _check_emscripten_component(formulation_components.get(f"toolchain:{runtime_name}:emscripten"), toolchain, summary, bundle_name, runtime_name)
            elif builder == "native":
                toolchain_complete &= bool(toolchain.get("make_version"))
                component = formulation_components.get(f"toolchain:{runtime_name}:make") or {}
                _require(summary, bundle_name, component.get("version") == _first_line(toolchain.get("make_version")), "TOOLCHAIN_COMPONENT_MISMATCH", f"SBOM {runtime_name} make component does not match build evidence")
            else:
                toolchain_complete = False

    _require(summary, bundle_name, toolchain_complete, "TOOLCHAIN_EVIDENCE_MISSING", "required build toolchain evidence is missing")
    _require(summary, bundle_name, source_properties.get("biochef:build.toolchain.evidence.available") == str(toolchain_complete).lower(), "TOOLCHAIN_EVIDENCE_MISMATCH", "SBOM toolchain evidence property does not match build evidence")


def _check_git_toolchain_component(component: dict[str, Any] | None, repo: str | None, commit: str | None, summary: CheckSummary, bundle_name: str, label: str) -> None:
    component = component or {}
    _require(summary, bundle_name, component.get("version") == commit, "TOOLCHAIN_COMPONENT_MISMATCH", f"SBOM {label} commit does not match build evidence")
    _require(summary, bundle_name, _same_repo(_external_reference_url(component, "vcs"), repo), "TOOLCHAIN_COMPONENT_MISMATCH", f"SBOM {label} repository does not match build evidence")


def _check_emscripten_component(component: dict[str, Any] | None, toolchain: dict[str, Any], summary: CheckSummary, bundle_name: str, runtime_name: str) -> None:
    component = component or {}
    properties = _properties(component)
    _require(summary, bundle_name, component.get("version") == _first_line(toolchain.get("emcc_version")), "TOOLCHAIN_COMPONENT_MISMATCH", f"SBOM {runtime_name} Emscripten version does not match build evidence")
    _require(summary, bundle_name, properties.get("biochef:toolchain.emsdk.resolved_commit") == toolchain.get("emsdk_resolved_commit"), "TOOLCHAIN_COMPONENT_MISMATCH", f"SBOM {runtime_name} emsdk commit does not match build evidence")


def _check_build_inputs(formulation_components: dict[str, dict[str, Any]], evidence: dict[str, Any], summary: CheckSummary, bundle_name: str) -> None:
    for runtime_name, runtime_data in (evidence.get("runtimes") or {}).items():
        build = runtime_data.get("build") or {}
        build_inputs = build.get("build_inputs")
        for error in validate_build_inputs(build_inputs):
            _require(
                summary,
                bundle_name,
                False,
                "BUILD_INPUT_EVIDENCE_INVALID",
                f"{runtime_name} build input evidence: {error}",
            )
        if not isinstance(build_inputs, dict):
            continue

        for submodule in build_inputs.get("git_submodules") or []:
            if not isinstance(submodule, dict):
                continue
            path = submodule.get("path")
            ref = f"build-input:{runtime_name}:git-submodule:{path}"
            component = formulation_components.get(ref)
            _require(summary, bundle_name, component is not None, "BUILD_INPUT_COMPONENT_MISSING", f"SBOM is missing fetched submodule component {ref}")
            if not component:
                continue
            _require(summary, bundle_name, component.get("version") == submodule.get("resolved_commit"), "BUILD_INPUT_VERSION_MISMATCH", f"SBOM submodule version does not match observed commit for {path}")
            _require(summary, bundle_name, bool(component.get("purl")), "BUILD_INPUT_PURL_MISSING", f"SBOM submodule component lacks a PURL for {path}")
            _require(summary, bundle_name, _external_reference_url(component, "vcs") == submodule.get("repo"), "BUILD_INPUT_IDENTITY_MISMATCH", f"SBOM submodule repository does not match build evidence for {path}")
            _require(
                summary,
                bundle_name,
                _component_has_sha256(component, submodule.get("post_build_tree_sha256")),
                "BUILD_INPUT_TREE_DIGEST_MISMATCH",
                f"SBOM submodule tree digest does not match build evidence for {path}",
            )
            properties = _properties(component)
            _require(
                summary,
                bundle_name,
                properties.get("biochef:build.input.post_build_tree_sha256")
                == submodule.get("post_build_tree_sha256"),
                "BUILD_INPUT_TREE_DIGEST_MISMATCH",
                f"SBOM submodule tree digest property does not match build evidence for {path}",
            )


def _runtime_composition_evidence(evidence: dict[str, Any]) -> tuple[bool, list[str]]:
    runtimes = evidence.get("runtimes") or {}
    if not runtimes:
        return False, []

    complete = True
    errors = []
    for runtime_name, runtime_data in runtimes.items():
        build = runtime_data.get("build") or {}
        if runtime_name == "native":
            complete = False
            continue
        if runtime_name != "wasm":
            complete = False
            continue
        link = build.get("wasm_link")
        if not isinstance(link, dict) or not link.get("complete"):
            complete = False
            continue
        validation_errors = validate_wasm_link_evidence(link)
        if validation_errors:
            complete = False
            errors.extend(f"{runtime_name} linker evidence: {error}" for error in validation_errors)
    return complete, errors


def _check_dependency_graph(sbom: dict[str, Any], components: dict[str, dict[str, Any]], root_component: dict[str, Any], evidence: dict[str, Any], summary: CheckSummary, bundle_name: str) -> None:
    dependency_map = {}
    for dependency in sbom.get("dependencies") or []:
        ref = dependency.get("ref")
        _require(summary, bundle_name, ref not in dependency_map, "DUPLICATE_DEPENDENCY_REF", f"SBOM dependency graph contains duplicate ref {ref!r}")
        dependency_map[ref] = set(dependency.get("dependsOn") or [])

    expected_runtime_dependencies = _expected_runtime_dependencies(evidence)
    actual_runtime_dependency_refs = {
        ref for ref in components if isinstance(ref, str) and ref.startswith("runtime-dependency:")
    }
    _require(
        summary,
        bundle_name,
        actual_runtime_dependency_refs == set(expected_runtime_dependencies),
        "RUNTIME_DEPENDENCY_COMPONENT_MISMATCH",
        "SBOM runtime dependency components do not match complete linker evidence",
    )
    for ref, expected in expected_runtime_dependencies.items():
        component = components.get(ref) or {}
        properties = _properties(component)
        if expected.get("sha256"):
            _require(summary, bundle_name, _component_has_sha256(component, expected["sha256"]), "RUNTIME_DEPENDENCY_HASH_MISMATCH", f"SBOM runtime dependency hash does not match linker evidence for {ref}")
        for property_name, expected_value in (expected.get("properties") or {}).items():
            _require(summary, bundle_name, properties.get(property_name) == str(expected_value), "RUNTIME_DEPENDENCY_IDENTITY_MISMATCH", f"SBOM runtime dependency identity does not match linker evidence for {ref}")

    root_ref = root_component.get("bom-ref")
    runtime_refs = {
        ref for ref in components if isinstance(ref, str) and ref.startswith("file:runtime/")
    }
    required_root_refs = {
        "file:bundle.json",
        "file:build-evidence.json",
        "file:recipe/biochef.yaml",
        "source:upstream",
        *runtime_refs,
    }
    required_root_refs.update(
        ref
        for ref in components
        if isinstance(ref, str) and (ref == "file:LICENSE" or ref.startswith("file:license-evidence/"))
    )
    _require(summary, bundle_name, root_ref in dependency_map, "ROOT_DEPENDENCY_GRAPH_MISSING", "SBOM dependency graph is missing the root component")
    _require(summary, bundle_name, required_root_refs <= dependency_map.get(root_ref, set()), "ROOT_DEPENDENCY_GRAPH_INCOMPLETE", "SBOM root dependency graph omits required bundle components")

    runtime_dependencies_by_runtime = {}
    for ref in expected_runtime_dependencies:
        runtime_name = ref.split(":", 2)[1]
        runtime_dependencies_by_runtime.setdefault(runtime_name, set()).add(ref)
    for runtime_ref in runtime_refs:
        runtime_name = runtime_ref.split("/", 2)[1]
        expected_dependencies = {"source:upstream", *runtime_dependencies_by_runtime.get(runtime_name, set())}
        _require(summary, bundle_name, runtime_ref in dependency_map, "RUNTIME_DEPENDENCY_GRAPH_MISSING", f"SBOM dependency graph is missing runtime artifact {runtime_ref}")
        _require(summary, bundle_name, expected_dependencies <= dependency_map.get(runtime_ref, set()), "RUNTIME_DEPENDENCY_GRAPH_INCOMPLETE", f"SBOM runtime dependency graph omits source or linked dependencies for {runtime_ref}")


def _expected_runtime_dependencies(evidence: dict[str, Any]) -> dict[str, dict[str, Any]]:
    expected = {}
    for runtime_name, runtime_data in (evidence.get("runtimes") or {}).items():
        if runtime_name != "wasm" or not isinstance(runtime_data, dict):
            continue
        build = runtime_data.get("build") or {}
        link = build.get("wasm_link") or {}
        if not isinstance(link, dict) or link.get("complete") is not True or validate_wasm_link_evidence(link):
            continue
        output = link["outputs"][0]
        for archive in output.get("selected_archives") or []:
            identity = archive.get("identity") or {}
            if identity.get("kind") == "primary-source":
                continue
            ref = _runtime_dependency_ref(runtime_name, f"archive:{archive.get('path')}")
            expected[ref] = {
                "sha256": archive.get("sha256"),
                "properties": {
                    "biochef:runtime.dependency.runtime": runtime_name,
                    "biochef:runtime.dependency.kind": identity.get("kind"),
                    "biochef:runtime.dependency.path": archive.get("path"),
                },
            }

        linked_inputs = {}
        for item in output.get("direct_inputs") or []:
            identity = item.get("identity") or {}
            if identity.get("kind") in {"primary-source", "emscripten-generated"}:
                continue
            key = json.dumps(identity, sort_keys=True, separators=(",", ":"))
            linked_inputs.setdefault(key, {"identity": identity, "inputs": []})["inputs"].append(
                {"path": item.get("path"), "sha256": item.get("sha256")}
            )
        for key, group in linked_inputs.items():
            ref = _runtime_dependency_ref(runtime_name, f"identity:{key}")
            inputs = sorted(group["inputs"], key=lambda item: item.get("path") or "")
            expected[ref] = {
                "properties": {
                    "biochef:runtime.dependency.runtime": runtime_name,
                    "biochef:runtime.dependency.kind": group["identity"].get("kind"),
                    "biochef:runtime.dependency.selected_inputs_digest": canonical_digest(inputs),
                }
            }

        toolchain = build.get("toolchain") or {}
        for library in output.get("javascript_libraries") or []:
            ref = _runtime_dependency_ref(runtime_name, f"emscripten-js:{library}")
            expected[ref] = {
                "properties": {
                    "biochef:runtime.dependency.runtime": runtime_name,
                    "biochef:runtime.dependency.kind": "emscripten-javascript-library",
                    "biochef:runtime.dependency.emsdk.commit": toolchain.get("emsdk_resolved_commit"),
                }
            }
    return expected


def _runtime_dependency_ref(runtime: str, identity: str) -> str:
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return f"runtime-dependency:{runtime}:{digest}"


def _check_runtime_artifacts(bundle_dir: Path, components: dict[str, dict[str, Any]], bundle: dict[str, Any], evidence: dict[str, Any], summary: CheckSummary, bundle_name: str) -> None:
    runtime = bundle.get("runtime") or {}
    wasm = runtime.get("wasm") or {}
    bin_name = bundle.get("bin")
    safe_bin = isinstance(bin_name, str) and bin_name not in {"", ".", ".."} and "/" not in bin_name and "\\" not in bin_name
    _require(summary, bundle_name, safe_bin, "RUNTIME_BIN_PATH", f"bundle binary name is unsafe: {bin_name!r}")

    bundle_files = []
    if wasm and safe_bin:
        wasm_files = wasm.get("files")
        expected_paths = {f"runtime/wasm/{bin_name}.js", f"runtime/wasm/{bin_name}.wasm"}
        valid_wasm_files = isinstance(wasm_files, list) and len(wasm_files) == 2 and all(isinstance(item, dict) for item in wasm_files)
        valid_wasm_files = valid_wasm_files and all(isinstance(item.get("path"), str) for item in wasm_files)
        actual_paths = {item.get("path") for item in wasm_files} if valid_wasm_files else set()
        _require(summary, bundle_name, valid_wasm_files and actual_paths == expected_paths, "RUNTIME_WASM_INVENTORY", "WASM runtime inventory must contain exactly the operation JavaScript and WebAssembly artifacts")
        if valid_wasm_files:
            bundle_files.extend(wasm_files)

    native = runtime.get("native") or {}
    if native and safe_bin:
        bundle_files.append({"path": f"runtime/native/{bin_name}", "digest": native.get("digest")})

    bundle_inventory = {}
    for item in bundle_files:
        rel_path = item.get("path")
        digest = _normalize_digest(item.get("digest"))
        safe_path = _safe_bundle_relative_path(rel_path)
        _require(summary, bundle_name, safe_path is not None and digest != "", "RUNTIME_ARTIFACT_INCOMPLETE", f"bundle runtime artifact entry is invalid: {item!r}")
        if safe_path is None or not digest:
            continue
        _require(summary, bundle_name, safe_path not in bundle_inventory, "RUNTIME_ARTIFACT_DUPLICATE", f"bundle.json contains duplicate runtime artifact {safe_path}")
        bundle_inventory[safe_path] = digest

    evidence_inventory = {}
    evidence_runtimes = evidence.get("runtimes") or {}
    if not isinstance(evidence_runtimes, dict):
        return
    for runtime_name, runtime_data in evidence_runtimes.items():
        if not isinstance(runtime_data, dict):
            _require(summary, bundle_name, False, "RUNTIME_EVIDENCE_INVALID", f"{runtime_name} runtime evidence must be an object")
            continue
        artifacts = (runtime_data.get("artifacts") or {}).get("files") or []
        if not isinstance(artifacts, list):
            _require(summary, bundle_name, False, "RUNTIME_EVIDENCE_INVALID", f"{runtime_name} runtime artifact inventory must be a list")
            continue
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                _require(summary, bundle_name, False, "RUNTIME_ARTIFACT_INCOMPLETE", f"{runtime_name} runtime artifact entry must be an object")
                continue
            rel_path = artifact.get("path")
            expected_digest = artifact.get("digest")
            safe_path = _safe_bundle_relative_path(rel_path)
            normalized_digest = _normalize_digest(expected_digest)
            if safe_path is None or not normalized_digest:
                _require(
                    summary,
                    bundle_name,
                    False,
                    "RUNTIME_ARTIFACT_INCOMPLETE",
                    f"{runtime_name} runtime artifact entry has an unsafe path or invalid digest",
                )
                continue
            _require(summary, bundle_name, safe_path not in evidence_inventory, "RUNTIME_ARTIFACT_DUPLICATE", f"build evidence contains duplicate runtime artifact {safe_path}")
            evidence_inventory[safe_path] = normalized_digest
            candidate = Path(safe_path)

            artifact_path = bundle_dir / candidate
            component = components.get(f"file:{candidate.as_posix()}")
            _require(
                summary,
                bundle_name,
                artifact_path.is_file() and not artifact_path.is_symlink(),
                "RUNTIME_ARTIFACT_MISSING",
                f"{runtime_name} runtime artifact is missing or unsafe: {safe_path}",
            )
            _require(
                summary,
                bundle_name,
                component is not None,
                "RUNTIME_COMPONENT_MISSING",
                f"SBOM is missing runtime artifact component file:{candidate.as_posix()}",
            )
            if artifact_path.is_file() and not artifact_path.is_symlink():
                actual_hash = sha256_hex(artifact_path)
                _require(
                    summary,
                    bundle_name,
                    normalized_digest == actual_hash,
                    "RUNTIME_DIGEST_MISMATCH",
                    f"{runtime_name} runtime artifact digest mismatch for {safe_path}",
                )
                if component:
                    _require(
                        summary,
                        bundle_name,
                        _component_has_sha256(component, f"sha256:{actual_hash}"),
                        "RUNTIME_SBOM_HASH_MISMATCH",
                        f"SBOM hash does not match runtime artifact {safe_path}",
                    )
    _require(
        summary,
        bundle_name,
        bool(bundle_inventory) and bundle_inventory == evidence_inventory,
        "RUNTIME_INVENTORY_MISMATCH",
        "bundle.json and build evidence runtime inventories do not match",
    )

    wasm_link = (((evidence_runtimes.get("wasm") or {}).get("build") or {}).get("wasm_link") or {})
    if isinstance(wasm_link, dict) and wasm_link.get("complete") is True:
        outputs = wasm_link.get("outputs") or []
        linked_inventory = {}
        if len(outputs) == 1 and isinstance(outputs[0], dict):
            linked_inventory = {
                f"runtime/wasm/{item.get('name')}": _normalize_digest(item.get("digest"))
                for item in outputs[0].get("artifacts") or []
                if isinstance(item, dict) and item.get("name")
            }
        expected_wasm_inventory = {path: digest for path, digest in evidence_inventory.items() if path.startswith("runtime/wasm/")}
        _require(summary, bundle_name, linked_inventory == expected_wasm_inventory, "RUNTIME_LINK_ARTIFACT_MISMATCH", "WebAssembly linker evidence artifact digests do not match the bundled runtime artifacts")


def _check_file_hash(path: Path, component: dict[str, Any] | None, summary: CheckSummary, bundle_name: str, code: str) -> None:
    if not path.is_file() or component is None:
        return
    _require(
        summary,
        bundle_name,
        _component_has_sha256(component, f"sha256:{sha256_hex(path)}"),
        code,
        f"SBOM hash does not match {path.name}",
    )


def _check_license_evidence(bundle_dir: Path, components: dict[str, dict[str, Any]], root_component: dict[str, Any], root_properties: dict[str, str], evidence: dict[str, Any], summary: CheckSummary, bundle_name: str) -> None:
    license_evidence = evidence.get("license") or {}
    license_files = _license_evidence_files(license_evidence)
    license_available = license_evidence.get("available") is True
    license_exact = license_evidence.get("exact") is True
    license_verified = license_evidence.get("verified", license_exact) is True
    root_declares_license = bool(root_component.get("licenses"))

    if root_declares_license:
        _require(
            summary,
            bundle_name,
            license_available,
            "LICENSE_EVIDENCE_MISSING",
            "root component declares a license but build-evidence.json does not record license evidence",
        )
        _require(
            summary,
            bundle_name,
            license_verified,
            "LICENSE_EVIDENCE_NOT_VERIFIED",
            "root component declares a license but license evidence is not exact-source or hash-verified",
        )

    evidence_paths = []
    for license_file in license_files:
        relative_path = _safe_bundle_relative_path(license_file.get("path"))
        role = license_file.get("role")
        if relative_path is None:
            _require(
                summary,
                bundle_name,
                False,
                "LICENSE_EVIDENCE_PATH",
                f"license evidence path is unsafe: {license_file.get('path')!r}",
            )
            continue
        _require(
            summary,
            bundle_name,
            role in {"license", "source-header"},
            "LICENSE_EVIDENCE_ROLE",
            f"license evidence role is unsupported for {relative_path}: {role!r}",
        )
        _require(
            summary,
            bundle_name,
            (role == "license" and relative_path == "LICENSE")
            or (role == "source-header" and relative_path.startswith("license-evidence/")),
            "LICENSE_EVIDENCE_PATH",
            f"license evidence path is inconsistent with role {role!r}: {relative_path}",
        )
        evidence_path = bundle_dir / relative_path
        component = components.get(f"file:{relative_path}")
        _require(
            summary,
            bundle_name,
            component is not None,
            "LICENSE_COMPONENT_MISSING",
            f"license evidence exists but SBOM is missing file:{relative_path} component",
        )
        _require(
            summary,
            bundle_name,
            evidence_path.is_file() and not evidence_path.is_symlink(),
            "LICENSE_FILE_MISSING",
            f"license evidence file is missing or unsafe: {relative_path}",
        )
        _require(
            summary,
            bundle_name,
            license_file.get("verified") is True,
            "LICENSE_EVIDENCE_NOT_VERIFIED",
            f"license evidence file is not verified: {relative_path}",
        )
        expected_digest = license_file.get("digest")
        _require(
            summary,
            bundle_name,
            bool(_normalize_digest(expected_digest)),
            "LICENSE_EVIDENCE_DIGEST_MISSING",
            f"license evidence file does not record a valid SHA-256 digest: {relative_path}",
        )
        if _normalize_digest(expected_digest) and evidence_path.is_file() and not evidence_path.is_symlink():
            _require(
                summary,
                bundle_name,
                _normalize_digest(expected_digest) == sha256_hex(evidence_path),
                "LICENSE_EVIDENCE_HASH",
                f"license evidence file hash does not match build-evidence.json: {relative_path}",
            )
        if component:
            _check_file_hash(evidence_path, component, summary, bundle_name, "LICENSE_SBOM_HASH")
        if evidence_path.is_file() and not evidence_path.is_symlink():
            evidence_paths.append((evidence_path, license_file))

    if (bundle_dir / "LICENSE").is_file() and not any(license_file.get("path") == "LICENSE" for _, license_file in evidence_paths):
        _require(
            summary,
            bundle_name,
            False,
            "LICENSE_EVIDENCE_MISSING",
            "LICENSE file is present but build-evidence.json does not record its source",
        )

    if root_declares_license and not evidence_paths:
        _require(
            summary,
            bundle_name,
            False,
            "LICENSE_FILE_MISSING",
            "root component declares a license, but no license evidence files are present",
        )

    if root_properties.get("biochef:license.evidence.available") == "true":
        _require(
            summary,
            bundle_name,
            bool(evidence_paths),
            "LICENSE_FILE_MISSING",
            "SBOM says license evidence is available, but no license evidence files are present",
        )

    _check_declared_license_matches_evidence(evidence_paths, license_evidence, root_component, summary, bundle_name)


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


def _check_declared_license_matches_evidence(evidence_paths: list[tuple[Path, dict[str, Any]]], license_evidence: dict[str, Any], root_component: dict[str, Any], summary: CheckSummary, bundle_name: str) -> None:
    declared = _declared_license_expressions(root_component)
    if not declared:
        return
    for expression in declared:
        try:
            SPDX_LICENSING.parse(expression, validate=True)
        except ExpressionError as exc:
            _require(
                summary,
                bundle_name,
                False,
                "LICENSE_DECLARATION_INVALID",
                f"recipe declares invalid SPDX license expression {expression!r}: {exc}",
            )
            return

    detections = []
    for evidence_path, license_file in evidence_paths:
        detected = _detect_license_with_scancode(evidence_path, summary, bundle_name)
        if detected:
            detections.append(
                {
                    "expression": detected,
                    "path": license_file.get("path"),
                    "role": license_file.get("role"),
                }
            )
    if not detections:
        return

    unaccounted = [
        detection
        for detection in detections
        if not _license_detection_supports_conclusion(declared, detection, detections)
    ]
    if not unaccounted:
        return

    _require(
        summary,
        bundle_name,
        False,
        "LICENSE_CONCLUSION_UNSUPPORTED",
        f"recipe concludes license {declared}, but ScanCode did not find supporting license evidence {unaccounted!r}",
    )


def _declared_license_expressions(root_component: dict[str, Any]) -> list[str]:
    expressions = []
    for entry in root_component.get("licenses") or []:
        license_data = entry.get("license") or {}
        expression = license_data.get("id") or license_data.get("expression")
        if expression:
            expressions.append(str(expression))
    return expressions


def _detect_license_with_scancode(path: Path, summary: CheckSummary, bundle_name: str) -> str | None:
    license_digest = sha256_hex(path)
    if license_digest in LICENSE_SCAN_CACHE:
        detected = LICENSE_SCAN_CACHE[license_digest]
        if detected is None:
            _require(
                summary,
                bundle_name,
                False,
                "LICENSE_DETECTION_MISSING",
                "ScanCode did not detect a license expression in bundled license evidence",
            )
        return detected

    command = _resolve_scancode_command()
    if command is None:
        _require(
            summary,
            bundle_name,
            False,
            "LICENSE_DETECTOR_UNAVAILABLE",
            "ScanCode executable is required for license declaration verification",
        )
        return None

    with tempfile.TemporaryDirectory(prefix="biochef-scancode-") as tmpdir:
        output_path = Path(tmpdir) / "license-scan.json"
        try:
            result = subprocess.run(
                [
                    command,
                    "--license",
                    "--license-score",
                    SCANCODE_MIN_LICENSE_SCORE,
                    "--json-pp",
                    str(output_path),
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=SCANCODE_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            _require(
                summary,
                bundle_name,
                False,
                "LICENSE_DETECTION_TIMEOUT",
                f"ScanCode license scan exceeded {SCANCODE_TIMEOUT_SECONDS} seconds",
            )
            return None
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "ScanCode license scan failed").strip()
            _require(summary, bundle_name, False, "LICENSE_DETECTION_FAILED", message)
            return None

        try:
            report = json.loads(output_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _require(
                summary,
                bundle_name,
                False,
                "LICENSE_DETECTION_OUTPUT",
                f"ScanCode did not produce valid JSON output: {exc}",
            )
            return None

    detected = _scancode_license_expression(report)
    LICENSE_SCAN_CACHE[license_digest] = detected
    if not detected:
        _require(
            summary,
            bundle_name,
            False,
            "LICENSE_DETECTION_MISSING",
            "ScanCode did not detect a license expression in bundled license evidence",
        )
    return detected


def _resolve_scancode_command() -> str | None:
    command = shutil.which("scancode")
    if command:
        return command

    venv_command = Path(sys.executable).with_name("scancode")
    if venv_command.is_file():
        return str(venv_command)
    return None


def _scancode_license_expression(report: dict[str, Any]) -> str | None:
    files = report.get("files") or []
    file_record = next((item for item in files if item.get("type") != "directory"), None)
    if not file_record:
        return None

    detected_expression = file_record.get("detected_license_expression_spdx")
    if detected_expression and detected_expression != "unknown":
        return str(detected_expression)

    for detection in file_record.get("license_detections") or []:
        expression = detection.get("license_expression_spdx")
        if expression and expression != "unknown":
            return str(expression)
    return None


def _declared_license_covers_detection(declared: str, detected: str) -> bool:
    try:
        return SPDX_LICENSING.is_equivalent(declared, detected) or SPDX_LICENSING.contains(declared, detected)
    except ExpressionError:
        return False


def _license_detection_supports_conclusion(declared: list[str], detection: dict[str, str], detections: list[dict[str, str]]) -> bool:
    expression = detection["expression"]
    if any(_declared_license_covers_detection(declared_expression, expression) for declared_expression in declared):
        return True
    # ScanCode reports all full texts and notices in a file, but cannot reliably
    # resolve upstream alternatives or build-dependent license selections.
    if detection.get("role") == "source-header":
        return any(_license_expressions_overlap(declared_expression, expression) for declared_expression in declared)
    if detection.get("role") != "license":
        return False
    if any(_license_expressions_overlap(declared_expression, expression) for declared_expression in declared):
        return True
    return any(
        _declared_license_covers_detection(declared_expression, header_detection["expression"])
        and _or_later_declaration_has_base_license_text(declared_expression, expression)
        for declared_expression in declared
        for header_detection in detections
        if header_detection.get("role") == "source-header"
    )


def _or_later_declaration_has_base_license_text(declared: str, detected: str) -> bool:
    try:
        declared = str(SPDX_LICENSING.parse(declared, validate=True))
        detected = str(SPDX_LICENSING.parse(detected, validate=True))
    except ExpressionError:
        return False
    if not declared.endswith("-or-later") or not detected.endswith("-only"):
        return False
    return declared.removesuffix("-or-later") == detected.removesuffix("-only")


def _license_expressions_overlap(left: str, right: str) -> bool:
    try:
        return (
            SPDX_LICENSING.is_equivalent(left, right)
            or SPDX_LICENSING.contains(left, right)
            or SPDX_LICENSING.contains(right, left)
            or bool(_license_symbol_keys(left) & _license_symbol_keys(right))
        )
    except ExpressionError:
        return False


def _license_symbol_keys(expression: str) -> set[str]:
    parsed = SPDX_LICENSING.parse(expression, validate=True)
    return {str(symbol.key) for symbol in parsed.symbols}


def _check_builder_dirty_state(evidence: dict[str, Any], summary: CheckSummary, bundle_name: str) -> None:
    for runtime_name, runtime_data in (evidence.get("runtimes") or {}).items():
        build = runtime_data.get("build") or {}
        source = build.get("source") or {}
        actual_source = source.get("actual") or {}
        for label, value in (
            ("source dirty", source.get("dirty")),
            ("actual source dirty", actual_source.get("dirty")),
            ("BioWASM dirty before build", (build.get("biowasm") or {}).get("dirty_before_build")),
        ):
            _require(summary, bundle_name, value is None or isinstance(value, bool), "INVALID_EVIDENCE_TYPE", f"{runtime_name} {label} value must be boolean when present")
        source_dirty = source.get("dirty") is True or actual_source.get("dirty") is True
        biowasm = build.get("biowasm") or {}
        biowasm_dirty_before = biowasm.get("dirty_before_build", biowasm.get("dirty")) is True
        _require(
            summary,
            bundle_name,
            not source_dirty or _recorded_source_mutation(source),
            "SOURCE_MUTATION_UNRECORDED",
            f"{runtime_name} source checkout changed during build without complete mutation evidence",
        )
        _require(
            summary,
            bundle_name,
            not biowasm_dirty_before,
            "BIOWASM_WORKTREE_DIRTY",
            f"{runtime_name} BioWASM checkout was dirty before build",
        )


def _recorded_source_mutation(source: dict[str, Any]) -> bool:
    actual = source.get("actual") or {}
    mutation = source.get("mutation") or {}
    if actual.get("kind") != "git":
        return False
    dirty_files = actual.get("dirty_files")
    if not dirty_files:
        return True
    if mutation.get("status") != dirty_files:
        return False
    return bool(
        mutation.get("recorded")
        and mutation.get("status_digest")
        and (mutation.get("git_diff_digest") or mutation.get("untracked_files") or mutation.get("patches"))
        and mutation.get("biowasm_config_digest")
        and mutation.get("biowasm_scripts")
    )


def _check_declared_source_resolution(evidence: dict[str, Any], summary: CheckSummary, bundle_name: str) -> None:
    recipe_source = ((evidence.get("recipe") or {}).get("source") or {})
    for runtime_name, runtime_data in (evidence.get("runtimes") or {}).items():
        build = runtime_data.get("build") or {}
        if build.get("builder") == "biowasm":
            continue
        source = build.get("source") or {}
        requested_commit = source.get("requested_commit")
        resolved_commit = source.get("resolved_commit")

        _require(
            summary,
            bundle_name,
            _is_git_commit(requested_commit),
            "SOURCE_REQUESTED_COMMIT_MISSING",
            f"{runtime_name} build did not record the recipe-requested source commit",
        )
        _require(
            summary,
            bundle_name,
            _is_git_commit(resolved_commit),
            "SOURCE_RESOLVED_COMMIT_MISSING",
            f"{runtime_name} build did not record the resolved source commit",
        )
        if requested_commit and resolved_commit:
            _require(
                summary,
                bundle_name,
                requested_commit == resolved_commit,
                "SOURCE_RESOLVED_COMMIT_MISMATCH",
                f"{runtime_name} resolved source commit {resolved_commit!r} does not match requested commit {requested_commit!r}",
            )
        if recipe_source.get("commit"):
            _require(
                summary,
                bundle_name,
                requested_commit == recipe_source.get("commit"),
                "SOURCE_REQUESTED_COMMIT_MISMATCH",
                f"{runtime_name} requested source commit does not match the recipe source commit",
            )


def _check_build_source_alignment(evidence: dict[str, Any], summary: CheckSummary, bundle_name: str) -> None:
    recipe_source = ((evidence.get("recipe") or {}).get("source") or {})
    for runtime_name, runtime_data in (evidence.get("runtimes") or {}).items():
        build = runtime_data.get("build") or {}
        if build.get("builder") != "biowasm":
            source = build.get("source") or {}
            actual = source.get("actual") or {}
            _require(summary, bundle_name, actual.get("kind") == "git", "SOURCE_EVIDENCE_MISSING", f"{runtime_name} build did not record the actual git source used")
            _require(summary, bundle_name, _same_repo(actual.get("repo"), recipe_source.get("repo")), "SOURCE_REPO_MISMATCH", f"{runtime_name} actual source repository does not match the recipe source repository")
            _require(summary, bundle_name, actual.get("resolved_commit") == recipe_source.get("commit"), "SOURCE_COMMIT_MISMATCH", f"{runtime_name} actual source commit does not match the recipe source commit")
            continue

        source = build.get("source") or {}
        declared = source.get("declared") or {}
        actual = source.get("actual") or {}
        actual_kind = actual.get("kind")

        _require(
            summary,
            bundle_name,
            bool(actual_kind),
            "BIOWASM_SOURCE_EVIDENCE_MISSING",
            f"{runtime_name} BioWASM build did not record the actual source used",
        )
        if not actual_kind:
            continue

        if actual_kind == "git":
            declared_repo = declared.get("repo")
            actual_repo = actual.get("repo")
            _require(
                summary,
                bundle_name,
                _same_repo(declared_repo, actual_repo),
                "BIOWASM_SOURCE_REPO_MISMATCH",
                f"{runtime_name} BioWASM source repo {actual_repo!r} does not match recipe source repo {declared_repo!r}",
            )

            declared_commit = declared.get("commit")
            actual_commit = actual.get("resolved_commit")
            if declared_commit:
                _require(
                    summary,
                    bundle_name,
                    declared_commit == actual_commit,
                    "BIOWASM_SOURCE_COMMIT_MISMATCH",
                    f"{runtime_name} BioWASM source commit {actual_commit!r} does not match recipe source commit {declared_commit!r}",
                )
            continue

        if actual_kind == "vendored":
            expected_hash = declared.get("sha256")
            actual_files = actual.get("files") or []
            if expected_hash:
                actual_hashes = {_normalize_digest(actual.get("directory_digest"))}
                if len(actual_files) == 1:
                    actual_hashes.add(_normalize_digest(actual_files[0].get("sha256")))
                actual_hashes.discard("")
                _require(
                    summary,
                    bundle_name,
                    _normalize_digest(expected_hash) in actual_hashes,
                    "BIOWASM_VENDORED_SOURCE_HASH_MISMATCH",
                    f"{runtime_name} vendored BioWASM source hash does not match recipe source hash",
                )
            else:
                _require(
                    summary,
                    bundle_name,
                    not declared.get("repo"),
                    "BIOWASM_SOURCE_VENDORED_MISMATCH",
                    f"{runtime_name} BioWASM built vendored source, but recipe declares git source repo {declared.get('repo')!r}",
                )
            continue

        _require(
            summary,
            bundle_name,
            False,
            "BIOWASM_SOURCE_KIND_UNSUPPORTED",
            f"{runtime_name} BioWASM source kind {actual_kind!r} is not supported by release policy",
        )


def _read_json(path: Path, summary: CheckSummary, bundle_name: str, label: str) -> dict[str, Any] | None:
    if path.is_symlink():
        _require(summary, bundle_name, False, "SYMLINKED_INPUT", f"{label} must not be a symlink")
        return None
    if not path.is_file():
        _require(summary, bundle_name, False, "MISSING_INPUT", f"{label} is missing")
        return None
    try:
        with path.open(encoding="utf-8") as file:
            document = json.load(file)
        if not isinstance(document, dict):
            _require(summary, bundle_name, False, "INVALID_JSON_TYPE", f"{label} must contain a JSON object")
            return None
        return document
    except OSError as exc:
        _require(summary, bundle_name, False, "INPUT_READ_ERROR", f"{label} could not be read: {exc}")
        return None
    except json.JSONDecodeError as exc:
        _require(summary, bundle_name, False, "INVALID_JSON", f"{label} is invalid JSON: {exc}")
        return None


def _properties(component: dict[str, Any]) -> dict[str, str]:
    properties = {}
    for prop in component.get("properties", []):
        name = prop.get("name")
        value = prop.get("value")
        if name is not None and value is not None:
            properties[str(name)] = str(value)
    return properties


def _component_has_sha256(component: dict[str, Any], digest: str) -> bool:
    expected = _normalize_digest(digest)
    return any(
        item.get("alg") == "SHA-256" and _normalize_digest(item.get("content")) == expected
        for item in component.get("hashes", [])
    )


def _external_reference_url(component: dict[str, Any], reference_type: str) -> str | None:
    return next(
        (
            reference.get("url")
            for reference in component.get("externalReferences", [])
            if reference.get("type") == reference_type
        ),
        None,
    )


def _same_repo(left: str | None, right: str | None) -> bool:
    return bool(left and right and _normalize_repo(left) == _normalize_repo(right))


def _normalize_repo(url: str | None) -> str:
    if not isinstance(url, str) or not url:
        return ""
    value = url.strip()
    if value.startswith("git@github.com:"):
        value = "https://github.com/" + value.split(":", 1)[1]
    value = value.replace("git://", "https://")
    parsed = urlparse(value)
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    if host == "git.savannah.gnu.org" and path.startswith("/git/"):
        path = "/" + path.removeprefix("/git/")
    if host == "github.com":
        path = path.lower()
    return f"{host}{path}"


def _normalize_digest(value: str | None) -> str:
    if not isinstance(value, str) or not value:
        return ""
    if value.startswith("sha256:"):
        value = value.split(":", 1)[1]
    if len(value) != 64 or any(char not in "0123456789abcdefABCDEF" for char in value):
        return ""
    return value.lower()


def _safe_bundle_relative_path(value: str | None) -> str | None:
    if not isinstance(value, str) or not value or not is_safe_relative_path(value):
        return None
    candidate = Path(value)
    if candidate.as_posix() == ".":
        return None
    return candidate.as_posix()


def _is_git_commit(value: Any) -> bool:
    return isinstance(value, str) and len(value) in {40, 64} and all(char in "0123456789abcdefABCDEF" for char in value)


def _first_line(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().splitlines()[0]


def _require(summary: CheckSummary, bundle: str, condition: bool, code: str, message: str) -> None:
    if condition:
        return
    summary.failures.append(CheckIssue(bundle=bundle, code=code, message=message))
