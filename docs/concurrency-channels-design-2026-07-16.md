# Channels (L0.8) — design proposal (2026-07-16)

**Status: IMPLEMENTED (2026-07-16, branch `concurrency-l07-deadlines`).** `Chan a` +
`chan_new`/`chan_send`/`chan_recv` in the new `stdlib.chan`; runtime `__chan_new`/`__chan_send`/
`__chan_recv` + `PARK_CHAN` force-drop in `sprout_scheduler.c`; `scope_cancel`/`with_timeout`
integration. All DoD gates green: full `just test` (incl. `test_chan.spr`), `task-io-smoke`
(incl. new `cancel_chan_drop` + `timeout_chan_drop`, plain + GC-stress), ASan-clean channel
force-drop with a verified negative control (freed-stack UAF in `sprout_gc_mark_root_list`),
`compile-examples-stage1`, `run-example-canary`, seed fixed-point unchanged (`stdlib.chan`/
`stdlib.task` are not bundled). **Both-backends note:** the channel runtime calls **zero poller
functions** (wait-queues are pure in-memory scheduler state); the only poller interaction in the
fixtures is `timeout_chan_drop`'s deadline timer, which is unchanged L0.7 code already verified on
kqueue + epoll. So there is no new backend-divergent code. **Discovered en route:** a pre-existing
silent-miscompile — a user top-level function name colliding with an imported function's parameter
name (`with_timeout`'s `body`) resolves to the user global and eta-expands it, discarding the real
closure argument (tracked in BACKLOG §1; the `timeout_chan_drop` fixture avoids the name `body`).
**Also added:** `stdlib.task.scope_handle` — a read-only accessor exposing a scope's raw handle so
`stdlib.chan` can pass it to `__chan_new` without owning the opaque `Scope` constructor (keeps
`Scope` unforgeable; see §8). Scope locked: **bounded buffered channels only** (capacity ≥ 1,
with backpressure) in a **new `stdlib.chan` module**; rendezvous (capacity 0), `close`, and
`select` are deferred fast-follows (§9). Builds on the shipped L0.1–L0.7 scheduler
(`runtime/sprout_scheduler.c`): one OS thread, cooperative, no preemption; tasks park by
swapping to the pump and are woken by `rq_push`. Channels are the first inter-task *streaming*
communication primitive — until now tasks could only pass one value parent→child via
`task_await`.

**Decisions locked with Kuba (2026-07-16):**
- **Bounded buffered, capacity ≥ 1**, Go/Kotlin/Rust `sync_channel` semantics: `chan_send`
  parks when the buffer is full, `chan_recv` parks when it is empty. `capacity < 1` loud-fails
  (rendezvous deferred — same shape as `task_sleep`'s `ms <= 0` guard). **[§9.1]**
- **New `stdlib.chan` module** (not folded into `stdlib.task`). De-risked: cross-module stdlib
  imports are established (`stdlib.http_server` ← `stdlib.http`/`stdlib.net`), `Chan a = Chan Int`
  is shape-identical to the shipped `Task a = Task Int` (phantom param, proven to bundle), and
  the `export extern fn`-not-qualifiable bundler limitation is avoided because the `__chan_*`
  externs stay internal to the module (the public API is `export fn` wrappers). **[§8]**
- **Scope-tied lifetime**: a channel is created *into a `Scope`* and freed at `__scope_join`,
  exactly like forks — Sprout has no finalizers, so a free-floating channel could not be
  reclaimed. Limitation accepted: no top-level channels (task-0 has `scope == NULL`) and a
  channel cannot outlive its creating scope. **[§4]**
- **Three new builtins** (`__chan_new`, `__chan_send`, `__chan_recv`) — scheduler primitives
  (parking / context-switching), same justification class as the existing `__scope_*`/`__task_*`
  set; impossible to express in Sprout. **Approved up front** per AGENTS.md "Builtin vs Stdlib"
  and Collaboration Rule 6. **[§7]**
- **Force-drop integration is in-scope, not deferred** — a channel-parked task is a new park
  reason that `scope_cancel` and `with_timeout` (both *shipped*) must be able to drop, else
  timing out or cancelling a `chan_recv` regresses into a loud-fail / deadlock-panic (§6, the
  critical section).

---

## 0. TL;DR

- A channel is a **third park reason** alongside "parked on fd" (L0.3) and "parked on timer"
  (L0.6). The queue itself is trivial; the real work is making the new park state obey the two
  invariants every prior increment established: **force-droppable** (§6) and **GC-rooted across
  the park** (§5).
- `chan_send`/`chan_recv` reuse the exact park/wake mechanism the scheduler already has
  (`park_to_pump` / `rq_push`). No new scheduler control flow — a channel is a wait-queue plus a
  ring buffer.
- Channel-parked tasks are deliberately **kept off `g_io_head`** (they have no poller
  registration). This both avoids wedging the pump in an empty `poll_wait` and *preserves* the
  correctness of the deadlock-panic (§6.3).

---

## 1. Problem statement

Concurrent tasks cannot stream values to each other. `task_await` delivers exactly one value,
parent→child, and the handle is known at fork time. The exploration doc's motivating shape
(§4.B — a fan-in of concurrent query results) and any producer/consumer pipeline need a
first-class channel: a queue one task sends into and another receives from, with the scheduler
parking a sender that outruns the receiver (backpressure) and a receiver that outruns the sender.

## 2. Goals / non-goals

**Goals.** A bounded buffered channel usable between tasks in a scope; backpressure (send parks
when full); blocking receive (parks when empty); full integration with the shipped cancellation
and deadline machinery (a channel-parked task is droppable); GC-correct buffering (a value
sitting in the buffer or held by a parked sender stays rooted).

**Non-goals (this increment).** Rendezvous (capacity 0); `close` + recv-on-closed signalling;
`select`/multiplexing; multi-producer fairness guarantees beyond FIFO wake order; top-level
(scope-less) channels; multi-core. Each is a clean separate increment (§9).

## 3. Prior-art survey (verified against primary sources)

The decision axis is buffered vs unbuffered vs unbounded, plus the "done" signal — a question
Go, Kotlin, and Rust all faced.

| Language | Bounded buffered | Rendezvous (cap 0) | Unbounded | "Done" signal | Multiplex |
|---|---|---|---|---|---|
| **Go** (spec, Channel types) | `make(chan T, n)` — send blocks if full, recv if empty | `make(chan T)` / cap 0 — "communication succeeds only when both a sender and receiver are ready" (**default**) | — | `close(ch)`: recv yields zero+`ok=false`; **send panics** | `select` |
| **Kotlin** (kotlinx.coroutines `Channel`) | `Channel(n)` — `send` suspends if full, `receive` if empty | `Channel()` = `RENDEZVOUS` (**default**) | `Channel(UNLIMITED)` | `close()` | `select` |
| **Rust** (std `sync::mpsc`) | `sync_channel(n)` — send blocks if full | `sync_channel(0)` — sender hands off atomically | `channel()` — send never blocks | drop all senders → recv `Err` | — |

**Consensus:** all three offer bounded-buffered + rendezvous + a done-signal; Go/Kotlin add
`select`. **Notable divergence I am consciously deferring:** rendezvous is the *default* in both
Go and Kotlin. The MVP ships bounded-buffered-only because it is the increment that exercises
*both* park directions (send-park and recv-park) and the force-drop integration, while
rendezvous's direct sender→receiver handoff is separable added complexity (§9.2). `close` is the
most-likely-wanted follow-up (a streaming consumer needs it to know when to stop) — flagged as
the immediate fast-follow (§9.3).

Sources: Go spec §Channel types (go.dev/ref/spec); kotlinx.coroutines `Channel` API reference;
Rust `std::sync::mpsc` module docs.

## 4. Lifetime — scope-tied

`chan_new(scope, capacity)` allocates a non-moving `Chan` record and links it into
`scope->chans`. `__scope_join` frees every channel in the list (alongside the forks) after all
tasks have finished — at which point no task is parked on any of them, so the free is safe. The
`Chan` pointer is encoded as the `Int` the Sprout `Chan a` value wraps (the `Scope`/`Task` handle
ABI), valid from `chan_new` until the owning scope closes.

Limitation (documented in `stdlib/chan.sprout`): a channel cannot outlive its creating scope, and
there are no top-level channels. Both are acceptable — channels exist to let *sibling tasks in a
scope* communicate, which is precisely the scope's dynamic extent.

## 5. GC rooting

A buffered value may be a heap pointer; the collector must find it. The `Chan` owns its own
`SproutRoots` context (registered → scanned by `mark_roots` via the registry). At creation, each
of the `capacity` buffer-slot **addresses** is `push_ptr`'d once (the buffer is a fixed
non-moving malloc, so slot addresses are stable for the channel's life). Empty slots hold `0`;
the mark path is membership-guarded, so rooting a slot that currently holds a scalar (an `Int`
value, or `0`) is a safe no-op — the exact pattern the trampoline uses for `&t->result`.

- **Dequeue clears the slot to `0`** after the value is read out, so a drained slot never keeps a
  stale pointer alive (no bounded leak).
- **A send-parked task** (full buffer) holds its pending value in a new `Task.chan_pending` field,
  rooted via `push_ptr(&t->chan_pending)` into the task's own root context — reachable while the
  task is suspended (same as `&t->work`/`&t->result`; `push_ptr` has no pop, so it stays for task
  life, which is bounded and fine). Only the backpressure path needs this; a non-full send
  deposits straight into a channel-rooted slot and returns without parking.

## 6. Force-drop integration (critical)

Channel-parked tasks are a new park reason. `scope_cancel` and `with_timeout` are already
shipped; without integration:

- `with_timeout(ms, \_ -> chan_recv(ch))`: the deadline fires, `__await_deadline` classifies the
  child — not `on_io_list`, not `in_rq` — and falls to the final `else`, **loud-failing**.
- `scope_cancel` on a scope with a `chan_recv`-parked task: the `g_io_head` walk never sees it, so
  it stays parked, `__scope_join` parks the waiter, and the pump hits **deadlock-panic**.

Both are regressions of landed guarantees. So this increment integrates:

### 6.1 New park reason `PARK_CHAN`

`Task` gains `park_kind == PARK_CHAN`, a `park_chan` back-pointer to the `Chan`, and
`chan_prev`/`chan_next` links for the channel's wait-queue (doubly-linked so force-drop can
O(1)-unlink from the middle). A channel-parked task is on **exactly one** of the channel's two
FIFO wait-queues (`send_waiters` when the buffer is full, `recv_waiters` when empty) and on
`g_io_head` **never**.

### 6.2 `force_drop_task` gains a `PARK_CHAN` branch

Instead of `sprout_poll_remove`, it unlinks the task from `park_chan`'s wait-queue. The parked
sender's `chan_pending` value dies with the task (never delivered; nobody else references it —
correct). Roots-then-stack reclamation and the awaitable/fire-and-forget record handling are
unchanged.

