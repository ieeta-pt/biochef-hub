import ast
import hashlib
import os
import re
import shlex
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from builders.bundle_evidence import (
    canonical_digest,
    is_sha256_digest,
    is_sha256_hex,
    sha256_hex,
)


SCHEMA = "biochef.wasm-link-evidence.v1"
_EXTRACTION_HEADER = "reference\textracted\tsymbol"
_ARCHIVE_MEMBER = re.compile(r"^(.*\.a)\((.*)\)$")
_JS_LIBRARY = re.compile(r"(?<!\S)-l([^\s]+\.js)(?=\s|$)")
_MAKE_DIRECTORY = re.compile(
    r"^make(?:\[(\d+)\])?: (Entering|Leaving) directory ['`](.*)['`]$"
)
_EMMAKE_DIRECTORY = re.compile(r'^emmake: .* in "([^"]+)"$')
_SHELL_ASSIGNMENT = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)=(?:\"([^\"]*)\"|'([^']*)'|([^\s#]+))",
    re.MULTILINE,
)
_SHELL_VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
_URL = re.compile(r"https?://[^\s\"'<>]+")
_PACKAGE_DIRECTORY = re.compile(r"^(.+?)-([0-9][0-9A-Za-z.+~-]*)$")


def traced_environment(environ=None):
    env = dict(os.environ if environ is None else environ)
    flags = env.get("EMCC_CFLAGS", "")
    if "--why-extract" in flags:
        raise ValueError("EMCC_CFLAGS already contains --why-extract")
    env["EMCC_CFLAGS"] = " ".join(
        part for part in (flags, "-Wl,--why-extract=/dev/stderr") if part
    )
    env["EMCC_VERBOSE"] = "1"
    return env


def run_and_capture(command, env, *, shell=False):
    link_events = []
    compiled_inputs = {}
    javascript_libraries = {}
    current_link = None
    reading_extractions = False
    initial_cwd = Path.cwd().resolve()
    emmake_directory = None
    make_directories = {}
    with subprocess.Popen(
        command,
        env=env,
        shell=shell,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        bufsize=1,
    ) as process:
        assert process.stdout is not None
        for line in process.stdout:
            stripped = line.rstrip("\n")
            directory_match = _MAKE_DIRECTORY.fullmatch(stripped.strip())
            if directory_match:
                level_value, action, directory = directory_match.groups()
                level = int(level_value or 0)
                if action == "Entering":
                    make_directories[level] = directory
                else:
                    for key in [key for key in make_directories if key >= level]:
                        make_directories.pop(key, None)
            else:
                emmake_match = _EMMAKE_DIRECTORY.fullmatch(stripped.strip())
                if emmake_match:
                    emmake_directory = emmake_match.group(1)
            link_cwd = Path(
                make_directories[max(make_directories)]
                if make_directories
                else emmake_directory or initial_cwd
            ).resolve()
            libraries = _JS_LIBRARY.findall(stripped)
            if libraries:
                javascript_libraries.setdefault(str(link_cwd), set()).update(libraries)
            compiler_input = _parse_compiler_input(stripped, link_cwd)
            if compiler_input:
                compiled_inputs[compiler_input["resolved_output"]] = compiler_input
            if "wasm-ld " in stripped:
                current_link = _link_event(
                    stripped,
                    link_cwd,
                    compiled_inputs,
                    javascript_libraries.get(str(link_cwd), set()),
                )
                if current_link:
                    link_events.append(current_link)
                reading_extractions = False
            if stripped == _EXTRACTION_HEADER:
                if current_link:
                    current_link["extraction_observed"] = True
                    reading_extractions = True
                continue
            if reading_extractions and current_link:
                fields = stripped.split("\t")
                if len(fields) == 3 and _ARCHIVE_MEMBER.fullmatch(fields[1]):
                    current_link["extractions"].append(fields)
                    continue
            reading_extractions = False
            print(line, end="", flush=True)
        returncode = process.wait()
    if returncode:
        raise subprocess.CalledProcessError(returncode, command)
    return {"link_events": link_events}


