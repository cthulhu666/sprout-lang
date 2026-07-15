# task_sleep — design proposal (2026-07-15)

**Status: APPROVED (2026-07-15).** New builtin cleared with Kuba up front (AGENTS.md
Builtin-vs-Stdlib #6). Per the Design Change Process. Builds on L0.1–L0.5 (green-thread
scheduler, nested scopes, I/O netpoller, result-carrying tasks, cooperative cancellation
+ I/O-drop). Reuses the L0.3 park machinery and the L0.5 `g_io_head` force-drop path
almost entirely — the only net-new capability is a one-shot timer in the poller.

---

## 0. TL;DR

- A green task can currently give up the CPU only by hitting I/O (`tcp_*` park on
  `EAGAIN`) or by calling `task_yield()` by hand. There is no way to suspend for a
  *duration*. Manual `task_yield` in business logic is unrealistic (real cooperative code
  yields implicitly at I/O; `task_yield` is Go's `runtime.Gosched()` — a rare escape hatch).
- `task_sleep(ms)` adds a **cooperative sleep**: suspend the current task for ~`ms`
  milliseconds, running its siblings meanwhile. It is the enabler for realistic timed-work
  examples and, later, the substrate for timeouts/deadlines.
- **Recommended MVP:** `task_sleep(ms: Int) -> Unit !{IO}`. Park the task on a one-shot
  poller timer; the pump resumes it when the timer fires — the same flow as an fd wakeup,
  so a sleeping task rides the existing `g_io_head` list and is **force-dropped by
  `scope_cancel` for free**.

## 1. Problem statement

Interleaving today requires either real sockets or hand-placed `task_yield()`. The latter
is not how real programs are written — it exists for CPU-bound loops that would starve
siblings, nothing more. Without a timer primitive we cannot express "do a bit of work,
wait, do more" without busy-waiting, and we have no basis for deadlines. `task_sleep` fills
the gap and is the first *timer* the runtime exposes.

## 2. Goals / non-goals

**Goals**
- Suspend the current task for a duration; run siblings meanwhile; resume it cooperatively.
- Reuse the L0.3 park path and the L0.5 `g_io_head` force-drop — no parallel machinery.
- A sleeping task in a cancelled scope stops instantly (dropped like an I/O-parked task).
- Behave identically on kqueue and epoll (the two blockers in §5 are exactly the places
  where a naive port diverges).

**Non-goals (this increment)**
- Precise timing / real-time guarantees — cooperative scheduling means "at least `ms`,
  then when the pump next runs me," matching every cooperative runtime.
- `task_status()` / `TimedOut` — the *second* stop-reason (`Cancelled` vs `TimedOut`) only
  becomes real with deadlines; deferred (see the cancellation doc §9/§10.4). `task_sleep`
  alone adds no new return type.
- A userspace timer heap (one `poll_wait` timeout for N timers) — see §7 scaling note.

## 3. Prior-art survey (verified against primary sources, 2026-07-15)

| System | Primitive | Suspends | Unit |
|---|---|---|---|
| **Go** | `time.Sleep(d Duration)` | the goroutine (not the OS thread) | Duration |
| **Kotlin** | `delay(timeMillis: Long)` | the coroutine | **milliseconds** |
| **Swift** | `Task.sleep(for:)` / `sleep(nanoseconds:)` | the task | Duration / ns |
| **Python Trio** | `trio.sleep(seconds)` | the task (a checkpoint) | seconds |
| **Rust tokio** | `tokio::time::sleep(Duration)` | the task | Duration |

**Consensus:** a cooperative sleep that parks the *task*, never the thread; `d <= 0`
returns promptly (Go: returns immediately; Kotlin `delay(0)` ≈ a yield point). Unit varies;
**milliseconds** (Kotlin's `delay`) is the ergonomic default for an `Int` API — no
`Duration` type in Sprout yet.

### Sources
- Go `time.Sleep`: https://pkg.go.dev/time#Sleep
- Kotlin `delay`: https://kotlinlang.org/api/kotlinx.coroutines/kotlinx-coroutines-core/kotlinx.coroutines/delay.html
- Swift `Task.sleep`: https://developer.apple.com/documentation/swift/task/sleep(for:tolerance:clock:)
- Trio `trio.sleep`: https://trio.readthedocs.io/en/stable/reference-core.html#trio.sleep
- tokio `sleep`: https://docs.rs/tokio/latest/tokio/time/fn.sleep.html

## 4. Design

**API** (`stdlib.task`):

```sprout
export fn task_sleep(ms: Int) -> Unit !{IO}
```

The Sprout wrapper handles the `ms <= 0` case (§5.2); `ms > 0` calls the scheduler extern
`__task_sleep`, which parks the current task on a one-shot timer and returns when it fires.

**Mechanism** — the task parks exactly like an I/O park, but on a timer instead of an fd:

- `scheduler_park_on_timer(ms)`: arm a one-shot timer (`sprout_poll_add_timer` → an opaque
  `timer_id`), record `park_kind = PARK_TIMER` + `park_timer_id` on the `Task`, link onto
  `g_io_head`, and park to the pump. On resume, `sprout_poll_remove_timer(timer_id)` tears
  the timer down (kqueue: no-op via `ENOENT`, one-shot auto-removed; epoll: `EPOLL_CTL_DEL`
  + `close` the timerfd).
- The pump is **unchanged**: `poll_wait` returns the fired timer's token (the `Task`) just
  like a ready fd's token; the pump unlinks it from `g_io_head` and re-enqueues it.
- Backends (in `sprout_poll.c`):
  - **kqueue:** `EVFILT_TIMER`, `EV_ADD | EV_ONESHOT`, `NOTE_MSECONDS`, `data = ms`,
    `ident = (uintptr_t)token` (a task sleeps on ≤1 timer, so the `Task*` is a unique
    ident; the `EVFILT_TIMER` ident namespace is disjoint from the fd read/write filters).
  - **epoll:** `timerfd_create(CLOCK_MONOTONIC, TFD_CLOEXEC)` + `timerfd_settime` (one-shot,
    relative), registered `EPOLLIN | EPOLLONESHOT` with `data.ptr = token`. The timerfd is
    closed exactly once — on resume *or* on drop, never both (mutually exclusive: a dropped
    task never resumes).

**Cancellation interaction** — a sleeping task is on `g_io_head`, so `__scope_cancel`
already finds it. The only new logic is the deregister switch at the drop site:

```c
if (t->park_kind == PARK_TIMER) sprout_poll_remove_timer(t->park_timer_id);
else                            sprout_poll_remove(t->park_fd, t->park_interest);
```

The reclaim (free roots + stack together, §7 of the cancellation doc) is identical — a
sleeping task holds stack-rooted values exactly like an I/O-parked one.

## 5. The two portability blockers (must be handled, not reasoned around)

### 5.1 Async fire vs. the `g_io_head` no-race invariant

The cancellation doc §7 argues "a task still on `g_io_head` at `scope_cancel` time has not
fired, because the pump drains every `poll_wait` token before running the owner." **That
holds for fds but NOT for timers:** a timer expires on the kernel's schedule while the
owner is running, so its event can be *fired but undrained* when `scope_cancel` drops the
task. If the fired event survives deregistration, the next `poll_wait` returns a token
pointing at the **freed** `Task` → `rq_push` + `swapcontext` into freed memory.

Correctness therefore depends on `sprout_poll_remove_timer` **discarding an already-fired,
unretrieved timer event:**
- **epoll:** `EPOLL_CTL_DEL` removes the fd from the set (a subsequent `epoll_wait` will not
  return it) and `close` frees it. Safe.
- **kqueue:** `EV_DELETE` on a triggered-but-unretrieved `EVFILT_TIMER` knote — believed to
  discard the pending event, but **load-bearing and verified by test**, not by reasoning
  (§6 test 3, the fired-timer-drop ASan negative control). If it leaks, the fallback is a
  deferred-free / token-validity check before `rq_push`; not built unless the test forces it.

### 5.2 `ms <= 0`

`timerfd_settime` with `it_value == {0,0}` **disarms** the timer (never fires) → the task
parks forever → **hang on Linux**, while kqueue `NOTE_MSECONDS data=0` fires immediately.
Resolved in the Sprout wrapper: **`ms <= 0` yields once (`task_yield`) without arming a
timer** — no backend divergence, and a useful "give siblings a turn" semantics (Kotlin
`delay(0)` ≈ yield). Documented and tested.

## 6. Tests

1. **Interleaving:** task A `task_sleep`s while task B runs to completion; assert B's output
   precedes A's wake — proves the sleeper yields the thread and resumes.
2. **`ms <= 0`:** `task_sleep(0)` completes promptly (does not hang) and yields — the
   epoll-divergence guard.
3. **Fired-timer drop (the §5.1 negative control), ASan, BOTH backends:** a child
   `task_sleep`s briefly; the owner busy-spins well past the sleep so the timer *definitely*
   fires kernel-side; the owner `scope_cancel`s (dropping the child mid-fired-timer); a
   surviving sibling then forces another `poll_wait`. Under ASan a leaked stale token →
   freed-`Task` access. Distinct from the L0.5 roots-UAF control.
4. **Cancel-drop of a sleeper:** a plain `task_sleep` in a cancelled scope stops without a
   hang (reuses the L0.5 drop path).
5. Green under `SPROUT_GC_STRESS=1`.

## 7. Impact — Design Change Process checklist

- **Syntax:** none — one new library function.
- **Type system / effects:** none — `task_sleep(ms: Int) -> Unit !{IO}`.
- **Error messages:** none new.
- **Compat/migration:** fully additive.
- **Builtins:** one scheduler extern `__task_sleep(ms)` (impossible in Sprout — needs
  kqueue/epoll timers) + internal-C `sprout_poll_add_timer`/`sprout_poll_remove_timer` (not
  Sprout-visible). `APPROVED_BUILTINS` updated.
- **Scaling boundary (deliberate deferral):** one kernel timer per concurrent sleeper —
  free on kqueue, **one timerfd per sleeper on epoll/Linux**. Fine for the MVP; if
  deadlines / many-timer workloads arrive, the standard move is a userspace timer heap
  feeding a computed `poll_wait` timeout (the libuv/tokio/Go-netpoller model), not one
  kernel timer each. Noted so this is a known boundary, not a surprise.
- **Tests:** §6. **Spec/docs:** experimental; documented here. Not in `docs/spec-v0.md`
  until the concurrency model graduates.
