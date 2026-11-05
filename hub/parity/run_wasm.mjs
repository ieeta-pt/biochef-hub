// Runs operations against a bundle's WASM build and reports what came out.
//
// This deliberately loads the artifact exactly as published -- the one built
// with `-s ENVIRONMENT=web,worker` for the browser -- rather than rebuilding it
// with node support. A harness that tests a differently-built artifact is not
// comparing the thing anyone runs. That works because the module is handed its
// `wasmBinary` directly, so it never reaches the environment detection that
// would otherwise look for a browser fetch().
//
// `-s EXIT_RUNTIME=1` tears the runtime down when main returns, so a module
// instance is good for exactly one call. Every case therefore instantiates its
// own; the cost of that is why cases arrive in a batch rather than one node
// process per invocation.
//
// Input and output are JSON files rather than stdin/stdout because a tool's own
// output may be binary and must not have to survive a pipe. File contents are
// base64 on both sides for the same reason.
import fs from "node:fs";
import path from "node:path";

const [jobPath, outPath] = process.argv.slice(2);
const job = JSON.parse(fs.readFileSync(jobPath, "utf8"));

const modulePath = path.resolve(job.module);
const moduleDir = path.dirname(modulePath);
const wasmPath = path.join(moduleDir, path.basename(modulePath, ".js") + ".wasm");
const wasmBinary = fs.readFileSync(wasmPath);
const factory = (await import(modulePath)).default;

// Emscripten's ErrnoError carries `name` and `errno` and no `message`, so the
// obvious String(err.message || err) renders it as "[object Object]" -- which is
// what this reported the first time a stdin case failed, and told nobody
// anything.
function describe(err) {
  if (err === null || err === undefined) return "unknown error";
  const parts = [];
  if (err.name) parts.push(err.name);
  if (err.message) parts.push(err.message);
  if (err.errno !== undefined) parts.push(`errno=${err.errno}`);
  if (err.status !== undefined) parts.push(`status=${err.status}`);
  return parts.length ? parts.join(" ") : String(err);
}

const results = [];

for (const testCase of job.cases) {
  const record = { stdout: "", stderr: "", code: 0, files: {}, error: null };
  try {
    // stdin has to be handed to the factory, not installed afterwards. Calling
    // FS.init() on the resolved module throws ErrnoError -- the filesystem is
    // already initialised by then -- and the damage if that throw is swallowed
    // is worse than a crash: the program still runs and reads zero bytes, so
    // every stdin-driven tool in the catalogue would look like it diverged
    // when only the harness had failed to deliver the input. Emscripten's own
    // FS.init picks up Module['stdin'] during startup, which is this.
    const stdinBytes =
      testCase.stdin !== undefined && testCase.stdin !== null
        ? Buffer.from(testCase.stdin, "base64")
        : null;
    let stdinCursor = 0;

    const Module = await factory({
      wasmBinary,
      noInitialRun: true,
      ...(stdinBytes
        ? { stdin: () => (stdinCursor < stdinBytes.length ? stdinBytes[stdinCursor++] : null) }
        : {}),
      print: (line) => { record.stdout += line + "\n"; },
      printErr: (line) => { record.stderr += line + "\n"; },
    });

    for (const [name, b64] of Object.entries(testCase.files || {})) {
      Module.FS.writeFile(name, Buffer.from(b64, "base64"));
    }

    try {
      // A status reaches us by one of two routes, and taking only the second
      // was a real bug here: when main RETURNS a value, callMain hands it back
      // and throws nothing, so ignoring the return value recorded 0 for every
      // tool that reports failure by returning rather than by calling exit().
      // ksw2 with no arguments does exactly that -- native exits 1, and this
      // reported a divergence that was entirely the harness's own.
      const status = Module.callMain(testCase.args);
      record.code = typeof status === "number" ? status : 0;
    } catch (err) {
      // The other route: exit() unwinds as an ExitStatus carrying the code, and
      // an abort arrives as something else entirely. Both are outcomes to
      // compare, not harness failures.
      record.code = err && err.status !== undefined ? err.status : 1;
      if (err && err.status === undefined) record.error = describe(err);
    }

    // Matched by stem, exactly as the native side matches it. A bundle declares
    // an output called "out" and the tool writes "out.txt"; reading the literal
    // name here while the native side searched by stem would report every such
    // tool as divergent, with one side holding the file and the other null.
    // The two sides must apply the SAME rule or the comparison is meaningless.
    let entries = [];
    try {
      entries = Module.FS.readdir(".").filter((e) => e !== "." && e !== "..");
    } catch { /* no working directory listing; every collect below yields null */ }

    for (const name of testCase.collect || []) {
      const wanted = name.toLowerCase();
      const hit = entries.find((entry) => {
        const dot = entry.lastIndexOf(".");
        const stem = dot > 0 ? entry.slice(0, dot) : entry;
        return stem.toLowerCase() === wanted;
      });
      if (hit === undefined) {
        // Absent is a result: the native side may not have produced it either,
        // and that agreement is what the comparison is for.
        record.files[name] = null;
        continue;
      }
      try {
        record.files[name] = Buffer.from(Module.FS.readFile(hit)).toString("base64");
      } catch {
        record.files[name] = null;
      }
    }
  } catch (err) {
    // Instantiation itself failed. Distinct from the tool exiting non-zero, and
    // reported as such so it cannot be read as a divergence in the tool.
    record.error = describe(err);
    record.code = null;
  }
  results.push(record);
}

fs.writeFileSync(outPath, JSON.stringify(results));