def collect_wasm_link_evidence(trace, *, source_root, expected_dir, default_link_cwd, input_root, build_inputs, source_evidence, framework_evidence, toolchain_evidence):
    source_root = Path(source_root).resolve()
    expected_dir = Path(expected_dir).resolve()
    default_link_cwd = Path(default_link_cwd).resolve()
    input_root = Path(input_root).resolve()
    emsdk_value = toolchain_evidence.get("emsdk_directory")
    emsdk_root = Path(emsdk_value).resolve() if emsdk_value else None
    expected_wasm_paths = sorted(expected_dir.rglob("*.wasm"))
    expected_outputs = [path.name for path in expected_wasm_paths]
    invocations = trace.get("link_events", []) if isinstance(trace, dict) else []
    expected_linker = (
        (emsdk_root / "upstream" / "bin" / "wasm-ld").resolve()
        if emsdk_root
        else None
    )
    final_invocations = [
        item
        for item in invocations
        if item["output"].endswith(".wasm")
        and "--relocatable" not in item["argv"]
        and expected_linker
        and Path(item["linker"]).resolve() == expected_linker
    ]

    errors = []
    outputs = []
    if not expected_outputs:
        errors.append("no final WebAssembly outputs were found")
    if not expected_linker:
        errors.append("the Emscripten SDK directory is unavailable")

    for output_path in expected_wasm_paths:
        output_name = output_path.name
        matches = [
            item
            for item in final_invocations
            if Path(item["output"]).name == output_name
        ]
        if not matches:
            errors.append(
                f"expected a final linker invocation for {output_name}, found 0"
            )
            continue
        # Emscripten may perform an earlier debug link before producing the
        # distributable output. The last matching invocation is the final one.
        invocation = matches[-1]
        output = _resolve_invocation(
            invocation,
            source_root=source_root,
            default_link_cwd=default_link_cwd,
            input_root=input_root,
            emsdk_root=emsdk_root,
            build_inputs=build_inputs,
            source_evidence=source_evidence,
            framework_evidence=framework_evidence,
            toolchain_evidence=toolchain_evidence,
        )
        output["artifacts"] = _runtime_artifact_digests(output_path)
        output["complete"] = not output["errors"]
        outputs.append(output)
        errors.extend(f"{output_name}: {message}" for message in output["errors"])

    return {
        "schema": SCHEMA,
        "method": "emcc-verbose-and-wasm-ld-why-extract",
        "complete": not errors,
        "errors": errors,
        "expected_outputs": expected_outputs,
        "outputs": outputs,
    }


