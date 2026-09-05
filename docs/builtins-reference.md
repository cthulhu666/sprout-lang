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

### Where a builtin lives

Not every builtin is globally reachable. A builtin is declared either in
`stdlib/prelude.sprout` — which every program with a module header receives
automatically — or in the module that owns its surface, which must be imported
explicitly. The placement rule:

> An extern stays in the prelude if the prelude's own code calls it, if it is a
> hardcoded compiler intrinsic, or if it is language core (the `Ref` family, the
> char-indexed `String` operations, the `Vec`/`Dict`/`Set` primitives).
> Otherwise it moves to a module — but only to a **leaf** module, or one its
> consumers would import anyway.

**The leaf qualifier stopped being load-bearing on 2026-08-27.** It existed
because there was no cross-module dead-code elimination: `import stdlib.X` emitted
every definition in `X` and everything `X` imported, whether called or not. Homing
`env_get` in `stdlib.process` was measured at 247 extra lines of IR in a demo that
reads one variable, against 23 for a leaf `stdlib.env`. `dce.elim_unreachable` now
drops every declaration the entry point cannot reach, so an unused import
contributes nothing and neither figure reproduces. See
[spec-v0.md §3](spec-v0.md) — the qualifier is retained in the rule pending a
decision to remove it, which is a design change rather than a correction.

Note that an extern is invisible to the module system — the bundler never
registers extern names, so `export` on one is inert and a moved extern is still
called by **bare name**. Importing its module is what brings it into the build,
not the import list's contents. A consumer that forgets the import gets
`Unknown variable` from the typechecker or an undefined symbol at link time, not
a missing-import diagnostic.

`!{IO}` builtins in the prelude:

- `print(x) -> Unit !{IO}`
- `argv_get(index: Int) -> Maybe String !{IO}` (`0` is the first user-supplied program argument)

Pure builtin in the prelude that nevertheless touches the terminal:

- `panic(msg: String) -> a` — writes `runtime error: <msg>` to stderr and exits 1,
  and is **deliberately not `!{IO}`**. An abort has no continuation, so nothing
  downstream can observe the write; the `a` return type already says it does not
  come back. Callable from a pure function, which is the point — an unreachable
  `| _ -> panic("… (internal error)")` arm does not make its function effectful.

  It is not alone in this, only the most visible: most pure builtins abort the
  same way on a precondition violation (`vector_length` and `vector_get` abort on
  a null vector, `str_slice` on a negative start or length), and they are pure too.
  Normative in spec §6; survey in `docs/effect-enforcement-v0.md` §6.

`!{IO}` builtins in modules — imported explicitly, then called by bare name or
through the module's wrapper API:

| builtin | module | preferred call |
|---|---|---|
| `read_file(path) -> Result String String` | `stdlib.fs` | `fs.read_text(path)` |
| `write_file(path, content) -> Result String Unit` | `stdlib.fs` | `fs.write_text(path, content)` |
| `env_get(name) -> Maybe String` | `stdlib.env` | `env.get(name)` |
| `time_now_micros() -> Int` | `stdlib.time` | `time.now_micros()` — monotonic, for elapsed time |
| `wall_time_micros() -> Int` | `stdlib.time` | `time.wall_micros()` — realtime, for timestamps |
| `term_*` | `stdlib.terminal` | `terminal.write(…)`, `terminal.clear()`, … |
| `vec_make_filled`, `vector_mutset`, `vector_get_direct`, `vector_push` | `stdlib.mutable` | the `MutVec` API |
| `bytes_*` | `stdlib.bytes` | bare name |
| `crypto_*` | `stdlib.crypto` | bare name |
| `regex_*` | `stdlib.regex` | bare name |
| `proc_run_vec`, `proc_run_stdin_vec` | `stdlib.process` | `process.proc_run(…)` |
- **Integer ranges have no builtins.** `IntRange` is an ordinary Sprout ADT declared in
  `stdlib/prelude.sprout` (`IntRange Int Int Int`), and `a..b` lowers to a call to the prelude's
  `range_up`. The five former builtins (`int_range`, `int_range_by`, `int_range_start`,
  `int_range_end`, `int_range_step`) and the `SPROUT_HEAP_RANGE` heap kind behind them were removed
  2026-08-19: the fields are three scalars, no operation on them needs the host, and the only place
  C constructed a range was `regex_find_range` misusing it as a two-Int transport. Listed here as a
  DELIBERATE absence — see `docs/ranges-v0.md` Appendix B, which argued for keeping them and is
  superseded there.
