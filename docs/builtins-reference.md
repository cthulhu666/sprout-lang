# Sprout Builtins Reference

Host-implemented runtime builtins and their effect annotations. Only a small
subset is the default implicit surface for ordinary modules; the rest sit behind
`stdlib.*` modules (`stdlib.terminal`, `stdlib.compiler`, `stdlib.bytes`, …).

For language semantics see [spec-v0.md](./spec-v0.md); for build/toolchain see
[development.md](./development.md).

Runtime builtins (host-implemented):

Only a small subset is intended as the default implicit builtin surface.
Raw terminal control hooks and the neutral `analysis_*` snapshot hooks now sit
behind `stdlib.terminal` and `stdlib.compiler` for ordinary modules, even
though the underlying host builtins still exist.

Builtin effect convention:

- runtime-bound host interaction uses `!{IO}`
- pure host-backed computation stays pure
- `Maybe` / `Result` describe value-level failure or optionality, not
  effectfulness
- internal and compatibility hooks still follow the same typing rule, but they
  are not part of the preferred ordinary-module surface

`!{IO}` builtins:

- `print(x) -> Unit !{IO}`
- `print_int(x: Int) -> Int !{IO}` (prints and returns `x`, useful for native backend subset)
- `read_lines(path: String) -> List String !{IO}`
- `read_file(path: String) -> Result String String !{IO}`
- `write_file(path: String, content: String) -> Result String Unit !{IO}`
- `panic(msg: String) -> a !{IO}`
- `read_int_lines(path: String) -> Vector Int !{IO}`
- `env_get(name: String) -> Maybe String !{IO}`
- `argv_get(index: Int) -> Maybe String !{IO}` (`0` is the first user-supplied program argument)
- `int_range(lo: Int, hi: Int) -> IntRange`
- `int_range_start(r: IntRange) -> Int`
- `int_range_end(r: IntRange) -> Int`
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

Application code should prefer the package surface in `stdlib.terminal`
(`write`, `hide_cursor`, `show_cursor`, `term_read_key_once`, and related
helpers) instead of the raw `term_*` hooks.

Experimental snapshot analysis hooks:

- Snapshot analysis hooks — route to the self-hosted `analysis_service_bin` subprocess
  when `SPROUT_ANALYSIS_SERVICE_CMD` is set; otherwise these hooks are unavailable
  (no REPL frontend is currently active):
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

- Neutral aliases for the shared analysis subset:
  `analysis_check_source`, `analysis_declared_names_in_source`,
  `analysis_exported_names_in_source`, `analysis_symbol_inventory_in_source`,
  `analysis_symbol_locations_in_source`, `analysis_diagnostics_in_source`,
  `analysis_type_of_in_source`, `analysis_instances_in_source`.
- Application code should prefer `stdlib.compiler` for these capabilities.
  The raw `analysis_*`/`repl_*` hooks are not part of the implicit builtin
  prelude for ordinary modules.
- The self-hosted analysis service is served by `sproutd`: build it with
  `just build-sproutd` and run it with `sproutd --analysis-service <stdlib_root>`.
  (The former standalone `analysis_service_bin` / `just build-analysis-service`
  are retired — sproutd wraps the identical `analysis_service_driver.run_service`
  entry.) It implements `declared_names_in_source`, `exported_names_in_source`,
  `symbol_inventory_in_source`, `symbol_locations_in_source`, `check_source`,
  `diagnostics_in_source`, `type_of_in_source`, and `eval_expr_in_source` over a
  JSON-over-stdio protocol. Override the service command via
  `SPROUT_ANALYSIS_SERVICE_CMD`.

Native TCP listener and connection handle tables now reuse closed slots, so long-running native servers no longer fail after a fixed total number of accepted connections.

Pure value transforms and runtime-backed persistent data helpers:

- `parse_int(s: String) -> Int`
- `int_to_string(value: Int) -> String` (runtime primitive; public formatting should prefer `Show.to_string`)
- `char_to_string(value: Char) -> String`
- `split_words(s: String) -> List String`
- `str_concat(a: String, b: String) -> String`
- `str_len(s: String) -> Int`
- `str_slice(s: String, start: Int, len: Int) -> String`
- `str_char_at(s: String, index: Int) -> Maybe Char`
- `str_find(s: String, needle: String) -> Int` (`-1` when not found)
- `str_starts_with(s: String, prefix: String) -> Bool`
- `str_compare(left: String, right: String) -> Int` (`-1`, `0`, `1`)
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
- public JSON entrypoints live in `stdlib.json` as `parse(raw)` and `stringify(value)`
- `vector_empty() -> Vector a`
- `vector_length(v: Vector a) -> Int`
- `vector_get(v: Vector a, index: Int) -> Maybe a`
- `vector_set(v: Vector a, index: Int, value: a) -> Vector a`
- `vector_append(v: Vector a, value: a) -> Vector a`
- `vector_concat(a: Vector x, b: Vector x) -> Vector x`
- `map_nth_key(m: Map a, index: Int) -> Maybe String`
- `map_nth_value(m: Map a, index: Int) -> Maybe a`

Effect notes:

- Sprout v0 now tracks the built-in `IO` effect on function types.
- Pure functions omit an effect annotation.
- Effectful functions use `!{IO}`, for example `fn main() -> Unit !{IO} = ...`.
- Host-implemented builtins follow the same rule as user-defined functions:
  their declared type determines whether they are pure or `!{IO}`.
- Builtins that interact with runtime or external state use `!{IO}` even when
  they return `Maybe` or `Result`; pure value transforms stay pure even when
  they return `Maybe` or `Result`.
- Restricted effect polymorphism is supported for higher-order helpers via
  singleton effect variables such as:
  `fn apply_twice(f: Int -> Int !{e}, x: Int) -> Int !{e} = f(f(x))`.
- Executable `main` must stay concrete and have type `Unit !{IO}`.
- Effects do not change Sprout's strict execution order; they constrain which
  functions may call which other functions.
- Mixed/open effect rows and additional effect labels are still deferred
  follow-up work, and remain deferred until real code demonstrates recurring
  pressure that the current `!{IO}` and singleton `!{e}` model cannot express
  cleanly.

String/runtime helpers are host-implemented primitives. In the current experimental text slice, `str_len`, `str_slice`, `str_char_at`, and `str_find` use Unicode code-point semantics rather than UTF-8 byte offsets. Application code should use `stdlib.string`; direct `str_*`/`split_words` usage is reserved for `stdlib.*` modules. The same applies to raw `regex_*` helpers, which are internal to `stdlib.regex`.

Standard library (Sprout source in `stdlib/prelude.sprout`):

- `Maybe a` (`Just`, `Nothing`)
- `map(fn, list) -> List`
- `fold(fn, init, list) -> value`
- `filter(predicate, list) -> List`
- `split_ints(s: String) -> List Int`
- `Vec a` plus foundational helpers:
  - `vec_empty()`
  - `vec_singleton(value)`
  - `vec_prepend(value, vec)`
  - `vec_append(value, vec)`
  - `vec_length(vec)`
  - `vec_get(index, vec) -> Maybe a`
  - `vec_get_or(index, fallback, vec)`
  - `vec_set(index, value, vec)`
  - `vec_map(f, vec)`
  - `vec_fold(f, init, vec)`
  - `vec_filter(pred, vec)`
  - `vec_filter_map(f, vec)`
  - `vec_any(pred, vec)`
  - `vec_all(pred, vec)`
  - `vec_count(pred, vec)`
  - `vec_slice(start, count, vec)`
  - `vec_reverse(vec)`
  - `vec_sum(vec)`
  - `vec_sum_by(f, vec)`
  - `vec_sort(vec)` where `Ord a` (initial built-in coverage: `Int`, `Bool`, `String`)
  - `vec_sort_by(key, vec)` where `Ord key`
- `Dict v` plus foundational helpers:
  - `dict_empty()`
  - `dict_get(key, dict) -> Maybe v`
  - `dict_set(key, value, dict)`
  - `dict_remove(key, dict)`
  - `dict_keys(dict) -> Vec String`
  - `dict_values(dict) -> Vec v`
  - `dict_entries(dict) -> Vec (String, v)`
  - dict literals: `{foo: 1, "bar": 2}`, `{}`
