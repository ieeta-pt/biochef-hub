import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from cyclonedx.schema import SchemaVersion
from cyclonedx.validation.json import JsonStrictValidator

from builders.container import is_sha256_digest, sha256_hex


COMMIT = re.compile(r"^[0-9a-f]{40}$")


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

    @property
    def failed(self):
        return bool(self.failures)


def check_registry(registry_dir="registry"):
    registry_path = Path(registry_dir).resolve()
    if not registry_path.is_dir():
        raise SbomCheckError(
            f"Registry directory does not exist: {registry_path}"
        )
    summary = CheckSummary()
    for bundle_path in sorted(registry_path.glob("*/*/bundle.json")):
        summary.scanned += 1
        check_bundle(bundle_path, summary)
    if not summary.scanned:
        raise SbomCheckError(
            f"No bundle.json files found under {registry_path}"
        )
    return summary


def check_bundle(bundle_path, summary):
    bundle_dir = bundle_path.parent
    name = f"{bundle_dir.parent.name}@{bundle_dir.name}"
    bundle = read_json(bundle_path, summary, name)
    evidence_path = bundle_dir / "build-evidence.json"
    sbom_path = bundle_dir / "sbom.cdx.json"
    evidence = read_json(evidence_path, summary, name)
    sbom = read_json(sbom_path, summary, name)
    if not all((bundle, evidence, sbom)):
        return

    errors = JsonStrictValidator(SchemaVersion.V1_7).validate_str(
        sbom_path.read_text(encoding="utf-8"),
        all_errors=True,
    )
    for error in errors or []:
        fail(summary, name, "SBOM_SCHEMA", str(error))
    if errors:
        return

    operation_id = bundle_dir.parent.name
    version = bundle_dir.name
    operation = evidence.get("operation") or {}
    recipe = evidence.get("recipe") or {}
    require(
        summary,
        name,
        evidence.get("schema") == "biochef.build-evidence.v1",
        "EVIDENCE_SCHEMA",
        "build-evidence.json has an unsupported schema",
    )
    for actual, expected, code, label in (
        (bundle.get("id"), operation_id, "BUNDLE_ID", "bundle id"),
        (bundle.get("version"), version, "BUNDLE_VERSION", "bundle version"),
        (operation.get("id"), operation_id, "EVIDENCE_ID", "evidence operation id"),
        (recipe.get("version"), version, "EVIDENCE_VERSION", "evidence recipe version"),
    ):
        require(
            summary,
            name,
            actual == expected,
            code,
            f"{label} does not match registry path",
        )

    root = (sbom.get("metadata") or {}).get("component") or {}
    require(
        summary,
        name,
        root.get("name") == operation_id and root.get("version") == version,
        "SBOM_ROOT",
        "SBOM root component does not match the bundle",
    )

    components = {
        item.get("bom-ref"): item
        for item in sbom.get("components") or []
        if isinstance(item, dict)
    }
    refs = [item.get("bom-ref") for item in sbom.get("components") or []]
    require(
        summary,
        name,
        len(refs) == len(set(refs)),
        "DUPLICATE_COMPONENT",
        "SBOM contains duplicate component references",
    )

    expected_files = {
        "bundle.json": f"sha256:{sha256_hex(bundle_path)}",
        "build-evidence.json": f"sha256:{sha256_hex(evidence_path)}",
    }
    for item in (evidence.get("license") or {}).get("files") or []:
        expected_files[item.get("path")] = item.get("digest")
    for runtime_data in (evidence.get("runtimes") or {}).values():
        for item in (runtime_data.get("artifacts") or {}).get("files") or []:
            expected_files[item.get("path")] = item.get("digest")

    for relative, expected_digest in expected_files.items():
        check_file(
            bundle_dir,
            relative,
            expected_digest,
            components,
            summary,
            name,
        )

    actual_shipped = {
        path.relative_to(bundle_dir).as_posix()
        for path in bundle_dir.rglob("*")
        if path.is_file()
        and not path.is_symlink()
        and path.name != "sbom.cdx.json"
    }
    require(
        summary,
        name,
        actual_shipped == set(expected_files),
        "BUNDLE_INVENTORY",
        "Build evidence does not enumerate every shipped bundle file: "
        f"missing={sorted(actual_shipped - set(expected_files))}, "
        f"absent={sorted(set(expected_files) - actual_shipped)}",
    )

    license_data = evidence.get("license") or {}
    require(
        summary,
        name,
        license_data.get("verified") is True,
        "LICENSE_EVIDENCE",
        f"License evidence is missing or incomplete: {license_data.get('missing')}",
    )

    hub = evidence.get("hub") or {}
    require(
        summary,
        name,
        bool(COMMIT.fullmatch(str(hub.get("commit", "")))),
        "HUB_COMMIT",
        "Build evidence has no exact BioCHEF Hub commit",
    )
    require(
        summary,
        name,
        hub.get("dirty") is False,
        "HUB_DIRTY",
        "Bundle was built from a dirty BioCHEF Hub worktree",
    )

    runtimes = evidence.get("runtimes") or {}
    require(
        summary,
        name,
        isinstance(runtimes, dict) and bool(runtimes),
        "RUNTIME_EVIDENCE",
        "Build evidence has no runtime observations",
    )
    for runtime, runtime_data in runtimes.items():
        check_runtime(
            runtime,
            runtime_data.get("build") or {},
            recipe.get("source") or {},
            summary,
            name,
        )


