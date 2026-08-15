# Windows port — Milestone A: compile *to* Windows

Status: **design, not yet implemented.** Non-normative; `docs/spec-v0.md` remains the normative
source for language semantics. Nothing here changes the language.

Date: 2026-08-15.

## 1. Problem

Sprout cannot produce a Windows executable. `runtime/` is POSIX-only: green threads use
`ucontext`, the readiness poller has kqueue and epoll backends only, I/O uses BSD sockets, and
the crash handler uses `sigaction` + `execinfo`.

The driver is concrete: **uncharted-suns is intended to ship on Steam**, which means a Windows
`.exe`. That game links this repo's runtime directly — its `Justfile:106` compiles
`$SPROUT_ROOT/runtime/*.c` into every graphics build — so the game's Windows port is gated
entirely on the language runtime, not on the game.

### 1.1 The code generator is already portable — this is a runtime project

`stdlib/compiler/ir_lowering.sprout:460` emits `target triple = "unknown-unknown-unknown"` and no
`datalayout`, so the emitted module carries no host assumptions and the backend supplies the
target's own layout. Sprout's uniform ABI (every value is a boxed handle passed as `i64`)
sidesteps the place where Win64 actually diverges from SysV — struct-by-value passing and
register assignment.

Verified by cross-compiling committed golden IR, including the two IR features where "probably
fine" would not be good enough — `musttail` (mutual-recursion TCO) and
`llvm.stacksave`/`llvm.stackrestore`:

```
clang --target=x86_64-pc-windows-gnu   -c tests/golden/ir/examples__astar.sprout.ll       -o /tmp/x.obj
clang --target=aarch64-pc-windows-msvc -c tests/golden/ir/examples__repl_hosted.sprout.ll -o /tmp/a.obj
```

Both exit 0, emitting only `-Woverride-module`. `file /tmp/x.obj` reports
`Intel amd64 COFF object file, not stripped, 7 sections`. Compiling to an object needs no Windows
sysroot, so this is reproducible from a clean checkout on macOS or Linux. *Linking* an executable
does need a sysroot — that is §5, W5. (The arm64 run is evidence of target-neutrality, not a
support claim; ARM64 Windows is a non-goal per §2.)

**Conclusion: zero codegen work. The ~11.3k-line C runtime is the entire job.**

### 1.2 Two structural advantages that already exist

- **GC rooting is an explicit shadow stack** (`sprout_gc_push_i64_root`,
  `runtime/sprout_runtime.c:1628`; see the commentary at `:1713`), not conservative stack
  scanning. No code walks native stacks or spills registers, so the part of a GC port that is
  normally worst does not exist here.
- **The poller is already an abstract interface** — six functions over an opaque per-registration
  token (`runtime/sprout_scheduler.h:71-100`), with two backends already living behind it in
  `runtime/sprout_poll.c` (193 lines total). Windows is a *third backend*, not a redesign.
- The runtime uses no thread-local storage (`grep` for `__thread` / `_Thread_local` finds
  nothing), which removes the classic fiber hazard: a fiber accesses the TLS of whichever thread
  runs it.

## 2. Goals and non-goals

**Goal.** A Windows `.exe` of uncharted-suns, cross-built from macOS/Linux, running the full
green-thread scheduler and graphics stack.

**Non-goals for Milestone A:**

- **Running the *compiler* on Windows.** That is Milestone B: the `justfile` and `scripts/*.sh`
  are bash, the bootstrap-seed flow assumes a POSIX shell, and `mise` provisions the toolchain.
  Realistically B means MSYS2 or WSL rather than a native port. A is a strict prerequisite for B
  and not a smaller version of it — Sprout is self-hosted, so a Windows-native `sproutc` is itself
  a program whose runtime must already be ported.
- **HTTPS on Windows.** TLS is SecureTransport-only today; the non-Apple branch already returns
  `"https unsupported on this platform"` (`runtime/sprout_runtime.c:7676`). Windows inherits an
  existing stub — this is not a new gap being opened.
