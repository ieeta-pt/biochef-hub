import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

from cyclonedx.schema import SchemaVersion
from cyclonedx.validation.json import JsonStrictValidator

from builders.container import canonical_digest, is_sha256_digest, sha256_hex


SBOM_FILE_NAME = "sbom.cdx.json"


class SbomGenerationError(RuntimeError):
    pass


@dataclass
class SbomSummary:
    scanned: int = 0
    written: int = 0
    failures: list[str] = field(default_factory=list)
    outputs: list[Path] = field(default_factory=list)


def generate_sboms(registry_dir="registry"):
    registry_path = Path(registry_dir).resolve()
    if not registry_path.is_dir():
        raise SbomGenerationError(
            f"Registry directory does not exist: {registry_path}"
        )

    summary = SbomSummary()
    for bundle_path in sorted(registry_path.glob("*/*/bundle.json")):
        summary.scanned += 1
        try:
            output = generate_bundle_sbom(bundle_path)
            summary.written += 1
            summary.outputs.append(output)
        except Exception as exc:
            summary.failures.append(f"{bundle_path}: {exc}")

    if not summary.scanned:
        raise SbomGenerationError(
            f"No bundle.json files found under {registry_path}"
        )
    if summary.failures:
        raise SbomGenerationError(
            "SBOM generation failed:\n"
            + "\n".join(f"  - {failure}" for failure in summary.failures)
        )
    return summary


def generate_bundle_sbom(bundle_path):
    bundle_path = Path(bundle_path)
    bundle_dir = bundle_path.parent
    operation_id = bundle_dir.parent.name
    version = bundle_dir.name
    bundle = read_json(bundle_path)
    evidence_path = require_file(bundle_dir, "build-evidence.json")
    evidence = read_json(evidence_path)
    validate_identity(bundle, evidence, operation_id, version)

    root_ref = f"pkg:generic/biochef/{quote(operation_id, safe='.-_')}@{quote(version, safe='.-_+')}"
    components = []
    dependencies = {root_ref: set()}

    add_file_component(
        components,
        dependencies,
        root_ref,
        bundle_dir,
        "bundle.json",
        "BioCHEF operation bundle",
    )
    add_file_component(
        components,
        dependencies,
        root_ref,
        bundle_dir,
        "build-evidence.json",
        "BioCHEF build evidence",
    )

    for item in (evidence.get("license") or {}).get("files") or []:
        add_file_component(
            components,
            dependencies,
            root_ref,
            bundle_dir,
            item["path"],
            f"License evidence ({item.get('role', 'license')})",
        )

    runtime_refs = []
    for runtime, runtime_data in sorted((evidence.get("runtimes") or {}).items()):
        for item in (runtime_data.get("artifacts") or {}).get("files") or []:
            reference = add_file_component(
                components,
                dependencies,
                root_ref,
                bundle_dir,
                item["path"],
                f"BioCHEF {runtime} runtime artifact",
                expected_digest=item.get("digest"),
                properties={"biochef.runtime": runtime},
            )
            runtime_refs.append(reference)

    source_ref = add_source_component(components, evidence)
    if source_ref:
        dependencies[root_ref].add(source_ref)
        dependencies.setdefault(source_ref, set())

    dependency_refs = []
    for runtime_data in (evidence.get("runtimes") or {}).values():
        build = runtime_data.get("build") or {}
        for item in build.get("dependencies") or []:
            reference = add_dependency_component(components, item)
            if reference:
                dependency_refs.append(reference)
                dependencies.setdefault(reference, set())

    for runtime_ref in runtime_refs:
        dependencies.setdefault(runtime_ref, set()).update(dependency_refs)
        if source_ref:
            dependencies[runtime_ref].add(source_ref)

    formulation = formulation_components(evidence)
    document = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.7",
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "authors": [{"name": "BioChef"}],
            "lifecycles": [{"phase": "post-build"}],
            "component": {
                "type": "application",
                "bom-ref": root_ref,
                "group": "biochef",
                "name": operation_id,
                "version": version,
                "description": bundle.get("description", ""),
                "purl": root_ref,
                "licenses": license_expression(evidence),
                "properties": properties(
                    {
                        "biochef.operation.id": operation_id,
                        "biochef.operation.bin": bundle.get("bin"),
                        "biochef.recipe.id": (evidence.get("recipe") or {}).get("id"),
                        "biochef.recipe.digest": (evidence.get("recipe") or {}).get("digest"),
                        "biochef.inventory.scope": "bundle-files-and-recorded-build-inputs",
                    }
                ),
            },
            "tools": {
                "components": [
                    {
                        "type": "application",
                        "name": "biochef-hub",
                        "version": (evidence.get("hub") or {}).get("commit"),
                    }
                ]
            },
        },
        "components": deduplicate_components(components),
        "dependencies": [
            {"ref": reference, "dependsOn": sorted(refs)}
            for reference, refs in sorted(dependencies.items())
        ],
    }
    if formulation:
        document["formulation"] = [
            {
                "bom-ref": "formulation:biochef-build",
                "components": formulation,
            }
        ]

    output = json.dumps(document, indent=2, sort_keys=True) + "\n"
    errors = JsonStrictValidator(SchemaVersion.V1_7).validate_str(
        output,
        all_errors=True,
    )
    if errors:
        raise SbomGenerationError(
            "CycloneDX validation failed:\n"
            + "\n".join(f"  - {error}" for error in errors)
        )

    output_path = bundle_dir / SBOM_FILE_NAME
    if output_path.is_symlink():
        raise SbomGenerationError(
            f"Refusing to overwrite symlinked SBOM: {output_path}"
        )
    output_path.write_text(output, encoding="utf-8")
    return output_path


