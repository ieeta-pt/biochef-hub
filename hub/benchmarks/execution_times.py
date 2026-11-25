
import os
import csv
import json
import time
import tempfile
import subprocess
from pathlib import Path
from threading import Thread
from http.server import HTTPServer, SimpleHTTPRequestHandler

import dotenv
import oras.client

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait


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


def run_native(tool_bin, args, input_file, tmpdir, stdout_mode):
    output_path = Path(tmpdir) / "output.txt"

    if stdout_mode not in ("capture", "discard", "file"):
        raise ValueError(f"Invalid stdout_mode: {stdout_mode}")

    if stdout_mode == "capture":
        stdout_target = subprocess.PIPE
    elif stdout_mode == "discard":
        stdout_target = subprocess.DEVNULL
    else:
        stdout_target = open(output_path, "w")

    try:
        with open(input_file, "rb") as f:
            start = time.perf_counter_ns()

            result = subprocess.run(
                [str(tool_bin)] + args,
                stdin=f,
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


def run_node(tool_bin, args, input_file, tmpdir, stdout_mode):
    spec_path = Path(tmpdir) / "spec.json"

    spec_path.write_text(json.dumps({
        "argv": args,
        "input_file": str(Path(input_file).resolve()),
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
        check=True,
    )

    result = json.loads(result.stdout)

    if not result["completed"] or result["exitCode"] != 0:
        raise RuntimeError(result["stderr"])

    return result["elapsed_ms"]


def run_browser(driver, tool_repo, args, input_file, stdout_mode):
    wait = WebDriverWait(driver, 600)

    wait.until(lambda d: d.find_element(By.ID, "toolSelect").is_enabled())

    Select(driver.find_element(By.ID, "toolSelect")).select_by_value(
        f"{CATALOG_PACKAGE}-{tool_repo}"
    )

    Select(driver.find_element(By.ID, "outputMode")).select_by_value(
        stdout_mode
    )

    driver.find_element(By.ID, "inputFile").send_keys(
        str(Path(input_file).resolve())
    )

    args_input = driver.find_element(By.ID, "args")
    args_input.clear()
    args_input.send_keys(" ".join(args))

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
    args,
    input_files,
    preparation_runs,
    measured_runs,
    stdout_mode,
    tool_bin=None,
    tmpdir=None,
    driver=None,
):
    results = []

    for input_file in input_files:
        for run in range(1, preparation_runs + measured_runs + 1):

            if substrate == "native":
                elapsed = run_native(
                    tool_bin, args, input_file, tmpdir, stdout_mode
                )

            elif substrate == "nodejs":
                elapsed = run_node(
                    tool_bin, args, input_file, tmpdir, stdout_mode
                )

            else:
                elapsed = run_browser(
                    driver, tool_repo, args, input_file, stdout_mode
                )

            run_type = (
                "preparation" if run <= preparation_runs else "measured"
            )

            run_number = (
                run if run_type == "preparation"
                else run - preparation_runs
            )

            print(
                f"[{substrate.upper()}] {input_file.name} | "
                f"{run_type} run {run_number} | {elapsed:.4f} ms"
            )

            if run_type == "measured":
                results.append({
                    "tool": tool_repo,
                    "file": input_file.name,
                    "file_size_bytes": input_file.stat().st_size,
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
    tool_repo,
    args,
    input_files,
    runs,
    warmup,
    output,
    substrates,
    stdout_mode,
    headless,
    registry_url,
):

    if substrates is None:
        substrates = [
            "native",
            "nodejs",
            "browser_firefox",
            "browser_chrome",
        ]

    if stdout_mode not in ("capture", "discard", "file"):
        raise ValueError(f"Invalid stdout_mode: {stdout_mode}")

    oras_client = oras.client.OrasClient(
        hostname=registry_url,
        insecure=REGISTRY_INSECURE,
        auth_backend=ORAS_AUTH_BACKEND,
    )

    oras_client.login(
        username=REGISTRY_USERNAME,
        password=REGISTRY_PASSWORD,
    )

    results = []

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
        input_files = sorted(
            [
                f for p in map(Path, input_files)
                for f in (p.iterdir() if p.is_dir() else [p])
                if f.is_file()
            ]
        )

        for substrate in substrates:

            if substrate.startswith("browser_"):
                browser = substrate.replace("browser_", "")
                driver = create_browser(browser, headless)

                try:
                    results.extend(benchmark(
                        substrate,
                        tool_repo,
                        args,
                        input_files,
                        preparation_runs=warmup,
                        measured_runs=runs,
                        stdout_mode=stdout_mode,
                        driver=driver,
                    ))

                finally:
                    driver.quit()

            else:
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

                    tool_bin.chmod(
                        tool_bin.stat().st_mode | 0o111
                    )

                    results.extend(benchmark(
                        substrate,
                        tool_repo,
                        args,
                        input_files,
                        preparation_runs=warmup,
                        measured_runs=runs,
                        stdout_mode=stdout_mode,
                        tool_bin=tool_bin,
                        tmpdir=tmpdir,
                    ))

        with open(output, "w", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "tool",
                    "file",
                    "file_size_bytes",
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