- `tcp_listen(port: Int) -> Int !{IO}`
- `tcp_accept(listener: Int) -> Result stdlib.net.TcpError Int !{IO}` — **recoverable**, not fatal. `EAGAIN` parks; `EINTR`, `ECONNABORTED` and the eight pending-network errnos [accept(2)](https://man7.org/linux/man-pages/man2/accept.2.html) says to *"treat like EAGAIN by retrying"* (`ENETDOWN`, `EPROTO`, `ENOPROTOOPT`, `EHOSTDOWN`, `ENONET`, `EHOSTUNREACH`, `EOPNOTSUPP`, `ENETUNREACH`) are retried inside the builtin, since none is an event a caller could act on. `EMFILE`/`ENFILE` and a full connection table become `Err TcpAcceptExhausted`, which a caller answers by backing off and retrying — the condition is transient, so this must never be fatal. Everything else (`EBADF`, `EINVAL`, `ENOTSOCK`) becomes `Err TcpAcceptFailed`, which does not heal and should stop the loop.
- `tcp_write(conn: Int, payload: String) -> Unit !{IO}`
- `tcp_connect(host: String, port: Int) -> Result stdlib.net.TcpError Int !{IO}`
- `tcp_wait(conn: Int, interest: Int, ms: Int) -> Result stdlib.net.TcpError Int !{IO}` — **readiness only, moving no data.** Parks the calling task until the connection is ready for `interest` (1 = read, 2 = write, mirroring `SPROUT_POLL_READ`/`SPROUT_POLL_WRITE`) or `ms` elapses: `Ok(1)` = ready, `Ok(0)` = the deadline passed. `ms <= 0` reports "not ready" without parking, so a caller enforcing a *total* budget can pass the remaining slice and needs no special case once it is spent. Being interest-parameterised, this is the only park primitive read, write, connect and accept need.
- `tcp_read_some(conn: Int, max_bytes: Int) -> Result stdlib.net.TcpError Bytes !{IO}` — **transfer only, never parking.** One `recv` of at most `max_bytes` (clamped to 64 KiB): `Ok(chunk)` holds at least one byte, `Err TcpWouldBlock` means the kernel had none, `Err TcpEndOfStream` means the peer closed cleanly. Returns **Bytes**, not String: a socket carries arbitrary bytes and a Sprout String may not (see [spec-v0.md](./spec-v0.md) — always valid UTF-8, contains no NUL byte), so decoding is the caller's decision and goes through `bytes.to_string`, which returns a `Result`. Paired with `tcp_wait` by a loop in `stdlib.net`, which is where the timeout, size and rate policies now live.
- `tcp_write_some(conn: Int, payload: Bytes, offset: Int) -> Result stdlib.net.TcpError Int !{IO}` — the write-side twin: one `send` from `offset`, never parking. `Ok(n > 0)` on progress, `Err TcpWouldBlock` when the kernel took nothing, `Ok(0)` only for an already-exhausted payload. `offset` rather than a re-sliced tail is what keeps a Sprout-side write loop linear instead of O(n²) in the payload length.
- `tcp_read_exact(conn: Int, count: Int) -> Result stdlib.net.TcpError Bytes !{IO}`
- `tcp_write_all(conn: Int, payload: Bytes) -> Result stdlib.net.TcpError Int !{IO}`
- `tcp_write_all_timeout(conn: Int, payload: Bytes, idle_ms: Int) -> Result stdlib.net.TcpError Int !{IO}` — `tcp_write_all` bounded by an **idle** deadline: no single stall may exceed `idle_ms`, and any byte the kernel accepts re-arms it, after which it returns `Err TcpTimeout` with the connection **still valid**. Idle rather than total follows nginx `send_timeout` ("the timeout is set only between two successive write operations, not for the transmission of the whole response"), so a slow-but-reading client is never cut off while one that stops reading entirely is. Without it, a client that requests a response larger than the socket buffers and then stops reading parks its handler in `send()` forever and never returns its connection handle — the write-side twin of the unbounded read. `idle_ms <= 0` attempts the write once without parking.
- `tcp_close(conn: Int) -> Unit !{IO}`
- `tcp_close_listener(listener: Int) -> Unit !{IO}`
- `http_request(method: String, url: String, headers: String, body: String, timeout_ms: Int) -> Result HttpError HttpResponse !{IO}` — `timeout_ms` is a **total** request deadline: one budget covering connect, send and the entire response read, reported as `Err HttpTimeout` when it runs out. Total rather than idle follows Go's `http.Client.Timeout` ("the timeout includes connection time, any redirects, and reading the response body") and reqwest's `timeout` ("applied from when the request starts connecting until the response body has finished") — the two established single-knob client APIs. Note the deliberate contrast with `tcp_write_all_timeout` above, which is an **idle** bound on nginx `send_timeout` prior art: that is a server-side per-operation primitive where cutting off a slow-but-reading peer is the failure to avoid, whereas this is a caller saying "give up after N ms". A consequence worth knowing: a peer that keeps dripping bytes cannot extend a request past its deadline, so streaming a large body needs a `timeout_ms` sized for the whole transfer. **The call parks rather than blocking** — sibling green tasks continue to run and timers continue to fire for its whole duration. `getaddrinfo` is the one remaining exception: name resolution still blocks the scheduler (tracked in `BACKLOG.md`).
- `crypto_random_bytes(count: Int) -> Result stdlib.crypto.CryptoError Bytes !{IO}`
- `term_clear() -> Unit !{IO}`
- `term_move(row: Int, col: Int) -> Unit !{IO}`
- `term_hide_cursor() -> Unit !{IO}`
- `term_show_cursor() -> Unit !{IO}`
- `term_read_key() -> String !{IO}` (reads one key from stdin; in TTY mode it reads immediately without waiting for newline). **Enters and leaves raw mode around each keypress**, which bounds what it can decode: it recognises `ESC [ A/B/C/D` and returns the tail bytes of anything longer — a modifier chord, an SGR mouse report, a bracketed paste — as separate fake keypresses. It also **blocks the OS thread**, so a key read starves every other green task. Use the session surface below for anything beyond a prompt; this one is kept for `stdlib.repl` and is unchanged.
- `term_read_line() -> Maybe String !{IO}` (reads one stdin line, trims trailing `\n`/`\r\n`, returns `Nothing` at EOF)
- `term_write(text: String) -> Unit !{IO}`
- `term_raw_enter() -> Unit !{IO}` / `term_raw_exit() -> Unit !{IO}` — hold raw mode for a **session** rather than a keypress: no echo, no line buffering, and ctrl-C, ctrl-S and ctrl-Q delivered as ordinary bytes (`ISIG`/`IXON` off) so a UI can bind them. `OPOST` is off too, so `\n` no longer implies `\r` and `print` is unusable while raw mode is held — send diagnostics to stderr. `term_raw_enter` also installs a `SIGWINCH` handler and an `atexit` restore; the restore is mandatory rather than tidy, because a crash would otherwise strand the user's shell with echo off and no working ctrl-C. A no-op when stdin is not a terminal.
- `term_size() -> stdlib.terminal.TermSize !{IO}` — rows and columns from `TIOCGWINSZ`, falling back to `$LINES`/`$COLUMNS` and then 24x80, so a layout always has finite numbers. Nothing else can answer this: the DSR escape (`ESC[6n`) writes its reply into stdin, where `term_read_key` discards it.
- `term_read_avail(max: Int, ms: Int) -> stdlib.terminal.TermInput !{IO}` — up to `max` raw bytes, waiting at most `ms`. **Parks the calling task rather than the OS thread**, so timers, animation and network I/O keep running while a UI waits on the keyboard; `ms <= 0` polls once and returns `TermIdle`. Returns `Bytes`, not `String`, because a read can land mid-UTF-8-sequence — decoding (CSI parsing, modifiers, mouse, paste, UTF-8 reassembly) belongs in Sprout, where it is testable. Two constraints worth knowing: **exactly one task may be parked on stdin at a time** (a second one fails loudly, because both would wake on readability and the loser's read would block the thread), and a resize is reported as `TermResized` on the *next* call rather than cutting a park short.

Application code should prefer the package surface in `stdlib.terminal`
(`write`, `hide_cursor`, `show_cursor`, `raw_enter`, `raw_exit`, `size`,
`read_avail`, `term_read_key_once`, and related helpers) instead of the raw
`term_*` hooks.

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
- `char_to_str(codepoint: Int) -> String` (note: an Int codepoint, unlike `char_to_string`)
- `char_from_codepoint(cp: Int) -> Char`
- `str_concat(a: String, b: String) -> String`
- `str_len(s: String) -> Int`
- `str_slice(s: String, start: Int, len: Int) -> String`
- `str_char_at(s: String, index: Int) -> Maybe Char`
- `str_find(s: String, needle: String) -> Int` (`-1` when not found)
- `str_starts_with(s: String, prefix: String) -> Bool`
- `str_compare(left: String, right: String) -> Int` (`-1`, `0`, `1`)

The list above is the **char-indexed** core, which stays in the prelude. The
byte-indexed surface and the splitters live in `stdlib.string` and need
`import stdlib.string` — they are then called by bare name, not through a
wrapper, because several sit in per-token and per-byte parse loops:

- `str_byte_len(s: String) -> Int` (O(1), from the CSTR header)
- `str_slice_bytes(s: String, byte_start: Int, byte_len: Int) -> String`
- `str_starts_with_at_byte(s: String, byte: Int, prefix: String) -> Bool`
- `str_split_lines(s: String) -> List String`
- `split_words(s: String) -> List String`

Likewise `double_to_bits` / `double_from_bits` live in `stdlib.math`
(see [spec-v0.md §8.1.1](./spec-v0.md)), and the bitwise intrinsics live in
`stdlib.bits` (see [spec-v0.md §8.1.2](./spec-v0.md) and
[bitwise-int-ops-v0.md](./bitwise-int-ops-v0.md)). Both families are compiler
intrinsics with **no runtime symbol and no `APPROVED_BUILTINS` entry** — each
lowers to a machine instruction, so there is nothing to call:

- `bit_and(a: Int, b: Int) -> Int`, `bit_or`, `bit_xor` (same shape)
- `bit_not(a: Int) -> Int` — flips every bit, so `bit_not(0)` is `-1`
- `bit_shl(x: Int, n: Int) -> Int` — left shift; bits above position 63 are discarded
- `bit_shr(x: Int, n: Int) -> Int` — arithmetic (sign-filling) right shift
- `bit_shr_zf(x: Int, n: Int) -> Int` — logical (zero-fill) right shift

A shift count of `0..63` shifts as expected; `>= 64` saturates; a negative count
panics, and a negative *literal* count is a compile error.
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
- public JSON entrypoints live in `stdlib.json` as `parse(raw)` and `stringify(value)`; both return
  a `Result JsonError _`
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

String/runtime helpers are host-implemented primitives. In the current experimental text slice, `str_len`, `str_slice`, `str_char_at`, and `str_find` use Unicode code-point semantics rather than UTF-8 byte offsets. Application code should use `stdlib.string`; direct `str_*`/`split_words` usage is reserved for `stdlib.*` modules. The same applies to raw `regex_*` helpers, which are internal to `stdlib.regex`. Note that the byte-offset builtins are no longer globally reachable at all — `str_byte_len`, `str_slice_bytes`, `str_starts_with_at_byte`, `str_split_lines` and `split_words` are declared in `stdlib.string`, so reaching one now requires importing that module rather than merely ignoring a convention.

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
- `Int` is *specified* as a mathematical integer, but the only backend lowers it to machine `i64`, so arithmetic wraps (defined two's-complement, not UB)
- overflow-sensitive results for `abs`, `pow`, `gcd`, and `lcm` are therefore silently wrong outside the representable range
- this is a v0 implementation limitation, not the intended long-term meaning of `Int`; whether `+`/`-`/`*` should trap is open (`docs/int-overflow-policy-decision.md`)

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

Session surface, for a UI rather than a prompt:

- `raw_enter() -> Unit !{IO}` / `raw_exit() -> Unit !{IO}`
- `size() -> TermSize !{IO}`, with `size_rows(s) -> Int` / `size_cols(s) -> Int`
- `read_avail(max, timeout_ms) -> TermInput !{IO}`

`TermInput` is total by construction — `TermBytes Bytes`, `TermIdle`,
`TermResized`, `TermEof`, `TermFailed String` — so every outcome the descriptor
can produce has a constructor and a caller cannot forget one. `TermIdle` is not
an error: it is how a UI gets its frame tick. See the `term_*` entries above for
the constraints (one parked reader; resize reported on the next call).

These helpers follow the current sequencing style rule: use `do` for
multi-step `IO` and mixed `IO` plus `Maybe`/`Result` flows, and keep
`after(...)` only for trivial single-step convenience.

Filesystem module (in `stdlib/fs.sprout`):

- `read_text(path: String) -> Result String String !{IO}`
- `write_text(path: String, content: String) -> Result String Unit !{IO}`

Named `*_text` rather than `*_file` because that is the actual contract:
`read_file` validates the whole buffer as UTF-8 before returning and reports a
binary file as `Err`, so this pair cannot read one. `Err` carries a
human-readable message — `strerror(errno)`, a UTF-8 decode reason, or
`"null path"` / `"out of memory"` — and must not be pattern-matched on.

Environment module (in `stdlib/env.sprout`):

- `get(name: String) -> Maybe String !{IO}`

`Nothing` means the name is unset. A name set to the **empty string** is
`Just ""`, not `Nothing`, matching POSIX — test the constructor, not emptiness.

Time module (in `stdlib/time.sprout`) — two clocks that are **not**
interchangeable:

- `now_micros() -> Int !{IO}` — CLOCK_MONOTONIC. Unspecified epoch; only
  *differences* are meaningful. Use for elapsed time, timeouts, benchmarks.
- `wall_micros() -> Int !{IO}` — CLOCK_REALTIME, microseconds since the Unix
  epoch. Use for timestamps and civil-time rendering. **Not monotonic**: NTP
  steps can move it backwards, so never subtract two of these to measure a
  duration.

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
- `examples/sentry_issue_browser.sprout` is the runnable wrapper around that helper module for `just run` and `just compile-native`, including HTTPS-backed Sentry API calls in native mode.
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
| `MutVec a` (`stdlib.mutable`) | `mutvec_push` (one element) | amortised O(1) | **Mutable and in place**, unlike every other row here. Start from `mutvec_empty()` when the size is discovered at runtime; capacity doubles from 8 as it fills. See below. |

### Growing a `MutVec`

`mutvec_push` appends one element, doubling the backing capacity (starting at 8) whenever it fills. Two properties are worth knowing before you rely on it:

- **Growth is in place, so every copy of the handle sees it.** The backing array is reallocated *inside* the existing vector object, not swapped for a fresh one, so a handle already stored in a record or an ECS component column keeps working after a push — including one copied before the growth happened. This is what makes `MutVec` usable as a runtime-sized log rather than something that must guess a capacity up front.
- **`mutvec_len` counts elements, never capacity**, and `mutvec_get` / `mutvec_at` keep bounds-checking against the length. An index that lands in reserved-but-unwritten capacity misses (`Nothing`) or fails loudly, exactly as it did before the push.

Doubling means the peak allocation can be up to 2× the final length. A caller that knows the size and cares about the peak should allocate it directly with `mutvec_new(n, fill)` and write by index. Iteration takes no snapshot — `mutvec_each` / `mutvec_fold` read the length once on entry, so pushing from inside one of them is the caller's problem.

If you find yourself repeatedly appending small fragments to a `String`, reach for `bytes.Builder` (collect fragments as `Bytes`, finalize once) or the `string_concat_many` builtin (one allocation for an arbitrary list of `String`s). String interpolation with `` `pre${x}post` `` desugars to `string_concat_many` automatically.

