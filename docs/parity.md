# WASM versus native parity

A recipe that declares

```yaml
runtime:
  modes: [wasm, native]
```

is promising that an operation means the same thing whichever runtime executes
it. BioChef relies on that promise in a way that is easy to miss: the editor
runs the **WASM** build in the browser through Aioli, and the Agent runs the
**native** build as a subprocess. The same workflow, run in two places, is
expected to produce the same result.

Nothing checked that until now.

## What was actually being tested

`hub.py test` runs `runtime/native/<bin>`, and only that. Three consequences,
all of which made the existing signal weaker than it looks:

- **WASM was never executed by any test, anywhere.** The artifact every browser
  user runs is the one nothing ran.
- **A missing native binary is a pass.** `test.py` returns `True` when the
  binary is absent, so the 24 of 31 recipes that declare no native build are
  reported as passing without anything being executed.
- **An empty output is a pass.** Only a *wrong* detected type fails; an empty
  one prints a warning.

And separately, `hub.py test` prints the tools that failed rather than raising,
so a genuine failure leaves the workflow green. That last one is
[#38](https://github.com/ieeta-pt/biochef-hub/issues/38) and is not fixed here.

## What this does

```
python hub/hub.py parity --registry-dir registry --report parity-report.json
```

For every bundle whose recipe declares both runtimes, it builds one invocation
from the operation's required parameters and its declared example inputs, runs
that same invocation against both builds, and compares stdout, the exit status,
and any declared file outputs.

Both runtimes are handed byte-identical inputs and the identical argument
strings — file arguments are relative names, so a tool that echoes its own
arguments does not look divergent because the harness passed a path to one side
and a basename to the other.

It runs the WASM artifact **exactly as published**, built for `web,worker` with
`MODULARIZE=1` and `INVOKE_RUN=0`. Rebuilding it in a node-friendly
configuration would compare an artifact nobody runs. That works because the
module is handed its `wasmBinary` directly and so never reaches the environment
detection that would otherwise look for a browser `fetch()`.

## How a run is judged

| outcome | meaning |
|---|---|
| `match` | ran both ways, agreed |
| `agreed-failure` | both runtimes failed identically — parity, but no evidence the operation works, so it never satisfies `--min-compared` |
| `DIFFER` | the two disagree; fails the run |
| `SKIP` | declared both runtimes and could not be compared anyway; fails the run |
| `--` | single-runtime recipe, never in scope |

The distinction between the last two is the one that matters. A recipe that only
ever claimed `wasm` has nothing to compare and must not fail a pull request. A
recipe that claimed **both** and could not be compared did not keep its promise —
a missing artifact, an input type with no example — and letting that pass
quietly is how a parity gate becomes a parity suggestion.

`--min-compared` is a floor for a run over the whole catalogue, where the caller
knows roughly how much *should* have been compared. It defaults to 0, because
per-PR validation legitimately sees nothing in scope.

## Checking the checker

```
python hub/hub.py parity --self-test
```

A comparison that silently stops comparing looks exactly like a catalogue that
agrees, so the harness is asked to prove it still fails on a divergence before
its report is believed. CI runs this immediately before the report.

End to end, the harness was validated against ksw2 by rebuilding its WASM side
with a different default match score and confirming the report said `DIFFER`
with the two scores side by side, and that the command exited non-zero. That
exercise found a real bug in `run_wasm.mjs`: `callMain` **returns** main's value
rather than throwing when a program reports failure by returning, so reading
only the thrown `ExitStatus` recorded 0 for every such tool and would have
reported false divergences across the catalogue.

## What it does not cover

**One invocation per operation**, built from the required parameters. Anything
reachable only through an *optional* parameter is not exercised.

ksw2 is the clearest example, and the most uncomfortable one. It selects among
seven alignment implementations — scalar and SSE variants of the same
algorithms — through an optional `-t`, so the harness compares the default path
and nothing else. That is precisely where divergence is most plausible: on x86
the native build is compiled `-march=native` against real SSE, while the WASM
build goes through emscripten's SSE-over-wasm-SIMD compatibility headers. Two
implementations of the same intrinsics.

A manual sweep of 385 invocations across all seven ksw2 algorithms, four
sequence lengths, four divergence rates, band-width and Z-drop variations, and
seven adversarial shapes (homopolymers, tandem repeats, `N`s, one-long-one-short)
found **no divergence**. That is a real result for ksw2 and not a general one.

Covering optional parameters properly needs cases declared per operation, which
needs a recipe schema key that does not exist yet —
[#39](https://github.com/ieeta-pt/biochef-hub/issues/39).
