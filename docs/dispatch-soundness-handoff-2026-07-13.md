# Handoff: dict-dispatch soundness arc (2026-07-13)

Status: **handoff** — non-normative. Continuation notes for the retro follow-ups in
[retro-dict-dispatch-soundness-2026-07-13.md](retro-dict-dispatch-soundness-2026-07-13.md).
Backlog items live under `BACKLOG.md` → "Compiler Internals Follow-Ups → Dispatch
Soundness & Diagnostics" (items 1–4).

## Where things stand

The retro decomposed the `vec_sort_by` projection-sort crash (PR #176) into four
items. Two landed on master via **PR #178** (merged 2026-07-13):

| Item | State | Commit |
|------|-------|--------|
| **2** — `SPROUT_TRACE_DISPATCH` trace | **DONE** | `3db6187` |
| **1 phase 1** — dict-passing verifier (typed/Evidence) | **DONE** | `3d923da` |
| **1 phase 2** — return-type + IR-level checks | **NEXT** (design below) | — |
| **3** — loud dict-resolution fallback | scoped, deferred behind item 1 | — |
| **4** — canonical type-variable identity | not started (large, separate project) | — |

Working branch for the next piece already exists: **`verify-dispatch-phase2`**
(off master; no code changes yet — this doc is its only commit, pushed direct to
master).

## What item 1 phase 1 actually does (read before touching it)

`stdlib/compiler/verify_dispatch.sprout`, run in the check phase from
`compiler.sprout` (both `compile_phase_check` sites, right after
`resolve.resolve_program`). **Default-fatal.**

**Core idea (Core-lint style):** re-derive each constrained call's constraint-var
type from the callee's **SOURCE signature** — its written param types +
`where`-clause, in source names (`TFnDecl`'s `List ast.Param` +
`List ast.TypeConstraint`) — matched one-directionally (`match_te`) against the
call's concrete argument types. Then reject a call whose injected `TDict` head
disagrees with that derived truth. The derivation **never touches the generalized
env scheme / `prog_to_fresh` / `@constrained` markers** the resolver uses, so it is
a genuine independent cross-check and self-guards a
`canonicalize_constrained_markers` regression.

Key functions:
- `match_te(pattern: ast.TypeExpr, concrete: types.Type, theta)` — structural
  one-directional match binding source var names. Normalises all head names with
  `short` (= `after_last_dot`) to avoid qualified-vs-short false positives.
- `verify_call(param_tes, constraints, arg_types, dict_heads, pos)` — per-call
  check. Builds `theta` from **params only** (see phase-2 gap below).
- `collect_program` / `verify_program` (first mismatch) / `verify_stats`
  (coverage `(verified, skipped, mismatched)`).
- Walker keys calls by **`TVar` callee name** against a sig table built from
  `TFnDecl`s that have a `where`-clause AND full param annotations.

**Env controls:** `SPROUT_VERIFY_DISPATCH_OFF=1` downgrades a finding to a warning
(escape hatch); `SPROUT_VERIFY_DISPATCH_STATS=1` logs the coverage tally.

### Phase-1 scope — PRECISE (do not overstate; the commit/BACKLOG/debugging.md all say this)

- **CATCHES:** mis-resolution where the call's **value arguments** fix the
  constraint var to a concrete type → this is #176 (`k = Int`). Empirically
  `verified=2` on the projection sort; `mismatched=0` across corpus + compiler
  source; coverage `verified` 2–60 per dispatch-heavy test, `verified=6` on the
  whole compiler bundle (most compiler-internal dispatch is polymorphic/forwarded).
- **SKIPS (never a false alarm), so DOES NOT catch:**
  1. forwarded/polymorphic dicts inside a generic fn — the **#141** shape (truth
     is still a type variable);
  2. **return-type dispatch** — a constraint var only in the return position; `build_theta`
     matches params, never the return, so it's always underdetermined → skip;
  3. the `++`/`mconcat` **lowering-discard** — resolved dict correct but dropped in
     IR emission (`ast_to_ir.translate_append_operands`); a *lowering* fault a
     post-resolve pass structurally cannot see.

The scope boundary is **locked by a test**: `test_verify_dispatch.spr` asserts a
return-position wrong dict is `VerifySkip`, not `VerifyMismatch`.

## Phase 2 — the plan (this is the "NEXT")

Gaps (2) and (3) above are two DIFFERENT layers. Do them as separate changes.

### Part 2a — return-type dispatch (recommended first; typed-level, cheap)

The resolver already places the class var using the whole scheme *including the
return* (`infer.sprout:find_class_var_in_type`, called with `ret_t` at
`infer.sprout:1188`), and a return-type-dispatch call on a regular constrained
`fn` still carries an injected `TDict`. Phase 1 skips it only because
`build_theta` ignores the return.

