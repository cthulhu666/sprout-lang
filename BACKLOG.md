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
- [x] `P2` Extend collections helpers (`vec_slice`, `vec_reverse`, `dict_keys`, `dict_values`).
- [~] `P2` Add vector utility combinators (for example `vec_sum_by`; `vec_max_subsequence_by_count` is now a maybe/later item).
- [x] `P2` Add set type and common ops.

### 6) Modules and Packaging

- [x] `P0` Implement real module namespaces (remove flattened global import model).
- [x] `P1` Move global string helpers into namespaced stdlib module(s).
- [ ] `P1` Define package/dependency conventions for third-party modules.

### 7) Tooling and Developer UX

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
- [ ] `P0` Verify stage-2: use `compile_driver_bin` (stage-0, Python-produced) to compile `compile_driver.sprout` to a new native binary (stage-1, Sprout-IR-produced) and confirm stage-1 output matches stage-0 on the conformance corpus.
- [ ] `P0` Update `test_bootstrap_stage1.py` to run the stage-2 binary and verify end-to-end parity without Python involvement in the compile step.

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