def validate_wasm_link_evidence(document):
    errors = []
    if not isinstance(document, dict):
        return ["link evidence must be an object"]
    if document.get("schema") != SCHEMA:
        errors.append(f"schema must be {SCHEMA}")
    if not document.get("complete"):
        errors.append("link evidence is not complete")
    if document.get("errors"):
        errors.append("link evidence contains errors")

    expected_outputs = document.get("expected_outputs")
    outputs = document.get("outputs")
    if not isinstance(expected_outputs, list) or len(expected_outputs) != 1:
        errors.append("expected_outputs must contain exactly one operation output")
    if not isinstance(outputs, list) or len(outputs) != 1:
        errors.append("outputs must contain exactly one operation linker record")
        return errors

    output = outputs[0]
    if not isinstance(output, dict):
        errors.append("operation linker record must be an object")
        return errors
    if expected_outputs and output.get("output") != expected_outputs[0]:
        errors.append("operation linker output does not match expected_outputs")
    if not output.get("complete") or output.get("errors"):
        errors.append("operation linker record is not complete")
    if not is_sha256_digest(output.get("command_digest")):
        errors.append("operation linker record has invalid command_digest")

    artifacts = output.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        errors.append("operation linker record has no runtime artifact digests")
    else:
        names = set()
        for index, artifact in enumerate(artifacts):
            label = f"artifacts[{index}]"
            if not isinstance(artifact, dict) or not artifact.get("name"):
                errors.append(f"{label} has no artifact name")
                continue
            if artifact["name"] in names:
                errors.append(f"{label} duplicates artifact name {artifact['name']}")
            names.add(artifact["name"])
            if not is_sha256_digest(artifact.get("digest")):
                errors.append(f"{label} has an invalid SHA-256 digest")
        expected_wasm = expected_outputs[0] if expected_outputs else None
        expected_js = f"{Path(expected_wasm).stem}.js" if expected_wasm else None
        if expected_wasm not in names or expected_js not in names:
            errors.append("operation linker record must bind its WASM and JavaScript artifacts")

    for index, archive in enumerate(output.get("selected_archives") or []):
        label = f"selected_archives[{index}]"
        if not isinstance(archive, dict):
            errors.append(f"{label} must be an object")
            continue
        if not archive.get("path") or not is_sha256_hex(archive.get("sha256")):
            errors.append(f"{label} is missing its path or SHA-256")
        if not _identity_complete(archive.get("identity") or {}):
            errors.append(f"{label} has incomplete source identity")
        if not isinstance(archive.get("selected_member_count"), int) or archive.get(
            "selected_member_count", 0
        ) <= 0:
            errors.append(f"{label} has invalid selected member count")
        if not is_sha256_digest(archive.get("selected_members_digest")):
            errors.append(f"{label} has invalid selected members digest")

    for index, item in enumerate(output.get("direct_inputs") or []):
        label = f"direct_inputs[{index}]"
        if not isinstance(item, dict) or not item.get("path"):
            errors.append(f"{label} is missing its path")
            continue
        if not _identity_complete(item.get("identity") or {}):
            errors.append(f"{label} has incomplete source identity")
        if not is_sha256_hex(item.get("sha256")):
            errors.append(f"{label} has no exact linker-input SHA-256")

    output_libraries = output.get("javascript_libraries") or []
    if not isinstance(output_libraries, list) or not all(
        isinstance(item, str) and item for item in output_libraries
    ):
        errors.append("operation javascript_libraries must be a list of names")
    return errors


def _runtime_artifact_digests(wasm_path):
    artifacts = []
    for extension in ("wasm", "js"):
        path = wasm_path.with_suffix(f".{extension}")
        if path.is_file() and not path.is_symlink():
            artifacts.append(
                {
                    "name": path.name,
                    "digest": f"sha256:{sha256_hex(path)}",
                }
            )
    return artifacts


def _parse_compiler_input(line, active_directory):
    if " -c " not in f" {line} ":
        return None
    try:
        argv = shlex.split(line)
    except ValueError:
        return None
    if not argv or Path(argv[0]).name not in {"clang", "clang++"}:
        return None
    source = _argument_after(argv, "-c")
    output = _argument_after(argv, "-o")
    if not source or not output or not output.endswith(".o"):
        return None
    cwd = Path(active_directory).resolve()
    return {
        "source": source,
        "cwd": str(cwd),
        "resolved_output": str(_resolve_path(output, cwd)),
    }


def _link_event(line, link_cwd, compiled_inputs, javascript_libraries):
    try:
        line_argv = shlex.split(line)
        linker_index = next(
            index
            for index, value in enumerate(line_argv)
            if Path(value).name == "wasm-ld"
        )
    except (StopIteration, ValueError):
        return None

    argv = line_argv[linker_index:]
    output = _argument_after(argv, "-o")
    if not output:
        return None

    input_paths, input_errors = _link_input_paths(argv, link_cwd)
    input_hashes = {
        str(path): sha256_hex(path)
        for path in input_paths.values()
        if path.is_file() and not path.is_symlink()
    }
    return {
        "argv": argv,
        "linker": str(_resolve_path(argv[0], link_cwd)),
        "output": output,
        "cwd": str(link_cwd),
        "input_paths": {token: str(path) for token, path in input_paths.items()},
        "input_hashes": input_hashes,
        "input_errors": input_errors,
        "compiled_inputs": {
            path: compiled_inputs[path]
            for path in input_hashes
            if path in compiled_inputs
        },
        "javascript_libraries": sorted(javascript_libraries),
        "extraction_observed": False,
        "extractions": [],
    }


