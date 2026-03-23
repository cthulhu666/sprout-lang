![Sprout logo](assets/logo.png)

# Sprout

Sprout is an experimental, statically typed, functional-first programming language focused on being "Haskell for ordinary people": strong type safety, predictable semantics, and approachable syntax.

The v0 core aims to infer types wherever they can be determined unambiguously
without compromising implementation simplicity, predictable behavior, or
diagnostic quality.

## Status

Prototype implementation stage. The repository includes a working tokenizer/parser/typechecker,
interpreter runtime, early native backend, module loader, and stdlib examples.

## Docs

- [Specification v0 (Normative)](./docs/spec-v0.md)
- [Language Design v0](./docs/language-design-v0.md)
- [Effect System v0 Plan](./docs/effect-system-v0-plan.md)
- [Effect System v1 Draft](./docs/effect-system-v1-draft.md)
- [Int Ranges v1 Draft](./docs/int-ranges-v1-draft.md)
- [Native REPL Roadmap](./docs/native-repl-roadmap.md)
- [REPL Self-Hosting v1 Draft](./docs/repl-self-hosting-v1-draft.md)
- [Sequencing Sugar v1 Draft](./docs/sequencing-sugar-v1-draft.md)
- [Language Design Best Practices (Research Notes)](./docs/language-design-best-practices.md)
- [HM Typechecker Guide (Human-Friendly)](./docs/hm-typechecker.md)

Normative status:

- `docs/spec-v0.md` defines the stable Sprout core for v0.
- Features described elsewhere in this README but not specified in `docs/spec-v0.md`
  are implementation features or experimental extensions.
- In particular, the current module system and typeclass support are implemented
  in the prototype, but are not yet part of normative v0.
- The current implementation also includes an experimental first records slice:
  nominal record declarations such as `type User = { name: String }`, typed
  record literals such as `User { name = "Ada" }`, and field projection via the
  contextual special form `get user name`. Records are not part of normative v0
  yet, and record updates remain deferred.
- The current implementation uses explicit function effects in the v0 core:
  pure functions omit an annotation, effectful functions use `!{IO}`, and
  higher-order helpers may use restricted singleton effect variables such as
  `!{e}`.
- Mixed/open effect rows are not supported yet; keep `!{IO}` and `!{e}` cases
  concrete for now.
- `docs/effect-system-v1-draft.md` is now a forward-looking draft for the next
  effect milestone beyond the implemented v0 baseline.
- Native REPL work is the current tooling priority.
- `docs/repl-self-hosting-v1-draft.md` and the language-server/compiler
  milestones are currently deferred as product work, but the native REPL bridge
  is being shaped as reusable language-service infrastructure for those later
  directions rather than as REPL-only glue.

## Repository Layout

- `docs/` design and process documents
- `examples/` sample source files
- `sprout/` implementation (`tokenizer`, `parser`, `typechecker`, `cli`)
- `tests/` parser and typechecker tests
- `tests/conformance/` executable language behavior fixtures (`run`, `parse_error`, `type_error`, `runtime_error`)
- `stdlib/` language-level standard library source (`prelude.sprout`)
  plus protocol helpers such as `http.sprout`
- `mise.toml` pinned local toolchain (`python`, `just`)
- `justfile` common project commands

v0 execution note:

- Top-level `let` bindings must be pure.
- Effectful work is expected to live in functions, with `main` as the entrypoint.

## Tooling (mise + just)

