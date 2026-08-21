# Windows port — Milestone A: compile *to* Windows

Status: **W0a–W2 implemented and on master; PARKED after W2 — see §5.1 to resume.** Non-normative;
`docs/spec-v0.md` remains the normative source for language semantics. Nothing here changes the
language.

Date: 2026-08-15. Last updated: 2026-08-16 (W2 landed, arc parked).

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
clang --target=x86_64-pc-windows-gnu   -c tests/golden/ir/examples__astar.sprout.ll                        -o /tmp/x.obj
clang --target=aarch64-pc-windows-msvc -c tests/golden/ir/tests__smoke_shapes__11_compiler_bundle.spr.ll -o /tmp/a.obj
```

(The second file was `examples__repl_hosted.sprout.ll` until that example was removed; the
compiler-bundling golden it names is the successor, and carries the same `musttail` and
`llvm.stacksave`/`llvm.stackrestore` usage that makes it the interesting target here — re-verified
on the new path, exit 0 with only `-Woverride-module`.)

Both exit 0, emitting only `-Woverride-module`. `file /tmp/x.obj` reports
`Intel amd64 COFF object file, not stripped, 7 sections`. Compiling to an object needs no Windows
sysroot, so this is reproducible from a clean checkout on macOS or Linux. *Linking* an executable
does need a sysroot — that is §5, W5. (The arm64 run is evidence of target-neutrality, not a
support claim; ARM64 Windows is a non-goal per §2.)

**Conclusion: zero codegen work. The ~11.3k-line C runtime is the entire job.**

**Now gated, not assumed.** The above was a one-time hand check; `just windows-ir-gate`
(`scripts/windows_ir_gate.sh`) runs it on every commit over **all 58** golden IR snapshots × both
Windows targets, and checks the COFF machine type in the object header rather than trusting a
zero exit status. It also asserts the corpus still contains `musttail`, so a golden refresh that
dropped the one ABI-sensitive construct cannot silently weaken the gate. Needs only clang ≥ 16 —
no runtime, no sysroot, no Windows host — which is why it can be green today while the runtime
still does not compile for Windows at all. The `windows` CI job runs it on a real Windows host
(§10).

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

**Standing constraint on the whole effort (Kuba, 2026-08-16): the port changes no macOS or Linux
behaviour or logic.** Windows support is additive. A Windows arm goes *alongside* the POSIX code,
never through a refactor of it, and "while I am here" cleanups of a working POSIX path are out of
scope by construction. This is what keeps every milestone's POSIX gate meaningful: a green
`just test` after a Windows milestone should be green for the boring reason that nothing on that
path moved.

Two consequences that are easy to miss:

- A **three-way `#if`** puts Windows first and leaves both POSIX arms textually intact, rather
  than restructuring a two-way split into something "cleaner".
- Where a Windows change would require reworking shared POSIX code, the Windows arm waits for the
  milestone that owns that surface instead of landing early. §4.3's DNS pipe is the worked example.

One delta predates the constraint and is recorded rather than hidden: W0b moved the scheduler
pump's stack from a BSS array to a `malloc` (§4.6), unavoidable under `CreateFiber`'s ownership
model. It is an allocation-strategy change, gated by the full POSIX suite, and it is the only one.

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
  on. Verified at W0a against mingw-w64 14.0.0; `just windows-probe` locates the sysroot via
  `brew --prefix mingw-w64`, overridable with `SPROUT_MINGW_SYSROOT`.
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
be an `#ifdef` around call sites alone. Hence `runtime/sprout_context.h`, landed at W0b — the four operations and the decisions behind them are in §4.6. It covers every
existing use: `sprout_scheduler.c:390`, `:437`, `:499` (switches), `:509` (task-0 adoption),
`:539` (pump setup), `:603` (task setup), and all three stack frees — `:442` and `:445` (the pump
reclaiming a finished task) and `:727` (`force_drop_task`).

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

**Sockets only — the limitation that bites, and it is not `WSAPoll`-specific.** `WSAPoll` accepts
only sockets, where epoll and kqueue accept any descriptor. Sprout depends on that generality in
exactly one place: **async DNS parks a green task on a `pipe()` read end**
(`sprout_runtime.c:7117`), used as the completion signal from the detached `getaddrinfo` thread.
No readiness backend on Windows can poll a pipe.

The fix is a **loopback socket pair** — the pipe carries a single completion byte, so a
self-connected `127.0.0.1` pair serves the same purpose and is a socket. Winsock has no
`socketpair()`; the emulation is `bind` a loopback listener → `listen(1)` → `getsockname` →
`connect` → `accept`. libuv hand-rolls exactly that (`src/win/tcp.c:1627`, `uv_socketpair`), which
is the practical confirmation that no Winsock equivalent exists. Sprout's version can use a
blocking `accept` where libuv needs `AcceptEx`, because libuv requires overlapped handles for IOCP
and this pair is built once on one thread.

