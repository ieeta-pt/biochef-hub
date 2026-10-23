import io
import json
import re
import shutil
import tarfile
import tempfile
from pathlib import Path

import docker
import requests

from builders.bundle_evidence import directory_tree_digest
from builders.container import (
    ContainerEvidenceError,
    extract_container_archive,
    file_digest,
    image_evidence,
    is_sha256_digest,
    safe_segment,
)
from builders.native import download_source_file


LOCK_FILE = Path(__file__).resolve().parent / "r-builder.lock.json"
DIGEST_REFERENCE = re.compile(r"^.+@sha256:[0-9a-f]{64}$")
SNAPSHOT_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
EMCC_COMMIT = re.compile(r"\(([0-9a-f]{40})\)")
# CRAN moves a version from src/contrib to src/contrib/Archive/<package> when the
# next one is released, so a URL that was right when the recipe was written stops
# resolving. The archived copy is the same file, and its digest is checked anyway.
CRAN_CONTRIB = re.compile(
    r"^(https://[^/?#]+/src/contrib)/([A-Za-z][A-Za-z0-9.]*)_([0-9][0-9.-]*)\.tar\.gz$"
)
LIBRARY_FILES = ("library.data.gz", "library.js.metadata")

# Evaluated inside the container. Fixed text: the source file name, snapshot and
# dependency mode arrive as environment variables, so nothing from the recipe is
# parsed as R code.
#
# rwasm::add_pkg() is not used. It resolves through
# pkgdepends::new_pkg_download_proposal(), which fails in this image with
# "`nrow(out)` must equal `1`" whatever the mirror. new_pkg_deps() resolves the
# same tree, so the resolution happens here and only rwasm's build step,
# update_repo(), is called.
#
# remotes = NULL means rwasm's webR-patched forks are not used, so a package it
# keeps a fork of would be built from unpatched CRAN source and may misbehave
# under wasm. Any such package anywhere in the resolved tree is refused.
BUILD_SCRIPT = r"""
source_file <- file.path("/input", Sys.getenv("BIOCHEF_SOURCE"))
snapshot <- Sys.getenv("BIOCHEF_CRAN_SNAPSHOT")
deps <- switch(Sys.getenv("BIOCHEF_DEPENDENCIES"),
               "TRUE" = TRUE, "NA" = NA, "FALSE" = FALSE,
               stop("dependencies must be one of TRUE, NA, FALSE"))

config <- list(cran_mirror = snapshot, platforms = "source", dependencies = deps)
plan <- pkgdepends::new_pkg_deps(paste0("local::", source_file), config = config)
plan$resolve()
resolution <- plan$get_resolution()
if (any(resolution$status != "OK")) {
  stop("could not resolve: ", paste(resolution$ref[resolution$status != "OK"], collapse = ", "))
}
info <- resolution[resolution$platform == "source" & !grepl("/Recommended/", resolution$target), ]
info <- info[!duplicated(info$package), ]
# Recommended packages such as lattice and nlme are installed in this image's
# own R, but webR's library holds only the base packages, so they are built
# like any other dependency.

remotes_file <- system.file("webr-remotes", package = "rwasm")
# The aliases below were checked against this exact list: a fork whose
# repository is not named after its package is otherwise missed, since rwasm
# substitutes by package name. A different list stops the build until they are
# checked again.
remotes_digest <- paste0("sha256:", sub(" .*$", "", system2("sha256sum", remotes_file, stdout = TRUE)))
if (!identical(remotes_digest, Sys.getenv("BIOCHEF_WEBR_REMOTES"))) {
  stop("rwasm's list of webR-patched packages is not the one reviewed (",
       remotes_digest, "); check which forks are named differently from ",
       "their package before updating webr_remotes in r-builder.lock.json")
}
patched <- readLines(remotes_file)
patched <- sub("@.*$", "", basename(patched))
# r-wasm/rigraph is the one fork in the list whose DESCRIPTION names another
# package: igraph.
patched[patched == "rigraph"] <- "igraph"
clash <- intersect(info$package, patched)
if (length(clash) > 0) {
  stop("these packages need rwasm's webR-patched sources, which this build ",
       "cannot use: ", paste(clash, collapse = ", "))
}

repo <- "/output/repo"
rwasm:::update_repo(info, remotes = NULL, repo_dir = repo, compress = TRUE)

# update_repo() reduces a failed package build to a warning and carries on.
bin_dir <- file.path(repo, "bin", "emscripten", "contrib")
built <- basename(list.files(bin_dir, pattern = "[.]tgz$", recursive = TRUE))
missing <- info$package[!vapply(info$package, function(p) {
  any(startsWith(built, paste0(p, "_")))
}, logical(1))]
if (length(missing) > 0) {
  stop("no wasm binary was produced for: ", paste(missing, collapse = ", "))
}

rwasm::make_vfs_library(out_dir = "/output/vfs", repo_dir = repo, compress = TRUE)

observations <- list(
  r_version = R.version.string,
  rwasm_version = as.character(packageVersion("rwasm")),
  emcc_version = system2("emcc", "--version", stdout = TRUE)[1],
  root = info$package[info$type == "local"],
  built = lapply(seq_len(nrow(info)), function(i) list(
    package = info$package[i],
    version = info$version[i],
    type = info$type[i],
    tarball = basename(info$target[i])
  ))
)
jsonlite::write_json(observations, "/output/observations.json", auto_unbox = TRUE, pretty = TRUE)
"""