- `Show t`, `Ord t`, `Semigroup t`, `Functor f`, and `Foldable f`
- `to_string(x)` is the default `Show` operation
- `map(f, xs)` is the default `Functor` operation
- `fold(step, init, xs)` is the default `Foldable` operation
- `fmap(f, xs)` remains available as an alias for the underlying `Functor` method
- `foldable_to_vec(xs: f a) -> Vec a` where `Foldable f`
- prelude instances are currently provided for `String`, `List a`, `Vec a`, and `Dict v`
- `left ++ right` works in the default REPL for strings and lists
- `Result e a` with helpers:
  - `after(effect, value)` sequences `effect` and returns `value` as a small
    compatibility convenience for single-step `IO`
  - experimental `do` blocks for `Maybe`/`Result`, `IO`, and mixed `IO` plus inner `Maybe`/`Result` sequencing, for example:
    `do ... x <- mx ... y <- my ... Just((x, y))`
    The intended current model is intentionally narrow: mixed `IO` blocks use
    `<-` to unwrap an inner `Maybe`/`Result` and short-circuit on failure; code
    that needs the whole container should use explicit `match`. This is the
    preferred story for multi-step `IO` and mixed failure-aware flows.
    See `examples/do_notation_demo.sprout` for `Maybe`/`Result` and
    `examples/io_do_demo.sprout` / `examples/io_result_do_demo.sprout` for
    helper-level mixed `IO` plus `Maybe` / `Result` flows handled by a
    `Unit !{IO}` `main`.
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

The current `after(effect, value)` helper in `stdlib/prelude.sprout` is a
small compatibility convenience for single-step `IO` sequencing. It is still
supported, but `do` is the preferred surface for multi-step sequencing and
mixed `IO` plus `Maybe`/`Result` flows.

Experimental compiler helper module (in `stdlib/compiler.sprout`):

- `CompilerSession` with `empty_session()`, `with_import(line, session)`, and
  `with_declaration(line, session)`
- `CompilerReport` as a Sprout-owned snapshot analysis result carrying
  validity, optional primary error text, diagnostics, and symbol inventory
- `session_source(session) -> String`
- snapshot helpers over the existing host analysis bridge:
  `analyze(session)`,
  `check(session)`, `declared_names(session)`, `exported_names(session)`,
  `type_of(session, expr)`, `eval_lines(session, expr)`,
  `symbol_inventory(session)`, `diagnostics(session)`, and
  `instances(session, query)`
- wrapper result types:
  `SymbolInventory`, `Diagnostic`, `InstanceMatches`, and `CompilerReport`

Current call semantics note: ordinary function values support under-application.
Sprout uses nested arrow types for multi-parameter functions, so `f(x)` returns
another function value when `f` still has remaining parameters.

Function-local bindings are available through `where` on `fn` declarations:

```sprout
fn score(n: Int) -> Int =
  x + y
where
  x = n + 1
  y = x * 2
```

This `where` form is intentionally small in v0:

- it applies only to `fn` declarations
- bindings are value bindings only, with no local type annotations
- bindings may use either a single name or tuple destructuring with names / `_`
- bindings are non-recursive and may use only function parameters and earlier local bindings

Math modules — split by numeric type, since Sprout has no overloading and a single
name cannot serve both. Each module therefore uses plain, unprefixed names:

| module | file | type |
|---|---|---|
| `stdlib.math.int` | `stdlib/math/int.sprout` | `Int` |
| `stdlib.math` | `stdlib/math.sprout` | `Double` |

Integer math (`stdlib.math.int`) — this does not add `Float`, `Decimal`, or fixed-width
integer types to v0:

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

Integer math semantics:

- `mod(x, n)` is Euclidean modulo
- when `n > 0`, `mod(x, n)` returns `Just r` with `0 <= r < n`
- when `n <= 0`, `mod(x, n)` returns `Nothing`
- `pow(base, exp)` returns `Nothing` when `exp < 0`
- interpreter `Int` arithmetic currently follows host arbitrary-precision integer behavior
- the current native backend still lowers `Int` to `i64`, so overflow-sensitive results for `abs`, `pow`, `gcd`, and `lcm` are not yet backend-independent outside the backend's current representable range
- this is a v0 implementation limitation, not the intended long-term meaning of `Int`