This repo uses [`mise`](https://mise.jdx.dev/) to pin tools and [`just`](https://github.com/casey/just) as task runner.

1. Install tools from `mise.toml`:
   `mise install`
2. Run commands through mise:
   `mise exec -- just test`

Common tasks:

- Parse file: `mise exec -- just parse examples/fizzbuzz.sprout`
- Format repo: `mise exec -- just fmt`
- Check repo formatting: `mise exec -- just fmt-check`
- Lint repo: `mise exec -- just lint`
- Format file: `mise exec -- just fmt-file examples/fizzbuzz.sprout`
- Check file formatting: `mise exec -- just fmt-check-file examples/fizzbuzz.sprout`
- Lint file: `mise exec -- just lint-file examples/fizzbuzz.sprout`
- Typecheck file: `mise exec -- just check examples/fizzbuzz.sprout`
- Run file: `mise exec -- just run examples/fizzbuzz.sprout`
- Start REPL: `mise exec -- python -m sprout.cli repl` (default interpreter-launched path) or `mise exec -- python -m sprout.cli repl --native` (experimental native launcher backed by `analysis-service`; both run the Sprout-hosted frontend in [stdlib/repl.sprout](./stdlib/repl.sprout); [examples/repl_hosted.sprout](./examples/repl_hosted.sprout) remains a thin wrapper; the native launcher now reuses a cached compiled REPL binary between launches and the compiled native frontend carries its own default `analysis-service` command based on the Python used at compile time; loads the foundational prelude by default; interactive mode detection, line editing, `Tab` completion, and `Up`/`Down` history now live in Sprout code; `Tab` completion is ASCII case-insensitive and can complete imported namespace members such as `json.string` after `import stdlib.json`; `:{` and `:}` execute explicit multiline REPL blocks sequentially behind a distinct `block| ` continuation prompt, and `:cancel` aborts the current block; ordinary `import ...` lines work inside the session)
  If native REPL cache build fails, the launcher now reports the native compile error directly and suggests the interpreter-backed `repl` path.
  Native REPL startup itself no longer requires a live `analysis-service`; the bridge is contacted lazily on the first analysis-backed action such as `import`, declaration acceptance, `:type`, `:instances`, or expression evaluation.
- Run tests: `mise exec -- just test`
- Run integration-style IO tests: `mise exec -- just test-integration`
- Emit LLVM IR: `mise exec -- just compile examples/factorial.sprout /tmp/factorial.ll`
- Build native binary (clang): `mise exec -- just compile-native /tmp/prog.sprout /tmp/prog`

Integration-style IO test convention:

- Service-backed tests live in [tests/test_integration_io.py](./tests/test_integration_io.py).
- Shared local-fixture helpers live in [tests/integration_support.py](./tests/integration_support.py).
- Prefer local mock services on `127.0.0.1` over external hosted dependencies.
- Keep `just test` as the full suite; use `mise exec -- just test-integration` when iterating on service-backed interpreter/native behavior.

## Builtin Helpers (v0)

Runtime builtins (host-implemented):

`!{IO}` builtins:

- `print(x) -> Unit !{IO}`
- `print_int(x: Int) -> Int !{IO}` (prints and returns `x`, useful for native backend subset)
- `read_lines(path: String) -> List String !{IO}`
- `read_file(path: String) -> String !{IO}`
- `read_int_lines(path: String) -> Vector Int !{IO}`
- `env_get(name: String) -> Maybe String !{IO}`
- `argv_get(index: Int) -> Maybe String !{IO}` (`0` is the first user-supplied program argument)
- `tcp_listen(port: Int) -> Int !{IO}`
- `tcp_accept(listener: Int) -> Int !{IO}`
- `tcp_read(conn: Int) -> String !{IO}`
- `tcp_write(conn: Int, payload: String) -> Unit !{IO}`
- `tcp_connect(host: String, port: Int) -> Result stdlib.net.TcpError Int !{IO}`
- `tcp_read_exact(conn: Int, count: Int) -> Result stdlib.net.TcpError Bytes !{IO}`
- `tcp_write_all(conn: Int, payload: Bytes) -> Result stdlib.net.TcpError Int !{IO}`
- `tcp_close(conn: Int) -> Unit !{IO}`
- `tcp_close_listener(listener: Int) -> Unit !{IO}`
- `tcp_echo_serve(port: Int, max_connections: Int) -> Unit !{IO}`
- `http_request(method: String, url: String, headers: String, body: String, timeout_ms: Int) -> Result HttpError HttpResponse !{IO}`
- `crypto_random_bytes(count: Int) -> Result stdlib.crypto.CryptoError Bytes !{IO}`
- `term_clear() -> Unit !{IO}`
- `term_move(row: Int, col: Int) -> Unit !{IO}`
- `term_hide_cursor() -> Unit !{IO}`
- `term_show_cursor() -> Unit !{IO}`
- `term_read_key() -> String !{IO}` (reads one key from stdin; in TTY mode it reads immediately without waiting for newline)
- `term_read_line() -> Maybe String !{IO}` (reads one stdin line, trims trailing `\n`/`\r\n`, returns `Nothing` at EOF)
- `term_write(text: String) -> Unit !{IO}`

Experimental snapshot analysis hooks:

- Active snapshot/state hooks used by the current Sprout REPL frontend.
  The host implementation now routes snapshot analysis through `sprout.analysis`:
- `repl_eval_expr_in_source(module_source: String, expr: String) -> Result String (Vec String) !{IO}`
- `repl_check_source(module_source: String) -> Result String Unit !{IO}`
- `repl_declared_names_in_source(module_source: String) -> Result String (Vec String) !{IO}`
- `repl_exported_names_in_source(module_source: String) -> Result String (Vec String) !{IO}`
- `repl_symbol_inventory_in_source(module_source: String) -> Result String (Vec String, Vec String, Vec String) !{IO}` (`declared`, `imported`, `exported`)
- `repl_diagnostics_in_source(module_source: String) -> Vec (String, Int, Int) !{IO}`
- `repl_type_of_in_source(module_source: String, expr: String) -> Result String String !{IO}`
- `repl_instances_in_source(module_source: String, query: String) -> Result String (String, Vec String) !{IO}`
- `repl_complete_in_state(line_buffer: String, imports: Vec String, declarations: Vec String) -> (String, Vec String) !{IO}`
- `repl_reset_session() -> Unit !{IO}`

- Neutral compatibility aliases now exist for the shared analysis subset:
  `analysis_check_source`, `analysis_declared_names_in_source`,
  `analysis_exported_names_in_source`, `analysis_symbol_inventory_in_source`,
  `analysis_symbol_locations_in_source`, `analysis_diagnostics_in_source`,
  `analysis_type_of_in_source`, `analysis_instances_in_source`.
- Python-side analysis helpers in `sprout.analysis` now also expose
  `symbol_metadata_in_source(...)` for structured top-level/import symbol
  metadata, including optional definition locations for imported symbols when
  the provider declaration is available, without widening the builtin/runtime
  ABI. They also expose
  `structured_diagnostics_in_source(...)` for severity/stage/location-aware
  diagnostics records while the builtin bridge keeps the older tuple shape.

- Legacy compatibility hooks still present in the host runtime, but no longer used
  by `stdlib/repl.sprout`:
- `repl_add_import(source: String) -> Result String Unit !{IO}`
- `repl_add_declaration(source: String) -> Result String Unit !{IO}`
- `repl_eval_expr(source: String) -> Result String (Vec String) !{IO}`
- `repl_type_of(source: String) -> Result String String !{IO}`
- `repl_instances(source: String) -> Result String (String, Vec String) !{IO}`
- `repl_complete(line_buffer: String) -> (String, Vec String) !{IO}`

These are implementation hooks for the Sprout-hosted REPL frontend. They are
still mostly interpreter-backed. The current near-term priority is making that
bridge native-capable rather than making it self-hosted. The canonical
analysis-service subprocess boundary is now
`python -m sprout.analysis_stdio` for snapshot `check_source` and
`declared_names_in_source` / `exported_names_in_source` /
`symbol_inventory_in_source` / `diagnostics_in_source` /
`type_of_in_source` / `instances_in_source` / `eval_expr_in_source` queries,
plus compatibility-only explicit-state `complete_in_state`, as the first explicit host-service
bridge below the REPL frontend, and native compiled programs now use that bridge for
`repl_check_source(...)`, `analysis_check_source(...)`,
`repl_declared_names_in_source(...)`, `analysis_declared_names_in_source(...)`,
`repl_exported_names_in_source(...)`, `analysis_exported_names_in_source(...)`,
`repl_symbol_inventory_in_source(...)`,
`analysis_symbol_inventory_in_source(...)`,
`repl_diagnostics_in_source(...)`, `analysis_diagnostics_in_source(...)`,
`repl_type_of_in_source(...)`, `analysis_type_of_in_source(...)`,
`repl_instances_in_source(...)`, `analysis_instances_in_source(...)`, and
`repl_eval_expr_in_source(...)`, plus `repl_complete_in_state(...)` and
`analysis_symbol_locations_in_source(...)`. The active Sprout REPL frontend no
longer depends on that bridge for `Tab` completion; completion now runs locally
in `stdlib/repl.sprout` from the current session text state, and startup no
longer calls `repl_reset_session()` either, so that hook is now
compatibility-only rather than part of the active frontend path. The rest
of the REPL/analysis snapshot hooks still report unsupported-backend runtime
errors in native binaries. End-to-end native execution of the current Sprout
REPL frontend is now verified by compiling and running
`examples/repl_hosted.sprout` with that bridge in place, and the user-facing
`repl --native` launcher now exposes that path experimentally while still using
the Python `analysis-service` bridge underneath. The canonical stdio adapter is
[sprout/analysis_stdio.py](./sprout/analysis_stdio.py), and that module is now only the
JSON/stdin/stdout adapter over the reusable dispatcher in
[sprout/analysis_dispatch.py](./sprout/analysis_dispatch.py) and protocol loop
in [sprout/analysis_protocol.py](./sprout/analysis_protocol.py), which is the
intended replacement seam for a future non-Python native service. That bridge
is being treated as reusable language-service infrastructure for later
self-hosted compiler and language-server work, not as REPL-only plumbing. The launcher
reuses both a cached
compiled REPL binary between launches and one long-lived analysis-service
subprocess per native program run, with one automatic restart for replay-safe
snapshot queries if that child dies mid-session. The hidden
`sprout.analysis_service` and `sprout.cli analysis-service` remain only as
compatibility wrappers.
Native programs can override the service command via
`SPROUT_ANALYSIS_SERVICE_CMD`; if that command is invalid, native REPL and
native snapshot-query failures now point back to that env var explicitly.
Tests can override the launcher cache
directory via `SPROUT_NATIVE_REPL_CACHE_DIR`.

Native TCP listener and connection handle tables now reuse closed slots, so long-running native servers no longer fail after a fixed total number of accepted connections.

Pure value transforms and runtime-backed persistent data helpers:

- `parse_int(s: String) -> Int`
- `split_words(s: String) -> List String`
- `str_concat(a: String, b: String) -> String`
- `str_len(s: String) -> Int`
- `str_slice(s: String, start: Int, len: Int) -> String`
- `str_find(s: String, needle: String) -> Int` (`-1` when not found)
- `str_starts_with(s: String, prefix: String) -> Bool`
- `bytes_empty() -> Bytes`
- `bytes_length(value: Bytes) -> Int`
- `bytes_get(value: Bytes, index: Int) -> Maybe Int`
- `bytes_slice(value: Bytes, start: Int, count: Int) -> Bytes`
- `bytes_append(left: Bytes, right: Bytes) -> Bytes`
- `bytes_singleton(value: Int) -> Bytes`
- `bytes_from_utf8(raw: String) -> Bytes`
- `bytes_to_utf8(value: Bytes) -> Result stdlib.bytes.Utf8Error String`
- `bytes_builder_empty() -> Builder`
- `bytes_builder_bytes(value: Bytes) -> Builder`
- `bytes_builder_byte(value: Int) -> Builder`
- `bytes_builder_u16_be(value: Int) -> Builder`
- `bytes_builder_u32_be(value: Int) -> Builder`
- `bytes_builder_append(left: Builder, right: Builder) -> Builder`
- `bytes_builder_build(value: Builder) -> Bytes`
- `crypto_sha256(value: Bytes) -> Bytes`
- `crypto_hmac_sha256(key: Bytes, msg: Bytes) -> Bytes`
- `crypto_base64_encode(value: Bytes) -> String`
- `crypto_base64_decode(raw: String) -> Result stdlib.crypto.Base64Error Bytes`
- `crypto_bytes_xor(left: Bytes, right: Bytes) -> Result stdlib.crypto.BytesOpError Bytes`
- `map_empty() -> Map a`
- `map_get(m: Map a, key: String) -> Maybe a`
- `map_set(m: Map a, key: String, value: a) -> Map a`
- `map_remove(m: Map a, key: String) -> Map a`
- `map_size(m: Map a) -> Int`
- `json_parse(raw: String) -> Result stdlib.json.JsonError stdlib.json.Json`
- `json_stringify(value: stdlib.json.Json) -> String`
- `vector_empty() -> Vector a`
- `vector_length(v: Vector a) -> Int`
- `vector_get(v: Vector a, index: Int) -> Maybe a`
- `vector_set(v: Vector a, index: Int, value: a) -> Vector a`
- `vector_append(v: Vector a, value: a) -> Vector a`
- `map_nth_key(m: Map a, index: Int) -> Maybe String`
- `map_nth_value(m: Map a, index: Int) -> Maybe a`

Effect notes:

- Sprout v0 now tracks the built-in `IO` effect on function types.
- Pure functions omit an effect annotation.
- Effectful functions use `!{IO}`, for example `fn main() -> Unit !{IO} = ...`.
- Restricted effect polymorphism is supported for higher-order helpers via
  singleton effect variables such as:
  `fn apply_twice(f: Int -> Int !{e}, x: Int) -> Int !{e} = f(f(x))`.
- `main` stays concrete when effectful; do not use `!{e}` on `main`.
- Effects do not change Sprout's strict execution order; they constrain which
  functions may call which other functions.
- Mixed/open effect rows and additional effect labels are still deferred follow-up work.

String/runtime helpers are host-implemented primitives. Application code should use `stdlib.string`; direct `str_*`/`split_words` usage is reserved for `stdlib.*` modules.

Standard library (Sprout source in `stdlib/prelude.sprout`):

- `Maybe a` (`Just`, `Nothing`)
- `map(fn, list) -> List`
- `fold(fn, init, list) -> value`
- `filter(predicate, list) -> List`
- `split_ints(s: String) -> List Int`
- `Vec a` plus foundational helpers:
  - `vec_empty()`
  - `vec_prepend(value, vec)`
  - `vec_append(value, vec)`
  - `vec_length(vec)`
  - `vec_get(index, vec) -> Maybe a`
  - `vec_get_or(index, fallback, vec)`
  - `vec_set(index, value, vec)`
  - `vec_map(f, vec)`
  - `vec_fold(f, init, vec)`
  - `vec_slice(start, count, vec)`
  - `vec_reverse(vec)`
  - `vec_sum(vec)`
  - `vec_sum_by(f, vec)`
- `Dict v` plus foundational helpers:
  - `dict_empty()`
  - `dict_get(key, dict) -> Maybe v`
  - `dict_set(key, value, dict)`
  - `dict_remove(key, dict)`
  - `dict_keys(dict) -> Vec String`
  - `dict_values(dict) -> Vec v`
  - dict literals: `{foo: 1, "bar": 2}`, `{}`
- `Semigroup t`, `Functor f`, and `Foldable f`
- `foldable_to_vec(xs: f a) -> Vec a` where `Foldable f`
- prelude instances are currently provided for `String`, `List a`, `Vec a`, and `Dict v`
- `left ++ right` works in the default REPL for strings and lists
- `Result e a` with helpers:
  - forward pipe operator: `value |> f` rewrites to `f(value)`, and
    `value |> g(a, b)` rewrites to `g(a, b, value)`
  - function composition operators:
    `f >> g` rewrites to `\x -> g(f(x))`
    `f << g` rewrites to `\x -> f(g(x))`
  - `pipe(f, value)`
  - `result_map(f, r)`
  - `result_map_error(f, r)`
  - `result_and_then(f, r)`
  - `result_with_default(fallback, r)`
  - `result_pipe(f, r)` aliases `result_and_then` in pipeline style
  - `result_pipe_ok(f, r)` aliases `result_map` in pipeline style
  - `result_pipe_error(f, r)` aliases `result_map_error` in pipeline style
  - `when_ok(f, r)` runs `f` for `Ok` and preserves `r`
  - `when_error(f, r)` runs `f` for `Err` and preserves `r`

Current call semantics note: ordinary function values support under-application.
Sprout uses nested arrow types for multi-parameter functions, so `f(x)` returns
another function value when `f` still has remaining parameters.

Math module (in `stdlib/math.sprout`):

- Int-only helper surface; this does not add `Float`, `Decimal`, or fixed-width integer types to v0
- `abs(x) -> Int`
- `min(x, y) -> Int`
- `max(x, y) -> Int`
- `clamp(x, lo, hi) -> Int`
- `sign(x) -> Int`
- `pow(base, exp) -> Maybe Int`
- `mod(x, n) -> Maybe Int`
- `gcd(x, y) -> Int`
- `lcm(x, y) -> Int`
- `is_even(x) -> Bool`
- `is_odd(x) -> Bool`

Math semantics:

- `mod(x, n)` is Euclidean modulo
- when `n > 0`, `mod(x, n)` returns `Just r` with `0 <= r < n`
- when `n <= 0`, `mod(x, n)` returns `Nothing`
- `pow(base, exp)` returns `Nothing` when `exp < 0`
- interpreter `Int` arithmetic currently follows host arbitrary-precision integer behavior
- the current native backend still lowers `Int` to `i64`, so overflow-sensitive results for `abs`, `pow`, `gcd`, and `lcm` are not yet backend-independent outside the backend's current representable range
- this is a v0 implementation limitation, not the intended long-term meaning of `Int`

For module code, prefer:
`import stdlib.math as math`
then call helpers like `math.mod(...)` and `math.gcd(...)`.

Example usage:

```sprout
fn inc(x: Int) -> Int = x + 1
fn double_if_large(x: Int) -> Result String Int =
  if x > 10 then Ok(x * 2) else Err("too-small")
fn show_ok(x: Int) -> Unit !{IO} = print(x)
fn show_error(e: String) -> Unit !{IO} = print(e)

# Nested style
when_error(
  show_error,
  when_ok(
    show_ok,
    result_pipe_ok(
      inc,
      result_pipe(
        double_if_large,
        Ok(pipe(inc, 20))
      ),
    )
  )
)

# Piped style
Ok(20)
|> result_pipe_ok(inc)
|> result_pipe(double_if_large)
|> result_pipe_ok(inc)
|> when_ok(show_ok)
|> when_error(show_error)
```

Runnable demo:
- `python3 -m sprout.cli run --with-stdlib examples/result_demo.sprout`

HTTP stdlib helpers (in `stdlib/http.sprout`):

- uses foundational prelude `Maybe` and `Result`
- `HttpResponse(status, headers, body)`
- `HttpError` variants (`HttpTimeout`, `HttpNetwork`, `HttpBadStatus`, `HttpDecode`)
- `HttpStatusError` variants (`HttpUnsupportedStatus`)
- `parse_request_line(raw) -> Maybe RequestLine`
- `http_response(status, body) -> Result HttpStatusError String`
- `http_response_body(resp: HttpResponse) -> String`
- `http_ok(body) -> String`
- `http_bad_request() -> String`
- `http_echo_response(raw_request) -> String`

Experimental HTTP server helpers (in `stdlib/http_server.sprout`):

- `HttpRequest` request values with accessor helpers
- `HttpServerResponse` response values built via helper functions
- `HttpServerError` variants (`HttpInvalidRequest`, `HttpServerUnsupportedStatus`)
- `parse(raw) -> Result HttpServerError HttpRequest`
- `render(resp) -> Result HttpServerError String`
- `response(status, body) -> HttpServerResponse`
- `ok(body) -> HttpServerResponse`
- `bad_request(body) -> HttpServerResponse`
- `not_found(body) -> HttpServerResponse`
- `with_header(name, value, resp) -> HttpServerResponse`
- `request_method(req) -> String`
- `request_path(req) -> String`
- `request_version(req) -> String`
- `request_body(req) -> String`
- `request_header(name, req) -> Maybe String`
- `serve_n(port, max_connections, handler) -> Unit !{IO}`

Current experimental scope:

- HTTP/1.1 request line parsing plus header parsing into a `Dict String`
- `Content-Length` request bodies
- `Connection: close` responses only
- sequential request handling per accepted connection
- no keep-alive, chunked transfer encoding, TLS, or multi-reactor native server runtime yet

JSON stdlib helpers (in `stdlib/json.sprout`):

- `JsonError` / `Json` / `JsonArray` / `JsonObject` ADTs
- `JsonArrayStep` / `JsonObjectStep` traversal ADTs
- builder helpers: `null()`, `bool(value)`, `int(value)`, `string(value)`, `array_from_list(items)`, `object_from_pairs(items)`
- `json_parse(raw) -> Result JsonError Json`
- `json_stringify(value: Json) -> String` (compact JSON for the currently representable subset)
- `json_get_field(value, key) -> Maybe Json`
- `json_get_string(value) -> Maybe String`
- `json_get_int(value) -> Maybe Int`
- `json_get_array(value) -> Maybe JsonArray`
- `json_get_object(value) -> Maybe JsonObject`
- `json_array_next(array) -> Maybe JsonArrayStep`
- `json_object_next(object) -> Maybe JsonObjectStep`

Example:

```sprout
import stdlib.json as json

fn payload() -> json.Json =
  json.object_from_pairs(
    [
      ("title", json.string("hello")),
      ("count", json.int(2)),
      ("items", json.array_from_list([json.string("a"), json.bool(true)]))
    ]
  )
```

`http_response` currently supports a practical fixed subset of common statuses:
`200`, `201`, `202`, `204`, `400`, `401`, `403`, `404`, `405`, `409`, `410`,
`422`, `429`, `500`, `501`, `502`, `503`, and `504`. Unsupported codes return
`Err(HttpUnsupportedStatus(code))` instead of being silently rewritten.

HTTP client convenience module (in `stdlib/http_client.sprout`):

- `http_get(url, headers, timeout_ms) -> Result HttpError HttpResponse`
- `http_post(url, headers, body, timeout_ms) -> Result HttpError HttpResponse`
- `http_put(url, headers, body, timeout_ms) -> Result HttpError HttpResponse`

TCP client helper types (in `stdlib/net.sprout`):

- uses foundational prelude `Result`
- `TcpError` variants (`TcpInvalidArgument`, `TcpInvalidHandle`, `TcpConnectFailed`, `TcpReadFailed`, `TcpWriteFailed`, `TcpEndOfStream`)
- `TcpConnection`
- `TcpListener`
- `connect(host, port) -> Result TcpError TcpConnection`
- `read_exact(conn, count) -> Result TcpError Bytes`
- `write_all(conn, payload) -> Result TcpError Int`
- `read_exact_utf8(conn, count) -> Result TcpError String`
- `write_all_utf8(conn, payload) -> Result TcpError Int`
- `close(conn) -> Unit !{IO}`
- `listen_local(port) -> TcpListener`
- `accept(listener) -> TcpConnection`
- `close_listener(listener) -> Unit !{IO}`
- `tcp_error_message(err) -> String`

`TcpConnection` and `TcpListener` are now exported as opaque handle types; application code can use the types but cannot forge the underlying constructors outside `stdlib.net`.

Bytes helpers (in `stdlib/bytes.sprout`):

- uses foundational prelude `Maybe` and `Result`
- `Builder` opaque type for efficient packet construction
- `empty() -> Bytes`
- `singleton(value) -> Bytes`
- `length(value) -> Int`
- `get(value, index) -> Maybe Int`
- `slice(value, start, count) -> Bytes`
- `append(left, right) -> Bytes`
- `u16_be(value) -> Bytes`
- `u32_be(value) -> Bytes`
- `read_u16_be(value) -> Maybe Int`
- `read_u32_be(value) -> Maybe Int`
- `from_string(raw) -> Bytes`
- `to_string(value) -> Result Utf8Error String`
- `c_string(raw) -> Bytes`
- `read_c_string(value) -> Result Utf8Error String`
- builder helpers:
  - `builder_empty() -> Builder`
  - `builder_bytes(value: Bytes) -> Builder`
  - `builder_byte(value: Int) -> Builder`
  - `builder_u16_be(value: Int) -> Builder`
  - `builder_u32_be(value: Int) -> Builder`
  - `builder_append(left: Builder, right: Builder) -> Builder`
  - `builder_build(value: Builder) -> Bytes`

Crypto helpers (in `stdlib/crypto.sprout`):

- `sha256(value: Bytes) -> Bytes`
- `hmac_sha256(key: Bytes, message: Bytes) -> Bytes`
- `base64_encode(value: Bytes) -> String`
- `base64_decode(raw: String) -> Result Base64Error Bytes`
- `bytes_xor(left: Bytes, right: Bytes) -> Result BytesOpError Bytes`
- `random_bytes(count: Int) -> Result CryptoError Bytes` (effectful; reads runtime entropy)

Terminal convenience module (in `stdlib/terminal.sprout`):

- `term_home() -> Unit !{IO}`
- `term_reset_screen() -> Unit !{IO}`
- `term_render_line(row, text) -> Unit !{IO}`
- `term_read_key_once() -> String !{IO}`
- `term_read_line_once() -> Maybe String !{IO}`

Collections module (in `stdlib/collections.sprout`):

- compatibility namespace for the foundational collection/typeclass surface now defined in the prelude
- existing imports such as `import stdlib.collections (Vec, Dict, Functor, Foldable, vec_append, dict_get)` continue to resolve
- prefer the unqualified prelude surface in standalone code and the default REPL

Low-level runtime notes:

- `Vector` and `vector_*` builtins exist as backend/runtime primitives.
- For module code, `stdlib.collections` remains the stable compatibility import path for collection helpers.
- CLI/module checks reject raw `Vector`/`Map` and `vector_*`/`map_*` usage outside `stdlib.*` modules.
- Builtin failures now follow one convention:
  `runtime error: builtin \`name\`: detail`
- `sprout run` surfaces that as `error: runtime error: builtin ...`.
- Native binaries print the same runtime-error message to stderr and exit with status `1`.

String module (in `stdlib/string.sprout`):

- `words(raw: String) -> List String`
- `concat(left: String, right: String) -> String`
- `length(raw: String) -> Int`
- `slice(raw: String, start: Int, count: Int) -> String`
- `find(raw: String, needle: String) -> Int`
- `starts_with(raw: String, prefix: String) -> Bool`
- `string_lines(raw: String) -> Vec String`
- `string_digits(raw: String) -> Vec Int`

For module code, prefer:
`import stdlib.string as string`
then call helpers like `string.concat(...)` and `string.length(...)`.

Application-level example wrapper:

- `examples/sentry_api.sprout` demonstrates how to layer Sentry-specific API helpers on top of generic `stdlib.http` + `stdlib.http_client`.
- `examples/sentry_issue_browser_tui.sprout` is a minimal TUI-oriented scaffold module using the app-level Sentry API layer.
- `examples/http_get_cli.sprout` is a simple CLI HTTP client that reads its URL from `argv_get(0)` and prints the response body.

Load stdlib prelude explicitly for standalone files:

- `python3 -m sprout.cli check --with-stdlib your_file.sprout`
- `python3 -m sprout.cli run --with-stdlib your_file.sprout`
- module-loaded programs get the foundational prelude implicitly

Load HTTP and JSON helpers:

- `python3 -m sprout.cli check --with-http-stdlib your_file.sprout`
- `python3 -m sprout.cli run --with-http-stdlib your_file.sprout`
- Pass program arguments to `sprout run` after the source path; inside Sprout, read them with `argv_get(index)`.
- Example HTTP echo server:
  `SPROUT_NET_MODEL=reactor python3 -m sprout.cli run examples/http_echo_server.sprout`
- Example HTTP GET CLI:
  `python3 -m sprout.cli run examples/http_get_cli.sprout http://127.0.0.1:8080/`
- Collections helper demo:
  `python3 -m sprout.cli run examples/collections_demo.sprout`
- Typeclass collections demo (experimental surface area, not normative v0):
  `python3 -m sprout.cli run examples/typeclass_functor_foldable_demo.sprout`

## Native Backend (Early)

`sprout compile` currently supports a small subset:

- top-level `fn`, `type`, and top-level `let` (constant and runtime-initialized),
- `Int`/`Bool`/`String` typed params and returns, plus effectful returns such as `Unit !{IO}` and `Int !{IO}`,
- expressions: literals, vars, arithmetic, comparisons, `&&`, `||`, function composition (`f >> g` means `g(f(x))`, `f << g` means `f(g(x))`), lambdas (`\(x, y) -> ...`), `if`, direct calls, indirect closure calls, recursion,
- tuple expressions/types/patterns with general `n`-tuple arity (`(x, y)`, `(Int, String)`, `match pair with | (x, y) -> ...`),
  one-arg lambdas may also use the shorthand `\x -> ...`,
  and empty lambda parameter lists are currently rejected,
- ADT constructor calls and `match` lowering (constructors with up to 3 fields),
- generic type variables are currently erased to runtime `i64` handles,
- closure-backed function values are supported, including captured lambdas and higher-order params/returns,
- `print(...)` lowering for `Int`/`Bool`/`String`/ADT values,
- `print_int(...)` external call.

TCP server builtins are available in interpreter and native (`sprout compile --native`) modes.
Typed TCP client builtins are also available and now use `Bytes` payloads for raw protocol data.
Application code should prefer the typed `stdlib.net` wrapper API over bare `Int` socket handles.
Raw `bytes_*` primitives are internal to `stdlib.*`; application code should use `stdlib.bytes`.
`to_string` rejects invalid UTF-8 and decoded NUL bytes; `read_c_string` is the intended helper for null-terminated protocol strings.
`http_request` is available in interpreter and native modes for plain `http://` requests.

Interpreter runtime has a swappable server model selected by `SPROUT_NET_MODEL`:

- `reactor` (default): event loop / readiness-based echo server
- `blocking`: simple blocking accept/read/write loop

## Modules (Experimental)

The prototype currently supports file-based modules with top-of-file headers.
This module system is not yet part of normative v0; treat this section as
implementation status rather than language spec.

Sprout supports:

- `module a.b.c`
- `import x.y.z`
- `import x.y.z as alias`
- `import x.y.z (name1, name2)`
- `import x.y.z as alias (name1, name2)`
- `export fn ...`, `export type Name`, `export type Name(..)`, `export let ...` (top-level only)

Resolution:

- `import stdlib.http` resolves to `stdlib/http.sprout`
- loader checks importing file directory first, then current working directory
- import cycles are rejected
- bare `import x.y.z` introduces a namespace qualifier using the last path segment (`z.symbol`)
- `import x.y.z as alias` introduces `alias.symbol`
- `import x.y.z (name1, name2)` imports only those names unqualified
- importing `export type Name` exposes the type name only
- importing `export type Name(..)` also exposes the type's constructors for pattern matches and calls
- top-level declarations are internally namespaced by module, so imported modules no longer flatten into one global scope

Export behavior:

- only explicitly exported top-level declarations are importable
- declarations without `export` are module-private

Commands:

- LLVM IR output: `python3 -m sprout.cli compile input.sprout -o out.ll`
- Native binary (requires `clang`): `python3 -m sprout.cli compile input.sprout --native -o out_bin`
- REPL: `python3 -m sprout.cli repl`
  - commands: `:type EXPR`, `:t EXPR`, `:instances TYPE`, `:i TYPE`, `:help`, `:quit`
  - the foundational prelude is loaded by default, so list literals, dict literals, `split_ints(...)`, `foldable_to_vec(...)`, and `++` work immediately
  - `:instances TYPE` lists matching unary typeclass instances for a type, including constructor-head matches such as `List Int` reporting `Functor List`
  - use ordinary imports inside the session to access stdlib modules, for example `import stdlib.http` or `import stdlib.string`
  - imported modules use their final path segment (`http.http_ok(...)`, `math.gcd(...)`)
- Formatter/linter baseline:
  - format in place: `python3 -m sprout.cli fmt your_file.sprout`
  - check formatting only: `python3 -m sprout.cli fmt --check your_file.sprout`
  - lint baseline style issues: `python3 -m sprout.cli lint your_file.sprout`
  - current scope: whitespace-aware formatting, comment preservation, trailing-whitespace/tab/final-newline checks

## Backlog

See [docs/backlog.md](./docs/backlog.md) for the current roadmap and follow-up work.

## Contributing

See [AGENTS.md](./AGENTS.md) for project-specific collaboration and change rules.
