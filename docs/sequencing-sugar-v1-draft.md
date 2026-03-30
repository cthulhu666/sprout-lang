# Sequencing Sugar v1 Draft

This document is a draft design for ergonomic sequencing sugar in Sprout v1.

It is not part of normative v0. Its purpose is to define an experimental `do`
surface that removes deeply nested `match` expressions when sequencing
container-like computations such as `Maybe` and `Result`, while leaving room
for broader Haskell-style sequencing later.

## 1. Problem Statement

Sprout can already express sequential failure-aware logic with `match`, but the
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

That code is explicit, but the happy path is visually buried and the same
shape recurs for `Maybe`, `Result`, and eventually effectful sequencing.

## 2. Goals

1. Make sequential `Maybe` and `Result` code read top-to-bottom.
2. Preserve strict, explicit evaluation order.
3. Keep the first milestone small enough to ship end-to-end.
4. Avoid locking Sprout into a dead-end syntax if the language later grows more
   Haskell-like sequencing abstractions.

## 3. Non-Goals

1. Do not add laziness.
2. Do not require higher-kinded types in the first milestone.
3. Do not claim full monadic generality yet.
4. Do not settle the whole future effect-system story in this slice.

## 4. Proposed Direction

The current recommendation is `do` notation with layout-style steps and `<-`
bind syntax.

Illustrative surface:

```sprout
fn read_u32_be(value: Bytes) -> Maybe Int =
  do
    b0 <- bytes_get(value, 0)
    b1 <- bytes_get(value, 1)
    b2 <- bytes_get(value, 2)
    b3 <- bytes_get(value, 3)
    Just(b0 * 16777216 + b1 * 65536 + b2 * 256 + b3)
```

Interpretation:

1. Each `<-` step sequences a container-like value and binds its success
   payload.
2. Failure propagates out of the block.
3. The final expression returns the block result.

## 5. Current Implemented Semantics

The implementation currently supports:

1. Layout-style `do` blocks.
2. `<-` bind steps.
3. A final plain expression at the end of the block.
4. Type-directed sequencing for `Maybe a` and `Result e a`.

The implementation does not yet support:

1. `IO`-specific sequencing.
2. User-extensible sequencing abstractions.
3. Pure local `let` statements inside `do`.
4. Pattern destructuring in bind position.
5. Full Haskell-style desugaring through a standard `Monad`/`Applicative`
   hierarchy.

## 6. Typing Model

At a high level:

1. Each bind step must currently produce either `Maybe a` or `Result e a`.
2. A single `do` block may not mix `Maybe` and `Result`.
3. For `Result`, all bind steps and the final expression must agree on the
   error type.
4. The final expression must return the same outer sequencing family as the
   earlier bind steps.

Examples:

```sprout
fn sum_two(ma: Maybe Int, mb: Maybe Int) -> Maybe Int =
  do
    a <- ma
    b <- mb
    Just(a + b)
```

```sprout
fn parse_pair(a: String, b: String) -> Result String Int =
  do
    x <- parse_nat(a)
    y <- parse_nat(b)
    Ok(x + y)
```

## 7. Desugaring Model

The current implementation parses a dedicated `DoExpr`, lets typechecking
determine which sequencing family each bind step belongs to, then rewrites the
block to nested `match` expressions.

`Maybe` step:

```sprout
do
  x <- expr
  rest
```

desugars to:

```sprout
match expr with
| Nothing -> Nothing
| Just x -> rest
```

`Result` step:

```sprout
do
  x <- expr
  rest
```

desugars to:

```sprout
match expr with
| Err err -> Err(err)
| Ok x -> rest
```

## 8. Why This Is Still Future-Friendly

The current implementation is intentionally narrower than full Haskell `do`,
but the surface is chosen to preserve room for that direction later.

The key forward-compatible choices are:

1. The syntax uses `do` and `<-`, not a throwaway milestone-specific keyword.
2. The parser produces explicit `do` AST nodes instead of hard-coding a parser
   rewrite to one container family.
3. Sequencing resolution is type-directed, which is the right direction if
   Sprout later grows standard abstractions instead of special cases.

## 9. Diagnostics

Current high-value diagnostics should identify:

1. A bind step that does not produce `Maybe` or `Result`.
2. A block that mixes `Maybe` and `Result`.
3. A final expression that returns the wrong sequencing family.
4. A malformed block where a plain expression appears before the final step.

## 10. Compatibility and Status

This feature is additive and experimental.

Compatibility notes:

1. Existing nested-`match` code remains valid.
2. Existing combinator helpers remain valid.
3. The feature is implemented in the prototype, but it is not yet normative
   v0.

## 11. Follow-Up Directions

Likely follow-up directions include:

1. Pure local binds inside `do`.
2. Pattern binds in `<-` position.
3. `Applicative`-style conveniences where they make sense.
4. `IO` integration once the effect story is ready.
5. User-extensible sequencing abstractions, likely via typeclasses or closely
   related machinery.