def _link_input_paths(argv, link_cwd):
    search_directories = [link_cwd]
    errors = []
    index = 1
    while index < len(argv):
        value = argv[index]
        if value == "-L" and index + 1 < len(argv):
            search_directories.append(_resolve_path(argv[index + 1], link_cwd))
            index += 2
            continue
        if value.startswith("-L") and len(value) > 2:
            search_directories.append(_resolve_path(value[2:], link_cwd))
        index += 1

    paths = {}
    output = _argument_after(argv, "-o")
    for value in argv[1:]:
        if value == output:
            continue
        if value.startswith("@"):
            errors.append(f"response-file linker input is unsupported: {value}")
            continue
        if value.endswith((".a", ".o", ".so", ".bc", ".wasm", ".obj")):
            paths[value] = _resolve_path(value, link_cwd)
            continue
        if not value.startswith("-l") or value.endswith(".js"):
            continue
        library = value[2:]
        filename = library[1:] if library.startswith(":") else f"lib{library}.a"
        for directory in search_directories:
            candidate = directory / filename
            if candidate.is_file() and not candidate.is_symlink():
                paths[value] = candidate.resolve()
                break
    return paths, errors


def _resolve_invocation(
    invocation,
    *,
    source_root,
    default_link_cwd,
    input_root,
    emsdk_root,
    build_inputs,
    source_evidence,
    framework_evidence,
    toolchain_evidence,
):
    errors = list(invocation.get("input_errors") or [])
    link_cwd = Path(invocation.get("cwd") or default_link_cwd).resolve()
    input_hashes = invocation.get("input_hashes") or {}
    selected = {}
    for _reference, extracted, _symbol in invocation["extractions"]:
        match = _ARCHIVE_MEMBER.fullmatch(extracted)
        if not match:
            continue
        raw_archive, member = match.groups()
        selected.setdefault(raw_archive, set()).add(member)

    selected_archives = []
    for raw_archive, members in sorted(selected.items()):
        resolved = _resolve_path(raw_archive, link_cwd)
        identity = _identity_for(
            resolved,
            source_root=source_root,
            input_root=input_root,
            emsdk_root=emsdk_root,
            build_inputs=build_inputs,
            source_evidence=source_evidence,
            framework_evidence=framework_evidence,
            toolchain_evidence=toolchain_evidence,
        )
        if identity.get("kind") == "emscripten-sdk":
            identity = _emscripten_port_identity(
                resolved,
                emsdk_root,
                toolchain_evidence,
            ) or identity
        archive_hash = input_hashes.get(str(resolved))
        if not archive_hash:
            errors.append(f"selected archive is unavailable for hashing: {raw_archive}")
        if not _identity_complete(identity):
            errors.append(f"selected archive has no source identity: {raw_archive}")
        selected_archives.append(
            {
                "path": _normalize_path(resolved, source_root, input_root, emsdk_root),
                "sha256": archive_hash,
                "selected_member_count": len(members),
                "selected_members_digest": canonical_digest(sorted(members)),
                "identity": identity,
            }
        )

    direct_inputs = []
    for token in invocation["argv"][1:]:
        if token == invocation["output"] or token.endswith(".a") or not token.endswith(
            (".o", ".so", ".bc", ".wasm", ".obj")
        ):
            continue
        resolved_value = (invocation.get("input_paths") or {}).get(token)
        if not resolved_value:
            errors.append(f"direct linker input was not captured: {token}")
            continue
        resolved = Path(resolved_value)
        normalized_input_path = _normalize_path(
            resolved, source_root, input_root, emsdk_root
        )
        identity = _identity_for(
            resolved,
            source_root=source_root,
            input_root=input_root,
            emsdk_root=emsdk_root,
            build_inputs=build_inputs,
            source_evidence=source_evidence,
            framework_evidence=framework_evidence,
            toolchain_evidence=toolchain_evidence,
        )
        compiler_input = (invocation.get("compiled_inputs") or {}).get(str(resolved))
        if resolved.name.endswith("libemscripten_js_symbols.so"):
            identity = {
                "kind": "emscripten-generated",
                "version": toolchain_evidence.get("emsdk_version"),
                "commit": toolchain_evidence.get("emsdk_resolved_commit"),
            }
        elif compiler_input and not _identity_complete(identity):
            compiler_cwd = Path(compiler_input.get("cwd") or link_cwd).resolve()
            source = _resolve_path(compiler_input["source"], compiler_cwd)
            identity = _identity_for(
                source,
                source_root=source_root,
                input_root=input_root,
                emsdk_root=emsdk_root,
                build_inputs=build_inputs,
                source_evidence=source_evidence,
                framework_evidence=framework_evidence,
                toolchain_evidence=toolchain_evidence,
            )
            normalized_input_path = f"generated://{Path(token).name}"
            if not source.is_file():
                errors.append(f"temporary linker input source is unavailable: {token}")
            elif not _identity_complete(identity):
                errors.append(f"temporary linker input source has no identity: {token}")
        input_hash = input_hashes.get(str(resolved))
        if not input_hash:
            errors.append(f"direct linker input is unavailable for hashing: {token}")
        elif not _identity_complete(identity):
            errors.append(f"direct linker input has no source identity: {token}")
        direct_inputs.append(
            {
                "path": normalized_input_path,
                "sha256": input_hash,
                "identity": identity,
            }
        )

    if not invocation["extraction_observed"]:
        errors.append("linker archive-extraction evidence was not observed")
    if not selected_archives and not direct_inputs:
        errors.append("linker invocation has no captured inputs")

    normalized_argv = [
        _normalize_argument(value, link_cwd, source_root, input_root, emsdk_root)
        for value in invocation["argv"]
        if not value.startswith("--why-extract=")
    ]
    return {
        "output": Path(invocation["output"]).name,
        "command_digest": canonical_digest(normalized_argv),
        "selected_archives": selected_archives,
        "javascript_libraries": invocation.get("javascript_libraries", []),
        "direct_inputs": direct_inputs,
        "errors": errors,
    }