**Change:** extend `verify_call`/`build_theta` to also match the callee's declared
return type (`TFnDecl`'s `Maybe ast.TypeExpr`) against the call's concrete `ret_t`
(`TCall`'s type), feeding the same `theta`. Then a return-position constraint var
gets bound and is verified like any other.

- Thread the declared return `TypeExpr` into the sig table (add it alongside
  `(param_tes, constraints)`), and the concrete `ret_t` into `check_call` (it's
  the `TCall`'s type field — confirm it is zonked to the concrete instance type at
  the call site; spot-check with a `Read`-style fixture).
- TDD: flip the existing scope-boundary test — a return-position wrong dict must
  become `VerifyMismatch`. Add a positive (correct return-type dict → `Ok`) and
  keep an underdetermined case (neither param nor return pins it → still skip).
- Verifier is **already fatal**, so there is no report-only→flip dance for the
  logic itself — BUT you MUST re-run the corpus + compiler-source sweep in
  report-mode-equivalent (temporarily read `SPROUT_VERIFY_DISPATCH_STATS` /
  inspect `mismatched`) to confirm the return-type extension introduces **zero**
  new false positives before landing, or a bad extension wedges `refresh-seed`.
- **Known residual gap:** class-method return-type dispatch via `TMethodRef` is
  NOT covered — the sig table is `TFnDecl`-based and the walker keys `TVar`
  callees. Document it; don't silently imply coverage.

### Part 2b — IR-level `++`/dict-null guard (harder, follow-on)

A pass over lowered IR asserting a Semigroup/constrained-call dict operand is not
`IRConst 0` (null). Guards the fixed (`PR #166`) `translate_append_operands`
lowering-discard against regression. Different layer (`sprout_ir` / `ir_lowering`),
less type structure — you correlate the threaded dict *argument* positionally, not
a typed `Evidence`. Lower urgency (bug already fixed). This is the only part that
needs the "typed-IR verifier" framing from the retro.

## Items 3 and 4 (unchanged from BACKLOG, summarised)

- **Item 3** — make `scan_prog_to_fresh_for_instance`'s precise-miss a hard error.
  The item-2 sweep showed that branch fires **0×** post-fix, so it's a dead-branch
  tripwire — BUT "corpus-clean ≠ unreachable": `resolve_obligation`'s header names
  `check2` / non-VarExpr-callee paths that can still reach it, so a hard error
  needs a full corpus regression pass. Now **unit-testable** (`test_resolve_evidence`
  drives `resolve` directly). Spare the benign concrete-constructor
  `first_concrete_arg(guess)` case (12 events, all `Functor`/`Foldable` on
  `Vec`/`List`). Sequence after item 1 phase 2.
- **Item 4** — canonical type-variable identity (unique IDs at binding, preserved
  through instantiate/generalize/unify). Removes all four reconciliation side
  tables (`prog_to_fresh`, `@fwd`, `@eta_fwd`, `@constrained`) and the whole bug
  class. Bootstrap-critical; a deliberate project with its own design process, not
  a reactive change. The north star (`project_typevar_identity_generalization_gap`,
  `project_dict_resolution_north_star`).

## Working notes / gotchas learned this session

- **Compiler-source change order** (`stdlib/compiler/*.sprout`): run `just fmt`
  BEFORE `just refresh-seed` (the seed fingerprint hashes the `.sprout` bytes;
  fmt-after-refresh makes it stale and forces a re-refresh). Then `refresh-seed`
  BEFORE `just test`. Delete `build/compile_driver_bin_stage1` before
  `refresh-seed` (stale-binary reuse trap).
- **Adding a fatal check that runs on the compiler's own source:** integrate
  **report-only** first, prove `mismatched=0` across corpus + `compile_driver`
  source, THEN flip fatal — otherwise `refresh-seed`'s iteration-2 (stage-2
  compiles the compiler) wedges. `refresh-seed` reaching "Fixed point at iteration
  2" IS the proof the fatal check accepts the compiler source.
- **New compiler module referencing `typed_ast.*`** must import it — the
  "Unknown constructor" error prints to **stdout** (as the `--emit-ir` `.ll`
  content), not stderr; check the `.ll`, and check `grep -c '^define'` (0 defines =
  the compile actually failed).
- `typed_ast` record arities to match against: `TypedInstanceMethod` has **5**
  fields (`name, params, ret, effs, body`); `TFnDecl` has 6
  (`name, params, ret, effs, constraints, body`).
- Verifier adds **one full typed-AST traversal per compile** — measured acceptable
  (full suite + compiler self-compile at normal speed).

## Quick commands

```
# fast unit-test iteration on the verifier (no seed refresh — module bundled from source):
./build/compile_driver_bin_stage1 --emit-ir stdlib tests/stdlib/compiler/test_verify_dispatch.spr \
  | clang - runtime/sprout_runtime.c -o /tmp/t -framework Security -framework CoreFoundation && /tmp/t

# coverage on any file:
SPROUT_VERIFY_DISPATCH_STATS=1 ./build/compile_driver_bin_stage2 --emit-ir stdlib <file> 2>&1 >/dev/null | grep stats:

# gates: just verify-dispatch-smoke  ·  just trace-dispatch-smoke  ·  just test
```

Key files: `stdlib/compiler/verify_dispatch.sprout` (verifier),
`stdlib/compiler/compiler.sprout` (integration + `report_verify_stats`),
`tests/stdlib/compiler/test_verify_dispatch.spr` (unit + scope-boundary),
`justfile` (`verify-dispatch-smoke`), `.forgejo/workflows/ci.yml` (wiring).
