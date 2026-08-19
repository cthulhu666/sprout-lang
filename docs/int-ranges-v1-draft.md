# Int Ranges v1 Draft

This document is a draft design for Sprout v1 integer ranges.

It is not part of normative v0. Its purpose is to define a compact, typed range
surface for integer intervals and integer-sequence workflows after the v0 core
is stabilized.

Current implementation status:

- The initial slice in this draft is now implemented experimentally in the
  prototype.
- `IntRange` remains outside normative v0.
- The implemented slice includes inclusive `a..b` syntax, ascending and
  descending unit-step semantics, distinct `IntRange` values, and the helper
  surface described below.
- **Partly superseded 2026-08-19.** `docs/ranges-v0.md` replaces the
  direction-from-bound-order rule (§2 goal 4, §6 rule 3, and §14 Q2 below, each
  annotated in place). A backwards range is now empty and direction is carried in
  the value via `range_up` / `range_down`. Read `ranges-v0.md` first where the two
  disagree; everything else in this draft still stands.
- Remaining work is mainly contract and diagnostics polish rather than basic
  parser, typechecker, interpreter, or native-backend support.

## 1. Problem Statement

Sprout v0 has `Int`, `List`, `Vec`, and several APIs that work with integer
indexes, but it does not have a first-class way to represent an integer range.

That leaves a gap in the language surface:

- there is no concise syntax for bounded integer intervals,
- users must write recursive helper functions for common range folds,
- membership checks and index-window logic are more verbose than necessary,
- desugaring range-like syntax directly into `List Int` would be a poor fit for
  a strict language because it would allocate eagerly.

## 2. Goals

1. Add a beginner-readable surface for integer ranges.
2. Make range construction cheap and explicit rather than eager list-building.
3. Keep the first milestone small: integer-only, inclusive-only, unit-step.
4. ~~Support both ascending and descending ranges without extra syntax.~~
   **SUPERSEDED 2026-08-19 by `docs/ranges-v0.md`.** "Without extra syntax" meant
   inferring the direction from bound order, which made an empty range
   unrepresentable and turned `range(0, n - 1)` at `n == 0` into a walk over `0` and
   `-1`. Both directions are still supported, but through the peer constructors
   `range_up` / `range_down` — an extra *name*, not extra syntax. `a..b` remains
   ascending-only.
5. Provide a small standard-library surface for common range operations.

## 3. Non-Goals

1. Do not add arbitrary step sizes in the first milestone.
2. Do not add floating-point or generic ordinal ranges.
3. Do not add infinite or open-ended ranges.
4. Do not add range patterns to `match` in the first milestone.
5. Do not immediately redesign all slicing APIs around ranges.

## 4. Proposed Direction

The first v1 range milestone introduces a dedicated `IntRange` value type and a
single inclusive range operator:

```sprout
let xs = 1..5
let ys = 5..1
```

Interpretation:

- `1..5` represents `1, 2, 3, 4, 5`
- `5..1` represents `5, 4, 3, 2, 1`
- the range step is implicit and always has magnitude `1`
- the surface is inclusive at both ends

The operator is expression syntax, not a special eager list literal.

## 5. Core Syntax

Draft syntax:

```sprout
fn small_window(center: Int) -> IntRange =
  center - 2 .. center + 2
```

Desugaring model:

```sprout
let xs = 1..5
```

desugars to:

```sprout
let xs = range(1, 5)
```

The corresponding constructor-style helper remains available as an ordinary
stdlib-facing function:

```sprout
range(lo: Int, hi: Int) -> IntRange
```

## 6. Semantics

Evaluation remains strict and left-to-right.

The range feature adds a compact value form; it does not change evaluation
order.

Semantics:

1. Both bounds are evaluated left-to-right like other binary operators.
2. `lo..hi` enumerates upward from `lo` to `hi`.
3. ~~If `lo > hi`, the range enumerates downward from `lo` to `hi`.~~
   **SUPERSEDED 2026-08-19 by `docs/ranges-v0.md` §5**: if `lo > hi` the range is
   **EMPTY**. Direction is carried in the range rather than inferred from bound
   order, and `a..b` always builds an ascending one. Downward enumeration is
   `range_down(hi, lo)`. A reversed literal with constant bounds is rejected at
   compile time.
4. Both endpoints are included.
5. Range construction itself is cheap; materialization happens only through
   explicit stdlib helpers like `range_to_list`.

