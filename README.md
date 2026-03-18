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
- [Effect System v1 Draft](./docs/effect-system-v1-draft.md)
- [Int Ranges v1 Draft](./docs/int-ranges-v1-draft.md)
- [Sequencing Sugar v1 Draft](./docs/sequencing-sugar-v1-draft.md)
- [Language Design Best Practices (Research Notes)](./docs/language-design-best-practices.md)
- [HM Typechecker Guide (Human-Friendly)](./docs/hm-typechecker.md)

Normative status:

- `docs/spec-v0.md` defines the stable Sprout core for v0.
- Features described elsewhere in this README but not specified in `docs/spec-v0.md`
  are implementation features or experimental extensions.
- In particular, the current module system and typeclass support are implemented
  in the prototype, but are not yet part of normative v0.
- In v0, `IO a` is only a lightweight annotation for effectful APIs; a real
  effect system is deferred to v1.

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

- Top-level `let` bindings must not have type `IO a`.
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
- Start REPL: `mise exec -- python -m sprout.cli repl` (loads prelude by default; interactive mode supports arrow-key editing and persistent history via `~/.sprout_repl_history`)
- Run tests: `mise exec -- just test`
- Emit LLVM IR: `mise exec -- just compile examples/factorial.sprout /tmp/factorial.ll`
- Build native binary (clang): `mise exec -- just compile-native /tmp/prog.sprout /tmp/prog`

## Builtin Helpers (v0)

Runtime builtins (host-implemented):

- `print(x) -> IO Unit`
- `print_int(x: Int) -> Int` (prints and returns `x`, useful for native backend subset)
- `read_lines(path: String) -> List String`
- `env_get(name: String) -> Maybe String`
- `argv_get(index: Int) -> Maybe String` (`0` is the first user-supplied program argument)
- `parse_int(s: String) -> Int`
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
- `map_empty() -> Map a`
- `map_get(m: Map a, key: String) -> Maybe a`
- `map_set(m: Map a, key: String, value: a) -> Map a`
- `map_remove(m: Map a, key: String) -> Map a`
- `map_size(m: Map a) -> Int`
- `tcp_listen(port: Int) -> Int`
- `tcp_accept(listener: Int) -> Int`
- `tcp_read(conn: Int) -> String`
- `tcp_write(conn: Int, payload: String) -> IO Unit`
- `tcp_connect(host: String, port: Int) -> Result stdlib.net.TcpError Int`
- `tcp_read_exact(conn: Int, count: Int) -> Result stdlib.net.TcpError Bytes`
- `tcp_write_all(conn: Int, payload: Bytes) -> Result stdlib.net.TcpError Int`
- `tcp_close(conn: Int) -> IO Unit`
- `tcp_close_listener(listener: Int) -> IO Unit`
- `tcp_echo_serve(port: Int, max_connections: Int) -> IO Unit`
- `http_request(method: String, url: String, headers: String, body: String, timeout_ms: Int) -> Result HttpError HttpResponse`
- `json_parse(raw: String) -> Result stdlib.json.JsonError stdlib.json.Json`
- `json_stringify(value: stdlib.json.Json) -> String`
- `term_clear() -> IO Unit`
- `term_move(row: Int, col: Int) -> IO Unit`
- `term_hide_cursor() -> IO Unit`
- `term_show_cursor() -> IO Unit`
- `term_read_key() -> String` (currently from `SPROUT_TERM_KEY` env var, default `"q"`)
- `term_write(text: String) -> IO Unit`

`IO a` in v0 is annotation-only. It marks APIs that are expected to perform
effects, but Sprout v0 does not yet have a separate effect system, purity
checking, or delayed execution model.

That means the current top-level restriction is narrow: Sprout rejects
top-level `let` bindings whose inferred type is `IO a`, but it does not claim a
general purity proof for every non-`IO` top-level expression in v0.

String/runtime helpers are host-implemented primitives. Application code should use `stdlib.string`; direct `str_*`/`split_words` usage is reserved for `stdlib.*` modules.

Standard library (Sprout source in `stdlib/prelude.sprout`):

- `Maybe a` (`Just`, `Nothing`)
- `map(list, fn) -> List`
- `fold(list, init, fn) -> value`
- `filter(list, predicate) -> List`
- `split_ints(s: String) -> List Int`
- `Vec a` plus foundational helpers:
  - `vec_empty()`
  - `vec_prepend(value, vec)`
  - `vec_append(vec, value)`
  - `vec_length(vec)`
  - `vec_get(vec, index) -> Maybe a`
  - `vec_get_or(vec, index, fallback)`
  - `vec_set(vec, index, value)`
  - `vec_map(vec, f)`
  - `vec_fold(vec, init, f)`
  - `vec_slice(vec, start, count)`
  - `vec_reverse(vec)`
  - `vec_sum(vec)`
  - `vec_sum_by(vec, f)`