### 6.3 Making cancel/timeout *find* channel-parked tasks

Channel-parked tasks are off `g_io_head`, so the `g_io_head` walk cannot reach them. Because
channels are scope-tied, `scope_cancel` additionally walks `scope->chans` and, for each channel,
its `send_waiters`/`recv_waiters`, force-dropping tasks whose `scope == s`. For `with_timeout`,
`__await_deadline`'s classification gains a `park_kind == PARK_CHAN` case that force-drops the
child exactly like the `on_io_list` (direct-I/O) case.

**Keeping channel-parked tasks off `g_io_head` preserves the deadlock-panic's correctness.** On a
single capacity ≥ 1 channel, an empty-buffer recv-parked task and a full-buffer send-parked task
cannot coexist (a sender facing an empty buffer deposits instead of parking; a receiver facing a
full buffer takes instead of parking). So `rq empty ∧ g_io_head empty ∧ some task channel-parked`
remains a genuine deadlock (no runnable task, no poller event that can wake anyone) and the panic
firing there is correct — e.g. a lone task recv-parked on a channel nobody will send to.

## 7. New builtins

```
extern fn __chan_new(scope_id: Int, capacity: Int) -> Int  !{IO}
extern fn __chan_send(chan_id: Int, value: a)      -> Unit !{IO}
extern fn __chan_recv(chan_id: Int)                -> a    !{IO}
```

