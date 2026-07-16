# Channel `close` + recv-on-closed (L0.9) — design (2026-07-16)

**Status: APPROVED (return shape ratified by Kuba 2026-07-16), implementing via TDD.**
Fast-follow to the L0.8 bounded-buffered channels (`docs/concurrency-channels-design-2026-07-16.md`
§9.3). Adds a producer-side "no more values" signal so a streaming consumer knows when to stop,
which buffered channels alone cannot express (`chan_recv` on a drained channel parks forever).

## 1. Problem statement

A streaming consumer loops `chan_recv` until the producer is done — but nothing tells it when.
Without `close`, a consumer that has received the last value parks forever on the next `chan_recv`,
and the scope cannot join. `close` lets any task holding the channel signal end-of-stream; `chan_recv`
then drains whatever is buffered and reports closed.

## 2. Goals / non-goals

**Goals.** `chan_close(ch)`; `chan_recv` reports closed (after draining any buffered values);
a receiver already parked on an empty channel is woken and told closed; full integration with the
shipped cancellation/deadline force-drop (unchanged — a closed channel's tasks are still `PARK_CHAN`).

**Non-goals (this increment).** Rendezvous (capacity 0); `select`; a non-blocking `try_recv`;
a fallible `chan_send` that returns a Result instead of aborting on closed. Each is a later increment.

## 3. Prior-art survey (verified against primary sources; see channels doc §3)

| Language | close op | recv on closed | send on closed | double close |
|---|---|---|---|---|
| **Go** (spec, §Close/§Receive) | `close(ch)` | drains buffer, then zero value + `ok=false` | **panics** | **panics** |
| **Kotlin** (`SendChannel.close`) | `close()` | drains, then `ClosedReceiveChannelException` | throws `ClosedSendChannelException` | idempotent (returns false) |
| **Rust** (`mpsc`) | drop all senders | drains, then `Recv Err` | n/a (senders gone) | n/a |

**Consensus:** recv drains buffered values *before* reporting closed (all three); send-after-close is
an error (Go/Kotlin). **Sprout adaptation:** Sprout has no exceptions and no recovery, so the
"closed" signal must be a **value** on the recv path, and the error cases (send-on-closed,
double-close) become **loud-fail** (`sprout_fail`, program abort). This is *harsher* than Go's
recoverable panic — there is no recover — but it is the honest analog: both are unrecoverable program
bugs, and silent-drop would lose data invisibly (violates the fail-loudly doctrine). A fallible
`chan_send : ... -> Result` is deferred (§7) rather than shipped half-done.

## 4. Return shape — `Recv a = Got a | Closed` (RATIFIED)

The channels doc §9.3 deferred the return-shape choice to this increment. Options weighed:
`chan_recv -> Maybe a` (reuses prelude, but `Nothing` is ambiguous and cannot grow a third case);
`Recv a = Got a | Closed` (dedicated, self-documenting, extensible); a non-breaking `chan_recv_opt`
(two functions, makes the default `chan_recv` a footgun). **Kuba chose `Recv a`** — it is
inescapable (the consumer must match both cases), self-documenting, and grows cleanly to
`Got a | Closed | Empty` when a future non-blocking `try_recv`/`select` needs a third case, which
`Maybe` (only two cases) cannot. Cost: breaking the experimental `chan_recv -> a` (only in-repo
tests/fixtures use it; the compiler does not import `stdlib.chan`, so the bootstrap seed is
unaffected) and no `Maybe`-combinator reuse.

```
export type Recv a = Got a | Closed

export fn chan_recv(ch: Chan a) -> Recv a !{IO}   # was -> a
export fn chan_close(ch: Chan a) -> Unit !{IO}    # new
```

Consumer idiom (note: `Recv` is a user ADT, not `Maybe`/`Result`, so `do`-bind does **not** strip
it — it must be matched directly):

```
match chan_recv(ch) with
| Got v  -> process(v); loop()
| Closed -> done            # producer called chan_close
```

## 5. Semantics

| Situation | Behavior |
|---|---|
| `chan_recv`, buffer non-empty (open or closed) | returns `Got v`, drains FIFO — closing never discards buffered values |
| `chan_recv`, buffer empty, **open** | parks (unchanged) |
| `chan_recv`, buffer empty, **closed** | returns `Closed` immediately — never parks |
| a task recv-parked when `chan_close` runs | woken, returns `Closed` |
| `chan_send` on a closed channel | **loud-fail** ("send on closed channel") |
| a task send-parked when `chan_close` runs | woken, **loud-fails** on resume (send on closed) |
| `chan_close` on an already-closed channel | **loud-fail** ("channel already closed") |
| who may `chan_close` | any task holding the handle (Go semantics; no owner restriction) |

Invariant from cap ≥ 1 still holds: send-parked and recv-parked tasks never coexist on one channel,
so at close time at most one of the two wait-queues is non-empty.

## 6. Runtime (`runtime/sprout_scheduler.c`)

- `Chan` gains `int closed` (0 at `chan_new`).
- `Task` gains `int chan_closed_wake` (0 normally). A `chan_close` sets it to 1 on each task it wakes
  so the woken task, on resume, distinguishes a close-wake (→ `Closed` for a receiver; loud-fail for a
  sender) from a normal value delivery. Cleared back to 0 after it is read.
- **`__chan_recv` now returns a `Recv a`** built in the runtime by name, mirroring
  `sprout_make_proc_result` (`find_ctor_tag_by_name("stdlib.chan.Got")` / `"stdlib.chan.Closed"`,
  then `sprout_make1` / `sprout_make0`). Qualified names are collision-safe (a bare `Closed` could be
  shadowed by another module's ctor). **GC:** the payload `v`'s existing root (its buffer slot or the
  task's `chan_pending`) is kept live *until after* `sprout_make1` returns, so no manual temp-rooting
  is needed and `v` cannot be collected during the `Got` allocation.
- **`__chan_close`** (new builtin): loud-fail if already closed; set `closed = 1`; wake every
  recv-parked task (each returns `Closed`) and every send-parked task (each loud-fails on resume).
- **`__chan_send`** gains a closed check at entry and immediately after park-wake.
- **Force-drop / cancel / deadline:** unchanged. A closed channel's parked tasks are still `PARK_CHAN`;
  `force_drop_task`, `__scope_cancel`'s channel walk, and `__await_deadline`'s `PARK_CHAN` branch all
  already handle them. `chan_close` only ever *reduces* the set of parked tasks (it wakes them), so it
  cannot create a new stuck state; the §6.3 deadlock-panic reasoning of the channels doc is preserved
  (close is a new way *out* of a would-be deadlock, not into one).

**+1 builtin** (`__chan_close`), added to `runtime/APPROVED_BUILTINS`. `__chan_recv`'s signature
changes (return type) but it is not a new builtin.

## 7. Deferred (fast-follows, unchanged from channels doc §9)

Rendezvous (cap 0); `select`; non-blocking `try_recv` (the `Empty` third case `Recv` is shaped for);
fallible `chan_send -> Result` (abort-on-closed is the MVP).

## 8. Tests (TDD)

Red first, confirmed red behaviorally (not a type/import error): the type + wrappers + a no-op
`__chan_close` stub land first so the tests typecheck, then the behavior tests fail (recv-after-close
returns `Got`/hangs instead of `Closed`) until the runtime is implemented.

- **Drain-then-Closed** (`tests/stdlib/test_chan_close.spr`): buffer two values, close, recv → `Got`,
  `Got`, then `Closed`. Order-sensitive.
- **Close wakes a parked receiver**: fork a receiver that parks on an empty channel; owner closes;
  the receiver observes `Closed` and the scope joins (would hang without the wake).
- **Heap value + close under GC**: buffered heap strings survive a GC storm, drain as `Got`, then
  `Closed` — guards the `Got`-payload rooting across `make1`.
- **Send-on-closed loud-fails** (`tests/task_io_smoke/…`, `run_expect_fail`): asserts non-zero exit +
  the guard message (so a hang cannot masquerade as the abort).
- **Double-close loud-fails** (`run_expect_fail`).
- Existing `tests/stdlib/test_chan.spr` updated to the `Recv` return (match `Got`).

Both poller backends: channels touch no fd, and `close` adds no poller calls, so there is no new
backend-divergent code (per channels doc); the cancel/timeout fixtures continue to exercise epoll.
