# Sprout Backlog

Purpose: track progress toward a usable general-purpose language, with a concrete MVP target of a terminal UI app that can browse Sentry issues.

Legend:
- Priority: `P0` (critical), `P1` (important), `P2` (later)
- Status: `[ ]` todo, `[~]` in progress, `[x]` done

## MVP Goal: Sentry Issue Browser (Terminal UI)

Definition of done:
- Authenticate with Sentry API token.
- Fetch and render issue lists.
- Navigate list in terminal UI.
- Open/select issue details.
- Handle API/network/parse errors without crashing.

## Backlog

### 1) Language Core and Safety

- [x] `P0` Add `Result e a` and core helpers in stdlib (`map`, `map_error`, `and_then`, `with_default`).
- [x] `P0` Define runtime error conventions for effectful builtins (no silent exits).
- [x] `P1` Add ergonomic helpers for control flow (`when_ok`, `when_error`, optional pipeline helpers).
- [x] `P1` Decide how builtins participate in effect tracking; host-implemented builtins now follow the same effect-typing rule as ordinary functions, with runtime/external interaction tracked via `!{IO}` and value-level `Maybe`/`Result` shapes kept separate from effectfulness.
- [ ] `P2` Move `stdlib.compiler` to a dedicated tooling/compiler namespace once the non-stdlib tooling-package model is settled.
- [ ] `P2` Add syntactic sugar for `Ref` operations in do-notation: `:=` for `ref_write`, `<~` for a ref-read bind step, and `var x = expr` as `x <- ref_new(expr)`.
- [x] `P2` Add effect-sequencing sugar for `IO Unit` flows (`do` blocks or a dedicated sequencing operator).
- [x] `P2` Align function composition operator direction with Elm/F# conventions; `f >> g` now means `g(f(x))` and `f << g` means `f(g(x))`.
- [x] `P1` Add string interpolation syntax: backtick template literals `` `hello ${name}!` `` desugar to `string_concat_many` calls; `StringTemplateExpr`/`TemplateExprPart` AST nodes added; lexer emits 5 new token kinds (`TEMPLATE_START`, `TEMPLATE_LIT`, `TEMPLATE_INTERP_START`, `TEMPLATE_INTERP_END`, `TEMPLATE_END`); desugar runs in Python typechecker before inference, and mirrored in the Sprout bootstrap checker (`checker.sprout`); parser + checker parity tests pass with `string_template_basic.spr` in corpus.
- [ ] `P2` Revisit string-interpolation type-directed dispatch (Mechanism A): Phase 4 ships a simple syntactic-coercion form (elaborator inserts `template_to_string` only at `String`-expected contexts; default template result is `String`). Evaluate migrating to an `IsTemplate` typeclass with instances for `String` and `StringTemplate` once usage patterns settle. Class-based dispatch is more principled and consistent with the rest of the class system; tradeoff is added constraint-machinery overhead and possible defaulting ambiguity. Decision should be driven by whether a third meaningful instance (e.g. `Bytes`, a logging frame, a tagged-template processor) lands and forces generality.

### 2) Networking and HTTP Client

- [x] `P0` Add builtin: `http_request(method, url, headers, body, timeout_ms) -> Result HttpError HttpResponse`.
- [x] `P0` Define `HttpResponse` shape (`status`, `headers`, `body`) and `HttpError` variants.
- [x] `P1` Add convenience wrappers in `stdlib/http_client.sprout` (`get`, `post`, header helpers).
- [x] `P1` Ensure interpreter/native parity for HTTP client builtins.
- [x] `P0` Add outbound TCP client connect primitive (`tcp_connect(host, port)`) for external services such as databases.
- [x] `P0` Add exact-read and write-all socket operations suitable for framed protocols.
- [x] `P1` Define transport failures for socket operations as typed `Result` values instead of only runtime-fatal builtin errors.

### 2.5) Binary Data and Protocol Primitives

- [x] `P0` Add a stable `Bytes` type for raw binary data handling.
- [x] `P0` Add byte primitives: length, indexing, slicing, append, and construction.
- [x] `P0` Add big-endian integer encode/decode helpers for framed protocols.
- [x] `P1` Add UTF-8 string/bytes conversion helpers and null-terminated string helpers.
- [x] `P1` Add efficient byte-buffer/builder utilities so protocol parsers do not depend on repeated full-copy concatenation.
- [ ] `P2` Fix `bytes_builder_append` to O(1) amortized: currently O(n_left + n_right) per call (copies full chunk arrays into a new flat array), so a `list_fold` over n strings costs O(n²) total chunk copies. Switch to a tree/rope representation where append creates a new internal node pointing at both operands (O(1)), and `builder_build` traverses the tree once (O(total_bytes)). Also add `builder_str(s: String) -> Builder` (wrap a string as a single-chunk builder without a Bytes intermediary) and `builder_to_str(b: Builder) -> String` (build + return String directly, skipping the Bytes allocation + UTF-8 round-trip). These three changes unblock a pure-Sprout `string_join_suffix` implementation using `list_fold` + builder.

### 3) JSON Support

- [x] `P0` Add `json_parse(String) -> Result JsonError Json`.
- [x] `P0` Add JSON query helpers (`json_get`, `json_get_string`, `json_get_int`, `json_get_array`, etc.).
- [x] `P1` Add `json_stringify(Json) -> String` for debug and payload building.
- [ ] `P2` Reimplement `json_stringify` in Sprout once string/escaping primitives make that practical, keeping host builtins reserved for impossible or efficiency-critical operations.
- [ ] `P1` Add tests for malformed input and edge cases.

### 4) Terminal UI Runtime

- [~] `P0` Add terminal builtins: alternate screen, clear, cursor move, hide/show cursor, style/color.
- [~] `P0` Add key input primitive (single-key read with non-blocking/poll mode).
- [ ] `P1` Add line wrapping / viewport helpers in stdlib.
- [ ] `P1` Add basic event loop utility for TUI apps.

### 5) Data Structures and Collections

- [x] `P1` Add practical indexed sequence type (`Array`/`Vector`) for UI lists.
- [x] `P1` Add dictionary/map type for API payload handling.
- [x] `P1` Add stdlib text parsing helpers: `string_lines(String) -> Vec String`.
- [x] `P1` Add stdlib digit helpers: `string_digits(String) -> Vec Int`.
- [ ] `P2` Generalize `string_join_newlines` C workaround to `string_join_suffix(suffix: String, lines: List String) -> String` (drop the hardcoded `\n`), then re-implement it in pure Sprout using `list_fold` + builder once the builder O(1) fix and `builder_str`/`builder_to_str` primitives land (see section 2.5). Remove the C builtin once the native implementation is verified equivalent. `string_join_newlines` was introduced as a workaround (2026-05-11) to eliminate a 204K-deep recursive right-fold in `codegen.sprout`'s `join_line_sections` that caused a 2.6 GB memory spike during stage-2 self-compile.
- [x] `P2` Extend collections helpers (`vec_slice`, `vec_reverse`, `dict_keys`, `dict_values`).
- [~] `P2` Add vector utility combinators (for example `vec_sum_by`; `vec_max_subsequence_by_count` is now a maybe/later item).
- [x] `P2` Add set type and common ops.

### 6) Modules and Packaging

- [x] `P0` Implement real module namespaces (remove flattened global import model).
- [x] `P1` Move global string helpers into namespaced stdlib module(s).
- [ ] `P1` Define package/dependency conventions for third-party modules.

### 7) Tooling and Developer UX

- [x] `P2` Extract the C runtime out of the `runtime_c = """..."""` string in `sprout/cli.py` into a standalone `runtime/sprout_runtime.c` source file (2026-05-17). All `build-stage*`, `test-stdlib-stage*`, `compile-examples-stage*`, and `compile-native` justfile recipes now link `runtime/sprout_runtime.c` directly — no Python invocation needed for the C runtime. `just update-runtime` regenerates the file when the embedded template in `cli.py` changes. `scripts/gc_safety_check.py` updated to read the file directly (now reports correct line numbers). Remaining dynamic piece (analysis bridge with embedded Python executable path) is still rendered by `cli.py` for native REPL builds only.