All three require scheduler parking / green-thread context switching and therefore cannot live in
Sprout — the identical justification as `__scope_open`/`__task_fork`/`__task_await`. Added to
`runtime/APPROVED_BUILTINS`. Net builtin churn: **+3**.

## 8. Module `stdlib.chan`

```
module stdlib.chan
import stdlib.task (Scope)

export type Chan a =
  | Chan Int

fn __chan_new(...)   # internal externs, called unqualified in-module
...

export fn chan_new(scope: Scope, capacity: Int) -> Chan a !{IO} =
  match scope with
  | Scope sid -> Chan(__chan_new(sid, capacity))

export fn chan_send(ch: Chan a, value: a) -> Unit !{IO} =
  match ch with
  | Chan cid -> __chan_send(cid, value)

export fn chan_recv(ch: Chan a) -> a !{IO} =
  match ch with
  | Chan cid -> __chan_recv(cid)
```

`Chan a` is a phantom-param wrapper over the runtime pointer, shape-identical to the proven
`Task a`. The externs are internal (never accessed as `chan.__chan_*`), so the `export extern fn`
qualification bundler bug does not apply.

## 9. Deferred (fast-follows)

1. **§9.1 done (L0.8)** — bounded buffered, cap ≥ 1.
2. **§9.2 rendezvous (cap 0) — DONE (L0.10).** Direct sender→receiver hand-off, no buffer, both
   sides rendezvous (Go/Kotlin default). Smaller than the "doubles the send path" this doc
   anticipated: the send path was already correct for cap 0 — its `recv_waiters`-first check hands
   off to a waiting receiver, and its `count < cap` buffer step is dead (`0 < 0`), so it parks. The
   only new mechanism is the symmetric recv-side branch — on an empty channel with a parked sender,
   take the sender's value directly and wake it — which is provably unreachable for cap ≥ 1 (a
   sender parks only when `count == cap ≥ 1`, so `count == 0` ⇒ no send-waiters). `__chan_new` now
   accepts 0 (no buffer: `buffer = NULL`, empty roots context; `< 0` still loud-fails). The
   deadlock-panic (§6.3) stays correct: send checks `recv_waiters` and recv checks `send_waiters`
   before either parks, so a cap-0 channel never holds both a parked sender and a parked receiver.
   Tests: `tests/stdlib/test_chan_rendezvous.spr` (send-blocks-until-received value oracle, FIFO
   hand-off, heap hand-off under GC stress) + `tests/task_io_smoke/cancel_rendezvous_send_drop.spr`
   (force-drop of a send-parked task). RED→GREEN verified via the deadlock-panic negative control.