**This landed in W3, not W2** — a scope correction made when W2 was implemented. The change is
mechanical, but it sits in `async_resolve` (`sprout_runtime.c:7102`), a function that also calls
`fcntl`, `read`, `close` and `pthread_create`, in a TU that does not compile for Windows at all
until W3 clears `regex.h` at line 7. Writing the Windows arm at W2 would have produced code no
gate could compile, in the one place a mistake breaks the POSIX DNS path — the same
unverifiable-exit-criterion error W0 and W1 each made once. What W2 did leave at the site is a
comment, so someone editing the DNS path learns the constraint without reading this document.

None of this is a reason to prefer a different poller, because *the alternative has the same
limitation* (below).

### 4.3.1 Poller decision (2026-08-16): `WSAPoll`, revisitable

**Decided: `WSAPoll`, vendoring nothing.** Recorded with its limits so the decision can be
re-opened on evidence rather than re-litigated from scratch.

**AFD/wepoll buys no capability.** It was carried above as the scale fallback, implying it reached
further than `WSAPoll`. It does not. wepoll's own README states its limitations:

> *"Only works with sockets."* … *"Edge-triggered (`EPOLLET`) mode isn't supported."*

That is structural: AFD *is* the Ancillary Function Driver, the kernel layer beneath Winsock, so
it has no more reach than `WSAPoll` — it offers a *registered set* instead of a re-passed array,
and nothing else. In particular **it does not solve the DNS-pipe problem either**. (The
edge-triggered gap is moot for us: this poller is documented one-shot, nearer `EPOLLONESHOT`.)

So the only question AFD answers is scale, and three facts bound it:

| bound | value |
|---|---|
| worst-case registered fds | **2048** — `g_conn_fd[2048]`, `sprout_runtime.c:153` |
| worst-case array copied per wait | ~16 KB of `WSAPOLLFD` |
| what is actually in the array | tasks *currently parked*, since registration is one-shot — not all open connections |

and the motivating consumer, uncharted-suns (§12), is a single-player title whose poller load is
timers, not sockets. The C10k regime AFD exists for is not a workload Sprout has.

**What makes this safe to decide now is that it is cheap to undo.** The poller is a 6-function
interface with two backends behind it (`sprout_scheduler.h:71-100`); W2 is the act of adding a
third, and a fourth would be the same contained change to one file — no call-site churn, no
protocol reshape. Choosing AFD today would pay undocumented NT interfaces, vendored third-party
code, and permanent maintenance up front against a hypothetical. Choosing `WSAPoll` today pays a
later swap the architecture already accommodates.

**Revisit triggers — any one of these re-opens it:**