def validate_identity(bundle, evidence, operation_id, version):
    recipe = evidence.get("recipe") or {}
    operation = evidence.get("operation") or {}
    expected = (
        (bundle.get("id"), operation_id, "bundle id"),
        (bundle.get("version"), version, "bundle version"),
        (operation.get("id"), operation_id, "evidence operation id"),
        (recipe.get("version"), version, "evidence recipe version"),
    )
    for actual, wanted, label in expected:
        if actual != wanted:
            raise SbomGenerationError(
                f"{label} does not match registry path: {actual!r} != {wanted!r}"
            )
    if evidence.get("schema") != "biochef.build-evidence.v1":
        raise SbomGenerationError("Unsupported build evidence schema")


def add_file_component(
    components,
    dependencies,
    root_ref,
    bundle_dir,
    relative,
    description,
    expected_digest=None,
    properties=None,
):
    path = require_file(bundle_dir, relative)
    digest = f"sha256:{sha256_hex(path)}"
    if expected_digest and digest != expected_digest:
        raise SbomGenerationError(
            f"Recorded digest does not match {relative}: {expected_digest} != {digest}"
        )
    reference = f"file:{relative}"
    component = {
        "type": "file",
        "bom-ref": reference,
        "name": Path(relative).name,
        "description": description,
        "hashes": [{"alg": "SHA-256", "content": digest[7:]}],
        "properties": properties_for_file(relative, path, properties),
    }
    components.append(component)
    dependencies[root_ref].add(reference)
    dependencies.setdefault(reference, set())
    return reference


def add_source_component(components, evidence):
    recipe = evidence.get("recipe") or {}
    declared = recipe.get("source") or {}
    actual = first_actual_source(evidence)
    if not declared:
        return None
    reference = "source:upstream"
    name = source_name(declared)
    component = {
        "type": "library",
        "bom-ref": reference,
        "name": name,
        "version": declared.get("version") or (actual or {}).get("commit"),
        "properties": properties(
            {
                "biochef.source.commit": (actual or {}).get("commit"),
                "biochef.source.tree_digest": (actual or {}).get("tree_digest"),
                "biochef.source.final_tree_digest": (actual or {}).get("final_tree_digest"),
            }
        ),
    }
    url = declared.get("repo") or declared.get("url")
    if url:
        component["externalReferences"] = [
            {
                "type": "vcs" if declared.get("repo") else "distribution",
                "url": url,
            }
        ]
    purl = source_purl(declared, actual)
    if purl:
        component["purl"] = purl
    components.append(component)
    return reference


def add_dependency_component(components, item):
    source = item.get("source") or {}
    if source.get("kind") not in {"git", "vendored"}:
        return None
    identity = (
        source.get("commit")
        or source.get("tree_digest")
        or canonical_digest(item)
    )
    reference = f"dependency:{canonical_digest([item.get('name'), identity])[7:23]}"
    component = {
        "type": "library",
        "bom-ref": reference,
        "name": item.get("name") or "build-dependency",
        "version": item.get("version") or source.get("commit"),
        "hashes": digest_hashes(source.get("tree_digest")),
        "properties": properties(
            {
                "biochef.source.commit": source.get("commit"),
                "biochef.source.path": item.get("path"),
            }
        ),
    }
    if source.get("repo"):
        component["externalReferences"] = [
            {"type": "vcs", "url": source["repo"]}
        ]
    components.append(component)
    return reference


