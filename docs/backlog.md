# Sprout Backlog

This file tracks open design, implementation, and tooling follow-up work.

## Current Priorities

1. Extend native backend coverage (broader ADT lowering and remaining interpreter parity gaps).
2. Add stronger server-side runtime models (multi-reactor as next target).
3. Expand stdlib text/data helpers (`string_lines`, `string_digits`, vector utility combinators).
4. Improve the formatter/linter beyond the current baseline (deeper structural formatting and broader lint rules).
5. Define the long-term `Int` contract and migrate the native backend away from raw `i64` semantics so overflow-sensitive math matches the language model across interpreter and native execution.
6. Continue native memory-management v1.
   Design doc: [native-memory-management-v1-draft.md](./native-memory-management-v1-draft.md).
   Completed groundwork: allocation visibility, centralized managed allocation for Sprout values, heap metadata hooks, and an initial exit-time non-moving stop-the-world mark-sweep collector.
   Remaining v1 scope: extend the new shadow-root model across the remaining live managed-value paths, define normal mid-execution collection policy, and add stronger reclamation-focused validation.
   V2 direction: pause/throughput improvements only after v1 is measured, likely via incremental or generational follow-up work if justified.

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
4. Add a first generic SCRAM helper slice using `stdlib.net`, `stdlib.bytes`, and `stdlib.crypto`.
   Initial scope: password-message building blocks, client nonce handling, and the first round of SCRAM message parsing/building.
   First milestone constraints: keep the helper surface small, bytes-oriented, and protocol-agnostic rather than embedding database-specific client APIs.
5. Add records in v1.
   Initial scope: immutable record values with explicit field names, field access, and straightforward construction/update rules that preserve Sprout's strict evaluation model.
   First milestone constraints: no row polymorphism, no structural subtyping, no implicit field punning, and no attempt to fold records into the current ADT surface without a dedicated spec.
