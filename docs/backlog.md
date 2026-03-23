# Sprout Backlog

This file tracks open design, implementation, and tooling follow-up work.

## Current Priorities

1. Push the current Sprout-hosted REPL toward a native-capable bridge.
   Design docs: [native-repl-roadmap.md](./native-repl-roadmap.md), [repl-self-hosting-v1-draft.md](./repl-self-hosting-v1-draft.md).
   Near-term scope: keep the current Sprout frontend stable, move host-backed session services behind an explicit native-callable bridge, and shape that bridge as reusable language-service infrastructure that serves the REPL first but can later support self-hosted compiler and language-server work.
   Completed groundwork: canonical `python -m sprout.analysis_stdio` bridge, plus compatibility wrappers in `sprout.analysis_service` and hidden `sprout.cli analysis-service`, and native-backed `repl_check_source(...)`, `repl_declared_names_in_source(...)`, `repl_exported_names_in_source(...)`, `repl_symbol_inventory_in_source(...)`, `repl_diagnostics_in_source(...)`, `analysis_symbol_locations_in_source(...)`, `repl_type_of_in_source(...)`, `repl_instances_in_source(...)`, `repl_eval_expr_in_source(...)`, and `repl_complete_in_state(...)` through that host-service path, with end-to-end native execution now covered by tests and exposed experimentally via `sprout.cli repl --native`, including launcher-side compiled-binary caching between launches, per-run analysis-service subprocess reuse, and one-shot child restart for replay-safe mid-run bridge failures.
   Short follow-up: keep shrinking first-class dependence on the Python stdio adapter itself so the native REPL can replace `python -m sprout.analysis_stdio`, rather than only renaming the module boundary.
   Deferred scope: the self-hosted session-engine work in `repl-self-hosting-v1-draft.md` is no longer the active milestone; treat it as a post-native-REPL direction.
2. Extend native backend coverage (broader ADT lowering and remaining interpreter parity gaps).
3. Add stronger server-side runtime models (multi-reactor as next target).
   Recent groundwork landed: native TCP handle-slot reuse and an experimental `stdlib.http_server` helper layer for structured request parsing/rendering.
   Remaining follow-up: incremental bytes-oriented HTTP reads, keep-alive/chunked support, and stronger concurrent runtime models.
4. Expand stdlib text/data helpers (`string_lines`, `string_digits`, vector utility combinators).
5. Improve the formatter/linter beyond the current baseline (deeper structural formatting and broader lint rules).
6. Define the long-term `Int` contract and migrate the native backend away from raw `i64` semantics so overflow-sensitive math matches the language model across interpreter and native execution.
7. Continue native memory-management v1.
   Design doc: [native-memory-management-v1-draft.md](./native-memory-management-v1-draft.md).
   Completed groundwork: allocation visibility, centralized managed allocation for Sprout values, heap metadata hooks, and an initial non-moving stop-the-world mark-sweep collector with default threshold-triggered in-process collection in the native profile.
   Remaining v1 scope: close the remaining path-specific live-value gaps outside the current shadow-root coverage, tune the current default threshold (`1024` managed nodes) with more measurements, and keep expanding reclamation-focused validation.
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
   Completed groundwork: experimental nominal record declarations, typed record literals, and read-only field projection via `get record field`.
   Remaining follow-up: record update syntax, a dedicated records draft/spec, and a final field-access surface decision if the language later wants something more ergonomic than the current contextual `get` form.
   First milestone constraints: no row polymorphism, no structural subtyping, no implicit field punning, and no attempt to fold records into the current ADT surface without a dedicated spec.
