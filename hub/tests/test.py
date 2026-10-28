import os
import subprocess
import json
import base64
import shutil
import tempfile
from pathlib import Path
import random
import string

from utils.data_types import detect_data_type
from utils.type_definitions import get_example_inputs, is_binary_type

seed = random.randint(0, 10_000)
rnd = random.Random(seed)
example_param_values = {
    "string": ''.join(rnd.choices(string.ascii_lowercase, k=50)),
    "integer": rnd.randint(1, 5),
    "float": round(rnd.uniform(1, 100), 2),
}

def test_tools(registry_dir):
    registry_path = Path(registry_dir)

    failed = []

    print(f"[INFO] Starting tests with seed {seed}")

    for tool_dir in registry_path.iterdir():
        if not tool_dir.is_dir():
            continue

        for version_dir in tool_dir.iterdir():
            if not version_dir.is_dir():
                continue

            bundle_path = version_dir / "bundle.json"
            if not bundle_path.exists():
                print(f"Skipping {tool_dir.name}: no bundle.json in {version_dir.name}")
                continue

            with open(bundle_path) as f:
                tool_bundle = json.load(f)

            bin_name = tool_bundle["bin"]
            artifacts = {
                "native": version_dir / "runtime" / "native" / bin_name,
                "wasm": version_dir / "runtime" / "wasm" / f"{bin_name}.js",
            }
            runtimes = [runtime for runtime, artifact in artifacts.items()
                        if runtime in tool_bundle.get("runtime", {}) or artifact.is_file()]
            if not runtimes:
                print(f"[SKIP] No native or wasm artifact found for: {bin_name}")

            # Each runtime gets its own directory, and a failed run must not
            # prevent the other runtime or subsequent tools from being tested.
            for runtime in runtimes:
                try:
                    ok = test_tool_outputs(version_dir, tool_bundle, runtime)
                except Exception as err:
                    print(f"[Error] Testing {tool_bundle.get('name')} ({runtime}) raised {type(err).__name__}: {err}")
                    ok = False

                if not ok:
                    failed.append(f"{tool_bundle['name']} ({runtime})")

    return failed


example_inputs = get_example_inputs()

WASM_HARNESS = Path(__file__).resolve().parent / "run_wasm.js"


def safe(text):
    """Makes text printable.

    A tool's output reaches us as a JSON string decoded from whatever bytes it
    wrote, so it can contain lone surrogates. print() cannot encode those, and
    the resulting UnicodeEncodeError escaped test_tool_outputs and left every
    remaining recipe untested.
    """
    return (text or "").encode("utf-8", errors="replace").decode("utf-8")


