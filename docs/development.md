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
- REPL: `mise exec -- just build-sproutd` once, then `mise exec -- just repl`
- Language server (LSP): `build/sproutd --lsp <stdlib-root>`; see [language-server-roadmap.md](./language-server-roadmap.md) and `editors/intellij` for the JetBrains plugin

**What a green `lint` means.** Most rules need an AST, so a file that does not lex
or parse is reported as `[unparsed]` and exits non-zero rather than passing. A
clean `lint` therefore means "this parsed *and* no rule fired" — without that, an
unchecked file is indistinguishable from a checked one and `just lint` over a tree
cannot be read as "every file was linted".

`fmt` is deliberately different: it is line-based, never parses, and falls back to
per-line formatting when the lexer fails, so it can legitimately report `ok` on a
file that does not parse.

### Platforms

Supported hosts today are **macOS** and **Linux**. The two workflows cover different
slices of that, and it is worth keeping them apart: `ci.yml` runs the full `test` job on
`ubuntu-latest` plus a `macos` job on `macos-latest`, while `release.yml` publishes
binaries for Linux **x86_64 and aarch64** only — there is no released macOS artifact,
and no CI job builds Linux aarch64.

**Windows is a port in progress, not a supported host.** It is parked after milestone W2
with a resume point recorded in [windows-port-v0.md](./windows-port-v0.md). What the
`windows` CI job gates today is compilation and one behavioural check, not a working
toolchain: the golden IR compiles to Windows COFF for x86-64 and ARM64, the runtime
translation units expected to build under MSVC do build, and the WSAPoll poller passes a
self-test over real loopback sockets. Nothing links a Windows executable yet (W5).

Local gates all run the kqueue backend; CI runs epoll + timerfd, and the two diverge in
ways unreachable on macOS. Before pushing a change to `runtime/`, the scheduler, or
`stdlib/net.sprout` / `stdlib/http_server.sprout`, run `mise exec -- just linux-smoke`
(needs a container runtime, which is why it is opt-in).

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


## Compiler driver (CLI)

`build/compile_driver_bin_stage1` **is** the compiler; the `just` recipes above wrap it.
Released binaries are the same driver under a friendlier name
(`sprout-linux-x86_64` / `sprout-linux-aarch64`, built by
`.github/workflows/release.yml` on tag push). There is no separate `sprout` executable.

Drive it directly when you need a phase the recipes do not expose:

| Invocation | Result on stdout |
|---|---|
| `<stdlib-root> <file>...` | compile (default path) |
| `--emit-ir [--debug] <stdlib-root> [--package-root <dir>] <file>...` | LLVM IR text |
| `--phase <p> <stdlib-root> [--package-root <dir>] <file>...` | stop after one phase — see below |
| `--use-ir-codegen <stdlib-root> [--package-root <dir>] <file>` | LLVM IR via typed AST → Sprout-IR |
| `--emit-iface <stdlib-root> <module-name> <file>` | encoded module interface |
| `--check-iface <iface-file>` | interface verification result |

`<stdlib-root>` is a **path**, not a flag — pass the literal `stdlib` directory.
`--phase` accepts `bundle`, `check`, `effects`, `lower`, `recheck`, `scan-info`, and
`dump-qualify`; [debugging.md](./debugging.md) explains what each is for.
`just build-debug` passes `--emit-ir --debug` and then links with `clang -g -O0`. The `--debug` flag itself is currently a **no-op** — both `--emit-ir` arms dispatch identically, and the DWARF comes from `clang -g`; see [debugging.md §Debugging compiled programs](./debugging.md#debugging-compiled-programs-dwarf--lldb).

Two invariants the driver guarantees, both gated by `just diagnostic-stream-smoke`:

- **Diagnostics go to stderr**, because stdout carries the artifact. A diagnostic on
  stdout does not merely look untidy — redirected into a `.ll` file it *becomes* the
  artifact, and the Sprout error resurfaces later as a clang parse error.
- **A failed run exits nonzero**, including a mistyped invocation, so a broken step
  stops a script instead of looking like a successful no-op.

For what the *language* accepts, read [spec-v0.md](./spec-v0.md); it is normative and
current, and this document deliberately does not duplicate it.

## Runtime surface notes

TCP server and typed TCP client builtins are available in native builds
(`just compile-native`); client payloads are `Bytes` for raw protocol data.
Application code should prefer the typed `stdlib.net` wrapper API over bare `Int`
socket handles. Raw `bytes_*` primitives are internal to `stdlib.*`; application code
should use `stdlib.bytes`. `to_string` rejects invalid UTF-8 and decoded NUL bytes;
`read_c_string` is the intended helper for null-terminated protocol strings.
`http_request` handles plain `http://`, and `https://` on macOS via the system TLS
stack. Set `SPROUT_HTTP_TLS_DEBUG=1` when running a native binary to emit TLS
handshake/read/write debug lines to stderr.

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
- a **dotted non-stdlib** import (`import myapp.util`) resolves only under an extra
  package root registered with `--package-root <dir>`, passed right after
  `<stdlib-root>`. Without it the import resolves to nothing *silently* — there is no
  error at import time; the symbols never bind and surface later as `Unknown variable`
  at the use site. One root, fixed position — the walking-skeleton surface
  (`docs/packaging-v0.md` §10 phase 2). Gated by `just test-package-resolution`, which
  pins both directions: registered root resolves, unregistered root does not.
- import cycles are rejected
- bare `import x.y.z` introduces a namespace qualifier using the last path segment (`z.symbol`)
- `import x.y.z as alias` introduces `alias.symbol`
- `import x.y.z (name1, name2)` imports only those names unqualified
- importing `export type Name` exposes the type name only
- importing `export type Name(..)` also exposes the type's constructors for pattern matches and calls
- top-level declarations are internally namespaced by module, so imported modules no longer flatten into one global scope
- a listed name the module does not export is an error, and so is binding one
  unqualified name twice from two modules — including via constructors two `(..)`
  type imports both carry, and including two whole-module imports that share a
  prefix (spec-v0 §3). An `extern fn` is the exception to the first rule: it is
  global once its module is bundled, so listing one is legal and is what bundles
  the module.

Export behavior:

- only explicitly exported top-level declarations are importable
- declarations without `export` are module-private

Commands:

- Typecheck: `mise exec -- just check input.sprout`
- Emit LLVM IR: `mise exec -- just compile input.sprout out.ll`
- Native binary (requires `clang`): `mise exec -- just compile-native input.sprout out_bin`
- Run without args: `mise exec -- just run input.sprout`
- REPL: `mise exec -- just build-sproutd`, then `mise exec -- just repl`
- Formatter/linter:
  - format in place: `mise exec -- just fmt-file your_file.sprout`
  - check formatting only: `mise exec -- just fmt-check-file your_file.sprout`
  - lint: `mise exec -- just lint-file your_file.sprout`
  - format/lint whole repo: `mise exec -- just fmt` / `mise exec -- just lint`
  - current scope: whitespace-aware formatting, comment preservation, trailing-whitespace/tab/final-newline checks