def check_runtime(runtime, build, declared_source, summary, bundle_name):
    require(
        summary,
        bundle_name,
        isinstance(build.get("builder"), str) and bool(build.get("builder")),
        "BUILDER_IDENTITY",
        f"{runtime} build has no builder identity",
    )
    source = build.get("source") or {}
    actual = source.get("actual") or source
    require(
        summary,
        bundle_name,
        actual.get("kind") in {"git", "vendored"},
        "SOURCE_IDENTITY",
        f"{runtime} build has no actual source identity",
    )
    if declared_source.get("repo"):
        require(
            summary,
            bundle_name,
            normalize_repo(actual.get("repo"))
            == normalize_repo(declared_source["repo"]),
            "SOURCE_REPOSITORY",
            f"{runtime} actual source repository differs from the recipe",
        )
        require(
            summary,
            bundle_name,
            actual.get("commit") == declared_source.get("commit"),
            "SOURCE_COMMIT",
            f"{runtime} actual source commit differs from the recipe",
        )
    elif declared_source.get("url"):
        digests = {
            item.get("digest")
            for item in actual.get("files") or []
        }
        require(
            summary,
            bundle_name,
            f"sha256:{declared_source.get('sha256')}" in digests,
            "SOURCE_DIGEST",
            f"{runtime} source files do not contain the declared source digest",
        )

    toolchain = build.get("toolchain") or {}
    require(
        summary,
        bundle_name,
        bool(toolchain.get("name")) and bool(toolchain.get("version")),
        "TOOLCHAIN_IDENTITY",
        f"{runtime} build has no exact toolchain name and version",
    )
    if runtime == "wasm":
        require(
            summary,
            bundle_name,
            bool(COMMIT.fullmatch(str(toolchain.get("commit", "")))),
            "TOOLCHAIN_COMMIT",
            "WASM build has no Emscripten source commit",
        )
    if build.get("builder") == "emscripten":
        require(
            summary,
            bundle_name,
            bool(COMMIT.fullmatch(str(toolchain.get("emsdk_commit", "")))),
            "EMSDK_COMMIT",
            "Direct Emscripten build has no exact emsdk commit",
        )

    if build.get("builder") == "biowasm":
        image = build.get("builder_image") or {}
        framework = build.get("framework") or {}
        require(
            summary,
            bundle_name,
            is_sha256_digest(image.get("image_id")),
            "BUILDER_IMAGE",
            "BioWASM builder image has no immutable image ID",
        )
        requested_image = image.get("requested_reference")
        if isinstance(requested_image, str) and "@sha256:" in requested_image:
            require(
                summary,
                bundle_name,
                image.get("manifest_digest")
                == f"sha256:{requested_image.rsplit('@sha256:', 1)[1]}",
                "BUILDER_IMAGE",
                "BioWASM builder image manifest digest does not match its reference",
            )
        require(
            summary,
            bundle_name,
            bool(COMMIT.fullmatch(str(framework.get("commit", "")))),
            "BIOWASM_COMMIT",
            "BioWASM framework has no exact commit",
        )
        require(
            summary,
            bundle_name,
            (build.get("isolation") or {}).get("containerized") is True,
            "BUILD_ISOLATION",
            "BioWASM build was not recorded as containerized",
        )

    for dependency in build.get("dependencies") or []:
        dep_source = dependency.get("source") or {}
        require(
            summary,
            bundle_name,
            dep_source.get("kind") in {"git", "vendored"}
            and (
                bool(COMMIT.fullmatch(str(dep_source.get("commit", ""))))
                or is_sha256_digest(dep_source.get("tree_digest"))
            ),
            "DEPENDENCY_IDENTITY",
            f"Build dependency lacks immutable identity: {dependency.get('name')}",
        )