- **Steamworks integration and depot packaging.** Named in §9, designed later.
- **32-bit and ARM64 Windows.** x86-64 only.

## 3. Prior art

How comparable runtimes solve the two hard items. Every row verified against a primary source;
sources in §11.

### 3.1 Green threads without `ucontext`

Windows has no `ucontext`. The field splits two ways:

| Runtime | Approach |
|---|---|
| **Win32 Fibers** | The OS-provided answer. `ConvertThreadToFiber` turns the calling thread into a fiber; `CreateFiber(stackSize, entry, data)` allocates a new one; `SwitchToFiber` switches; `DeleteFiber` frees. Documented state is *"its stack, a subset of its registers, and the fiber data"* — the same shape `ucontext` saves. |
| **Go** | Does *not* use fibers; the goroutine switch is hand-written assembly per architecture, because Go needs growable stacks and its own preemption, neither of which fibers provide. |

**Decision: Fibers.** Sprout's tasks have fixed-size stacks (`SPROUT_TASK_STACK_BYTES`,
`sprout_scheduler.c:574`) and are cooperatively scheduled with explicit park points — exactly the
case fibers were built for, and exactly the case where Go's reasons for hand-rolled assembly do
not apply. Microsoft's own framing: *"using fibers can make it easier to port applications that
were designed to schedule their own threads."*

### 3.2 Async I/O: completion vs readiness

Sprout's poller interface is **readiness**-shaped (`sprout_poll_add(fd, interest, token)` →
`sprout_poll_wait` hands the token back when the fd is ready). Windows' flagship mechanism is
**completion**-shaped, which is the core mismatch.

| Runtime | Windows mechanism | Model |
|---|---|---|
| **Go** | IOCP — `CreateIoCompletionPort`, `GetQueuedCompletionStatusEx`, `PostQueuedCompletionStatus`; `SetWaitableTimer` + `NtAssociateWaitCompletionPacket` for timers | completion |
| **libuv** | IOCP (*"epoll on Linux, kqueue on OSX and other BSDs, event ports on SunOS, IOCP on Windows"*) | completion |
| **Rust `mio`** | AFD driven through IOCP: opens `\Device\Afd\Mio` via `NtCreateFile`, issues `IOCTL_AFD_POLL` through `NtDeviceIoControlFile`, cancels with `NtCancelIoFileEx`. Its `afd.rs` credits piscisaureus' **wepoll** by commit hash. | completion API synthesizing **readiness** |
| **Winsock `WSAPoll`** | A `poll(2)` analog in the Winsock API itself | **readiness** |

The consensus for high-performance runtimes is IOCP. But all three of those projects need
scalability Sprout does not: they are general-purpose I/O libraries serving tens of thousands of
sockets, whereas Sprout's poller today serves a game's optional debug socket and a modest HTTP
server. The readiness-preserving options are therefore live for us in a way they are not for
libuv.

**Decision: `WSAPoll` first (§4.3).** It is the only option that needs neither a model
translation nor undocumented kernel interfaces.

## 4. Design

### 4.1 Toolchain: develop with mingw-w64, ship with MSVC — write to the MSVC-strict surface

Valve's [Steamworks API documentation](https://partner.steamgames.com/doc/sdk/api) states: *"The
Steamworks API officially supports C++, using Microsoft Visual Studio 2008+ on Microsoft Windows,
GCC 4.6+ and Clang 3.0+ on macOS and SteamOS / Linux."* Windows support is **MSVC-only** as far
as Valve is concerned. Shipping a commercial title on an ABI the platform holder does not support
is an avoidable risk.

Steamworks integration itself, however, is **ABI-neutral for Sprout**. The same page documents
`steam_api_flat.h` — *"a set of 'flat' functions that mirror the interface functions in the SDK.
This is not pure C code, but it does use plain C linkage and calling conventions, so it is easy to
interop with other languages"* — exported from the redistributable DLL. Sprout's FFI is C-only,
so the game must use the flat API whichever toolchain builds it, and flat exports are reachable
by `LoadLibrary`/`GetProcAddress` from any ABI.

