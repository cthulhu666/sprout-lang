# Logging in Sprout (v0 design)

**Status:** experimental stdlib design. Non-normative — `docs/spec-v0.md` is
unchanged by this document. This design covers a general-purpose `stdlib/log`
module, motivated and driven by the HTTP server as the worked example.

This complements `docs/observability-guard-rails.md`, which fixes the *direction*
for compiler-internal logging (a log sink is a capability passed explicitly, not
a global). That doc is scoped to the self-hosted compiler; this one designs the
user-facing logging facility. The two agree on the load-bearing decision
(guard-rail #3): **a logger is a value you thread, never ambient global state.**

---

## 1. Problem statement

Sprout programs have no leveled, structured diagnostic facility. Concretely, in
`examples/http_web_server.sprout` today:

- Startup errors go to **stdout** via `print(...)` — the same stream a program
  uses for real output. A caller piping program output gets diagnostics mixed in.
- There is **no request logging**: nothing records that a request arrived, what
  it was, what status it got, or how long it took.
- There are **no levels** and no way to filter by severity, and **no structured
  fields** — everything is an ad-hoc interpolated string.

## 2. Goals and non-goals

**Goals**

1. Leveled logging: `Debug | Info | Warn | Error`, with a per-logger minimum
   threshold that filters lower levels out cheaply.
2. Structured key–value context (`Vec (String, String)`), enriched immutably
   (`with_field` returns a *new* logger — no mutation, no shared global).
3. A swappable **sink**: stderr by default (keeps stdout clean for program
   output); an in-memory buffer for tests (guard-rail #3 — pass a capture logger
   instead of a no-op one).
4. Wall-clock timestamps on each line (ISO-8601 UTC), plus request-duration
   timing for the HTTP middleware.
5. An HTTP **access-log middleware** as the driving example: `Logger -> handler
   -> handler'`, demonstrating that middleware in Sprout is ordinary
   higher-order composition, not framework machinery.

**Non-goals (this version)**

- A dedicated `!{Log}` effect label. Logging stays `!{IO}`; the guard-rails doc
  explicitly leaves the label choice open, and adding an effect kind is a
  language change out of scope here.
- Asynchronous / buffered / batched log shipping, log rotation, or network
  sinks. The sink is a synchronous `String -> Unit !{IO}`.
- Sub-microsecond precision, monotonic-vs-wall reconciliation, timezone
  handling other than UTC, or locale-aware formatting.
- A general `stdlib/time` date library. This design adds only the minimum time
  surface logging needs; see §4.4 for the graduation note.

## 3. Prior-art survey

The decision — level set, structured context, and how the sink is abstracted —
is one that mainstream logging libraries have converged on. The `slog` row (the
primary anchor) is verified against the Go source (`golang/go`
`api/go1.21.txt`): `LevelDebug=-4, LevelInfo=0, LevelWarn=4, LevelError=8`,
`Handler` as the sink (`New(h Handler)`, `NewTextHandler`/`NewJSONHandler`), and
a `if !Enabled(level) { return }` gate before emit — the exact gate-then-format
shape adopted below. The remaining rows cite each library's stable public level
enum:

| Library | Levels | Structured context | Sink abstraction |
|---|---|---|---|
| Go `log/slog` (stdlib since 1.21) | `Debug Info Warn Error` | `Logger.With(attrs)` returns a **new** logger carrying the attrs | `Handler` interface (`TextHandler`/`JSONHandler`) |
| Rust `log` crate | `Error Warn Info Debug Trace` | key–values via `log!`'s structured fields | pluggable `Log` impl set once via `set_logger` |
| Python `logging` (stdlib) | `DEBUG INFO WARNING ERROR CRITICAL` | `LoggerAdapter` / `extra=` dict | `Handler` + `Formatter` |
| BSD syslog severities | 8 levels `emerg…debug` | none (freeform message) | system daemon |

**Consensus this design adopts:** a small ordered level set; a logger that
carries *context* and is *enriched by producing a new logger* (slog's `With`);
and a *sink abstracted behind one interface* (slog's `Handler`). Sprout's only
divergence is forced by its effect system and functional discipline: the sink is
a plain function value `String -> Unit !{IO}` and the "new logger from With" is
not a convenience but the *only* option — there is no ambient mutable default
logger to reconfigure (unlike Rust's `set_logger` global or Python's root
logger). This is guard-rail #3 made mandatory.

**Level set choice:** four levels (`Debug Info Warn Error`), matching slog
exactly. We omit `Trace` (Rust) and `Critical`/`Fatal` (Python/syslog): `Trace`
overlaps `Debug` for a first cut, and a `Fatal` that exits conflates a logging
concern with control flow — a caller that wants to abort calls `panic` after
logging at `Error`.

## 4. High-level implementation overview

Pieces: a new host builtin (minimal), a new `stdlib/log.sprout` module, a
one-accessor addition to `stdlib/http_server.sprout`, and a thin
`stdlib/http_middleware.sprout` (the access-log middleware). `stdlib.http_server`
itself keeps zero dependency on `stdlib.log`; the middleware module is the opt-in
layer that imports both. (The `stdlib/log` engine + builtin and the middleware +
example wiring landed as two stacked changes.)

### 4.1 The one new builtin — `wall_time_micros`

```
# In stdlib/prelude.sprout:
# Microseconds since the Unix epoch (gettimeofday / CLOCK_REALTIME).
# Wall-clock: subject to NTP steps and manual clock changes — NOT monotonic.
# Use for timestamps; use time_now_micros for elapsed-time measurement.
extern fn wall_time_micros() -> Int !{IO}
```

The runtime already computes this internally (`sprout_now_micros`, a `static`
`gettimeofday` wrapper in `runtime/sprout_runtime.c`). Exposing it is a
non-`static` wrapper + one `APPROVED_BUILTINS` line. **Justification for the
builtin (per Builtin vs Stdlib rules 4–6):** reading the system clock is a
syscall; it cannot be expressed in Sprout, and no composition of existing
builtins (`time_now_micros` is monotonic; `env`/`term`/`process` do not read the
clock) yields wall-clock time. This is a *correctness* requirement (the value is
unobtainable), not a performance one.

**Nothing else becomes a builtin.** The epoch-micros → calendar conversion is
pure arithmetic and stays in Sprout (§4.4). We do **not** add `strftime`.

### 4.2 `stdlib/log.sprout` — types and API

```
export type Level (..) = | Debug | Info | Warn | Error

# A logger is an immutable capability value (guard-rail #3):
#   min level | sink | base context fields (applied to every line)
export type Logger =
  | Logger Level (String -> Unit !{IO}) (Vec (String, String))

# Constructors
export fn stderr_logger(min: Level) -> Logger        # sink = eprint (stderr)
export fn logger_with_sink(min: Level, sink: String -> Unit !{IO}) -> Logger

# Enrichment (returns a NEW logger; the source logger is unchanged)
export fn with_field(k: String, v: String, lg: Logger) -> Logger

# Emit. `log` gates on rank(lvl) >= rank(min), then formats and calls the sink.
export fn log(lg: Logger, lvl: Level, msg: String, fields: Vec (String,String)) -> Unit !{IO}
export fn debug(lg: Logger, msg: String) -> Unit !{IO}
export fn info(lg: Logger, msg: String) -> Unit !{IO}
export fn warn(lg: Logger, msg: String) -> Unit !{IO}
export fn error(lg: Logger, msg: String) -> Unit !{IO}
# _f variants take explicit fields:
export fn info_f(lg: Logger, msg: String, fields: Vec (String,String)) -> Unit !{IO}   # + debug_f/warn_f/error_f
```

**Level ordering** is a private `rank : Level -> Int` (`Debug 0 … Error 3`);
filtering is `rank(lvl) >= rank(min)`. When a line is filtered out, `log`
returns `Unit` without touching the sink and without building the formatted
string (the format work is inside the `if`), so a `Debug`-heavy path under an
`Info` logger pays only a comparison.

**Line format** (default, human-readable, one line per record):

```
2026-07-18T14:03:21Z INFO request method=GET path=/users status=200 dur_us=418
```

`<iso8601-utc> <LEVEL> <msg> <k=v ...>` where the fields are the logger's base
context followed by the call's fields. The timestamp comes from
`wall_time_micros()` (so `log` reads the clock once per emitted line, inside the
level gate). Values are emitted as-is for v0; escaping/quoting of values
containing spaces is a documented follow-up (§9 note).

### 4.3 The sink, and testability

The sink is `String -> Unit !{IO}`.

**Line-termination contract (explicit):** the formatter produces the record with
**no trailing newline**; **the sink owns line termination.** This is forced by
the default sink: `eprint` is `fprintf(stderr, "%s\n", …)` in the runtime — it
*always* appends `\n`, so a formatter that also appended one would double-space
stderr. Consequences a sink author must respect: `stderr_logger` (sink =
`eprint`) is correct with no extra work; a `Ref`-backed capture sink appends
`"\n"` itself (see below); a `term_write`-based sink must append `"\n"` too, or
every record runs onto one line.

`stderr_logger` wires the sink to `eprint`. A test constructs a capture logger
over a `Ref String` (note the explicit `"\n"`, per the contract above):

```
fn capture_logger(buf: Ref String, min: Level) -> Logger !{IO} =
  logger_with_sink(min, \line -> do { cur <- ref_read(buf); ref_write(buf, cur ++ line ++ "\n") })
```

so a test asserts on `buf`'s contents with no stderr and no sockets. This is the
concrete payoff of guard-rail #3.

### 4.4 Timestamp formatting (pure Sprout)

`format_iso8601(micros: Int) -> String` converts epoch-microseconds to
`YYYY-MM-DDThh:mm:ssZ` using the standard civil-from-days algorithm (days since
epoch → Gregorian y/m/d via the well-known integer formulas; seconds-of-day →
h/m/s by division/modulo). Pure, total, no builtin. It is **exported** from
`log.sprout` (so it is unit-testable directly, and callers who want a timestamp
without a whole log record can use it), with a note that it — and a
`wall_now_micros` re-export of the builtin — should graduate to a
`stdlib/time.sprout` module once a second consumer appears (deferred to
`BACKLOG.md`, not speculatively built now). For the same testability reason,
`should_log` and the pure `format_line` record formatter are exported too — the
gate and the format are asserted directly, without the wall clock or a sink.

### 4.5 HTTP integration — the worked example

One small stdlib addition — a status accessor `http_server` currently lacks:

```
# stdlib/http_server.sprout
export fn response_status(resp: HttpServerResponse) -> Int =
  match resp with | HttpServerResponse status _ _ -> status
```

Then the access-log middleware. **As landed** (a refinement over this section's
original "in the example" sketch): it lives in a small `stdlib/http_middleware`
module rather than inline in the example, so its timing/emit logic is
unit-testable (`tests/stdlib/test_http_middleware.spr` drives it over a
`Ref`-backed capture logger). The module imports both `stdlib.http_server` and
`stdlib.log`; `stdlib.http_server` itself stays free of any logging dependency.

```
# stdlib/http_middleware.sprout
import stdlib.http_server (response_status, ...)
import stdlib.log as log

fn with_logging(lg: log.Logger, handler: HttpRequest -> HttpServerResponse !{IO})
              -> HttpRequest -> HttpServerResponse !{IO} =
  \req ->
    do
      start <- time_now_micros()          # monotonic: correct for durations
      resp  <- handler(req)
      fin   <- time_now_micros()
      log.info_f(lg, "request",
        [ ("method", request_method(req)), ("path", request_path(req)),
          ("status", int_to_string(response_status(resp))),
          ("dur_us", int_to_string(fin - start)) ])
      resp

# wiring, in examples/http_web_server.sprout main:
#   lg = log.stderr_logger(log.Info)          # threaded through boot -> serve_crud
#   serve(8082, with_logging(lg, \req -> dispatch(rs, req)))
```

Two clocks, used correctly and deliberately: **monotonic** (`time_now_micros`)
for the *duration* (immune to NTP steps mid-request), **wall-clock**
(`wall_time_micros`, inside `log`) for the line *timestamp*. The example threads
the logger as an explicit capability (like its `store` and templates) and its
startup `print(...)` calls move to `log.error` on stderr.

The server module keeps zero dependency on `stdlib.log`: the server is the
mechanism, `stdlib.http_middleware` is the opt-in policy layer that depends on
both. Verified live — the running example emits one line per request:
`2026-…Z INFO request method=GET path=/users status=200 dur_us=28`.

## 5. Syntax and semantics impact

None. No grammar, parser, or evaluation-order change. `stdlib/log` is ordinary
Sprout using existing constructs (ADTs, `do`, closures, `Ref`).

## 6. Type-system impact

None. The design uses `!{IO}` (already the effect on `eprint`, `time_now_micros`,
and all HTTP handlers) and a first-class function-typed field
(`String -> Unit !{IO}`) inside the `Logger` ADT — both already supported (the
`Route` type already stores an `!{IO}` function).

## 7. Error-message impact

None. No new diagnostics. A miswired sink or a bad field is ordinary Sprout code.

## 8. Compatibility / migration notes

- Purely additive to the stdlib and to the prelude's extern list.
- `wall_time_micros` is a new builtin — requires an `APPROVED_BUILTINS` entry
  with a correctness justification (§4.1).
- **Seed gate (verified empirically — the ack bypass does NOT apply):** adding
  the extern edits `stdlib/prelude.sprout`, so the commit hook fires. Contrary to
  the initial expectation, `just verify-bootstrap-fixed-point` **breaks**: the
  extern-declare emitter (`ir_lowering.lower_extern_decls`) declares *every*
  bundled prelude extern, and `compile_driver` bundles prelude, so a new prelude
  `extern fn` adds one `declare i64 @wall_time_micros()` line to
  `bootstrap/compile_driver.ll`. A prelude extern is therefore NOT a "non-seed"
  edit — it requires a full **`just refresh-seed`** (delete the stale stage-1
  binary first) and staging the regenerated `bootstrap/compile_driver.ll`, not
  the `seed-fp-ack` bypass. No 2-step bootstrap is needed (no parser/compiler-
  source change — the seed diff is purely the additive declare line).
- **Runtime change gates:** touching `runtime/sprout_runtime.c` triggers DoD #11
  (the example canary set must compile *and run*) and the full test suite
  (DoD #5), in addition to the `APPROVED_BUILTINS` gate (DoD #10).
- `examples/http_web_server.sprout` migrates `print(...)` diagnostics to
  `stdlib.log` on stderr and adds the middleware. This is an example change, not
  a semantics change; its observable stdout shrinks (diagnostics leave stdout),
  which is the intended fix.

## 9. Tests added / updated

- `tests/stdlib/test_log.spr` — level filtering (a `Debug` call under an `Info`
  logger produces no output; an `Error` call does); field rendering order
  (base context then call fields); `with_field` returns an independent logger
  (enriching one does not affect the original) — all via a `Ref String` capture
  sink, no stderr.
- `tests/stdlib/test_time_iso8601.spr` — `format_iso8601` against known epoch
  instants (the epoch `0` → `1970-01-01T00:00:00Z`; a leap-year date; a
  post-2000 date; end-of-year rollover). This is the correctness-critical pure
  function and is tested independently of the clock.
- `tests/stdlib/test_http_middleware.spr` — `response_status` returns the
  status; `with_logging` returns the handler's response unchanged and emits one
  INFO record carrying `method`/`path`/`status`/`dur_us`, with the logged status
  reflecting the handler's actual response (200 and 400 cases). Driven over a
  `Ref`-backed capture logger and a parsed request — no sockets, no stderr.
- **Follow-up noted, not blocking:** value-escaping for fields containing spaces
  or `=` (filed to `BACKLOG.md`); a JSON-lines formatter variant (the sink and
  format are already decoupled, so this is a new formatter, not an API change).

## 10. Spec / docs status

- `docs/spec-v0.md`: **unchanged** — logging is experimental stdlib, not core
  language. Explicitly non-normative.
- `README.md`: add `stdlib/log` to the stdlib listing once implemented.
- `BACKLOG.md`: the `stdlib/log` entry and its deferred follow-ups (value
  escaping, JSON formatter, `stdlib/time` graduation) are **filed** under §2
  (Networking and HTTP). Guard-rails item 13's logging sub-point can be checked
  off as *user-facing logging designed* once this lands.
- This document is the design of record for `stdlib/log` v0.
```
