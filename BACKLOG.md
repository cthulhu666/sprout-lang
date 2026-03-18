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
- [ ] `P2` Add effect-sequencing sugar for `IO Unit` flows (`do` blocks or a dedicated sequencing operator).

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
- [ ] `P1` Add efficient byte-buffer/builder utilities so protocol parsers do not depend on repeated full-copy concatenation.

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
  - list issues
  - issue details
  - pagination cursor extraction
- [ ] `P1` Add auth/token helpers and secure configuration loading.
- [ ] `P1` Add integration tests with mocked HTTP responses.

### 8.5) Runtime and FFI Foundations for Database Clients

- [x] `P0` Define a safer representation for external resource handles (currently `stdlib.net` wrapper ADTs; true opacity still depends on hidden constructors).
- [x] `P1` Add environment/config helpers such as `env_get(name) -> Maybe String`.
- [ ] `P1` Define test support for integration-style IO programs that depend on external services.

### 9) Issue Browser TUI App

- [~] `P0` Build `examples/sentry_issue_browser_tui.sprout`:
  - load token/org/project config
  - fetch first page
  - render list
  - key navigation (`j/k`, enter)
  - refresh/retry
- [ ] `P1` Add issue detail panel.
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
- [ ] byte builders and fully opaque external-resource handles are not yet implemented.
- [x] `env_get(name) -> Maybe String` is available in interpreter and native modes.

## Next 3 Tasks (Execution Order)

1. Add byte-buffer/builder utilities so protocol parsers do not depend on repeated full-copy concatenation.
2. Add fully opaque constructor/private-export support so runtime resource handles cannot be forged.
3. Define test support for integration-style IO programs that depend on external services.
