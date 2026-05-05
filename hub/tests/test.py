import os
import subprocess
import json
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
    "integer": rnd.randint(0, 5),
    "float": round(rnd.uniform(0, 100), 2),
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

            if not test_tool_outputs(version_dir, tool_bundle):
                failed.append(tool_bundle["name"])

    return failed


example_inputs = get_example_inputs()

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

            if not bin_path.is_file():
                print(f"[SKIP] Binary not found: {bin_name}")
                return False

            tool_input = ""
            cmd = [str(bin_path)]

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

                    if input_def.get("flag"):
                        cmd.append(input_def["flag"])

                    cmd.append(str(file_path))

                else:
                    print(f"[TODO] Unsupported input mode: {input_def}")
                    return False

            # Run tool
            print(f"Testing tool {tool_bundle['name']} with command {cmd}")
            result = subprocess.run(
                cmd,
                input=tool_input.strip().encode("ascii") if tool_input else None,
                capture_output=True
            )

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
                    for f in tmp_path.iterdir():
                        if f.is_file() and f.stem.lower() == output_name.lower():
                            matched = f
                            break

                    if not matched:
                        print("[WARNING] Output file not found")
                        print(f"  Expected name: {output_name}")
                        all_ok = False
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
                    print(stderr.strip())
                elif detected not in output_def["types"]:
                    print(f"[ERROR] Unexpected output type ({output_name}, {tool_bundle['name']}, {cmd})")
                    print(f"  Detected : {detected}")
                    print(f"  Expected : {output_def['types']}")
                    all_ok = False

            return all_ok

    finally:
        os.chdir(base_dir)