Double math (`stdlib.math`) — all pure Sprout, **no C builtins**; `Double` is an
experimental extension rather than normative v0:

- `pi -> Double`, `nan -> Double`, `is_nan(x) -> Bool`
- `abs(x) -> Double`, `clamp(x, lo, hi) -> Double`, `lerp(a, b, t) -> Double`
- `floor(x) -> Double`
- `sqrt(x) -> Double`, `cbrt(x) -> Double`
- `exp(x) -> Double`, `ln(x) -> Double`
- `log2(x) -> Double`, `log10(x) -> Double`, `log(x, base) -> Double`
- `pow(x, y) -> Double`
- `sin(x)`, `cos(x)`, `tan(x)`, `atan(x)`, `atan2(y, x)`, `radians(deg)` — all `-> Double`

Double math semantics:

- Out-of-domain arguments give IEEE `NaN` / `±inf` rather than `Maybe` (Rule 2 of
  `docs/math-partiality-v0.md`), detected with `is_nan`. So `sqrt(-4.0)` and `ln(-1.0)`
  are `NaN`, `ln(0.0)` is `-inf`, and a negative `pow` base with a fractional exponent
  is `NaN`.
- `cbrt` is defined on the whole real line — a negative argument is **in** domain
  (`cbrt(-8.0) == -2.0`), unlike `sqrt`.
- `pow(x, y)` follows C99/IEEE F.9.4.4, which differs from Python: `pow(0.0, -1.0)` is
  `+inf` rather than an error, and `pow(x, 0.0)` / `pow(1.0, y)` are `1.0` even when the
  other operand is `NaN`.
- `pow` with an integer exponent is computed by exact binary exponentiation, so
  `pow(t, 4.0)` equals `t*t*t*t` bit for bit.
- Measured accuracy is ~1e-13 relative or better across the full exponent range; see
  `docs/math-transcendental-v0.md` for the per-function figures and
  `bench/results-2026-08-06-math-transcendental.md` for speed against libm.
- `log(x, base)` takes the argument first, matching the `log2`/`log10` shape:
  `log(8.0, 2.0) == 3.0`. Base 1 has no logarithm, so `log(x, 1.0)` is `±inf`.

For module code, prefer:
`import stdlib.math as math` and/or `import stdlib.math.int as imath`,
then call helpers like `imath.mod(...)`, `imath.gcd(...)`, `math.exp(...)`.

Example usage:

```sprout
fn require_large(x: Int) -> Result String Int =
  if x > 10 then Ok(x) else Err("too-small")

fn compute(x: Int) -> Result String Int =
  do
    large <- require_large(x)
    Ok((large * 2) + 1)

fn main() -> Unit !{IO} =
  match argv_get(0) with
  | Nothing -> print("usage: ... <int>")
  | Just raw ->
      match compute(parse_int(raw)) with
      | Ok value -> print(value)
      | Err err -> print(err)
```

Runnable demo (compile to native then pass args directly):
- `mise exec -- just compile-native examples/result_demo.sprout /tmp/result_demo && /tmp/result_demo 21`
- `mise exec -- just compile-native examples/result_demo.sprout /tmp/result_demo && /tmp/result_demo 3`

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
- `serve_n(port, max_connections, handler) -> Unit !{IO}` — accepts up to `max_connections` connections, handling each in its own green task (a slow connection does not block others); joins all handlers before returning

Request params (low-level, lossless — derived on demand from the parsed request, so pure and socket-free). Values are percent/`+`-decoded via `stdlib.url`. `_param` returns the FIRST value for a key (the Go `url.Values.Get` / Werkzeug `MultiDict.get` convention); `_param_all` returns every value; `_pairs` is the ordered, duplicate-preserving source of truth:

- `query_string(req) -> String` — the raw target substring after the first `?`, `""` if none
- `query_pairs(req) -> Vec (String, String)` — every decoded query param, in order
- `query_param(name, req) -> Maybe String`
- `query_param_all(name, req) -> Vec String`
- `form_pairs(req) -> Vec (String, String)` — decoded body params, but only when `Content-Type` is `application/x-www-form-urlencoded` (a charset parameter is allowed); any other content type yields no params
- `form_param(name, req) -> Maybe String`
- `form_param_all(name, req) -> Vec String`

Current experimental scope:

- HTTP/1.1 request line parsing plus header parsing into a `Dict String`
- `Content-Length` request bodies
- query-string and `application/x-www-form-urlencoded` body param access (see above); a merged `param`/`params` bag over both is planned
- `Connection: close` responses only
- sequential request handling per accepted connection
- no keep-alive, chunked transfer encoding for server responses, TLS server support, or concurrent connection handling yet
- no path/route params (e.g. `/users/:id`) yet — routing matches exact paths

URL helpers (in `stdlib/url.sprout`) — percent/query decoding, decoding at the byte level so multi-byte escapes (`%C3%A9` -> `é`) join correctly and validate as UTF-8 once:

- `percent_decode(s) -> Result Utf8Error String` — resolve `%XX` escapes only (path-segment semantics; `+` left literal)
- `query_decode(s) -> Result Utf8Error String` — resolve `%XX` and map `+` to space (`application/x-www-form-urlencoded`)
- `parse_query(s) -> Vec (String, String)` — split on `&`, each segment on the first `=`, decode both sides; preserves duplicate keys and order; drops empty segments and any segment whose key or value fails to decode

JSON stdlib helpers (in `stdlib/json.sprout`):

- `JsonError` / `Json` / `JsonArray` / `JsonObject` ADTs
- `JsonEncode a` plus `encode(value)` for directly encodable values (`Json`, `Int`, `Bool`, `String`)
- `JsonArrayStep` / `JsonObjectStep` traversal ADTs
- builder helpers: `null()`, `bool(value)`, `int(value)`, `string(value)`, `array_from_list(items)`, `object_from_pairs(items)`, `object_from_dict(items)`
- `parse(raw) -> Result JsonError Json`
- `stringify(value: Json) -> String` (compact JSON for the currently representable subset)
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

SCRAM helpers (in `stdlib/scram.sprout`):

- `no_channel_binding() -> String`
- `random_nonce(count: Int) -> Result CryptoError String`
- `client_first_bare(username, nonce) -> String`
- `client_first_message(username, nonce) -> String`
- `parse_server_first(raw) -> Result ScramError ScramServerFirst`
- `client_final_without_proof(channel_binding, server) -> String`
- `client_proof(password, client_first_bare_raw, server, channel_binding) -> Result ScramError String`
- `client_final_message(password, client_first_bare_raw, server, channel_binding) -> Result ScramError String`
- `server_signature(password, client_first_bare_raw, server, channel_binding) -> Result ScramError String`
- `verify_server_final(password, client_first_bare_raw, server, channel_binding, raw) -> Result ScramError Bool`
- `error_message(err) -> String`

The first slice is intentionally generic and SCRAM-SHA-256-focused; protocol-specific auth and wire-message flow should live in external libraries layered on top.

Terminal convenience module (in `stdlib/terminal.sprout`):

- `term_home() -> Unit !{IO}`
- `term_reset_screen() -> Unit !{IO}`
- `term_render_line(row, text) -> Unit !{IO}`
- `term_read_key_once() -> String !{IO}`
- `term_read_line_once() -> Maybe String !{IO}`

These helpers follow the current sequencing style rule: use `do` for
multi-step `IO` and mixed `IO` plus `Maybe`/`Result` flows, and keep
`after(...)` only for trivial single-step convenience.

Collections module (in `stdlib/collections.sprout`):

