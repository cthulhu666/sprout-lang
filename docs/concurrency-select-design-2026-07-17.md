# Channel `select` (L0.11) — design (2026-07-17)

**Status: IMPLEMENTED (2026-07-17).** Homogeneous, recv-only, list-based `select` in `stdlib.chan`.
The last deferred channel fast-follow (§9.4 of the L0.8 channels design) apart from multi-core.
Builds on the shipped L0.8 buffered + L0.9 close + L0.10 rendezvous channels and the L0.1–L0.7
cooperative scheduler. Shipped as designed below (bolt-on `select_waiters`; initial synchronous
scan; atomic-at-fire delivery; multi-channel force-drop unlink). All DoD gates green: `just test`
(incl. `test_chan_select.spr`), `test-stress` (14 files), `task-io-smoke` (incl. select cancel-drop
+ timeout-drop, plain + stress), ASan-clean multi-channel drop with a verified negative control
(single-channel unlink → deterministic heap-use-after-free in `__scope_cancel`). One Sprout builtin
`__chan_select`; internal C helpers `sprout_chan_make_selected` + `sprout_list_next`. The
`__chan_recv` non-parking prefix was refactored into the shared `chan_poll_take` (behavior-identical,
guarded by the channel stress tests). Seed unchanged (chan/task not bundled).

## 0. TL;DR

- `chan_select(chans: List (Chan a)) -> (Int, Recv a)` — wait on N channels of the **same**
  element type; return the list index of the channel that became ready first and its `Recv a`
  outcome (`Got v` / `Closed`). Covers the fan-in / first-of-N-workers pattern.
- The single most common *heterogeneous* select in Go — `select { case v := <-work; case <-timer }`
  — is already served by the shipped `with_timeout`. So homogeneous recv-only is the right-sized
  increment, not a compromise: what is genuinely missing is multiplexing over N same-typed sources.
- **Implemented native, not by racing forked recv tasks.** A fork-race violates exactly-once
  delivery (§3). The scheduler primitive registers interest on all channels **without consuming**,
  commits exactly one op on wake, and unregisters from the rest.
- **Bolt-on runtime:** the shipped `recv_waiters` (`Task*`-linked) stays byte-identical; a
  **separate `select_waiters`** list (of `SelectWaiter` nodes) is added and consulted *in addition*
  by `chan_send` / `chan_close` / `force_drop_task` / `scope_cancel`. All new complexity lands in
  additive select-only code; the proven single-recv path does not move.

## 1. Problem statement

Channels let one task stream to another, but a consumer can only block on **one** channel at a
time (`chan_recv`). The fan-in shape — one consumer draining N concurrent producers, taking
whichever produces first — cannot be written: recv on producer A blocks progress from B. `select`
is the multiplexer: wait on the first-ready of several channel operations.

## 2. Goals / non-goals

**Goals.** Block on N channels of one element type; return which fired and its outcome; a closed
channel in the set is always ready (returns `Closed`, never hangs); full integration with the
shipped cancellation/deadline machinery (a select-parked task on N channels is force-droppable);
GC-correct delivery under a concurrent collector.

**Non-goals (this increment).** Heterogeneous channels / mixed element types (`with_timeout`
already covers the dominant work-or-timeout case); **send**-cases in a select; a non-blocking
`default` clause; fairness / randomized choice among simultaneously-ready channels (v1 is
biased-to-lowest-index — see §4); a `select {}` *syntactic* construct; multi-core.

## 3. Prior-art survey (verified against primary sources)

The axes are: multiplex API shape, the tie-break when several ops are ready, and closed-channel
behavior.

| Language | Multiplex API | Tie-break (≥2 ready) | Recv on closed | Non-blocking |
|---|---|---|---|---|
| **Go** (spec, Select statements) | `select` statement | "a uniform pseudo-random choice is made" | "can always proceed immediately, yielding the zero value" (`ok=false`) | `default` clause |
| **Kotlin** (kotlinx.coroutines `select`) | `select {}` builder | **"biased to the first clause … the first one gets priority"** (`selectUnbiased` for random) | `onReceiveCatching` → closure as a normal result (vs `onReceive` throws) | `onTimeout` / trySelect |
| **Rust** (crossbeam `select!`) | `select!` macro / `Select` | "a random one among them is selected (unbiased)" | disconnected → recv op fires with an `Err`/closed result | `default` / `default(timeout)` |

Sources: Go spec §Select statements (go.dev/ref/spec); kotlinx.coroutines `select` API reference;
crossbeam-channel `select!` macro docs. (Rust `std::sync::mpsc` has **no** stable select;
crossbeam is the de-facto solution.)

**Consensus:** all three provide first-ready multiplexing and treat recv-on-closed as
always-ready yielding a closure signal (not a panic in the catching forms) — which maps exactly
onto Sprout's `Recv a = Got a | Closed` (L0.9). **Divergence on tie-break:** Go and Rust randomize
for fairness; **Kotlin biases to the first clause.** v1 follows **Kotlin** — lowest list index
wins — because it is deterministic (testable) and the simplest correct rule; randomized fairness
is a clean later addition (a `select_unbiased` sibling), exactly as Kotlin layers it.