def check_file(
    bundle_dir,
    relative,
    expected_digest,
    components,
    summary,
    bundle_name,
):
    if not safe_relative(relative):
        fail(summary, bundle_name, "UNSAFE_PATH", f"Unsafe bundle path: {relative}")
        return
    path = (bundle_dir / relative).resolve()
    try:
        path.relative_to(bundle_dir.resolve())
    except ValueError:
        fail(summary, bundle_name, "UNSAFE_PATH", f"Escaping bundle path: {relative}")
        return
    if path.is_symlink() or not path.is_file():
        fail(summary, bundle_name, "MISSING_FILE", f"Missing bundle file: {relative}")
        return
    actual = f"sha256:{sha256_hex(path)}"
    require(
        summary,
        bundle_name,
        is_sha256_digest(expected_digest) and actual == expected_digest,
        "FILE_DIGEST",
        f"Digest mismatch for {relative}",
    )
    component = components.get(f"file:{relative}") or {}
    hashes = {
        f"sha256:{item.get('content')}"
        for item in component.get("hashes") or []
        if item.get("alg") == "SHA-256"
    }
    require(
        summary,
        bundle_name,
        actual in hashes,
        "SBOM_FILE_DIGEST",
        f"SBOM does not contain the exact digest for {relative}",
    )


def normalize_repo(value):
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    if parsed.scheme:
        host = parsed.netloc.lower()
        path = parsed.path
    elif value.startswith("git@") and ":" in value:
        host, path = value[4:].split(":", 1)
    else:
        return value.rstrip("/").removesuffix(".git").lower()
    host = host.lower()
    path = path.strip("/")
    if host == "git.savannah.gnu.org":
        path = path.removeprefix("git/")
    return f"{host}/{path}".removesuffix(".git").lower()


def safe_relative(value):
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = Path(value)
    return not path.is_absolute() and ".." not in path.parts


def read_json(path, summary, bundle_name):
    if path.is_symlink():
        fail(summary, bundle_name, "UNSAFE_JSON", f"Symlinked JSON file: {path.name}")
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(summary, bundle_name, "INVALID_JSON", f"{path.name}: {exc}")
        return None
    if not isinstance(document, dict):
        fail(summary, bundle_name, "INVALID_JSON", f"{path.name} is not an object")
        return None
    return document


def require(summary, bundle, condition, code, message):
    if not condition:
        fail(summary, bundle, code, message)


def fail(summary, bundle, code, message):
    summary.failures.append(CheckIssue(bundle=bundle, code=code, message=message))