So the ABI choice is driven by risk and ergonomics, not by Steamworks:

- **Develop with mingw-w64.** `clang --target=x86_64-w64-mingw32` cross-compiles from macOS with
  a `brew install mingw-w64` sysroot, keeping the day-to-day loop on the machine the work happens
  on.
- **Ship with MSVC**, and gate it in CI from W5 onward so the path never rots.
- **The rule that makes both work: write the Windows backend against pure Win32 + ISO C, never
  against mingw's POSIX shims.** mingw-w64 supplies `unistd.h`, `sys/time.h`, `strings.h` and
  winpthreads; MSVC supplies none of them. Code written to the MSVC-strict surface compiles under
  both; code written to mingw's shims must be rewritten later. **This costs nothing if adopted
  from the first line and is expensive to retrofit** — it is the single highest-leverage
  discipline in the port.

raylib publishes both variants, so the game side splits the same way: raylib 6.0 ships
`raylib-6.0_win64_mingw-w64.zip` and `raylib-6.0_win64_msvc16.zip`.

**LLP64 is a non-issue.** Windows is LLP64 (`long` is 32-bit), but the runtime uses
`long long`/`unsigned long long` throughout. A precise grep finds exactly two bare-`long` uses:
`sprout_runtime.c:6958` (`atol` on a debug delay) and `:7241` (`ftell` for a file size — needs
`_ftelli64` to exceed 2 GB).

### 4.2 Where Windows code lives

**Match the existing precedent: `#ifdef` blocks in place.** `sprout_poll.c` already carries
kqueue and epoll in one file selected by `#ifdef __APPLE__` / `#else`; `sprout_runtime.c` carries
9 `#ifdef __APPLE__` blocks. A Windows arm goes in the same places. Splitting into
`sprout_*_win.c` files would fork 9641 lines of `sprout_runtime.c` along a seam that does not
exist.

**One exception — the context switch gets a real seam.** `ucontext_t` is embedded by value in the
`Task` struct, and fibers have no equivalent type (a fiber is an opaque `LPVOID`), so this cannot
be an `#ifdef` around call sites alone. Introduce `runtime/sprout_context.h`:

| Operation | POSIX | Windows |
|---|---|---|
| `sprout_ctx_adopt_current(ctx)` | `getcontext` | `ConvertThreadToFiber` |
| `sprout_ctx_create(ctx, entry, stack_bytes)` | `getcontext` + `uc_stack` + `makecontext` | `CreateFiber` |
| `sprout_ctx_switch(from, to)` | `swapcontext` | `SwitchToFiber` |
| `sprout_ctx_destroy(ctx)` | `free(stack)` | `DeleteFiber` |

That covers every existing use: `sprout_scheduler.c:399`, `:446`, `:509` (switches), `:549-553`
(pump setup), `:616-621` (task setup), and all three stack frees — `:451` and `:455` (the pump
reclaiming a finished task) and `:743` (`force_drop_task`).

### 4.3 W2 in detail: the poller, and why `WSAPoll` goes first

The interface to implement is fixed and small (`sprout_scheduler.h:71-100`): `sprout_poll_init`,
`_add`, `_remove`, `_add_timer`, `_remove_timer`, `_wait`.

**`WSAPoll` (first choice).** Verified against Microsoft Learn: `POLLIN` is
`POLLRDNORM|POLLRDBAND`, `POLLOUT` is `POLLWRNORM`, error conditions are *"always returned, so
information on them need not be requested"*, negative `fd` entries are ignored and get
`POLLNVAL` — which gives `sprout_poll_remove`'s idempotent "this fd will not report readiness"
contract for free, without rebuilding the array. Timeout is milliseconds, `0` immediate,
negative infinite.

Two caveats, both verified and both acceptable:

1. **The historical connect-failure defect is fixed and dated.** Microsoft documents: *"As of
   Windows 10 version 2004, when a TCP socket fails to connect, (POLLHUP | POLLERR | POLLWRNORM)
   is indicated."* Before 2004 (May 2020) a failed `connect()` was not reported — fatal for
   `tcp_connect`'s park. **This sets a floor of Windows 10 version 2004**, which is acceptable for
   a title shipping in 2026 and should be recorded as the game's minimum OS.
2. **Blocking `WSAPoll` performs an alertable wait**, and issuing a blocking Winsock call from an
   APC that interrupted one is documented undefined behaviour. Sprout queues no APCs, so this is
   a "do not start" note, not a constraint.

**Timers.** Windows has no timerfd. The backend keeps a deadline min-heap and passes the nearest
deadline as the `WSAPoll` timeout, synthesizing timer events on expiry — the token-based
`sprout_poll_add_timer` contract permits this, since it only promises the token comes back from
`sprout_poll_wait` with `out_is_timer` set. A useful consequence: the epoll backend spends one
timerfd per registration and can therefore fail to arm under `ulimit -n` pressure, which is why
`sprout_poll_add_timer` returns an int at all (`sprout_scheduler.h:77-89`). A heap has no such
pressure, so **on Windows the "returns 0" path becomes unreachable**. Document it as
unreachable-but-honoured rather than leaving the contract undefined.

**Fallbacks, if `WSAPoll` proves inadequate:**