- `Dict v` plus foundational helpers:
  - `dict_empty()`
  - `dict_get(dict, key) -> Maybe v`
  - `dict_set(dict, key, value)`
  - `dict_remove(dict, key)`
  - `dict_keys(dict) -> Vec String`
  - `dict_values(dict) -> Vec v`
  - dict literals: `{foo: 1, "bar": 2}`, `{}`
- `Semigroup t`, `Functor f`, and `Foldable f`
- `foldable_to_vec(xs: f a) -> Vec a` where `Foldable f`
- prelude instances are currently provided for `String`, `List a`, `Vec a`, and `Dict v`
- `left ++ right` works in the default REPL for strings and lists
- `Result e a` with helpers:
  - forward pipe operator: `value |> f` rewrites to `f(value)`
  - `pipe(value, f)`
  - `result_map(r, f)`
  - `result_map_error(r, f)`
  - `result_and_then(r, f)`
  - `result_with_default(r, fallback)`
  - `result_pipe(r, f)` aliases `result_and_then` in pipeline style
  - `result_pipe_ok(r, f)` aliases `result_map` in pipeline style
  - `result_pipe_error(r, f)` aliases `result_map_error` in pipeline style
  - `when_ok(r, f)` runs `f` for `Ok` and preserves `r`
  - `when_error(r, f)` runs `f` for `Err` and preserves `r`

Current call semantics note: ordinary function calls are exact-arity today.
Sprout uses nested arrow types for multi-parameter functions, but partial
application is not implemented yet as a language/runtime feature.

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
fn show_ok(x: Int) -> IO Unit = print(x)
fn show_error(e: String) -> IO Unit = print(e)

# Nested style
when_error(
  when_ok(
    result_pipe_ok(
      result_pipe(
        Ok(pipe(20, inc)),
        double_if_large
      ),
      inc
    ),
    show_ok
  ),
  show_error
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
- `python3 -m sprout.cli run --with-stdlib examples/result_control_flow_demo.sprout`

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
- `close(conn) -> IO Unit`
- `listen_local(port) -> TcpListener`
- `accept(listener) -> TcpConnection`
- `close_listener(listener) -> IO Unit`
- `tcp_error_message(err) -> String`

Current limitation:
- `TcpConnection(...)` and `TcpListener(...)` constructors are still public because Sprout does not yet support hidden/export-private constructors, so this is safer than bare `Int` but not fully opaque yet.

Bytes helpers (in `stdlib/bytes.sprout`):

- uses foundational prelude `Maybe` and `Result`
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

Terminal convenience module (in `stdlib/terminal.sprout`):

- `term_home() -> IO Unit`
- `term_reset_screen() -> IO Unit`
- `term_render_line(row, text) -> IO Unit`
- `term_read_key_once() -> String`

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
  `python3 -m sprout.cli run examples/collections_utils_demo.sprout`
- Typeclass collections demo (experimental surface area, not normative v0):
  `python3 -m sprout.cli run examples/typeclass_functor_foldable_demo.sprout`

## Native Backend (Early)

`sprout compile` currently supports a small subset:

- top-level `fn`, `type`, and top-level `let` (constant and runtime-initialized),
- `Int`/`Bool`/`String` typed params and returns, plus `IO Unit` function return,
- expressions: literals, vars, arithmetic, comparisons, `&&`, `||`, function composition (`f >> g` means `f(g(x))`), lambdas (`\(x, y) -> ...`), `if`, direct calls, indirect closure calls, recursion,
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
- `export fn ...`, `export type ...`, `export let ...` (top-level only)

Resolution:

- `import stdlib.http` resolves to `stdlib/http.sprout`
- loader checks importing file directory first, then current working directory
- import cycles are rejected
- bare `import x.y.z` introduces a namespace qualifier using the last path segment (`z.symbol`)
- `import x.y.z as alias` introduces `alias.symbol`
- `import x.y.z (name1, name2)` imports only those names unqualified
- importing an exported type also makes its constructors available to pattern matches and calls
- top-level declarations are internally namespaced by module, so imported modules no longer flatten into one global scope

Export behavior:

- only explicitly exported top-level declarations are importable
- declarations without `export` are module-private

Commands:

- LLVM IR output: `python3 -m sprout.cli compile input.sprout -o out.ll`
- Native binary (requires `clang`): `python3 -m sprout.cli compile input.sprout --native -o out_bin`
- REPL: `python3 -m sprout.cli repl`
  - commands: `:type EXPR`, `:t EXPR`, `:help`, `:quit`
  - the prelude is loaded by default, so list literals, dict literals, `split_ints(...)`, `foldable_to_vec(...)`, and `++` work without extra flags
  - add `--with-stdlib` to preload the rest of the modules under `stdlib/`
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