**Why not fork-race (rejected).** The tempting reuse of shipped machinery is: fork one recv task
per channel into an internal rendezvous channel, first-to-arrive wins, cancel the rest. It is
**unsound** — it breaks exactly-once delivery. A losing fork that already completed its recv has
*consumed* a value (a buffered element, or a rendezvous hand-off from a sender who was told
"delivered"); force-dropping it mid-result-forward **loses that message**. Cooperative scheduling
does not save it: if two channels are ready, both forks run and both consume before either is
cancelled. This is exactly why select must be a native primitive that **registers without
consuming** and commits exactly one op.

## 4. Semantics (locked)

- `chan_select(chans)` returns `(i, outcome)` where `i` is the 0-based index into `chans` of the
  channel that fired and `outcome : Recv a` is `Got v` or `Closed`.
- **Initial synchronous scan (mandatory).** On entry, scan `chans[0..N)` in order; if any channel
  is *already* ready (`count > 0`, or a sender parked on a rendezvous channel, or `closed`), take
  from the **lowest-index** ready channel immediately and return — no registration, no park. This
  is where the lowest-index tie-break manifests (once parked, first-sender-wins). It is also
  required for correctness: a select over a lone closed or pre-buffered channel must return, not
  hang.
- **Park.** If none is ready, register a `SelectWaiter` on each channel and park. The first sender
  or closer to make one of them ready wakes the selector, delivering the value into the selector's
  `chan_pending` (or setting `Closed`) and recording the fired index — atomically, at fire time,
  before any other task can run (this closes the "value stolen between wake and re-check" race a
  return-index-then-re-recv design would have).
- **Closed channel** is always ready for recv → `Closed`. A select whose set contains a closed
  channel never parks (the scan returns it, lowest-index first).
- **Empty list** `chan_select([])` loud-fails — it can never proceed (a select with no cases is a
  guaranteed deadlock; Go's `select {}` blocks forever, which here is a program bug).
- **v1 is recv-only** and has **no `default`** (always blocks until one is ready).

## 5. API (`stdlib.chan`)

```
# Internal: the runtime returns which channel fired plus its Recv outcome as one value.
type Selected a =
  | Selected Int (Recv a)

extern fn __chan_select(chan_ids: List Int) -> Selected a !{IO}

# Wait on several same-typed channels; return the index of the one that became ready first and
# its Recv outcome. A closed channel is always ready (returns Closed). Lowest index wins when
# several are ready at entry. Empty list loud-fails.
export fn chan_select(chans: List (Chan a)) -> (Int, Recv a) !{IO} =
  match __chan_select(chan_handles(chans)) with
  | Selected i r -> (i, r)
```

`Selected a` is an internal 2-field ADT the runtime builds via a `sprout_chan_make_selected`
shim (bare tuples are heap blobs, awkward to construct in C; a 2-field ADT reuses the shipped
`sprout_make2` + the `Got/Closed` construction, mirroring the L0.9 `sprout_chan_make_got/closed`
shims). The public surface is the promised `(Int, Recv a)` tuple. `chan_handles` maps the
`List (Chan a)` to the `List Int` of raw handles the builtin walks.

## 6. Runtime — bolt-on `select_waiters`

**New node.** A select-parked task waits on N channels at once, so it cannot use the single
`Task`-linked wait-queue. Each registration is a `SelectWaiter`:

```c
typedef struct SelectWaiter {
  Task*  task;                       /* the parked selector */
  Chan*  chan;                       /* the channel this registration is on */
  long long sel_index;               /* index in the select list, returned on fire */
  struct SelectWaiter* q_prev, *q_next;  /* this channel's select-wait queue (FIFO) */
  struct SelectWaiter* sib_next;     /* the task's own list of its N registrations (unlink-all) */
} SelectWaiter;
```

`Chan` gains `SelectWaiter* select_head, *select_tail`. `Task` gains `SelectWaiter* sel_regs`
(head of its sibling list, NULL if not select-parked), `long long sel_fired_index`, and a new
`park_kind == PARK_SELECT`. The `sel_regs` array is one `malloc(N * sizeof(SelectWaiter))` per
select call, freed when the select returns or the task is force-dropped. It holds no Sprout heap
pointers (task/chan are runtime pointers, sel_index is a scalar), so it needs **no GC rooting** —
the delivered value rides `chan_pending`, which is already rooted at `task_create`.

**Shared ready-check.** Factor `chan_poll_take(Chan*, long long* out_v, int* out_closed) -> int`
capturing `chan_recv`'s non-parking prefix: `count > 0` → dequeue (+ refill a parked sender);
else a parked sender (rendezvous) → hand off; else `closed` → closed; else not ready. `chan_recv`
is refactored to call it (behavior-identical, guarded by the existing stress tests); the select
scan calls it per channel. Single source of truth for "is this channel ready and what does it
yield."

**`__chan_select`:**
1. Walk `chan_ids` (a `List Int`) into a C array of `Chan*` (via `sprout_list_next`, an
   `int`-returning helper in `sprout_runtime.c` where the `Nil`/`Cons` tags are visible — mirrors
   `string_concat_many`). Empty list → loud-fail.
2. Scan `0..N`; first channel for which `chan_poll_take` succeeds → `make_selected(i, Got v |
   Closed)`, return. (Lowest-index tie-break; exactly one op committed.)
3. None ready: `malloc` N `SelectWaiter`s, link each into its channel's `select` queue and the
   task's `sib` list, set `park_kind = PARK_SELECT`, `park_to_pump()`.
4. On wake: a sender/closer has set `chan_pending` + `sel_fired_index` and unlinked all our regs.
   `make_selected(sel_fired_index, chan_closed_wake ? Closed : Got chan_pending)`.

**`chan_send` (additive branch).** After the existing single `recv_waiters` hand-off, before
buffering: if `select_head` is non-empty, pop a `SelectWaiter sw`, deliver `value` into
`sw->task->chan_pending`, set `sw->task->sel_fired_index = sw->sel_index`,
`select_unlink_all(sw->task)` (remove its regs from every channel, free the array), wake the task.
Priority: a single `recv_waiters` parker is served before a `select_waiters` one (documented; Go
guarantees no cross-select fairness anyway). `chan_recv` is **unchanged** — a plain receiver never
consults `select_waiters`.

**`chan_close` (additive branch).** After waking `recv_waiters` and `send_waiters`, drain
`select_head`: for each waiter set `chan_closed_wake = 1`, `sel_fired_index`,
`select_unlink_all`, wake.

## 7. Force-drop / cancellation (critical)

`with_timeout` and `scope_cancel` are shipped; a select-parked task must be droppable or timing
out / cancelling a `chan_select` regresses to a loud-fail / deadlock.

- **`force_drop_task` gains a `PARK_SELECT` branch:** `select_unlink_all(t)` — unlink the task's
  `SelectWaiter`s from **all** their channels and free the array — then the usual roots-then-stack
  reclaim (unchanged). The load-bearing invariant: a task appears **at most once per channel**
  (the select set is distinct channels), and force-drop removes it from *all* channels at once, so
  a later cancel-walk of another channel never re-sees it → no double force-drop / double-free.
- **`scope_cancel`** additionally walks each channel's `select_head` (alongside the existing
  `send_head`/`recv_head` walks), force-dropping select-waiters whose *task's* scope is `s`. Same
  single-thread no-race reasoning as the shipped channel walk.
