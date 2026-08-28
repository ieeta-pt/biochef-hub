"""Run the same operation on the WASM and the native build and compare them.

Every recipe that declares both runtimes promises that an operation means the
same thing whichever one runs it. The browser runs the WASM build through Aioli;
the Agent runs the native build as a subprocess. Nothing has ever checked that
the two agree, and the builds are not merely different compilers: on x86 the
native build is compiled `-march=native` against real SSE, while the WASM build
goes through emscripten's SSE-over-wasm-SIMD compatibility headers. Those are
two implementations of the same intrinsics.

What this does NOT do is rebuild the WASM artifact in a node-friendly
configuration. The published module is built for `web,worker`, and testing a
differently-built one would compare something nobody runs. See run_wasm.mjs.

Coverage, stated plainly so the report is not read as more than it is: this
builds ONE invocation per operation, from the operation's required parameters
and its declared example inputs. Anything reachable only through an optional
parameter is not exercised. ksw2 is the clearest case -- it selects among seven
alignment implementations, scalar and SSE, through an optional `-t`, so the
default path is all this compares. Covering those needs cases declared per
operation, which needs a recipe schema key that does not exist yet.
"""

import base64
import json
import os
import random
import shutil
import string
import subprocess
import tempfile
from pathlib import Path

from utils.type_definitions import get_example_inputs

NODE_RUNNER = Path(__file__).resolve().parent / "run_wasm.mjs"

# How much of a differing output to put in the report. Enough to see what went
# wrong, bounded so one badly-behaved tool cannot produce an unreadable report.
EXCERPT_BYTES = 400


class Outcome:
    MATCH = "match"
    # Both runtimes failed, and failed identically. That is parity, but it is
    # not evidence the operation works, so it is counted separately and never
    # allowed to satisfy the minimum below.
    AGREED_FAILURE = "agreed-failure"
    DIVERGED = "diverged"
    # In scope and not compared: the recipe declared both runtimes, so failing
    # to compare it is a missing artifact or a broken invocation, not a thing to
    # shrug at. Always a problem.
    SKIPPED = "skipped"
    # Never in scope: the recipe only ever claimed one runtime. Not a failure,
    # and kept distinct so a catalogue of single-runtime tools does not read as
    # a catalogue of skipped ones.
    OUT_OF_SCOPE = "out-of-scope"


def _example_parameter_values(operation_id):
    """Stable per-operation example values.

    Seeded from the operation id rather than from the clock, because a parity
    run that cannot be reproduced cannot be investigated -- and because both
    runtimes must be handed the same arguments to be comparable at all.
    """
    rnd = random.Random(operation_id)
    return {
        "string": "".join(rnd.choices(string.ascii_lowercase, k=12)),
        "integer": rnd.randint(1, 5),
        "float": round(rnd.uniform(1, 100), 2),
    }


def build_invocation(bundle, example_inputs):
    """Turn a bundle into one concrete invocation, or explain why it cannot.

    Returns (args, files, stdin, collect, reason). `reason` is non-None when the
    operation cannot be exercised, and is reported rather than swallowed.
    """
    values = _example_parameter_values(bundle.get("id", bundle.get("name", "")))
    args = []
    files = {}
    stdin = None

    for parameter in bundle.get("parameters", []):
        if not parameter.get("required"):
            continue
        if parameter.get("flag"):
            args.append(parameter["flag"])
        if parameter.get("default") is not None:
            args.append(str(parameter["default"]))
        elif parameter.get("type") in values:
            args.append(str(values[parameter["type"]]))

    for input_def in bundle.get("io", {}).get("inputs", []):
        types = input_def.get("types") or []
        usable = next((t for t in types if t in example_inputs), None)
        if usable is None:
            return None, None, None, None, f"no example input for types {types}"
        content = example_inputs[usable]

        mode = input_def.get("mode")
        if mode == "stdin":
            stdin = content
        elif mode == "file":
            # A relative name, and both runtimes are given the identical string.
            # Passing an absolute path to the native side and a basename to the
            # WASM side would make every tool that echoes its arguments look
            # divergent for a reason that is entirely the harness's fault.
            name = f"input_{input_def['name']}"
            files[name] = content
            if input_def.get("flag"):
                args.append(input_def["flag"])
            args.append(name)
        else:
            return None, None, None, None, f"unsupported input mode {mode!r}"

    collect = [
        output["name"]
        for output in bundle.get("io", {}).get("outputs", [])
        if output.get("mode") == "file"
    ]
    return args, files, stdin, collect, None