3. **§9.3 `close` + recv-on-closed — DONE (L0.9).** A producer signals "no more values"; recv
   drains buffered values then returns a done-marker. Return shape chosen: **`Recv a = Got a |
   Closed`** (dedicated type, extensible to a `Got | Closed | Empty` for a future non-blocking
   try-recv, where `Maybe` cannot grow a third case). See
   `docs/concurrency-channel-close-design-2026-07-16.md`.
4. **§9.4 `select` — DONE (L0.11).** Wait on the first-ready of several channel ops.
   `chan_select(List (Chan a)) -> (Int, Recv a)` — homogeneous (same element type), recv-only,
   list-based; lowest-index tie-break; a closed channel is always ready. Implemented native (a
   fork-race would break exactly-once delivery), bolt-on `select_waiters` alongside the shipped
   `recv_waiters`. Send-cases, a non-blocking `default`, and randomized fairness stay deferred.
   See `docs/concurrency-select-design-2026-07-17.md`.

## 10. Tests (TDD)

Red first, confirmed red for the right reason (wrong output / hang, not a parse/import error):

- **Round-trip** — `chan_new(s, 4)`, send then recv within one task; value survives.
- **Backpressure producer/consumer** — buffer smaller than the message count forces the producer
  to send-park; consumer drains; all messages received in FIFO order.