- [x] `P1` Add REPL (parse/typecheck/eval loop).
- [x] `P1` Add better diagnostics for module/import/export errors with source context.
- [x] `P2` Add formatter/linter baseline.
- [ ] `P2` Improve formatter/linter beyond the baseline (structural formatting and broader lint rules).
- [~] `P1` Execute the staged self-hosting plan in [docs/self-hosting-eliminate-python-backlog.md](./docs/self-hosting-eliminate-python-backlog.md), with the end goal that compiler/tooling ownership moves from Python into Sprout and the Python path becomes compatibility-only before removal.
  - bootstrap lexer (`stdlib/compiler/source`, `token`, `lexer`) is at Python tokenizer parity
  - bootstrap parser AST types and parser exist in `stdlib/compiler/ast`, `parser`
  - bootstrap HM typechecker stack exists in `stdlib/compiler/types`, `unifier`, `infer`
  - `stdlib/compiler/driver.sprout` emits a flat s-expression AST dump (one decl per line)
  - `tools/dump_ast.py` emits the same format via the Python parser
  - `tests/test_parser_parity.py` runs both on the conformance corpus and diffs output; 11/11 pass (no known divergences — `++` now desugars to `append` in both parsers)
  - **integration seam landed**: `stdlib/compiler/checker.sprout` wraps `infer.typecheck_decls` behind a `CheckResult` ADT; `stdlib/compiler/type_driver.sprout` is an executable that lex→parse→check→dumps typed names; `tools/dump_types.py` does the same via the Python typechecker; `tests/test_checker_parity.py` confirms 6/6 corpus files match (no known divergences — forall generalization fully implemented)
  - **Phase 2 driver landed**: `stdlib/compiler/compiler.sprout` exposes `compile_source`/`compile_file` API; `stdlib/compiler/compile_driver.sprout` is an end-to-end executable; `sprout.cli bootstrap-check` routes at least one real CLI check path through Sprout-owned control flow
  - **FnDecl body inference landed**: `check_fn_body` instantiates the annotation scheme and checks the body against it; unknown-variable/constructor errors silently accepted (builtin leniency), real type mismatches propagate; checker corpus expanded to 6 files
  - **builtin env seeded**: `checker.check_program` starts from a pre-populated env (~25 entries: ADT constructors, string/IO ops, dict/list ops) so body inference resolves calls to common functions without leniency fallback
  - **ClassDecl/InstanceDecl landed**: class methods registered as globally polymorphic schemes; instance method bodies type-checked against method annotations; `type_classes.spr` added to checker parity corpus (now 6/6)
  - **type aliases landed**: `type alias Name = TypeExpr` parsed in Python + Sprout parsers; Python typechecker expands aliases as a pre-desugar pass; bootstrap checker skips `AliasDecl` (transparent to inference); `Set` type added for constraint-satisfaction work pre-work
  - **checker parity corpus expanded 6→8**: `stdlib_fold_filter_map.spr` and `stdlib_mixed_io_result_do.spr` added; `tools/dump_types.py` now seeds prelude ADT constructors + list/dict helpers so corpus files that call them can be type-checked without full module loading
  - **constraint-satisfaction checking landed**: at concrete call sites the bootstrap checker looks up `@class:<method>` markers in env to identify class methods, then verifies a matching `@inst:<class>:<type>` marker exists; missing instances produce a typed error "No instance of X for T in function f"; `tests/conformance/run/instance_check.spr` added to conformance corpus and passes
  - **record field access landed**: `RecordDecl` registers `@rec:<Name>:<field>` markers in env; `RecordExpr` and `GetFieldExpr` inference implemented; `record_types.spr` fixed to use `get p x` syntax, added to conformance corpus and parity corpus (now 9/9)
  - **do-bind monadic unwrapping landed**: `infer.sprout` now unwraps `Maybe a`/`Result e a` at do-bind sites so the bound variable gets the payload type; `append` seeded in checker env and `dump_types.py`; `stdlib_mixed_io_maybe_do.spr` added to parity corpus (now 10/10)
  - **GHC-style forall variable ordering landed**: both Python and bootstrap Sprout checkers now use left-to-right first-appearance ordering for forall vars; bootstrap `scheme_to_string` now renames bound vars to a, b, c… in that order (matching Python); `poly_types.spr` (multi-param ADTs, `Either`, `Pair`) added to checker parity corpus (now 11/11)
  - **module loading wired into type_driver + compile_driver**: `type_driver.sprout` and `compile_driver.sprout` now resolve imports via `module_loader.sprout` before typechecking; `sprout.cli bootstrap-check` passes `stdlib_root` so CLI path fully works; `BootstrapCheckParityTests` (11/11) added to `test_checker_parity.py` to verify `bootstrap-check` CLI output matches Python typechecker
  - **batch mode + import corpus landed**: both drivers now accept `<stdlib_root> <file>...` batch mode with `=== path ===` separators; `test_checker_parity.py` runs each driver once for all 13 files (7.6x speedup: 610s → 80s); `is_lowercase_name` fixed to reject qualified type names (e.g. `json.Json`); `dump_types.py` extended with `load_module_bundle` for import-using files; new `tests/conformance/parity_import/` corpus with 2 import-using files; parity corpus now 13/13 on both checkers
  - **M1 complete (14/14)**: `strip_module_prefix` in `lookup_type_var` fixes qualified annotation mismatch; `alias_env` threading through `typecheck_decls_inner` expands type aliases inline; `VarPattern` fix prevents cross-branch type leakage; all 14 `stdlib/compiler/*.sprout` modules pass `bootstrap-check`; Python recursion limit bumped to 20000
  - **M3 complete**: `stdlib/compiler/bundler.sprout` implements full topological module loading, cycle detection, prelude injection, symbol table building, and name qualification; `bundle_driver.sprout` is a batch-mode executable; `test_bundler_parity.py` confirms 3/3 parity corpus files match Python's bundler output; interpreter extended with Char ordering support; self-hosted parser extended for single-constructor types + class superclasses; `ClassDecl` gains `superclasses` field; module name dotted-ident scanning fixed
  - **M6 manual stage-2 verified**: Sprout-owned codegen now emits enough LLVM IR for `compile_driver.sprout` to build a stage-1 native compiler via `compile_driver_bin --emit-ir` + `clang`; that compiler type-checks the 5-file bootstrap corpus successfully. The stage-2 `Int vs Int` / `Maybe vs Maybe` identity mismatch was fixed by preserving primitive ADT constructor field types in `codegen.sprout` so `TConst String` payload equality lowers to `str_eq` instead of packed pointer identity.
  - **string interpolation mirrored in bootstrap compiler (Phase 3)**: backtick template literals now parsed + desugared in the Sprout self-hosted pipeline: 5 new `TokenKind` variants in `token.sprout`; `scan_template_content`/`scan_interp_body` state machine in `lexer.sprout`; `parse_template`/`collect_template_parts` in `parser.sprout`; `desugar_program` + `desugar_template` pass wired into `checker.sprout`'s `check_program_with_env`/`typecheck_typed`; `StringTemplateExpr` exhaustiveness stubs in `infer.sprout` and `bundler.sprout`; `dump_expr` extended in `driver.sprout`; `string_template_basic.spr` added to parser + checker parity corpus; 26/26 checker parity tests pass. Conformance corpus: `stdlib_string_template_basic.spr` added (prefixed `stdlib_` so bundler injects the prelude's `Cons`/`Nil`/`to_string` needed by `string_concat_many` desugaring). `test_modules.py` exhaustiveness gaps fixed: `render_kind` in 3 test programs got `| _ -> "template"` catchall for the 5 `TokenTemplate*` constructors, and `describe_expr` got `| ast.StringTemplateExpr _ _ -> "template"` for the new constructor.
  - **Phase 9 analysis service binary landed (2026-05-17)**: `stdlib/compiler/analysis_service_driver.sprout` is a JSON-over-stdio analysis daemon; handles `declared_names_in_source`, `exported_names_in_source`, `symbol_inventory_in_source`, `symbol_locations_in_source`, `check_source`, `diagnostics_in_source` (last two require stdlib root as `argv[0]`); stubs `type_of`, `instances`, `eval_expr` to `not_implemented`. Built as `analysis_service_bin` via new `just build-analysis-service` recipe. Crash fixed: `term_read_line`, `term_write`, `json_parse` added to `extern_sigs_list()` in `codegen.sprout` — missing entries silently returned `ret i64 0` causing `sprout_tag(null)` abort. All 5 manual test cases pass. Remaining: parity test suite, stdlib-root discovery mechanism for REPL integration, unimplemented semantic ops.

