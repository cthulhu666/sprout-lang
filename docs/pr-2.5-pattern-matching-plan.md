# PR 2.5 — Pattern matching in the IR codegen

**Branch:** `m2-pr-2.5-pattern-matching`
**Milestone:** GC-rooting Model C — Milestone 2, PR 2.5 (see `gc-rooting-model-c-plan-2026-06-02.md`).
**Status:** User approved scope (Bool included; residual follow-ups recorded in backlog). Ready for implementation.

## Problem statement

`ast_to_ir.translate_expr` currently rejects `typed_ast.TMatch` with `Err("ast_to_ir: match not yet supported")` at `stdlib/compiler/ast_to_ir.sprout:674`. Until `match` lowers through the IR path, programs that use pattern matching fall back to the direct codegen and `--use-ir-codegen` cannot compile any non-trivial Sprout program.

## Goals

1. Extend Sprout-IR with the minimal ops needed to encode a `match` expression as a chain of conditional branches with a phi at the join.
2. Add an AST → IR `translate_match` modelled on the existing `translate_if` (`ast_to_ir.sprout:1100`).
3. Cover the patterns that are actionable in the current IR path (see Scope below).
4. Keep rooting correctness intact: every new op is classified in `ir_rooting` (trigger / successors / operands / heap-definers), and any heap-typed field result is reachable for liveness analysis.

## Non-goals (explicitly deferred)

- **String, Char, Unit, Tuple patterns.** The IR path does not yet handle `TString` in arbitrary positions, `TChar`, `TUnit`, or `TTuple` as expressions (`ast_to_ir.sprout:671–684`). Their patterns are not actionable until the matching expression forms land. Each ships in its own follow-up alongside the expression form.
- **Unboxed-CPR match optimisation** (`emit_match_unboxed_adt` / `emit_match_unboxed_call` in old codegen). `translate_program` already rejects CPR-eligible ctors at `IRMakeCtor`, so CPR scrutinees cannot reach the IR path. CPR-aware match is a post-M2 optimisation.
- **TCO integration.** The IR path has no TCO at all (`grep tco stdlib/compiler/ast_to_ir.sprout` → nothing). Match-in-tail-position lowers like any other expression. TCO is a separate cross-cutting concern.
- **Linear-value-in-arms checking.** Per the milestone doc, this is deferred to Milestone 4.

## Scope (in this PR)

Patterns supported in PR 2.5 — each one's scrutinee form either already lowers in the IR path or is added in this PR (marked **new**):

| Pattern                | Source-level             | Scrutinee form    | Verified at                                  |
|------------------------|--------------------------|-------------------|----------------------------------------------|
| `WildcardPattern`      | `_`                      | any               | always-match, no scrutinee dependency        |
| `VarPattern`           | `x`                      | any               | always-match, no scrutinee dependency        |
| `IntPattern`           | `42`                     | `TInt`            | `ast_to_ir.sprout:627` (translates)          |
| `BoolPattern`          | `true` / `false`         | `TBool` **new**   | added in this PR (see "TBool translation")   |
| `ConstructorPattern`   | `Cons h t`, …            | `TCall` of a ctor | `ast_to_ir.sprout:647` (translates, boxed)   |

**Patterns deferred to other PRs (gated by their scrutinee form):**

- `StringPattern`, `CharPattern` → ship with their literal forms (`TString` partially supported, `TChar` rejected).
- `TuplePattern` → ships with `TTuple` (currently rejected).
- `UnitPattern` → ships with `TUnit` (currently rejected).

### TBool translation (bundled with `BoolPattern`)

To make `BoolPattern` actionable, this PR also adds the trivially-scoped piece of `TBool` lowering:

- New op `IRConstBool String Bool` in `sprout_ir.sprout`, modelled after `IRConst String Int` (line 59).
- Lowering in `ir_lowering.sprout`: `r = add i1 0, <0|1>` — mirrors how `IRConst` is lowered at line 110 (`r = add i64 0, N`).
- `translate_expr` (`ast_to_ir.sprout:665`): replace the `TBool` reject with an `IRConstBool` emission, parallel to the `TInt` case at line 627.
- `ir_rooting`: `IRConstBool` is non-trigger, has no operands, no successors, defines a scalar result. (Add to the four-place checklist below.)

