# Effect System: Algebraic Effect Handlers Draft

Status note:

- This document defines the recommended next major effect milestone after the v1
  ergonomics pass described in `effect-system-v1-draft.md`.
- It is not part of normative v0 or v1.
- The immediate motivation is eliminating explicit `TestState` threading in stdlib tests.
  The general motivation is establishing the handler infrastructure that richer effect
  patterns (async, generators, capability injection) will build on.

---

## 1. Problem Statement

The current stdlib test library requires explicit state threading through every assertion:

```sprout
fn main() -> Unit !{IO} =
  do
    state <- new_state()
    assert_eq(state, "abs(-3)", abs(-3), 3)
    assert_true(state, "is_even 4", is_even(4))
    summary(state)
```

The `state` argument is pure noise — it carries no semantic meaning at the call site.
The underlying problem is that Sprout has no way to implicitly pass contextual state
through a call tree. The current `!{IO}` effect system tracks purity boundaries but
provides no mechanism for intercepting or reinterpreting effectful operations.

Algebraic effect handlers solve this directly. With handlers the same code becomes:

```sprout
fn my_tests() -> Unit !{Test} =
  do
    assert_eq("abs(-3)", abs(-3), 3)
    assert_true("is_even 4", is_even(4))

fn main() -> Unit !{IO} =
  run_tests(my_tests)
```

The `Test` effect carries the assertion contract. `run_tests` is the handler that
interprets assertions as IO operations and accumulates pass/fail counts.

---

## 2. Goals

1. Add `effect` declarations to define named effect labels and their operations.
2. Add `handle`/`with` expressions to install handlers that interpret effect operations.
3. Keep performing effect operations implicit: calling an effect operation by name is a
   perform — no `perform` keyword.
4. Support multi-label effect rows (`!{IO, Test}`) so handlers can mix effects.
5. Preserve strict left-to-right evaluation and the existing `!{IO}` model exactly.
6. Native compiler only — the Python reference compiler is not touched.
7. Keep the existing `TestState` API for backwards compatibility during migration.
8. Respect the six observability guard rails from `observability-guard-rails.md`.

---

## 3. Non-Goals

1. Do not add multi-shot handlers (resuming the continuation more than once).
2. Do not add open effect row polymorphism in phase 1 (`!{e, Test}` style).
3. Do not add constrained effect operations (type class constraints on op signatures).
4. Do not change the strict evaluation model or add laziness.
5. Do not implement algebraic effects in the Python reference compiler.
6. Do not force migration of existing test files or other code using the old API.

---

## 4. Syntax

### 4.1 Effect declarations

```sprout
effect Test {
  assert_pass : String -> Unit
  assert_fail : String -> Unit
}
```

Grammar:
```
EffectDecl ::= "effect" Name "{" EffectOp* "}"
EffectOp   ::= Name ":" TypeExpr
```

Effect declarations are top-level. Operations with polymorphic argument types are NOT
placed directly in the effect block; they are written as regular polymorphic functions
that call the primitive operations. For example:

```sprout
export fn assert_eq(label: String, actual: a, expected: a) -> Unit !{Test}
  where Eq a, ToString a =
  if eq(actual, expected)
  then assert_pass(label)
  else assert_fail(label ++ " (expected " ++ to_string(expected) ++ ", got " ++ to_string(actual) ++ ")")
```

This sidesteps the need for constrained effect operations in phase 1 while producing
the same call-site ergonomics.

### 4.2 Handle expressions

```sprout
handle suite() with
| assert_pass(label, k) ->
    do
      p <- ref_read(pass_ref)
      ref_write(pass_ref, p + 1)
      print("PASS: " ++ label)
      k()
| assert_fail(label, k) ->
    do
      f <- ref_read(fail_ref)
      ref_write(fail_ref, f + 1)
      print("FAIL: " ++ label)
      k()
```

Grammar:
```
HandleExpr   ::= "handle" Expr "with" HandleBranch+
HandleBranch ::= "|" Name "(" VarPattern* "," VarPattern ")" "->" Expr
```

The last variable in each branch pattern is always the continuation `k`. In phase 1 the
continuation type is hardcoded to `Unit -> Unit` (sufficient for the `Test` use case
where operations always resume once and return `Unit`).

### 4.3 Perform (implicit)

Calling an effect operation by name inside a function typed `!{Test}` is a perform. No
keyword is required. The operation name resolves through the effect environment to the
handler record for the enclosing `handle` expression.

