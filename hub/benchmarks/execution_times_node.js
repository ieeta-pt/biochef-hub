
const fs = require("fs");
const path = require("path");

async function main() {
  const [, , modulePath, specPath] = process.argv;

  if (!modulePath || !specPath) {
    throw new Error("Usage: node run_wasm.js <module.js> <spec.json>");
  }

  const resolved = path.resolve(modulePath);
  const spec = JSON.parse(fs.readFileSync(specPath, "utf8"));

  const cwd = path.resolve(spec.cwd || process.cwd());

  if (!fs.existsSync(cwd)) {
    throw new Error(`Working directory does not exist: ${cwd}`);
  }

  const factory = require(resolved);
  const wasmBinary = fs.readFileSync(
    resolved.replace(/\.js$/, ".wasm")
  );

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

      locateFile: (file) => {
          return path.join(path.dirname(resolved), file);
      },

      print: line => {
        // if (stdoutMode === "capture") {
        //   stdout += String(line) + "\n";
        // }
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

    // Mount the scenario working directory.
    FS.mkdir("/work");

    FS.mount(
      NODEFS,
      { root: cwd },
      "/work"
    );

    // Translate arguments referring to files in the working directory.
    const argv = (spec.argv || []).map(arg => {
      if (typeof arg !== "string") {
        return arg;
      }

      const resolvedArg = path.resolve(cwd, arg);

      if (
        resolvedArg.startsWith(cwd + path.sep) &&
        fs.existsSync(resolvedArg) &&
        fs.statSync(resolvedArg).isFile()
      ) {
        return `/work/${path.relative(cwd, resolvedArg).split(path.sep).join("/")}`;
      }

      return arg;
    });

    // Set the working directory inside WASM.
    FS.chdir("/work");

    // Configure stdout.
    if (stdoutMode !== "capture") {
      FS.close(FS.streams[1]);

      const destination = stdoutMode === "discard"
        ? "/dev/null"
        : "output.txt";

      FS.streams[1] = FS.open(destination, "w");
    }

    const start = performance.now();

    Module.callMain(argv);

    elapsedMs = performance.now() - start;

    // if (stdoutMode !== "capture") {
    //   stdout = "";
    // }

  } catch (err) {
    completed = false;
    stderr += String(err.stack || err) + "\n";
  }

  process.stdout.write(JSON.stringify({
    loaded: true,
    completed,
    exitCode,
    elapsed_ms: elapsedMs,
    stdout: "",
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