def run_wasm_tool(wasm_path, argv, tool_input, input_files, tmp_path):
    """Runs a wasm tool through the Node harness.

    Returns (stdout, stderr), or None if the tool could not be run at all --
    which is a test failure rather than something to report as tool output.
    Files the tool produced are written into tmp_path, so the caller inspects
    the results exactly as it does for a native binary.
    """
    spec = {
        "argv": argv,
        "stdin": tool_input,
        "files": {
            name: base64.b64encode((tmp_path / name).read_bytes()).decode("ascii")
            for name in input_files
        },
    }

    spec_path = tmp_path / "_wasm_spec.json"
    spec_path.write_text(json.dumps(spec))

    try:
        result = subprocess.run(
            ["node", str(WASM_HARNESS), str(wasm_path), str(spec_path)],
            capture_output=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        print("[Error] Tool execution timed out")
        return None
    except FileNotFoundError:
        print("[Error] node is required to test wasm tools and was not found")
        return None

    try:
        run = json.loads(result.stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        print("[Error] The wasm harness produced no usable result")
        print(result.stdout.decode("utf-8", errors="replace")[:500])
        print(result.stderr.decode("utf-8", errors="replace")[:500])
        return None

    for name, contents in run.get("files", {}).items():
        # Inputs are written back too. A tool editing its input in place is a
        # normal pattern, and under the native runtime it writes straight into
        # this directory, so skipping them here would make the two runtimes
        # disagree about what the tool produced.
        if name == spec_path.name:
            continue
        (tmp_path / name).write_bytes(base64.b64decode(contents))

    if not run.get("loaded"):
        if run.get("unsupportedEnvironment"):
            # Built without "node" in -sENVIRONMENT, so it refuses to start
            # outside a browser. That is a property of how it was compiled, not
            # evidence that the tool is broken, so it is skipped rather than
            # failed -- reporting it as a failure would turn every recipe built
            # this way red for a reason unrelated to the recipe.
            print("[SKIP] Built without node support, so it cannot be run here")
            return "", ""

        print("[Error] The wasm module failed to load")
        print(safe(run.get("stderr"))[:600])
        return None

    if not run.get("completed"):
        print("[Error] The wasm module trapped part way through")
        print(safe(run.get("stderr"))[:600])
        return None

    # A tool's own non-zero status is only reported, matching how the native
    # path treats it, since some tools exit non-zero by design.
    if run.get("exitCode"):
        print(f"[WARNING] Tool exited with status {run['exitCode']}")

    return run.get("stdout", ""), run.get("stderr", "")

def test_tool_outputs(tool_dir, tool_bundle, runtime):
    base_dir = os.getcwd()

    print(f"[INFO] Testing tool '{tool_bundle['name']}' ({runtime})")

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Copy runtime folder so binary works
            runtime_src = Path(tool_dir) / "runtime"
            runtime_dst = tmp_path / "runtime"
            if runtime_src.exists():
                shutil.copytree(runtime_src, runtime_dst)

            os.chdir(tmp_path)

            bin_name = tool_bundle.get("bin")
            bin_path = tmp_path / "runtime" / "native" / bin_name
            wasm_path = tmp_path / "runtime" / "wasm" / f"{bin_name}.js"

            artifact = bin_path if runtime == "native" else wasm_path
            if not artifact.is_file():
                print(f"[Error] Missing {runtime} artifact for: {bin_name}")
                return False

            tool_input = ""
            input_files = []
            expected_outputs = {}
            # Keep positional files after flags, as the frontend does.
            trailing = []
            cmd = [bin_name] if runtime == "native" else []

            # Parameters
            for parameter in tool_bundle.get("parameters", []):
                if not parameter.get("required"):
                    continue

                if parameter.get("flag"):
                    cmd.append(parameter["flag"])

                if parameter.get("default") is not None:
                    cmd.append(str(parameter["default"]))
                    continue

                if parameter.get("type") in example_param_values:
                    cmd.append(str(example_param_values[parameter["type"]]))

            # Inputs
            for input_def in tool_bundle["io"]["inputs"]:
                input_type = input_def["types"][0]

                if input_type not in example_inputs:
                    print(f"[WARNING] No example for input {input_def['name']} ({input_type})")
                    continue

                example = example_inputs[input_type]

                if input_def["mode"] == "stdin":
                    # The harnesses pass stdin as text, which cannot carry
                    # a binary example.
                    if isinstance(example, bytes):
                        print(f"[WARNING] No text example for stdin input {input_def['name']} ({input_type})")
                        continue
                    tool_input = example.rstrip("\n") + "\n"

                elif input_def["mode"] in ("file", "sidecar"):
                    # A declared filename is the name the tool sees, as in the
                    # app: a sidecar is found by it, next to the file it goes with.
                    file_name = input_def.get("filename") or f"input_{input_def['name']}.txt"
                    file_path = tmp_path / file_name

                    if isinstance(example, bytes):
                        file_path.write_bytes(example)
                    else:
                        with open(file_path, "w") as f:
                            f.write(example.rstrip("\n") + "\n")

                    input_files.append(file_name)

                    # A sidecar is mounted, never placed on the command line.
                    if input_def["mode"] == "sidecar":
                        continue

                    if input_def.get("flag"):
                        cmd.append(input_def["flag"])

                    # A wasm tool sees its own virtual filesystem, where the
                    # host path means nothing.
                    argument = file_name if runtime == "wasm" else str(file_path)
                    if input_def.get("flag"):
                        cmd.append(argument)
                    else:
                        trailing.append(argument)

                else:
                    print(f"[TODO] Unsupported input mode: {input_def}")
                    return False

            # Tell the tool where its declared file outputs should be written.
            for output_def in tool_bundle["io"]["outputs"]:
                if output_def["mode"] != "file":
                    continue
                flag = output_def.get("flag")
                if flag is None:
                    continue
                target = output_def.get("filename") or f"{output_def['name']}.txt"
                expected_outputs[output_def["name"]] = target
                if flag == "":
                    trailing.append(target)
                else:
                    cmd.extend([flag, target])

            cmd.extend(trailing)

            # Run tool
            print(f"Testing tool {tool_bundle['name']} ({runtime}) with command {cmd}")
            if runtime == "wasm":
                run = run_wasm_tool(wasm_path, cmd, tool_input, input_files, tmp_path)
                if run is None:
                    return False
                stdout, stderr = run
            else:
                try:
                    result = subprocess.run(
                        cmd,
                        executable=str(bin_path),
                        input=tool_input.encode("utf-8"),
                        capture_output=True,
                        timeout=10,
                    )
                except subprocess.TimeoutExpired:
                    print("[Error] Tool execution timed out")
                    return False

                stdout = result.stdout.decode("ascii", errors="replace")
                stderr = result.stderr.decode("ascii", errors="replace")
                if result.returncode < 0:
                    print(f"[Error] Tool terminated by signal {-result.returncode}")
                    print(safe(stderr)[:600])
                    return False
                if result.returncode:
                    print(f"[WARNING] Tool exited with status {result.returncode}")

            all_ok = True

            # Outputs
            for output_def in tool_bundle["io"]["outputs"]:
                output_name = output_def["name"]

                if output_def["mode"] == "stdout":
                    content = stdout

                elif output_def["mode"] == "file":
                    matched = None
                    declared_name = expected_outputs.get(output_name) or output_def.get("filename") or output_name
                    if (tmp_path / declared_name).is_file():
                        matched = tmp_path / declared_name
                    if not matched:
                        for f in tmp_path.iterdir():
                            if f.is_file() and f.stem.lower() == output_name.lower():
                                matched = f
                                break

                    if not matched:
                        print("[WARNING] Output file not found")
                        print(f"  Expected name: {output_name}")
                        continue

                    if all(is_binary_type(t) for t in output_def["types"]):
                        print(f"[INFO] Binary output found ({output_name}); format not checked")
                        continue
                    try:
                        content = matched.read_text()
                    except UnicodeDecodeError:
                        # A declaration can allow both text and binary formats,
                        if any(is_binary_type(t) for t in output_def["types"]):
                            print(f"[INFO] Binary output found ({output_name}); format not checked")
                            continue
                        print(f"[ERROR] Output is not UTF-8 text ({output_name})")
                        all_ok = False
                        continue

                else:
                    print(f"[TODO] Unsupported output mode: {output_def}")
                    all_ok = False
                    continue
                detected = detect_data_type(content, output_def["types"])

                if not detected:
                    print(f"[WARNING] Empty output ({output_name}, {tool_bundle['name']}, {cmd})")
                    print("stderr:")
                    print(safe(stderr).strip())
                elif detected not in output_def["types"]:
                    print(f"[ERROR] Unexpected output type ({output_name}, {tool_bundle['name']}, {cmd})")
                    print(f"  Detected : {detected}")
                    print(f"  Expected : {output_def['types']}")
                    all_ok = False

            return all_ok

    finally:
        os.chdir(base_dir)
