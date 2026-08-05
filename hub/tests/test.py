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
from utils.type_definitions import get_example_inputs

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

            # Guarded so that one tool cannot end the run. Anything unexpected
            # here previously escaped as a traceback and left every remaining
            # recipe untested, which is the opposite of what this command is for.
            try:
                ok = test_tool_outputs(version_dir, tool_bundle)
            except Exception as err:
                print(f"[Error] Testing {tool_bundle.get('name')} raised {type(err).__name__}: {err}")
                ok = False

            if not ok:
                failed.append(tool_bundle["name"])

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
        "stdin": tool_input.strip() if tool_input else "",
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

def test_tool_outputs(tool_dir, tool_bundle):
    base_dir = os.getcwd()

    print(f"[INFO] Testing tool '{tool_bundle['name']}'")

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

            # Most of the catalogue has no native build, and those recipes used
            # to be skipped -- so a green run said only that the build had
            # produced a file of the right name, and anything that linked but
            # trapped at run time went unnoticed. Fall back to the wasm artifact,
            # which every recipe has.
            runtime = None
            if bin_path.is_file():
                runtime = "native"
            elif wasm_path.is_file():
                runtime = "wasm"
            else:
                print(f"[SKIP] No native or wasm artifact found for: {bin_name}")
                return True

            tool_input = ""
            input_files = []
            expected_outputs = {}
            # Arguments with no flag are held back and appended after every
            # flagged one, which is what the frontend does. Emitting a bare
            # filename first makes a getopt-style parser stop scanning, so the
            # flags that follow are never seen: tn93 given "in.txt -o out.txt"
            # prints its usage and exits 1, and given "-o out.txt in.txt" runs.
            trailing = []
            cmd = [str(bin_path)] if runtime == "native" else []

            # Parameters
            for parameter in tool_bundle.get("parameters", []):
                if not parameter.get("required"):
                    continue

                if parameter.get("flag"):
                    cmd.append(parameter["flag"])

                if parameter.get("default"):
                    cmd.append(str(parameter["default"]))
                    continue

                if parameter.get("type") in example_param_values:
                    cmd.append(str(example_param_values[parameter["type"]]))

            # Inputs
            for input_def in tool_bundle["io"]["inputs"]:
                input_type = input_def["types"][0]

                if input_type not in example_inputs:
                    continue

                if input_def["mode"] == "stdin":
                    tool_input = example_inputs[input_type]

                elif input_def["mode"] == "file":
                    file_name = f"input_{input_def['name']}.txt"
                    file_path = tmp_path / file_name

                    with open(file_path, "w") as f:
                        f.write(example_inputs[input_type])

                    input_files.append(file_name)

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

            # Outputs. Nothing previously told a tool where to write, so any
            # operation whose output is a file could only ever report that the
            # file was missing -- 45 of the 186 declared outputs. The shape
            # follows what the frontend does when it builds an invocation: a
            # flag and a target, or a bare target when the flag is empty.
            for output_def in tool_bundle["io"]["outputs"]:
                if output_def.get("mode") != "file":
                    continue

                flag = output_def.get("flag")
                if flag is None:
                    # No way to say where it should go; the tool decides.
                    continue

                target = output_def.get("filename") or f"{output_def['name']}.txt"
                expected_outputs[output_def["name"]] = target

                if flag == "":
                    trailing.append(target)
                else:
                    cmd.append(flag)
                    cmd.append(target)

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
                        input=tool_input.strip().encode("ascii") if tool_input else None,
                        capture_output=True,
                        timeout=10,
                    )
                except subprocess.TimeoutExpired:
                    print("[Error] Tool execution timed out")
                    return False

                stdout = result.stdout.decode("ascii", errors="replace")
                stderr = result.stderr.decode("ascii", errors="replace")

            all_ok = True

            # Outputs
            for output_def in tool_bundle["io"]["outputs"]:
                output_name = output_def["name"]

                if output_def["mode"] == "stdout":
                    content = stdout

                elif output_def["mode"] == "file":
                    matched = None

                    # The name the tool was told to write, when it was told.
                    asked_for = expected_outputs.get(output_name)
                    if asked_for and (tmp_path / asked_for).is_file():
                        matched = tmp_path / asked_for

                    # Otherwise fall back to guessing, which is all that was
                    # possible before the tool was given a target.
                    if not matched:
                        for f in tmp_path.iterdir():
                            if f.is_file() and f.stem.lower() == output_name.lower():
                                matched = f
                                break

                    if not matched:
                        print("[WARNING] Output file not found")
                        print(f"  Expected name: {output_name}")
                        continue

                    content = matched.read_text()

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