- A **measured** `WSAPoll` bottleneck on a real workload. Not a suspicion: this decision rests on
  the shape of the problem (the 2048 cap, one-shot registration, the driver's socket count), and
  no one has benchmarked `WSAPoll` under Sprout. A measurement beats the reasoning above.
- **`g_conn_fd`'s 2048 cap being raised toward five figures.** The cap is what bounds the array
  copy; if the HTTP server is meant to serve serious concurrency, the calculus changes and AFD
  stops being hypothetical. Whether 2048 is a deliberate ceiling or an arbitrary table size is
  currently unanswered.
- A need to poll something that is **not a socket and not the DNS pipe**. Both readiness options
  fail there, so that is the case that forces **IOCP** — which does reach other handle types
  (named pipes support overlapped I/O) and is what Go and libuv use.

**IOCP is deliberately not the fallback of first resort**, and not for performance reasons: it is
a *completion* model, so unlike swapping in AFD it reshapes the park protocol rather than just the
backend. That is the one option this interface does **not** make cheap to adopt later, which is
the argument for keeping it out of W2 — and for taking the non-socket requirement above seriously
if it ever appears.

### 4.4 W1 in detail: the one genuine risk

`force_drop_task` (with_timeout expiry / scope_cancel, `sprout_scheduler.c:662`) frees a parked
task's stack **without unwinding the C frame on it** (`:727`) — a deliberate design whose
consequences the codebase already handles via `scheduler_set_park_cleanup`
(`sprout_scheduler.h:126-135`). Since W0b that free is `sprout_ctx_destroy` (§4.6); under fibers
its body becomes `DeleteFiber`.

Microsoft documents exactly two dangerous cases, and Sprout is in neither:

> *"If the currently running fiber calls DeleteFiber, its thread calls ExitThread and terminates.
> However, if the selected fiber of a thread is deleted by a fiber running in another thread, the
> thread with the deleted fiber is likely to terminate abnormally because the fiber stack has been
> freed."*

Sprout's scheduler is single-threaded and force-drops run **from the pump fiber against a parked
task**, which is neither the running fiber nor another thread's selected fiber. Deleting it frees
*"the stack, a subset of the registers, and the fiber data"* — precisely the current
`sprout_ctx_destroy` semantics, whose contract already states the caller must not be executing on
the context it destroys.

**Two further fiber facts that constrain the port:**

- *"If your fiber function returns, the thread running the fiber exits."* Sprout's
  `task_trampoline` already never returns — it swaps back to the pump (`sprout_scheduler.c:499`)
  — so the existing structure is already correct. It must stay that way.
- Fiber-local storage switches with the fiber, but plain TLS does **not**. The runtime uses no
  TLS today (§1.2); that must remain true, or any added TLS has to become FLS.

**The floating-point trap, found while implementing W1.** `CreateFiberEx`'s `dwFlags` is not
cosmetic:

> *"If this parameter is zero, the floating-point state on x86 systems is not switched and data
> can be corrupted if a fiber uses floating-point arithmetic."*

Sprout compiles `Float` arithmetic to real `double` instructions — `ir_lowering.sprout:115-118`
bitcasts the i64 handle to `double`, applies `fadd`/`fmul`/…, and bitcasts back — so green tasks
absolutely do use FP, and unsaved FP state across a yield is *silent* data corruption. `ucontext`
has no equivalent hazard, so nothing in the POSIX arm hints at it.

The mechanism is that `CONTEXT_FULL` is defined **per architecture**, verified in mingw-w64's
`winnt.h`:

| architecture | `CONTEXT_FULL` | flag needed? |
|---|---|---|
| i386 (`CONTEXT_i386`) | `CONTROL │ INTEGER │ SEGMENTS` | **yes** — FP is omitted |
| x86-64 (`CONTEXT_AMD64`) | `CONTROL │ INTEGER │ FLOATING_POINT` | no — already covered |

which is exactly why Microsoft words the hazard as *"on x86 systems"*. `FIBER_FLAG_FLOAT_SWITCH`
adds `CONTEXT_FLOATING_POINT` to what the fiber saves (confirmed against ReactOS's independent
kernel32 implementation, which applies it with no architecture conditional).

**Sprout passes it unconditionally.** It is free where it is redundant, and the bug it prevents is
invisible — wrong arithmetic, no crash, no diagnostic. That asymmetry decides it.

### 4.5 The loud-stub policy

Any surface with no Windows implementation returns the established
`"…unsupported on this platform"` error shape (precedent: `sprout_runtime.c:7676`) — **never a
silent success, and never a compile-time removal**. A stub that reports success is worse than one
that fails, for the same reason `just test-file`'s silent skip is a worse outcome than a red test.

### 4.6 The context seam, as landed (W0b)

`runtime/sprout_context.h`. The decisive constraint is stack ownership: `makecontext` runs on a
stack you hand it, `CreateFiber` allocates its own and returns an opaque handle. So **`SproutCtx`
owns its stack and no operation accepts a caller-supplied one** — otherwise the interface would
carry an op the Windows arm cannot implement, which is the whole thing the seam exists to avoid.
`Task`'s `ucontext_t ctx` + `void* stack` collapse into one `SproutCtx ctx`.

| Op | POSIX | W1 |
|---|---|---|
| `sprout_ctx_adopt_current` | no-op; `uc` is filled by the first switch out | `ConvertThreadToFiber` |
| `sprout_ctx_create(c, entry, bytes)` | `malloc` + `getcontext`/`makecontext` | `CreateFiber` |
| `sprout_ctx_switch(from, to)` | `swapcontext` | `SwitchToFiber(to)` |
| `sprout_ctx_destroy` | `free`; idempotent | `DeleteFiber` |

`ctx_switch` keeps a `from` argument that `SwitchToFiber` ignores. All three call sites pass the
currently-running context, so that is documented as the interface's precondition rather than left
to chance — a switch whose source is some third context has no Windows implementation.

`ctx_create` returns a status code instead of failing internally, which keeps each caller's
diagnostic wording its own. Messages are byte-identical to the pre-seam ones, **including
`"getcontext failed"` — a non-POSIX arm has to reword that**, and the header says so.

**One deliberate behaviour delta:** the pump's stack moves from a 256 KiB BSS array to a
constructor-time `malloc`, for the ownership reason above. Its OOM path fails loudly, matching the
task path.

Incidental: the `-Wdeprecated-declarations` suppression (macOS marks the `ucontext` family
deprecated) narrows from the whole 1300-line scheduler to the four calls that need it. Nothing
else was leaning on it — the TU compiles clean under `-Wall -Wextra -Wdeprecated-declarations`.

A `_WIN32` include trips an `#error` naming this milestone, so `just windows-probe` reports the
scheduler's blocker as that `#error` rather than as `ucontext.h` — the work list points at the
milestone that closes it. **W1 replaced that `#error` with the fiber arm.**

### 4.7 The fiber arm, as landed (W1)

W0b's shape held: W1 filled in four function bodies and changed **no scheduler logic**. The one
call-site edit was `sprout_ctx_adopt_current` gaining a return code, because
`ConvertThreadToFiber` can fail where the POSIX no-op cannot.

| Op | POSIX | Windows |
|---|---|---|
| `adopt_current` | no-op; `uc` filled by the first switch out | `ConvertThreadToFiber(NULL)` |
| `create` | `malloc` + `getcontext`/`makecontext` | `CreateFiberEx(0, bytes, FIBER_FLAG_FLOAT_SWITCH, …)` |
| `switch` | `swapcontext` | `SwitchToFiber(to)` — `from` unused, as designed |
| `destroy` | `free` | `DeleteFiber`, skipped when adopted |

Decisions worth keeping:

- **Stack sizing is `commit = 0`, `reserve = stack_bytes`.** Commit-0 takes the executable's
  default so pages are backed as the stack grows — matching the POSIX arm, where a `malloc`'d
  stack is equally only backed once touched. Committing `stack_bytes` up front would make every
  task cost a real megabyte. Windows' own default *reserve* is 1 MiB, which
  `SPROUT_TASK_STACK_BYTES` already is, so the two platforms agree without tuning.
- **`SproutCtx` carries an `adopted` flag.** `DeleteFiber` on the thread's own fiber makes that
  thread call `ExitThread`; task-0 must never be deleted. The POSIX arm gets this for free
  (task-0 owns no malloc'd stack), so the flag exists only on the Windows side.
- **A trampoline, not a function-pointer cast.** `CreateFiberEx` wants `VOID WINAPI f(LPVOID)`
  and the seam's entries take no argument. The cast happens to work on x64 — `WINAPI` is a no-op
  there and the extra argument is ignored — but is wrong under 32-bit `stdcall`, so the entry
  travels in `lpParameter` instead, which is what that parameter is for.
- **`FIBER_FLAG_FLOAT_SWITCH` is passed unconditionally** — see §4.4's floating-point trap.

**W1's original exit criterion was unachievable, the same way W0's was.** It read *"task spawn /
yield / join / `scope_cancel` smoke passes"*, which requires **running** on Windows — impossible
until W5 links an executable. The achievable criterion at W1 is compilation, gated by
`scripts/windows_tu_check.sh`.

**And the two-toolchain rule earned its keep on that gate's first real run.** W1 initially also
claimed `sprout_scheduler.c` as a whole, on the strength of a local mingw build: `sprout_scheduler.h`
only *declares* the poller interface, so nothing on the scheduler's path reaches a POSIX poller
header, and mingw compiled the TU clean. The first MSVC run refuted it — line 30 is
`#include <unistd.h>` for `close()`, which mingw supplies and **MSVC does not**. The claim was
true of the developer surface and false of the ship surface.

That is precisely the failure §4.1 predicts, arriving where it is cheap instead of at W5, and it
is why `windows_tu_check.sh` runs mingw off-Windows and MSVC in CI rather than trusting either
alone. `sprout_scheduler.c` is therefore listed as outstanding against W3 — its remaining blocker
is the socket-`close` substitution, Winsock work by nature, not scheduler work.

### 4.8 The `WSAPoll` backend, as landed (W2)

`sprout_poll.c` gained a third arm, placed **first** in the chain — `#if defined(_WIN32)` /
`#elif defined(__APPLE__)` / `#else`. W0a had already flagged why: the file's old `#else` reads
"not macOS" as "Linux", so a Windows arm appended after it is dead code and `sys/epoll.h` is what
the compiler actually reaches. The two POSIX arms are otherwise untouched.

**The finding that shaped it: `WSAPoll` cannot wait on an empty socket set.** Two statements on
the same Microsoft Learn page: *"The array must contain at least one structure with a valid
socket"*, and `WSAEINVAL` is returned *"if none of the sockets specified in the **fd** member of
any of the **WSAPOLLFD** structures pointed to by the fdarray parameter were valid."*

The scheduler's pump blocks in `sprout_poll_wait` whenever anything at all is parked
(`sprout_scheduler.c:401`), and that includes a parked set that is **entirely timers** — a single
`task_sleep` produces one. On kqueue and epoll the question cannot arise, because each exposes a
timer as a pollable object (`EVFILT_TIMER`, `timerfd`) sitting in the same wait set as the
sockets. Windows has no timerfd equivalent, so the deadlines live in a heap here and the socket
array is legitimately empty.

This is not a corner case for the motivating consumer: uncharted-suns is a single-player title
whose poller load is timers, so the empty-socket wait is its *common* path. A timers-only wait
therefore uses `Sleep(nearest deadline)` instead of `WSAPoll`. That is safe for a reason specific
to this runtime rather than to `Sleep`: the only cross-thread wakeup the runtime has is the
detached `getaddrinfo` thread, and a pending DNS park is a socket park once §4.3's loopback pair
lands — so a wakeup can never arrive while the poller is in a timers-only sleep. Should a second
cross-thread wakeup source ever appear, this is the line that has to change.

Decisions worth recording:

- **The timeout is clamped to `[0, INT_MAX]`.** Deadlines are `long long` milliseconds and
  `WSAPoll`'s `timeout` is a signed `INT` in which *"a negative value indicates that WSAPoll
  should wait indefinitely"*. An already-due deadline computes negative, so an unclamped
  subtraction would turn "fire this now" into "block forever" — a hang, in the code path that
  exists to prevent hangs. `-1` is passed only when no timer is registered at all.
- **Due timers are harvested on both return paths**, not only when `WSAPoll` times out. A deadline
  and a ready socket can come due in the same wait, and the pump already handles a batch carrying
  both, because a `PARK_FD_TIMER` task is registered on each (`sprout_scheduler.c:418-423`).
- **The registration array is kept compact rather than using negative-fd holes.** `WSAPoll` would
  let holes stay in place — negative entries are *"ignored and their revents will be set to
  POLLNVAL"* — but then the array length and the live-socket count diverge, and the empty-set test
  above depends on the second. One number is harder to get wrong than two.
- **Timer ids are monotonic and never reused.** That is what makes `sprout_poll_remove_timer` safe
  against a stale token (§5.1's guarantee): an id belonging to an already-harvested timer matches
  nothing, rather than matching whatever new timer landed in that slot. Removal is an O(n) scan
  over *concurrently parked* sleepers — cheaper than the syscall the POSIX arms spend, and it
  avoids an id→slot index that every heap sift would have to maintain.
- **`sprout_poll_add_timer`'s "returns 0" path is unreachable in practice.** On epoll it is a real
  failure (one timerfd per registration, first thing to break under `ulimit -n`); here only the
  heap's `realloc` can fail. Honoured, not removed.
- **`sprout_poll_init` is empty.** `WSAStartup` belongs to W3's socket layer — the runtime must
  have initialized Winsock before it can hand this layer an fd to poll.

**Not done here, deliberately:** the `int fd` in the poller interface. A Winsock `SOCKET` is a
`UINT_PTR`, not an `int`; the Windows arm casts, and W3 widens the interface when it converts the
handle table. Splitting that off keeps W2 to one file.

**W2 also breaks the compile-only pattern, on purpose.** `tests/windows/poll_selftest.c` +
`scripts/windows_poll_selftest.sh` build and **run** the backend on the CI runner against real
loopback sockets. Nothing else in this port can do that before W5, and the poller is the wrong
component to accept it for: unlike W3's substitutions, its logic branches — the empty-socket set
that must never reach `WSAPoll`, a timeout clamp whose failure mode is a hang, a swap-remove
during a live scan. Left to W5, a bug in any of those would surface tangled with four other
milestones' bugs.

It works this early because the poller's dependency set is `Ws2_32` and nothing else: no
scheduler, no GC, no Sprout runtime. The test `#include`s `sprout_poll.c` directly to reach its
file-static tables and supplies its own `sprout_fail`. Seven cases: timers-only ordering (the
`WSAEINVAL` path), a removed timer never firing, socket readiness, one-shot registration not
re-reporting a still-readable socket, a deadline beating an idle socket (the `with_timeout`
shape, and where an unclamped timeout would hang), a deregistered fd staying silent, and a
socket plus a due timer arriving in **one** batch. It also exercises the loopback-pair
construction W3 needs for the DNS fix — so if that emulation is wrong, this says so at W2 rather
than the DNS path saying it at W3.

## 5. Milestones

| ID | Scope | Exit criterion |
|---|---|---|
| **W0a** | *(done, 2026-08-15)* `scripts/windows_probe.sh` + `just windows-probe`: measure what the target provides and where each TU stops; adopt §4.1's pure-Win32 rule | a measured §6, replacing the assumed one |
| **W0b** | *(done, 2026-08-15)* `sprout_context.h` seam — the `ucontext` calls behind a 4-op header, POSIX arm behaviourally unchanged but for §4.6's one delta | `just test` + `task-io-smoke` + `linux-smoke` + the example canary still pass **on POSIX**; no Windows code yet |
| **W1** | *(done, 2026-08-16)* `ucontext` → fibers, per §4.4 and §4.7 | `sprout_context.h`'s Windows arm compiles under **both** mingw and MSVC; POSIX gates unchanged |
| **W2** | *(done, 2026-08-16)* `WSAPoll` backend + timer min-heap, per §4.3 and §4.8 | `sprout_poll.c` compiles under **both** mingw and MSVC (promoted to `windows_tu_check.sh`'s EXPECTED); POSIX gates unchanged |
| **W3** | Winsock2, files, arena, console, regex, process — §6 | all three TUs compile; `just windows-probe` becomes a gate |
| **W4** | Vectored exception handler; `CaptureStackBackTrace` or a loud stub | a deliberate stack overflow prints a diagnostic, not a silent exit |
| **W5** | First `.exe`; game-side link flags; the `windows` job gains a run smoke | uncharted-suns runs on Windows |

W1 and W2 carry the design risk; W3 is mechanical substitution.

**The `windows-latest` CI job moved from W5 to W1** (`.github/workflows/ci.yml`, job `windows`).
It was originally listed as a W5 deliverable, which would have meant writing the fiber and poller
work — the two milestones carrying the design risk — with no Windows verification at all, then
discovering four milestones' worth of problems at once. The alternative local loop, Wine, is the
wrong instrument: it is an independent reimplementation of Win32, and fibers and `WSAPoll` are
precisely where a reimplementation is most likely to differ, so a green Wine run would not
establish that the port works. (It is also being retired underneath us — Homebrew's wine casks are
disabled from 2026-09-01, and x86_64 Wine on Apple Silicon rides Rosetta 2, which Apple is winding
down.) A free GitHub-hosted runner is genuine Windows.

The job is **green from day one and grows one step per milestone** — today `windows-ir-gate`
(§1.1), then the fiber arm at W1, the `WSAPoll` backend at W2, all three TUs at W3, and a linked
run smoke at W5. A job that is red until W3 would train everyone to ignore it, which costs more
than it gains.

**W0 was originally one milestone whose exit criterion was "all three `.c` files reach `clang -c`
exit 0". That was wrong** — it is W3's criterion, not a first step: `sprout_runtime.c` stops on
its very first non-standard include, so reaching a clean compile means W3 and W4 are already
done. The split separates a *measurement* (W0a, no design decisions, cannot be wrong) from the
first *code* change (W0b, a pure POSIX-side refactor needing no Windows toolchain at all).

W0b is deliberately not Windows code. Extracting the seam while it still has one implementation
means the existing POSIX gates can prove it correct; doing it later means debugging the refactor
and the fiber port simultaneously, on a platform that cannot yet run the test suite.

### 5.1 Parked at W2 (2026-08-16) — how to pick this up

The arc is **paused after W2, deliberately, not blocked.** W0a/W0b/W1/W2 are on master; nothing is
half-landed, no branch is outstanding, and every gate listed below is green. This section exists so
resuming needs no archaeology.

**What works today.** Two of the three runtime TUs compile for Windows under *both* toolchains, and
the poller is verified by *execution*, not just compilation:

| | state |
|---|---|
| `runtime/sprout_context.h` | compiles (mingw + MSVC) — fiber arm, W1 |
| `runtime/sprout_poll.c` | compiles (mingw + MSVC) **and runs** — `WSAPoll` arm, W2 |
| `runtime/sprout_scheduler.c` | mingw only; MSVC stops at line 30, `#include <unistd.h>` for `close()` → W3 |
| `runtime/sprout_runtime.c` | stops at line 7, `#include <regex.h>` → W3 |

**The developer loop, all runnable from macOS/Linux with no Windows machine:**

    just windows-probe          # what the target provides; a diagnostic until W3, then a gate
    just windows-tu-check       # the promise list: TUs that must compile (mingw here, MSVC in CI)
    just windows-ir-gate        # golden IR -> Win64 COFF, x86-64 + ARM64 (in `just gate`)
    just windows-poll-selftest  # skips off-Windows; CI runs it for real

`just windows-tu-check` needs a mingw-w64 sysroot (`brew install mingw-w64`, or
`SPROUT_MINGW_SYSROOT`). The `windows` CI job runs the last three on a real Windows host against
MSVC and is **advisory** — `test` remains the only required check.

**Two decisions are waiting on the project owner, and W3 should not start without them.** Both are
language-or-product questions wearing porting clothes, which is why they were split out rather than
absorbed into W3's substitution list:

1. **POSIX `<regex.h>` has no MSVC equivalent.** One use, `regex_compile_ere`
   (`sprout_runtime.c:6039`). Vendor a small ERE implementation, or narrow a **language-visible**
   feature on Windows? Own BACKLOG entry.
2. **`proc_run`'s `fork`/`execvp`/`pipe` → `CreateProcess`.** Needed by Milestone B and the game's
   offline bake, *not* by the shipped game — so a loud stub may be the right answer rather than a
   compromise.

**Two items W2 explicitly handed to W3**, both recorded at their call sites so they cannot be lost:
the DNS-pipe → loopback-pair swap (§4.3, comment at `sprout_runtime.c:7102`) and widening the
poller interface's `int fd` to a `SOCKET` (§4.8).

**The standing constraint governing everything above is §2**: this port changes no macOS or Linux
behaviour or logic. Read it before writing W3 — it is what decides, in the ambiguous cases, whether
a Windows arm goes in now or waits for the milestone that owns the surface.

## 6. W3 surface inventory — **measured**

Measured 2026-08-15 against mingw-w64 14.0.0 with `just windows-probe`
(`scripts/windows_probe.sh`), which probes each header and function independently. Re-run it
rather than trusting this table if the toolchain moves. Counts are occurrences in
`runtime/sprout_runtime.c` unless noted.

**Scope of this measurement:** the probe compiles with `-fsyntax-only`, so it measures the
*compile* surface only. Link-time facts are invisible to it and land at W5 — most concretely for
winpthreads, which may need `-lwinpthread` and may carry a `libwinpthread-1.dll` runtime
dependency unless linked statically. "No work under mingw" for the pthread row below means no
*code* work; the link and redistribution question is open.

**Every Win32 replacement this design names is available** — all of `ConvertThreadToFiber`,
`CreateFiber`, `SwitchToFiber`, `DeleteFiber`, `WSAPoll`, `WSAStartup`, `closesocket`,
`VirtualAlloc`, `SetConsoleMode`, `GetModuleFileNameA`, `CreateProcessA`, `CreatePipe`,
`CaptureStackBackTrace`, `AddVectoredExceptionHandler`, `CreateWaitableTimerA`. No item below is
blocked on a missing API. Score at W0a: **56 available, 22 missing.**

| POSIX surface | Count | Probe result | Windows replacement |
|---|---|---|---|
| BSD sockets (`sys/socket.h`, `netinet/in.h`, `arpa/inet.h`, `netdb.h`) | pervasive | all 4 headers **missing** | Winsock2 (`winsock2.h`, `ws2tcpip.h` both present) — `SOCKET` is **not** an `int` fd, plus `WSAStartup`, `closesocket`, `WSAGetLastError`. Touches every `tcp_*` builtin and the handle table. |
| `termios` family | 14 | header **missing** | `SetConsoleMode`/`GetConsoleMode` + `ENABLE_VIRTUAL_TERMINAL_PROCESSING`. The escapes `stdlib.terminal` emits work on Windows 10+. |
| `sigaction` / `sigaltstack` | 10 | `signal.h` present, but `sigaction`, `sigaltstack`, `sigemptyset` all **missing** | vectored exception handler (W4). `signal`/`raise` do exist, but not the POSIX surface the crash handler needs. |
| `backtrace` (`execinfo.h`) | 13 | header **missing**; `dbghelp.h` also **absent from the sysroot** | `CaptureStackBackTrace` (in `windows.h`, present). DbgHelp is *not* an option here, so frames come back as addresses — symbolization is a separate question, not a W4 blocker. |
| `fork` / `execvp` / `pipe` | 3 / 3 / 7 | `fork` **missing**, `pipe` **missing**, `execvp` present | `CreateProcessA` + `CreatePipe`. **Needed by Milestone B and the game's offline `gen-living` bake, not by the shipped game.** Note `analysis_service_driver.sprout:751` shells out via `["sh", "-c", …]` and there is no `sh` on Windows either way. |
| `regcomp` (POSIX `<regex.h>`) | 1 (`regex_compile_ere`, `:6039`) | header **missing** | Vendor a small ERE implementation. **This is `sprout_runtime.c`'s *first* blocker (line 7)** — nothing else in that TU can be compile-checked until it is resolved or temporarily `#ifdef`'d out, which makes it an ordering constraint on top of being a feature question. Own backlog item: stubbing it removes a *language-visible* feature. |
| `mmap(PROT_NONE)` GC arena | 2 + 4 `mprotect` | `sys/mman.h` **missing** | `VirtualAlloc` `MEM_RESERVE`/`MEM_COMMIT` — a direct equivalent, the easiest item here |
| `pthread_create` (async DNS) | 2 | **`pthread.h` present**, and `pthread_create`/`_detach`/`_mutex_lock`/`_cond_wait` all available | **No work needed under mingw** (winpthreads). Corrects this table's pre-measurement claim that `CreateThread` was required. It *would* be required under MSVC, so §4.1's ship-target rule still applies — treat this as deferred, not free. |
| `readlink` / `_NSGetExecutablePath` | 2 / 2 | `readlink` **missing** | `GetModuleFileNameA` |
| `getrlimit` | 1 | `sys/resource.h` **missing** | stub |
| `ftell` | `:7241` | `ftello`, `fseeko`, `_ftelli64`, `_fseeki64` **all present** | Use **`_ftelli64`**, not `ftello`: the probe shows mingw provides both, but only `_ftelli64` also exists under MSVC, so it is the one choice that satisfies §4.1's write-to-the-strict-surface rule. |
| `sys/wait.h`, `poll.h` | — | both **missing** | subsumed by `CreateProcessA` and `WSAPoll` respectively |

**Where each TU stops today** (a missing include is fatal, so each reports one blocker per run):

| TU | First blocker |
|---|---|
| `sprout_poll.c` | `:94` `sys/epoll.h` — note it reaches the *epoll* arm, because the file's `#ifdef __APPLE__` / `#else` treats "not macOS" as "Linux". Windows needs a genuine three-way split, not an arm appended after the `#else`. |
| `sprout_scheduler.c` | since W1: **compiles under mingw**, and under MSVC stops at `:30` `unistd.h` (`close()`, a Winsock substitution — W3). Was `:30` `ucontext.h` before W0b, then briefly `sprout_context.h`'s own `#error`. The probe measures the **mingw** surface, so it now reports this TU as compiling; `just windows-tu-check` is what covers the MSVC surface |
| `sprout_runtime.c` | `:7` `regex.h` |

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

- **W0a** added `just windows-probe` (`scripts/windows_probe.sh`), runnable from macOS/Linux with
  no Windows machine — the fast feedback loop for the whole port. It is a **diagnostic** until W3:
  the runtime is POSIX-only today, so a non-zero missing count is the expected state and its
  output *is* the work list. At W3 it becomes a gate, when all three TUs are meant to compile.
- **W0b** is covered by gates that already exist. A refactor needing a *new* test to prove it
  behaviour-preserving is not behaviour-preserving, so the evidence is the standing suite:
  `just test`, `compile-examples-stage1`, the 5-example run canary, `gc-safety-check --strict`,
  and above all **`task-io-smoke` on kqueue and `linux-smoke` on epoll+timerfd** — 43 scenarios
  each including the GC-stress variants and the ASan-verified select/chan force-drop negative
  controls, which target exactly the free-ordering the seam rewrites.

  One test *was* added (`tests/stdlib/test_task_timeout.spr`, a `with_timeout` over a
  `chan_select`-parked body). It closes no coverage hole — `tests/task_io_smoke/` already had
  both the timeout and cancel variants, and CI runs them — but that harness is not part of
  `just test`, so the `PARK_SELECT` force-drop branch was invisible to the gate AGENTS.md
  requires for a runtime change. The seam rewrites that branch; it belongs in the default suite.
- **The `windows` CI job exists from W1**, not W5 (see §5 for why it moved). It is **advisory**,
  like `macos` — `test` on Linux stays the one required check — and green at every milestone
  rather than red until W3, because a job that is expected to fail stops being read.
  - *`windows-ir-gate`*: compiles committed IR only, so it needs no runtime, no sysroot and no
    bootstrap — the reason it could pass from day one, while none of the three C TUs compiled
    for Windows.
  - *`windows-tu-check`* (added at W1): compiles the runtime TUs that are **expected** to build,
    from a list that grows one entry per milestone, and prints the rest as outstanding. A TU on
    the list is a promise; shrinking the list to make the gate pass is the one way to defeat it,
    which the script says out loud. On the runner it targets `x86_64-pc-windows-msvc` against
    the host SDK — the **ship** surface, and the stricter one, since MSVC has no `unistd.h`, no
    `sys/time.h` and no POSIX shims. Off-Windows the same script uses a mingw-w64 sysroot, so
    §4.1's develop-mingw / ship-MSVC split is exercised on both sides rather than asserted.
    Currently 2 expected (`sprout_context.h`, `sprout_poll.c`), 2 outstanding.
  - *`windows-poll-selftest`* (added at W2): builds and **runs** `tests/windows/poll_selftest.c`
    against real loopback sockets — the first Windows gate that checks behaviour rather than
    compilation, three milestones before W5 makes that generally possible. See §4.8 for why the
    poller specifically does not wait. Off-Windows the script exits 0 with a note, since it must
    run a `.exe`; that is why it is CI-only rather than a `just gate` member.
  - *W3*: `sprout_runtime.c` joins the expected list, and `just windows-probe` graduates from
    diagnostic to gate.
  - *W5*: link and **run** a task/IO smoke, mirroring what the `macos` job
    (`.github/workflows/ci.yml`) does for kqueue — the same battery against the `WSAPoll`
    backend. Built with clang targeting `x86_64-pc-windows-msvc`: the local loop is mingw, so
    MSVC has to be the gated one for §4.1's rule to stay honest.
- No Sprout-level test *expectations* change. The language surface is identical; only which
  platforms the runtime supports changes.

## 11. Sources

- [Steamworks API Overview](https://partner.steamgames.com/doc/sdk/api) — supported compilers;
  `steam_api_flat.h`.
- [Fibers — Win32 apps, Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/procthread/fibers)
  — `ConvertThreadToFiber`, `CreateFiber`, `SwitchToFiber`, `DeleteFiber` semantics; fiber state;
  TLS/FLS behaviour.
- [CreateFiberEx function (winbase.h), Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-createfiberex)
  — `dwStackCommitSize` / `dwStackReserveSize` semantics and their zero-defaults; the
  `FIBER_FLAG_FLOAT_SWITCH` floating-point warning; the 1 MiB default reserve.
- [ConvertThreadToFiber function (winbase.h), Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-convertthreadtofiber)
  — "Only fibers can execute other fibers"; NULL return on failure.
- mingw-w64 `winnt.h` (local sysroot, read directly) — the per-architecture `CONTEXT_FULL`
  definitions that explain *why* `FIBER_FLAG_FLOAT_SWITCH` is x86-only in Microsoft's wording:
  `CONTEXT_AMD64`'s includes `CONTEXT_FLOATING_POINT`, `CONTEXT_i386`'s does not.
- [ReactOS `dll/win32/kernel32/client/fiber.c`](https://doxygen.reactos.org/d9/d44/dll_2win32_2kernel32_2client_2fiber_8c_source.html)
  — independent implementation showing `FIBER_FLAG_FLOAT_SWITCH` mapping to
  `CONTEXT_FULL | CONTEXT_FLOATING_POINT`, with no architecture conditional.
- [WSAPoll function (winsock2.h), Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/api/winsock2/nf-winsock2-wsapoll)
  — flags, timeout, `POLLNVAL` on negative fds, the Windows 10 version 2004 connect-failure note,
  the APC/alertable-wait note.
- [Go `src/runtime/netpoll_windows.go`](https://github.com/golang/go/blob/master/src/runtime/netpoll_windows.go)
  — IOCP (`CreateIoCompletionPort`, `GetQueuedCompletionStatusEx`).
- [libuv design overview](https://docs.libuv.org/en/v1.x/design.html) — "IOCP on Windows".
- [libuv `uv_socketpair`, Windows implementation](https://github.com/libuv/libuv/blob/v1.x/src/win/tcp.c)
  (`src/win/tcp.c:1627`) — `WSASocketW` → `bind` loopback → `listen(1)` → `getsockname` →
  `connect` → `AcceptEx`. Cited in §4.3 as evidence that Winsock has no `socketpair()`.
- [Rust `mio` `src/sys/windows/afd.rs`](https://github.com/tokio-rs/mio/blob/master/src/sys/windows/afd.rs)
  — `\Device\Afd`, `IOCTL_AFD_POLL`, wepoll attribution.
- [wepoll README](https://github.com/piscisaureus/wepoll) — *"Only works with sockets"* and no
  edge-triggered support: the two limits that decide §4.3.1, since they mean AFD reaches no
  further than `WSAPoll`.
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
  (`game/livegen_bake.sprout:46`). That is dev-time tooling that runs on macOS and does not need a
  Windows port. It is the only game-side `proc_run` user, so keeping it out of scope is precisely
  what leaves `CreateProcess` off the Milestone-A critical path.
