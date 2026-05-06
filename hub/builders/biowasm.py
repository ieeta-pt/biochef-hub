import subprocess
import os
import shutil
import hashlib
import requests

# CDN-first hybrid builder.
#
# biowasm publishes prebuilt wasm artifacts to its own CDN, built by their CI in
# the right Emscripten container with the right system packages and the right
# inter-tool dependency ordering (htslib before bcftools/samtools/ivar, etc.).
# The hub used to clone WildBunnie/biowasm and re-compile from source on every
# build, which trips on environment differences (libcurl detection, emsdk
# version, missing build tools). When the CDN already has the binary, the right
# move is to fetch it and verify checksums.
#
# Falls back to clone+compile if the binary isn't on the CDN or the checksum
# verification fails.

CDN_BASE = "https://biowasm.com/cdn/v3"
MANIFEST_URL = "https://raw.githubusercontent.com/biowasm/biowasm/main/biowasm.manifest.json"
BIOWASM_JSON_URL = "https://raw.githubusercontent.com/biowasm/biowasm/main/biowasm.json"

# Default file list when biowasm.json can't be loaded. Each tool's biowasm.json
# entry overrides this via its own `files` array — some tools ship `["js"]` only
# (e.g. aioli, the JS runtime) and others add `["data"]` for preloaded fixtures.
DEFAULT_FILE_EXTS = ["js", "wasm", "data"]


def _load_manifest():
    """biowasm.manifest.json maps `<tool>/<version>/<file>` to `<path>:<md5>`."""
    try:
        r = requests.get(MANIFEST_URL, timeout=30)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"[biowasm] manifest fetch failed: {e}")
    return {}


def _load_biowasm_json():
    """biowasm.json declares each tool's `programs` list (tools with multiple
    binaries like bcftools+plugins or ASTER's astral/wastral)."""
    try:
        r = requests.get(BIOWASM_JSON_URL, timeout=30)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"[biowasm] biowasm.json fetch failed: {e}")
    return {}


def _programs_for(biowasm_json, tool_name):
    for tool in biowasm_json.get("tools", []):
        if tool.get("name") == tool_name:
            return tool.get("programs", [tool_name])
    return [tool_name]


def _files_for(biowasm_json, tool_name):
    """Per-tool file list from biowasm.json. Tools like aioli only ship `js`;
    most ship `js`+`wasm`+optionally `data`."""
    for tool in biowasm_json.get("tools", []):
        if tool.get("name") == tool_name:
            return tool.get("files", DEFAULT_FILE_EXTS)
    return DEFAULT_FILE_EXTS


def _md5_of(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _fetch_one(url, dest):
    """Fetch one file. Returns True on 200, False on any non-200, on a
    network-level error, or on a write failure. Never raises."""
    try:
        r = requests.get(url, timeout=120)
    except requests.RequestException as e:
        print(f"[biowasm] network error fetching {url}: {e}")
        return False
    if r.status_code != 200:
        return False
    try:
        with open(dest, "wb") as f:
            f.write(r.content)
    except OSError as e:
        print(f"[biowasm] write error for {dest}: {e}")
        return False
    return True


_WASM_MAGIC = b"\x00asm"


def _looks_like_wasm(path):
    try:
        with open(path, "rb") as f:
            return f.read(4) == _WASM_MAGIC
    except Exception:
        return False


def _try_cdn(tool_name, version, dest_dir):
    """Try to fetch tool/version from biowasm CDN with checksum verification.
    Returns True on success, False to trigger source-build fallback.

    On any failure, dest_dir is wiped before returning so a downstream fallback
    starts from a clean slate (no stale partial-fetch files getting merged with
    source-built output)."""
    manifest = _load_manifest()
    biowasm_json = _load_biowasm_json()
    programs = _programs_for(biowasm_json, tool_name)
    file_exts = _files_for(biowasm_json, tool_name)

    if not manifest:
        print("[biowasm] WARNING: manifest unavailable; checksum verification disabled")

    # Always start from a clean slate so partial fetches from previous attempts
    # don't survive into the next run.
    if os.path.isdir(dest_dir):
        shutil.rmtree(dest_dir)
    os.makedirs(dest_dir, exist_ok=True)

    fetched_any = False
    unverified_files = []

    def _fail(reason):
        print(f"[biowasm] {reason}")
        if os.path.isdir(dest_dir):
            shutil.rmtree(dest_dir)
        return False

    for program in programs:
        for ext in file_exts:
            key = f"{tool_name}/{version}/{program}.{ext}"
            url = f"{CDN_BASE}/{tool_name}/{version}/{program}.{ext}"
            dest = os.path.join(dest_dir, f"{program}.{ext}")
            ok = _fetch_one(url, dest)
            if not ok:
                # .data is optional — its absence isn't a failure.
                if ext == "data":
                    continue
                # Required file missing: CDN doesn't have this tool/version.
                return _fail(f"CDN miss: {url}")
            fetched_any = True

            # Magic-byte sanity check on .wasm so a Cloudflare HTML error page
            # served as a 200 doesn't get accepted as a wasm binary.
            if ext == "wasm" and not _looks_like_wasm(dest):
                return _fail(f"non-wasm content at {url}")

            # Verify md5 if the manifest knows about this file.
            entry = manifest.get(key)
            if entry and ":" in entry:
                expected = entry.rsplit(":", 1)[1]
                actual = _md5_of(dest)
                if expected != actual:
                    return _fail(f"checksum mismatch on {key}: expected {expected}, got {actual}")
            else:
                unverified_files.append(key)

    if not fetched_any:
        return _fail("nothing fetched")

    if unverified_files:
        print(f"[biowasm] WARNING: {len(unverified_files)} file(s) fetched without checksum verification (manifest had no entry)")

    print(f"[biowasm] fetched {tool_name}/{version} from CDN ({len(programs)} program(s))")
    return True


def _build_from_source(tool_name, version, output_dir, biowasm_dir, biowasm_repo):
    """Original behaviour: clone WildBunnie/biowasm and run its compile.py."""
    if not os.path.isdir(biowasm_dir):
        subprocess.run(["git", "clone", biowasm_repo, biowasm_dir], check=True)

    base_dir = os.getcwd()
    os.chdir(biowasm_dir)
    subprocess.run(["python", "./bin/compile.py", "--tools", tool_name, "--versions", version])
    os.chdir(base_dir)

    if os.path.isdir(f"{biowasm_dir}/build"):
        shutil.copytree(f"{biowasm_dir}/build/{tool_name}/{version}", f"{output_dir}/{tool_name}", dirs_exist_ok=True)
        shutil.rmtree(f"{biowasm_dir}/build")
        return os.path.abspath(f"{output_dir}/{tool_name}")

    return ""


def build(tool_name, version, output_dir="build", biowasm_dir="biowasm", biowasm_repo="https://github.com/WildBunnie/biowasm"):
    dest_dir = os.path.join(output_dir, tool_name)

    # CDN-first.
    if _try_cdn(tool_name, version, dest_dir):
        return os.path.abspath(dest_dir)

    # Fall back to source build.
    print(f"[biowasm] falling back to source build for {tool_name}/{version}")
    return _build_from_source(tool_name, version, output_dir, biowasm_dir, biowasm_repo)