This is genuinely the minimum: no new runtime symbol, no new builtin, no `APPROVED_BUILTINS` change — just one IR op and one source-position swap in `translate_expr`. Bundling it keeps the boolean story atomic, since `BoolPattern` without `TBool` cannot be tested with a translator-driven test.

**M2 acceptance gap (tracked in `docs/backlog.md` in this PR).** The original milestone acceptance — "stage-1 self-compile under `--use-ir-codegen` with `SPROUT_GC_THRESHOLD=1`" — will *not* be met by PR 2.5 alone. After this PR lands, the residual expression forms still rejected by `ast_to_ir.translate_expr` are: `TChar`, `TUnit`, `TTuple`, `TDict`, `TUnary`, `TRange`, `TDo`, `TRecord`, `TGetField`. This PR adds a "PR 2.6+ residual expression forms" sub-bullet under the GC-rooting line in `docs/backlog.md` enumerating them, so M2 acceptance is reachable via the existing roadmap rather than an unwritten one.

## IR additions (`stdlib/compiler/sprout_ir.sprout`)

Five new ops; each chosen so that **no new op is a GC trigger**. Three are for `match`; two (`IRConstBool`, `IRICmpBool`) unblock `TBool` and `BoolPattern`.

```
| IRGetTag String String
    # IRGetTag <result> <scrut-handle>
    # Lowers to: %result = call i64 @sprout_tag(i64 %scrut-handle)
    # @sprout_tag reads ctor metadata; no allocation. NOT a trigger.

| IRGetField String String Int String
    # IRGetField <result> <scrut-handle> <field-idx> <field-kind>
    # field-kind: "heap" | "scalar" (mirrors IRLoadEnvSlot's kind arg).
    # Encoding source: extend the ctors-dict value tuple from
    #   Dict String (Int, Int, Int)        # (tag, arity, max_arity)
    # to
    #   Dict String (Int, Int, Int, String)  # (tag, arity, max_arity, field_kinds_string)
    # `field_kinds_string` is already computed by build_ctor_table at
    # ast_to_ir.sprout:493 (passed to CtorReg); we'd be threading the same
    # value into the dict. translate_match decodes per-byte: 'p' → "heap";
    # 'i'|'b'|'s'|'_' → "scalar" (the '_' type-var case is the only soft
    # spot — conservatively treat it as "heap" to avoid missed roots).
    # Lowers to: %result = call i64 @sprout_field(i64 %scrut-handle, i64 <idx>)
    # @sprout_field is a pure read; no allocation. NOT a trigger.
    # field-kind drives ir_rooting heap-vs-scalar classification of <result>.

| IRAbortMatch String
    # IRAbortMatch <discard-reg>
    # Lowers to:
    #   %discard-reg = call i64 @sprout_abort_match()
    #   unreachable
    # Single op so that "this block aborts" is a syntactically visible terminator;
    # equivalent to IRCall + IRUnreachable but avoids growing the IR with a
    # standalone unreachable op that has no other use today.

| IRConstBool String Bool
    # IRConstBool <result> <value>
    # Lowers to: %result = add i1 0, <0|1>
    # Models IRConst (line 59) which lowers to "add i64 0, N" at ir_lowering line 110.
    # NOT a trigger; defines a scalar (i1) value.

| IRICmpBool String String String String
    # IRICmpBool <result> <pred> <lhs> <rhs>
    # i1 sibling of IRICmp (which hard-codes i64 at ir_lowering line 115).
    # Lowers to: %result = icmp <pred> i1 <lhs>, <rhs>
    # Used by BoolPattern's pattern-test phase. NOT a trigger.
    # Decision rationale: a dedicated op vs. retrofitting a `ty` parameter
    # onto IRICmp. The dedicated op is smaller scope (no call-site churn
    # on existing i64 IRICmp uses) and parallels IRConstBool symmetrically.
    # If a third i1 arithmetic op is ever needed, revisit the type-parameter
    # refactor.
```