def _identity_for(
    path,
    *,
    source_root,
    input_root,
    emsdk_root,
    build_inputs,
    source_evidence,
    framework_evidence,
    toolchain_evidence,
):
    submodules = sorted(
        (build_inputs or {}).get("git_submodules", []),
        key=lambda item: len(Path(item.get("path", "")).parts),
        reverse=True,
    )
    for item in submodules:
        root = (input_root / item["path"]).resolve()
        if _is_within(path, root):
            downloaded = _downloaded_source_identity(
                path,
                submodule_root=root,
                submodule=item,
                input_root=input_root,
                framework_evidence=framework_evidence,
            )
            if downloaded:
                return downloaded
            if _is_untracked_build_path(path, root, item):
                return {
                    "kind": "unresolved",
                    "reason": "selected input is inside an untracked build-time source directory",
                }
            return {
                "kind": "git-submodule",
                "repo": item.get("repo"),
                "commit": item.get("resolved_commit"),
                "path": item.get("path"),
            }
    if _is_within(path, source_root):
        actual = (source_evidence or {}).get("actual") or {}
        return {
            "kind": "primary-source",
            "repo": actual.get("repo"),
            "commit": actual.get("resolved_commit"),
            "directory_digest": actual.get("directory_digest"),
        }
    if emsdk_root and _is_within(path, emsdk_root):
        return {
            "kind": "emscripten-sdk",
            "version": toolchain_evidence.get("emsdk_version"),
            "commit": toolchain_evidence.get("emsdk_resolved_commit"),
        }
    if framework_evidence and _is_within(path, input_root):
        return {
            "kind": "build-framework-source",
            "repo": framework_evidence.get("repo"),
            "commit": framework_evidence.get("resolved_commit"),
        }
    return {"kind": "unresolved"}


