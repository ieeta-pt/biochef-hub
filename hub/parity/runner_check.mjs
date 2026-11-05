// Checks run_wasm.mjs against a stub module, so the two things it got wrong
// cannot come back quietly.
//
// Both bugs this pins down were found by compiling a purpose-built probe to
// WASM and comparing it with its native build, and neither was visible from
// reading the code:
//
//   * stdin was installed with FS.init() on the RESOLVED module, which throws
//     ErrnoError because the filesystem is already initialised. The program
//     still ran and read zero bytes, so every stdin-driven tool would have
//     looked like it diverged when only the harness had failed to feed it.
//   * declared file outputs were read by their literal name while the native
//     side searched by stem. A bundle declaring "out" against a tool writing
//     "out.txt" would have had a file on one side and null on the other.
//
// The stub encodes emscripten's actual contract as observed from a real 4.0.18
// module: stdin arrives as a `stdin` property on the factory options and is
// drained a byte at a time until it returns null; outputs land in the module
// filesystem under whatever name the tool chose. That makes this a regression
// guard rather than a proof -- the proof was the probe -- but it is the part
// that runs every time, and it needs no toolchain.
import { execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const work = mkdtempSync(path.join(tmpdir(), "parity-runner-check-"));

// A stand-in for an emscripten module. It reports what the runner handed it.
writeFileSync(path.join(work, "stub.js"), `
export default async function factory(options) {
  const files = new Map();
  let drained = "";
  if (options.stdin) {
    for (;;) {
      const byte = options.stdin();
      if (byte === null || byte === undefined) break;
      drained += String.fromCharCode(byte);
    }
  }
  return {
    FS: {
      writeFile: (name, data) => files.set(name, Buffer.from(data)),
      readdir: () => [".", "..", ...files.keys()],
      readFile: (name) => {
        if (!files.has(name)) throw Object.assign(new Error(), { name: "ErrnoError", errno: 44 });
        return files.get(name);
      },
    },
    callMain: (args) => {
      options.print("stdin=" + drained);
      options.print("args=" + args.join(","));
      // The tool chooses the on-disk name; the bundle only declared a stem.
      files.set("out.txt", Buffer.from("from stdin: " + drained));
      return 0;
    },
  };
}
`);
// The runner derives the .wasm path from the .js path and reads it eagerly.
writeFileSync(path.join(work, "stub.wasm"), Buffer.from([0]));

const job = path.join(work, "job.json");
const out = path.join(work, "out.json");
writeFileSync(job, JSON.stringify({
  module: path.join(work, "stub.js"),
  cases: [{
    args: ["-x", "1"],
    files: { "input_in": Buffer.from("FILE").toString("base64") },
    stdin: Buffer.from("HELLO").toString("base64"),
    collect: ["out"],
  }],
}));

execFileSync("node", [path.join(here, "run_wasm.mjs"), job, out]);
const [result] = JSON.parse(readFileSync(out, "utf8"));

const failures = [];
const expect = (label, condition) => { if (!condition) failures.push(label); };

expect("stdin must reach the module, not be dropped on the floor",
  result.stdout.includes("stdin=HELLO"));
expect("arguments must be passed through unchanged",
  result.stdout.includes("args=-x,1"));
expect("a declared output must be matched by stem, as the native side matches it",
  result.files.out !== null &&
  Buffer.from(result.files.out, "base64").toString() === "from stdin: HELLO");
expect("a clean run must report exit 0",
  result.code === 0);
expect("a run without errors must not report one",
  result.error === null);

for (const failure of failures) console.log(`  [FAIL] ${failure}`);
if (failures.length) {
  console.log(`The WASM runner failed ${failures.length} of its own checks`);
  process.exit(1);
}
console.log("WASM runner check passed: stdin is delivered and outputs match by stem");