### Printer

Add five cases to `print_ir_program`'s op dispatch matching existing style (one-line textual rendering for diagnostic tests).

### Why not reuse `IRCall`?

`ir_rooting.op_triggers_gc` (`stdlib/compiler/ir_rooting.sprout:38`) classifies *every* `IRCall` as a GC trigger, conservatively. `sprout_tag`/`sprout_field` never allocate, and treating them as triggers would force spurious roots around every pattern test/bind — defeating the point of having an IR-level dataflow rooter. Distinct ops cleanly express "looks like a call, isn't a trigger".

## Lowering (`stdlib/compiler/ir_lowering.sprout`)

Add five op-dispatch cases inside `lower_op` and the three new runtime decls:

```
"declare i64 @sprout_tag(i64)\n"
"declare i64 @sprout_field(i64, i64)\n"
"declare i64 @sprout_abort_match()\n"
```

Templates (verbatim shape):
- `IRGetTag r s`            → `  r = call i64 @sprout_tag(i64 s)`
- `IRGetField r s i _kind`  → `  r = call i64 @sprout_field(i64 s, i64 i)`
- `IRAbortMatch d`          → `  d = call i64 @sprout_abort_match()\n  unreachable`
- `IRConstBool r b`         → `  r = add i1 0, <0|1>`
- `IRICmpBool r pred a b`   → `  r = icmp pred i1 a, b`

All three runtime symbols already exist in `runtime/sprout_runtime.c` (`sprout_tag` at line 3210, `sprout_field` at 3222, `sprout_abort_match` at 3282) — no runtime change needed, no `APPROVED_BUILTINS` update required.

## Rooting (`stdlib/compiler/ir_rooting.sprout`)

**Checklist** (PR 2.4 review rounds 2/3/4 showed it is easy to miss one — make this a literal grep-checklist):

- `op_triggers_gc`           → all five (IRGetTag, IRGetField, IRAbortMatch, IRConstBool, IRICmpBool) return `false`.
- `successors_of`            → all five return `Nil` (no branch targets).
- `operands_of`              → IRGetTag returns `[scrut]`; IRGetField returns `[scrut]`; IRICmpBool returns `[lhs, rhs]`; IRAbortMatch and IRConstBool return `Nil`. *The `[scrut]` cases are load-bearing for keeping the scrutinee SSA-live across nested allocs inside branch bodies.*
- `defining_op_heap` (or equivalent heap-result classifier) → IRGetField with `field-kind = "heap"` declares its result heap; with `"scalar"` declares it scalar. IRGetTag, IRConstBool, IRICmpBool always define scalar results. IRAbortMatch has no result.

## AST → IR (`stdlib/compiler/ast_to_ir.sprout`)

Replace the line-674 stub with `translate_match`. Shape mirrors `translate_if` / `translate_if_after_cond` / `finish_if`:

1. Translate the scrutinee → `s_name` (heap or scalar SSA), continuing in `cur_label` with accumulated ops.
2. If the scrutinee type is an ADT, emit `IRGetTag tag_name s_name` *once* up front (constant across all branches) and reuse `tag_name` in every constructor test.
3. Allocate `join_lbl` and `phi_name` from the fresh-name pool, plus per-branch `(test_lbl_i, body_lbl_i, miss_lbl_i)` triples.
4. For each branch in order:
   - **Test phase** (skipped if pattern is `WildcardPattern` or `VarPattern` — always-match): emit a comparison against the scrutinee. For `ConstructorPattern`: `IRICmp r "eq" tag_name <ctor-tag>` (against the hoisted `IRGetTag`). For `IntPattern n`: `IRConst tmp n` then `IRICmp r "eq" scrut tmp` (i64). For `BoolPattern b`: `IRConstBool tmp b` then `IRICmpBool r "eq" scrut tmp` (i1). Then `IRCondBr r body_lbl_i miss_lbl_i`. Multi-field constructor patterns chain inner field tests using nested `IRCondBr` to the same `miss_lbl_i` (mirrors `emit_ctor_field_tests` in old codegen).
   - **Bind phase**: for `VarPattern` bind the scrutinee directly; for `ConstructorPattern` emit `IRGetField` per arg (recursively for nested patterns) and register each binding in the captures/params dict the way `translate_lambda` already does.
   - **Body phase**: recurse via `translate_expr` with the extended bindings, capturing the final `(cur_lbl_i, body_value_i)` for the phi.
   - Seal the body block with `IRBr join_lbl`.
