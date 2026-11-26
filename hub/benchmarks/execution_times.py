
import os
import csv
import json
import time
import shutil
import tempfile
import subprocess
from pathlib import Path
from threading import Thread
from http.server import HTTPServer, SimpleHTTPRequestHandler

import dotenv

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait

from publish.publish import get_oras_client

dotenv.load_dotenv()

REGISTRY_USERNAME = os.getenv("REGISTRY_USERNAME", "username")
REGISTRY_PASSWORD = os.getenv("REGISTRY_PASSWORD", "password")
REGISTRY_INSECURE = os.getenv("REGISTRY_INSECURE", "true").lower() == "true"
ORAS_AUTH_BACKEND = os.getenv("ORAS_AUTH_BACKEND", "token")
CATALOG_PACKAGE = os.getenv("CATALOG_PACKAGE", "biochef-plugins")

BROWSER_URL = os.getenv(
    "BROWSER_URL",
    "http://localhost:8000/benchmarks/execution_times_aioli.html"
)


def prepare_scenario_files(scenario, workdir):
    files = []

    for file in scenario.get("files", []):
        source = Path(file).resolve()

        if not source.is_file():
            raise FileNotFoundError(f"Scenario file not found: {source}")

        destination = Path(workdir) / source.name
        shutil.copy2(source, destination)
        files.append(destination)

    return files


def run_native(tool_bin, command, workdir, stdout_mode):
    output_path = Path(workdir) / "output.txt"

    if stdout_mode not in ("capture", "discard", "file"):
        raise ValueError(f"Invalid stdout_mode: {stdout_mode}")

    if stdout_mode == "capture":
        stdout_target = subprocess.PIPE
    elif stdout_mode == "discard":
        stdout_target = subprocess.DEVNULL
    else:
        stdout_target = open(output_path, "w")

    try:
        start = time.perf_counter_ns()

        result = subprocess.run(
            [str(tool_bin)] + command.split(),
            cwd=workdir,
            stdout=stdout_target,
            stderr=subprocess.PIPE,
            timeout=600,
        )

        elapsed = (time.perf_counter_ns() - start) / 1e6

    finally:
        if stdout_mode == "file":
            stdout_target.close()

    if result.returncode:
        raise subprocess.CalledProcessError(
            result.returncode,
            result.args,
            stderr=result.stderr,
        )

    return elapsed


