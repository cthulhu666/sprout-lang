# Sprout Backlog

This file tracks open design, implementation, and tooling follow-up work.

## Current Priorities

1. Resolve the top-level purity story: either tighten the language so non-`IO` expressions cannot hide effects, or narrow the docs so “pure top-level let” does not overclaim what v0 guarantees.
2. Remove or properly specify tuple/record support in the v0 docs so the normative surface does not mention ghost features.
3. Define unreachable-pattern handling explicitly (warn, error, or ignore) instead of leaving it as advisory design guidance.
4. Extend native backend coverage (broader ADT lowering and remaining interpreter parity gaps).
5. Add stronger server-side runtime models (multi-reactor as next target).
6. Expand stdlib text/data helpers (`string_lines`, `string_digits`, vector utility combinators).
7. Improve the formatter/linter beyond the current baseline (deeper structural formatting and broader lint rules).
8. Define the long-term `Int` contract and migrate the native backend away from raw `i64` semantics so overflow-sensitive math matches the language model across interpreter and native execution.

## V1 Roadmap Candidates

1. Add list comprehensions for `List` values in v1.
   Initial scope: `[expr for x in xs]` and `[expr for x in xs if pred]`.
   First milestone constraints: single generator, optional guard, list-only, no pattern generators, and no nested or multi-generator comprehensions.
2. Add inclusive integer ranges in v1.
   Initial scope: a dedicated `IntRange` type, `a..b` inclusive syntax, ascending and descending unit-step semantics, and a small helper surface such as `range_contains`, `range_count`, and `range_fold`.
   First milestone constraints: integer-only, inclusive-only, no custom step syntax, no open-ended ranges, no range patterns, and no immediate redesign of existing slice APIs.
3. Expand native ADT lowering in v1.
   Design doc: [native-adt-lowering-v1.md](./native-adt-lowering-v1.md).
   Initial completed slice: native `Nothing` singleton plus immediate-match optimization for direct constructor-producing scrutinees.
   Planned follow-up: broader constructor forwarding, whole-scrutinee binding support, and specialized representations for tiny ADTs.
