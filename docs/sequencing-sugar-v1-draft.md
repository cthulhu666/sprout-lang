# Sequencing Sugar v1 Draft

This document is a draft design for ergonomic sequencing sugar in Sprout v1.

It is not part of normative v0. Its purpose is to define a small language
feature that removes deeply nested `match` expressions when sequencing
`Maybe`, `Result`, and later effectful computations.

## 1. Problem Statement

Sprout v0 can express sequential failure-aware logic with `match`, but the
surface becomes noisy quickly.

Representative example:

```sprout
fn read_u32_be(value: Bytes) -> Maybe Int =
  match bytes_get(value, 0) with
  | Nothing -> Nothing
  | Just b0 ->
      match bytes_get(value, 1) with
      | Nothing -> Nothing
      | Just b1 ->
          match bytes_get(value, 2) with
          | Nothing -> Nothing
          | Just b2 ->
              match bytes_get(value, 3) with
              | Nothing -> Nothing
              | Just b3 -> Just(b0 * 16777216 + b1 * 65536 + b2 * 256 + b3)
```

The language already has `|>` and can grow stdlib helpers such as
`maybe_and_then`, but that only moves the nesting around. It does not solve the
core readability issue:

- intermediate names are hard to bind cleanly,
- the happy path is visually buried,
- the repeated failure branches obscure the intent,
- the same pattern recurs for `Maybe`, `Result`, and eventually `IO`.

## 2. Goals

1. Make sequential `Maybe` and `Result` code read top-to-bottom.
2. Keep the first milestone much smaller than a full effect-system redesign.
3. Preserve strict, explicit evaluation order.
4. Keep failure propagation obvious rather than magical.
5. Reuse the same mental model across `Maybe`, `Result`, and later `IO`.

## 3. Non-Goals

1. Do not add laziness.
2. Do not require higher-kinded types.
3. Do not commit v1 to full Haskell-style monad generality in the first pass.
4. Do not redesign pattern matching itself.
5. Do not introduce multiple overlapping sequencing syntaxes in the same milestone.

## 4. Proposed Direction

The recommended first step is a lightweight binding form, tentatively called
`let?`, rather than full general-purpose `do` notation.

Illustrative surface:

```sprout
fn read_u32_be(value: Bytes) -> Maybe Int =
  let? b0 = bytes_get(value, 0)
  let? b1 = bytes_get(value, 1)
  let? b2 = bytes_get(value, 2)
  let? b3 = bytes_get(value, 3)
  Just(b0 * 16777216 + b1 * 65536 + b2 * 256 + b3)
```

Interpretation:

- each `let?` unwraps a success case and binds a local name,
- on failure, the function returns early with the same failure container,
- the final expression becomes the success-path result.

Initial scope:

- allow `let?` for `Maybe a`,
- allow `let?` for `Result e a`,
- defer broader effectful sequencing until the effect system is more concrete.

## 5. Why Not Start With Full `do`

Full `do` notation is attractive, but it carries more design surface than this
problem needs immediately.

Questions `do` forces early:

1. Is `do` tied only to `IO`, or also to `Maybe` and `Result`?
2. Is it hard-coded for a few built-in container types, or generalized through
   typeclasses or another abstraction?
3. What syntax handles pure local bindings inside the block?
4. How does it interact with a future effect-system design?

By contrast, `let?` answers the immediate ergonomics problem directly without
forcing Sprout to settle all of those questions at once.

## 6. The Three Main Options

Sprout has three realistic directions for sequencing sugar.

### Option A: `let?`

This is the narrowest option and the current recommendation for the first
milestone.

Illustrative shape:

```sprout
fn read_u32_be(value: Bytes) -> Maybe Int =
  let? b0 = bytes_get(value, 0)
  let? b1 = bytes_get(value, 1)
  let? b2 = bytes_get(value, 2)
  let? b3 = bytes_get(value, 3)
  Just(b0 * 16777216 + b1 * 65536 + b2 * 256 + b3)
```

Characteristics:

1. Smallest parser and typechecker expansion.
2. Solves the immediate nested-`match` readability problem directly.
3. Keeps the control-flow model easy to explain because it desugars to `match`.
4. Works well for `Maybe` and `Result` without forcing a broader abstraction story.