def _identity_complete(identity):
    kind = identity.get("kind")
    if kind in {"git-submodule", "build-framework-source"}:
        return bool(identity.get("repo") and identity.get("commit"))
    if kind == "primary-source":
        return bool(
            (identity.get("repo") and identity.get("commit"))
            or identity.get("directory_digest")
        )
    if kind in {"emscripten-sdk", "emscripten-generated"}:
        return bool(identity.get("version") and identity.get("commit"))
    if kind == "emscripten-port":
        return bool(
            identity.get("name")
            and identity.get("version")
            and identity.get("source_url")
            and identity.get("source_sha512")
            and identity.get("source_archive_sha512") == identity.get("source_sha512")
        )
    if kind == "downloaded-source":
        return bool(
            identity.get("name")
            and identity.get("version")
            and identity.get("source_url")
            and is_sha256_hex(identity.get("source_sha256"))
            and identity.get("acquisition_repo")
            and identity.get("acquisition_commit")
            and is_sha256_hex(identity.get("acquisition_file_sha256"))
        )
    return False


def _downloaded_source_identity(
    path,
    *,
    submodule_root,
    submodule,
    input_root,
    framework_evidence,
):
    untracked = []
    for status in submodule.get("post_build_status") or []:
        if status.startswith("?? "):
            untracked.append(status[3:].rstrip("/"))

    source_dirs = []
    for value in untracked:
        candidate = (submodule_root / value).resolve()
        if candidate.is_dir() and _is_within(path, candidate):
            source_dirs.append(candidate)
    if not source_dirs:
        return None
    source_dir = max(source_dirs, key=lambda item: len(item.parts))

    match = _PACKAGE_DIRECTORY.fullmatch(source_dir.name)
    if not match:
        return None
    name, version = match.groups()
    archives = []
    for value in untracked:
        candidate = (submodule_root / value).resolve()
        if candidate.is_file() and candidate.name.startswith(f"{source_dir.name}."):
            archives.append(candidate)
    if len(archives) != 1:
        return None
    source_archive = archives[0]

    acquisition = _find_source_acquisition(
        source_archive.name,
        search_root=submodule_root.parent,
        excluded_root=submodule_root,
    )
    if not acquisition:
        return None
    acquisition_file, source_url = acquisition
    try:
        acquisition_path = acquisition_file.relative_to(input_root).as_posix()
    except ValueError:
        return None

    return {
        "kind": "downloaded-source",
        "name": name,
        "version": version,
        "source_url": source_url,
        "source_sha256": sha256_hex(source_archive),
        "acquisition_repo": (framework_evidence or {}).get("repo"),
        "acquisition_commit": (framework_evidence or {}).get("resolved_commit"),
        "acquisition_file": acquisition_path,
        "acquisition_file_sha256": sha256_hex(acquisition_file),
    }


def _is_untracked_build_path(path, submodule_root, submodule):
    for status in submodule.get("post_build_status") or []:
        if not status.startswith("?? "):
            continue
        candidate = (submodule_root / status[3:].rstrip("/")).resolve()
        if candidate.is_dir() and _is_within(path, candidate):
            return True
    return False


def _find_source_acquisition(archive_name, *, search_root, excluded_root):
    for candidate in sorted(search_root.rglob("*")):
        if not candidate.is_file() or _is_within(candidate, excluded_root):
            continue
        try:
            if candidate.stat().st_size > 1024 * 1024:
                continue
            source = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        variables = {
            match.group(1): next(
                value for value in match.groups()[1:] if value is not None
            )
            for match in _SHELL_ASSIGNMENT.finditer(source)
        }
        for raw_url in _URL.findall(source):
            source_url = _expand_shell_variables(raw_url, variables)
            if Path(urlparse(source_url).path).name == archive_name:
                return candidate, source_url
    return None


