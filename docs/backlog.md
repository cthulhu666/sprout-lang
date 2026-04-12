# Sprout Backlog

This file tracks open design, implementation, and tooling follow-up work.

## Current Priorities

1. Push the current Sprout-hosted REPL toward a native-capable bridge.
   Design docs: [native-repl-roadmap.md](./native-repl-roadmap.md), [repl-self-hosting-v1-draft.md](./repl-self-hosting-v1-draft.md), [compiler-self-hosting-roadmap.md](./compiler-self-hosting-roadmap.md).
   Near-term scope: keep the current Sprout frontend stable, move host-backed session services behind an explicit native-callable bridge, and shape that bridge as reusable language-service infrastructure that serves the REPL first but can later support self-hosted compiler and language-server work.
   Completed groundwork: canonical `python -m sprout.analysis_service_entrypoint` bridge, plus compatibility wrappers in `sprout.analysis_stdio`, `sprout.analysis_service`, and hidden `sprout.cli analysis-service`, and native-backed `repl_check_source(...)`, `repl_declared_names_in_source(...)`, `repl_exported_names_in_source(...)`, `repl_symbol_inventory_in_source(...)`, `repl_diagnostics_in_source(...)`, `analysis_symbol_locations_in_source(...)`, `repl_type_of_in_source(...)`, `repl_instances_in_source(...)`, `repl_eval_expr_in_source(...)`, and `repl_complete_in_state(...)` through that host-service path, with end-to-end native execution now covered by tests and exposed experimentally via `sprout.cli repl --native`, including launcher-side compiled-binary caching between launches, per-run analysis-service subprocess reuse, and one-shot child restart for replay-safe mid-run bridge failures.
   Phase 1 target: replace the execution-oriented backend bundle first, while preserving the current bridge contract and de-REPL-shaping the compiler-facing surface around it.
   Short follow-up: keep shrinking first-class dependence on the Python adapter itself so the native REPL can replace `python -m sprout.analysis_service_entrypoint`, rather than only renaming the module boundary.
   Pause milestone for switching back to core language work:
   - native REPL is stable enough for daily interactive use
   - the remaining Python dependency is isolated behind the adapter/backend seam
   - no active frontend behavior depends on legacy mutable-session hooks
   - docs describe the unfinished work as backend replacement, not frontend instability
   Planned stop-point work:
   - choose the first post-pause backend-replacement target
   Deferred scope: the self-hosted session-engine work in `repl-self-hosting-v1-draft.md` and the broader compiler direction in `compiler-self-hosting-roadmap.md` are no longer the active milestone; treat them as post-native-REPL directions.
   Long-term execution backlog: [self-hosting-eliminate-python-backlog.md](./self-hosting-eliminate-python-backlog.md) tracks the concrete staged work required to remove Python from compiler/tooling ownership entirely.
2. Extend native backend coverage (broader ADT lowering and remaining interpreter parity gaps).
   Native-performance follow-up:
   - make tight Sprout string-processing loops competitive with host builtins so moderate stdin/text workloads do not require dedicated host helpers just to be practical
   - investigate the remaining native overhead in recursive stdlib string loops such as `string_lines(read_file("-"))`, with focus on tail-recursive loop lowering, call/closure overhead, primitive boxing, and efficient string/vector iteration
   - add stable native performance benchmarks for `string_lines`, `trim`, and AoC-style stdin parsing so regressions and wins are measurable
   - target: native `string_lines(read_file("-"))` on the current `day5input`-style workload should complete in low single-digit seconds rather than tens of seconds
3. Add stronger server-side runtime models (multi-reactor as next target).
   Recent groundwork landed: native TCP handle-slot reuse and an experimental `stdlib.http_server` helper layer for structured request parsing/rendering.
   Remaining follow-up: incremental bytes-oriented HTTP reads, keep-alive/chunked support, and stronger concurrent runtime models.
4. Keep expanding stdlib text/data helpers beyond the current baseline (`trim*`, `contains`, `ends_with`, `string_lines`, `string_digits`, vector utility combinators).
   Remaining follow-up: define the Unicode text model explicitly enough to support a future `Char` type and consistent string indexing/length/slice semantics.
5. Improve the formatter/linter beyond the current baseline (deeper structural formatting and broader lint rules).
6. Keep improving local test throughput beyond the current per-file parallel runner.
   Completed groundwork: `just test-parallel` now provides a materially faster local loop than serial `just test-serial`.
   Remaining follow-up:
   - migrate the repeated native compile/run scaffolding in `tests/test_codegen.py` onto the shared cached helper path so more native tests benefit from compile caching
   - measure whether `tests/test_cli.py` native REPL coverage is now dominated by process startup/analysis-service handshake overhead rather than compilation, and only then decide whether more fixture sharing is worth the complexity
   - keep `just test-serial`/`just test-all` available as fallback full-suite entrypoints when diagnosing runner discrepancies or order-sensitive failures
7. Define the long-term `Int` contract and migrate the native backend away from raw `i64` semantics so overflow-sensitive math matches the language model across interpreter and native execution.
8. Continue native memory-management v1.
   Design doc: [native-memory-management-v1-draft.md](./native-memory-management-v1-draft.md).
   Completed groundwork: allocation visibility, centralized managed allocation for Sprout values, heap metadata hooks, and an initial non-moving stop-the-world mark-sweep collector with default threshold-triggered in-process collection in the native profile.
   Remaining v1 scope: close the remaining path-specific live-value gaps outside the current shadow-root coverage, keep validating and tuning the current default threshold (`4096` managed nodes) with the new per-cycle live-heap/timing diagnostics, and keep expanding reclamation-focused validation.
   V2 direction: pause/throughput improvements only after v1 is measured, likely via incremental or generational follow-up work if justified.

