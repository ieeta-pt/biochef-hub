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

const results = [];

for (const testCase of job.cases) {
  const record = { stdout: "", stderr: "", code: 0, files: {}, error: null };
  try {
    const Module = await factory({
      wasmBinary,
      noInitialRun: true,
      print: (line) => { record.stdout += line + "\n"; },
      printErr: (line) => { record.stderr += line + "\n"; },
    });

    for (const [name, b64] of Object.entries(testCase.files || {})) {
      Module.FS.writeFile(name, Buffer.from(b64, "base64"));
    }

    if (testCase.stdin !== undefined && testCase.stdin !== null) {
      // Emscripten reads stdin through a callback returning one byte at a time,
      // and null means EOF. Installing it before callMain is the only chance:
      // the runtime wires up the streams during startup.
      const bytes = Buffer.from(testCase.stdin, "base64");
      let cursor = 0;
      Module.FS.init(() => (cursor < bytes.length ? bytes[cursor++] : null), null, null);
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
      if (err && err.status === undefined) record.error = String(err.message || err);
    }

    for (const name of testCase.collect || []) {
      try {
        record.files[name] = Buffer.from(Module.FS.readFile(name)).toString("base64");
      } catch {
        // Absent is a result: the native side may not have produced it either,
        // and that agreement is what the comparison is for.
        record.files[name] = null;
      }
    }
  } catch (err) {
    // Instantiation itself failed. Distinct from the tool exiting non-zero, and
    // reported as such so it cannot be read as a divergence in the tool.
    record.error = String((err && err.message) || err);
    record.code = null;
  }
  results.push(record);
}

fs.writeFileSync(outPath, JSON.stringify(results));