- **`__await_deadline`** classifies a `PARK_SELECT` child as droppable (new case beside
  `PARK_CHAN`).
- Select-parked tasks are **off `g_io_head`** (no poller registration), like channel-parked tasks
  — preserving the deadlock-panic's correctness (a lone select-parked task nobody will send to is
  a genuine deadlock).

## 8. New builtins

`extern fn __chan_select(chan_ids: List Int) -> Selected a !{IO}` — one scheduler primitive
(parks/registers on N channels; impossible in Sprout), same justification class as the shipped
`__chan_*`/`__scope_*`/`__task_*`. Net Sprout-builtin churn **+1**. Internal C helpers (not
Sprout-callable): `sprout_chan_make_selected` (value-returning constructor shim → listed in
`APPROVED_BUILTINS` with the `make_got/make_closed` group) and `sprout_list_next`
(`int`-returning, exempt from the `^long long` builtin check).

## 9. Tests (TDD, RED first)

- **Fan-in first-ready** — N producers into N channels, one consumer loops `chan_select`,
  receives all N values; sum/order asserts.
- **Lowest-index tie-break** — pre-ready ≥2 channels *before* the select runs (buffer two, or park
  two senders), assert the lowest index is returned. (Must pre-ready; racing live senders proves
  nothing — §4.)
- **Closed channel is always ready** — a closed channel in the set returns `(i, Closed)` without
  parking, even alongside an empty open channel.
- **Heap-value select under GC stress** — the delivered value is a heap String, a churn loop
  forces a GC while the selector is parked; value survives (`chan_pending` rooting).
- **`scope_cancel` drops a select-parked task on ≥2 channels** (task_io_smoke) — multi-channel
  unlink; reaching "done" proves it; a churn GC in the cancel→join window + ASan gates the
  freed-stack path. **Negative control:** unlinking only the "current" channel double-frees
  (deterministic ASan double-free) — this is the #1 correctness hazard.
- **`with_timeout` over a `chan_select` body** — returns `Expired`, child force-dropped.

## 10. Deferred (after this)

Send-cases in select; non-blocking `default`; randomized fairness (`select_unbiased`); a
syntactic `select {}`; heterogeneous/typed-clause select; multi-core.
