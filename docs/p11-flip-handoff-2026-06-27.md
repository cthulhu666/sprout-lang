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

## The flip — DONE 2026-06-27 (branch `fix/typed-codegen-tco`)

Both blockers closed (argv via #95 rebased in; TCO via the single-arm-match
cascade fix), so the flip landed:

1. **Escape hatch** — `--use-direct-codegen [--debug]` routes to the direct
   backend (the old `--emit-ir` behavior, `run_batch … "ir"`).
2. **Reroute** — `--emit-ir [--debug]` now routes to **typed** codegen via a new
   `"ir-typed"` phase in `run_batch` (calls `run_file_use_ir_codegen` per file).
   Batch support was a non-issue: every `--emit-ir` call site passes exactly one
   file, and `run_batch` already loops. Diagnostic phases and the bare check
   path are unchanged (they do no full codegen). Dispatcher: `compile_driver.sprout`.
3. **Seed re-bootstrapped via typed** — `refresh-seed` (which calls `--emit-ir`,
   now typed) converged to a **byte-identical typed fixed point at iteration 3**
   (iter1 direct → iter2 typed-from-direct-built → iter3 typed-from-typed-built ==
   iter2). This is the strong property `flip-readiness` alone does NOT check.
4. **Differential gates retargeted** — `tco-diff`, `ir_runtime_parity.sh`, and
   `cpr_differential_check.sh` now use `--use-direct-codegen` for their direct
   reference (since `--emit-ir` is typed now). The golden scripts already used
   `--use-ir-codegen`. New regression: `just flip-smoke` (`--emit-ir` ≡ typed;
   `--use-direct-codegen` reaches direct).
5. Full DoD revalidation: `test`, `test-stress`, smoke-shapes, bundle-smoke,
   argv-smoke, flip-smoke, compile-examples-stage1, run-example-canary,
   flip-readiness — all GREEN.

## Memory regression the flip exposed — FIXED 2026-06-28

Making typed the default surfaced that typed codegen used **~9× the memory** of
direct: compiling the whole compiler took **2.67 GB** (direct: 305 MB), and the
test suite went from <6 GB (direct) to **7.8 GB**. Root causes and fixes:

1. **`compiler_intrinsic_sigs` (codegen.sprout) — the dominant spike.** A single
   ~45-entry list literal `[(name, FnSig(Cons(ll_i64(),…))), …]`: a literal keeps
   ALL elements live until the spine is built, so the GC-rooting pass roots every
   prior element across each allocation — **O(N²) roots** (11 202 push/pops in one
   function!) and O(N²) transient allocation → **8.8 GB** to compile codegen.sprout
   alone. FIX: split the literal into 5 chunk functions joined by a monomorphic
   `append_sigs` (the file's local `list_append` is `List String` only). Bounds
   each function's live set to ~9 → **8.8 GB → 875 MB** for codegen, **2.67 → 1.4 GB**
   whole-compiler, **and ~2× faster**.
2. **Unbounded adaptive GC threshold (runtime).** The collect trigger is an OBJECT
   COUNT; the adaptive policy doubled it whenever <20 % was swept, with **no cap** —
   only ever growing, so garbage piled up without limit (10 GB+ with GC throttled).
   FIX: re-base the threshold on the LIVE set each collection
   (`threshold = clamp(live × factor, floor, cap)`), so heap/RSS tracks live data
   and shrinks again. Output is byte-identical (GC timing never affects codegen).
3. **Rooting per-op liveness was O(block²)** (`ir_rooting.rewrite_ops` recomputed
   `live_after` by re-walking the suffix per op). FIX: precompute all per-op
   `live_after` in one backward pass (`block_live_afters`).
4. **TCO correctness guard (a real bug the flip exposed).** With `tco_safe_hits`
   removed, a self-call whose result is used by a computational op BEFORE the
   return (union-find path compression: `root <- find_root(..);
   ref_write(.., BoundTo(root)); root`) was wrongly rewritten to an IRTcoBack
   back-edge, dropping the value (`opt --passes=verify`: use-of-undefined; caught by
   `compile-examples` on `ref_tutorial`/`ref_union_find`). FIX: only TCO a self-call
   that is the last op before its block terminator (`tco_tail_safe_hits`).
   *Caveat:* this guard is POSITIONAL (last-op-before-terminator), which assumes the
   call's result leaves the block only via its terminator/phi — true for what the
   translator emits today. If a future change lets a self-call result be consumed in
   a LATER block it dominates, the guard must become use-based (result used only by
   `IRPhi`/`IRRet`). Parity (98/98 OK, 0 TYPED-*) + the typed self-compile confirm no
   such pattern exists in the current corpus.

**Result:** whole compiler **1.4 GB / 35s** (was 2.67 GB / 78s; direct 305 MB);
test suite **3.38 GB** (was 7.8 GB; below the pre-flip direct suite). Regression
resolved. **Follow-up (P2):** typed still over-roots vs direct (~11 k vs 4 k on the
split table aggregate) — a liveness-precision gap, not a correctness issue.
