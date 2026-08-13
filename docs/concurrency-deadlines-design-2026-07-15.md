# Deadlines & timeouts — design proposal (2026-07-15)

**Status: IMPLEMENTED (2026-07-16, branch `concurrency-l07-deadlines`).** `with_timeout` +
`TimeoutResult` + `task_status` landed; `task_cancelled` reimplemented in Sprout; runtime
`__await_deadline`/`__task_stop_reason` + `force_drop_task` refactor. Implementing this
surfaced and fixed a latent **bundler** bug — a generic type's constructors were never exported
cross-module (`has_ctor_export` didn't skip type params before `(..)`); `TimeoutResult a` was the
first generic user-facing ADT to hit it (§14). All DoD gates green (full `just test`,
task-io-smoke incl. new fd-drop + nested-loudfail fixtures plain+stress, ASan-clean force-drop,
compile-examples, smoke-shapes, bundle-smoke, canary, seed fixed-point). §13 decisions locked
with Kuba (2026-07-15). Builds on L0.5 (cooperative
cancellation: `scope_cancel` + `task_cancelled` + I/O-drop via `g_io_head`/`sprout_poll_remove`)
and L0.6 (`task_sleep`: one-shot poller timers via `sprout_poll_add_timer` + `PARK_TIMER`
force-drop). Prior-art rows (§3) are verified against primary sources. This is the increment the
cancellation doc (§10, §11) explicitly deferred `task_status` and the second stop-reason
(`TimedOut`) to.

**Decisions locked (§13 resolved with Kuba):**
- Value-based outcome, **not** an exception (Sprout has none): `with_timeout` returns
  `TimeoutResult a = Completed a | Expired` — Go's `ctx.Err()`/Trio `move_on_after`
  family, not Kotlin/Trio-`fail_after`'s throw. **[§13.2 locked: dedicated type.]**
- **The timed body runs as a child task**, not on the caller's own stack — so the body's
  *own* blocking I/O falls within the deadline's reach (§5.1 is the crux; the alternative
  cannot cancel a synchronous call on the owner's own stack without unwinding).
- `ms <= 0` → **immediate `Expired`, no timer, but still force-drop the already-forked child**
  (§5.2/§13.1 locked).
- Deadline delivery reuses the L0.5 cooperative flag + L0.6 timer, but **not** for free: a
  fired timer wakes the **owner**, and the owner — now running, after the poll batch is fully
  drained, so the L0.5 force-drop safety invariant holds — runs the drop itself (§5.2). Making
  that race safe needs two new `Task` fields (`deadline_child`, `on_io_list`) and a
  **guarded** force-drop (drop the child only if it is still parked); the pump and trampoline
  both gain a deadline-aware branch (§5.3). This is more than "one `if`" — the earlier draft's
  claim to that was wrong (a same-batch fd-ready-at-deadline double-enqueues the owner).
- Cooperative delivery: **accept + document** the busy-but-yielding-body limitation; the
  pump-loop deadline heap is deferred (§5.4/§13.4 locked).
- Second stop-reason: `scope.cancelled` (bool) becomes `scope.reason` (0/1/2); new
  `task_status() -> TaskStatus = Running | Cancelled | TimedOut` (Go's `ctx.Err()`).
  **`task_cancelled` is reimplemented in Sprout as `__task_stop_reason() != 0`** — the
  builtin is *removed* (§13.3 locked), gated on verifying the compiler bundle/seed never
  reference it (§10).
- **Two new builtins** (`__await_deadline`, `__task_stop_reason`), **one removed**
  (`task_cancelled`) — scheduler primitives (context-switching), same justification class as
  the existing `__scope_*`/`__task_*`. **Approved up front** per AGENTS.md "Builtin vs Stdlib"
  and Collaboration Rule 6.

---

## 0. TL;DR

- L0.6 shipped `task_sleep(ms)` — a task parks on a timer and *resumes*. It is a sleep, not
  a deadline: firing wakes the sleeper, it does not cancel anything.
- This increment turns that same timer into a **cancellation source**: `with_timeout(ms,
  body)` runs `body` and, if it has not finished within `ms`, cancels it and returns
  `Expired` instead of its result.
- **The hard constraint is the same one L0.5 hit: Sprout has no exceptions.** So the outcome
  is a *value* (`Completed a | Expired`), delivery is *cooperative* (the body stops at a
  yield/await/park or a `task_status` checkpoint), and I/O-parked body tasks are
  *scheduler-dropped* exactly as `scope_cancel` already drops them.
- **The one genuinely new idea** is that a timeout must be able to cancel the timed work,
  and you cannot cancel a synchronous call running on your own stack (no unwinding). So the
  timed body is **forked as a child task**; the deadline cancels *that task* (whose own I/O
  is now force-droppable), and the caller waits on "body done OR deadline fired."
- The building blocks are reuse — the timer is L0.6's, the cooperative flag and reclaim are
  L0.5's — but the timer-vs-completion race is real work: the pump and trampoline each grow a
  deadline-aware branch and the force-drop becomes state-guarded (§5.3). The pump *never*
  force-drops (that would race stale same-batch tokens, breaking L0.5's invariant); the owner
  does, in its own context, after the batch drains.

---

## 1. Problem statement

Real programs need bounded waits. "Fetch this URL, but give up after 500 ms." "Run this
query, cancel the scope if it overruns its deadline." Today Sprout can `task_sleep` but has
no way to say *"stop this work when a deadline passes."* A user can hand-roll it — fork the
work, fork a sleeper, race them, `scope_cancel` on whichever the sleeper wins — but that is
several lines of subtle scheduler-aware code for a one-line intent, and it burns an extra
green task purely as a watchdog.

The cancellation doc (§10) already committed to filling this gap: it kept `task_cancelled`
*binary* for the L0.5 MVP precisely because there was exactly one stop-reason, and named
"the `task_sleep`/deadlines increment" as where the second reason (`TimedOut`) and a richer
`task_status()` would land. L0.6 shipped the timer half; this increment ships the
cancellation-source half and the richer status.

## 2. Goals and non-goals

**Goals**
- `with_timeout(ms, body)` — run `body`, cancel it and report `Expired` if it overruns `ms`.
- A second stop-reason: distinguish "cancelled" from "timed out" via `task_status()`.
- Reuse the L0.5 cooperative-flag + force-drop machinery and the L0.6 timer verbatim; add
  the minimum new runtime surface.
- Correct interaction with the timer-vs-completion race (the body finishing at the same
  moment the deadline fires must not double-enqueue or leak — §5.3).

**Non-goals (this increment)**
- **A non-forking fast path.** `with_timeout` forks the body as a task (1 MiB green stack)
  even for trivial work. A future optimization could special-case a body that only parks on
  its own I/O, but it is out of scope here (§8).
- **Absolute deadlines / deadline propagation across nested scopes.** Go threads a deadline
  through the context tree so an inner op inherits the outer deadline. Sprout's cancellation
  is *local* (cancellation doc §10.1); a nested `with_timeout` gets its own independent
  deadline. Cross-scope deadline inheritance is deferred, consistent with L0.5.
- **Auto fail-fast / task-group policy.** Still the deferred `_try`/task-group layer
  (cancellation doc §11). `with_timeout` is a primitive, not that layer.
- **Interrupting CPU-bound bodies.** Cooperative, like every system in §3 — a body that
  never reaches a yield/park/`task_status` checkpoint cannot be timed out (§5.4).
- **Timing out structured concurrent work (direct-I/O-only MVP; decided with Kuba).** A body
  that parks *directly* on I/O / `task_sleep` is timed out cleanly. A body that wraps
  structured concurrency — forks workers into a *nested* scope and blocks in its join — cannot
  be force-dropped without the deferred tree-cancel cascade (§10.2 of the cancellation doc), so
  it **loud-fails** if the deadline fires while it is join-blocked (§5.2, §5.5). Timing out a
  concurrent *group* is a later increment (the cascade), not this one.

## 3. Prior-art survey

How comparable structured/cooperative systems expose a timeout. Every row verified against
the language's own reference (URLs in the commit log of this doc).

| System | Timeout API | Outcome on expiry | Cooperative? | Distinguishes timeout from cancel? |
|---|---|---|---|---|
| **Go** `context` | `WithTimeout(parent, d) → (ctx, cancel)` | **value**: `ctx.Err() == DeadlineExceeded` (vs `Canceled`) | **Yes** — goroutine must `select` on `<-ctx.Done()` | **Yes** — `ctx.Err()`: `DeadlineExceeded` vs `Canceled` |
| **Trio** (Python) | `move_on_after(s)` / `fail_after(s)` cancel scope | `move_on_after`: **flag** `cancel_scope.cancelled_caught`; `fail_after`: **raises** `TooSlowError` | **Yes** — cancellation delivered at `await` checkpoints | Via the scope object / exception type |
| **Kotlin** coroutines | `withTimeout(t){}` / `withTimeoutOrNull(t){}` | `withTimeout`: **throws** `TimeoutCancellationException`; `OrNull`: **returns null** | **Yes** — docs: "only cancels the running block, it's up to the block to notice, by suspending or checking `isActive`" | By exception type / null |

**Reading for Sprout.** The split is on *how the deadline surfaces*: a **throw**
(Kotlin `withTimeout`, Trio `fail_after`) or a **value** (Go `ctx.Err()`, Kotlin
`withTimeoutOrNull`, Trio `move_on_after`'s `cancelled_caught`). Sprout has no exceptions, so
only the value family is available — and it is a *good* fit: `with_timeout` returns
`Completed a | Expired`, the direct analogue of `withTimeoutOrNull`'s value-or-null and Go's
`ctx.Err()` distinguishing `DeadlineExceeded`. All three confirm **cooperative** delivery —
the ground for §5.4's caveat.

## 4. Syntax and API

Two new public types and one new function in `stdlib.task`, plus one new query:

```sprout
# The outcome of a timed operation. `Completed a` carries the body's result;
# `Expired` means the deadline fired first and the body's tasks were cancelled.
export type TimeoutResult a =
  | Completed a
  | Expired

# Why the current task's scope is stopping (Go's ctx.Err(): nil / Canceled / DeadlineExceeded).
export type TaskStatus =
  | Running
  | Cancelled
  | TimedOut

# Run `body` as a child task; if it does not finish within ~`ms` milliseconds, cancel it and
# return `Expired`. Otherwise return `Completed(result)`. Timing is best-effort (≥ ms), like
# task_sleep. Delivery is cooperative: a body blocked on I/O is dropped by the scheduler; a
# CPU-bound body must reach a yield/await/task_status checkpoint to observe the deadline (§5.4).
export fn with_timeout(ms: Int, body: Unit -> a !{IO}) -> TimeoutResult a !{IO}

# The current task's stop-reason. `Running` normally; `Cancelled` after scope_cancel;
# `TimedOut` after a with_timeout deadline fired on this task's scope. task_cancelled() stays
# as the binary "should I stop?" check (true for BOTH Cancelled and TimedOut).
export fn task_status() -> TaskStatus !{IO}
```

**MVP observability caveat (found during implementation).** `TimedOut` is *set* on the scope but
is **not observable by the timed body** in this increment: `with_timeout` force-drops the body
*during* its park, before it re-checks status, and the with_timeout scope holds exactly one task
(the body gets no scope handle, so it cannot spawn a sibling that could read the reason). So
`task_status()` observably returns only `Running | Cancelled` today; `TimedOut` is forward-compat
scaffolding (it becomes observable once the tree-cancel cascade lets a *group* under a deadline
cooperatively stop). Only the observable states are tested (`test_task_status.spr`).

Usage — the motivating shape:

```sprout
match with_timeout(500, fn -> slow_fetch(url)) with
| Completed(body) -> use(body)
| Expired         -> log("fetch timed out")
```

Cooperative CPU-bound body distinguishing *why* it stopped:

```sprout
fn crunch() -> Result Unit Int !{IO} =
  loop_until(fn ->
    match task_status() with
    | TimedOut  -> Err(-1)      # deadline: bail with a partial marker
    | Cancelled -> Err(0)       # explicit cancel: different handling
    | Running   -> step())
```

**Note on the earlier `with_timeout(scope, ms, body)` sketch.** The option preview during
scoping showed `with_timeout` taking an *existing* scope. §5.1 shows why that shape is
unsound: a deadline cannot cancel work running synchronously on the owner's own stack, so the
timed body must be a *child task* and `with_timeout` must own the wait. A deadline attached to
a pre-existing shared scope (`scope_set_deadline`) is a coherent but separate, more advanced
primitive — deferred.

## 5. Semantics

### 5.1 Why the body must be a child task (the crux)

A timeout must be able to *stop* the timed work. Sprout stops a task in exactly two ways
(L0.5): it sets a cooperative flag the task checks, or — for a task suspended in the poller —
the scheduler force-drops it (deregister the fd/timer, free its roots+stack). **Neither can
act on a call running synchronously on the caller's own stack.** If `with_timeout(500, fn ->
tcp_read(conn))` ran `tcp_read` inline on the owner's stack, `tcp_read`'s `EAGAIN` would park
*the owner*, and to "time out" we would have to force-drop the owner — freeing the very stack
we must return onto. Impossible without unwinding, which Sprout does not have.

Therefore the body is **forked as a child task** (`task_fork`). The child's own blocking I/O
now parks the *child*, which the scheduler can force-drop. The owner does not run the body; it
waits for "child done OR deadline," and on deadline it drops the child (the owner is running —
it is safe to drop *another* task's stack). This is the same reason Kotlin's `withTimeout`
block is a coroutine and Swift's timeout idiom races a child `Task.sleep`: the cancellable
unit must be a task, not a stack frame.

### 5.2 Deadline delivery: fork the body, park the owner on (done ∨ timer)

`with_timeout` desugars to (Sprout, over one new builtin):

```sprout
export fn with_timeout(ms: Int, body: Unit -> a !{IO}) -> TimeoutResult a !{IO} =
  with_scope(fn s ->
    match s with
    | Scope sid ->
      let handle = task_fork(s, body)          # body runs as a child (§5.1)
      match handle with
      | Task tid ->
        if __await_deadline(sid, tid, ms) then  # 1 = child finished; 0 = deadline dropped it
          Completed(task_await(handle))          # child is done → immediate, raw result
        else
          Expired)
```

`__await_deadline(scope_id, task_id, ms)` (new C builtin) arms the deadline and parks the
owner so it can be woken by **either** the child completing **or** the timer firing:

1. Arm a one-shot timer for `ms`, token = the owner task (L0.6 `sprout_poll_add_timer`); set
   `owner.park_kind = PARK_TIMER`, `owner.deadline_child = child`, `io_list_push(owner)`
   (which now also sets `owner.on_io_list = 1`).
2. Register the owner as the child's awaiter (`child.awaiter = owner`), then `park_to_pump`.
3. On resume (see §5.3 for who woke us and why it is unambiguous), classify the child into
   exactly one of **three** states — only one is cleanly droppable:
   - **`child.done` → completed.** Return 1; `with_timeout` then `task_await`s the (already
     finished) child for its raw result.
   - **not done, `child.on_io_list` (parked directly on I/O / a `task_sleep` timer) → timed
     out.** Set `scope.reason = TimedOut`, `force_drop_task(child)` (§6.3), return 0 →
     `Expired`. The child never completes, so `task_await` is never called on it (the `if`
     guarantees it), avoiding the L0.5 `roots == NULL` loud-fail (cancellation doc §4.2).
     **This is the supported MVP case** — a body that parks directly on I/O (the §5.1 headline
     `with_timeout(500, \_ -> tcp_read(conn))`).
   - **not done, `child.park_kind` is `PARK_CHAN` / `PARK_SELECT` (parked in `chan_send` /
     `chan_recv` / `chan_select`) → timed out.** Added with L0.8/L0.11, after this section was
     first written. Same treatment as the `on_io_list` case, but the child is on a channel wait
     queue rather than `g_io_head`, so `force_drop_task` tears down the channel/select
     registrations instead of a poller one.
   - **not done, `child.in_rq` (runnable — its fd went ready right at the boundary) → YIELD and
     RE-CLASSIFY.** Do **not** drop it (it is linked in the ready queue; dropping a queued task
     is a use-after-free). Clear `child->awaiter`, `rq_push` the owner, `park_to_pump`, then run
     this whole classification again. Appending the owner behind the child makes the pump run the
     child first, so on the next visit it is `done` (→ 1, `Completed`, the best-effort `≥ ms`
     edge) or parked (→ one of the droppable branches above, → `Expired`).

     **Corrected 2026-08-13 (green-threads review, finding 1).** The original rule here was
     "re-register as its awaiter and `park_to_pump` again; the child completes → return 1",
     justified by reading `in_rq` as "one scheduler tick from done". That reading is wrong:
     `in_rq` means RUNNABLE, and a runnable child may run and then park on something else
     entirely. The owner's deadline timer is already spent at this point, so that second await
     had **no deadline at all** — a body shaped `read A; read B` blocked forever whenever A's
     readiness landed in the deadline's own poll batch, with `with_timeout`'s only guarantee
     silently void. Yielding instead of committing bounds the overshoot at one scheduling step
     of the body. Re-looping terminates on the child's own progress; a body that never yields at
     all is untimeoutable under a cooperative scheduler regardless, which is a property of the
     model rather than of this branch.

     Not reachable from a test: it needs the deadline timer and the child's fd readiness in the
     *same* `poll_wait` batch with the timer listed first, a sub-microsecond window nothing can
     schedule. The invariant is pinned by source assertion in `tests/c_runtime/run.sh`, and the
     reachable neighbours (a body that wakes then re-parks; a channel-parked body) are covered in
     `tests/stdlib/test_task_timeout.spr`.
   - **not done, neither `on_io_list` nor `in_rq` (blocked in a *nested* `with_scope` join or a
     `task_await`) → loud-fail.** A join/await-waiter lives in `s->waiter` / `child->awaiter`,
     *not* on `g_io_head` and *not* in the ready queue (verified: `io_list_push` is called only
     from `scheduler_park_on_fd`/`scheduler_park_on_timer`). It cannot be force-dropped without
     orphaning its inner scope (the tree-cancel cascade the cancellation doc §10.2 deferred,
     and §5.5 here). MVP loud-fails with "with_timeout: cannot time out a body blocked in a
     nested scope/await — deadline cascade deferred; time out a body that parks directly on
     I/O." **This is the direct-I/O-only MVP boundary (decided with Kuba): `with_timeout` times
     out a body that parks directly on I/O, not one wrapping structured concurrent work.**

**Why the force-drop runs in the owner, never the pump.** L0.5's `scope_cancel` is safe only
because "the pump drains every `poll_wait` token before it can resume the owner, so any task
still on `g_io_head` has *not* fired." Dropping from the pump mid-batch would violate that: a
stale token for the just-dropped child could still be sitting in the same `toks[]` batch, and
processing it is a UAF. So `__await_deadline` does the drop after control returns to the owner
(the batch is fully drained), preserving the invariant verbatim.

### 5.3 The timer-vs-completion race (why `park_kind` is not enough)

The child can finish at the very moment its deadline fires, and — critically — the child's own
fd can go ready *in the same `poll_wait` batch* as the timer. The owner has two potential
wake-sources: the pump harvesting its timer token, and the child's `task_trampoline`
awaiter-wake. They must enqueue the owner **exactly once**; a double `rq_push` turns the
singly-linked ready queue into a self-cycle (`tail->next = owner; owner->next = owner`).

**`park_kind` cannot be the dedup signal.** `pump_loop` sets `w->park_kind = PARK_NONE` on
*every* harvested token (line 176) before it could ever reach the trampoline. Concrete failing
trace with the naïve "trampoline tears down a `PARK_TIMER` awaiter" rule: `poll_wait` returns
`[child_fd, owner_timer]` in one batch → pump pushes `child`, then harvests `owner_timer`
(clearing its `park_kind` and pushing it) → pump pops `child` → child completes → trampoline
sees `owner.park_kind == PARK_NONE`, skips teardown, but still runs its unconditional
`rq_push(owner)` → **owner enqueued twice → self-cycle.**

**The fix: two explicit fields + an idempotent, membership-based dedup.**
- `Task.on_io_list` (bool, maintained by `io_list_push`/`io_list_remove`) — the robust "is
  this task still poller-parked?" signal, immune to `park_kind` being reset.
- `Task.deadline_child` (Task*, non-NULL only while a task is a deadline-owner in
  `__await_deadline`) — lets the trampoline recognise a deadline-owner awaiter.

`task_trampoline`, waking `aw = t->awaiter`:
```
if      (aw->on_io_list)          { poll_remove_timer(aw->park_timer_id); io_list_remove(aw); rq_push(aw); }
else if (aw->deadline_child == t) { /* timer already harvested → aw already in rq → DO NOT push */ }
else                              { rq_push(aw); }                     /* ordinary task_await awaiter */
```
- **Child finishes, timer still live** (`aw->on_io_list` true): tear the timer down
  (`poll_remove_timer` discards an already-fired-but-undrained event, task_sleep §5.1), unlink,
  push once. Owner resumes → `child.done` → Completed.
- **Timer already harvested, then child finishes** (`aw->on_io_list` false,
  `aw->deadline_child == t`): the harvest already pushed the owner; the trampoline must **not**
  push again. Owner resumes → `child.done` → Completed.
- **Ordinary `task_await`**: unchanged (`aw->deadline_child` is NULL, `aw->on_io_list` false).

A normal `task_await` never touches `g_io_head`, so `on_io_list` is a clean discriminator; a
task parks in exactly one place, so `deadline_child != NULL` unambiguously marks a live
deadline-owner. Both invariants are documented in-file at the trampoline and `__await_deadline`.

### 5.4 Cooperative delivery — "park or finish," not "yield"

Like Go, Kotlin, and Trio (§3), delivery is cooperative, and the *precise* requirement is
sharper than "the body must yield": **the deadline is delivered when the pump reaches
`poll_wait`, which happens only when the ready queue momentarily empties** (`rq_pop` returns
NULL, line 168-172). So:

- **A body that parks (blocks on I/O, `task_sleep`) is timed out promptly.** The child parks
  on `g_io_head`, the owner parks on the timer, the ready queue empties → the pump blocks in
  `poll_wait` and returns the moment the timer fires. This is the target case (bound a
  blocking wait) and it works well.
- **A body that spins *and yields* but never parks is *never* timed out.** Each `task_yield`
  re-enqueues the child, so the ready queue never empties, so the pump never reaches
  `poll_wait`, so the timer is never harvested and `reason` is never set — even a body polling
  `task_status()` in its loop sees `Running` forever. This is a real limitation, worse than
  Kotlin's (whose event loop checks its timer heap every iteration).
- **A body that neither yields nor parks** (pure CPU) blocks the whole cooperative scheduler,
  the pre-existing L0.5 caveat.

**MVP stance:** accept this and document it loudly on `with_timeout`. The timeout is prompt
and correct for *parked* work — which is what timeouts are for. A pump-loop deadline heap
(check deadlines every iteration, set `reason` independent of `poll_wait`) would make
`task_status`-cooperative timeout prompt for busy bodies too, but it adds hot-path cost to the
pump; deferred unless wanted (§13.4). `task_status()` remains useful for a body that *does*
park between CPU bursts (it observes `TimedOut` after the next park cycle).

### 5.5 Nesting, `task_status`, and the outer-cancel hazard

`task_status()` reports the *current task's own scope*'s reason. A `with_timeout` inside an
outer scope gets a fresh inner scope; the outer scope's cancellation and the inner deadline
are independent (local propagation, §2 non-goal, consistent with L0.5). A body task inside the
inner scope sees `TimedOut` when the inner deadline fires; a task in the outer scope sees
`Cancelled`/`Running` per the outer scope, untouched by the inner deadline.

**New hazard this increment introduces — flagged, not hand-waved.** A plain nested
`with_scope` is safe from outer `scope_cancel` because a join-waiter parks in `s->waiter`, *not*
on `g_io_head`, so `scope_cancel`'s `g_io_head` walk never sees it. But `__await_deadline`
parks its owner *on* `g_io_head` (for the timer). So if task `T` (spawned into a cancellable
outer scope) is sitting in `with_timeout`, and the outer owner calls `scope_cancel(outer)`, the
walk **matches `T`** (`T->scope == outer`) and would `force_drop` it — freeing `T`'s stack while
`T`'s inner scope + child are still live, orphaning them (the inner child later wakes a freed
awaiter → UAF; the inner scope leaks).

**MVP resolution: loud-fail, consistent with L0.5's deferred cascade.** `force_drop_task` gains
a guard: dropping a task with `deadline_child != NULL` (a deadline-owner) is refused with a
loud fail — "cannot cancel a scope whose task is inside `with_timeout`; deadline/cancel nesting
cascade is deferred." This mirrors L0.5's existing loud-fail on dropping a sibling-awaited task
(§9): owner-only cancel makes this rare, and a full tree-cancel cascade is the same deferred
work the cancellation doc §10.2 already parks. A regression test pins the loud fail (§11.8).

## 6. Implementation overview (for approval before editing)

Runtime (`runtime/sprout_scheduler.c`):

1. **Three new `Task` fields.** `int on_io_list` (maintained by `io_list_push`/`io_list_remove`
   — the `g_io_head` membership signal §5.3 relies on, immune to `park_kind` being reset);
   `int in_rq` (maintained by `rq_push`/`rq_pop` — "is this task runnable in the ready queue,"
   the §5.2 signal that distinguishes a raced-to-ready child from one blocked in a nested
   join/await); and `Task* deadline_child` (non-NULL only while a task is a deadline-owner in
   `__await_deadline`). All initialised in `task_create`/`sprout_scheduler_init`.
2. **`Scope.cancelled` (bool) → `Scope.reason` (int: 0 none / 1 cancelled / 2 timed-out).**
   `__scope_cancel` sets `reason = 1`. New builtin `__task_stop_reason()` returns `reason`
   (0 for task-0 / no scope). **The `task_cancelled` builtin is removed** and reimplemented in
   `stdlib/task.sprout` as `task_cancelled() = __task_stop_reason() != 0` — behaviour
   unchanged (still true after cancel, now *also* true after timeout, so existing L0.5
   cooperative-stop bodies Just Work). *Removal gate (§10):* before deleting the C symbol,
   confirm neither the compiler bundle nor the committed seed emits a call to `task_cancelled`
   (grep `stdlib/compiler/**` and `bootstrap/compile_driver.ll`); it is a concurrency
   primitive the compiler does not import, so removal is expected to be seed-safe — but this
   is verified, not assumed. If the seed *does* reference it, use the bridge-runtime protocol
   (re-add old builtin → emit new seed → remove).
3. **Factor the L0.5 force-drop reclaim** (the body of `scope_cancel`'s drop loop: deregister
   poller/timer, `io_list_remove`, free roots+stack, decrement `live`, null-or-free the
   record) into a `static void force_drop_task(Task* t)` helper, so `scope_cancel` and
   `__await_deadline` share one audited reclaim path. Two guard changes in it:
   - **Relax the awaiter guard** from `t->awaiter != NULL` to `t->awaiter != NULL &&
     t->awaiter != g_current_task` — dropping a task you *yourself* await while *running* is
     safe (you will not strand yourself; you proceed to return `Expired`); a *sibling* awaiter
     is still a loud fail.
   - **Refuse to drop a deadline-owner** (`t->deadline_child != NULL`): loud-fail with the
     nested-cancel message (§5.5). Guards the outer-cancel-orphans-inner-scope UAF.
4. **`__await_deadline(scope_id, task_id, ms) -> long long`** — new builtin (ms > 0 only; the
   wrapper handles ms ≤ 0) implementing §5.2: arm timer (token = owner) + set `deadline_child`
   + `io_list_push`; register owner as the child's awaiter; `park_to_pump`; on resume,
   three-way classify: `done` → 1; `on_io_list` → drop + reason=TimedOut → 0; `in_rq` →
   re-await → 1; else (nested join/await) → loud-fail (cascade deferred). Reuses
   `sprout_poll_add_timer`/`sprout_poll_remove_timer`, `io_list_*`, `park_to_pump`,
   `force_drop_task`. Clears `deadline_child` before returning.
5. **`task_trampoline`**: replace the unconditional `rq_push(t->awaiter)` with the
   three-way dedup of §5.3 (`aw->on_io_list` → teardown+push; `aw->deadline_child == t` →
   no-op; else → push). This is the one behavioural change to an existing hot path — documented
   in-file with the failing-trace rationale.
6. **`__task_stop_reason(void) -> long long`** — new builtin: `reason` of the current task's
   scope (0 if none).

Stdlib (`stdlib/task.sprout`): add `TimeoutResult a`, `TaskStatus`, `with_timeout`,
`task_status`, the two `extern fn __await_deadline` / `__task_stop_reason` decls, and
**re-declare `task_cancelled` as a Sprout fn** (`__task_stop_reason() != 0`), removing its
`extern` declaration.

`runtime/APPROVED_BUILTINS`: add `__await_deadline` and `__task_stop_reason` with
justifications (context-switching scheduler primitives; cannot be expressed in Sprout, same
class as `__scope_open`/`__task_fork`).

## 7. Type-system impact

None beyond two new nullary-and-unary ADTs. `TimeoutResult a` is an ordinary parametric sum
(like `Maybe a`); `TaskStatus` is a plain enum. `with_timeout` is fully parametric in the
body's result type. No new inference, kinding, or dispatch machinery. Constructor names
(`Completed`/`Expired`, `Running`/`Cancelled`/`TimedOut`) do not collide.

## 8. Performance notes

`with_timeout` forks one green task (1 MiB stack) per call — the §5.1 requirement, not
incidental. For the target use (a timed network fetch), this is dominated by the I/O itself.
A non-forking fast path (§2 non-goal) is possible later but unmeasured and out of scope; per
AGENTS.md, performance alone does not justify added surface without a measured bottleneck.

## 9. Error-message impact

- `__await_deadline` on a null scope/task → loud fail, matching the existing `__scope_*`
  null guards.
- The `Expired` path provably never calls `task_await` on the dropped child, so the L0.5
  "awaiting a task dropped by scope_cancel" loud-fail is not reachable through `with_timeout`
  — but it remains the backstop if a future caller misuses the handle.
- The sibling-awaiter loud-fail in the shared `force_drop_task` (§6.3) is unchanged for
  `scope_cancel`; `__await_deadline` is exempt only for the owner-as-awaiter case.
- New loud-fail: `scope_cancel` reaching a deadline-owner task (nested `with_timeout` inside a
  cancelled outer scope, §5.5) — "cannot cancel a scope whose task is inside `with_timeout`;
  deadline/cancel nesting cascade is deferred." Refuses rather than orphan the inner scope.

## 10. Compatibility / migration

Purely additive at the *language* surface. `task_cancelled()`'s contract and signature are
unchanged (it was "true if the scope is stopping"; a timeout *is* a stop, so timed-out bodies
that already check `task_cancelled` cooperatively stop with no code change) — only its
implementation moves from a C builtin to a one-line Sprout fn. The `Scope.cancelled → reason`
rename is internal to the runtime. No existing example, test, or stdlib caller changes meaning.

**Builtin churn: +2 (`__await_deadline`, `__task_stop_reason`), −1 (`task_cancelled`).** The
removal is the only bootstrap-sensitive step (§13.3, §6.2). The catch-22 bites only if the
committed seed emits a call to the removed symbol; `task_cancelled` is a concurrency primitive
the compiler bundle does not import, so the seed is expected not to reference it — **verified
at implementation** (grep `stdlib/compiler/**` + `bootstrap/compile_driver.ll`) before deleting
the C function. If it *does* appear, the bridge protocol (keep the builtin one seed-cycle
longer, refresh the seed, then remove) applies. Committing `task.sprout` also trips the seed
gate hook (any staged `stdlib/*.sprout` needs a refreshed `bootstrap/compile_driver.ll` or a
`just seed-fp-ack` when the IR is genuinely unchanged) — a commit-time step, not a design risk.

## 11. Tests added/updated

TDD order (failing first, per AGENTS.md Definition of Ready):
1. **`Expired` on I/O overrun** — `with_timeout(50, fn -> <park on a socket that never
   becomes ready>)` returns `Expired`; the child is force-dropped (verify under ASan for the
   freed-stack UAF, per L0.5's lesson that GC_STRESS alone is insufficient).
2. **`Completed` on fast body** — `with_timeout(10_000, fn -> quick())` returns
   `Completed(quick_result)`; the timer is torn down (no leak; verify no stale-timer wake).
3. **Race: body finishes ~at the deadline** — a body that finishes right around `ms`; assert
   no double-enqueue / crash under repetition (both kqueue and epoll). This is the §5.3 case;
   the self-cycle it guards against is a hang/UAF, so run it under ASan and with a bounded
   watchdog. Include the specific "fd-ready-in-the-same-batch-as-the-timer" sub-case (a socket
   that becomes readable at ~`ms`) — the exact trace that broke the naïve `park_kind` dedup.
4. **Runnable-child edge** — a child whose I/O goes ready right at the boundary must not be
   force-dropped mid-flight: it runs on, and the OUTCOME follows what it does next — `Completed`
   if it finishes, `Expired` if it re-parks. Both halves matter; the second is the finding-1
   correction and is the one that used to hang. Exercising the branch directly needs a same-batch
   timer/fd ordering no test can schedule, so this is covered indirectly (see §5.2's note).
5. **`task_status` transitions** — a body that parks between bursts observes `Running` then
   `TimedOut`; under `scope_cancel` observes `Cancelled`; a scopeless/task-0 context reports
   `Running`.
6. **`task_cancelled` still fires on timeout** — an L0.5-style cooperative body using only
   `task_cancelled()` stops when its `with_timeout` deadline fires.
7. **Nesting isolation** — inner `with_timeout` `Expired` does not mark an outer scope; an
   outer `scope_cancel` does not read as `TimedOut` inside an unrelated inner scope.
8. **Nested-cancel loud-fail** (regression for the §5.5 hazard) — outer `scope_cancel` while a
   spawned task sits in `with_timeout` hits the deadline-owner guard and loud-fails, rather
   than silently orphaning the inner scope. Pins the deferred-cascade boundary.
9. **`ms <= 0`** — force-drops the already-forked child and returns `Expired` immediately
   without arming a timer (§13.1); assert the child does *not* run to completion (its side
   effect is absent).

Plus the DoD gates for runtime changes: example canary, ASan run, both poller backends. Note
(L0.5 lesson, memory): the freed-stack UAF for a force-dropped child only bites when the child
holds a heap value rooted *into its own stack* across the park — tests must root a live heap
value across the timed-out park, not just park a bare `accept`.

## 12. Spec / docs updated

- `stdlib/task.sprout` doc comments (the module is the primary reference for the task API).
- A short `docs/spec-v0.md` note if the concurrency surface is spec'd there (verify;
  L0.x has been doc-driven, not spec-normative, so this may be a design-doc-only surface).
- This doc flipped to `IMPLEMENTED` with the landing commit, mirroring the cancellation doc.
- Update the exploration doc's "next increments" line and the L0.6 memory note.

## 13. Decisions (resolved with Kuba, 2026-07-15)

1. **`ms <= 0` semantics — RESOLVED: immediate `Expired`, body never forked.**
   *(Refined during implementation.)* The original plan (fork, then force-drop the child) is
   **unsafe**: right after `task_fork` the child is in the *ready queue*, not parked, and
   `force_drop_task` on a queued task is a UAF (the pump would later pop a freed record — the
   same hazard as §5.2's runnable-child branch). The clean fix: **guard `ms <= 0` in the
   `with_timeout` stdlib wrapper, before `with_scope`/`task_fork`** — so the body is never
   forked and never runs, and `__await_deadline` is only ever reached with `ms > 0`. "0 ms for
   a result" is contradictory; nothing to drop, no timer armed. (Test §11.9 asserts the body's
   side effect is absent — trivially true since it never runs.)
2. **Result type — RESOLVED: dedicated `TimeoutResult a = Completed a | Expired`.** Not
   `Maybe a` — `Nothing`-on-timeout is ambiguous when `a` is itself `Maybe b`, and
   `Completed`/`Expired` reads at the call site (Sprout's explicit-over-implicit style).
3. **`task_cancelled` — RESOLVED: reimplement in Sprout, remove the builtin.**
   `task_cancelled() = __task_stop_reason() != 0`. One fewer builtin, cleaner layering. Gated
   on the seed-safety check in §6.2/§10 (the compiler bundle does not import `stdlib.task`, so
   removal is expected seed-safe — verified, not assumed; bridge protocol as fallback).
4. **Busy-body cooperative timeout — RESOLVED: accept + document; defer the deadline heap.**
   The MVP delivers the deadline when the ready queue empties and the pump reaches `poll_wait`.
   Parked work (blocking I/O — the point of timeouts) is prompt; a body that spins+yields but
   never parks is not timed out, documented loudly on `with_timeout` (§5.4). A pump-loop
   deadline heap (set `reason` every pump iteration, independent of `poll_wait`) is a clean
   future improvement but adds hot-path cost — out of scope for this increment.

## 14. Implementation notes (2026-07-16) — two compiler bugs surfaced

**(a) FIXED — generic type constructors were never exported cross-module (bundler).** The
dedicated `TimeoutResult a` refused to resolve from an importing module ("Unknown constructor:
`task.Completed`"). Isolation tests pinned it precisely: a *local* generic ctor match works, a
*cross-module non-generic* ctor (`task.Running`, `JsonError`) works, but a *cross-module generic*
ctor fails. `TimeoutResult a` is the **first** generic user-facing ADT meant to be
matched/constructed across a module boundary — every prior generic ADT is prelude (`Maybe`,
`Result`, auto-imported) or same-module-only, so the bug sat latent. Root cause: the bundler
inlines imported modules and decides which types export their constructors via a byte-level
line-scanner (`process_line` → `has_ctor_export`) that checked for `(..)` **immediately after the
type name, skipping only spaces — never the type parameters**. So `export type Foo (..)` was
detected but `export type Foo a (..)` was not (`a` sits between the name and `(..)`). Fix:
`skip_type_params` skips the parameter idents before the `(..)` check (`stdlib/compiler/bundler.sprout`).
This unblocks **every** future generic stdlib ADT — notably `Chan a` for channels. Regression
coverage: `tests/stdlib/test_task_timeout.spr` exercises cross-module generic-ctor match/construct
through `stdlib.task`'s `TimeoutResult a`.

**(b) OPEN follow-up — `export extern fn` names are not qualifiable (`module.fn`).** While writing
a fixture, `task.task_yield` failed with "Unknown variable" even though `task_yield` is
`export extern fn` in `stdlib.task`. The same bundler line-scanner (`process_line`) recognises
`export fn`/`export let`/`export type`/`export class` but **not** `export extern fn`, so an extern
export is not added to the module's exported-name set and cannot be reached via an
`import … as alias` prefix (unqualified `import stdlib.task (task_yield)` still works). Orthogonal
to deadlines and low-impact (externs are usually thin wrappers re-exported as plain `fn`), so it is
left as a documented follow-up rather than expanded into this increment. Fixtures avoid it by not
qualifying extern calls.
