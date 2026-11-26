
import json
import tempfile
from pathlib import Path

import yaml

from publish.publish import get_oras_client


def generate_execution_times_template(
    tool_repo,
    registry_url,
    output,
):
    oras_client = get_oras_client(registry_url)

    with tempfile.TemporaryDirectory() as tmpdir:
        oras_client.pull(
            target=f"{registry_url}/biochef-plugins-{tool_repo}:latest",
            outdir=tmpdir,
        )

        bundle = json.loads(
            (Path(tmpdir) / "bundle.json").read_text()
        )

    # Inputs declared by the bundle.
    has_stdin = False
    for item in bundle.get("io", {}).get("inputs", []):
        if item.get("mode") == "stdin":
            has_stdin = True

    command = ""
    for param in bundle.get("parameters", []):
        if param.get("hidden") and param.get("required"):
            command += f"{param.get("default")} "

    scenario = {
        "name": "default",
        "files": [],
        "command": command.strip(),
        "stdout_mode": "file",
    }

    if has_stdin:
        scenario["stdin_file"] = ""

    template = {
        "tool": bundle["id"],
        "runs": 5,
        "warmup": 2,
        "substrates": [
            "native",
            "nodejs",
            "browser_firefox",
            "browser_chrome",
        ],
        "scenarios": [scenario],
    }

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    output.write_text(
        yaml.safe_dump(
            template,
            sort_keys=False,
            default_flow_style=False,
            indent=2,
        )
    )

    print(f"Benchmark template written to {output}")

    return output
