# PR 11 — Item 2 investigation (read-only diagnosis)

**Date:** 2026-06-24
**Status:** Read-only diagnosis only. No source edits (gated on the 3 in-flight PRs
landing per `docs/archive/p11-campaign-handoff-2026-06-24.md`, and on user approval for
compiler-source changes per AGENTS.md Collaboration Rule 5).
**Binary used:** snapshot of `build/compile_driver_bin_stage1` at `/tmp/cd_invest_stage1`
(isolated from the concurrent merge agent's `refresh-seed`).

---

## Item 2a — `'t0'` dup-block (astar / nqueens) — ROOT CAUSE FOUND

### Symptom
`opt --passes=verify` rejects the typed-codegen module:
```
examples/astar.sprout:6335: error: multiple definition of local value named 't0'
  %t0 = call i64 @time_now_micros()
examples/nqueens.sprout:5560: same
```

### Root cause
Both files define a function with a **parameter literally named `t0`**:
```
examples/astar.sprout:112   fn print_result(n: Int, total: Int, t0: Int) -> Unit !{IO}
examples/nqueens.sprout:57   fn print_result(n: Int, c: Int, t0: Int) -> Unit !{IO}
```
The parameter lowers to LLVM `%t0`. The typed path's synthetic temporaries also use
the `%t<N>` namespace and, because `ir_pipeline` streams **per function** (the design
that cut peak RSS 2 GB→240 MB), the temp counter **resets to 0 per function** — so the
first body temp is `%t0`, colliding with the parameter.

This is NOT a counter-threading bug and NOT the `__sprout_init_globals` root-slot clash
fixed in #81. It is a **name-namespace collision** between synthetic `%t<N>` temps and
source-derived SSA names.

### Why direct codegen does not hit it
Direct codegen (`codegen.sprout`) uses a **module-global monotonic** temp counter. By
the time `print_result` emits, temps are at `%t5710+` (verified in `astar_direct.ll`),
so they never collide with the `%t0` parameter. Direct codegen is safe only *by luck of
ordering*, not by design — the first-emitted function could in principle still collide.

### The project already solved this class — for lambdas only
`ast_to_ir.sprout:514-518`:
```
# User fns use prefix "%t" → %t0, %t1, …
# Lifted lambda bodies use prefix "%t$" → %t$0, %t$1, … to avoid colliding
# with user-written param names like "t0" (which produce "%t0" in LLVM).
# '$' cannot appear in Sprout-source identifiers, guaranteeing disjointness.
```
The `$`-disjointness trick is the established fix; it was applied to lifted lambda
bodies but **not** to ordinary user-function bodies. (Rare in practice — almost nobody
names a parameter `t0`.)

### Recommended fix — bulletproof-by-construction, not by-discipline

The robustness goal is `{synthetic temp names} ∩ {source-derived names} = ∅`. You can only
guarantee that by controlling ONE side completely. Two ways:
- Make the **synthetic side unspeakable** (`%t$`, `$ ∉ source identifiers`): one
  centralized generator; ANY source binding (param, let, pattern var, lambda param) is
  safe automatically.
- Make the **source side never appear** (rename params to `%pN`, keep source names only
  as dict keys): requires routing EVERY source binding-emission site through the
  generator. Miss one *kind* (e.g. match-pattern bindings, lambda params) and it leaks.

**Controlling the synthetic side is strictly better here:** the synthetic side is small
and already centralized in `fresh_name`; the source side is many binding sites. The
`%t$` approach is also **immune to leak-inventory completeness** — the empirical "leak is
parameters only" claim below was checked on a *sample* (5 names; match-pattern and lambda
bindings NOT exhaustively verified), and `%t$` does not depend on it being complete.

**The fix (makes `%t$` by-construction, closing the only residual — a future emitter
passing `"%t"`):**
1. Set all three current emitter sites to `"%t$"`:
   - `ast_to_ir.sprout:3936` (`translate_user_fn` → `translate_body`)
   - `ast_to_ir.sprout:4282` (`synthesize_init_body_loop`, `__sprout_init_globals` body)
   - `ir_rooting.sprout:729` (prefix-picker → unconditional `"%t$"`)
   (All three together: otherwise a function gets a body/rooting prefix mismatch.)
