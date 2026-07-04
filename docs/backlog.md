# Sprout Backlog

This file tracks open design, implementation, and tooling follow-up work.

## Current Priorities

**Fundamentals-review fix campaign (2026-07-03).** An adversarial review of the runtime,
both codegen paths, the type system, and the prelude found confirmed soundness and
memory-safety holes: effect system unenforced, declared type variables not rigid, no
value restriction, class-method dict dispatch by wrong position, typed-path top-level
`let` globals never GC-rooted (silent use-after-free), UTF-8 out-of-bounds walkers +
unvalidated ingestion, `/` division UB, exhaustiveness gaps. Full findings, probe
programs, session-by-session fix plan, and the five user decisions needed (D1-D5) are in
[fundamentals-code-review-handoff-2026-07-03.md](./fundamentals-code-review-handoff-2026-07-03.md).
**Decisions D1–D5 worked through 2026-07-04:** D1 (division = panic + stdlib `safe_div`;
`+`/`*` overflow = documented i64 wrap), D3 (retire the direct codegen path), D4 (reject
invalid UTF-8, Bytes-primary via the `bytes_to_utf8` choke point), and D5 (total
`parse_int : Maybe Int` + delete dead `split_ints`; `mutvec_get : Maybe a`) all DECIDED;
**D2 (effects) DEFERRED** — W6 is blocked on an effect-system *design* pass, not rollout
shape. See §2 of the handoff doc for full rationale. W1 (global GC roots) and W5
(exhaustiveness) already landed; recommended next unblocked session: W4
(dispatch-by-constraint-position).

**Bare-name type identity — cross-module type-name collision (2026-07-04).** Type
resolution collapses every type to its unqualified name: scrutinee types resolve to
`TConst(after_last_dot(name))` and `infer.build_ctor_map` keys constructor sets by the
same bare name. Two types sharing a short name across modules (e.g.
`stdlib.compiler.Diagnostic` vs `stdlib.compiler.compiler.Diagnostic`) therefore become
one type identity — distinct runtime layouts, indistinguishable to the checker. The
exhaustiveness pass caught this as a false "non-exhaustive match" when `sproutd` first
bundled both modules (the guard fired; the latent hole is that unifying values of the two
layouts would be silently accepted → memory corruption). Mitigated for that instance by
renaming the report-entry type to `stdlib.compiler.ReportEntry` (regression guard:
`tests/stdlib/compiler/test_diagnostic_name_collision.spr`). Root-cause fix (deferred):
thread fully-qualified names through `lookup_type_var` and the `TConst` representation so
type identity is module-qualified. This is a soundness-scale change to type resolution and
directly contends with the documented reason bare keying exists (own-module ADTs keyed
`main.C`, looked up as `C`, must still resolve) — scope it deliberately, not opportunistically.