def load_lock():
    try:
        lock = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not read R builder lock: {exc}") from exc
    for version, reference in (lock.get("webr") or {}).items():
        if not DIGEST_REFERENCE.fullmatch(str(reference)):
            raise RuntimeError(f"webR {version} image must be pinned by OCI digest")
    for version in lock.get("webr") or {}:
        if not is_sha256_digest((lock.get("webr_remotes") or {}).get(version)):
            raise RuntimeError(f"webR {version} has no reviewed webr-remotes digest")
    if not str(lock.get("cran_snapshot_base", "")).startswith("https://"):
        raise RuntimeError("CRAN snapshot base must be an HTTPS URL")
    return lock


def fetch_source(source, source_dir):
    try:
        return download_source_file(source, source_dir)
    except requests.HTTPError as exc:
        match = CRAN_CONTRIB.fullmatch(str(source.get("url", "")))
        if not match or exc.response is None or exc.response.status_code != 404:
            raise
        base, package, version = match.groups()
        archived = dict(source)
        archived["url"] = f"{base}/Archive/{package}/{package}_{version}.tar.gz"
        print(f"{source['url']} is gone; using {archived['url']}")
        return download_source_file(archived, source_dir)


def unpack_package(tarball, destination):
    """Unpacks an R source tarball and returns the package directory it holds."""
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tarball, "r:gz") as archive:
        roots = {Path(member.name).parts[0] for member in archive.getmembers()}
        if len(roots) != 1:
            raise RuntimeError(f"{tarball.name} must hold exactly one package directory")
        archive.extractall(destination, filter="data")
    return destination / roots.pop()


def tarball_bytes(path):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        info = archive.gettarinfo(str(path), arcname=f"input/{path.name}")
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        with path.open("rb") as handle:
            archive.addfile(info, handle)
    return buffer.getvalue()