Tradeoffs:

1. Narrower than a full sequencing model.
2. May later feel like an intermediate feature if Sprout eventually adopts
   block-based sequencing.
3. Does not by itself provide a unified visual form for `IO` and future effects.

### Option B: Type-Specific Blocks

This option introduces dedicated block forms such as `maybe { ... }` and
`result { ... }`.

Illustrative shape:

```sprout
maybe {
  b0 <- bytes_get(value, 0)
  b1 <- bytes_get(value, 1)
  b2 <- bytes_get(value, 2)
  b3 <- bytes_get(value, 3)
  Just(b0 * 16777216 + b1 * 65536 + b2 * 256 + b3)
}
```

Characteristics:

1. More readable than nested combinator pipelines.
2. Makes the container family explicit at the block boundary.
3. Gives Sprout a block-oriented surface without committing to a single
   universal `do` form immediately.

Tradeoffs:

1. Larger surface than `let?`.
2. Introduces a new block syntax plus `<-` binding syntax.
3. Risks fragmentation if Sprout later also adds full `do`.

### Option C: Full `do` Notation

This is the broadest and most ambitious option.

Illustrative shape:

```sprout
do
  b0 <- bytes_get(value, 0)
  b1 <- bytes_get(value, 1)
  b2 <- bytes_get(value, 2)
  b3 <- bytes_get(value, 3)
  Just(b0 * 16777216 + b1 * 65536 + b2 * 256 + b3)
```

Characteristics:

1. Best long-term unification story if Sprout wants one sequencing surface for
   `Maybe`, `Result`, and eventually `IO`.
2. Familiar to users coming from Haskell, Elm-adjacent literature, or other
   expression-oriented languages with computation syntax.
3. Gives the cleanest end-state if Sprout intends to make sequencing a major
   language idiom.

Tradeoffs:

1. Highest design cost.
2. Forces larger unanswered questions early, including how `do` selects its
   sequencing behavior and how it should relate to a future effect system.
3. Harder to ship as a small, isolated milestone.

### Summary

In terms of scope:

1. `let?` is the smallest and safest.
2. type-specific blocks are the middle option.
3. full `do` is the most powerful and the most expensive.

This document recommends `let?` first because it solves the real readability
problem while preserving room to grow toward block-based sequencing later.

The current `after(effect, value)` helper in `stdlib/prelude.sprout` is a
temporary IO sequencing convenience, not the final abstraction. Longer term,
Sprout likely wants something closer to Haskell's generic sequencing machinery
so the same idea can be shared across multiple effectful or container-like
contexts instead of living as one-off aliases.

## 7. Core Syntax

Draft syntax:

```sprout
let? name = expr
```

inside a function body, followed by a final expression:

```sprout
fn parse_port(raw: String) -> Result String Int =
  let? n = parse_nat(raw)
  if n > 65535 then Err("port out of range") else Ok(n)
```

The first milestone should keep the syntax narrow:

1. `let?` binds only a simple name, not a full pattern.
2. `expr` must have type `Maybe a` or `Result e a`.
3. `let?` may appear only in function bodies, not at top level.
4. Ordinary `let` remains separate and unchanged.

## 8. Desugaring Model

### `Maybe`

```sprout
let? x = expr
rest
```

desugars to:

```sprout
match expr with
| Nothing -> Nothing
| Just x -> rest
```

### `Result`

```sprout
let? x = expr
rest
```

desugars to:

```sprout
match expr with
| Err e -> Err(e)
| Ok x -> rest
```

This keeps evaluation strict and explicit. The sugar changes only the surface,
not the control-flow model.

## 9. Typing Model

At a high level:

1. `let?` introduces a local binding whose type is the success payload type.
2. All `let?` steps in one sequence must agree on the surrounding failure
   container family.
3. `Maybe` and `Result` sequences are distinct in the first milestone.
4. The final expression must produce the same outer container type.

Examples:

```sprout
fn sum_two(ma: Maybe Int, mb: Maybe Int) -> Maybe Int =
  let? a = ma
  let? b = mb
  Just(a + b)
```

