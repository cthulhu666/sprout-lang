# Layer-0 I/O Parking + Top-Level Scheduler Pump — Design (EXPERIMENTAL)

Status: **LANDED 2026-07-14** (macOS/kqueue verified natively; Linux/epoll verified in a
Linux aarch64 container — fixture completes with the interleaved output, plain and under
`SPROUT_GC_STRESS=1`). `stdlib.task` remains EXPERIMENTAL and out of `docs/spec-v0.md`. Builds on the L0.1
cooperative scheduler + L0.2 nested scopes; the top-level pump here **replaces** the L0.2
recursive-C-stack joins. Implementation: `runtime/sprout_poll.c` (kqueue/epoll),
`runtime/sprout_scheduler.c` (pump), `tcp_*` retrofit in `runtime/sprout_runtime.c`.

Author date: 2026-07-14.

**As-built deltas from this design (all covered above):** per-`Scope` queues became one
global queue; nested interleaving changed (outer siblings interleave with inner scopes —
`test_task_nested_scope` golden is now `P1Q1Q2XYQ3P2P3`); `task_yield` from main is a legal
no-op (task-0 is a materialized task), so the old `task-guard-smoke` was retired; poller +
pump initialize in a startup constructor. New gate: `just task-io-smoke` (timeout-wrapped,
plain + `SPROUT_GC_STRESS=1`).

---

## 1. Problem statement

