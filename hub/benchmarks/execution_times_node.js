
const fs = require("fs");
const path = require("path");

async function main() {
  const [, , modulePath, specPath] = process.argv;

  if (!modulePath || !specPath) {
    throw new Error("Usage: node run_wasm.js <module.js> <spec.json>");
  }

  const resolved = path.resolve(modulePath);
  const spec = JSON.parse(fs.readFileSync(specPath, "utf8"));

  if (!spec.input_file) {
    throw new Error("spec.input_file is required");
  }

  const inputFile = path.resolve(spec.input_file);

  if (!fs.existsSync(inputFile)) {
    throw new Error(`Input file does not exist: ${inputFile}`);
  }

  const factory = require(resolved);
  const wasmBinary = fs.readFileSync(resolved.replace(/\.js$/, ".wasm"));

  let stdout = "";
  let stderr = "";
  let Module;

  const stdoutMode = spec.stdout_mode || "capture";

  if (!["capture", "discard", "file"].includes(stdoutMode)) {
    throw new Error(`Invalid stdout_mode: ${stdoutMode}`);
  }

  try {
    Module = await factory({
      wasmBinary,
      noInitialRun: true,
      print: line => {
        if (stdoutMode === "capture") {
          stdout += String(line) + "\n";
        }
      },
      printErr: line => {
        if (stderr.length < 65536) {
          stderr += String(line) + "\n";
        }
      },
    });
  } catch (err) {
    process.stdout.write(JSON.stringify({
      loaded: false,
      completed: false,
      exitCode: -1,
      stdout: "",
      stderr: String(err.stack || err),
      files: {},
    }));
    return;
  }

  let completed = true;
  let exitCode = 0;
  let elapsedMs = null;

  try {
    const FS = Module.FS;

    if (!FS) {
      throw new Error("Emscripten FS is unavailable.");
    }

    const NODEFS = Module.NODEFS || FS.filesystems?.NODEFS;

    if (!NODEFS) {
      throw new Error("NODEFS is unavailable. Rebuild with -lnodefs.js.");
    }

    // Mount the directory containing the input file
    const inputDir = path.dirname(inputFile);

    FS.mkdir("/host");
    FS.mount(NODEFS, { root: inputDir }, "/host");

    const inputName = path.basename(inputFile);

    const argv = [
      ...(spec.argv || []),
      `/host/${inputName}`,
    ];

    const outputPath = "output.txt"

    if (stdoutMode !== "capture") {
      FS.close(FS.streams[1]);

      const destination = stdoutMode === "discard"
        ? "/dev/null"
        : outputPath;

      FS.streams[1] = FS.open(destination, "w");
    }

    const start = performance.now();

    Module.callMain(argv);

    if (stdoutMode !== "capture") {
      stdout = ""
    }

    // if (stdoutMode === "file") {
    //   stdout = FS.readFile(outputPath, {
    //     encoding: "utf8"
    //   });
    // }

    // if (stdoutMode === "discard") {
    //   stdout = "";
    // }

    elapsedMs = performance.now() - start;

  } catch (err) {
    completed = false;
    stderr += String(err.stack || err) + "\n";
  }

  process.stdout.write(JSON.stringify({
    loaded: true,
    completed,
    exitCode,
    elapsed_ms: elapsedMs,
    stdout,
    stderr,
    files: {},
  }));
}

main().catch(err => {
  process.stdout.write(JSON.stringify({
    loaded: false,
    completed: false,
    exitCode: -1,
    stdout: "",
    stderr: String(err.stack || err),
    files: {},
  }));
});
