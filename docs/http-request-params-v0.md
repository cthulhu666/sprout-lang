# HTTP Request Params (v0) — design

**Status:** experimental. Describes the request-parameter access layer for
`stdlib/http_server.sprout` and the supporting `stdlib/url.sprout` decoder. This is
supporting design material, not normative — `docs/spec-v0.md` remains the source of
truth for the language core. The low-level layer (below) is implemented; the
convenience layer and path params are deferred follow-ups (§7, tracked in
`BACKLOG.md` §2).

## 1. Problem statement

Handlers had no way to read request parameters. `request_path(req)` returns the raw
target *including* any `?query`, and the router discards everything after `?`
(`path_before_query`) purely to match routes — so a handler wanting `?q=foo` had to
hand-split the path *and* percent-decode by hand, and no URL decoder existed anywhere
in stdlib. We want ergonomic, pure accessors for query-string and form-body params,
with correct percent/`+` decoding.

## 2. Goals / non-goals

**Goals**
- Read query-string params and `application/x-www-form-urlencoded` body params.
- Correct decoding: `%XX` (including multi-byte, e.g. `%C3%A9` → `é`) and `+` → space.
- Pure, socket-free accessors derived on demand from the already-parsed request, so
  they are unit-testable via `parse(raw) -> accessor` with zero sockets.
- Lossless: duplicate keys (`?a=1&a=2`) and their order are preserved.
- No new host builtin — implementable entirely in Sprout.

**Non-goals (deferred follow-ups — see §7)**
- A merged `param`/`params` bag over query+form (Sinatra/Rails style).
- First-wins `Dict String` whole-bag projections (`query_params`, etc.).
- Path / route params (`/users/:id`) — needs router pattern-matching surgery.
- `multipart/form-data`, JSON-body binding (that is `stdlib.json` over
  `request_body`), typed/struct extraction (axum/serde style).

## 3. Prior-art survey (primary-source verified)

### 3a. Single accessor vs. whole bag, and multi-value handling

| System | Single accessor (repeated key) | All values | Whole-bag type |
|---|---|---|---|
| Go `net/url` | `Query().Get(k)` → **first**, `""` if absent | index the map | `map[string][]string` |
| Werkzeug / Flask | `args.get(k)` → **first** | `args.getlist(k)` | `MultiDict` (list-backed) |
| Python `urllib` | — | `parse_qs` → `dict[str,list]` | / `parse_qsl` → `list[(k,v)]` |
| Node `querystring` / Express `qs` | value is string **or** array by arity | array on repeat | plain object |
| PHP | last-wins unless `a[]` bracket syntax | `a[]` → array | plain map |

**Conclusion.** The libraries that do not lose data use either a **multimap
(key → list)** (Go, Werkzeug, `parse_qs`) or an **ordered pair list** (`parse_qsl`).
The "scalar-or-array value" family (Node/Express/PHP) relies on **dynamic typing** —
a key's value *type* changes with arity — which a statically typed language cannot
express. So Sprout's lossless representation is the **ordered pair list**
(`Vec (String, String)`), with a first-value accessor and an all-values accessor
derived from it.

### 3b. Naming

| Framework | Query | Form body | Merged bag | Path |
|---|---|---|---|---|
| Sinatra / Rails | `params['x']` | `params['x']` | **`params['x']`** (merged) | `params['x']` |
| Flask / Werkzeug | `request.args` | `request.form` | `request.values` | route converters |
| Express | `req.query` | `req.body` | — | **`req.params`** (path) |
| Go | `URL.Query().Get` | `FormValue` | `Form` | `PathValue` |

**Conclusion.** `params` is overloaded: Sinatra/Rails use it for the *merged* bag,
Express reserves it for *path* params. The low-level layer therefore names accessors
by **source** (`query_*` / `form_*`, per Flask/Express/Go), leaving `param`/`params`
free for the deferred merged bag — and, since path params are deferred, a future
`path_param` avoids the Express collision.

## 4. Design decisions

- **Byte-level decoding.** Each `%XX` names a single *byte*, not a codepoint, so the
  decoder accumulates raw bytes (`stdlib.bytes` builder) and validates the whole
  result as UTF-8 exactly once (`bytes.to_string`). This makes multi-byte sequences
  (`%C3%A9` → `é`) join correctly and returns `Err(Utf8Error)` on invalid UTF-8
  rather than emitting a corrupt string. No `Char→Int` primitive was needed —
  `bytes.get` returns the byte as an `Int`, so hex nibbles are plain arithmetic.
- **`+` is query/form-only.** `query_decode` maps `+` → space; `percent_decode`
  leaves `+` literal (path-segment semantics). Two decoders, kept distinct.
- **Split before decode.** `parse_query` splits on the *literal* `&` and `=` first,
  then decodes each side — so an encoded delimiter inside a value survives
  (`a=1%262` → `("a","1&2")`, not a spurious split). This is the classic hand-rolled-
  parser bug, avoided by ordering.
- **First-wins single accessor.** `_param` returns the first value for a key
  (Go/Werkzeug consensus); `_param_all` returns every value; `_pairs` is the ordered,
  duplicate-preserving source of truth.
- **Form gating.** `form_*` read the body only when the `Content-Type` media type
  (lowercased, `;charset=…` stripped) is `application/x-www-form-urlencoded`; any
  other type yields no params rather than misparsing an arbitrary body.
- **Malformed encoding.** The low-level `percent_decode`/`query_decode` return `Err`
  on a bad escape (`%zz`, truncated/trailing `%`). `parse_query` treats a pair whose
  key or value fails to decode as *dropped* (absent), keeping the pair list clean;
  the raw truth remains available via `query_string`.

## 5. Architecture

`stdlib/url.sprout` (reusable — `http_client` builds query strings too):
- `percent_decode(s) -> Result Utf8Error String` — `%XX` only.
- `query_decode(s) -> Result Utf8Error String` — `%XX` + `+` → space.
- `parse_query(s) -> Vec (String, String)` — split `&`, each on first `=`, decode
  both; preserves duplicates and order; drops empty/undecodable segments.

`stdlib/http_server.sprout` accessors (pure, derived from the parsed request, no
`HttpRequest` type change):
- `query_string`, `query_pairs`, `query_param`, `query_param_all`
- `form_pairs`, `form_param`, `form_param_all`

**Known cost.** The decoder appends one byte at a time to a `bytes` builder, which is
O(n²) in the builder chunk table (see `BACKLOG.md` §2.5, the `bytes_builder_append`
O(1)-amortized item). Fine for query strings (tens of bytes); revisit if a large-body
decoder ever needs it.

## 6. Tests

- `tests/stdlib/test_url_decode.spr` — decoding + `parse_query` edge cases.
- `tests/stdlib/test_query_params.spr` — query accessors over parsed requests.
- `tests/stdlib/test_form_params.spr` — form accessors + Content-Type gating.

## 7. Deferred roadmap (tracked in `BACKLOG.md` §2)

- **Merged `param`/`params` bag** over query+form (precedence: query-first, matching
  Werkzeug's `CombinedMultiDict([args, form])` ordering) — the Sinatra/Rails idiom.
- **First-wins `Dict String` projections** (`query_params`/`form_params`/`params`) —
  the convenient whole-bag view, built on top of `_pairs`.
- **Path / route params** (`/users/:id`) — requires changing `Route` from exact match
  to pattern match, a segment-capturing matcher, and threading captures into dispatch.