- compatibility namespace for the foundational collection/typeclass surface now defined in the prelude
- existing imports such as `import stdlib.collections (Vec, Dict, Functor, Foldable, map, fold, vec_append, dict_get)` continue to resolve
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
- `take(raw: String, count: Int) -> String`
- `drop(raw: String, count: Int) -> String`
- `find(raw: String, needle: String) -> Int`
- `starts_with(raw: String, prefix: String) -> Bool`
- `contains(raw: String, needle: String) -> Bool`
- `ends_with(raw: String, suffix: String) -> Bool`
- `char_at(raw: String, index: Int) -> Maybe Char`
- `char_at_or(raw: String, index: Int, fallback: Char) -> Char`
- `string_from_char(ch: Char) -> String`
- `is_ascii_whitespace(ch: Char) -> Bool`
- `is_ascii_digit(ch: Char) -> Bool`
- `is_ascii_alpha(ch: Char) -> Bool`
- `is_ascii_alnum(ch: Char) -> Bool`
- `is_ident_start(ch: Char) -> Bool`
- `is_ident_continue(ch: Char) -> Bool`
- `trim_left(raw: String) -> String`
- `trim_right(raw: String) -> String`
- `trim(raw: String) -> String`
- `is_empty(raw: String) -> Bool`
- `strip_prefix(raw: String, prefix: String) -> Maybe String`
- `strip_suffix(raw: String, suffix: String) -> Maybe String`
- `split_once(raw: String, sep: String) -> Maybe (String, String)`
- `string_chars(raw: String) -> Vec Char`
- `string_lines(raw: String) -> Vec String`
- `string_digits(raw: String) -> Vec Int`

For module code, prefer:
`import stdlib.string as string`
then call helpers like `string.concat(...)` and `string.length(...)`.

Regex module (experimental, in `stdlib/regex.sprout`):

- `compile(pattern: String) -> Result RegexError Regex`
- `is_match(re: Regex, text: String) -> Bool`
- `find_first(re: Regex, text: String) -> Maybe Match`
- `split_first(re: Regex, text: String) -> Maybe (String, String)`
- `replace_all_literal(re: Regex, replacement: String, text: String) -> String`
- `escape(raw: String) -> String`
- `RegexError` distinguishes `RegexInvalidPattern String` from `RegexUnsupportedFeature String`
- `Match(..)` exposes `Match start end` code-point offsets
- Supported regex surface is intentionally small: literals, `.`, `*`, `+`, `?`, grouping, alternation, character classes, anchors, escaped metacharacters, and ASCII shorthands `\d`, `\w`, `\s`
- Deliberately unsupported in this milestone: counted repetition `{m,n}`, non-greedy quantifiers, extended `(?...)` group syntax, and backreferences
- Patterns are ordinary `String` literals, so backslashes must survive Sprout string parsing first; for example, write `"\\\\d+"` in source to pass `\d+` to the regex compiler

For module code, prefer:
`import stdlib.regex as regex`
then call helpers like `regex.compile(...)` and `regex.replace_all_literal(...)`.

Example classification:

- Runnable examples define `main() -> Unit !{IO}` and can be compiled with `just compile-native` or run with `just run` (no program arguments) or `just compile-native` + direct execution (for programs that read `argv_get`).
- Library-style examples expose helpers without `main`; use `just check` for them directly, or import them from another runnable module.
- `examples/sentry_api.sprout` is a library-style module layering Sentry-specific API helpers plus typed issue-summary and issue-detail decoding on top of generic `stdlib.http` + `stdlib.http_client`.
- `examples/sentry_issue_browser_tui.sprout` is a library-style interactive issue browser module with environment-based config loading, list navigation, refresh, and detail rendering.
- `examples/sentry_issue_browser.sprout` is the runnable wrapper around that helper module for `sprout run` and `sprout compile --native`, including HTTPS-backed Sentry API calls in native mode.
- `examples/http_get_cli.sprout` is a runnable CLI example that reads its URL from `argv_get(0)` and prints the response body.
- `examples/text_demo.sprout` is a runnable Unicode-aware text summary CLI showing `Char`, `char_at_or`, `string_from_char`, and code-point `length`.
- `examples/regex_demo.sprout` is a runnable experimental regex demo showing `compile`, `find_first`, `is_match`, `replace_all_literal`, and `escape`, including doubled-backslash regex patterns inside ordinary string literals.
- `examples/string_templates.sprout` is a runnable experimental string-template demo showing templates in both `String` contexts (`string_concat_many` instead of `++` chains) and `StringTemplate` contexts (structured parts passed to a sink).