def _expand_shell_variables(value, variables):
    for _ in range(10):
        expanded = _SHELL_VARIABLE.sub(
            lambda match: variables.get(match.group(1) or match.group(2), match.group(0)),
            value,
        )
        if expanded == value:
            return expanded
        value = expanded
    return value


def _emscripten_port_identity(path, emsdk_root, toolchain_evidence):
    if not emsdk_root:
        return None
    ports_dir = emsdk_root / "upstream" / "emscripten" / "tools" / "ports"
    if not ports_dir.is_dir():
        return None

    archive_name = Path(path).name
    for definition in sorted(ports_dir.glob("*.py")):
        try:
            source = definition.read_text(encoding="utf-8")
        except OSError:
            continue
        if archive_name not in source:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        if not _port_builds_archive(tree, archive_name):
            continue
        constants = _module_string_constants(tree)
        version = constants.get("VERSION")
        source_sha512 = constants.get("HASH")
        port_name = definition.stem
        source_archive_sha512 = _verified_port_archive_hash(
            emsdk_root,
            port_name,
            source_sha512,
        )
        url_file = (
            emsdk_root
            / "upstream"
            / "emscripten"
            / "cache"
            / "ports"
            / port_name
            / ".emscripten_url"
        )
        try:
            source_url = url_file.read_text(encoding="utf-8").strip()
        except OSError:
            source_url = None
        if not all((version, source_sha512, source_url)):
            return None
        return {
            "kind": "emscripten-port",
            "name": port_name,
            "version": version,
            "source_url": source_url,
            "source_sha512": source_sha512,
            "source_archive_sha512": source_archive_sha512,
            "emsdk_version": toolchain_evidence.get("emsdk_version"),
            "emsdk_commit": toolchain_evidence.get("emsdk_resolved_commit"),
        }
    return None


def _verified_port_archive_hash(emsdk_root, port_name, expected_hash):
    if not expected_hash:
        return None
    ports_cache = emsdk_root / "upstream" / "emscripten" / "cache" / "ports"
    for candidate in sorted(ports_cache.glob(f"{port_name}*")):
        if not candidate.is_file() or candidate.name == ".emscripten_url":
            continue
        digest = hashlib.sha512()
        with candidate.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() == expected_hash:
            return expected_hash
    return None


def _port_builds_archive(tree, archive_name):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "get_lib" or not node.args:
            continue
        if isinstance(node.args[0], ast.Constant) and node.args[0].value == archive_name:
            return True
    return False


def _module_string_constants(tree):
    constants = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str):
                constants[target.id] = node.value.value
    return constants


def _normalize_argument(value, link_cwd, source_root, input_root, emsdk_root):
    if value.endswith((".a", ".o", ".so", ".bc", ".wasm", ".obj")):
        return _normalize_path(
            _resolve_path(value, link_cwd), source_root, input_root, emsdk_root
        )
    return value


def _normalize_path(path, source_root, input_root, emsdk_root):
    path = Path(path).resolve()
    for prefix, root in (
        ("source", source_root),
        ("framework", input_root if input_root != source_root else None),
        ("emsdk", emsdk_root),
    ):
        if root and _is_within(path, root):
            return f"{prefix}://{path.relative_to(root).as_posix()}"
    if path.name.endswith("libemscripten_js_symbols.so"):
        return "generated://libemscripten_js_symbols.so"
    return f"unresolved://{path.name}"


def _resolve_path(value, link_cwd):
    path = Path(value)
    return path.resolve() if path.is_absolute() else (link_cwd / path).resolve()


def _argument_after(argv, name):
    try:
        return argv[argv.index(name) + 1]
    except (ValueError, IndexError):
        return None


def _is_within(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False