- **Fan-in** — N forked producers each send into one channel; one consumer recvs N values.
- **Heap-value rooting oracle** — buffered values are heap strings; a GC storm from another task
  runs while values sit in the buffer / a sender is parked; values survive (green under
  `SPROUT_GC_STRESS`).
- **`scope_cancel` drops a `chan_recv`-parked task** — fixture under `tests/task_io_smoke/`:
  worker recv-parks on an empty channel nobody sends to; owner cancels; scope completes instead
  of deadlocking (perl-alarm timeout, plain + stress).
- **`with_timeout` on a `chan_recv` body** — returns `Expired`; the child is force-dropped.
- **ASan force-drop negative control** — per the L0.5 lesson, `SPROUT_GC_STRESS` alone is not
  enough for a freed-*stack* UAF (a freed green stack still holds valid-looking root addresses).
  The dropped task binds a heap value rooted across the channel park; a churn loop forces a GC
  between `scope_cancel` and `__scope_join`; the correct version is ASan-clean, and a negative
  control (keep roots, free stack) deterministically ASan-UAFs.

Both poller backends (kqueue native + epoll via `docker run silkeh/clang` on Linux aarch64) — the
cancel/timeout fixtures gate through the pump, so the epoll path is exercised even though channels
themselves touch no fd.

## 11. Runtime data structures (implementation sketch)

```c
typedef struct Chan {
  long long*   buffer;      /* ring of `cap` slots; each rooted via `roots` */
  long long    cap, count, head, tail;
  SproutRoots* roots;       /* roots every buffer slot (registered, scanned) */
  Task*        send_head; Task* send_tail;   /* FIFO of send-parked tasks (full)  */
  Task*        recv_head; Task* recv_tail;   /* FIFO of recv-parked tasks (empty) */
  Scope*       scope;
  struct Chan* scope_next;  /* scope->chans list link */
} Chan;
```

`Scope` gains `struct Chan* chans`. `Task` gains `long long chan_pending`, `struct Chan*
park_chan`, `struct Task* chan_prev`/`chan_next`, and `PARK_CHAN` in the `park_kind` enum.

**Wake protocol — direct handoff (not condvar re-check).** In a cooperative single-threaded
scheduler the waker can perform the transfer on the parked task's behalf, so the woken task just
completes — no re-check, no re-park, no spurious-wakeup race. Every task's `chan_pending` is
`push_ptr`-rooted **once at `task_create`** (initialized `0`), so it is a rooted delivery slot in
*both* directions (the value I am sending while parked, or the value handed to me while parked);
a task is only ever at one channel park point, so the two uses never collide. `chan_pending` is
cleared to `0` after it is consumed.

- **`__chan_send(ch, value)`**:
  - `recv_waiters` non-empty (⟹ buffer empty): pop receiver `r`, `r->chan_pending = value`, unlink
    + `rq_push(r)`, return. *Direct handoff; buffer stays empty.*
  - else if `count < cap`: `buffer[tail] = value`, advance `tail`, `count++`, return.
  - else (`count == cap`, full): `chan_pending = value`, enqueue on `send_waiters`, `PARK_CHAN`,
    `park_to_pump`. On wake, a receiver has already moved our `chan_pending` into the freed slot —
    just return `Unit`.
- **`__chan_recv(ch)`**:
  - `count > 0`: `v = buffer[head]`, `buffer[head] = 0`, advance `head`, `count--`. Then if
    `send_waiters` non-empty (⟹ we were full): pop sender `s`, `buffer[tail] = s->chan_pending`,
    `s->chan_pending = 0`, advance `tail`, `count++`, unlink + `rq_push(s)`. Return `v`. *The
    parked sender's value lands at the tail — FIFO preserved.*
  - else (empty): enqueue on `recv_waiters`, `PARK_CHAN`, `park_to_pump`. On wake, a sender has
    placed the value in our `chan_pending` — read it, clear it to `0`, return it.

Invariants (both discharged by `cap ≥ 1`): `send_waiters` non-empty ⟹ `count == cap`;
`recv_waiters` non-empty ⟹ `count == 0`. They are mutually exclusive, so a channel never has both
parked senders and parked receivers — which is also what makes §6.3's deadlock-panic correct.
```