### 7.3) In-Language Stdlib Test Framework

- [x] `P1` Add `stdlib/test.sprout`: minimal HUnit-style framework (`TestState (Ref Int) (Ref Int)`, `new_state`, `assert_true`, `assert_false`, `assert_eq`, `summary`). `assert_eq` uses `where Eq a, ToString a` — correct class-based equality, `ToString` only for failure messages.
- [x] `P1` Add `Eq (Maybe a) where Eq a`, `Eq (List a) where Eq a`, `Eq (Result e a) where Eq e, Eq a` instances to `stdlib/prelude.sprout`. `Eq (List a)` uses top-level `list_eq` helper to avoid self-referential instance-body `eq` call.
- [x] `P1` Add `Eq Type`, `Eq Effect`, `ToString Type`, `ToString Effect` instances to `stdlib/compiler/types.sprout`. `Eq Type`'s `TTuple` case uses `types_eq` helper for the same reason.
- [x] `P1` Add `tests/stdlib/test_math.spr` (27 assertions), `tests/stdlib/test_string.spr` (31 assertions), `tests/stdlib/compiler/test_types.spr` (20 assertions).
- [x] `P1` Add `test-stdlib` recipe to `justfile`: pure-shell loop over `tests/stdlib/` and `tests/stdlib/compiler/`, greps for `SUITE FAILED`, exits 1 if any suite fails. No Python.
- [ ] `P2` Add `tests/stdlib/test_collections.spr` once `Eq Vec`, `Eq Dict` instances exist.
- [ ] `P2` Add law-oriented conformance tests via `assert_eq` once list/functor/monoid `Eq` instances are stable.

### 7.5) Type Classes (Collections First)

- [x] `P0` Add class declarations and constrained function signatures (`class`, `where` constraints).
- [~] `P0` Add instance declarations and resolution (`instance` lookup, coherence checks).
- [~] `P0` Implement dictionary-passing lowering in typechecker/backend (hidden-method-parameter lowering supports constrained polymorphic helpers via forwarding and now monomorphizes concrete call sites to specialized wrappers; true first-class dictionaries for polymorphic class methods are blocked on higher-rank method-field representation).
- [x] `P0` Add `Functor` class and instances for `List` and `Vec`.
- [x] `P0` Add `Foldable` class and instances for `List` and `Vec`.
- [x] `P1` Add `Semigroup` class with associativity law documented.
- [x] `P1` Replace `++` special-case dispatch with proper infix operator machinery: `++` desugars to `append` in both Python and Sprout parsers; `__append` sentinel and `infer_semigroup_append` removed from `infer.sprout`; instance resolution uses head-constructor matching so `List Int` matches `instance Semigroup (List a)`; parser parity divergence eliminated.
- [x] `P1` Add `Monoid` class with identity law documented.
- [x] `P1` Add `Eq` class with `==`/`!=` constraint checking; superclass of `Ord`.
- [ ] `P1` Add `deriving Eq` (and similar) for ADT structural equality — currently ADT `==` uses runtime primitive without typeclass dispatch; tracked as known gap.
- [x] `P1` Add pragmatic utility classes (`Eq`, `Ord`, `ToString`) for collection-focused workflows.
- [ ] `P1` Add law-oriented conformance tests (functor identity/composition, monoid identity/associativity).
- [ ] `P1` Add diagnostics for ambiguous/overlapping instance errors.
- [ ] `P2` Add deriving/specialization follow-ups once core class system is stable.

### 8) External Integration Example Layer

- [~] `P0` Add `examples/sentry_api.sprout` with typed wrappers for key endpoints:
  - typed list-issue summary decoding is now implemented
  - issue details are now implemented for the interactive browser
  - pagination cursor extraction remains pending
- [ ] `P1` Add auth/token helpers and secure configuration loading.
- [ ] `P1` Add integration tests with mocked HTTP responses.

### 8.5) Runtime and FFI Foundations for Database Clients

- [x] `P0` Define a safer representation for external resource handles (currently `stdlib.net` wrapper ADTs; true opacity still depends on hidden constructors).
- [x] `P1` Add environment/config helpers such as `env_get(name) -> Maybe String`.
- [x] `P1` Define test support for integration-style IO programs that depend on external services.

### 9) Issue Browser TUI App

- [~] `P0` Build `examples/sentry_issue_browser_tui.sprout`:
  - load token/org/project config from environment is now implemented
  - fetch first page is now implemented
  - render list is now implemented
  - key navigation (`j/k`, arrows, enter) is now implemented
  - refresh/retry is now implemented
- [x] `P1` Add issue detail panel.
- [ ] `P1` Add filtering/sorting controls.
- [ ] `P2` Add search and pagination UI.

## Minimum Viable Path to Escape Python

Goal: a native binary produced by `sprout compile stdlib/compiler/compile_driver.sprout` that can compile itself without Python involvement.

Six ordered milestones. Each is a prerequisite for the next.

### M1 — Bootstrap self-typecheck ✓ DONE

The bootstrap checker successfully typechecks all `stdlib/compiler/*.sprout` modules (14/14).

- [x] `P0` Fix qualified type name resolution in bootstrap checker: `strip_module_prefix` in `lookup_type_var` normalises `ast.TypeExpr` → `TypeExpr` so annotations unify with the imported module env.
- [x] `P0` Implement type alias expansion in bootstrap checker: `alias_env: Dict types.Type` threaded through `typecheck_decls_inner` → `typecheck_decl` → `check_fn_body` → `scheme_from_fn_parts`; aliases collected inline as `AliasDecl` nodes are processed (in order, no pre-scan).
- [x] `P0` VarPattern cross-branch type leakage fixed: `infer_pattern` for `VarPattern` now records `name → expected` in the substitution so each match branch starts with the correct binding rather than leaking from a sibling branch.
- [x] `P0` Add a self-typecheck test: `bootstrap-check` on all `stdlib/compiler/*.sprout` modules passes (14/14 green).

### M2 — Type class lowering in Sprout

The Python pipeline runs `lower_typeclasses` (1619-line Python pass) before evaluation/codegen. Without a Sprout equivalent, the self-hosted pipeline can only handle class-free programs.

- [x] `P0` Implement `stdlib/compiler/lowering.sprout`: dictionary-passing lowering that transforms `class`/`instance` declarations and constrained function calls into explicit dictionary parameters. `lower_program : TypedProgram -> LowerResult` typechecks clean (bootstrap-check green).
- [x] `P0` Add parity tests: lowering output from `lowering.sprout` matches `sprout.typeclass_lowering` on a corpus of class-using files. `lower_driver.sprout` + `test_lowering_parity.py` cover concrete, polymorphic, and two-method cases; `__tc_*` instance-fn names agree on all 3 corpus files.

### M3 — Module bundling + name qualification in Sprout ✓ DONE

Python's `load_module_bundle` + `resolve_program_names` produces a flat, single-namespace source from multi-module programs. The bootstrap module loader only builds a type env — it doesn't produce a unified runnable AST.

- [x] `P0` Implement `stdlib/compiler/bundler.sprout`: given a file path and stdlib root, produce a bundled `ast.Program` with all imports inlined and names fully qualified.
- [x] `P0` Add parity tests: bundled FnDecl names match Python's bundled source on the conformance corpus. `bundle_driver.sprout` + `test_bundler_parity.py` cover simple, import, and list-ops cases; all 3 pass. Runtime fixes: Char ordering comparisons (`>=` etc.) now work in the interpreter; self-hosted parser extended to handle single-constructor types (no leading `|`) and class superclass constraints (`where` clauses); `ClassDecl` gains a `superclasses: List TypeConstraint` field (5-field, was 4); module name scanning fixed to read dotted identifiers (`stdlib.string` not `stdlib`).

### M4 — End-to-end Sprout pipeline (wire M1–M3) ✓ DONE