```sprout
fn parse_pair(a: String, b: String) -> Result String Int =
  let? x = parse_nat(a)
  let? y = parse_nat(b)
  Ok(x + y)
```

Rejected example:

```sprout
fn bad(ma: Maybe Int, rb: Result String Int) -> Maybe Int =
  let? a = ma
  let? b = rb
  Just(a + b)
```

The sequence mixes `Maybe` and `Result`, so the compiler should reject it with
an explicit diagnostic.

## 10. Diagnostics

The first milestone should prioritize a few high-value diagnostics:

1. Using `let?` with a non-`Maybe`/non-`Result` expression.
2. Mixing `Maybe` and `Result` in the same sequence.
3. Ending a `let?` sequence with a final expression of the wrong outer type.
4. Using `let?` where only a pure local binding was intended.

Example style:

- what failed,
- which `let?` binding introduced the mismatch,
- what container type the surrounding sequence expects.

## 11. Relationship to Stdlib Helpers

This proposal does not replace stdlib helpers such as:

- `result_map`
- `result_and_then`
- potential future `maybe_map`
- potential future `maybe_and_then`

Those remain useful for point-free composition and API design.

`let?` solves a different problem: making multi-step happy-path code readable
when several intermediate names are needed.

## 12. Alternatives Considered

### A. Library Helpers Only

Example:

```sprout
bytes_get(value, 0)
|> maybe_and_then(\b0 ->
     bytes_get(value, 1)
     |> maybe_and_then(\b1 ->
          bytes_get(value, 2)
          |> maybe_and_then(\b2 ->
               bytes_get(value, 3)
               |> maybe_map(\b3 -> b0 * 16777216 + b1 * 65536 + b2 * 256 + b3)
             )
        )
   )
```

This works in principle, but it is still visually nested and depends on more
lambda-heavy literacy than Sprout should require for common code.

### B. Full `do` Notation

Example:

```sprout
do
  b0 <- bytes_get(value, 0)
  b1 <- bytes_get(value, 1)
  b2 <- bytes_get(value, 2)
  b3 <- bytes_get(value, 3)
  Just(b0 * 16777216 + b1 * 65536 + b2 * 256 + b3)
```

This is appealing long-term, but it is a larger design commitment. It is still
worth keeping open as a later generalization once effect sequencing and
container abstractions are more settled.

### C. Type-Specific Blocks

Example:

```sprout
maybe {
  b0 <- bytes_get(value, 0)
  b1 <- bytes_get(value, 1)
  b2 <- bytes_get(value, 2)
  b3 <- bytes_get(value, 3)
  Just(b0 * 16777216 + b1 * 65536 + b2 * 256 + b3)
}
```

This is more explicit than full `do`, but it still introduces a new block form
and a second binding syntax. It is a plausible follow-up if `let?` proves too
narrow.

## 13. Compatibility and Migration

This feature is additive.

Compatibility notes:

1. Existing v0 code remains valid.
2. Nested `match` code can migrate gradually.
3. Stdlib combinator-based code remains valid and may remain preferable in some
   APIs.

## 14. Milestone Plan

Proposed first milestone:

1. Finalize whether the first sequencing sugar is `let?` or a block-based form.
2. Add parser support for `let?` within function bodies.
3. Extend the AST with explicit sequencing-bind nodes.
4. Teach the typechecker the first container-aware typing rules for `Maybe` and
   `Result`.
5. Add diagnostics for mixed-container and non-sequencable usage.
6. Add executable tests covering `Maybe` and `Result` success and failure paths.
7. Update docs and examples.

Deferred:

- `IO` sequencing sugar,
- general `do` notation,
- pattern destructuring in bind position,
- user-extensible sequencing abstractions.

## 15. Open Questions

1. Is `let?` the right surface name, or should Sprout prefer a more
   beginner-friendly keyword such as `bind`?
2. Should the first milestone support only `Maybe` and `Result`, or also the
   current v0 `IO a` convention?
3. Should `let?` sequences require the final expression to be wrapped
   explicitly (`Just(...)`, `Ok(...)`), or should the language insert that
   wrapping automatically?
4. Is there enough long-term value in `let?` alone, or should Sprout plan from
   the start to generalize it into block-based sequencing once v1 effect work
   begins?