## V1 Roadmap Candidates

1. Add list comprehensions for `List` values in v1.
   Initial scope: `[expr for x in xs]` and `[expr for x in xs if pred]`.
   First milestone constraints: single generator, optional guard, list-only, no pattern generators, and no nested or multi-generator comprehensions.
2. Continue the experimental integer-ranges slice toward a final v1 contract.
   Current implemented scope: a dedicated `IntRange` type, `a..b` inclusive syntax, ascending and descending unit-step semantics, and helper surface including `range`, `range_start`, `range_end`, `range_contains`, `range_count`, `range_to_list`, `range_to_vec`, and `range_fold`.
   Remaining follow-up: finalize the normative v1 contract, keep diagnostics sharp, and decide whether later range extensions such as patterns or half-open forms should exist at all.
3. Expand native ADT lowering in v1.
   Design doc: [native-adt-lowering-v1.md](./native-adt-lowering-v1.md).
   Initial completed slice: native `Nothing` singleton plus immediate-match optimization for direct constructor-producing scrutinees.
   Planned follow-up: broader constructor forwarding, whole-scrutinee binding support, and specialized representations for tiny ADTs.
4. Harden the language/runtime prerequisites for external protocol client libraries built on top of `stdlib.scram`, `stdlib.net`, and `stdlib.bytes`.
   Initial scope: keep the byte-building/parsing, TCP, crypto, and generic SCRAM surfaces stable enough for a separate repository to implement protocol-specific auth and wire flows.
   First milestone constraints: no protocol-specific client implementation in this repository, keep host-side builtins minimal, and prefer generic helpers that external libraries can compose.
5. Add records in v1.
   Initial scope: immutable record values with explicit field names, field access, and straightforward construction/update rules that preserve Sprout's strict evaluation model.
   Completed groundwork: experimental nominal record declarations, typed record literals, and read-only field projection via `get record field`.
   Remaining follow-up: record update syntax, a dedicated records draft/spec, and a final field-access surface decision if the language later wants something more ergonomic than the current contextual `get` form.
   First milestone constraints: no row polymorphism, no structural subtyping, no implicit field punning, and no attempt to fold records into the current ADT surface without a dedicated spec.
6. Add a Unicode `Char` type and define string text semantics in v1.
   Initial scope: distinct `Char` values and literals, `String` text defined in terms of Unicode code points, and a small helper surface such as `char_at`, `char_at_or`, `string_from_char`, and `string_chars`.
   Landed initial slice: distinct `Char` type/literals, code-point-based string `length`/`slice`/`find` behavior across interpreter and native execution, and `stdlib.string` helpers `char_at`, `char_at_or`, `string_from_char`, and `string_chars`.
   First milestone constraints: code-point indexing/length/slice semantics only, no grapheme-cluster-aware APIs yet, and no promise of full Unicode normalization or one-to-many case mapping in the initial slice.
7. Add fast `Vec` sorting helpers in v1.
   Landed initial slice: `Ord`-constrained `vec_sort(vec)` and `vec_sort_by(key, vec)` helpers.
   Current built-in coverage: `Ord Int`, `Ord Bool`, and `Ord String`.
   Planned next slices: add constrained instance support first, then use it to define tuple `Ord` instances for composite sort keys such as `(Int, String)`.
   Remaining follow-up: decide whether the long-term design wants a richer public ordering story such as `Eq`, an `Ordering` ADT, constrained tuple instances, or custom descending/comparator APIs.
   First milestone constraints: keep the API `Vec`-focused, keep ordering fully inside the type system, and defer richer ordering surface area until the broader class design is clearer.
8. Add a minimal `Show` typeclass slice for library-friendly value formatting.
   Initial scope: `Show.to_string(x) -> String` with `Int`, `Bool`, and `String` instances, backed by a small host primitive for integer formatting.
   First milestone constraints: no promise that `print` uses `Show`, no interpolation syntax, no collection/ADT deriving, and no debugging-vs-user-display split yet.
9. Generalize experimental `do` notation toward real monadic sequencing.
   Current implemented scope: layout-style `do` blocks with `<-` binds, resolved type-directed for `Maybe` and `Result`, then lowered by a dedicated post-typecheck elaboration step through a small explicit core expression form into nested `match`.
   Completed compiler-architecture follow-up: `do` desugaring no longer stays entangled with the typechecker.
   Completed language follow-up: the experimental `do` surface now supports `IO`, pure local `let` steps, and narrow mixed `IO` plus inner `Maybe`/`Result` sequencing. That narrow mixed shape is now the preferred near-term story for multi-step `IO` and mixed failure-aware flows, with `after(...)` left as a compatibility convenience only.
   Planned next follow-up: prune remaining helper-heavy call sites that still obscure the `do` story, and only revisit broader sequencing abstractions if real code shows the narrow model is insufficient.
   First milestone constraints: keep current `Maybe`/`Result` behavior stable, keep mixed `IO` sequencing intentionally narrow, and prefer explicit `match` over speculative generalization when code needs the whole container value.
