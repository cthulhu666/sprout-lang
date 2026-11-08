# Test coverage

Manual tool, not a gate: `mise exec -- just coverage`. Nothing in `gate`, `dod` or CI runs it
(`just gate-audit` does not flag it: `coverage` is not a verification-named recipe, and its
scripts sit in a subdirectory the audit does not scan).

macOS only (`sancov_dump.c` uses `mach-o/dyld.h`, links use `-framework`). Needs Homebrew LLVM
(`clang`, `opt`, `llvm-profdata`, `llvm-cov`) on PATH, as for the rest of the repo.

## What it measures

Coverage of whatever `just test` runs. Nothing else is executed, so a feature tested only by a
separate smoke gate (`task-io-smoke`, `repl-smoke`, ...) shows as uncovered.

| Part | Unit | Source |
|---|---|---|
| Compiler (`stdlib/compiler/`) | functions | the instrumented compiler process during `just test`, unioned with compiler functions run inside the unit-test binaries |
| Stdlib (`stdlib/**`, prelude) | functions | every test binary `just test` builds and runs |
| C runtime (`runtime/*.c`) | lines | `llvm-cov` over the test binaries plus `tests/c_runtime/run.sh` |

Denominator rules (compiler and stdlib):

- One source function is one unit. Compiler variants `$u`, `_argw` and `_worker` are merged into their
  base function: covered if any variant ran.
- Instance methods (`__tc_*`) count as functions.
- Not counted: lambdas, eta thunks, `__cm_*` and other `__*` generated functions,
  `__sprout_init_globals`, `main`.
- A source function never emitted into the binary (dead from `main`, or not imported by any test)
  counts as uncovered. The `noemit` column shows how many.
- Runtime C is excluded from the Sprout figures; it has its own line figure.

Reports also give instruction-weighted and block figures. Block % runs low: sancov counts every IR
block, including the failure and unreachable blocks the compiler emits for exhaustive matches.

## Run

```
mise exec -- just coverage        # runs bootstrap-from-seed first
COV_DIR=/some/dir just coverage   # output elsewhere (default build/coverage, gitignored)
JOBS=4 just coverage              # test-binary parallelism (default 10)
```

Results are in `$COV_DIR/results/`:

- `compiler_report.txt`, `stdlib_report.txt`: headline figures, per-module table, largest uncovered functions.
- `runtime_report_{all,tests,crt}.txt`: `llvm-cov report` per source file.
- `runtime_uncovered.json`: runtime functions never entered, with size.
- `compiler_fn.json`, `compiler_ir_rows.json`, `stdlib_fn.json`: per-function data (which tests entered a stdlib function).

Logs: `compiler_run/just_test.log`, `stdlib_run/work/<n>/` (one dir per test binary, `status` file
says ok / emit_fail / run_exit_N / suite_failed), `crt_run/log`.

## How it works

1. `build_compiler.sh`: the stage-1 compiler re-emits `compile_driver.sprout` to IR.
   `instrument_ll.sh` runs `opt -passes=sancov-module,pgo-instr-gen,instrprof` on it: a bool flag
   and a PC-table entry on every block (`sancov_dump.c` writes them at exit) plus PGO entry
   counters. Linked with an `-O2` runtime.
2. `run_compiler_cov.sh`: **the stage-1 swap.** Copies the instrumented compiler over
   `build/compile_driver_bin_stage1` (gitignored), runs one full `just test`, then restores the
   original. The backup is `cmp`-checked before the swap; restore runs on any exit (trap) and is checked against the original's sha256. If a run is killed hard
   (SIGKILL), the backup `$COV_DIR/stage1.swap-backup` stays and the next run refuses to start;
   the error says how to restore. `run_all.sh` also checks stage-1 is unchanged at the end.
3. `run_stdlib_cov.sh` / `stdlib_one.sh`: for every input of `just test` (`tests/stdlib`,
   `tests/stdlib/compiler`, `tests/ide`, `tests/conformance/run`, `tests/renaming_smoke`), emit IR
   with a copy of the plain stage-1, instrument it, link against a runtime built with
   `-fprofile-instr-generate -fcoverage-mapping`, and run it.
4. `run_crt_cov.sh`: runs `tests/c_runtime/run.sh` unchanged, with `shim/clang` first on PATH
   adding the coverage flags.
5. `analyze_*.py`, `analyze_runtime.sh` (shared code in `covlib.py`) produce the reports.

Every instrumented run sets `LLVM_PROFILE_FILE` under `$COV_DIR`; `run_all.sh` fails if a
`default.profraw` shows up in the repo root.

## Cost

About 13 to 16 minutes on an M-series Mac (2026-11-08, 10 jobs): the full `just test` under the
instrumented compiler takes 6.5 to 9.5 min, the ~880 instrumented test binaries ~5 min, `tests/c_runtime`
~40 s, analysis ~30 s. Disk: ~2.3 GB under `build/coverage`, nearly all of it
`stdlib_run` (one binary per test). `just test` is slower while the swap is in place; do not run
other work against this checkout meanwhile.

## Caveats

- Only `just test`'s inputs count (see above).
- The compiler figure is a union of two sources (the process, and unit-test binaries), so it is a
  function-level number only; block data exists for the process alone.
- Numbers are for one commit. Compare runs of the same master, not across big refactors.