- *AFD/wepoll-style* (mio's approach) preserves readiness semantics at scale, but reaches
  `\Device\Afd` through **undocumented** NT interfaces and means **vendoring third-party code** —
  which under this project's norms is an explicit call for Kuba, not a default.
- *IOCP* is the industry answer but is a completion model; adopting it means reshaping the park
  protocol, not just the backend.

Neither should be adopted before `WSAPoll` is measured against a real workload.

### 4.4 W1 in detail: the one genuine risk

`force_drop_task` (with_timeout expiry / scope_cancel, `sprout_scheduler.c:678`) frees a parked
task's stack **without unwinding the C frame on it** (`:743`) — a deliberate design whose
consequences the codebase already handles via `scheduler_set_park_cleanup`
(`sprout_scheduler.h:126-135`). Under fibers the stack belongs to the fiber, so `free(t->stack)`
becomes `DeleteFiber`.

Microsoft documents exactly two dangerous cases, and Sprout is in neither:

> *"If the currently running fiber calls DeleteFiber, its thread calls ExitThread and terminates.
> However, if the selected fiber of a thread is deleted by a fiber running in another thread, the
> thread with the deleted fiber is likely to terminate abnormally because the fiber stack has been
> freed."*

Sprout's scheduler is single-threaded and force-drops run **from the pump fiber against a parked
task**, which is neither the running fiber nor another thread's selected fiber. Deleting it frees
*"the stack, a subset of the registers, and the fiber data"* — precisely the current
`free(t->stack)` semantics.

**Two further fiber facts that constrain the port:**

- *"If your fiber function returns, the thread running the fiber exits."* Sprout's
  `task_trampoline` already never returns — it swaps back to the pump (`sprout_scheduler.c:509`)
  — so the existing structure is already correct. It must stay that way.
- Fiber-local storage switches with the fiber, but plain TLS does **not**. The runtime uses no
  TLS today (§1.2); that must remain true, or any added TLS has to become FLS.

### 4.5 The loud-stub policy

Any surface with no Windows implementation returns the established
`"…unsupported on this platform"` error shape (precedent: `sprout_runtime.c:7676`) — **never a
silent success, and never a compile-time removal**. A stub that reports success is worse than one
that fails, for the same reason `just test-file`'s silent skip is a worse outcome than a red test.

## 5. Milestones

| ID | Scope | Exit criterion |
|---|---|---|
| **W0** | `sprout_context.h` seam; `just windows-cross` recipe compiling (not linking) the three runtime TUs with `clang --target=x86_64-w64-mingw32`; adopt §4.1's pure-Win32 rule | all three `.c` files reach `clang -c` exit 0 |
| **W1** | `ucontext` → fibers, per §4.4 | task spawn / yield / join / `scope_cancel` smoke passes |
| **W2** | `WSAPoll` backend + timer min-heap, per §4.3 | `task_sleep` + `with_timeout` + a TCP echo smoke pass |
| **W3** | Winsock2, files, arena, threads, console, regex, process — §6 | the runtime links |
| **W4** | Vectored exception handler; `CaptureStackBackTrace`/DbgHelp or a loud stub | a deliberate stack overflow prints a diagnostic, not a silent exit |
| **W5** | First `.exe`; `windows-latest` CI job; game-side link flags | uncharted-suns runs on Windows |

W1 and W2 carry the design risk; W3 is mechanical substitution.

## 6. W3 surface inventory

Counts are occurrences in `runtime/sprout_runtime.c` unless noted.

| POSIX surface | Count | Windows replacement |
|---|---|---|
| BSD sockets (`sys/socket.h`, `netinet/in.h`, `arpa/inet.h`, `netdb.h`) | pervasive | Winsock2 — `SOCKET` is **not** an `int` fd, plus `WSAStartup`, `closesocket`, `WSAGetLastError`. Touches every `tcp_*` builtin and the handle table. |
| `termios` family | 14 | `SetConsoleMode` + `ENABLE_VIRTUAL_TERMINAL_PROCESSING`. The escape sequences `stdlib.terminal` emits work on Windows 10+. |
| `sigaction` / `sigaltstack` | 10 | vectored exception handler (W4) |
| `backtrace` (`execinfo.h`) | 13 | `CaptureStackBackTrace` / DbgHelp, or a loud stub (W4) |
| `fork` / `execvp` / `pipe` | 3 / 3 / 7 | `CreateProcess` + anonymous pipes. **Needed by Milestone B and the game's offline `gen-living` bake, not by the shipped game** — implement rather than stub, because `analysis_service_driver.sprout:751` shells out via `["sh", "-c", …]` and there is no `sh` on Windows either way. |
| `regcomp` (POSIX `<regex.h>`) | 1 (`regex_compile_ere`, `:6039`) | **Not in the MSVC CRT.** Vendor a small implementation. Stubbing removes a *language-visible* feature, so this is its own backlog item, not a W3 sub-bullet. |
| `mmap(PROT_NONE)` GC arena | 2 + 4 `mprotect` | `VirtualAlloc` `MEM_RESERVE` / `MEM_COMMIT` — a direct equivalent, the easiest item on this list |
| `pthread_create` (async DNS) | 2 | `CreateThread`; `stdatomic.h` is C11 and portable |
| `readlink` / `_NSGetExecutablePath` | 2 / 2 | `GetModuleFileName` |
| `getrlimit` | 1 | stub |
| `ftell` | `:7241` | `_ftelli64` |

## 7. Syntax, type-system and error-message impact

- **Syntax:** none.
- **Type system:** none.
- **Errors:** new `"…unsupported on this platform"` strings only, matching the existing shape.
  No diagnostic wording changes.
- **Spec:** none. `docs/spec-v0.md` is untouched; this document is non-normative.

## 8. Compatibility and migration

No source-level change for any existing program. Existing macOS and Linux behaviour is unchanged
by construction — every edit is additive behind `#ifdef _WIN32`, except `sprout_context.h`, which
is a mechanical extraction of existing `ucontext` calls whose POSIX arm must stay byte-equivalent
in behaviour. The `just linux-smoke` and `macos-latest` CI gates cover that regression.

Minimum OS: **Windows 10 version 2004** (§4.3, `WSAPoll` connect-failure reporting).

## 9. Deferred: what Steam additionally requires

Named here so it is not rediscovered later; designed when W5 lands.

- **Steamworks flat-API shim** — a `sprout_steam.c` in the game repo, sibling to its existing
  `graphics/sprout_gfx.c` and `graphics/sprout_audio.c` shims, calling `steam_api_flat.h` exports.
- **VC++ redistributable** checkbox in the Steamworks App Admin *Installation → Redistributables*
  tab (required when shipping MSVC-built binaries).
- Depot build upload, icon/manifest, code signing.
- `/SUBSYSTEM:WINDOWS` (MSVC) or `-mwindows` (mingw) so the shipped game has no console window.
  The compiler emits `define i32 @main(i32 %argc, ptr %argv)` (`ir_lowering.sprout:710`), which
  links as a console app by default.

## 10. Tests

Each milestone's exit criterion in §5 is its test. Additionally:

- **W0** adds `just windows-cross`, a compile-only gate runnable from macOS/Linux with no Windows
  machine — the fast feedback loop for the whole port.
- **W5** adds a `windows-latest` CI job mirroring the existing `macos-latest` job's shape
  (`.github/workflows/ci.yml:181-210`, which bootstraps and runs a task/IO smoke against the
  kqueue backend). The Windows job runs the same smoke against the `WSAPoll` backend, and
  **builds with clang targeting `x86_64-pc-windows-msvc`** — the local loop is mingw, so MSVC has
  to be the gated one for §4.1's rule to stay honest.
- No Sprout-level tests change. The language surface is identical; only which platforms the
  runtime supports changes.

## 11. Sources

- [Steamworks API Overview](https://partner.steamgames.com/doc/sdk/api) — supported compilers;
  `steam_api_flat.h`.
- [Fibers — Win32 apps, Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/procthread/fibers)
  — `ConvertThreadToFiber`, `CreateFiber`, `SwitchToFiber`, `DeleteFiber` semantics; fiber state;
  TLS/FLS behaviour.
- [WSAPoll function (winsock2.h), Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/api/winsock2/nf-winsock2-wsapoll)
  — flags, timeout, `POLLNVAL` on negative fds, the Windows 10 version 2004 connect-failure note,
  the APC/alertable-wait note.
- [Go `src/runtime/netpoll_windows.go`](https://github.com/golang/go/blob/master/src/runtime/netpoll_windows.go)
  — IOCP (`CreateIoCompletionPort`, `GetQueuedCompletionStatusEx`).
- [libuv design overview](https://docs.libuv.org/en/v1.x/design.html) — "IOCP on Windows".
- [Rust `mio` `src/sys/windows/afd.rs`](https://github.com/tokio-rs/mio/blob/master/src/sys/windows/afd.rs)
  — `\Device\Afd`, `IOCTL_AFD_POLL`, wepoll attribution.
- [raylib releases](https://github.com/raysan5/raylib/releases) — `win64_mingw-w64` and
  `win64_msvc16` variants.

## 12. Appendix — the uncharted-suns side

Recorded here because the driver lives in a different repo (`uncharted-suns`, private) and these
items would otherwise be rediscovered at W5. They are game-repo work, not language-repo work.

- **`Justfile:26`** — `gfx_link` has a macOS arm and a Linux/X11 arm only. Windows needs
  `-lraylib -lopengl32 -lgdi32 -lwinmm`.
- **The game's own C shims must cross-compile too.** `graphics/sprout_gfx.c` and
  `graphics/sprout_audio.c` are linked into every graphics build alongside the runtime
  (`Justfile:106`), so §4.1's pure-Win32 discipline applies to them identically.
- **Obtain a Windows raylib.** raylib 6.0 publishes both variants the develop/ship split needs:
  `raylib-6.0_win64_mingw-w64.zip` and `raylib-6.0_win64_msvc16.zip`. The `raylib_prefix` variable
  (`Justfile:19`) currently defaults to a `brew --prefix raylib` lookup and needs a Windows arm.
- **Subsystem**: `-mwindows` / `/SUBSYSTEM:WINDOWS`, per §9.
- **Not part of the ship**: the offline `gen-living` bake shells out to the `sqlite3` CLI
  (`game/livegen_bake.sprout:46`). That is dev-time tooling that runs on macOS; it does not need
  a Windows port, and its `proc_run` dependency is why the `CreateProcess` item is Milestone-B
  scoped rather than a Milestone-A blocker.