def formulation_components(evidence):
    components = []
    hub = evidence.get("hub") or {}
    if hub.get("commit"):
        components.append(
            {
                "type": "application",
                "bom-ref": "build:biochef-hub",
                "name": "biochef-hub",
                "version": hub["commit"],
            }
        )
    seen = set()
    for runtime_data in (evidence.get("runtimes") or {}).values():
        build = runtime_data.get("build") or {}
        for key, component in (
            ("builder_image", image_component(build.get("builder_image"))),
            ("framework", framework_component(build.get("framework"))),
            ("toolchain", toolchain_component(build.get("toolchain"))),
        ):
            if component and (key, component.get("version")) not in seen:
                seen.add((key, component.get("version")))
                components.append(component)
    return components


def image_component(image):
    if not isinstance(image, dict):
        return None
    digest = image.get("manifest_digest") or image.get("image_id")
    return {
        "type": "container",
        "bom-ref": "build:biowasm-image",
        "name": "biochef-biowasm-builder",
        "version": digest,
        "hashes": digest_hashes(digest),
        "properties": properties(
            {
                "biochef.image.reference": image.get("requested_reference"),
                "biochef.image.context_digest": image.get("context_digest"),
            }
        ),
    }


def framework_component(framework):
    if not isinstance(framework, dict):
        return None
    return {
        "type": "application",
        "bom-ref": "build:biowasm",
        "name": "BioWASM",
        "version": framework.get("commit"),
        "externalReferences": (
            [{"type": "vcs", "url": framework["repo"]}]
            if framework.get("repo")
            else []
        ),
    }


def toolchain_component(toolchain):
    if not isinstance(toolchain, dict):
        return None
    return {
        "type": "application",
        "bom-ref": f"build:toolchain:{toolchain.get('name', 'unknown')}",
        "name": toolchain.get("name") or "build-toolchain",
        "version": toolchain.get("version") or toolchain.get("commit"),
        "properties": properties(
            {
                "biochef.source.commit": toolchain.get("commit"),
                "biochef.emsdk.commit": toolchain.get("emsdk_commit"),
            }
        ),
    }


def first_actual_source(evidence):
    for runtime_data in (evidence.get("runtimes") or {}).values():
        source = (runtime_data.get("build") or {}).get("source") or {}
        actual = source.get("actual") or source
        if isinstance(actual, dict) and actual.get("kind"):
            return actual
    return None


def source_purl(declared, actual):
    repo = declared.get("repo")
    commit = (actual or {}).get("commit") or declared.get("commit")
    if not repo or not commit:
        return None
    parsed = urlparse(repo)
    if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
        return None
    parts = parsed.path.strip("/").removesuffix(".git").split("/")
    if len(parts) < 2:
        return None
    return f"pkg:github/{quote(parts[0])}/{quote(parts[1])}@{commit}"


def source_name(source):
    value = source.get("repo") or source.get("url") or "upstream-source"
    return Path(urlparse(value).path).name.removesuffix(".git") or "upstream-source"


def license_expression(evidence):
    expression = ((evidence.get("license") or {}).get("spdx") or "").strip()
    return (
        [{"expression": expression, "acknowledgement": "concluded"}]
        if expression
        else []
    )


def properties(values):
    return [
        {"name": name, "value": str(value).lower() if isinstance(value, bool) else str(value)}
        for name, value in values.items()
        if value not in (None, "", [], {})
    ]


def properties_for_file(relative, path, extra):
    return properties(
        {
            "biochef.bundle.path": relative,
            "biochef.file.size": path.stat().st_size,
            **(extra or {}),
        }
    )


def digest_hashes(value):
    return (
        [{"alg": "SHA-256", "content": value[7:]}]
        if is_sha256_digest(value)
        else []
    )


def deduplicate_components(components):
    unique = {}
    for component in components:
        unique.setdefault(component["bom-ref"], component)
    return list(unique.values())


def require_file(bundle_dir, relative):
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise SbomGenerationError(f"Unsafe bundle path: {relative!r}")
    root = Path(bundle_dir).resolve()
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise SbomGenerationError(
            f"Bundle path escapes its directory: {relative}"
        ) from exc
    if path.is_symlink() or not path.is_file():
        raise SbomGenerationError(f"Missing or unsafe bundle file: {relative}")
    return path


def read_json(path):
    if path.is_symlink():
        raise SbomGenerationError(f"Refusing to read symlinked JSON: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SbomGenerationError(f"Could not read JSON {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise SbomGenerationError(f"JSON document must be an object: {path}")
    return document