- [x] `P0` Add `compile_full` to `stdlib/compiler/compiler.sprout`: runs bundle → lex/parse → name-qualify → typecheck → lower in sequence, entirely in Sprout.
- [x] `P0` Route `sprout.cli bootstrap-check` through `compile_full` for at least one real program end-to-end.
- [x] `P0` `full_driver.sprout` batch driver runs compile_full over a list of files; `test_compile_full.py` verifies 5 corpus files (3 plain + 2 stdlib-import) all pass. Two `infer.sprout` bugs fixed: instance-resolution arg scanning now covers all args (not just first), and class method scheme registration now collects all method-specific type vars.

### M5 — Stage-0 bootstrap compile + self-check ✓ DONE

Note: "self-compilation" here means the native binary runs its own pipeline (bundle → typecheck → lower) over its own source and produces output that matches the Python-hosted run line-for-line. It does **not** mean the binary can produce a new native binary — LLVM IR emission is still Python-owned. That gap is M6.

- [x] `P0` `python -m sprout.cli compile -o compile_driver_bin --with-stdlib --native stdlib/compiler/compile_driver.sprout` produces a working native binary (uses existing LLVM codegen — Python-hosted, one-time). Runtime extended to support 8- and 9-field constructors (`ModuleSymbols`, `ResolveCtx`) required by the bundler module that is transitively compiled in.
- [x] `P0` Run that binary to self-check (`stage-1`): `compile_driver_bin` runs the Sprout pipeline over 5 corpus files and prints type-check output; `test_bootstrap_stage1.py` confirms output matches the Python-hosted `compile_driver.sprout` line-for-line.
- [x] `P0` Add CI step: stage-0 → stage-1 reproducibility check via `test_bootstrap_stage1.py`.
- [x] `P0` Fixed GC rooting bug in `TupleExpr` codegen: tuple items were evaluated without rooting intermediates; if item N+1 triggered GC (e.g. first `sprout_nothing` call allocating the Nothing singleton), item N's heap object was freed. Fix: root each tuple item as it is evaluated before proceeding to the next. Removed `SPROUT_GC_THRESHOLD=0` workaround; tests now run with GC enabled at default threshold.

### M6 — LLVM IR emission in Sprout

Python's LLVM IR emitter (~3 000 lines in `sprout/cli.py`) is the last Python-owned compiler pass. Until it is replaced, producing a native binary requires Python even though every upstream pass (bundle → typecheck → lower) is Sprout-owned. M6 closes the loop: `compile_driver_bin` must be able to emit `.ll` text and invoke `clang` without Python involvement.

- [x] `P0` Implement `stdlib/compiler/codegen.sprout`: walk the lowered `ast.Program` and emit LLVM IR text. Key concerns: value representation (tagged integers, heap-allocated ADT objects), GC root push/pop discipline around allocating calls, calling convention (curried functions as closures vs. direct calls), and string/bytes literal emission.
- [x] `P0` Add parity tests: IR emitted by `codegen.sprout` compiles and runs correctly on the existing conformance corpus (`factorial.spr` → `720`, `maybe_map.spr` → `Just(3)`). Correctness bugs fixed: constructor arity in `sprout_register_ctor`, constructor tag in registration, callee-type threading for closure-typed function parameters.
- [x] `P0` Wire codegen into `compile_driver.sprout`: `--emit-ir` flag runs the full pipeline (bundle → typecheck → lower → re-typecheck → codegen) and prints LLVM IR. `compiler.sprout` exports `compile_full_ir` and `IrFullResult`.
- [x] `P0` Fix pipeline correctness blockers for self-compilation: `parser.sprout` WHERE desugaring rewritten (broken `list_fold` → explicit recursion); `lowering.sprout` `expand_call_args` puts user args first and dict witnesses after; `checker.sprout` builtin env extended with Range/Vector/Map/NativeSet/Str entries; `codegen.sprout` `has_self_tail_calls` recognises `TDo` tail positions; `unifier.sprout` TVar–TVar unification keeps instantiation TVar canonical so `@fwd` markers survive tuple pattern destructuring; `infer.sprout` forward-marker infrastructure added for constrained recursive calls (seed_forward_markers, register_constrained_fn_markers, inject_constrained_fn_dicts, check_instance_for_marker).
- [x] `P0` Fix GC root pool exhaustion in `--emit-ir` for non-trivial stdlib modules. Root causes: Sprout-native codegen call emission rooted evaluated arguments without popping them, and full IR output assembled large line lists/strings through recursive append. Fix: pop temporary argument roots after emission and stream IR line sections from `compile_driver.sprout` instead of constructing one monolithic output string.
- [x] `P0` Verify stage-2 manually: use `compile_driver_bin` (stage-0, Python-produced) to emit LLVM IR for `compile_driver.sprout`, compile that IR with `clang` into a stage-1 native binary, and confirm stage-1 output matches the expected bootstrap corpus behavior. Correctness blocker fixed: constructor signatures now preserve primitive field LLVM types (`String`/`Char` as `ptr`, qualified primitive leaves included), preventing packed-pointer equality from reporting equal-looking type constructors as `Int vs Int` / `Maybe vs Maybe`.
- [x] `P0` Update `test_bootstrap_stage1.py` to run the stage-2 binary and verify end-to-end parity without Python involvement in the compile step. `BootstrapStage2Tests` added; `just build-stage1` recipe builds `compile_driver_bin_stage1` from Sprout-native `--emit-ir` output; `--emit-runtime-c` flag added to `sprout compile` to extract the C runtime for manual linking.

### M7 — Stage-3 fixed point (stage-1 self-compiles to stage-2) ✓ DONE

The next milestone after manual stage-2: have the stage-1 Sprout-native compiler (`compile_driver_bin_stage1`) emit LLVM IR for `compile_driver.sprout` itself, compile that to a stage-2 binary, and verify stage-2 reproduces the same output. This closes the bootstrap loop without Python in the compile path.

- [x] `P0` Fix stage-1 self-compile blockers in `stdlib/compiler/codegen.sprout`:
  - **TDoLetStep root push**: pure `let x = e` steps inside do-blocks now push a temp GC root for the bound value and propagate it via `tco_outer_roots`, mirroring what Python codegen already does (DoLetStep desugars to MatchExpr/VarPattern, which roots). Without this, allocating sub-expressions later in the do-block could free a still-live binding (notably in `compile_to_ir_lines`, which has 9 consecutive lets).
  - **TCO stacksave/stackrestore**: `emit_fn_tco` now emits `llvm.stacksave` at the entry block and `llvm.stackrestore` at every back-edge (top-level case + nested via `emit_tco_back_edge`, threaded through a new `sp_save` field on `TcoCtx`). Without this, dynamic GC-root allocas accumulated per loop iteration; `read_dotted_ident_chars` blew the 8 MiB stack on long qualified names. LLVM-O2 preserves the intrinsic on TCO functions whose loop bodies have escaping GC-root allocas (215 `mov sp, x{19..28}` patterns survive in `compile_driver_bin_stage1`); for `read_dotted_ident_chars` specifically the per-iteration sp delta is zero.
