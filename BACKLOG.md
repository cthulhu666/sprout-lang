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
- [ ] `P0` Define runtime error conventions for effectful builtins (no silent exits).
- [ ] `P1` Add ergonomic helpers for control flow (`when_ok`, `when_error`, optional pipeline helpers).

### 2) Networking and HTTP Client

- [x] `P0` Add builtin: `http_request(method, url, headers, body, timeout_ms) -> Result HttpError HttpResponse`.
- [x] `P0` Define `HttpResponse` shape (`status`, `headers`, `body`) and `HttpError` variants.
- [x] `P1` Add convenience wrappers in `stdlib/http_client.sprout` (`get`, `post`, header helpers).
- [ ] `P1` Ensure interpreter/native parity for HTTP client builtins.

### 3) JSON Support

- [x] `P0` Add `json_parse(String) -> Result JsonError Json`.
- [x] `P0` Add JSON query helpers (`json_get`, `json_get_string`, `json_get_int`, `json_get_array`, etc.).
- [ ] `P1` Add `json_stringify(Json) -> String` for debug and payload building.
- [ ] `P1` Add tests for malformed input and edge cases.

### 4) Terminal UI Runtime

- [~] `P0` Add terminal builtins: alternate screen, clear, cursor move, hide/show cursor, style/color.
- [~] `P0` Add key input primitive (single-key read with non-blocking/poll mode).
- [ ] `P1` Add line wrapping / viewport helpers in stdlib.
- [ ] `P1` Add basic event loop utility for TUI apps.

### 5) Data Structures and Collections

- [~] `P1` Add practical indexed sequence type (`Array`/`Vector`) for UI lists.
- [~] `P1` Add dictionary/map type for API payload handling.
- [ ] `P2` Add set type and common ops.

### 6) Modules and Packaging

- [ ] `P0` Implement real module namespaces (remove flattened global import model).
- [ ] `P1` Move global string helpers into namespaced stdlib module(s).
- [ ] `P1` Define package/dependency conventions for third-party modules.

### 7) Tooling and Developer UX

- [ ] `P1` Add REPL (parse/typecheck/eval loop).
- [ ] `P1` Add better diagnostics for module/import/export errors with source context.
- [ ] `P2` Add formatter/linter baseline.

### 8) External Integration Example Layer

- [~] `P0` Add `examples/sentry_api.sprout` with typed wrappers for key endpoints:
  - list issues
  - issue details
  - pagination cursor extraction
- [ ] `P1` Add auth/token helpers and secure configuration loading.
- [ ] `P1` Add integration tests with mocked HTTP responses.

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
- [x] Swappable TCP server model exists (`reactor`, `blocking`) for server-side runtime.
- [x] `http_request` builtin and typed HTTP result ADTs are implemented (interpreter).
- [x] `json_parse` builtin and basic JSON accessors are implemented.
- [x] initial `stdlib.collections` module exists with `Vec` and `Dict` ADTs plus core helpers.
- [ ] terminal interaction primitives are not yet fully implemented.

## Next 3 Tasks (Execution Order)

1. Extend `stdlib.collections` with richer operations (`vec_slice`, `vec_reverse`, `dict_keys`, `dict_values`) and edge-case tests.
2. Add `json_stringify(Json) -> String` with malformed/edge-case tests.
3. Implement consistent runtime error conventions for effectful builtins.