`perform` is reserved as a keyword for future explicit use.

### 4.4 Multi-label effect rows

```sprout
fn my_tests() -> Unit !{Test} = ...
fn run_tests(suite: Unit -> Unit !{Test}) -> Unit !{IO} = ...
```

The native parser's `collect_effect_names` already accepts comma-separated labels
(`!{IO, Test}` parses today). The only change needed is in the type system's unification
and merge rules, which currently reject `EffectRow × EffectRow` pairs.

---

## 5. Type System Changes

### 5.1 `EffectRow` unification

`types.Effect` already has `EffectRow (List String)` as a constructor marked "future use".
The unifier's `unify_effects_applied` currently rejects any `EffectRow` pair with a
mismatch error. Two changes are needed:

1. `unify_effects_applied` in `unifier.sprout`: add cases for `EffectRow × EffectRow`,
   `EffectRow × EffectPure`, `EffectRow × EffectIO`, and their symmetric forms.

2. `merge_effects` in `infer.sprout`: add cases for `EffectRow × EffectRow` (label-set
   union), `EffectRow × EffectIO`, and `EffectRow × EffectVar`.

### 5.2 Effect environment

The type checker maintains an effect environment mapping effect labels to their operation
signatures. At an `EffectDecl`, each operation name is registered in the term environment
with its type and a `EffectRow([label])` effect annotation. This makes performs typecheck
identically to regular function calls — no special-case in the call-inference path.

### 5.3 Handle expression typing

The type of a `handle E with | ...` expression is:
- The result type of the handler branch bodies (typically `Unit`).
- The outgoing effect is the scrutinee's effect row minus the handled labels, plus the
  branch bodies' combined effects.

For `handle suite() with | assert_pass(...) -> ... | assert_fail(...) -> ...` where
`suite : Unit -> Unit !{Test}` and the branch bodies are `!{IO}`:
- Scrutinee effect: `!{Test}`
- Handled labels: `{Test}` (both ops are handled)
- Branch body effect: `!{IO}`
- Result effect: `!{IO}` ✓

### 5.4 Open rows deferred

`EffectOpen (List String) String` (an open row with a remainder variable) is added to
`types.Effect` as a stub for future effect polymorphism but is not yet used by the
unifier in phase 1.

---

## 6. Codegen Strategy: One-Shot Handler Records

Full algebraic effect handlers require heap-allocated continuations and a mechanism for
non-local transfer of control (setjmp/longjmp or fibers). Phase 1 avoids both by
restricting to **one-shot linear handlers** — handlers where each operation resumes
exactly once in LIFO stack order.

Under this restriction, continuations are ordinary tail calls: calling `k()` in a branch
body is equivalent to returning from the handler branch, which continues sequential
execution at the call site of the handled operation. No heap allocation, no non-local
jump.

**Implementation: handler records as implicit parameters.**

An effect declaration for `Test` with N operations produces a handler record type:
a fixed-size struct of N closure pointers (one per operation, in declaration order).

Functions with `!{Test}` in their effect row receive an implicit `i64` parameter
`__handler_Test` carrying the handler record pointer. At a perform site, the appropriate
closure pointer is extracted from the record and called with the operation arguments
plus a continuation closure. The continuation is the identity `\() -> ()` — it simply
returns, continuing normal sequential execution.

At a `handle` expression, the handler record is stack-allocated. Each branch body is
compiled as a closure stored into the corresponding slot. The record pointer (cast to
`i64`) is passed as the implicit parameter to the scrutinee function call.

This strategy reuses the existing closure machinery from the native codegen and requires
no new runtime primitives.

---

## 7. New `stdlib/test.sprout` Shape

The old `TestState`/`new_state`/`summary` API is kept. The new API is additive:

```sprout
effect Test {
  assert_pass : String -> Unit
  assert_fail : String -> Unit
}

export fn assert_eq(label: String, actual: a, expected: a) -> Unit !{Test}
  where Eq a, ToString a =
  if eq(actual, expected)
  then assert_pass(label)
  else assert_fail(label ++ " (expected " ++ to_string(expected) ++ ", got " ++ to_string(actual) ++ ")")

export fn assert_true(label: String, cond: Bool) -> Unit !{Test} =
  if cond then assert_pass(label) else assert_fail(label)

export fn assert_false(label: String, cond: Bool) -> Unit !{Test} =
  if cond == false then assert_pass(label) else assert_fail(label)

export fn run_tests(suite: Unit -> Unit !{Test}) -> Unit !{IO} =
  do
    pass_ref <- ref_new(0)
    fail_ref <- ref_new(0)
    handle suite() with
    | assert_pass(label, k) ->
        do
          p <- ref_read(pass_ref)
          ref_write(pass_ref, p + 1)
          print("PASS: " ++ label)
          k()
    | assert_fail(label, k) ->
        do
          f <- ref_read(fail_ref)
          ref_write(fail_ref, f + 1)
          print("FAIL: " ++ label)
          k()
    p <- ref_read(pass_ref)
    f <- ref_read(fail_ref)
    print(int_to_string(p) ++ " passed, " ++ int_to_string(f) ++ " failed")
    if f > 0 then print("SUITE FAILED")
    else print("SUITE PASSED")
```

Output format is unchanged (`PASS:`, `FAIL:`, `N passed, M failed`, `SUITE PASSED/FAILED`),
preserving the justfile `^SUITE FAILED` grep.

---

## 8. Files to Change (Native Compiler)

All changes are in `stdlib/compiler/*.sprout`. The Python compiler (`sprout/`) is not touched.
The compiler's own source does not use the new syntax — new AST/typed-AST nodes are plain
Sprout ADTs, fully expressible in stage-0 syntax.

| File | Change |
|---|---|
| `lexer.sprout` | Add `"effect"` and `"handle"` to `is_keyword` |
| `ast.sprout` | Add `EffectDecl`, `EffectOpSig`, `HandleExpr`, `HandleBranch` to `Decl` / `Expr` |
| `parser.sprout` | Add `parse_effect_decl`, `parse_handle_expr`, dispatcher branches |
| `types.sprout` | Add `EffectOpen` stub; update `ftv_effect`, `effect_to_string`, `effect_eq` |
| `unifier.sprout` | Extend `unify_effects_applied` for `EffectRow` cases |
| `infer.sprout` | Extend `merge_effects`; add effect env; handle `EffectDecl` + `HandleExpr` |
| `typed_ast.sprout` | Add `THandle`, `TypedHandleBranch`; update `typed_expr_type`/`_pos` |
| `lowering.sprout` | Add implicit handler-record params; rewrite perform sites; lower `THandle` |
| `codegen.sprout` | Emit handler records, perform-site calls, `THandle` |
| `stdlib/test.sprout` | Add `effect Test` + `run_tests` alongside old API |

---

## 9. Phasing

### Phase 1 — One-Shot Linear Handlers (immediate need)
Full implementation of §4–8 above. Enables `run_tests` and the `Test` effect.
New test file `tests/stdlib/test_effects.spr` verifies end-to-end.

### Phase 2 — Effect Polymorphism
Activate `EffectOpen` in the unifier. Functions can be polymorphic over effect remainders,
enabling handlers that work with any effect superset.

### Phase 3 — Multi-Effect Rows in Signatures
Full `!{IO, Test}` support for functions that perform multiple effects simultaneously.
Partial handlers (handle some labels, leave others unhandled).

### Phase 4 — Spec and Docs Update
Extend `docs/spec-v0.md` with algebraic effect handler semantics as normative.
Update `effect-system-v1-draft.md` status note to reflect this superseding draft.

---

## 10. Observability Guard Rail Compliance

Per the constraints in `observability-guard-rails.md`:

1. **Source locations first-class:** All new AST nodes (`EffectDecl`, `HandleExpr`,
   `HandleBranch`) carry `source.SourcePos`. Inference errors from `infer_handle`
   include position information.

2. **Explicit typed passes:** Effect registration (pre-scan), handle inference (infer),
   handler-record lowering (lower), and LLVM emission (codegen) are separate passes with
   no fusion.

3. **Explicit capability passing:** Handler records are explicit `i64` parameters.
   Functions that require an effect receive an explicit hidden parameter. Nothing is
   implicit in the IR.

4. **No premature pass fusion:** Effect declaration, check, lower, and codegen phases
   are separate. No handler-body inlining at check time.

5. **Type survival into typed core:** `THandle` in the typed AST carries the result
   `types.Type`. Effect labels are tracked as strings through the typed AST.

6. **Accurate effect annotations:** A function declared `!{Test}` gets the Test handler
   record parameter in the IR. A `handle` expression produces a reduced effect row
   (handled labels removed). This is computed in `infer_handle` and reflected in `THandle`.
