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
//   result { exitCode, stdout, stderr, files: { name: base64 } }
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
  const factory = require(path.resolve(modulePath));

  let stdout = "";
  let stderr = "";

  // The tool reads stdin a byte at a time and expects null at the end.
  const stdinBytes = Buffer.from(spec.stdin ?? "", "utf8");
  let stdinPos = 0;

  const Module = await factory({
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

  for (const [name, contents] of Object.entries(spec.files ?? {})) {
    Module.FS.writeFile(name, Buffer.from(contents, "base64"));
  }

  let exitCode = 0;
  try {
    Module.callMain(spec.argv ?? []);
  } catch (err) {
    // EXIT_RUNTIME=1 means returning from main throws ExitStatus rather than
    // returning normally, including on success.
    if (err && err.name === "ExitStatus") {
      exitCode = err.status;
    } else {
      exitCode = -1;
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

  process.stdout.write(JSON.stringify({ exitCode, stdout, stderr, files }));
}

main().catch((err) => {
  // Reported in the same shape as a normal result so the caller has one path.
  process.stdout.write(
    JSON.stringify({
      exitCode: -1,
      stdout: "",
      stderr: String((err && err.stack) || err),
      files: {},
    })
  );
});