def run_node(tool_bin, command, workdir, stdout_mode):
    spec_path = Path(workdir) / "spec.json"

    spec_path.write_text(json.dumps({
        "argv": command.split(),
        "cwd": str(Path(workdir).resolve()),
        "stdout_mode": stdout_mode,
        "output_file": "output.txt",
    }))

    env = os.environ.copy()
    env["NODE_OPTIONS"] = "--max-old-space-size=8192"

    result = subprocess.run(
        [
            "node",
            "benchmarks/execution_times_node.js",
            str(tool_bin.with_suffix(".js")),
            str(spec_path),
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Node.js execution failed (exit code {result.returncode})\n"
            f"STDOUT:\n{result.stdout}\n"
            f"STDERR:\n{result.stderr}"
        )

    result = json.loads(result.stdout)

    if not result["completed"] or result["exitCode"] != 0:
        raise RuntimeError(result["stderr"])

    return result["elapsed_ms"]


def run_browser(driver, tool_repo, command, files, stdout_mode):
    wait = WebDriverWait(driver, 600)

    wait.until(lambda d: d.find_element(By.ID, "toolSelect").is_enabled())

    Select(driver.find_element(By.ID, "toolSelect")).select_by_value(
        f"{CATALOG_PACKAGE}-{tool_repo}"
    )

    Select(driver.find_element(By.ID, "outputMode")).select_by_value(
        stdout_mode
    )

    for file in files:
        driver.find_element(By.ID, "inputFile").send_keys(
            str(Path(file).resolve())
        )

    args_input = driver.find_element(By.ID, "args")
    args_input.clear()
    args_input.send_keys(command)

    driver.execute_script(
        'document.getElementById("execution_time").textContent = "";'
    )

    driver.find_element(By.ID, "run").click()

    wait.until(lambda d: (
        d.find_element(By.ID, "execution_time").text.startswith("Elapsed:")
        and d.find_element(By.ID, "run").is_enabled()
    ))

    return float(
        driver.find_element(By.ID, "execution_time").text
        .replace("Elapsed:", "")
        .replace("ms", "")
        .strip()
    )


def benchmark(
    substrate,
    tool_repo,
    command,
    files,
    scenario_name,
    preparation_runs,
    measured_runs,
    stdout_mode,
    tool_bin=None,
    workdir=None,
    driver=None,
):
    results = []

    for run in range(1, preparation_runs + measured_runs + 1):

        if substrate == "native":
            elapsed = run_native(
                tool_bin, command, workdir, stdout_mode
            )

        elif substrate == "nodejs":
            elapsed = run_node(
                tool_bin, command, workdir, stdout_mode
            )

        else:
            elapsed = run_browser(
                driver, tool_repo, command, files, stdout_mode
            )

        run_type = (
            "preparation" if run <= preparation_runs else "measured"
        )

        run_number = (
            run if run_type == "preparation"
            else run - preparation_runs
        )

        print(
            f"[{substrate.upper()}] {scenario_name} | "
            f"{run_type} run {run_number} | {elapsed:.4f} ms"
        )

        if run_type == "measured":
            results.append({
                "tool": tool_repo,
                "scenario": scenario_name,
                "substrate": substrate,
                "run_number": run_number,
                "elapsed_ms": f"{elapsed:.4f}",
            })

    return results


def create_browser(browser, headless):
    if browser == "firefox":
        options = webdriver.FirefoxOptions()

        if headless:
            options.add_argument("--headless")

        driver = webdriver.Firefox(options=options)

    else:
        options = webdriver.ChromeOptions()

        if headless:
            options.add_argument("--headless=new")

        driver = webdriver.Chrome(options=options)

    driver.set_script_timeout(600)
    driver.get(BROWSER_URL)

    return driver


class SilentHTTPRequestHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


def benchmark_execution_times(
    benchmark_config,
    output,
    registry_url,
    headless,
):
    oras_client = get_oras_client(registry_url)

    tool_repo = benchmark_config["tool"]
    runs = benchmark_config["runs"]
    warmup = benchmark_config["warmup"]
    substrates = benchmark_config["substrates"]

    results = []

    with tempfile.TemporaryDirectory() as tmpdir:

        oras_client.pull(
            target=(
                f"{registry_url}/{CATALOG_PACKAGE}-"
                f"{tool_repo}:latest"
            ),
            outdir=tmpdir,
        )

        bundle = json.loads(
            (Path(tmpdir) / "bundle.json").read_text()
        )

        tool_bin = Path(tmpdir) / bundle["bin"]
        tool_bin.chmod(tool_bin.stat().st_mode | 0o111)

        httpd = HTTPServer(
            ("localhost", 8000),
            SilentHTTPRequestHandler,
        )

        server_thread = Thread(
            target=httpd.serve_forever,
            daemon=True,
        )

        server_thread.start()

        try:
            for scenario in benchmark_config["scenarios"]:
                scenario_name = scenario.get("name", "default")
                command = scenario["command"]
                stdout_mode = scenario.get("stdout_mode", "file")

                scenario_dir = Path(tmpdir) / scenario_name
                scenario_dir.mkdir()

                files = prepare_scenario_files(
                    scenario,
                    scenario_dir,
                )

                print(f"\nRunning scenario: {scenario_name}")
                print(f"Command: {command}")

                for substrate in substrates:

                    if substrate.startswith("browser_"):
                        browser = substrate.replace("browser_", "")
                        driver = create_browser(browser, headless)

                        try:
                            results.extend(benchmark(
                                substrate,
                                tool_repo,
                                command,
                                files,
                                scenario_name,
                                warmup,
                                runs,
                                stdout_mode,
                                driver=driver,
                            ))

                        finally:
                            driver.quit()

                    else:
                        results.extend(benchmark(
                            substrate,
                            tool_repo,
                            command,
                            files,
                            scenario_name,
                            warmup,
                            runs,
                            stdout_mode,
                            tool_bin=tool_bin,
                            workdir=scenario_dir,
                        ))

            with open(output, "w", newline="") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        "tool",
                        "scenario",
                        "substrate",
                        "run_number",
                        "elapsed_ms",
                    ],
                )

                writer.writeheader()
                writer.writerows(results)

            print(f"\nResults saved to {output}")

        finally:
            httpd.shutdown()
            server_thread.join()
            httpd.server_close()

    return results
