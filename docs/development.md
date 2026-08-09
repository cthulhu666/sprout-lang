# Sprout — Toolchain, Build & Implementation Status

Practical guide to building and running Sprout, plus the current implementation
surface. For normative language semantics see [spec-v0.md](./spec-v0.md); for the
builtin surface see [builtins-reference.md](./builtins-reference.md).

## Toolchain (mise + just)

This repo uses [`mise`](https://mise.jdx.dev/) to pin the `just` toolchain and [`just`](https://github.com/casey/just) as task runner.

Prerequisites:

- `mise`, for repository-managed `just` and `python`.
- `clang` and `opt` on `PATH` (system-installed, not managed by mise). `opt --passes=verify` runs between IR emission and clang in every build recipe; a missing `opt` fails loudly.
  - macOS: `brew install llvm` then add `$(brew --prefix llvm)/bin` to `PATH` (brew LLVM is keg-only).
  - Linux/Debian: `sudo apt-get install clang-16 llvm-16` then add `/usr/lib/llvm-16/bin` to `PATH`.
- C standard library headers for the active platform. On macOS, install Xcode Command Line Tools or Xcode so `xcrun --show-sdk-path` works; on Linux, install the distro C development package such as `build-essential` or equivalent.
- A pre-built `compile_driver_bin_stage1` binary (bootstrap from the committed seed with `just bootstrap-from-seed`, or build from a pre-existing stage-0 binary with `just build-stage1`).

Setup:

1. Install repository-managed tools from `mise.toml`:
   `mise install`
2. Bootstrap the stage-1 compiler binary from the committed platform seed:
   `mise exec -- just bootstrap-from-seed`
3. Run the test suite:
   `mise exec -- just test`

Common tasks:

- Format repo: `mise exec -- just fmt`
- Check repo formatting: `mise exec -- just fmt-check`
- Lint repo: `mise exec -- just lint`
- Format file: `mise exec -- just fmt-file examples/fizzbuzz.sprout`
- Check file formatting: `mise exec -- just fmt-check-file examples/fizzbuzz.sprout`
- Lint file: `mise exec -- just lint-file examples/fizzbuzz.sprout`
- Typecheck file: `mise exec -- just check examples/fizzbuzz.sprout`
- Run file: `mise exec -- just run examples/fizzbuzz.sprout`
- Run stdlib test suite: `mise exec -- just test`
- Emit LLVM IR: `mise exec -- just compile examples/factorial.sprout /tmp/factorial.ll`
- Build native binary (clang): `mise exec -- just compile-native examples/factorial.sprout /tmp/factorial`
- Build debug binary (DWARF, no optimisation): `mise exec -- just build-debug examples/factorial.sprout /tmp/factorial_dbg`
- Debug binary under lldb: `mise exec -- just debug-run examples/factorial.sprout`
- REPL: not yet available (the Python-backed REPL has been removed; a native launcher is planned — track progress in BACKLOG.md)

### Native runtime GC environment variables

All variables are read at program startup; invalid values abort with a message.

**Collection policy**

| Variable | Default | Description |
|---|---|---|
| `SPROUT_GC_THRESHOLD` | `4096` | Managed heap node count that triggers a mid-execution collection. Positive integer to override; `off` or `0` to collect only at exit. |
| `SPROUT_DEBUG_GC` | off | Set to `1` / `true` / `yes` to log each GC cycle to stderr: `[sprout gc] cycle=N reason=X threshold=N heap_before=N heap_after=N live=N roots=N marked=N alloc_since_gc=N swept=N elapsed_us=N arena_regions=N overflow_regions=N`. The last two report how many live regions sit inside the reserved arena versus outside it (see below). |

**Region arena** — 1-MiB regions are carved from a contiguous `mmap(PROT_NONE)` *reservation* of
address space (not memory; pages are committed per chunk with `mprotect` on first use). This makes
address→region resolution a shift instead of a binary search, worth ~5–7% on self-hosted
compilation. Objects larger than one region, and any region opened when the arena is unavailable or
exhausted, fall back to `malloc` and the original search — correctness never depends on the arena.
Full rationale and measurements in [gc-arena-lookup-v0.md](gc-arena-lookup-v0.md).

| Variable | Default | Description |
|---|---|---|
| `SPROUT_GC_ARENA_MB` | `4096` | Reserved address space in MiB. Costs no physical memory — RSS is driven by committed chunks, not by this number. Halves on `mmap` failure down to 1 MiB. Set `0` to disable the arena entirely and send every region to `malloc` (the pre-arena behaviour). |

**Adaptive threshold** — after each collection the trigger is re-based on the *live* set:
`threshold = max(live × SPROUT_GC_ADAPT_FACTOR, SPROUT_GC_THRESHOLD)`, capped by
`SPROUT_GC_ADAPT_CAP`. This keeps the heap (hence RSS) proportional to live data: it rises for
genuinely live-heavy heaps, avoiding GC thrash, and falls again when the working set shrinks.
Set `SPROUT_GC_ADAPT_RATIO=0` to disable and freeze the threshold.

Read the factor as a **garbage budget**: `(factor − 1) × live` objects of garbage are tolerated
before the next collection. Raising it trades RSS for time, and only for programs whose live set
exceeds `SPROUT_GC_THRESHOLD / factor` — smaller programs sit on the floor and are unaffected.

| Variable | Default | Description |
|---|---|---|
| `SPROUT_GC_ADAPT_RATIO` | `0.2` | On/off switch: any value > 0 enables the adaptive threshold, `0` disables it. **No longer a ratio** — it named the swept fraction under the old only-ever-grow policy, which no longer exists; intermediate values are accepted for compatibility but behave like the default. |
| `SPROUT_GC_ADAPT_FACTOR` | `3.0` | Float > 1. Multiplier on the live set. `3.0` is the measured knee on self-hosted compilation (−14% to −19% wall for +13% to +18% peak RSS vs `2.0`); `4.0` gives −29%/+38%, a worse-than-1:1 trade. Lower it toward `2.0` for memory-constrained workloads. |
| `SPROUT_GC_ADAPT_CAP` | `0` (no cap) | Non-negative integer. Maximum threshold value; `0` = unbounded. **Set a cap for long-lived or memory-constrained workloads** to prevent unbounded RSS growth. |

**Livelock detection** — warns or aborts when consecutive GC cycles free almost nothing (working set fills the heap).

| Variable | Default | Description |
|---|---|---|
| `SPROUT_GC_LIVELOCK_RATIO` | `0.05` | Float in `[0, 1]`. A cycle is "bad" when the fraction of heap freed is below this value. |
| `SPROUT_GC_LIVELOCK_CYCLES` | `1000` | Consecutive bad cycles before triggering the action. |
| `SPROUT_GC_LIVELOCK_ACTION` | `warn` | `off`/`0`, `warn`/`1`, or `abort`/`2`. `warn` prints one diagnostic; `abort` also calls `abort()`. |

Tip: `SPROUT_GC_LIVELOCK_ACTION=abort SPROUT_DEBUG_GC=1` turns an infinite GC thrash into a fast diagnosable crash with a full cycle log.


## Native backend (current subset)

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
`http_request` is available in native mode for plain `http://` requests, and also supports `https://` on macOS via the system TLS stack.
Set `SPROUT_HTTP_TLS_DEBUG=1` when running a native binary to emit TLS handshake/read/write debug lines to stderr.

The native server (`stdlib.http_server` / `stdlib.net`) spawns each accepted connection as a fire-and-forget green task (Layer-0 concurrency), so a slow connection does not block others — handlers interleave at their socket-I/O park points on a single OS thread. `serve_n`'s `max_connections` bounds the number of connections *accepted*; its enclosing `with_scope` joins all spawned handlers before returning.

## Modules (experimental)

The prototype supports file-based modules with top-of-file headers. This module
system is not yet part of normative v0; treat it as implementation status.

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

- Typecheck: `mise exec -- just check input.sprout`
- Emit LLVM IR: `mise exec -- just compile input.sprout out.ll`
- Native binary (requires `clang`): `mise exec -- just compile-native input.sprout out_bin`
- Run without args: `mise exec -- just run input.sprout`
- REPL: not yet available (planned; see BACKLOG.md)
- Formatter/linter:
  - format in place: `mise exec -- just fmt-file your_file.sprout`
  - check formatting only: `mise exec -- just fmt-check-file your_file.sprout`
  - lint: `mise exec -- just lint-file your_file.sprout`
  - format/lint whole repo: `mise exec -- just fmt` / `mise exec -- just lint`
  - current scope: whitespace-aware formatting, comment preservation, trailing-whitespace/tab/final-newline checks