Green tasks today are purely compute-cooperative: they interleave only at explicit
`task_yield` points. Any real I/O still uses the **blocking** `tcp_*` builtins, which
block the single OS thread — so while one task waits on a socket read, *no* other task
runs. This defeats the entire point of the concurrency substrate (the design doc's
driver story: "the Postgres/Redis drivers call `tcp_read`/`tcp_write` and inherit
parking for free", §6.1/§7).

The unlock is **I/O parking**: when a task would block on a socket, it suspends and
lets siblings run; the scheduler resumes it when the OS reports the fd is ready. This
is exactly Go's netpoller model (§9 survey).

There is a structural blocker in the current scheduler. `__scope_join` runs its drive
loop **on the calling task's C stack**; nested joins stack up (L0.2). A netpoller has
nowhere to live in that shape: when a task parks on I/O, *someone* must block in
`kevent`/`epoll_wait` and be free to run **any** ready task — but an inner join loop
cannot run outer-scope siblings suspended below it on the C stack. That is the deadlock
already flagged in the L0.2 commit and the design doc. Resolving it requires moving from
recursive-C-stack joins to a **single top-level scheduler pump**.

## 2. Goals and non-goals

**Goals**
- `tcp_accept`/`tcp_read`/`tcp_write` park the current green task on `EAGAIN` instead of
  blocking the OS thread; the scheduler runs other ready tasks meanwhile.
- One top-level scheduler pump owns the ready queue **and** the readiness poller.
  `__scope_join` becomes "park the joining task until the scope drains," not a nested
  drive loop.
- Cross-platform poller: **kqueue** (macOS, dev) + **epoll** (Linux, CI) behind one
  internal C interface. Both must work — CI runs on Linux.
- Preserve everything the spikes/increments established: non-moving green stacks,
  per-task GC root contexts, the registry-based `mark_roots` (a task parked on I/O keeps
  its roots — spike #2 proved root-across-real-park).
- No change to the Sprout surface (`with_scope`/`scope_spawn`/`task_yield`) or its types.
  (`scope_spawn` was later renamed `task_spawn` and joined by `task_fork`/`task_await` in L0.4.)

**Non-goals (this increment)**
- Timers / `task_sleep` (needs a timeout-driven poller wait; deferred — noted in §9 of the
  exploration doc). The poller blocks indefinitely when only I/O-parked tasks remain.
- Cancellation and error propagation (separate increment).
- Multi-core / work-stealing (Layer-1; share-nothing is the eventual route, §8).
- Channels/actors (additive models on top).

## 3. Prior-art survey (verified against primary sources, 2026-07-14)

The two design questions are (Q-A) *what poller model* and (Q-B) *where the scheduler
blocks*. Both have a clear cross-language consensus.

**Q-A — readiness vs completion poller.** kqueue and epoll are **readiness** APIs: you
register interest in an fd and are notified when it is readable/writable, then *you*
perform the non-blocking `read`/`write`.
- epoll (Linux) — man7: edge-triggered (`EPOLLET`) "delivers events only when changes
  occur"; the documented pattern is "nonblocking file descriptors" + "waiting for an
  event only after `read(2)` or `write(2)` return **EAGAIN**". Default is level-triggered
  ("a faster `poll(2)`"). [epoll(7)](https://man7.org/linux/man-pages/man7/epoll.7.html)
- kqueue (macOS/BSD) — man: `EVFILT_READ` fires "whenever there is data available to
  read"; `EVFILT_WRITE` "whenever it is possible to write"; `EV_CLEAR` gives edge-triggered
  "state transitions instead of the current state." [kqueue(2)](https://man.freebsd.org/cgi/man.cgi?kqueue)
- This is the model our cooperative scheduler wants: on `EAGAIN`, register + park; on
  readiness, retry the non-blocking op. (Completion APIs — io_uring/IOCP — are a different
  shape, deferred.)

**Q-B — where the scheduler blocks.** Go's runtime is the reference. Its scheduler drains
the local/global/steal run queues; when **no goroutine is runnable**, it makes the poll
**blocking** — the thread sleeps inside `epoll_wait`/`kevent` until an fd is ready (or a
timer fires) — then marks the goroutines waiting on the ready fds runnable. Platform
interface: `netpollinit` / `netpollopen(fd)` / `netpoll(delay)` (blocks when `delay<0`,
returns the list of ready goroutines). We adopt exactly this: **the pump runs ready
tasks; when the ready queue is empty and tasks are I/O-parked, it blocks in the poller.**
(Go runtime `src/runtime/netpoll.go`; corroborated by multiple runtime write-ups.)

Wider consensus (exploration doc §9, verified 2026-07-13): Go, Node/libuv, Rust-Tokio/mio,
OCaml-Eio all center a single readiness poller + a park/wake scheduler; no-coloring green
threads beat colored `async` for pervasive-I/O code. Our single-thread cooperative pump is
the simplest point on that spectrum.

## 4. High-level implementation overview (for approval)

### 4.1 Top-level pump replaces recursive joins

Introduce one scheduler context `g_pump` (a `ucontext_t` on a dedicated stack, entered via
`makecontext(pump_loop)`). Every task (and task-0/`main`) **parks** by `swapcontext(&self,
&g_pump)`; the pump resumes a task by `swapcontext(&g_pump, &task)`. Consequences:

- `task->sched_return` collapses to the single `&g_pump` (the per-join `my_sched` contexts
  from L0.2 go away — **the pump subsumes recursive-join nesting**: a nested `__scope_join`
  simply parks its joiner and the pump keeps running everyone else, so nesting still works
  with *less* machinery). Per-task root contexts are retained.
- **The per-`Scope` ready queues from L0.2 collapse into ONE global ready queue.** Under the
  pump a join is a *wait on the scope's live-count*, not a queue drain, so scopes no longer
  need their own queues — a `Scope` shrinks to `{live, waiter}`. Structured semantics are
  enforced by the join-wait, not by queue partitioning. **Consequence (semantics change vs
  L0.2, §5):** an outer sibling task now interleaves with an inner scope's tasks (the L0.2
  recursive-join froze outer siblings — a C-stack artifact, not intended semantics; Trio/
  Kotlin also interleave across nesting). The `test_task_nested_scope` golden changes to a
  new deterministic order; recompute it empirically.
- `g_current_task`/`g_current_roots` are still switched by the pump immediately before each
  `swapcontext` into a task (unchanged switch-point-alignment invariant).

`pump_loop` (single authority):
```
loop:
  if global ready queue non-empty:     run next ready task (swap in)
  elif any task parked on I/O:         poll_wait(block=true) -> move ready tasks to run queue
  elif tasks parked on joins only:     deadlock -> loud fail (well-structured code can't hit this)
  else (no tasks at all):              return -> program end
```

Park reasons (all swap to `g_pump`, differing only in bookkeeping):
- **yield** — push self to the global ready queue.
- **join** — `__scope_join(s)`: if `s.live>0`, record self as `s`'s waiter and park; the
  last child to finish (`s.live→0`) moves `s`'s waiter back to the ready queue, then frees `s`.
- **I/O** — register `(fd, interest)` with the poller, tag the current task, park. On
  readiness the pump moves it to the ready queue and it retries the non-blocking syscall.

### 4.1.1 main / task-0 (load-bearing)

**main must keep the native stack + the 131072-slot `g_task0_roots`.** The self-hosted
compiler runs as Sprout `main` with deep recursion sized to that pool; a green 1 MiB /
16384-slot main would exhaust roots and break the bootstrap. So main is NOT turned into a
green task. But bare `tcp_*` calls **outside any `with_scope`** (and `tcp_echo_serve`) must
still park — the pump/poller cannot be a `with_scope`-only facility.

Resolution: **initialize the poller + `g_pump` context at startup** (a `__attribute__
((constructor))`: create the poller fd, `makecontext(pump_loop)` on its own dedicated
stack), keep main on the native stack, and **materialize a task-0 `Task` record** (`ctx`
captured at first park, `roots = &g_task0_roots`, `stack` = the native stack, never freed).
The pump then parks/resumes main uniformly. A bare `tcp_read` parks task-0 into the
always-ready pump, which blocks in the poller and resumes task-0 on readiness. The
`g_current_task == NULL ⇒ main` sentinel is replaced by the task-0 record; `task_yield` from
task-0 with no scheduled work stays a guarded no-op (nothing else to run).

### 4.2 Poller abstraction (internal C, cross-platform)

A small internal interface in a new TU `runtime/sprout_poll.c` (+ decl in `sprout_scheduler.h`),
**not** Sprout-visible:
```
void poll_init(void);
void poll_register(int fd, int interest /*READ|WRITE*/, Task* t);  /* one-shot */
void poll_wait(void);   /* block in kevent/epoll_wait; wake tasks whose fds are ready */
```
`#ifdef __APPLE__` → kqueue (`EVFILT_READ`/`EVFILT_WRITE`, `EV_ONESHOT`); `#else` → epoll
(`EPOLLIN`/`EPOLLOUT`, `EPOLLONESHOT`). First cut uses **one-shot / level-triggered**
registration (register on park, fire once, re-register on the next `EAGAIN`) — simplest to
reason about in a cooperative loop; edge-triggered (`EV_CLEAR`/`EPOLLET`) is a later
optimization, not needed for correctness.

### 4.3 Retrofit the `tcp_*` builtins

The sockets become `O_NONBLOCK`. Inside the existing `tcp_read`/`tcp_accept`/`tcp_write`
builtins (C), the blocking syscall is wrapped:
```
retry:  n = read(fd, ...);
        if (n < 0 && errno == EAGAIN) { scheduler_park_on_fd(fd, READ); goto retry; }
```
`scheduler_park_on_fd` is an internal C function (poll_register + park-to-pump). **Trap —
`connect`:** setting `O_NONBLOCK` before `connect()` makes it return `EINPROGRESS`, which
would need a write-readiness park. First cut keeps `tcp_connect`'s `connect()` **blocking**
(loopback connect is immediate) and sets `O_NONBLOCK` *after* it succeeds — only the
read/accept/write steady-state parks. (Non-blocking connect is a later refinement.)

> **Update 2026-08-10 — the deferral was wrong, and the refinement has landed.** "Loopback
> connect is immediate" holds only while the peer's accept queue has room. Once it is full the
> kernel silently drops the SYN and the *blocking* connect stalls for its full timeout —
> measured **~7.5 s on macOS**, minutes on Linux — with the OS thread frozen, so no timer could
> fire and `with_timeout` could not bound a connect at all. `tcp_connect` now goes non-blocking
> before `connect()`, parks on `SPROUT_POLL_WRITE` for `EINPROGRESS`/`EINTR`, and reads the
> outcome from `SO_ERROR` (a ready fd signals success and failure alike). Regression:
> `tests/task_io_smoke/connect_park.spr`.
>
> **Update 2026-08-10 (b) — `PARK_FD_TIMER`, a park on an fd OR a deadline.** Added for
> `tcp_read_avail_timeout`, which bounds a read so a server survives a peer that connects and then
> says nothing. The task registers on the fd *and* a timer, and `scheduler_park_on_fd_timeout`
> reports which one won; on expiry the task **resumes normally** and its caller returns
> `Err TcpTimeout` — it is *not* dropped. That distinction is the whole reason this exists rather
> than reusing `with_timeout`: a force-dropped task never runs its linear `close`, so the
> cancel-the-task model leaks the connection handle it was supposed to protect, and it would also
> cost an extra green stack per connection. Same split as Go `SetReadDeadline` / Java `SO_TIMEOUT` /
> Erlang `gen_tcp:recv` timeout versus Tokio's `timeout`.
>
> Two new invariants come with it:
> 1. **Both registrations can fire in one poll batch**, so the pump must wake the task at most once
>    (it checks `on_io_list`, which the first event clears) while still accounting for the loser.
> 2. **Timer teardown is exactly-once, tracked explicitly** via `Task.park_timer_dead`, because the
>    Linux backend `close()`s the timerfd and a second teardown would close a reused descriptor.
>    `sprout_poll_wait` therefore reports fd-vs-timer per event: kqueue reads it off
>    `evs[i].filter`, epoll (whose `epoll_event.data` carries only the token) gets it from a tag in
>    the token's low bit, set when the timer is registered. The tagging stays inside `sprout_poll.c`.
>
> Two consequences that generalize to **any** future park site, and both bit here:
> 1. **An fd no handle table owns needs `scheduler_park_on_unowned_fd`.** Every pre-existing park
>    is on an fd reachable from a handle, so a cancel-drop can leave it open and its owner still
>    closes it. An in-flight connect's socket is reachable *only* from the parked frame that
>    `force_drop_task` frees — so the drop is its last chance to be closed, via the new
>    `Task.park_close_fd`. Without it, every timed-out connect leaked a descriptor.
> 2. **Whatever is held across a park must live on the green stack.** `force_drop_task` reclaims
>    the stack and the root context; it knows nothing about C heap allocations. `getaddrinfo`'s
>    list was held across the park, so it is now copied into a stack array and freed *before* the
>    connect loop.
>
> **Update 2026-08-10 (c) — `PARK_FD_TIMER` generalized to writes, and arming a timer may now
> fail.** Follow-ups from the code review of the bounded read.
>
> * **The write side needed the same bound.** `tcp_write_all_timeout` reuses `PARK_FD_TIMER` with
>   `SPROUT_POLL_WRITE`. A client that requests a response larger than the socket buffers and then
>   stops reading — *without* closing — parked its handler in `send()` forever, so the linear `close`
>   never ran and the connection handle was never returned to the table. Bounding only the read left
>   the identical exhaustion reachable from the other direction, including via the 408/431 replies,
>   which travel the same write path. Read and write budgets are **not** symmetric, though: the header
>   read is bounded *totally* (nginx `client_header_timeout`) because a per-read timer is renewed
>   forever by a dribbling peer, while the body read and the response write are bounded on **idle**
>   gaps (`client_body_timeout` / `send_timeout`), because a total bound there cuts off legitimately
>   slow large transfers.
> * **`sprout_poll_add_timer` now returns success/failure instead of aborting.** On Linux each timer
>   costs a `timerfd`, so a per-connection bounded read allocates a descriptor every time it blocks
>   and arming is the first thing to fail under `ulimit -n` pressure. Aborting there dropped every
>   in-flight connection to punish the one that could not be armed. The **caller** now decides, and
>   the split is by whether an honest degradation exists: `scheduler_park_on_fd_timeout` returns
>   "timed out", so the server sheds that one connection with a 408 and frees descriptors, while
>   `task_sleep` and `with_timeout` still fail loudly — there is no answer to give, and returning
>   early would silently break the only guarantee either one makes. It also arms the timer *first*,
>   before the fd, so a failure needs no unwind.
> * **The remaining cost is structural, not a bug**: one descriptor per parked bounded read on Linux,
>   invisible on kqueue (`EVFILT_TIMER` uses none). Removing it means a timerfd-free backend — one
>   shared timerfd plus a deadline heap, what a production reactor does — which would also delete the
>   `park_timer_dead` exactly-once dance above, since that invariant exists *only* because a timerfd
>   close must happen exactly once. Tracked in `BACKLOG.md`; deliberately not bundled with the review
>   fixes, because it rewrites timer semantics for `task_sleep`, `with_timeout` and `select` across
>   both backends at once.

 **No new
Sprout-visible builtin is required** for I/O parking — the new capability is internal
runtime plumbing; the Sprout-visible change is only that the existing `tcp_*` builtins now
*park* rather than *block*. (Builtin-vs-stdlib rule 6: this is a correctness/behavior change
to existing effect-oriented builtins, not a new pure helper; the poller cannot be expressed
in Sprout — it needs `kqueue`/`epoll` syscalls and context switching.) **This point is the
one that most needs your explicit sign-off** even though it adds no `APPROVED_BUILTINS`
line: it changes the semantics of shipped builtins.

### 4.4 Rooting across a park — already validated

A task parked on I/O keeps its green stack (non-moving) and its registered root context;
`mark_roots` scans all registered contexts, so its live values survive a GC storm driven by
another task. Spike #2 demonstrated exactly this (root held across a real 2-frames-deep park,
200-alloc storm). No collector or rooting change.

## 5. Syntax and semantics impact

- **Syntax:** none.
- **Semantics:** `with_scope`/`task_spawn` (né `scope_spawn`)/`task_yield` unchanged in meaning. New: `tcp_*`
  operations are now *suspension points* (a task may interleave at any socket op, not only at
  `task_yield`). This is observable — cooperative interleaving becomes finer-grained — but
  stays deterministic given a fixed I/O readiness order (readiness itself is nondeterministic,
  as with any real network). Structured-concurrency guarantee is preserved: `with_scope` still
  cannot return until all its tasks finish (now including tasks blocked on I/O).
- **Nesting interleaving changes (from L0.2).** With the single global ready queue (§4.1), an
  outer sibling task interleaves with an inner scope's tasks rather than being frozen until the
  inner scope drains. This matches Trio/Kotlin (nesting governs *lifetime/cancellation*, not
  scheduling exclusion); the L0.2 freeze was a recursive-C-stack artifact. Lifetime is
  unchanged — an inner scope still fully drains before its opener proceeds past `with_scope`,
  and an outer scope before `main` proceeds. `test_task_nested_scope`'s asserted order is
  updated accordingly (recomputed empirically, still deterministic).

## 6. Type-system impact

None. No new types, classes, or effects. (I/O already carries `!{IO}`; parking is within `IO`.)

## 7. Error-message impact

- New loud failures: poller syscall errors (`kqueue`/`epoll_create` failure → `sprout_fail`);
  the pump's `deadlock` branch (all tasks parked on joins, none on I/O and none ready) — a
  structured-concurrency bug, reported loudly.
- Retained: `task_yield` outside a task.
- The existing `tcp_*` error returns (EPIPE, connection errors) are unchanged; only `EAGAIN`
  is intercepted for parking.

## 8. Compatibility / migration notes

- Non-concurrent programs (no `with_scope`) are unaffected: with a single task, a `tcp_*`
  park immediately finds nothing else ready and the pump blocks in the poller — behaviorally
  identical to today's blocking call (one extra poller round-trip).
- The L0.2 per-join return-context code is **replaced** by the pump; nested-scope behavior is
  preserved (re-verified by the existing `test_task_nested_scope`). The pump is a net
  simplification of the drive path.
- Bootstrap/seed: runtime-only change (+ new `runtime/sprout_poll.c`, auto-globbed). No
  compiler-source or seed impact.

## 9. Tests added/updated

1. **Concurrent I/O interleave (new) — a TIMEOUT SMOKE, not a `just test` assert.** With the
   *blocking* baseline, a task that reads before its peer writes freezes the OS thread and the
   program **deadlocks** (no sibling runs to produce the data) — so the RED signal is a hang,
   caught by a timeout wrapper (like `stack-overflow-smoke`), and it must live outside
   `just test` (which has no per-test timeout and would hang the whole suite). GREEN = the
   parked version *completes within the timeout AND prints the interleaved order* (assert the
   output, not just completion — completion alone passes on wrong ordering). Keep a scratch
   negative control so a *setup* hang isn't mistaken for success.
2. **Park + GC-stress (rooting):** a task holds a heap value across an I/O park while a second
   task allocates heavily; assert the value survives. Added to `test-stress` (the netpoller is
   a new rooting surface).
3. **Single-task I/O unchanged (regression):** a no-`with_scope` program using `tcp_*` still
   works (park→poll→resume path with nothing else runnable).
4. **Nested-scope behavior preserved** under the pump: `test_task_nested_scope` must stay green.
5. **CI on Linux (epoll path):** all of the above must pass on the Linux CI runner, not just
   macOS/kqueue — the epoll backend is exercised only there.
6. Loud-failure smokes: poller-init failure and the pump deadlock branch (fixture that parks a
   joiner with no runnable/I/O tasks) — via the `task-guard-smoke` family.

TDD order: write test 1 red (against the current blocking `tcp_*`, it serializes → wrong
interleaving) before the retrofit.

## 10. Spec/docs updated

- This doc is the approved-design artifact; **experimental**, not normative.
- On landing: update `docs/concurrency-design-exploration-2026-07-13.md` status (netpoller
  LANDED, pump replaces recursive joins), the L0 milestone note, and BACKLOG.md V1 roadmap.
- `docs/spec-v0.md` is untouched (concurrency stays out of the stable core until promoted).

---

## Open questions for the approver

1. **Scope of this increment:** just the pump + kqueue/epoll + `tcp_*` retrofit (recommended),
   or also fold in a minimal `task_sleep` timer (adds timeout-driven `poll_wait`)?
2. **Poller triggering:** start with one-shot/level-triggered (recommended, simplest) and defer
   edge-triggered, or go edge-triggered from the start?
3. **`tcp_*` semantics change** (§4.3): confirm you're OK making the *existing* blocking builtins
   park — the only builtin-policy question here (no new `APPROVED_BUILTINS` entries).