Illustrative examples:

```sprout
1..1   # one element
1..3   # 1, 2, 3
3..1   # 3, 2, 1
```

## 7. Typing Model

At a high level:

1. `..` has type `Int -> Int -> IntRange`.
2. Both operands must typecheck as `Int`.
3. `IntRange` is distinct from `List Int` and `Vec Int`.
4. There is no implicit conversion from `IntRange` into a collection type.

Illustrative examples:

```sprout
fn bounds(lo: Int, hi: Int) -> IntRange =
  lo..hi

fn contains_digit(code: Int) -> Bool =
  range_contains(48..57, code)
```

## 8. Precedence and Parsing

The `..` operator should bind:

- looser than arithmetic like `+`, `-`, `*`, `/`,
- tighter than comparisons like `==`, `<`, `>=`,
- tighter than `&&`, `||`, and `|>`.

This preserves intuitive parsing:

```sprout
1..n + 1        # 1..(n + 1)
(a + 1)..(b-1)  # explicit grouping still allowed
a..b == r       # (a..b) == r
```

## 9. Stdlib Surface

The first milestone should keep the API small and biased toward clear, common
operations:

```sprout
range(lo: Int, hi: Int) -> IntRange
range_start(r: IntRange) -> Int
range_end(r: IntRange) -> Int
range_contains(r: IntRange, x: Int) -> Bool
range_count(r: IntRange) -> Int
range_to_list(r: IntRange) -> List Int
range_to_vec(r: IntRange) -> Vec Int
range_fold(r: IntRange, init: a, f: a -> Int -> a) -> a
```

Example:

```sprout
fn sum_to(n: Int) -> Int =
  range_fold(1..n, 0, \acc, x -> acc + x)
```

Deferred helpers:

- step-based constructors,
- filtering/comprehension sugar over ranges,
- slice APIs that take `IntRange` directly.

## 10. Diagnostics

The first v1 milestone should prioritize a few high-value diagnostics:

1. Using non-`Int` bounds with `..`.
2. Confusing `IntRange` with `List Int` or `Vec Int`.
3. Passing an `IntRange` where an API still expects `(start, count)` or similar
   explicit integer arguments.

Example style:

- what failed,
- where the mismatch occurred,
- what explicit conversion or helper would fix it.

## 11. Representation Notes

The implementation should not model inclusive ranges by rewriting `lo..hi` into
something like a half-open interval with `hi + 1`.

That rewrite creates avoidable edge cases around the current backend's `i64`
implementation limits and obscures the source-level meaning of an inclusive
range.

Instead, the runtime representation should preserve the original inclusive
bounds directly.

## 12. Compatibility and Migration

This feature is additive.

Compatibility notes:

1. Existing v0 code remains valid.
2. Recursive helper functions that manually enumerate integer spans can migrate
   gradually to `IntRange` helpers.
3. Existing slice APIs that take explicit integer arguments remain valid until a
   later range-aware redesign is specified.

## 13. Milestone Plan

Proposed first v1 milestone:

1. Finalize inclusive-only `IntRange` syntax and precedence.
2. Add tokenizer/parser support for `..`.
3. Extend the AST and typechecker with `IntRange`.
4. Add runtime/stdlib support for range construction and core helpers.
5. Add parser, typechecker, and behavior tests.
6. Update docs and examples.

Deferred beyond the first milestone:

- explicit step syntax,
- half-open ranges,
- range patterns,
- slice/operator overloading that consumes ranges directly.

## 14. Open Questions

1. Should `IntRange` be a language-level primitive type or an ordinary stdlib
   type with compiler-recognized syntax?
2. ~~Should descending ranges be part of the first milestone, or should `lo > hi`
   be rejected and deferred to a separate design?~~ **RESOLVED 2026-08-19 by
   `docs/ranges-v0.md`.** Neither option as posed: `lo > hi` is not rejected and
   descending is not deferred. `lo > hi` yields an **empty** range (the total
   answer, per `docs/guidelines.md:39`), and descending survives as the explicit
   `range_down` constructor with the direction stored in the value. A reversed
   *literal* is rejected at compile time, which is the "rejected" half of the
   question applied only where it cannot be anything but a typo.
3. Should `range_count` always return `Int`, or should it use `Maybe Int` if
   future integer backends introduce bounded-count concerns?
4. Should later half-open forms be added at all, or should Sprout keep the
   range model inclusive-only for simplicity?
