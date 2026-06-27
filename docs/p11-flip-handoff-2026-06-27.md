# PR 11 Flip Handoff — 2026-06-27

**Goal:** make typed codegen (`--use-ir-codegen`) the default, retiring direct
codegen (`--emit-ir`). See `docs/p11-campaign-handoff-2026-06-24.md` for the
older plan — but note its Item 5 ("the flip is mechanical") is **wrong**; see
below.

## TL;DR

Parity is **109/109 OK, 0 TYPED-***. That was thought to be the flip gate. It
is **not**. The real gate is **typed-codegen self-compile correctness** — the
typed-built compiler must compile the compiler to a fixed point. The
flip-readiness dry-run found two blockers parity structurally cannot catch.

- **Blocker #1 (argv) — FIXED**, PR **#95** `fix/typed-entrypoint-argv` (open).
- **Blocker #2 (backend SIGSEGV) — OPEN. This is the next task.**

## Why parity wasn't enough

`scripts/ir_runtime_parity.sh` runs every corpus binary with **no argv**
(`</dev/null`), so `argv_all()` is exercised by **0 of 109** files. The compiler
is the first program that reads its own argv. Only self-compilation hits it.

## The flip-readiness dry-run (the real gate — re-run this)

```sh
# 1. typed self-compile of the compiler
build/compile_driver_bin_stage1 --use-ir-codegen stdlib \
  stdlib/compiler/compile_driver.sprout > /tmp/typed_self.ll
opt --passes=verify /tmp/typed_self.ll -o /dev/null            # currently: clean
clang /tmp/typed_self.ll runtime/sprout_runtime.c -O2 \
  -framework Security -framework CoreFoundation -o /tmp/stage2_typed
# 2. does the typed-built compiler work?
/tmp/stage2_typed --emit-ir stdlib <small.sprout>   # OK (after #95)
# 3. FIXED-POINT — the failing step:
/tmp/stage2_typed --emit-ir stdlib stdlib/compiler/compile_driver.sprout
#   -> exit 139 (SIGSEGV), 8MB, 1s, 0 bytes
```

## Blocker #2 — typed-built compiler SIGSEGVs self-compiling a multi-module program

- **Symptom:** the typed-built compiler compiles a small **standalone**
  `module main` correctly (`flipsmoke.sprout` → `(1, true, hi)`), but
  **segfaults at startup** (exit 139, `peak_rss=8MB`, `wall=1s`, 0 bytes) on
  `compile_driver.sprout`, which `import`s the whole compiler.
- **Crashes early, not under load** → NOT a GC-at-scale/rooting bug. The only
  difference from the working case is **multiple `import`s**, so suspect a
  typed-codegen miscompile in the **module-loader / bundler** path
  (`stdlib/compiler/module_loader.sprout`, `bundler.sprout`).
- **Direct-built compiler self-compiles fine** (that's how `refresh-seed`
  works) → typed-codegen-specific.
- **Next step:** lldb the typed-built binary compiling the compiler:
  ```sh
  lldb -- /tmp/stage2_typed --emit-ir stdlib stdlib/compiler/compile_driver.sprout
  (lldb) run        # bt at the crash
  ```
  Then root-cause in the typed lowering of the bundler/module-loader, mirroring
  how `codegen.sprout` (direct, correct) lowers the same construct. Same A/B
  method as the rest of the campaign (see
  `[[project_pr11_typed_codegen_campaign]]`).
- **Possible further blockers behind #2** — keep re-running the dry-run after
  each fix until the typed-built compiler reaches a self-compile **fixed point**.

## What landed this session

- **#93** (merged) — `print(tuple)` → `to_string` at inference; parity → 0 TYPED-*.
- **#94** (merged) — `test-ir` is now a **required** CI gate on PRs + master.
  Caveat: no branch protection on the repo, so "required" is *visible* (red
  workflow) but does not hard-block merges; auto-merge fires on `ci=pending`.
- **#95** (open) — argv fix (blocker #1) + `just argv-smoke` regression.
- BACKLOG: blockers #1/#2, the `print`-redesign + prelude-default reassessments
  (P3), a REPL nested-tuple SIGSEGV (P2, separate), and a CI-enforce-parity note
  (item 8: `run-example-canary-ir` only checks exit 0, so the golden parity diff
  is still local-only).

## Once self-compile is clean (the actual flip, then)

1. Add `--use-direct-codegen` escape hatch (route to today's `run_file_ir`).
2. Route `--emit-ir` + default through the typed pipeline (note: typed is
   single-file `run_file_use_ir_codegen`; `--emit-ir` is batch `run_batch` —
   typed needs batch support). Dispatcher: `compile_driver.sprout:318 main`.
3. `refresh-seed` (seed now bootstraps via typed); 2-step bootstrap if the
   committed seed predates the flip.
4. Full DoD + `verify-bootstrap-fixed-point` + the dry-run as a permanent gate.
