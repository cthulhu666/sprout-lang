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
- [ ] `P2` Add set type and common ops.

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
  - `tests/test_parser_parity.py` runs both on the conformance corpus and diffs output; 7/7 pass (one known divergence: `++` desugars to `append` in Python, `list_append` in Sprout)
  - **integration seam landed**: `stdlib/compiler/checker.sprout` wraps `infer.typecheck_decls` behind a `CheckResult` ADT; `stdlib/compiler/type_driver.sprout` is an executable that lex→parse→check→dumps typed names; `tools/dump_types.py` does the same via the Python typechecker; `tests/test_checker_parity.py` confirms 5/5 corpus files match (no known divergences — forall generalization fully implemented)
  - **Phase 2 driver landed**: `stdlib/compiler/compiler.sprout` exposes `compile_source`/`compile_file` API; `stdlib/compiler/compile_driver.sprout` is an end-to-end executable; `sprout.cli bootstrap-check` routes at least one real CLI check path through Sprout-owned control flow

### 7.5) Type Classes (Collections First)

- [x] `P0` Add class declarations and constrained function signatures (`class`, `where` constraints).
- [~] `P0` Add instance declarations and resolution (`instance` lookup, coherence checks).
- [~] `P0` Implement dictionary-passing lowering in typechecker/backend (hidden-method-parameter lowering supports constrained polymorphic helpers via forwarding and now monomorphizes concrete call sites to specialized wrappers; true first-class dictionaries for polymorphic class methods are blocked on higher-rank method-field representation).
- [x] `P0` Add `Functor` class and instances for `List` and `Vec`.
- [x] `P0` Add `Foldable` class and instances for `List` and `Vec`.
- [x] `P1` Add `Semigroup` class with associativity law documented.
- [ ] `P1` Add `Monoid` class with identity law documented.
- [ ] `P1` Add pragmatic utility classes (`Eq`, `Ord`, `Show`) for collection-focused workflows.
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
- [x] Bootstrap checker integration seam: `checker.sprout` + `type_driver.sprout` + `tools/dump_types.py` + `tests/test_checker_parity.py`; 5/5 corpus files match, no known divergences.
- [x] Phase 2 compiler driver: `compiler.sprout` (API) + `compile_driver.sprout` (executable) + `sprout.cli bootstrap-check` subcommand; at least one CLI check path now runs through Sprout-owned control flow end-to-end.

## Next 3 Tasks (Execution Order)

1. Make `unifier` pure (state-threading): eliminate `!{IO}` from `compile_source`/`check_program` by threading the fresh-variable counter as a pure value; this lets the bootstrap checker work in pure contexts (REPL, library use without IO).
2. FnDecl body inference: implement expression-level type inference for function bodies in the bootstrap checker; currently annotation-only — inferring bodies would expand parity corpus coverage and close the gap with the Python typechecker.
3. Implement `ClassDecl`/`InstanceDecl` in the bootstrap checker: currently no-ops; adding constructor registration and instance method lookup would allow type-class-heavy corpus files to enter checker parity.