def build(tool_name, recipe_dir, r_settings, declared_source, output_dir="build"):
    webr_version = r_settings["webrVersion"]
    snapshot_date = r_settings["cranSnapshot"]
    dependencies = r_settings.get("dependencies", "NA")
    evidence = {
        "builder": "r",
        "source": {"declared": declared_source},
        "toolchain": {"name": "webr", "requested_version": webr_version},
        "isolation": {
            "containerized": True,
            "capabilities_dropped": ["ALL"],
            "no_new_privileges": True,
            "network": "default",
        },
    }

    try:
        safe_name = safe_segment(tool_name, "R tool name")
        if not SNAPSHOT_DATE.fullmatch(str(snapshot_date)):
            raise RuntimeError("cranSnapshot must be a date, YYYY-MM-DD")
        lock = load_lock()
        reference = (lock.get("webr") or {}).get(webr_version)
        if not reference:
            raise RuntimeError(f"No pinned webR image for version {webr_version}")
        snapshot = f"{lock['cran_snapshot_base']}{snapshot_date}"
        evidence["configuration"] = {
            "cran_snapshot": snapshot,
            "dependencies": dependencies,
        }

        work_dir = Path(output_dir).resolve() / "_sources" / "r" / safe_name
        if work_dir.exists():
            shutil.rmtree(work_dir)
        download_dir = work_dir / "download"
        download_dir.mkdir(parents=True)
        actual_source = fetch_source(declared_source, download_dir)
        source_file = download_dir / actual_source["files"][0]["path"]
        package_dir = unpack_package(source_file, work_dir / "source")

        client = docker.from_env()
        try:
            image = client.images.get(reference)
        except docker.errors.ImageNotFound:
            image = client.images.pull(reference)
        evidence["builder_image"] = image_evidence(
            image,
            requested_reference=reference,
            context_digest=None,
        )

        print(f"Building {source_file.name} for webR {webr_version} against {snapshot}")
        container = client.containers.create(
            image=image.id,
            command=["Rscript", "--vanilla", "-e", BUILD_SCRIPT],
            environment={
                "BIOCHEF_SOURCE": source_file.name,
                "BIOCHEF_CRAN_SNAPSHOT": snapshot,
                "BIOCHEF_DEPENDENCIES": dependencies,
                "BIOCHEF_WEBR_REMOTES": lock["webr_remotes"][webr_version],
                # rwasm unpacks each source tarball as root, and with every
                # capability dropped tar cannot restore the original owners.
                # It still extracts every file, then exits 2 and rwasm reports
                # a warning per package.
                "TAR_OPTIONS": "--no-same-owner",
            },
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
        )
        try:
            # Copied in rather than bind-mounted, so the container never writes
            # into the host filesystem.
            container.put_archive("/", tarball_bytes(source_file))
            container.start()
            for line in container.logs(stream=True, follow=True):
                print(line.decode(errors="replace"), end="")
            status = container.wait()
            evidence["status"] = {"exit_code": status.get("StatusCode")}
            if status.get("StatusCode") != 0:
                raise RuntimeError(f"R build exited with {status.get('StatusCode')}")

            output = work_dir / "output"
            stream, _ = container.get_archive("/output")
            extract_container_archive(stream, output, expected_top_level="output")
        finally:
            container.remove(force=True)

        observations = json.loads((output / "observations.json").read_text(encoding="utf-8"))

        emcc_commit = EMCC_COMMIT.search(observations.get("emcc_version") or "")
        if not emcc_commit:
            raise RuntimeError("webR's emcc does not report an exact source commit")
        evidence["toolchain"].update(
            {
                "version": webr_version,
                "commit": emcc_commit.group(1),
                "emcc_version": observations["emcc_version"],
                "r_version": observations["r_version"],
                "rwasm_version": observations["rwasm_version"],
            }
        )

        # What was built must be the declared tarball. rwasm repacks it, so the
        # bytes differ; the unpacked files must not.
        contrib = output / "repo" / "src" / "contrib"
        with tempfile.TemporaryDirectory(prefix="biochef-r-built-") as temporary:
            root = observations["root"]
            built_root = next(
                item for item in observations["built"] if item["package"] == root
            )
            built_dir = unpack_package(contrib / built_root["tarball"], Path(temporary) / root)
            if directory_tree_digest(built_dir) != directory_tree_digest(package_dir):
                raise RuntimeError(
                    f"The built {root} source differs from the declared source tarball"
                )

            dependencies_evidence = []
            for item in observations["built"]:
                if item["package"] == root:
                    continue
                unpacked = unpack_package(
                    contrib / item["tarball"],
                    Path(temporary) / item["package"],
                )
                tree_digest, file_count = directory_tree_digest(unpacked)
                dependencies_evidence.append(
                    {
                        "name": item["package"],
                        "version": item["version"],
                        "source": {
                            "kind": "vendored",
                            "tree_digest": tree_digest,
                            "file_count": file_count,
                            "origin": snapshot,
                        },
                    }
                )

        evidence["source"]["actual"] = actual_source
        evidence["dependencies"] = dependencies_evidence

        library_dir = output / "vfs"
        for name in LIBRARY_FILES:
            artifact = library_dir / name
            if artifact.is_symlink() or not artifact.is_file():
                raise RuntimeError(f"R build output is missing or unsafe: {artifact}")
        evidence["library"] = {
            name: file_digest(library_dir / name) for name in LIBRARY_FILES
        }

        return {
            "output_dir": str(library_dir),
            "source_dir": str(package_dir),
            "evidence": evidence,
        }
    except (
        OSError,
        KeyError,
        StopIteration,
        json.JSONDecodeError,
        requests.RequestException,
        tarfile.TarError,
        docker.errors.DockerException,
        ContainerEvidenceError,
        RuntimeError,
        ValueError,
    ) as exc:
        print(f"Error building R packages: {exc}")
        evidence["error"] = str(exc)
        return {"output_dir": "", "evidence": evidence}