- [x] `P1` Add `scripts/memwatch.sh` developer tooling: multi-signal memory watchdog (RSS + system swap delta + free-memory delta + hard floors + optional wall-timeout) that wraps memory-risky bootstrap commands and SIGKILLs the descendant tree before the host swaps so heavily that other processes (e.g. the editor/IDE) get strangled.
- [x] `P0` End-to-end dynamic verification: `compile_driver_bin_stage1 --emit-ir` produces valid stage-2 IR for `compile_driver.sprout` without crashing or running out of memory. Stage-1 binary rebuilt (2026-05-10); stage-1 IR crash (`str_concat: null input` / segfault) fixed — four TVar-typed `++` patterns baked into stage-1's codegen lambdas caused null/corrupt pointer args to `str_concat` at codegen time; root cause: `emit_append_call` called `zero_val("ptr")` = null for the LHS when the expression type was `TVar` (inferred as generic in closures and CPS-compiled `where` bindings). Fix: added `join_args_str(a: String, b: String)` and `format_ll_ir(ll: String, ir: String)` named helpers with explicit String params; replaced inline `\p -> match p with | (ll, ir) -> ll ++ " " ++ ir` at `build_closure_args_ir`/`emit_regular_direct_call` and the 4-line if-chain in `emit_partial_direct_wrapper_lines`/`emit_partial_closure_wrapper_lines` to use these helpers. Also added `sprout_abort_match` to C runtime (forward decl + definition in `sprout/cli.py`) to satisfy linker for non-exhaustive match fallback emitted at line 1876. Stage-1 now correctly compiles `fn test(a: String, b: String) -> String = a ++ b` and all 106 tests pass. **Second crash fixed (2026-05-14):** SIGABRT from `sprout_tag: null pointer` in `collect_free_vars_branches` → `list_fold_go(sprout_tag(result_of_pattern_bound_names))` because stage-1's baked-in codegen lambdas for `pattern_bound_names`'s `++ pattern_bound_names(p)` calls returned 0 (same TVar/class-dispatch root cause). Three-part fix: (1) `strip_module_prefix` in `emit_named_call` to handle qualified `stdlib.prelude.append`; (2) `|| is_list_type(ty)` fallback in `emit_append_call` for TVar-typed operands; (3) `pbn_acc` named helper that calls `list_append` (a builtin in `extern_sigs`) directly, bypassing class dispatch entirely, replacing the anonymous `\ (acc, p) -> acc ++ pattern_bound_names(p)` lambdas. Also normalized `resolve_tdict` and `build_hidden_for_constraint_with_supers` in `lowering.sprout` to use `strip_module_prefix(eff_class)` as the key, so qualified class names from the bundler match unqualified keys in `inst_table`. Stage-1 rebuilt 2026-05-14: zero `ret i64 0` in new IR; `just build-stage2` succeeds (640 MB / 172s); all 106 tests pass.
- [x] `P0` Fix generic constructor field type erasure in Sprout-native codegen (2026-05-09): `CtorSig` stored pre-erased LLVM types computed from the generic `TypeExpr`s at declaration time — type variable fields (e.g. `a` in `Cons a (List a)`) were mapped to `i64` regardless of instantiation. At a pattern-match site like `| Cons line rest ->` on `List String`, `emit_ctor_args_bind` used these generic lls, so `line` was bound as `i64` instead of `ptr`, causing `emit_print_call` to dispatch `print_value` (prints raw integer address) instead of `print_str`. Fix: `CtorSig` now carries the original `(List ast.TypeExpr)` and the ADT's `(List String)` type-parameter names; `build_ctor_sigs_acc` captures type params from `TypeDecl`; `emit_ctor_pattern_bind` receives the scrutinee's `Maybe types.Type`, builds a type-variable substitution via `extract_type_args` + `build_tvar_subst`, and calls `instantiated_ctor_arg_lls` to get field lls with type variables resolved to concrete LLVM types (e.g. `a=String` → `ptr`). The `emit_match` → `emit_branches` call chain now threads `scrut_type: types.Type` to `emit_pattern_bind`. Sub-patterns (nested constructors, tuple fields) pass `Nothing` and fall back to the pre-erased lls (correct for ADT-typed fields which are always `i64`). All 106 tests pass.
- [x] `P0` Investigate the stage-1-vs-stage-0 memory ratio. **Phase 4 measurement results (2026-05-02):** stage-0 binary rebuilt from Phase 4D source; peak RSS measured via `/usr/bin/time -l` under `scripts/memwatch.sh 6144`:
  | Workload | Pre-Phase-4 RSS | Phase 4D RSS | Δ (MB) | Δ (%) |
  |---|---|---|---|---|
  | typecheck factorial.spr | 6.2 MB | 6.6 MB | +0.4 MB | +6% |
  | typecheck codegen.sprout | 58.0 MB | 57.6 MB | −0.4 MB | −1% |
  | `--emit-ir compile_driver.sprout` | 1268 MB | 1006 MB | **−262 MB** | **−21%** |
  Phase 4 phases (A–D) delivered a 262 MB / 21% reduction on the full self-compile workload. Small-file workloads are unchanged (within noise). The gap to the stage-0 target of ~70 MB for `codegen.sprout` is unchanged. Remaining sources: per-let GC-root push overhead from the M7 TDoLetStep fix (could batch consecutive lets into a single push), Sprout codegen's lack of stack-slot promotion vs. Python codegen's, and possible inefficiencies in the AST/typed-AST representation that surface at scale. **Phase 5 fixes (2026-05-07):** two stage-1-rebuild blockers resolved: (1) `string_concat_many` was added to `checker.sprout`'s `builtin_entries()` so `emit_template` can be type-checked without unknown-variable abort; (2) `_unann` shared type-variable bug in `infer.sprout` — `collect_param_type_vars` and `param_types_from_decl` previously assigned ALL unannotated parameters the fixed name `"_unann"`, so when transitive-superclass lowering produced wrappers with 2+ hidden params, they all shared one type variable and caused cross-function type contamination (manifesting as `emit_str_ptr` returning wrong type). Fixed by using `"_unann_" ++ param_name` to make each unannotated param's type variable unique. (3) TuplePattern do-bind typing bug in `infer.sprout` (2026-05-08): `extend_env_from_pattern_typed` for `TuplePattern` fell through to `extend_env_from_pattern`, which bound each pattern variable via `TVar(name)` — using the programmer's variable name as a substitution probe-key. Since the unifier only stores fresh vars like `t123` in the substitution, variables like `gname` always resolved to `TVar("gname")` rather than the concrete element type, making typeclass-dispatch witness resolution fail (no concrete type arg → no `TDict` emitted → check2 sees a partially-applied function type instead of `String` in `emit_str_ptr`, producing "String vs t10306 → String"). Fixed by adding a `TuplePattern` arm to `extend_env_from_pattern_typed` that, when the expected type is `TTuple elem_types`, recursively zips pattern elements against concrete element types via new helper `extend_env_from_typed_tuple_pats`. Stage-1 rebuilt 2026-05-08: IR emission 749 MB / 51s (stage-0 compiling fixed sources); `compile_driver_bin_stage1` 1.52 MB produced; all 106 tests pass. **Phase 6 closure measurement (2026-05-17):** stage-0 and stage-1 binaries (both built from today's sources) re-measured via `/usr/bin/time -l` under `scripts/memwatch.sh 2048`: stage-0 typecheck `codegen.sprout` 63.4 MB / stage-1 57.3 MB (stage-1 **10% leaner**); stage-0 `--emit-ir compile_driver.sprout` 254 MB / 14.8 s / stage-1 251 MB / 14.7 s (**~1:1 parity**); IR output byte-identical (8 912 435 bytes). The gap is closed. Root cause of the 1006 MB → 254 MB drop (vs Phase 4D): the phase-isolation refactor (2026-05-10) split the monolithic `compile_full_ir_lines` do-block into four chained exported functions so the GC could reclaim earlier pipeline stages before codegen began; both stage-0 and stage-1 run the same `compiler.sprout` code and thus benefit equally. Investigation complete — no further memory work required.