The stdlib prelude is included automatically when a stdlib root is provided; `just check` and `just run` always include it.

Load HTTP and JSON helpers via imports such as `import stdlib.http (...)`, `import stdlib.http_client (...)`, `import stdlib.json as json`, and `import stdlib.string as string`.

For programs that take program arguments (`argv_get`), use `just compile-native` and then run the binary directly. `just run` does not forward arguments.

- Typecheck a file: `mise exec -- just check examples/fizzbuzz.sprout`
- Run a file (no program arguments): `mise exec -- just run examples/fizzbuzz.sprout`
- Run with program arguments (compile first):
  `mise exec -- just compile-native examples/http_get_cli.sprout /tmp/http_get && /tmp/http_get http://127.0.0.1:8080/`
- Example text demo:
  `mise exec -- just compile-native examples/text_demo.sprout /tmp/text_demo && /tmp/text_demo "zażółć gęślą jaźń"`
- Example regex demo:
  `mise exec -- just compile-native examples/regex_demo.sprout /tmp/regex_demo && /tmp/regex_demo "ticket=AB-42 owner=ada"`
- Example string templates demo:
  `mise exec -- just compile-native examples/string_templates.sprout /tmp/string_templates && /tmp/string_templates Ada 3`
- Sentry issue browser build and run:
  `mise exec -- just compile-native examples/sentry_issue_browser.sprout /tmp/sentry_issue_browser`
  `SENTRY_ORG=your-org SENTRY_PROJECT=your-project SENTRY_TOKEN=token /tmp/sentry_issue_browser`
  Interactive terminals use arrow keys or `j`/`k` to move, `Enter` to open details, `r` to refresh, and `q` to quit. Non-interactive runs fall back to the plain issue list.
- Collections helper demo: `mise exec -- just run examples/collections_demo.sprout`
- Typeclass collections demo (experimental surface area, not normative v0):
  `mise exec -- just run examples/typeclass_functor_foldable_demo.sprout`

## Collections

Quick reference for the main collection types in the prelude and `stdlib`, with the cost of joining two values together. `++` is the surface operator; it desugars to the `Semigroup append` instance method where one exists.

| Type | Append operator | Complexity | Notes |
|------|-----------------|------------|-------|
| `String` | `++` (lowers to `str_concat`) | O(\|left\| + \|right\|) | Allocates a fresh buffer and copies both inputs. Best avoided in hot loops; prefer `string_concat_many(List String)` (one allocation regardless of part count) or a `bytes.Builder` for chunked assembly. |
| `List a` | `++` (lowers to `list_append`) | O(\|left\|) | Right side is shared structurally; only the left spine is copied. Best for prepend-heavy work via `Cons`. Avoid right-folded concatenation (O(n²)); accumulate with `Cons` and reverse once instead. |
| `Vec a` | `++` (Semigroup instance) | O(\|left\| + \|right\|) | Lowers to the `vector_concat` builtin: one fresh `n+m` backing array, both element blocks copied in a single pass (no intermediate cons cells). |
| `Bytes` | `bytes.append` (`bytes_append`) | O(\|left\| + \|right\|) | Allocates a fresh contiguous buffer and copies both inputs. |
| `bytes.Builder` | `bytes.builder_append` | O(chunks\_left + chunks\_right) | Concatenates chunk tables without flattening the bytes themselves; the final `bytes.builder_build` is O(total\_bytes). The right tool for protocol packet assembly and other "many small fragments, one final blob" patterns. |
| `Dict v` | `++` (Semigroup instance) | O(m · log(n + m)) | Persistent: each of `right`'s m entries is folded into `left` via `dict_set`, which is O(log n) copy-on-write on the balanced AVL map (path copy, not a full-array copy). For very large merges, folding into a freshly built dict avoids re-walking the growing left. |

If you find yourself repeatedly appending small fragments to a `String`, reach for `bytes.Builder` (collect fragments as `Bytes`, finalize once) or the `string_concat_many` builtin (one allocation for an arbitrary list of `String`s). String interpolation with `` `pre${x}post` `` desugars to `string_concat_many` automatically.

