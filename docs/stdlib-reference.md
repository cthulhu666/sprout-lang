# Sprout Standard Library Reference

The surface of each `stdlib.*` module, and of the Sprout code in the prelude, one section per
module. The host builtins these modules wrap — every `extern fn` — are in
[builtins-reference.md](./builtins-reference.md); language semantics are in
[spec-v0.md](./spec-v0.md).

`just stdlib-reference` checks that every top-level `stdlib/*.sprout` module has a section here.
Modules not yet documented are listed in `scripts/stdlib_reference_gate.sh`.

## Prelude

Standard library (Sprout source in `stdlib/prelude.sprout`):

- `Maybe a` (`Just`, `Nothing`)
- `map(fn, list) -> List`
- `fold(fn, init, list) -> value`
- `filter(predicate, xs) -> c a` (`where Filterable c` — `List` or `Vec`, container preserved)
- `filter_map(f, xs) -> c b` (`where Filterable c` — drop and transform in one pass)
- `partition(predicate, xs) -> (c a, c a)` (`where Filterable c` — matches, then non-matches)
- `any(predicate, xs) -> Bool` / `all(predicate, xs) -> Bool` (`where Foldable c`)
- `find(predicate, xs) -> Maybe a` / `find_map(f, xs) -> Maybe b` (`where Foldable c`)
- `count(predicate, xs) -> Int` (`where Foldable c`)
- `member(x, xs) -> Bool` (`where Foldable c, Eq a`)
- `list_filter(predicate, list) -> List`, `list_filter_map`, `list_partition`
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
  - `dict_entries_with_prefix(prefix, dict) -> Vec (String, v)` — the entries whose key starts
    with `prefix`, in key order; O((log n + k) log n) for k matches
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
  - `result_from_maybe(err, m)` turns `Nothing` into `Err(err)`
  - `guard(condition, err)` is `Ok(())` or `Err(err)`, for a check inside `do`
  - `result_pipe(f, r)` aliases `result_and_then` in pipeline style
  - `result_pipe_ok(f, r)` aliases `result_map` in pipeline style
  - `result_pipe_error(f, r)` aliases `result_map_error` in pipeline style
  - `when_ok(f, r)` runs `f` for `Ok` and preserves `r`
  - `when_error(f, r)` runs `f` for `Err` and preserves `r`

The current `after(effect, value)` helper in `stdlib/prelude.sprout` is a
small compatibility convenience for single-step `IO` sequencing. It is still
supported, but `do` is the preferred surface for multi-step sequencing and
mixed `IO` plus `Maybe`/`Result` flows.

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

## stdlib.bytes

Bytes helpers (in `stdlib/bytes.sprout`):

- uses foundational prelude `Maybe` and `Result`
- `Builder` opaque type for efficient packet construction
- `instance Eq Bytes` — structural `==`; O(1) on a length mismatch, else O(|left|) and
  allocation-free. Early-exits on the first differing byte, so it is not constant-time — for
  anything key-derived use `stdlib.crypto.const_time_eq`. It is declared here, not in the
  prelude, so a program gets it only with `stdlib.bytes` in its import graph
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

## stdlib.collections

Collections module (in `stdlib/collections.sprout`):

- compatibility namespace for the foundational collection/typeclass surface now defined in the prelude
- existing imports such as `import stdlib.collections (Vec, Dict, Functor, Foldable, map, fold, vec_append, dict_get)` continue to resolve
- prefer the unqualified prelude surface in standalone code and the default REPL

## stdlib.compiler

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

## stdlib.crypto

Crypto helpers (in `stdlib/crypto.sprout`):

- `sha256(value: Bytes) -> Bytes`
- `hmac_sha256(key: Bytes, message: Bytes) -> Bytes`
- `base64_encode(value: Bytes) -> String`
- `base64_decode(raw: String) -> Result Base64Error Bytes`
- `bytes_xor(left: Bytes, right: Bytes) -> Result BytesOpError Bytes`
- `const_time_eq(left: Bytes, right: Bytes) -> Bool` — equality for key-derived values (an
  HMAC tag, a session token, a challenge). Reads every byte whichever way the answer goes, so
  the time taken depends on the length and not on where the values differ. Use it instead of
  `==` wherever a secret is on either side. A length mismatch is `false`, decided before
  anything is compared, so the length is not hidden
