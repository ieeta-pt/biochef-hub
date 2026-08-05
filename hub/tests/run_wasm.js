// Runs one Emscripten-built tool under Node and reports what it produced.
//
// hub test can only exercise a tool it can execute, and most of the catalogue
// has no native build at all -- those recipes were skipped, which meant a green
// test run said only that the build had produced a file of the right name. This
// runs the wasm artifact instead, so the same input generation and output type
// checking apply to every recipe rather than to the few with a native binary.
//
// Reads a JSON spec from a file and writes a JSON result to stdout, so the
// Python side does not have to know anything about Emscripten:
//
//   spec   { argv: [], stdin: "", files: { name: base64 } }
//   result { loaded, completed, exitCode, stdout, stderr, files: { name: base64 } }
//
// loaded=false means the module would not start at all -- most often because it
// was built without "node" in -sENVIRONMENT. completed=false means it started
// and then trapped. Neither is expressible as an exit status, because a tool
// returning -1 to reject its arguments is ordinary.
//
// Every regular file left in the working directory is returned, not a named
// list: the caller does not tell the tool where to write, it looks afterwards
// for a file named after the output, so the same discovery has to work here.
//
// Usage: node run_wasm.js <module.js> <spec.json>

const fs = require("fs");
const path = require("path");

async function main() {
  const [, , modulePath, specPath] = process.argv;
  if (!modulePath || !specPath) {
    throw new Error("usage: run_wasm.js <module.js> <spec.json>");
  }

  const spec = JSON.parse(fs.readFileSync(specPath, "utf8"));
  const resolved = path.resolve(modulePath);
  const factory = require(resolved);

  // Handed over rather than left for the module to fetch. A module built for
  // the browser resolves its .wasm with fetch or XHR, neither of which exists
  // here.
  const wasmBinary = fs.readFileSync(resolved.replace(/\.js$/, ".wasm"));

  let stdout = "";
  let stderr = "";

  // The tool reads stdin a byte at a time and expects null at the end.
  const stdinBytes = Buffer.from(spec.stdin ?? "", "utf8");
  let stdinPos = 0;

  let Module;
  try {
    Module = await factory({
      wasmBinary,
    // The recipes are built with INVOKE_RUN=0, so main is called explicitly
    // below once the input files are in place.
    noInitialRun: true,
    print: (line) => {
      stdout += line + "\n";
    },
    printErr: (line) => {
      stderr += line + "\n";
    },
      stdin: () => (stdinPos < stdinBytes.length ? stdinBytes[stdinPos++] : null),
    });
  } catch (err) {
    // A module built without "node" in -sENVIRONMENT refuses to start here. That
    // says nothing about whether the tool works, so it is reported as its own
    // outcome rather than as a failure -- the caller decides what to do with it.
    const message = String((err && err.message) || err);
    const unsupported = /not compiled for this environment|not enabled at build time/.test(message);
    process.stdout.write(JSON.stringify({
      loaded: false,
      unsupportedEnvironment: unsupported,
      stdout: "",
      stderr: message,
      files: {},
    }));
    return;
  }

  for (const [name, contents] of Object.entries(spec.files ?? {})) {
    Module.FS.writeFile(name, Buffer.from(contents, "base64"));
  }

  let exitCode = 0;
  let completed = true;
  try {
    // callMain returns main's value. It does not throw for a normal return,
    // even with EXIT_RUNTIME=1 -- reading the status from a thrown ExitStatus
    // alone would report every tool as having exited 0.
    const status = Module.callMain(spec.argv ?? []);
    if (typeof status === "number") exitCode = status;
  } catch (err) {
    if (err && err.name === "ExitStatus") {
      // A program that calls exit() rather than returning does throw.
      exitCode = err.status;
    } else {
      // Reported in its own field. Overloading a status value cannot work:
      // "return -1" is an ordinary way for a tool to reject its arguments, and
      // would be indistinguishable from a trap.
      completed = false;
      stderr += String((err && err.message) || err) + "\n";
    }
  }

  // Everything the tool left behind, so the caller can look for outputs the
  // same way it does for a native binary. The virtual root also holds the
  // mount points Emscripten sets up, hence the regular-file check.
  const files = {};
  for (const name of Module.FS.readdir("/")) {
    if (name === "." || name === "..") continue;
    try {
      const stat = Module.FS.stat("/" + name);
      if (!Module.FS.isFile(stat.mode)) continue;
      files[name] = Buffer.from(Module.FS.readFile("/" + name)).toString("base64");
    } catch {
      // Unreadable entries are simply not reported.
    }
  }

  process.stdout.write(JSON.stringify({ loaded: true, completed, exitCode, stdout, stderr, files }));
}

main().catch((err) => {
  // Reported in the same shape as a normal result so the caller has one path.
  process.stdout.write(
    JSON.stringify({
      loaded: false,
      unsupportedEnvironment: false,
      stdout: "",
      stderr: String((err && err.stack) || err),
      files: {},
    })
  );
});