2. **Eliminate the `prefix` parameter** from `fresh_name`/`translate_*`/the rooting
   prefix-picker once every caller uses `"%t$"`. The param is then vestigial; deleting it
   makes `"%t"` *impossible to pass* — the bug cannot recur via a future emitter.
   `fresh_name(idx)` becomes `"%t$" ++ int_to_string(idx)`.
3. **Lock the lexer invariant** with a one-line test asserting `$` is rejected in a source
   identifier. This is the single remaining assumption `%t$` rests on; pin it so it can't
   silently change.
4. (Optional defense-in-depth) a per-function dup-local-name assertion in the emitter that
   fails loud with the *Sprout* function name — better than relying on the
   `opt --passes=verify` line as the only backstop.

Params keep their readable source names (`%n`, `%total`); only synthetic temps change
`%tN`→`%t$N` (already unreadable). Same blast radius as any naming change (full seed +
golden regen) but **lower implementation risk** than param-rename (no param-*reference*
threading). Update comments at `ast_to_ir.sprout:514-518,3864,3934`,
`ir_rooting.sprout:568-569,716-729`.

**Why NOT the structural param-rename:** same blast radius, MORE implementation risk
(must thread every param reference + correctly enumerate all leaking binding kinds), and
it makes every `%n`→`%pN` (readability loss). It is the *less* robust choice here — the
bigger refactor is not the more bulletproof one.

**Blast radius (real constraint, not a reason to do a narrower patch):** this renames
every temporary in every user function across all emitted IR — the bootstrap seed and
every golden-IR fixture under `tests/golden/` regenerate. The seed regenerates anyway on
any compiler-source change; golden fixtures are mechanical to regenerate. A narrower
patch (mangle only `^t[0-9]+$` parameters) would shrink the diff but leaves the two
namespaces overlapping — i.e. it fixes the symptom, not the root cause. Not recommended.

### DoD for the fix (when unblocked + approved)
- Regression test: a `.spr` with a fn parameter named `t0` (and `t1`), compiled via
  `--use-ir-codegen`, asserting `opt --passes=verify` passes. Mirror the shape of
  `tests/stdlib/test_ir_rooting.spr`. Confirm it FAILS on unfixed code first (TDD).
- `rm -f build/compile_driver_bin_stage1` → `refresh-seed` (wrapped in
  `scripts/memwatch.sh 4096 1 --`) BEFORE `just test` (compiler-source change order).
- Regenerate affected golden IR fixtures.
- Remove `examples/astar.sprout` + `examples/nqueens.sprout` from `tests/IR_XFAIL`.
- Confirm both **run to completion** under typed codegen (nqueens: confirm it runs, not
  just compiles — note any timeout separately per handoff item 2a).
- `just fmt`, full `just test`, `just compile-examples-stage1`, smoke shapes, bundle
  smoke, `verify-bootstrap-fixed-point`, example canary.

---

## Item 2b — `__unresolved_Eq__` / `__unresolved_ToString__` — ISOLATED (parametric deriving)

### Symptom
Typed emit aborts with a single error line (no IR produced):
```
ERROR: ast_to_ir: unbound variable '__unresolved_Eq__'        (eq_operator_adt_dispatch, deriving_eq_parametric)
ERROR: ast_to_ir: unbound variable '__unresolved_ToString__'  (deriving_to_string)
```
Direct codegen compiles + runs all three correctly (they are in `IR_XFAIL`, i.e.
direct-OK / typed-fail).

### Common cause: `deriving`
All three files use `deriving (Eq)` / `(ToString)` / `(Eq, ToString)`. The polymorphic
`equal_pair(x, y) where Eq a = x == y` in `test_eq_operator_adt_dispatch` is a **red
herring** — `test_deriving_eq_parametric` and `test_deriving_to_string` have no
constraint-polymorphic helper and fail identically. The trigger is the
**`deriving`-synthesized instance dispatch**, not the `@fwd` path.

### Where the placeholder comes from (proven by elimination)
- The string `__unresolved_<Class>` is emitted **only** by `lowering.sprout`
  (`resolve_tdict_with_key` line 1255, `resolve_method_*` lines 1302/1320) when neither
  `ctx_fwd` nor `ctx_inst` has the dispatch key.