5. The final `miss_lbl_N` (after the last branch's test fails) gets `IRAbortMatch d_fresh` — the unreachable terminator that documents inexhaustive match at runtime.
6. The join block opens with `IRPhi phi_name phi_ty [(body_value_i, cur_lbl_i)…]` over the surviving branches.

### Edge case: all branches terminate

If every branch ends in a tail position that does not reach the join (e.g. every body itself returns or aborts), the phi has zero incoming edges. The old codegen handles this at `codegen.sprout:2216` by returning `undef` of the result type. The IR equivalent: emit no `IRPhi` in the join block — the join is unreachable from any predecessor, the LLVM verifier accepts it, and DCE removes it. `translate_match` returns `phi_name = "undef"` (literal string) in that case, matching the old codegen's no-edges path.

The phi type is `llvm_ret_type(match_ty)`, exactly as `translate_if` derives its phi type from `if_ty`.

### What "tag-once" buys

Hoisting `IRGetTag` out of every branch test is both a clarity win (the rooter sees the scrutinee operand exactly where it's first read) and a small codegen win. The old codegen does the same hoist implicitly via `emit_pattern_test` recursing on a captured `tag_t`.

## Tests

New file `tests/stdlib/test_ir_codegen_match.spr`, modelled on `test_ir_codegen_ctors.spr`'s shape (translator-driven, then lower, then assert on emitted LLVM substrings).

### Test ADT selection (CPR-eligibility constraint)

`ctor_is_cpr_eligible` at `ast_to_ir.sprout:498` returns `true` iff `max_arity ∈ {1, 2}`. **Any test ADT shaped like `MyJust x | MyNothing` (max_arity=1) is rejected at `IRMakeCtor` before pattern-matching code is exercised.** Safe test-ADT shapes:

- All-zero-arity (e.g. `MyShape = MyRed | MyBlue`), or
- Max arity ≥ 3 (e.g. `MyTrip = MyTrip Int Int Int | MyEmpty`, `MyBig a b c = MyBig3 a b c | MyBig0`).

Tests use these shapes; comments cite line 498 so future readers don't repeat the mistake.

### Test cases

1. **Int match** — `match n with | 0 -> 1 | _ -> 2`. Asserts: two blocks, one `icmp eq i64`, one phi at join, no `@sprout_tag` call.
2. **Bool match** — `match b with | true -> 1 | false -> 0`. Asserts one `icmp eq i1`, phi at join, no `@sprout_tag`. Also exercises the new `IRConstBool` lowering via a scrutinee that comes from a literal `true`/`false` expression.
3. **Constructor match, no binding** — `match c with | MyRed -> 0 | MyBlue -> 1` over `MyShape = MyRed | MyBlue`. Asserts a single `@sprout_tag` call and a tag-keyed condbr.
4. **Constructor match with binding** — `match t with | MyTrip a b c -> a + b + c | MyEmpty -> 0` over `MyTrip = MyTrip Int Int Int | MyEmpty`. Asserts `@sprout_tag`, three `@sprout_field` calls, phi.
5. **Wildcard fall-through** — `match n with | 0 -> 1 | x -> x`. Asserts no test emitted for the var-pattern branch.
6. **Multi-field test chaining** — constructor pattern with non-trivial inner patterns (e.g. `MyTrip 0 b c`); asserts the chained condbr / shared `miss_lbl` shape and the final 1/0 `icmp eq` phi.
7. **Match-inside-let / let-inside-match** — confirms `translate_match` composes with the let path.
8. **All-branches-terminate** — every branch body is a tail call; assert the join block has no phi and the program compiles cleanly.
9. **Rooting interaction (stress)** — match binding flows into a later `IRMakeCtor`; assert the rooter inserts an `IRRoot` covering the binding name, and the program runs cleanly under `SPROUT_GC_THRESHOLD=1`.

For each test, run the lowered LLVM through `clang` end-to-end and check the program output as well (the existing IR codegen test files already do this via the `translate_source` helper).

## Files touched

- `stdlib/compiler/sprout_ir.sprout` — 5 ctors + printer cases.
- `stdlib/compiler/ir_lowering.sprout` — 5 op cases + 3 runtime decls.
- `stdlib/compiler/ir_rooting.sprout` — 4 classifier extensions (one per symmetric set, covering all 5 new ops).
- `stdlib/compiler/ast_to_ir.sprout` — `translate_match` + helpers; remove the line-665 `TBool` stub (emit `IRConstBool`) and the line-674 `TMatch` stub; **widen the ctors dict value tuple to carry `field_kinds_string`** (touches `build_ctor_table`, `build_top_level_set`, and every call site that destructures the tuple — small but cross-cutting); existing `compute_free_vars_match_arms` / `find_capture_type_match_arms` already walk `TMatch` and need no change.
- `tests/stdlib/test_ir_codegen_match.spr` — new file.
- `docs/gc-rooting-model-c-plan-2026-06-02.md` — mark PR 2.5 as landed; note that M2 acceptance is also gated on PR 2.6+ (residual expression forms).
- `docs/backlog.md` — add a sub-bullet under the GC-rooting line listing the residual M2 expression-form follow-ups: `TChar`, `TUnit`, `TTuple`, `TDo`, `TRecord`, `TGetField`, `TDict`, `TUnary`, `TRange`. (`TBool` is NOT in this list — landed in this PR.)

## Definition of Done (per AGENTS.md)

This is a `stdlib/compiler/` change, so:

- [ ] `mise exec -- just fmt` clean.
- [ ] `mise exec -- just test` passes end-to-end (item 5).
- [ ] `mise exec -- just compile-examples-stage1` matches the pre-existing known-broken set (item 6).
- [ ] Smoke shapes: every `tests/smoke_shapes/*.spr` emits IR cleanly via `compile_driver_bin_stage1 --emit-ir`, contains ≥1 `define` block, no `str_concat(ptr null,…)` (item 7).
- [ ] Bundle smoke: `--phase bundle` on `token.sprout`, `ast.sprout`, `prelude.sprout` is non-empty and has no dot-prefix lines (item 8).
- [ ] `just refresh-seed` and stage `bootstrap/compile_driver.ll`; `just verify-bootstrap-fixed-point` (item 9).
- [ ] Example canary: `tuples.sprout`, `factorial.sprout`, `maybe_map.sprout`, `typeclass_collections_demo.sprout`, `fizzbuzz.sprout` all compile *and* run to completion (item 11).
- [ ] Self-review pass; commit.

## Risks

- **Rooting-classifier omission** (highest, by PR 2.4 history). Mitigated by the explicit four-place checklist above and by stress-test case #9.
- **Phi-block / current-label tracking after multi-branch chains.** Each branch may itself contain nested matches/ifs; the phi's incoming labels must be the *final* `cur_label` after sub-translation, not the entry `body_lbl_i`. Mirrors the existing pattern in `finish_if`.
- **Multi-field constructor tests producing dead labels** when an early field test fails — the old codegen's `emit_ctor_args_test` solves this with a shared `miss_lbl` + a final `icmp eq` phi over `[1, last_ok]/[0, miss]`. Plan to translate that pattern unchanged into IR form; flagged as the trickiest single piece of `translate_match`.

## Open questions for the user

*All resolved.* Scope approved as **Wildcard, Var, Int, Bool, Constructor** patterns (with `TBool` translation bundled). Residual M2 expression-form follow-ups (`TChar`, `TUnit`, `TTuple`, `TDo`, `TRecord`, `TGetField`, `TDict`, `TUnary`, `TRange`) will be added to `docs/backlog.md` in this PR.
