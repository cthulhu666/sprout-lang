# Sprout Builtins Reference

Host-implemented runtime builtins and their effect annotations. Only a small
subset is the default implicit surface for ordinary modules; the rest sit behind
`stdlib.*` modules (`stdlib.terminal`, `stdlib.compiler`, `stdlib.bytes`, …).

For language semantics see [spec-v0.md](./spec-v0.md); for build/toolchain see
[development.md](./development.md).

For the `stdlib.*` modules that wrap these builtins see
[stdlib-reference.md](./stdlib-reference.md).

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
  a null vector), and they are pure too.
  Normative in spec §6; survey in `docs/effect-enforcement-v0.md` §6.

`!{IO}` builtins in modules — imported explicitly, then called by bare name or
through the module's wrapper API:

| builtin | module | preferred call |
|---|---|---|
| `read_file(path) -> Result String String` | `stdlib.fs` | `fs.read_text(path)` |
| `write_file(path, content) -> Result String Unit` | `stdlib.fs` | `fs.write_text(path, content)` |
| `fs_*` (7: list_dir, stat_path, read_bytes, write_bytes, make_dir, remove, rename) | `stdlib.fs` | `fs.list_dir(…)`, `fs.stat(…)`, … |
| `env_get(name) -> Maybe String` | `stdlib.env` | `env.get(name)` |
| `time_now_micros() -> Int` | `stdlib.time` | `time.now_micros()` — monotonic, for elapsed time |
| `wall_time_micros() -> Int` | `stdlib.time` | `time.wall_micros()` — realtime, for timestamps |
| `term_*` | `stdlib.terminal` | `terminal.write(…)`, `terminal.clear()`, … |
| `vec_make_filled`, `vector_mutset`, `vector_get_direct`, `vector_push`, `vector_truncate` | `stdlib.mutable` | the `MutVec` API |
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
- `http_request(method: String, url: String, headers: String, body: String, timeout_ms: Int) -> Result HttpError HttpResponse !{IO}` — `timeout_ms` is a **total** request deadline: one budget covering connect, send and the entire response read, reported as `Err HttpTimeout` when it runs out. Total rather than idle follows Go's `http.Client.Timeout` ("the timeout includes connection time, any redirects, and reading the response body") and reqwest's `timeout` ("applied from when the request starts connecting until the response body has finished") — the two established single-knob client APIs. Note the deliberate contrast with `tcp_write_all_timeout` above, which is an **idle** bound on nginx `send_timeout` prior art: that is a server-side per-operation primitive where cutting off a slow-but-reading peer is the failure to avoid, whereas this is a caller saying "give up after N ms". A consequence worth knowing: a peer that keeps dripping bytes cannot extend a request past its deadline, so streaming a large body needs a `timeout_ms` sized for the whole transfer. **The call parks rather than blocking** — sibling green tasks continue to run and timers continue to fire for its whole duration. That includes name resolution: `getaddrinfo` runs on a resolver thread while the task parks ([async-dns-v0.md](async-dns-v0.md)). A numeric host skips the lookup. Past 64 concurrent lookups, or if the request, pipe or thread cannot be created (an fd limit under load is the likely case), the lookup runs on the scheduler thread and blocks it for that one call. **`timeout_ms` does not bound the lookup**: the budget starts before it, but the call waits the lookup out and then spends whatever budget is left, so a slow resolver can hold a request well past its deadline. **`https://` works only on Apple platforms**: elsewhere the call returns `Err (HttpNetwork "https unsupported on this platform")` before opening a socket.
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
- `term_read_avail(max: Int, ms: Int) -> stdlib.terminal.TermInput !{IO}` — up to `max` raw bytes, waiting at most `ms`. **Parks the calling task rather than the OS thread**, so timers, animation and network I/O keep running while a UI waits on the keyboard. `ms <= 0` polls once instead of waiting — it still returns `TermBytes` when input is already queued, and `TermIdle` only when there is none. `max` is capped at **4096** per call, so a large paste arrives over several calls. Returns `Bytes`, not `String`, because a read can land mid-UTF-8-sequence — decoding (CSI parsing, modifiers, mouse, paste, UTF-8 reassembly) belongs in Sprout, where it is testable. Three constraints worth knowing: **exactly one task may read stdin at a time** (a second one fails loudly, whether or not it parks, because both would otherwise race for the same bytes and the loser's read would block the thread); a resize is reported as `TermResized` on the *next* call rather than cutting a park short; and `TermEof` means end of input, which on a terminal held in raw mode a zero-byte read does **not** indicate (`VMIN=0` makes an empty queue read as zero), so an idle terminal answers `TermIdle`.

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
- `str_slice(s: String, start: Int, count: Int) -> String` — O(start + count), not O(|s|) (total: a negative `start` or `count` clamps to empty)
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
- `bytes_slice(value: Bytes, start: Int, count: Int) -> Bytes` (total: a negative `start` or `count` clamps to empty)
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

Low-level runtime notes:

- `Vector` and `vector_*` builtins exist as backend/runtime primitives.
- For module code, `stdlib.collections` remains the stable compatibility import path for collection helpers.
- CLI/module checks reject raw `Vector`/`Map` and `vector_*`/`map_*` usage outside `stdlib.*` modules.
- Runtime failures follow one convention: `runtime error: <message>`, printed verbatim.
  A builtin's own message conventionally leads with its name (`vector_get: null vector`),
  but the runtime adds no label of its own — so a message must not embed the prefix itself.
- `sprout run` surfaces that as `error: runtime error: ...`.
- Native binaries print the same runtime-error message to stderr and exit with status `1`.