1. Execute Model C GC-rooting plan (typed Sprout-IR + linear types).
   Design doc: [gc-rooting-model-c-plan-2026-06-02.md](./gc-rooting-model-c-plan-2026-06-02.md).
   Status: Milestone 1 (scalar IR scaffolding, PRs 1.1–1.5) and Milestone 2 PRs 2.1–2.5
   (heap ops + dataflow rooting + boxed ctor allocation + closures + pattern matching)
   have landed. M2 acceptance ("stage-1 self-compile under `--use-ir-codegen` with
   `SPROUT_GC_THRESHOLD=1`") is still gated on PR 2.6+ — the residual expression forms
   still rejected by `ast_to_ir.translate_expr`: `TChar`, `TUnit`, `TTuple`, `TDo`,
   `TRecord`, `TGetField`, `TDict`, `TUnary`, `TRange`. Each ships in its own follow-up
   PR alongside the matching `Pattern` (where one exists). **Before PR 2.6**, land the
   `clang_verifies_ir` promotion (see "PR 2.5 v2 code-review follow-ups (still open)"
   below) so each new IR-codegen test for the residual forms uses the shared helper
   from the start. Five deferred items from the PR 2.3 code-review pass are tracked
   in `BACKLOG.md`.
   PR 2.5 v2 code-review follow-ups (still open):
   - Promote the `clang_verifies_ir` test helper (currently local to
     `tests/stdlib/test_ir_codegen_match.spr`) to a shared module under
     `tests/stdlib/`, and adopt it in `test_ir_codegen_arith / basic / calls /
     closures / control / ctors / strings` and `test_ir_rooting`.  Closes the
     substring-blindness gap that hid bug #2 in PR 2.5.  Closed for the match
     and closures suites; remaining 6 suites need the same upgrade.
     **Sequencing**: land this *before* any PR 2.6+ (the residual M2
     expression-form PRs — `TChar`, `TUnit`, `TTuple`, `TDo`, `TRecord`,
     `TGetField`, `TDict`, `TUnary`, `TRange`).  Two reasons: (a) each new
     IR-codegen test for the residual forms should adopt the shared helper
     from the start rather than accumulating substring-only tests that later
     need rewriting; (b) substring-blindness is the bug class most likely to
     recur in new-form codegen (PR 2.5 bug #2 was exactly a "substring
     passes / clang rejects" case in freshly-added match-codegen).
   List-pattern sugar — pending sweeps (2026-06-08):
   - Expression-side sweep: rewrite multi-element `Cons(a, Cons(b, …))`
     *constructions* in `ast_to_ir.sprout`, `compiler.sprout`, and similar
     files to `[a, b]` / `[a, b, c]` literals.  Stage in batches with a
     bootstrap-seed refresh per batch to keep diffs reviewable.  See
     `docs/style-guide-v0.md` §8 for the policy on what to rewrite vs leave
     (sugar wins for 2+ heads; single-head `Cons(x, …)` constructions and
     `| Cons x rest ->` arms stay long-form).
   PR 2.5 (first /code-review pass) follow-ups:
   - Refactor the ctors-dict tuple `(tag, arity, max_arity, field_kinds_string)` in
     `ast_to_ir.sprout` to a named `CtorMeta` record (currently a positional 4-tuple,
     widened twice now). Trigger the refactor when a 5th field is forced (e.g. source
     location for diagnostics) so the refactor pays for itself rather than being a
     gratuitous reshape. Touch: ~3 destructure sites in ast_to_ir.sprout plus the
     dict-value type signature in ~15 function signatures. Risk: introducing the same
     class of bug the refactor is meant to prevent — defer until forced.
   - Split the field-kinds encoding's `'s'` byte into distinct `'s'` (String, heap)
     and `'c'` (Char, scalar) codes in `stdlib/compiler/field_kinds.sprout` so Char
     fields stop being conservatively over-rooted (small per-ctor-field perf win).
     Now a single-file edit since the encoder is consolidated (PR 2.5 /code-review
     fix #10). Trigger when Char field rooting becomes measurable, or on the next
     scheduled cleanup pass.
   After M2: flip default to `--use-ir-codegen` (M3), then linear types as a user-facing
   feature (M4), then apply linearity to Sprout-IR (M5) so GC rooting correctness becomes
   a theorem rather than a discipline.
   Native REPL groundwork is complete: the combined `build/sproutd` binary (sproutd M3,
   2026-05-26) launches as REPL by default and as the analysis service with
   `--analysis-service <stdlib_root>`. `sproutd_self_init()` auto-resolves stdlib root
   from the executable path; `SPROUT_ANALYSIS_SERVICE_CMD` still works as an explicit
   override. `just repl` wires this launcher. The minimal LSP layer (sproutd M4,
   `stdlib/compiler/lsp_driver.sprout`) handles `initialize`, `textDocument/didOpen/
   didChange/didClose`, `textDocument/hover`, and stub completion.
2. Extend native backend coverage (broader ADT lowering and remaining interpreter parity gaps).
   Native-performance follow-up:
   - make tight Sprout string-processing loops competitive with host builtins so moderate stdin/text workloads do not require dedicated host helpers just to be practical
   - investigate the remaining native overhead in recursive stdlib string loops such as `string_lines` over stdin-loaded text, with focus on tail-recursive loop lowering, call/closure overhead, primitive boxing, and efficient string/vector iteration
   - add stable native performance benchmarks for `string_lines`, `trim`, and AoC-style stdin parsing so regressions and wins are measurable
   - target: native `string_lines` over stdin-loaded text on the current `day5input`-style workload should complete in low single-digit seconds rather than tens of seconds
3. Add stronger server-side runtime models (multi-reactor as next target).
   Recent groundwork landed: native TCP handle-slot reuse and an experimental `stdlib.http_server` helper layer for structured request parsing/rendering.
   Remaining follow-up: incremental bytes-oriented HTTP reads, keep-alive/chunked support, and stronger concurrent runtime models.
4. Keep expanding stdlib text/data helpers beyond the current baseline (`trim*`, `contains`, `ends_with`, `string_lines`, `string_digits`, vector utility combinators).
   Remaining follow-up: define the Unicode text model explicitly enough to support a future `Char` type and consistent string indexing/length/slice semantics.
5. Improve the formatter/linter beyond the current baseline (deeper structural formatting and broader lint rules).
   Immediate tooling bug: `fmt_bin fmt` currently collapses some `.sprout`/`.spr`
   files to a single line, including `stdlib/compiler/lowering.sprout` and
   `tests/stdlib/compiler/test_lowering.spr`. Fix formatter newline preservation
   and add regression coverage before relying on the pre-commit formatting hook.
6. Keep improving local test throughput.
   Completed: native Sprout test files for all six compiler stages live in `tests/stdlib/compiler/` — `test_lexer.spr`, `test_bundler.spr`, `test_parser.spr`, `test_checker.spr`, `test_lowering.spr`, `test_codegen.spr`; run via `just test` (`test-stdlib-stage1`).
   Completed: fixed bundler UTF-8 bug in `strip_headers_b` — byte offset was used as codepoint index in `str_slice`, causing parse failures on files (e.g. `stdlib/compiler/types.sprout`) with multi-byte characters in comment headers.
   Remaining follow-up:
   - add compile caching for the stdlib test runner so repeated runs of unchanged test files skip the IR-emit + clang step
7. Define the long-term `Int` contract and migrate the native backend away from raw `i64` semantics so overflow-sensitive math matches the language model across interpreter and native execution.
8. Continue native memory-management v1.
   Design doc: [native-memory-management-v1-draft.md](./native-memory-management-v1-draft.md).
   Completed groundwork: allocation visibility, centralized managed allocation for Sprout values, heap metadata hooks, and an initial non-moving stop-the-world mark-sweep collector with default threshold-triggered in-process collection in the native profile.
   Remaining v1 scope: close the remaining path-specific live-value gaps outside the current shadow-root coverage, keep validating and tuning the current default threshold (`4096` managed nodes) with the new per-cycle live-heap/timing diagnostics, and keep expanding reclamation-focused validation.
   V2 direction: pause/throughput improvements only after v1 is measured, likely via incremental or generational follow-up work if justified.
9. **Incremental partial application miscompiles (SIGSEGV).** Applying a partial closure
   one argument at a time when two or more arguments remain crashes at runtime.
   `add3(1)(2)(3)` (with `fn add3(x, y, z)`) typechecks — the checker is currying-correct —
   but codegen builds an arity-2 partial closure (`__sprout_partial_N(env, a0, a1)`) and the
   next call site applies a single argument, under-saturating it; the malformed call returns
   an `Int` that the following application reinterprets as a closure pointer (`inttoptr` +
   `load`) → SIGSEGV. Saturating the partial in one call (`feed_two(add3(1), 2, 3)`) works, so
   only incremental application is affected. The fix needs a design decision: either support
   incremental partial application (a curried closure-calling convention that builds a fresh
   partial on under-saturation) or reject under-saturating re-application at the checker (as a
   clean type error, not a segfault). This decision also gates an `ap`/`<*>`-style Applicative
   interface (which feeds one wrapped argument at a time); a `map2`/`map3` interface — fully
   saturated application — is unaffected. Counterpart defect (checker rejecting function-typed
   returns because `ctor_result_type` over-stripped arrows) is fixed in `infer.sprout` via
   `fn_return_type`; regression: `tests/stdlib/test_function_returning_function.spr`.
   The remaining crash is coupled to the currying model and to item 12 (`|>` semantics); both
   are framed for decision in [currying-and-pipe-decision-v1.md](./currying-and-pipe-decision-v1.md).

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
10. Add an `Alternative` typeclass for first-success chaining.
   Motivation: self-hosted parser combinators (Phase 6 self-hosting) need a principled "try this, else try that" primitive. The current workaround is a list-based `try_ops` helper in `stdlib/compiler/lexer.sprout`.
   Initial scope: `class Alternative f { fn alt(left: f a, right: f a) -> f a }` with a `Maybe` instance. A `<|>` infix operator would make call sites readable but requires tokenizer and parser changes (new 3-char token not currently in the language).
   First milestone constraints: `Maybe` instance only to start; decide on `<|>` operator vs named `alt` before widening; no `Applicative`/`Functor` hierarchy requirement in the initial slice.
11. Add algebraic effect handlers (phase 1: one-shot linear handlers).
   Design doc: [effect-system-handlers-draft.md](./effect-system-handlers-draft.md).
   Motivation: eliminate explicit `TestState` threading in stdlib tests and establish the
   handler infrastructure that richer effect patterns (async, generators, capability
   injection) will build on.
   Initial scope: `effect` declarations, `handle`/`with` expressions, implicit perform,
   multi-label effect rows (`!{IO, Test}`), one-shot linear codegen via handler-record
   passing (no heap continuations or setjmp).
   First milestone constraints: one-shot handlers only (no multi-shot resumption), no
   open effect row polymorphism, no constrained effect operations, backwards-compatible
   (old `TestState` API stays in `stdlib/test.sprout` alongside new `run_tests`).
12. Revisit `|>` multi-arg semantics.
   Currently `x |> f(a, b)` desugars at parse time to `f(a, b, x)` (value appended as last
   argument). This gives the operator two distinct modes (plain-identifier RHS vs. partial-call
   RHS) that are not compositionally obvious. Options: drop the multi-arg form and require
   explicit lambdas (`x |> fn(y) = f(a, y)`), adopt Elixir-style value-first (`f(x, a, b)`),
   or keep as-is. First-class use is already covered by `pipe_apply` in the prelude.
   Decision deferred; current behaviour unchanged.
   **Coupled with item 9 (currying model)** — the two are one decision, framed together in
   [currying-and-pipe-decision-v1.md](./currying-and-pipe-decision-v1.md): a curried Sprout
   collapses this operator to the single rule `x |> g` ≡ `g(x)`; an n-ary Sprout keeps it as
   parser sugar and resolves the two-mode shape syntactically.
14. Add logging, debugging, profiling, and introspection to the self-hosted compiler (future).
   Design doc: [observability-guard-rails.md](./observability-guard-rails.md).
   These features are not scheduled, but the design constraints in that doc must be respected in all Stage 2+ self-hosted compiler code so they remain practical to add. The six constraints — source locations first-class, explicit typed passes, explicit capability passing, no premature pass fusion, type survival into typed core, accurate effect annotations — are active guard rails, not future work items.
14. Add a source-level debugger for compiled user programs (v1).
   Design doc: [debugger-v1-draft.md](./debugger-v1-draft.md).
   Approach: emit LLVM DWARF debug metadata from the codegen pass (opt-in via `--debug`
   flag), then use `lldb`/`gdb` as the debugger UI.  Every `TypedExpr` already carries
   `SourcePos(index, line, col)` — the foundation is in place.
   Three milestones:
   - M1: DWARF emission in `codegen.sprout`; 4th IR section for debug metadata; `--debug`
     flag wired through the compile driver.  Delivers `b file.spr:N`, `n`, `s`, `bt` in
     `lldb` at Sprout source granularity for user-module functions.
   - M2: Extended `SproutCtorMeta` with `field_kinds` descriptor; ADT pretty-printer tool
     under `tools/` (implementation language — LLDB Lua / standalone C binary / format
     strings — decided at M2 kickoff; no Python).  Delivers human-readable ADT values at
     breakpoints.
   - M3: `just build-debug` / `just debug-run` recipes; README §Debugging section.
   First milestone constraints: debug metadata is strictly opt-in; release builds are
   unchanged; M1 scopes `!dbg` to user-module functions only (not stdlib/prelude) to
   avoid misleading source attribution in multi-file bundles; full multi-file DWARF is a
   post-M1 follow-up.
15. `wrap` ergonomics follow-ups (the v1 `wrap` keyword shipped 2026-06-13).
   The zero-cost distinct-type feature `wrap Foo = T` is normative — see
   `docs/spec-v0.md` §5.6.1.  These ergonomics improvements remain open:
   - Parameter-level destructuring: `fn f(Foo x) -> ...` desugars to
     `match arg with | Foo x ->`; useful for all single-constructor types,
     not just wrap.
   - Auto-generated zero-cost accessor: `wrap Foo = T` generates
     `fn foo_inner(Foo x) -> T = x`.
   - Named-field variant (longer-term): `wrap Foo { inner: T }` for named
     accessor syntax.
   - `opaque type` for Scala 3-style module-boundary transparency
     (transparent within defining module, opaque to callers).
16. Span `BundleErr`, `LowerErr`, and codegen errors (guideline #5 Phase 3).
   Phase 1+2 landed in `fix/span-error-types`: `Diagnostic`, `InferErr`, `TypedErr`,
   `CheckErr`, `BodyErr` all carry `SourcePos`; lex/parse/type errors now print
   `line:col: ERROR: msg`. Remaining gaps — every `no_pos()` and `dummy_pos()` call in
   `compiler.sprout` that wraps a `BundleErr`/`LowerErr`/`IrLinesErr` — require adding
   `SourcePos` to `BundleResult`, `LowerResult`, and `IrLinesResult`. Sequencing: land
   after deriving-v1 to avoid compounding bootstrap cycles.
17. Add `stdlib.path` as the canonical Path API (v1).
   Design doc: [stdlib-path-v1-draft.md](./stdlib-path-v1-draft.md).
   Motivation: PR #40 introduced `wrap FilePath`/`wrap StdlibRoot` for the
   compiler's swap-bug class, but the underlying path-construction is still naive
   `str_concat` joins in `module_loader.module_name_to_path` and
   `bundler.prelude_path` (latent trailing-slash and empty-root bugs). There is no
   stdlib Path surface today, so any future user program that touches the
   filesystem will re-invent join/parent/extension logic by hand. Designing
   `stdlib.path` now is cheaper than retrofitting after users depend on raw
   `String` paths.
   Initial scope: two zero-cost wraps `File` and `Dir` (extending PR #36's `wrap`
   philosophy to the stdlib boundary), pure ops `dir_file` / `dir_sub` / `*_parent`
   / `*_basename` / `file_extension` / `file_with_extension` / `*_normalize`,
   smart constructors `file_checked` / `dir_checked` rejecting empty + NUL, and
   migration of `read_file` / `write_file` / `*_exists` / `dir_list` to take
   `File` / `Dir`. Compiler-internal `FilePath` / `StdlibRoot` retire to
   `path.File` / `path.Dir`.
   First milestone constraints: POSIX-only (no Windows separator/drive-letter
   abstraction), no absolute-vs-relative type distinction, no eager
   normalization, no symlink resolution, no byte-level (OsString-style) paths.