def _run_native(binary, args, files, stdin, collect, timeout):
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        for name, content in files.items():
            (work / name).write_text(content)
        local_binary = work / Path(binary).name
        shutil.copyfile(binary, local_binary)
        os.chmod(local_binary, os.stat(local_binary).st_mode | 0o111)
        try:
            done = subprocess.run(
                [f"./{local_binary.name}"] + args,
                cwd=work,
                input=stdin.encode() if stdin is not None else None,
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return {"stdout": "", "stderr": "", "code": None, "files": {},
                    "error": f"timed out after {timeout}s"}
        produced = {}
        for name in collect:
            match = next(
                (f for f in work.iterdir() if f.is_file() and f.stem.lower() == name.lower()),
                None,
            )
            produced[name] = (
                base64.b64encode(match.read_bytes()).decode() if match else None
            )
        return {
            "stdout": done.stdout.decode("utf-8", errors="replace"),
            "stderr": done.stderr.decode("utf-8", errors="replace"),
            "code": done.returncode,
            "files": produced,
            "error": None,
        }


def _run_wasm_batch(module_js, cases, timeout):
    with tempfile.TemporaryDirectory() as tmp:
        job = Path(tmp) / "job.json"
        out = Path(tmp) / "out.json"
        job.write_text(json.dumps({
            "module": str(module_js),
            "cases": [
                {
                    "args": c["args"],
                    "files": {n: base64.b64encode(v.encode()).decode()
                              for n, v in c["files"].items()},
                    "stdin": (base64.b64encode(c["stdin"].encode()).decode()
                              if c["stdin"] is not None else None),
                    "collect": c["collect"],
                }
                for c in cases
            ],
        }))
        try:
            subprocess.run(
                ["node", str(NODE_RUNNER), str(job), str(out)],
                check=True, capture_output=True, timeout=timeout * max(1, len(cases)),
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"the WASM runner failed: {exc.stderr.decode('utf-8', 'replace')[:800]}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("the WASM runner timed out") from exc
        return json.loads(out.read_text())


def _excerpt(text):
    if text is None:
        return None
    return text[:EXCERPT_BYTES] + ("…" if len(text) > EXCERPT_BYTES else "")


def compare(native, wasm, compare_stderr=False):
    """Return a list of differences; empty means the two agree."""
    differences = []
    if native["code"] != wasm["code"]:
        differences.append(f"exit code: native {native['code']}, wasm {wasm['code']}")
    if native["stdout"] != wasm["stdout"]:
        differences.append("stdout differs")
    if compare_stderr and native["stderr"] != wasm["stderr"]:
        differences.append("stderr differs")
    for name in sorted(set(native["files"]) | set(wasm["files"])):
        if native["files"].get(name) != wasm["files"].get(name):
            differences.append(f"output file {name!r} differs")
    return differences


def discover(registry_dir):
    """Every bundle that declares and ships both runtimes."""
    found = []
    registry = Path(registry_dir)
    if not registry.is_dir():
        return found
    for operation_dir in sorted(registry.iterdir()):
        if not operation_dir.is_dir():
            continue
        for version_dir in sorted(operation_dir.iterdir()):
            if not version_dir.is_dir():
                continue
            bundle_path = version_dir / "bundle.json"
            if not bundle_path.is_file():
                continue
            bundle = json.loads(bundle_path.read_text())
            found.append((version_dir, bundle))
    return found


def run_parity(registry_dir, timeout=30, compare_stderr=False, minimum=1):
    example_inputs = get_example_inputs()
    records = []

    for version_dir, bundle in discover(registry_dir):
        name = bundle.get("id") or bundle.get("name")
        binary = bundle.get("bin", "")
        native_path = version_dir / "runtime" / "native" / binary
        wasm_path = version_dir / "runtime" / "wasm" / f"{binary}.js"

        record = {"operation": name, "version": version_dir.name,
                  "outcome": Outcome.SKIPPED, "reason": None, "differences": []}

        modes = (bundle.get("runtime") or {}).get("modes") or []
        if not ("wasm" in modes and "native" in modes):
            record["outcome"] = Outcome.OUT_OF_SCOPE
            record["reason"] = f"declares modes {modes}, not both"
            records.append(record)
            continue
        if not native_path.is_file():
            record["reason"] = "no native artifact in the bundle"
            records.append(record)
            continue
        if not wasm_path.is_file():
            record["reason"] = "no wasm artifact in the bundle"
            records.append(record)
            continue

        args, files, stdin, collect, reason = build_invocation(bundle, example_inputs)
        if reason:
            record["reason"] = reason
            records.append(record)
            continue

        case = {"args": args, "files": files, "stdin": stdin, "collect": collect}
        record["invocation"] = " ".join([binary] + args)

        native = _run_native(native_path, args, files, stdin, collect, timeout)
        try:
            wasm = _run_wasm_batch(wasm_path, [case], timeout)[0]
        except RuntimeError as exc:
            record["reason"] = str(exc)
            records.append(record)
            continue

        if native.get("error") or wasm.get("error"):
            # One side could not be executed at all. Not a divergence in the
            # tool, and not a pass either.
            record["reason"] = (f"native: {native.get('error')}, "
                                f"wasm: {wasm.get('error')}")
            records.append(record)
            continue

        differences = compare(native, wasm, compare_stderr=compare_stderr)
        if differences:
            record["outcome"] = Outcome.DIVERGED
            record["differences"] = differences
            record["native"] = {"code": native["code"],
                                "stdout": _excerpt(native["stdout"]),
                                "stderr": _excerpt(native["stderr"])}
            record["wasm"] = {"code": wasm["code"],
                              "stdout": _excerpt(wasm["stdout"]),
                              "stderr": _excerpt(wasm["stderr"])}
        elif native["code"] != 0:
            record["outcome"] = Outcome.AGREED_FAILURE
            record["reason"] = f"both runtimes exited {native['code']}"
            record["native"] = {"stderr": _excerpt(native["stderr"])}
        else:
            record["outcome"] = Outcome.MATCH
        records.append(record)

    return summarise(records, minimum)


def summarise(records, minimum):
    counts = {
        Outcome.MATCH: 0,
        Outcome.AGREED_FAILURE: 0,
        Outcome.DIVERGED: 0,
        Outcome.SKIPPED: 0,
        Outcome.OUT_OF_SCOPE: 0,
    }
    for record in records:
        counts[record["outcome"]] += 1

    problems = []
    if counts[Outcome.DIVERGED]:
        problems.append(
            f"{counts[Outcome.DIVERGED]} operation(s) differ between wasm and native"
        )
    # A bundle whose recipe declared both runtimes and which could not be
    # compared anyway is a failure, not a note. Whatever went wrong -- an
    # artifact the build did not produce, an input type with no example -- the
    # promise in the recipe was not kept, and letting it pass quietly is how a
    # parity gate turns into a parity suggestion.
    if counts[Outcome.SKIPPED]:
        skipped = [f"{r['operation']} ({r['reason']})"
                   for r in records if r["outcome"] == Outcome.SKIPPED]
        problems.append(
            f"{len(skipped)} operation(s) declare both runtimes but could not be "
            f"compared: {'; '.join(skipped[:5])}"
            + (" ..." if len(skipped) > 5 else "")
        )
    # The floor, for a run over the whole catalogue where the caller knows how
    # much SHOULD have been compared. It defaults to zero because a pull request
    # touching only single-runtime recipes has nothing to compare and must not
    # fail for it -- but a catalogue job that passes 0 by accident would look
    # exactly like one that compared everything, so that job sets a real number.
    if counts[Outcome.MATCH] < minimum:
        problems.append(
            f"only {counts[Outcome.MATCH]} operation(s) were actually compared "
            f"and matched, below the required minimum of {minimum}"
        )

    return {"records": records, "counts": counts, "problems": problems,
            "ok": not problems}


def format_report(report):
    lines = []
    counts = report["counts"]
    lines.append("WASM versus native parity")
    lines.append("")
    for record in report["records"]:
        mark = {
            Outcome.MATCH: "  match  ",
            Outcome.AGREED_FAILURE: "  agreed-",
            Outcome.DIVERGED: "  DIFFER ",
            Outcome.SKIPPED: "  SKIP   ",
            Outcome.OUT_OF_SCOPE: "  --     ",
        }[record["outcome"]]
        line = f"{mark} {record['operation']}"
        if record.get("reason"):
            line += f"  ({record['reason']})"
        lines.append(line)
        for difference in record.get("differences", []):
            lines.append(f"           - {difference}")
        if record["outcome"] == Outcome.DIVERGED:
            lines.append(f"           invocation: {record.get('invocation')}")
            lines.append(f"           native: rc={record['native']['code']} "
                         f"stdout={record['native']['stdout']!r}")
            lines.append(f"           wasm  : rc={record['wasm']['code']} "
                         f"stdout={record['wasm']['stdout']!r}")
    lines.append("")
    lines.append(
        f"compared and matched: {counts[Outcome.MATCH]}   "
        f"agreed failures: {counts[Outcome.AGREED_FAILURE]}   "
        f"diverged: {counts[Outcome.DIVERGED]}   "
        f"skipped in scope: {counts[Outcome.SKIPPED]}   "
        f"single-runtime: {counts[Outcome.OUT_OF_SCOPE]}"
    )
    for problem in report["problems"]:
        lines.append(f"FAIL: {problem}")
    if report["ok"]:
        lines.append("OK: every comparable operation agrees across runtimes")
    return "\n".join(lines)


def self_test():
    """Check that this harness still reports a difference when there is one.

    A parity report is only worth reading if a divergence actually fails it, and
    that property is quiet when it breaks: a comparison that silently stops
    comparing looks exactly like a catalogue that agrees. These checks are cheap
    and need no toolchain, so there is no reason for CI not to ask.

    The end-to-end version of this was run by hand against ksw2, by rebuilding
    its WASM side with a different default match score and confirming the report
    said DIFFER and the command exited non-zero. That found a real bug in
    run_wasm.mjs -- see the comment there about callMain's return value.
    """
    def result(stdout="out", code=0, files=None, stderr="err"):
        return {"stdout": stdout, "stderr": stderr, "code": code,
                "files": files or {}, "error": None}

    failures = []

    def expect(label, condition):
        if not condition:
            failures.append(label)

    expect("identical results must agree",
           compare(result(), result()) == [])
    expect("a differing stdout must be caught",
           compare(result(stdout="a"), result(stdout="b")) != [])
    expect("a differing exit code must be caught",
           compare(result(code=0), result(code=1)) != [])
    expect("a differing output file must be caught",
           compare(result(files={"o": "AA"}), result(files={"o": "BB"})) != [])
    expect("an output file present on one side only must be caught",
           compare(result(files={"o": "AA"}), result(files={"o": None})) != [])
    expect("stderr must be ignored unless asked for",
           compare(result(stderr="a"), result(stderr="b")) == [])
    expect("stderr must be compared when asked for",
           compare(result(stderr="a"), result(stderr="b"), compare_stderr=True) != [])

    def record(outcome):
        return {"operation": "x", "version": "1", "outcome": outcome,
                "reason": None, "differences": []}

    expect("a divergence must fail the report",
           not summarise([record(Outcome.DIVERGED), record(Outcome.MATCH)], 1)["ok"])
    expect("an in-scope skip must fail even with no floor",
           not summarise([record(Outcome.SKIPPED)], 0)["ok"])
    expect("a single-runtime recipe must not fail the report",
           summarise([record(Outcome.OUT_OF_SCOPE)], 0)["ok"])
    expect("a report of nothing but skips must fail",
           not summarise([record(Outcome.SKIPPED)], 1)["ok"])
    expect("an empty report must fail",
           not summarise([], 1)["ok"])
    expect("agreed failures must not satisfy the minimum",
           not summarise([record(Outcome.AGREED_FAILURE)], 1)["ok"])
    expect("a genuine match must pass",
           summarise([record(Outcome.MATCH)], 1)["ok"])

    return failures
