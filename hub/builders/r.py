from pathlib import Path
import docker
import io
import os
import tarfile

bclient = docker.from_env()

# The build runs in the prebuilt webR container rather than an image of ours.
# It already carries Emscripten, a wasm-targeting LLVM flang for the Fortran a
# large part of CRAN still contains, a host R, and rwasm itself. Reassembling
# that would mean maintaining a toolchain nobody here wants to own.
WEBR_IMAGE = "ghcr.io/r-wasm/webr:v{version}"

# Evaluated inside the container. Fixed text: the package list and dependency
# mode arrive as environment variables, so nothing from the recipe is parsed as
# R code.
#
# Two behaviours of rwasm are worked around here, both of which otherwise
# produce a build that reports success and ships nothing usable:
#
#  * add_pkg() resolves every reference in its built-in webr-remotes list on
#    each call, whatever was asked for, and that resolution currently fails in
#    this container. remotes = NULL skips it. That has a real cost: skipping
#    prefer_remotes() also gives up rwasm's webR-patched forks, so any package
#    in inst/webr-remotes (igraph, png, mvtnorm, shiny, vroom and about a dozen
#    others) would be built from unpatched CRAN source and may misbehave under
#    wasm. Such a package is refused below rather than built quietly.
#  * add_pkg() reduces a failed package build to a warning and carries on, so
#    it exits 0 having produced no binary. The requested packages are checked
#    against what actually landed.
BUILD_SCRIPT = r"""
pkgs <- trimws(strsplit(Sys.getenv("BIOCHEF_PACKAGES"), ",")[[1]])
pkgs <- pkgs[nzchar(pkgs)]
if (length(pkgs) == 0) stop("no packages requested")

deps <- switch(Sys.getenv("BIOCHEF_DEPENDENCIES"),
               "TRUE" = TRUE, "NA" = NA, "FALSE" = FALSE,
               stop("dependencies must be one of TRUE, NA, FALSE"))

repo <- "/output/repo"
dir.create(repo, recursive = TRUE, showWarnings = FALSE)

# Refuse anything rwasm keeps a patched fork of, rather than silently
# building the unpatched CRAN source (see the note above remotes = NULL).
patched <- tryCatch(
  readLines(system.file("webr-remotes", package = "rwasm")),
  error = function(e) character(0)
)
patched_names <- basename(sub("@.*$", "", patched))
clash <- intersect(basename(sub("@.*$", "", sub("^[^:]+::", "", pkgs))), patched_names)
if (length(clash) > 0) {
  stop("these packages need rwasm's webR-patched sources, which this build ",
       "cannot use: ", paste(clash, collapse = ", "))
}

rwasm::add_pkg(pkgs, repo_dir = repo, dependencies = deps, remotes = NULL)

built <- list.files(repo, pattern = "[.]tgz$", recursive = TRUE)
if (length(built) == 0) stop("no wasm binaries were produced")

# Reduce a pkgdepends reference to the package name it will be built under:
# cran::ape -> ape, ape@5.8.1 -> ape, r-wasm/rgl@webr -> rgl,
# url::https://host/foo_1.0.tar.gz -> foo. Without the basename() a GitHub or
# url reference never matches its own artefact, and a build that succeeded is
# reported as having produced nothing.
missing <- Filter(function(p) {
  name <- basename(sub("@.*$", "", sub("^[^:]+::", "", p)))
  name <- sub("_.*$", "", name)
  !any(startsWith(basename(built), paste0(name, "_")))
}, pkgs)
if (length(missing) > 0) {
  stop(paste("no wasm binary was produced for:", paste(missing, collapse = ", ")))
}

# A CRAN-like repository is not what webR mounts. The importable artefact is an
# Emscripten filesystem image built from it.
rwasm::make_vfs_library(out_dir = "/output/vfs", repo_dir = repo, compress = TRUE)

cat("built:", paste(basename(built), collapse = ", "), "\n")
"""


def copy_from_container(container, source_path, destination):
    """Copies the contents of a folder inside the container to the destination."""
    buffer = io.BytesIO()

    stream, _ = container.get_archive(source_path)
    for chunk in stream:
        buffer.write(chunk)

    buffer.seek(0)

    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)

    with tarfile.open(fileobj=buffer) as tar:
        members = tar.getmembers()

        for member in members:
            # Drop the top-level directory so only the contents are extracted
            path_parts = Path(member.name).parts
            member.name = str(Path(*path_parts[1:]))

        tar.extractall(destination, members)


def build(tool_name, r_settings, output_dir="build"):
    """Cross-compiles a recipe's R packages to wasm and packages them.

    Returns the directory holding the filesystem image, or None if the build
    failed.
    """
    packages = r_settings["packages"]
    dependencies = r_settings.get("dependencies", "FALSE")
    image = WEBR_IMAGE.format(version=r_settings["webrVersion"])

    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    dest_dir = output_dir / tool_name

    print(f"Building R packages for wasm in {image}: {', '.join(packages)}")

    container = bclient.containers.run(
        image=image,
        # The image runs as root and writes to /output inside its own layer.
        # Nothing is bind-mounted, so the artefacts are copied out afterwards
        # rather than written into the host filesystem as root.
        command=["Rscript", "--vanilla", "-e", BUILD_SCRIPT],
        environment={
            "BIOCHEF_PACKAGES": ",".join(packages),
            "BIOCHEF_DEPENDENCIES": dependencies,
        },
        detach=True,
    )

    try:
        for line in container.logs(stream=True):
            print(line.decode(), end="")

        result = container.wait()

        if result["StatusCode"] != 0:
            print(f"R build failed with code {result['StatusCode']}")
            return None

        copy_from_container(container, "/output/vfs", dest_dir)
    finally:
        container.remove()

    return dest_dir
