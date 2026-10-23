// Runs one R operation under webR in Node and reports what it produced.
//
// An R operation has no binary of its own: it is a script run against the
// package library its recipe built, so run_wasm.js cannot exercise it. This
// runs it the way the app's RRuntime does: the library mounted as a filesystem
// image and put on .libPaths(), the arguments bound as `argv`, the script
// sourced from a fixed working directory, and an R error reported as a failed
// run rather than thrown.
//
// Same contract as run_wasm.js, so the Python side has one path for both:
//
//   spec   { script, webrVersion, argv: [], stdin: "", files: { name: base64 } }
//   result { loaded, completed, exitCode, stdout, stderr, files: { name: base64 } }
//
// loaded=false means webR, the library or the script could not be set up.
// completed=false means R itself stopped, as opposed to the script raising an
// error, which is reported as exitCode 1 like a command line tool would.
//
// Usage: node run_r.mjs <webr.mjs> <runtime/wasm dir> <spec.json>

import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const WORK_DIR = "/biochef";
const LIBRARY_DIR = "/biochef-lib";
const SCRIPT_PATH = `${WORK_DIR}/.operation.R`;

// The app's RUN_OPERATION: the script path and the arguments are bound as R
// variables, never interpolated, and an error comes back as the return value so
// that the captured streams survive it.
const RUN_OPERATION = `local({
  .biochef_error <- NULL
  tryCatch(
    source(.biochef_script, echo = FALSE, local = FALSE),
    error = function(e) .biochef_error <<- conditionMessage(e)
  )
  if (is.null(.biochef_error)) character(0) else as.character(.biochef_error)
})`;

function report(result) {
  process.stdout.write(JSON.stringify(result));
}

function notLoaded(message) {
  report({ loaded: false, unsupportedEnvironment: false, stdout: "", stderr: message, files: {} });
}

// A file the bundle ships next to the script, refused if it is a symlink or
// would resolve outside the runtime directory.
function runtimeFile(root, name) {
  const file = path.resolve(root, name);
  if (path.dirname(file) !== root || fs.lstatSync(file).isSymbolicLink()) {
    throw new Error(`unsafe runtime file: ${name}`);
  }
  return fs.readFileSync(file);
}

async function main() {
  const [, , webrModule, runtimeDir, specPath] = process.argv;
  if (!webrModule || !runtimeDir || !specPath) {
    throw new Error("usage: run_r.mjs <webr.mjs> <runtime/wasm dir> <spec.json>");
  }
  const spec = JSON.parse(fs.readFileSync(specPath, "utf8"));
  const root = path.resolve(runtimeDir);

  // webR talks to its worker over a WebSocket and, on a Node without a global
  // one, requires the `ws` package, which it does not declare. Node 22 has it;
  // CI runs the Node 22 that emsdk activates.
  if (!("WebSocket" in globalThis)) {
    return notLoaded(`webR needs Node 22 or later, with a global WebSocket; this is Node ${process.versions.node}`);
  }

  const { WebR } = await import(pathToFileURL(path.resolve(webrModule)).href);
  const webR = new WebR({ interactive: false });

  try {
    if (spec.webrVersion && webR.version !== spec.webrVersion) {
      return notLoaded(
        `R package library was built for webR ${spec.webrVersion}, but this harness runs webR ${webR.version}`
      );
    }

    let script;
    let library;
    let metadata;
    try {
      script = runtimeFile(root, spec.script);
      library = runtimeFile(root, "library.data.gz");
      metadata = JSON.parse(runtimeFile(root, "library.js.metadata").toString("utf8"));
    } catch (err) {
      return notLoaded(String((err && err.message) || err));
    }

    await webR.init();
    await webR.FS.mkdir(WORK_DIR);
    await webR.FS.mkdir(LIBRARY_DIR);
    // The hub gzips the image, and WORKERFS reads it as it is, so it is
    // inflated first, as the app does.
    const { gunzipSync } = await import("node:zlib");
    await webR.FS.mount(
      "WORKERFS",
      { packages: [{ blob: new Blob([gunzipSync(library)]), metadata }] },
      LIBRARY_DIR
    );
    await webR.evalRVoid(".libPaths(c(libpath, .libPaths()))", { env: { libpath: LIBRARY_DIR } });
    // A mount whose bytes R cannot read still succeeds and leaves the
    // directory empty, so the library is checked before anything runs.
    const packages = await webR.evalRNumber("length(list.files(dir))", { env: { dir: LIBRARY_DIR } });
    if (packages === 0) {
      return notLoaded(`R package library mounted at ${LIBRARY_DIR} is empty`);
    }
    await webR.evalRVoid("setwd(dir)", { env: { dir: WORK_DIR } });

    for (const [name, contents] of Object.entries(spec.files ?? {})) {
      await webR.FS.writeFile(`${WORK_DIR}/${name}`, Buffer.from(contents, "base64"));
    }
    await webR.FS.writeFile(SCRIPT_PATH, new Uint8Array(script));

    const shelter = await new webR.Shelter();
    const globalEnv = await webR.objs.globalEnv;
    await globalEnv.bind(".biochef_script", SCRIPT_PATH);
    const args = (spec.argv ?? []).map(String);
    const argv = args.length
      ? await shelter.evalR("as.character(x)", { env: { x: args } })
      : await shelter.evalR("character(0)");
    await globalEnv.bind("argv", argv);
    await globalEnv.bind("stdin", spec.stdin ?? "");

    let stdout = "";
    let stderr = "";
    let exitCode = 0;
    let completed = true;
    try {
      const capture = await shelter.captureR(RUN_OPERATION, {
        withAutoprint: false,
        captureStreams: true,
        captureConditions: false,
      });
      for (const line of capture.output) {
        if (line.type === "stdout") stdout += `${line.data}\n`;
        else stderr += `${line.data}\n`;
      }
      const result = await capture.result.toJs();
      if (result.values.length) {
        exitCode = 1;
        stderr += `${result.values[0] || "the operation failed without reporting a reason"}\n`;
      }
    } catch (err) {
      completed = false;
      stderr += String((err && err.message) || err) + "\n";
    }

    // Everything the script left in the working directory, so the caller looks
    // for outputs exactly as it does for a binary.
    const files = {};
    const entries = (await webR.FS.lookupPath(WORK_DIR)).contents ?? {};
    for (const [name, entry] of Object.entries(entries)) {
      const file = `${WORK_DIR}/${name}`;
      if (entry.isFolder || file === SCRIPT_PATH) continue;
      files[name] = Buffer.from(await webR.FS.readFile(file)).toString("base64");
    }

    report({ loaded: true, completed, exitCode, stdout, stderr, files });
  } finally {
    webR.close();
  }
}

main()
  .catch((err) => notLoaded(String((err && err.stack) || err)))
  .finally(() => process.exit(0));