- [x] `P0` Checker: remove `BodyLenient` silent error swallowing (2026-05-04). `fn_body_err` and `instance_method_err` previously short-circuited "Unknown variable" / "Unknown constructor" errors into `BodyLenient` / `InstanceMethodOk(dummy_unit)`, which `typecheck_decl` then substituted as a `dummy_unit()` body and continued. Combined with the lowering's `__unresolved_*` placeholder fallback, this masked real typeclass-dispatch bugs and produced misleading downstream errors (e.g. "Type mismatch: Unit vs String" five layers from the actual `__unresolved_Semigroup_a in mconcat`). Both helpers now produce hard errors; the `BodyLenient` ADT constructor is retained but no longer produced (defensive `TypedDeclErr` if encountered).
- [x] `P0` Lowering: propagate transitive superclass dictionaries. `stdlib/compiler/lowering.sprout` previously only seeded direct-constraint entries in `ctx_fwd`/`ctx_inst`, so `mconcat`'s body emitted `__unresolved_Semigroup_a` when `++` (Semigroup) was dispatched under a `Monoid a` constraint. `LowerEnv`/`LowerCtx` now carry a `super_map` (class → direct supers) collected from `ast.ClassDecl`'s 4th field; `build_hidden_for_constraints` walks `class_with_transitive_supers` for each direct constraint and appends hidden params + fwd_map entries for every transitive super at the same idx; `generate_class_wrappers` mirrors the param layout; and `resolve_tdict` expands a single TDict witness into the concatenated method-vars for `[Class, super1, super2, ...]` so caller and callee param lists line up. This matches `seed_superclass_fwd_markers` semantics in `infer.sprout` so the typechecker and lowering agree on which dictionaries are in scope.
- [x] `P0` Checker: register all missing builtins in `builtin_entries()` (2026-05-07). `BodyLenient` removal revealed that `stdlib/compiler/checker.sprout`'s `builtin_entries()` was missing many functions that the Python typechecker registers — causing `module_loader`'s `checker.check_program_with_env` to silently return `Nil` for stdlib modules that use them. Added 9 new sections: `prelude_ext_entries()` (dict_entries, vec_map, fold, map, split_ints, pipe, list_length, and 12 other prelude helpers not auto-included by module_loader), `io_extra_entries()` (print_int, read_lines, read_int_lines, env_get), `json_entries()` (json_parse, json_stringify), `regex_ext_entries()`, `bytes_ext_entries()`, `crypto_ext_entries()`, `tcp_ext_entries()`, `http_ext_entries()`, `term_ext_entries()`. All 26 `test_checker_parity` tests now pass (26/26 green).
- [x] `P0` Add `just build-stage2` recipe symmetric to `just build-stage1` (2026-05-08).
- [x] `P0` Fix `str_concat` GC tracking leak (2026-05-08): runtime `str_concat`, `string_concat_many`, `int_to_string`, `str_slice`, `read_file`, `tcp_read`, `regex_replace_all_literal`, `regex_escape`, `json_stringify`, and `crypto_base64_encode` all `malloc`'d without registering with the GC (`register_managed_ptr`), so allocated strings were never swept. Added `SPROUT_HEAP_CSTR = 10` heap kind; each builtin now calls `sprout_gc_maybe_collect_threshold()` before allocation and `register_managed_ptr(out, SPROUT_HEAP_CSTR, 0)` after. GC `free_payload`/`child_count`/`child_value` dispatch extended for `SPROUT_HEAP_CSTR`.
- [x] `P0` Add `BootstrapStage3Tests` (2026-05-17): `tests/test_bootstrap_stage1.py` now includes `BootstrapStage3Tests` (M7) — uses `compile_driver_bin_stage2` to typecheck-the corpus batch and confirms output matches the Python reference (5 corpus files, 5/5 pass). Implemented as behavioral parity rather than IR byte-equality; stage-2 consecutive `--emit-ir` runs are already byte-identical (verified 2026-05-15). `just build-stage3` recipe and `compile_driver_bin_stage3` also exist; stage1/2/3 binaries are the same size (1692072 bytes) confirming no IR regressions across rounds.
- [x] `P0` Fix `emit_binary` string-comparison type detection for where-binding lambda parameters (2026-05-15): `emit_binary` in `codegen.sprout` checked only the LEFT operand's type to decide whether to route `==`/`!=` to `str_eq` vs `icmp eq i64`. For where-binding lambda bodies (desugared to `(λc. body)(arg)`), the parameter `c` carries an unresolved unification variable `TVar "t123"` in the typed AST — because `check_fn_body` returns `typed_body` without applying the final substitution `s1`. So `is_string_type(typed_expr_type(c))` returned False even though `c: String`, causing ALL comparisons in `is_lowercase_name` (which checks `c == "a" || ... || c == "z"`) to emit `icmp eq i64` instead of `str_eq`. Result: `is_lowercase_name("a")` returned 0 at runtime, so the type checker treated single-letter type parameters like `a`, `b` as `TConst "a"` instead of `TVar "a"`, breaking polymorphic function type-checking for any function with type-variable parameters (e.g. `fn list_map_go(f: a -> b, xs: List a) -> List b`). Fix: also check RIGHT operand type — `c == "a"` has right=`TString("a", TConst "String", ...)` so `is_string_type(right_ty)` = True, routing to `str_eq`. Stage-1 rebuilt from Python compiler (bypassing the broken stage-0 native that predated the fix); `just build-stage2` now succeeds (170s / 585MB); stage-2 fixed-point verified (two consecutive `--emit-ir` runs produce byte-identical output).
- [x] `P1` Generalize CI pipeline-boundary invariant checks (2026-05-10): three improvements extracted from the stage-1 crash investigation: (1) `tests/ir_health.py` module with four composable checks — `assert_structural_ir`, `assert_no_str_concat_null`, `assert_no_undeclared_calls` (catches missing C-runtime entries like `sprout_abort_match`), `assert_valid_ir` (composite + llvm-as); `test_stage1_emit_ir.py` now delegates to this module. (2) `Stage0ExecutionTests` class in `test_stage1_emit_ir.py`: emits IR via `compile_driver_bin --emit-ir`, links with clang, runs, and asserts output matches hardcoded expected value — closes the loop from structural-only to semantic IR verification; also exposed two pre-existing native codegen bugs: bundler omits prelude for module-declaration-free programs (fixed in shapes by adding `module main`), and closure wrappers for named functions with tuple params use `{ ptr, ptr } %a0` instead of `i64 %a0` (`tuple_fn_as_value` xfail, see `_KNOWN_CC_BUG_SHAPES`). (3) `codegen.sprout` lambda naming now embeds scope: `__sprout_lambda_N_<fn_name>` (e.g. `__sprout_lambda_1285_build_closure_args_ir`) so LLDB backtraces, profiler output, and linker errors are self-documenting without IR comments.
- [ ] `P1` Fix closure wrapper calling convention for named functions with tuple parameters: `emit_named_fn_wrapper_lines` generates `(ptr %env, { ptr, ptr } %a0)` but `list_map_go` (and all generic higher-order functions) pass the element as `i64`. Fix: detect tuple param types in `build_wrapper_params` and emit an `inttoptr i64 %a0 to ptr` + `load { ptr, ptr }, ptr %a0_ptr` conversion before calling the named function. Tracked in `Stage0ExecutionTests::test_tuple_fn_as_value` as `xfail` (`_KNOWN_CC_BUG_SHAPES`).
- [x] `P0` Phase isolation in `compiler.sprout` to fix stage-2 OOM (2026-05-10): root cause of ~20 GB stage-2 self-compile OOM identified as lexical-scope rooting — Sprout's `emit_do` keeps all do-block bound variables on the GC root stack for the entire do-block scope; the monolithic `compile_full_ir_lines` do-block kept all 7 pipeline variables (`prog`, `typed_prog`, `lowered`, `tc2_result`, `typed_lowered`, `env`, `ir_result`) simultaneously live during codegen, making the GC unable to free any of them. Fix: replaced with four chained exported functions `compile_phase_bundle → compile_phase_check → compile_phase_lower → compile_phase_recheck`; each function's return discards its frame's GC roots so only the previous phase's output survives into the next. `compile_full_ir_lines` now calls `compile_phase_recheck` and then runs codegen; at codegen time only `typed_lowered + env` (2 roots instead of 7) are live. Two OOM failure modes eliminated: (1) adaptive-threshold unbounded growth to ~20 GB, (2) fixed-threshold GC thrash (81% runtime, 137K consecutive cycles sweeping ≤1 object).
- [x] `P1` GC livelock detection in `sprout/cli.py` (2026-05-10): three new env vars — `SPROUT_GC_LIVELOCK_RATIO` (default 0.05 = 5% sweep efficiency threshold), `SPROUT_GC_LIVELOCK_CYCLES` (default 1000 consecutive sub-threshold cycles before triggering), `SPROUT_GC_LIVELOCK_ACTION` (0=off, 1=warn, 2=abort); emits `[sprout gc] livelock: N consecutive cycles sweeping X% < Y%` to stderr; action=2 calls `abort()`. Would have triggered at cycle ~1000 (~0.1 s into the 137K-cycle thrash) in the stage-2 OOM experiment. Parsed via `sprout_gc_livelock_maybe_enable()` called from `sprout_set_argv`.
- [x] `P1` Phase-isolated pipeline CLI in `compile_driver.sprout` (2026-05-10): new `--phase bundle|check|lower|recheck|ir` flag with per-phase output dump; `--emit-ir` remains as alias for `--phase ir`; four new run functions print `OK` then per-phase artifact summary (`dump_ast_decl_names` for bundle/lower, `checker.dump_typed_program_names` for check/recheck); `checker.sprout` exports `dump_typed_program_names`/`dump_typed_decl_names` helpers. This enables incremental debugging of the pipeline without running full IR emission.
- [x] `P0` Bootstrap bundle identity test (2026-05-11): `tests/test_bootstrap_identity.py` runs `--phase bundle` on a corpus of stdlib modules (token.sprout, ast.sprout, prelude.sprout) against both stage-0 and stage-1 binaries and diffs the output. Stage-0 smoke tests also run without stage-1. All corpus files now pass; `XFAIL_FILES` is empty after the `Char` comparison codegen fix (2026-05-12).
- [x] `P0` Fix `Char` ordering comparisons in Sprout-native codegen (2026-05-12): `emit_comparison` in `codegen.sprout` used `str_eq` for ALL comparison operators on pointer-typed values (including `>=`, `<=`, `>`, `<`), so `ch >= 'a' && ch <= 'z'` compiled to `ch != 'a' && ch != 'z'` — correct only at boundaries, wrong for all interior chars and non-alpha chars like `(`. Fixed by extracting `emit_ptr_comparison` that uses `str_eq`/`xor` for `==`/`!=` and `str_compare` + `icmp sge/sle/sgt/slt` for ordering operators. Root cause of the stage-1 `--phase bundle` regression where `read_ident_chars` consumed entire source files as ident tokens.
- [x] `P0` `--phase scan-info` diagnostic command (2026-05-11): `compile_driver.sprout` exposes `--phase scan-info <stdlib-root> <file>` that calls `bundler.scan_source_info(src)` and prints `module:`, `export:`, and `ctor:` lines. Diagnoses scan_source_info bugs without a full bundler run.
- [x] `P1` GitHub Actions CI pipeline (2026-05-11): `.github/workflows/ci.yml` runs `python3 scripts/run_parallel_tests.py`, then builds stage-0 (`sprout compile --with-stdlib --native`), then builds stage-1 (`just build-stage1`). Ensures `just build-stage1` runs automatically on every push/PR.
- [x] `P1` Post-qualification assertion in `bundler.qualify_decl` (2026-05-11): `validate_qualified_decls` checks that no qualified name starts with `.` (empty module prefix + dot is always a `qualify_decl`/`scan_source_info` bug); error surfaced as `BundleErr("[assert] qualify_decl: ...")` via `make_bundle_or_err` helper. Would have caught the stage-1 regression immediately.
- [x] `P2` `--phase dump-qualify` diagnostic command (2026-05-11): `compile_driver.sprout` exposes `--phase dump-qualify <stdlib-root> <file>` that runs the full bundle pipeline and prints original→qualified name mapping per module plus `ctx: EMPTY` / `ctx: populated` status. Shows whether `build_resolve_ctx` found the module in `all_symbols`. Implemented in `bundler.dump_qualify_file`.
- [x] `P2` GC safety linter `scripts/gc_safety_check.py` (2026-05-11): scans the embedded C runtime in `sprout/cli.py` for functions where `sprout_gc_maybe_collect_threshold()` is called while `const char*`/`char*` parameters or locals are live but unregistered. Currently finds 9 pre-existing patterns (str_concat, string_concat_many, string_join_newlines, str_slice, regex_replace_all_literal, regex_escape) where callers are expected to root their inputs. Exits 0 (WARN) by default; `--strict` exits 1. Run via `just gc-safety-check`.
- [x] `P0` Crash-attribution signal handler in C runtime (2026-05-12): `g_sprout_current_fn` global + `sprout_set_current_fn(fn_name)` C function track which function IR is currently being emitted; `sprout_crash_handler` registered for SIGSEGV/SIGABRT prints `[sprout] SIGSEGV while emitting IR for: <fn>` before re-raising; `sprout_tag` null-guard prints `[sprout] sprout_tag: null pointer (in: <fn>)` then `abort()` instead of silently dereferencing null. Converts stage-2 SIGSEGV crashes into self-documenting diagnostics. `sprout_set_current_fn` registered in `extern_sigs_list()` in `codegen.sprout` so emitted IR can declare it.
- [x] `P0` `--emit-ir-one <fn_name>` compile_driver flag (2026-05-12): emits LLVM IR for a single function (substring match on qualified name) instead of the full program. `compile_to_ir_lines_one_fn` added to `codegen.sprout`; `compile_one_fn_ir_lines` added to `compiler.sprout`; `run_file_ir_one` + `--emit-ir-one` branch added to `compile_driver.sprout`. Enables binary-search debugging: test individual functions for null-pointer crashes without running the full ~1000-function codegen.
- [x] `P1` ASan/UBSan bootstrap build recipes (2026-05-12): `just build-stage1-asan` and `just build-stage2-asan` link with `-fsanitize=address,undefined` at `-O1`. Converts silent SIGSEGV → structured source-level error with function name, file, and line. Use only for debugging; ~5× slower than release.
- [x] `P1` Python `_validate_typed_program` AST guard (2026-05-12): `_validate_typed_program_expr` walks the lowered AST after `typecheck_program(lowered)` in `cmd_run`/`cmd_compile` and raises `RuntimeError` if any list-typed field (`LambdaExpr.params`, `CallExpr.args`, `DoExpr.steps`, `MatchExpr.branches`, `TupleExpr.items`) is `None`. Catches null-field bugs at Python level with a clear error before they reach the native codegen and produce cryptic SIGSEGV crashes in `collect_free_vars`.
- [x] `P1` Codegen regression corpus for free-var collection (2026-05-12): 4 new conformance files (`codegen_lambda_params.spr`, `codegen_do_bind.spr`, `codegen_nested_lambda.spr`, `codegen_match_patterns.spr`) cover TLambda param enumeration, TDo desugared match chains, nested lambda free-var capture, and multi-arm TMatch on recursive ADTs respectively. Also added to `test_stage1_emit_ir.py` `SHAPES`/`SHAPE_OUTPUTS` so they run through `Stage0EmitIrTests`, `Stage1EmitIrTests`, and `Stage0ExecutionTests` once binaries are present.
- [x] `P1` Tuple where-binding desugaring in self-hosted parser (2026-05-15): `starts_where_binding` in `parser.sprout` only matched `ident = expr` (required leading IDENT token), so `where (a, b) = expr` caused a parse error — the `where` keyword was consumed but the tuple pattern left in the token stream. `wrap_where_binding` silently dropped non-VarPattern bindings (`| _ -> inner`). Fix: (1) `starts_where_binding` also returns True for `(` token; (2) `wrap_where_binding` gains a `TuplePattern` arm that desugars to `(λ__where_tup. match __where_tup with | (a, b) -> body)(expr)`, matching Python parser semantics. Unblocks `examples/aoc_2025_day_3`, `aoc_2025_day_4`, and `http_server.sprout`.
- [x] `P1` Multi-byte UTF-8 string literal sizing in Sprout-native codegen (2026-05-15): `string_const` in `codegen.sprout` used `str_len(s) + 1` (codepoint count + null) for the LLVM `[N x i8]` array declaration. For multi-byte UTF-8 characters (e.g. `ą` = 0xC4 0x85, 2 bytes), the encoded content has more bytes than `str_len` reports, causing LLVM to reject the IR with "constant expression type mismatch: got type `[3 x i8]` but expected `[2 x i8]`". Fix: use `str_byte_len(s) + 1` (byte count + null). Unblocks `examples/text_demo.sprout`. Stage-2 examples pass: 22/33 (was 19/33); 10 remaining failures are type-checker gaps, missing builtins, or typeclass dispatch issues — not parser/codegen bugs.
- [x] `P0` Fix `check_fn_body` to apply final type substitution to typed AST (2026-05-15): `infer.sprout` `check_fn_body` returned `typed_body` from `InferOk typed_body s1 _ _` without applying `s1`, leaving all lambda-parameter types as unresolved `TVar "tN"` nodes in the typed AST. The symptom was `is_lowercase_name` always returning 0 in stage-2 (the `TVar`-typed lambda param `c` in `\c -> c == "a" || ...` routed to `icmp eq i64` instead of `str_eq`). Principled fix: `apply_subst_typed_expr(subst, expr)` walks all 19 `TypedExpr` constructors plus `TypedMatchBranch`, `TypedDoStep`, and `TypedRecordField`, replacing each type field via `unifier.apply_subst`; wired into `check_fn_body` as `BodyOk(apply_subst_typed_expr(s1, typed_body))`. Also added debug helper `assert_resolved_typed_expr` / `has_unresolved_tvar_in_type` for future post-inference audits.
- [x] `P1` Extract `compare_needs_ptr_dispatch` helper in codegen (2026-05-15): the 4-way type check (`is_string_type(left_ty) || is_char_type(left_ty) || is_string_type(right_ty) || is_char_type(right_ty)`) that routes binary comparisons to `emit_ptr_comparison` vs `emit_comparison` was inline in `emit_binary`. Extracted to a named predicate `compare_needs_ptr_dispatch(left_ty, right_ty) -> Bool` placed immediately before `emit_binary`, making the routing decision explicit and self-documenting.
- [x] `P1` Document GC Option C ABI in AGENTS.md and codegen.sprout header (2026-05-15): the `String`/`Char` = `i64` representation is non-idiomatic LLVM and was silently reverted by an AI subagent during a refactor. Added a "GC Option C ABI" subsection in `AGENTS.md` explaining the WHY (root table stores `i64`; `ptr` would require ptrtoint/inttoptr at every GC-safe point), the invariants (use `ll_i64()` for String/Char in `const_to_ll`; coerce back to `ptr` for `str_eq`/`str_compare` calls; `str_byte_len` not `str_len` for LLVM array sizes), and an explicit "DO NOT change to `ll_ptr()` — that is a regression." Mirrored as an expanded comment in the `codegen.sprout` design notes block.
- [x] `P1` Bootstrap freshness warning in `just build-stage1` (2026-05-15): added a `find stdlib/compiler -name "*.sprout" -newer compile_driver_bin` check at the top of `build-stage1` (and `build-stage1-asan`) that prints a two-line WARNING to stderr if any compiler source file is newer than the stage-0 binary. Surfaces the "I edited .sprout but the fix didn't take effect" failure mode immediately without aborting the build.
- [x] `P1` Parser parity conformance corpus additions (2026-05-15): `where_tuple_binding.spr` (regression for tuple where-binding desugaring) and `nonascii_string.spr` (regression for string-literal round-tripping) added to `tests/conformance/run/` and registered in `test_parser_parity.py` CORPUS. Known divergence registered: Python parser uses `__sprout_where_0` and Sprout parser uses `__where_tup` as the internal tuple-where parameter name — functionally identical.
- [x] `P1` `Stage2EmitIrTests` and `Stage2SelfCompileTests` CI test classes (2026-05-15): `tests/test_stage2_emit_ir.py` added with two classes that skip gracefully when `compile_driver_bin_stage2` is absent (`just build-stage2` to enable). `Stage2EmitIrTests` runs 3 shape snippets (noparam, strconcat, adtmatch) through `--emit-ir` and asserts IR health via `ir_health.assert_valid_ir`. `Stage2SelfCompileTests.test_self_compile` runs stage-2 on `compile_driver.sprout` itself and asserts zero `ERROR:` lines — a lightweight fixed-point smoke gate.
- [x] `P1` Flip `compile` and `compile-native` justfile recipes to use `compile_driver_bin_stage1` (2026-05-17, Phase 10): `just compile file out` now invokes `./compile_driver_bin_stage1 --emit-ir $(pwd)/stdlib {{file}} > {{out}}` instead of `python3 -m sprout.cli compile`. `just compile-native file out` emits IR via stage-1 then uses Python only for `--emit-runtime-c` (C runtime extraction) before linking with clang. `just compile-examples` alias flipped from `compile-examples-stage0` to `compile-examples-stage1`. Python is no longer in the IR-emission critical path for user programs. Remaining Python compile dependency: `--emit-runtime-c` (static C runtime string embedded in `sprout/cli.py` — candidate for extraction to `runtime/sprout_runtime.c`).

