import re
import subprocess
from pathlib import Path

from builders.bundle_evidence import file_digest
from builders.container import safe_child_path

# A hunk on a submodule's commit pointer. A plain `git diff` writes one for a
# submodule whose files were edited, and `git apply` accepts it and changes
# nothing, so the build would run without the edit while the evidence listed
# the patch as applied.
SUBMODULE_POINTER = re.compile(r"^[-+]Subproject commit ", re.MULTILINE)


def apply_patches(recipe_dir, patches, source_dir):
    """Applies a recipe's patches to a checked-out source tree, in order.

    Each patch is checked with `git apply --check` before anything is changed,
    so a patch that no longer matches upstream fails the build instead of
    leaving the tree half modified. That is the case a `sed -i` in a build
    script lets through: a pattern that matches nothing still succeeds.

    Returns the evidence for the patches applied: the declared path, the digest
    of the file, and its text, so the SBOM can carry the diff and sbom-check can
    tie the text to the digest without the recipe at hand.
    """
    applied = []
    if patches and (Path(source_dir) / ".gitmodules").is_file():
        # A patch may change a file inside a submodule, which is empty until it
        # is initialised. Build scripts that need submodules initialise them
        # themselves, and doing it again there is a no-op.
        subprocess.run(
            ["git", "-C", str(source_dir), "submodule", "update", "--init", "--recursive"],
            check=True,
        )
    for declared in patches or []:
        unresolved = Path(recipe_dir) / declared
        patch = safe_child_path(recipe_dir, declared, "patch")
        if unresolved.is_symlink() or not patch.is_file():
            raise RuntimeError(f"Patch is missing or unsafe: {declared}")
        try:
            text = patch.read_bytes().decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RuntimeError(f"Patch is not UTF-8 text: {declared}") from exc
        if SUBMODULE_POINTER.search(text):
            raise RuntimeError(
                f"Patch changes a submodule's commit instead of its files: {declared}. "
                "Generate it with `git diff --submodule=diff`."
            )

        for check in (["--check"], []):
            result = subprocess.run(
                ["git", "apply", *check, str(patch)],
                cwd=source_dir,
                capture_output=True,
                text=True,
            )
            if result.returncode:
                raise RuntimeError(
                    f"Patch does not apply: {declared}\n{result.stderr.strip()}"
                )
        print(f"Applied patch {declared}")
        applied.append(
            {"path": declared, "digest": file_digest(patch), "content": text}
        )
    return applied
