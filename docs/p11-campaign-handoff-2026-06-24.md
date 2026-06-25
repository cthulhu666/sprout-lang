# PR 11 Campaign Handoff — typed-codegen flip, remaining work

**Date:** 2026-06-24
**Branch/HEAD at handoff:** `chore/gc-safety-tooling` @ `8b60c22` (PR #83).
`origin/master` @ `d9f662d`.
**Status:** Three PRs in flight, not yet merged. This doc enumerates the work that
remains *after* they land, ranked by impact toward the flip.

---

## TL;DR

The campaign goal is the **PR 11 flip**: make typed codegen (`--use-ir-codegen`,
`ir_pipeline.sprout`) the default, replacing direct codegen (`--emit-ir`,
`codegen.sprout`). The flip is gated on `scripts/ir_runtime_parity.sh` reaching
**zero typed failures**.

After the three in-flight PRs merge, the parity gate has ~7 remaining entries
(see `tests/IR_XFAIL`), in three root-cause classes. This doc is items **2–5**
from the ranked next-steps list (item 1 = "merge the 3 PRs", already in motion).

### Status — 2026-06-25

- **Item 2a (t0 dup-block) — DONE** (#84). astar/nqueens un-xfailed; both compile
  and run under typed codegen.
- **Item 2b (`__unresolved_` dict sentinel) — DONE.** Parity gate **TYPED-COMPILE 3→0**.
- **Parity is NOT yet zero — 2 TYPED-RUNTIME remain** (gate: 109 runnable, 107 OK):
  - **astar** — un-xfailing it in 2a surfaced a runtime *mismatch*: astar prints
    wall-clock timing, so typed output can **never** byte-match direct. This is a
    measurement artifact, not a codegen bug (the algorithm result `path=198 steps`
    is identical; typed is faster). Needs its own fix before parity-zero: strip the
    timing line before compare, exclude astar from the parity corpus, or drop the
    timing print. **New follow-up — not covered by items 2–5 below.**
  - **tuples** — item 3 (type-directed tuple/Bool rendering), feature-sized.
- **Net remaining distance to the flip:** astar-timing (small) + item 3 (tuples) +
  item 5 (the flip mechanics). Item 2 is fully closed.

### Precondition — merge order (item 1, in flight)

Merge as CI greens, in this order (each branches off the prior):
1. **#81** `ir_rooting: root constructor/tuple operands across store-after-alloc GC` — the P11-2e fix.
2. **#83** `gc-safety tooling` — **retarget to master** after #81 lands, then merge.
3. **#82** `runtime: render tuples structurally in print` — **rebase** (BACKLOG conflict) after #83, then merge.

All work below should branch off **master after all three land** (it needs the
rooting fix + the GC-safety tooling). Until then, only read-only investigation is safe.

---

## Item 2 — the 4 TYPED-COMPILE blockers (highest remaining parity impact)

These are **hard parity gates**: the file does not compile under typed codegen.
Clearing them removes 5 of the ~7 remaining `tests/IR_XFAIL` entries. Two
independent root causes → splittable across two subagents.

### 2a. `'t0'` dup-block (codegen)

`tests/IR_XFAIL`:
```
examples/astar.sprout    # multiple definition of local 't0' (pre-existing codegen dup-block)
examples/nqueens.sprout  # multiple definition of local 't0' (pre-existing codegen dup-block)
```

- **Symptom:** LLVM verifier rejects the module — `multiple definition of local 't0'`.
- **NOT the init-globals `t0` collision** already fixed in #81 (that was a root-slot
  name clash in `__sprout_init_globals`; this is a *different* dup-block in ordinary
  function codegen).
- **Diagnosis approach:** emit IR for `astar.sprout` via the typed path, find the two
  `%t0 =` definitions in the same function, trace which codegen step reuses the index.
  Likely a temp-index counter not threaded/reset across a particular lowering branch.
  `just llvm-where <ll_file> <line>` maps the verifier error line to its enclosing
  Sprout function.
- **Acceptance:** `astar.sprout` + `nqueens.sprout` compile clean under
  `--use-ir-codegen` and run to completion; remove both from `IR_XFAIL`.
  (`nqueens` may also hit a timeout under the slow path — confirm it *runs*, not just
  compiles; if it only times out, note that separately, do not silently leave it xfail'd.)

### 2b. `__unresolved_Eq__` / `__unresolved_ToString__` (typeclass dispatch) — RESOLVED 2026-06-25

`tests/IR_XFAIL`: `test_deriving_eq_parametric`, `test_deriving_to_string`,
`test_eq_operator_adt_dispatch` — all cleared.

- **Root cause (not feature-sized — a consumer asymmetry):** `lowering.lower_program`
  mints `__unresolved_<Class>` `TVar` sentinels (`lowering.sprout:1255`) for inner
  typeclass dicts whose type var is *free* — e.g. `ToString a` for `to_string(Empty)`
  where `Empty : Box a`. There is genuinely no witness to resolve (and none is needed:
  `Empty` carries no `a`, so the derived method never reaches the inner dict). This
  happens on **both** codegen paths. The two backends then diverge on the unknown-var
  case: `codegen.sprout:1900` null-fills any unknown name to `i64 0` (`# unknown —
  return zero`); `ast_to_ir.sprout` hard-errored. Pure consumer asymmetry — *not* the
  deriving-v1 / inference rework the earlier estimate assumed.
- **Fix:** `ast_to_ir` now null-fills the `__unresolved_`-prefixed sentinel (`i64 0`),
  matching direct codegen, while keeping the hard error for every *other* unknown name
  (genuine unbound vars are caught at typecheck, before lowering — so an unknown name at
  codegen is always an internal sentinel). Leaves `ast_to_ir` stricter than direct.
- **Invariant (validated by run-not-just-compile):** a dict goes unresolved iff its type
  var is free iff no value of that type is present iff the method body never reaches it —
  so the null is provably safe. All three files + `test_unresolved_dict_nullfill` compile,
  run, and byte-match direct output under `--use-ir-codegen`.
- **Result:** parity gate TYPED-COMPILE 3→**0**. There was no Layer-2 lowering bug for
  these cases; the unresolved dicts are exactly the never-invoked ones.

---

## Item 3 — PR B: type-directed tuple/ADT rendering (last TYPED-RUNTIME blocker)

- **Symptom:** `examples/tuples.sprout` — a tuple element that is a **static string**
  renders as a raw pointer under typed codegen, not the string text. PR #82's runtime
  change (`SPROUT_HEAP_TUPLE` in `print_inline_value`) renders tuples *structurally* but
  only handles word-sized elements; it cannot recover the *type* of each slot, so it
  can't tell "this i64 is a heap string pointer" from "this i64 is an Int". Pretty `Bool`
  (`True`/`False` vs `1`/`0`) has the same root cause.
- **Why it's the right fix (option B, user-chosen):** the runtime can't carry type info;
  rendering must be **type-directed at the Sprout level**. Route `print` through a derived
  `ToString` with a tuple instance, so each element is rendered by its own `ToString`
  instance (string → text, Bool → `True`/`False`, nested tuple → recursion).
- **Sequencing:** do this **after item 2b** — it overlaps the deriving/typeclass machinery,
  and a working `ToString` dispatch on the typed path is a prerequisite.
- **Scope:** feature-sized. Per AGENTS.md this is a language/stdlib change → TDD failing
  test first, spec/docs sync, `just fmt`, full `just test`, `compile-examples-stage1`.
- **Acceptance:** `tuples.sprout` runs and prints tuple contents (strings as text, Bools
  as `True`/`False`) under `--use-ir-codegen`, matching the direct path / golden output
  (`tests/golden/runtime/`). This is the **last TYPED-RUNTIME parity entry** — clearing it
  + item 2 brings parity to zero.

---

## Item 4 — ctors/match stress UAFs (correctness, NOT parity)

- **What:** the 2 false-greens that `test-stress` (added in #83) surfaced —
  `test_ir_codegen_ctors` and `test_ir_codegen_match` crash under `SPROUT_GC_STRESS=1`
  (currently in `STRESS_XFAIL` in the `justfile` `test-stress` recipe).
- **Class:** residual GC-rooting use-after-free, same family as the P11-2e store-after-alloc
  bug fixed in #81 — but a path that fix did not cover.
- **Why it ranks below items 2–3:** parity is measured at the **default GC threshold**,
  where these pass. They are real bugs under GC pressure but **do not block the flip**.
  Do them when correctness-hardening, not when racing to parity-zero.
- **Diagnosis tooling (all landed in #83):**
  - `just test-stress` — reproduces (re-runs the curated subset under `SPROUT_GC_STRESS=1`).
  - `SPROUT_GC_DISABLE=1` — true GC-off, for bisecting whether a crash is GC-caused.
    (NOT `SPROUT_GC_THRESHOLD=huge` — that still collects with reason "threshold".)
  - `just gc-trace <file> <watch_fn>` — single-run lldb free-tracer (`scripts/gc_free_trace.py`);
    watch_fn must receive the victim as arg0. Heap addresses are unstable across runs, so
    use the single-run trace, not cross-run address comparison.
- **Acceptance:** `test_ir_codegen_ctors` + `test_ir_codegen_match` green under
  `SPROUT_GC_STRESS=1`; remove from `STRESS_XFAIL`. Add a deterministic IR-rooting
  assertion (mirroring `test_ir_rooting.spr`) once lldb confirms the mechanism.

---

## Item 5 — the flip itself (Phase 2)

Mechanical, once items 2 + 3 bring `ir_runtime_parity.sh` to zero typed failures.

1. Add a `--use-direct-codegen` escape hatch (keep direct codegen reachable for one
   release as a fallback / differential oracle).
2. Route `--emit-ir` (and the default compile path) through the typed pipeline.
3. `just refresh-seed` and stage `bootstrap/compile_driver.ll` (the seed now bootstraps
   via typed codegen). Use the 2-step bootstrap if the committed seed predates the flip
   (docs/debugging.md §2-Step Bootstrap Protocol).
4. Run the **full** DoD: `just fmt`, `just test`, `just compile-examples-stage1`,
   smoke shapes, bundle smoke, `verify-bootstrap-fixed-point`, the example canary
   (AGENTS.md DoD #11).
5. Keep `scripts/cpr_differential_check.sh` / the direct-vs-typed differential running
   in CI for the deprecation window before deleting `codegen.sprout`.

- **Acceptance:** `ir_runtime_parity.sh` reports zero typed failures; the seed bootstraps
  fixed-point via typed codegen; full suite + canary green.

---

## Operational notes (cost-savers, carried from prior handoff)

- **Compiler-source change order:** `rm -f build/compile_driver_bin_stage1` (mtime trap,
  memory `feedback_refresh_seed_stale_binary`) → `refresh-seed` (wrap in
  `scripts/memwatch.sh 4096 1 --`) **before** `just test` (memory
  `feedback_refresh_seed_before_test_for_compiler_changes`). `--use-ir-codegen` uses the
  seed-built binary's own codegen, so rooting/codegen source changes only take effect
  after refresh-seed. (A library-level test importing `ir_pipeline` recompiles from
  working-tree source and reflects changes immediately.)
- **Linking needs frameworks:** `clang <ir.ll> runtime/sprout_runtime.c -O2
  -framework Security -framework CoreFoundation -o <bin>`.
- **Timings:** refresh-seed ≈ 105s, full `just test` ≈ 245s, full parity ≈ 527s.
  Run `just test` exactly once per verification (memory `feedback_test_suite_once`);
  output lands in `/tmp/sprout_test_<session_id>.txt`.
- **Bash hooks** block `cat`/`head`/`tail`/`sed`/`awk` with file args — use Read.

---

## Pointers

- `tests/IR_XFAIL` — the live parity-blocker list (items 2 entries are lines 106–114).
- `scripts/ir_runtime_parity.sh` — the flip gate (+ golden mode added in #82).
- `tests/stdlib/test_ir_rooting.spr` — the rooting oracle; the IR-assertion shape for items 2a/4.
- `docs/p11-2e-PR-A-rootcause-2026-06-23.md` — the store-after-alloc root-cause writeup (#81).
- memory: `project_pr11_typed_codegen_campaign`, `project_gc_stress_oracle`,
  `project_deriving_v1_design`, `project_return_type_typeclass_dispatch`,
  `project_trivial_accessor_codegen_bug` (relevant to the `'t0'` dup-block class).