- `random_bytes(count: Int) -> Result CryptoError Bytes` (effectful; reads runtime entropy)

## stdlib.env

Environment module (in `stdlib/env.sprout`):

- `get(name: String) -> Maybe String !{IO}`

`Nothing` means the name is unset. A name set to the **empty string** is
`Just ""`, not `Nothing`, matching POSIX — test the constructor, not emptiness.

## stdlib.fs

Filesystem module (in `stdlib/fs.sprout`):

- `read_text(path: String) -> Result String String !{IO}`
- `write_text(path: String, content: String) -> Result String Unit !{IO}`

Named `*_text` rather than `*_file` because that is the actual contract:
`read_file` validates the whole buffer as UTF-8 before returning and reports a
binary file as `Err`, so this pair cannot read one. `Err` carries a
human-readable message — `strerror(errno)`, a UTF-8 decode reason, or
`"null path"` / `"out of memory"` — and must not be pattern-matched on.

The rest of the module is the **classified** surface, whose error is a matchable
`FsError` rather than a String. Both halves coexist; the pair above is not
deprecated.

- `list_dir(dir) -> Result FsError (List String) !{IO}` — entry names, excluding
  `.` and `..`. **Order is unspecified** (the filesystem's, not sorted).
- `stat(p) -> Result FsError Entry !{IO}` — **follows** a final symlink, like
  Rust's `fs::metadata` and Go's `os.Stat`.
- `symlink_stat(p) -> Result FsError Entry !{IO}` — does **not** follow, like
  `fs::symlink_metadata` / `os.Lstat`. Only this and `read_dir` ever report
  `SymlinkEntry`.
- `read_bytes(p) -> Result FsError Bytes !{IO}` / `write_bytes(p, bytes)` — no
  UTF-8 validation in either direction. The reader for a file that is not text,
  and the only way to put an arbitrary byte sequence into one.
- `make_dir(p)` / `make_dir_all(p)` / `remove(p)` / `remove_dir_all(p)` /
  `rename(from, to)` — all `-> Result FsError Unit !{IO}`. `make_dir_all`
  succeeds when the tree already exists; `remove` on a non-empty directory is
  `FsDirectoryNotEmpty`, and `remove_dir_all` is what recurses.
- `read_dir(dir) -> Result FsError (List (String, Entry)) !{IO}` — names paired
  with metadata, using `symlink_stat`. An entry that disappears between the
  listing and its stat is skipped; every other error propagates.
- `exists(p)` / `is_dir(p)` / `is_file(p)` `-> Bool !{IO}` — convenience over
  `stat`, so they follow symlinks and lose the *reason* for a `false`.

`Entry` is `Entry EntryKind Int Int` — kind, size in bytes, mtime in seconds —
read through `entry_kind` / `entry_size` / `entry_mtime` / `entry_is_dir` /
`entry_is_file`. It carries **no name**: the name of a path is
`stdlib.fs.path.basename`, and a second copy of that rule in the runtime would
drift from it.

`FsError` is a closed ADT — `FsNotFound`, `FsPermissionDenied`,
`FsAlreadyExists`, `FsNotADirectory`, `FsIsADirectory`, `FsDirectoryNotEmpty`,
`FsInvalidPath`, `FsIoError` — so a `match` on it is total, unlike Rust's
`#[non_exhaustive]` `ErrorKind`. Each carries a `"<path>: <reason>"` string for
display; match the **constructor**, not the text. `fs_error_message` extracts it.

## stdlib.fs.path

Path module (in `stdlib/fs/path.sprout`) — **pure**, no builtins, no filesystem
access:

- `is_absolute(p) -> Bool`, `join(left, right) -> String`,
  `split(p) -> List String` (non-empty components)
- `basename(p) -> String`, `dirname(p) -> String` — POSIX `basename(3)` /
  `dirname(3)`, including `basename("") == "."` and `basename("/") == "/"`
- `extension(p) -> Maybe String` — **without** the dot, and `Nothing` for a
  dotfile like `.bashrc`; `stem(p) -> String` is the complement
- `normalize(p) -> String` — Go's `Clean`, applied lexically, so it does not
  resolve symlinks
- `relative_to(base, p) -> Maybe String` — Rust's `strip_prefix`; it never
  synthesises `../..`

Survey and rationale for every edge case: `docs/stdlib-fs-v0.md`.

## stdlib.hex

Hex helpers (in `stdlib/hex.sprout`) — base16, RFC 4648 §8:

- `HexError` variants (`HexOddLength`, `HexBadDigit Int` — the byte offset of the first
  non-hex byte); derives `Eq`, `ToString`
- `encode(value: Bytes) -> String` — lowercase, two digits per byte. O(n)
- `decode(raw: String) -> Result HexError Bytes` — either case, no whitespace or prefix. An
  odd length is reported before any digit is read. O(n log n)

## stdlib.http

HTTP stdlib helpers (in `stdlib/http.sprout`):

- uses foundational prelude `Maybe` and `Result`
- `HttpResponse(status, headers, body)` — `body` is **`Bytes`**, not `String`
- `HttpError` variants (`HttpTimeout`, `HttpNetwork`, `HttpBadStatus`, `HttpDecode`)
- `HttpStatusError` variants (`HttpUnsupportedStatus`)
- `parse_request_line(raw) -> Maybe RequestLine`
- `http_response(status, body) -> Result HttpStatusError String`
- `http_response_body(resp: HttpResponse) -> Bytes`
- `http_response_text(resp: HttpResponse) -> Result Utf8Error String`

A response body is `Bytes` for the same reason a request body is on the server side: an HTTP body is
a byte sequence — a PNG, a gzip stream, a protobuf message — and a Sprout `String` cannot hold one,
being valid UTF-8 and NUL-free by construction ([spec-v0.md](./spec-v0.md)). While it was a `String`
the runtime re-measured the received body with `strlen`, so a body containing `0x00` was **silently
truncated at it and returned as `Ok`** — a fetched PNG arrived as a handful of bytes with no error —
and non-UTF-8 bytes were admitted into a `String` unvalidated, which is precisely the obligation the
spec puts on a builtin constructing a `String` from raw external bytes. Text callers use
`http_response_text`, which returns a `Result`, so the decode failure surfaces where it can be
handled rather than being decided during the read. Follows Go (`http.Response.Body` is an
`io.ReadCloser`) and hyper (a `Body` of `Bytes`).
- `http_ok(body) -> String`
- `http_bad_request() -> String`
- `http_echo_response(raw_request) -> String`

## stdlib.http_client

HTTP client convenience module (in `stdlib/http_client.sprout`):

- `http_get(url, headers, timeout_ms) -> Result HttpError HttpResponse`
- `http_post(url, headers, body, timeout_ms) -> Result HttpError HttpResponse`
- `http_put(url, headers, body, timeout_ms) -> Result HttpError HttpResponse`

## stdlib.http_server

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
- `with_header(name, value, resp) -> HttpServerResponse` — sets `name`, removing every earlier line of it
- `add_header(name, value, resp) -> HttpServerResponse` — adds another `name` line; header lines go out in the order set
- `request_method(req) -> String`
- `request_path(req) -> String`
- `request_version(req) -> String`
- `request_body_bytes(req) -> Bytes` — the body exactly as it arrived, byte for byte; total
- `request_body(req) -> Result Utf8Error String` — the body decoded as UTF-8. A `Result` because an HTTP body is a byte sequence and nothing guarantees it is text (a PNG upload, a protobuf message, or a `Content-Length` cutting a character in half all reach a handler legitimately). Follows Go (`Body io.ReadCloser`), ASGI (`body` is a byte string) and Jakarta Servlet (`getInputStream` binary / `getReader` character) in treating bytes as the primitive. The body is fully **buffered** and capped by `max_body_bytes`, so binary payloads work up to that cap — not large file uploads, which need streaming
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

Request cookies follow the same shape but **not** the same decoding — RFC 6265 gives a cookie
value no percent- or `+`-encoding, so applying the query decoder would corrupt the base64url a
session token usually is. Only a surrounding pair of double quotes comes off, which §4.1.1 makes
syntax (`cookie-octet` excludes `"`). A segment with no `=`, an empty name, or a value carrying a
byte outside `0x20..0x7e` (less `"`, `;` and `\`) is dropped — the opposite of the query layer's
choice, where `?flag` is a real shape. Cookie names are case-sensitive even though the header
name is not. Repeated `Cookie:` lines are joined with `"; "` rather than folded last-wins, which
is what RFC 9113 §8.2.3 requires of anything handing the field to a generic server:

- `cookie_pairs(req) -> Vec (String, String)` — every cookie, in order, duplicates kept
- `request_cookie(name, req) -> Maybe String` — the first value for `name`

Response cookies. A bad name or value cannot be built, so `with_cookie` and `render` never fail
on a cookie; a `;`, CR or LF in a path or domain is rendered as a space
([http-request-params-v0.md](http-request-params-v0.md) §9):

- `CookieName` — an RFC 2616 token, as RFC 6265 `cookie-name` requires; constructor hidden
- `cookie_name(raw) -> Maybe CookieName` — `Nothing` for an empty name, a separator (`=`, `;`, `,`, space, …), control or non-ASCII byte
- `cookie_name_text(name) -> String`
- `CookieValue` — a value with RFC 6265 `cookie-octet` bytes only; constructor hidden
- `cookie_value(raw) -> Maybe CookieValue` — `Nothing` for a space, comma, `"`, `;`, `\`, control or non-ASCII byte
- `cookie_value_of_bytes(data) -> CookieValue` — standard base64; total
- `cookie_value_text(value) -> String`
- `SetCookie` — record: `name`, `value`, `max_age_seconds`, `path`, `domain`, `http_only`, `same_site`, `secure`
- `SameSite` — `SameSiteStrict | SameSiteLax | SameSiteNone`; browsers drop `SameSiteNone` without `secure`
- `cookie(name, value) -> SetCookie` — `Path=/`, `HttpOnly`, `SameSite=Lax`; adjust with record update
- `expired_cookie(name) -> SetCookie` — empty value, `Max-Age=0`; set path and domain as the cookie was set
- `with_cookie(cookie, resp) -> HttpServerResponse` — one `Set-Cookie` line, replacing an earlier one of the same name

Current experimental scope:

- HTTP/1.1 request line parsing plus header parsing into a `Dict String`
- `Content-Length` request bodies
- query-string and `application/x-www-form-urlencoded` body param access (see above); a merged `param`/`params` bag over both is planned
- `Connection: close` responses only
- sequential request handling per accepted connection
- no keep-alive, chunked transfer encoding for server responses, TLS server support, or concurrent connection handling yet
- no path/route params (e.g. `/users/:id`) yet — routing matches exact paths

Request framing is strict, because a parser that disagrees with the proxy in front of it is a
request-smuggling primitive rather than a lenient convenience. Four rules, each answering 400 (or 501
where noted) instead of guessing:

- **CRLF only.** Header lines are split on `\r\n`. A bare LF or bare CR left inside a line is
  rejected. RFC 9112 §2.2 permits a recipient to accept a lone LF, but the block terminator is
  matched strictly as `\r\n\r\n`, so accepting it in one place and not the other would let this
  server and an intermediary frame different requests from the same bytes. §2.2's bare-CR rule is a
  MUST ("consider that element to be invalid or replace each bare CR with SP") — silently dropping
  the CR, which is what an earlier version did, is neither.
- **`Content-Length` repeats must agree.** Differing values are invalid framing (RFC 9112 §6.3);
  identical repeats are folded, which the RFC explicitly allows.
- **One `Host`.** RFC 9112 §3.2 requires 400 for more than one `Host` field line.
  (A repeated `Cookie` is neither refused nor folded but *joined* — see the cookie accessors above.
  It is not a framing rule, so it is not one of the four, but it is the third repeat handled by
  name.)
- **`Transfer-Encoding` is refused with 501.** No transfer coding is implemented, and consulting it
  *before* `Content-Length` is what stops a `chunked` request from silently framing as an empty body
  (and a CL+TE request from falling back to the `Content-Length`). RFC 9112 §6.1. Decoding needs the
  streaming read path filed in `BACKLOG.md` §2.

Every other repeated header still folds last-wins, so `Cookie` sent as several lines collapses to the
last one. That is a known limitation awaiting a list-valued header API, not a framing hazard.

On the response side, CR and LF in a header **name or value** are replaced with spaces before the
header reaches the wire (Go's `headerNewlineToSpace`). Without it, a handler putting request-derived
text into a header — `with_header("x-lang", query_param_or("lang", req), ok(page))`, with
`url.query_decode` resolving `%0d%0a` into real CR LF — lets the client inject headers or terminate
the header block early and supply its own body. Values are preserved, only flattened: replacing keeps
the caller's text, whereas deleting would splice `en\r\nde` into the token `ende`.

## stdlib.json

JSON stdlib helpers (in `stdlib/json.sprout`):

- `JsonError` / `Json` / `JsonArray` / `JsonObject` ADTs
- `JsonEncode a` plus `encode(value)` for directly encodable values (`Json`, `Int`, `Bool`, `String`)
- `JsonArrayStep` / `JsonObjectStep` traversal ADTs
- builder helpers: `null`, `bool(value)`, `int(value)`, `string(value)`, `array_from_list(items)`, `object_from_pairs(items)`, `object_from_dict(items)`
- `parse(raw) -> Result JsonError Json`
- `stringify(value: Json) -> Result JsonError String` (compact JSON). Returns `Err(JsonNonFinite x)`
  when the tree contains a NaN or an infinity: RFC 8259 §6 has no syntax for either, so a writer
  must choose between refusing and inventing a stand-in, and every stand-in (`null`, a quoted
  `"NaN"`) comes back a different `Json` constructor than went in.
- `json_error_message(err: JsonError) -> String` — render any `JsonError` without matching on the
  variant set
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

## stdlib.math and stdlib.math.int

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
- `Int` is *specified* as a mathematical integer, but the only backend lowers it to machine `i64`, so a value outside `[-2^63, 2^63-1]` cannot be produced
- `+`, `-`, `*` and unary negation **panic** on overflow with a source-located message rather than wrapping (spec §8.4); `abs`, `pow`, `gcd` and `lcm` panic on the inputs whose results do not fit, rather than returning a silently wrong one
- `BigInt` (`stdlib.math.bigint`, `docs/bigint-v0.md`) is the escape hatch for values that do not fit 64 bits

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
- `asin(x) -> Double`, `acos(x) -> Double`

Double math semantics:

- Out-of-domain arguments give IEEE `NaN` / `±inf` rather than `Maybe` (Rule 2 of
  `docs/math-partiality-v0.md`), detected with `is_nan`. So `sqrt(-4.0)` and `ln(-1.0)`
  are `NaN`, `ln(0.0)` is `-inf`, and a negative `pow` base with a fractional exponent
  is `NaN`.
- `cbrt` is defined on the whole real line — a negative argument is **in** domain
  (`cbrt(-8.0) == -2.0`), unlike `sqrt`.
- `asin`/`acos` are the Rule-2 inverse trig pair: `abs(x) > 1` is out of domain and gives
  `NaN`, never a clamped edge value. Both meet POSIX's range guarantee — `asin` returns
  within `[-pi/2, pi/2]`, `acos` within `[0, pi]` — with the endpoints *exact*:
  `acos(1.0)` is `+0.0`, `acos(-1.0)` is `pi`, `asin(±1.0)` is `±pi/2`, and `asin(±0.0)`
  keeps the sign of its zero. This matters for the common `acos(dot(u, v))` on unit
  vectors, where parallel inputs land on exactly `1.0`.
- `pow(x, y)` follows C99/IEEE F.9.4.4, which differs from Python: `pow(0.0, -1.0)` is
  `+inf` rather than an error, and `pow(x, 0.0)` / `pow(1.0, y)` are `1.0` even when the
  other operand is `NaN`.
- `pow` with an integer exponent is computed by binary exponentiation rather than through
  `exp`/`ln`, so it avoids that path's truncation error. It is *exact* when every
  intermediate product is exactly representable (e.g. `pow(5772.0, 4.0)`), and within
  about an ulp otherwise — it is **not** a guarantee of equality with `t*t*t*t`, whose
  left-to-right multiplication order rounds a different number of times.
- **Accuracy is not uniform.** `sqrt`, `cbrt`, `exp`, `ln`, `log2`, `log10` and `log` are
  ~1e-14 relative across the whole exponent range; `pow` with a fractional exponent is
  ~1e-13 (it composes `exp` and `ln`, inheriting both); the **trigonometric** functions
  functions are not one group. `sin` and `cos` are ~2e-8 absolute, their series being
  truncated for transform-scale use. `atan`/`atan2` are 1.6e-11 over the whole line and
  8e-16 on `[-1, 1]`. `asin`/`acos` are ~2e-15 absolute and ~5e-16 relative, because they
  only ever drive `atan` over `[-1, 1]` where it is at its best. **`tan` has no single
  figure**: it is `sin/cos`, so `cos`'s absolute error becomes an unbounded relative error
  as `cos → 0` — measured 4.5e-5 relative at 5e-4 from `pi/2`, 0.31 at 5e-8, 0.98 at 5e-10.
  Near a pole `tan` returns a large, plausible, arbitrarily wrong number with nothing to
  signal it, so bound your *distance* from `pi/2` rather than just avoiding the pole
  itself. Do not size a tolerance for one function from another's figure. Measurements:
  `docs/math-transcendental-v0.md`; speed against libm:
  `bench/results-2026-08-06-math-transcendental.md`.
- `log(x, base)` takes the argument first, matching the `log2`/`log10` shape:
  `log(8.0, 2.0) == 3.0`. Base 1 has no logarithm, so `log(x, 1.0)` is `±inf`.

For module code, prefer:
`import stdlib.math as math` and/or `import stdlib.math.int as imath`,
then call helpers like `imath.mod(...)`, `imath.gcd(...)`, `math.exp(...)`.

## stdlib.net

TCP client helper types (in `stdlib/net.sprout`):

- uses foundational prelude `Result`
- `TcpError` variants (`TcpInvalidArgument`, `TcpInvalidHandle`, `TcpConnectFailed`, `TcpReadFailed`, `TcpWriteFailed`, `TcpEndOfStream`, `TcpTimeout`, `TcpWouldBlock`, `TcpAcceptFailed`)
- `TcpConnection`
- `TcpListener`
- `connect(host, port) -> Result TcpError TcpConnection`
- `read_exact(conn, count) -> Result TcpError Bytes`
- `write_all(conn, payload) -> Result TcpError Int`
- `read_exact_utf8(conn, count) -> Result TcpError String`
- `write_all_utf8(conn, payload) -> Result TcpError Int`
- `close(conn) -> Unit !{IO}`
- `listen_local(port) -> TcpListener`
- `accept(listener) -> Result TcpError TcpConnection`
- `close_listener(listener) -> Unit !{IO}`
- `tcp_error_message(err) -> String`

Deadline-bounded forms. `*_by` take an ABSOLUTE deadline in monotonic microseconds (as from
`time.now_micros`); `*_timeout` take a relative bound. An absolute deadline is what composes: a
caller spending one budget across several calls passes the same figure to each, where a relative
one silently restarts on every call.

- `read_exact_by(conn, count, deadline_us) -> Result TcpError Bytes` — `Err TcpTimeout` discards the
  bytes already read and leaves the stream mid-message, so the caller must close rather than reuse.
  `Err TcpEndOfStream` when the peer closes before `count` arrives; a short message is not a timeout.
  A negative `count` is `Err TcpInvalidArgument`, as in the `tcp_read_exact` builtin; zero is
  `Ok(empty)`. The first recv always happens and the deadline governs between recvs, so a count one
  recv can satisfy is delivered even past the deadline, and a longer one is cut off at it
- `read_exact_utf8_by(conn, count, deadline_us) -> Result TcpError String` — `count` is BYTES, and the
  decode runs after reassembly, so a multibyte character split across arrivals is handled. A decode
  failure is `Err TcpReadFailed`, never `TcpTimeout`
- `read_avail_timeout(conn, timeout_ms) -> Result TcpError Bytes` — whatever has arrived, for a
  delimited protocol that cannot name a byte count. `timeout_ms <= 0` polls once without parking
- `write_all_by(conn, payload, deadline_us) -> Result TcpError Int`
- `write_all_utf8_by(conn, payload, deadline_us) -> Result TcpError Int`
- `write_all_timeout(conn, payload, idle_ms) -> Result TcpError Int` — an IDLE bound re-armed on
  every accepted byte, so it bounds a peer that stopped reading, not the total
- `write_all_utf8_timeout(conn, payload, idle_ms) -> Result TcpError Int`

`TcpConnection` and `TcpListener` are now exported as opaque handle types; application code can use the types but cannot forge the underlying constructors outside `stdlib.net`.

## stdlib.regex

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

## stdlib.scram

SCRAM helpers (in `stdlib/scram.sprout`):

- `no_channel_binding: String`
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

## stdlib.string

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
- `is_ascii_lower(ch: Char) -> Bool` — case-sensitive, which `is_ascii_alpha` cannot express
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

## stdlib.terminal

Terminal convenience module (in `stdlib/terminal.sprout`):

- `term_home() -> Unit !{IO}`
- `term_reset_screen() -> Unit !{IO}`
- `term_render_line(row, text) -> Unit !{IO}`
- `term_read_key_once() -> String !{IO}`
- `term_read_line_once() -> Maybe String !{IO}`

Session surface, for a UI rather than a prompt:

- `raw_enter() -> Unit !{IO}` / `raw_exit() -> Unit !{IO}`
- `size() -> TermSize !{IO}`, with `size_rows(s) -> Int` / `size_cols(s) -> Int`
- `read_avail(max, timeout_ms) -> TermInput !{IO}`

`TermInput` is total by construction — `TermBytes Bytes`, `TermIdle`,
`TermResized`, `TermEof`, `TermFailed String` — so every outcome the descriptor
can produce has a constructor and a caller cannot forget one. `TermIdle` is not
an error: it is how a UI gets its frame tick. See the `term_*` entries in
[builtins-reference.md](./builtins-reference.md) for the constraints (one parked
reader; resize reported on the next call).

These helpers follow the current sequencing style rule: use `do` for
multi-step `IO` and mixed `IO` plus `Maybe`/`Result` flows, and keep
`after(...)` only for trivial single-step convenience.

## stdlib.time

Time module (in `stdlib/time.sprout`) — two clocks that are **not**
interchangeable:

- `now_micros() -> Int !{IO}` — CLOCK_MONOTONIC. Unspecified epoch; only
  *differences* are meaningful. Use for elapsed time, timeouts, benchmarks.
- `wall_micros() -> Int !{IO}` — CLOCK_REALTIME, microseconds since the Unix
  epoch. Use for timestamps and civil-time rendering. **Not monotonic**: NTP
  steps can move it backwards, so never subtract two of these to measure a
  duration.

## stdlib.url

URL helpers (in `stdlib/url.sprout`) — percent/query decoding, decoding at the byte level so multi-byte escapes (`%C3%A9` -> `é`) join correctly and validate as UTF-8 once:

- `percent_decode(s) -> Result Utf8Error String` — resolve `%XX` escapes only (path-segment semantics; `+` left literal)
- `query_decode(s) -> Result Utf8Error String` — resolve `%XX` and map `+` to space (`application/x-www-form-urlencoded`)
- `parse_query(s) -> Vec (String, String)` — split on `&`, each segment on the first `=`, decode both sides; preserves duplicate keys and order; drops empty segments and any segment whose key or value fails to decode

## stdlib.uuid

UUID helpers (in `stdlib/uuid.sprout`) — RFC 9562:

- `Uuid` opaque type, always held lowercase 8-4-4-4-12; derives `Eq`, `Ord`.
  `instance ToString Uuid` gives that text, not `Uuid(…)`
- `parse(raw: String) -> Maybe Uuid` — either case; checks the shape only, so the Nil and Max
  ids parse and no version is required. Braces, a `urn:uuid:` prefix and missing dashes are
  rejected
- `v4_from_bytes(random: Bytes) -> Maybe Uuid` — exactly 16 bytes; sets the version and
  variant bits. Pure, so a test can pass fixed bytes
- `v4() -> Result CryptoError Uuid` (effectful; `random_bytes(16)` into `v4_from_bytes`)

## Collection costs and `MutVec`

Quick reference for the main collection types in the prelude and `stdlib`, with the cost of joining two values together. `++` is the surface operator; it desugars to the `Semigroup append` instance method where one exists.

| Type | Append operator | Complexity | Notes |
|------|-----------------|------------|-------|
| `String` | `++` (lowers to `str_concat`) | O(\|left\| + \|right\|) | Allocates a fresh buffer and copies both inputs. Best avoided in hot loops; prefer `string_concat_many(List String)` (one allocation regardless of part count) or a `bytes.Builder` for chunked assembly. |
| `List a` | `++` (lowers to `list_append`) | O(\|left\|) | Right side is shared structurally; the left spine is copied twice (reversed, then unreversed onto `right`), so 2\|left\| cells. Best for prepend-heavy work via `Cons`. Avoid right-folded concatenation (O(n²)); accumulate with `Cons` and reverse once instead. |
| `Vec a` | `++` (Semigroup instance) | O(\|left\| + \|right\|) | Lowers to the `vector_concat` builtin: one fresh `n+m` backing array, both element blocks copied in a single pass (no intermediate cons cells). |
| `Bytes` | `bytes.append` (`bytes_append`) | O(\|left\| + \|right\|) | Allocates a fresh contiguous buffer and copies both inputs. |
| `bytes.Builder` | `bytes.builder_append` | O(chunks\_left + chunks\_right) | Concatenates chunk tables without flattening the bytes themselves; the final `bytes.builder_build` is O(total\_bytes). The right tool for protocol packet assembly and other "many small fragments, one final blob" patterns. |
| `Dict v` | `++` (Semigroup instance) | O(m · log(n + m)) | Persistent: each of `right`'s m entries is folded into `left` via `dict_set`, which is O(log n) copy-on-write on the balanced AVL map (path copy, not a full-array copy). For very large merges, folding into a freshly built dict avoids re-walking the growing left. |
| `MutVec a` (`stdlib.mutable`) | `mutvec_push` (one element) | amortised O(1) | **Mutable and in place**, unlike every other row here. Start from `mutvec_empty()` when the size is discovered at runtime; capacity doubles from 8 as it fills. See below. |

### Growing a `MutVec`

`mutvec_push` appends one element, doubling the backing capacity (starting at 8) whenever it fills. Two properties are worth knowing before you rely on it:

- **Growth is in place, so every copy of the handle sees it.** The backing array is reallocated *inside* the existing vector object, not swapped for a fresh one, so a handle already stored in a record or an ECS component column keeps working after a push — including one copied before the growth happened. This is what makes `MutVec` usable as a runtime-sized log rather than something that must guess a capacity up front.
- **`mutvec_len` counts elements, never capacity**, and `mutvec_get` / `mutvec_at` keep bounds-checking against the length. An index that lands in reserved-but-unwritten capacity misses (`Nothing`) or fails loudly, exactly as it did before the push.

Doubling means the peak allocation can be up to 2× the final length. A caller that knows the size and cares about the peak should allocate it directly with `mutvec_new(n, fill)` and write by index. Iteration takes no snapshot — `mutvec_each` / `mutvec_fold` read the length once on entry, so pushing from inside one of them is the caller's problem.

### Shrinking a `MutVec`

`mutvec_remove(v, i)` slides the tail down and returns the removed element; `mutvec_insert(v, i, x)` slides it up. Both are O(n − i). `mutvec_pop` removes the last element, `mutvec_truncate(v, n)` keeps the first `n`, and `mutvec_clear` empties without releasing the capacity, so refilling a cleared vector reuses the buffer rather than reallocating it.

Only the length change is a builtin (`vector_truncate`); the shifts are ordinary Sprout over `vector_get_direct` and `vector_mutset`. If a profile ever shows the shift dominating, that is the point to consider a `memmove` builtin — there is no measured case today.

Three things to know:

- **All five are total.** `remove` and `pop` return `Maybe a` — out of range is `Nothing`, like `mutvec_get`. `insert` returns `Bool`, `false` when the index is outside `[0, len]`; note `len` itself is in range, since inserting there is an append. `truncate` takes any `Int`: a negative `n` empties the vector and an `n` at or past the length has no effect. In every out-of-range case the vector is left untouched.
- **`truncate` and `clear` are O(len − n), not O(1).** Shrinking zeroes every slot it drops, so `clear` on a large vector is a full pass over the live region, and truncating to a *small* `n` is the expensive direction. Zeroing is what keeps spare capacity free of stale handles; if you are clearing a large vector every iteration of a loop, that pass is the cost to weigh.
- **Shrinking is in place**, like growth, so every copy of the handle sees the new length.

If you find yourself repeatedly appending small fragments to a `String`, reach for `bytes.Builder` (collect fragments as `Bytes`, finalize once) or the `string_concat_many` builtin (one allocation for an arbitrary list of `String`s). String interpolation with `` `pre${x}post` `` desugars to `string_concat_many` automatically.