---

## Current Snapshot

- [x] Modules with explicit exports (`export`) are implemented.
- [x] HTTP response helpers exist in `stdlib/http.sprout`.
- [x] JSON types and helpers exist in `stdlib/json.sprout`.
- [x] `stdlib.net` defines typed TCP client result/error helpers.
- [x] `stdlib.net` wraps TCP connections/listeners in distinct handle types for user-facing APIs.
- [x] `stdlib.bytes` provides raw byte slicing plus big-endian integer helpers.
- [x] `stdlib.bytes` now includes UTF-8 encode/decode plus null-terminated string helpers.
- [x] `stdlib.bytes` now includes an efficient builder API for protocol packet assembly.
- [x] `stdlib.crypto` provides SHA-256, HMAC-SHA-256, base64, XOR, and entropy helpers for authenticated clients.
- [x] Swappable TCP server model exists (`reactor`, `blocking`) for server-side runtime.
- [x] `http_request` builtin and typed HTTP result ADTs are implemented in interpreter and native modes.
- [x] `stdlib.json` owns JSON types/helpers, and `json_parse` builtin plus basic JSON accessors are implemented there.
- [x] `stdlib.collections` now uses runtime-backed `Vector` for `Vec` indexing helpers.
- [x] `stdlib.collections` now uses runtime-backed `Map` for `Dict` helpers.
- [x] Runtime builtin failures now use a shared `runtime error: builtin ...` convention in interpreter and native paths.
- [x] CLI REPL exists with declarations, expression evaluation, and `:type`.
- [x] Prelude now includes `when_ok` / `when_error` effect taps for `Result`.
- [x] Prelude now includes `pipe` plus `result_pipe*` helpers for lighter `Result` pipelines.
- [x] CLI formatter/linter baseline exists (`fmt`, `fmt --check`, `lint`).
- [ ] terminal interaction primitives are not yet fully implemented.
- [x] Module exports now support opaque exported types via `export type Name` and constructor-exporting ADTs via `export type Name(..)`.
- [x] `env_get(name) -> Maybe String` is available in interpreter and native modes.
- [x] Integration-style IO tests now have a dedicated harness (`tests/integration_support.py`) and focused suite (`tests/test_integration_io.py`).
- [x] The builtin surface is now explicitly audited in the docs as `IO`-annotated, pure, or runtime-bound-but-non-`IO` in v0.
- [x] There is now an explicit design plan for promoting a minimal real effect system into v0: [docs/effect-system-v0-plan.md](./docs/effect-system-v0-plan.md).
- [x] Bootstrap self-hosting compiler modules exist in `stdlib/compiler/`: `source`, `token`, `lexer` (Python tokenizer parity), `ast`, `parser`, `types`, `unifier`, `infer` (HM constraint generation/solving).
- [x] Bootstrap parser parity harness: `driver.sprout` dumps AST as flat s-exprs; `tools/dump_ast.py` does the same via Python parser; `tests/test_parser_parity.py` confirms 7/7 corpus files match (one known `++`-desugaring divergence documented).
- [x] Bootstrap checker integration seam: `checker.sprout` + `type_driver.sprout` + `tools/dump_types.py` + `tests/test_checker_parity.py`; 6/6 corpus files match, no known divergences.
- [x] Phase 2 compiler driver: `compiler.sprout` (API) + `compile_driver.sprout` (executable) + `sprout.cli bootstrap-check` subcommand; at least one CLI check path now runs through Sprout-owned control flow end-to-end.
- [x] FnDecl body inference: `check_fn_body` in `infer.sprout` checks bodies against annotation schemes; unknown-ref leniency for builtins; real type mismatches propagate.
- [x] Builtin env seeded: ~25 common functions/constructors pre-populated in `checker.check_program` initial env so body inference resolves them without leniency.
- [x] ClassDecl/InstanceDecl: class methods registered globally as polymorphic schemes; instance method bodies checked against method annotations; checker corpus now 6/6.

## Next Steps

- Note: pure unifier (state-threading) was considered and decided against — keeping `Ref`-based mutable state in `InferState` for performance reasons.