- `ast_to_ir.sprout` does **not** call lowering's resolvers (grep: only `ir_lowering.`
  references, no `lowering.` calls; it only pattern-matches `TDict` nodes). So it cannot
  generate the placeholder — it must arrive **in its input**.
- ast_to_ir's input = `compile_phase_recheck` = `compile_phase_lower` = check +
  `lowering.lower_program` + `dce.elim_program`. So `lowering.lower_program` emitted
  `__unresolved_<Class>` into the lowered AST.

### Isolation experiment: parametric instances only
Minimal `type Color (..) deriving (ToString) = Red | Green` (non-parametric) +
`to_string(Red)` **compiles cleanly on the typed path** (7128 lines, 0 `__unresolved`).
The failing files all have **parametric** derived types (`Box a`, `Pair a b`,
`Player` with derived Eq dispatched polymorphically). So the typed-path failure is
specifically the **inner constraint dict** of a parametric `deriving` instance — e.g.
`instance ToString (Box a) where ToString a` needs an inner `ToString a` witness.

### Direct codegen does NOT mask — it resolves fully (corrects an earlier hypothesis)
Direct IR for `test_deriving_to_string` contains the **resolved** derived instances with
inner dicts threaded as parameters:
```
define i64 @__tc_ToString_Color_to_string(i64 %value)                                  # non-parametric
define i64 @__tc_ToString_Box_a_to_string(i64 %value, i64 %__tc_ToString_0_to_string)  # inner dict param
define i64 @__tc_ToString_Pair_a_b_to_string(i64 %value, i64 %d0, i64 %d1)             # two inner dicts
```
So direct codegen uses **real dictionary-passing**, not type-directed masking. lowering
resolved the parametric inner dicts correctly *for the direct path*.

### The genuine open question (do NOT present as settled)
Both paths nominally call the same `lowering.lower_program`, yet direct resolves the
parametric inner dict and the typed path emits `__unresolved_<Class>__`. The two paths
differ only in: direct = `compile_phase_recheck_timed` (lower, **no DCE**) → `codegen.sprout`;
typed = `compile_phase_recheck` (lower **+ `dce.elim_program`**) → `ast_to_ir`/`ir_pipeline`.
The earlier "both lowered ASTs are identical, direct just masks it" reasoning is
**contradicted** by the IR above — so one of these is true and is NOT yet pinned down:
  - (i) DCE perturbs the parametric inner-dict witness on the typed path, or
  - (ii) the two `bundle+check` invocations produce subtly different `typed_prog`
    (Ref-based inference state — `project_pure_unifier_decision`), or
  - (iii) ir_pipeline/ast_to_ir mishandles the parametric inner-dict witness that
    lowering DID resolve.

**Required next step before recommending a fix:** dump the *lowered expression body* of a
`to_string(Box …)` call on both paths and diff them. Confirmed there is **no existing
phase** that prints bodies: `--phase lower` and `--phase recheck` print only decl type
signatures (0 unresolved in both); `--phase ir` is the full *direct* LLVM IR (= `--emit-ir`,
0 unresolved). So a temporary debug dump in `compile_phase_lower`/`ir_pipeline` is needed.
Until that diff exists, the fix location (DCE vs check vs ir_pipeline) is unproven — do
not commit to A-vs-B.

### Fix direction (feature-sized; overlaps deriving-v1 — design + approval needed)
Resolve parametric `deriving` inner-dict witnesses on the typed path so it matches
direct codegen's dict-passing output (the IR above is the target shape). Whether that fix
lands in DCE, the check, or ir_pipeline depends on the body-diff above. Overlaps
`project_deriving_v1_design` and `project_return_type_typeclass_dispatch`. This is gated
behind the PR-landing precondition AND likely behind deriving-v1 — out of immediate
flip-critical scope, but it IS the last TYPED-COMPILE class blocking parity-zero.

### DoD for the fix (when unblocked + approved)
- Typecheck success + failure tests (AGENTS.md §Code and Testing #5); the three
  `IR_XFAIL` files become the success fixtures.
- Compiler-source change order: `rm build/…stage1` → memwatch `refresh-seed` → `just test`.
- Remove the 3 entries from `tests/IR_XFAIL`; confirm all three run to completion typed.
- Full DoD (fmt, test, compile-examples, smoke/bundle, fixed-point, canary).
