# Cancellation & structured error propagation — design proposal (2026-07-15)

**Status: IMPLEMENTED (2026-07-15).** Step 1 (cooperative core — `scope_cancel` +
`task_cancelled`) landed in 4532ce0; step 2 (I/O-drop of poller-parked tasks via
`sprout_poll_remove` + the global `g_io_head` enumeration, §7) landed on this branch. All
open questions resolved with Kuba (§9, §10). Per the Design Change Process in `AGENTS.md`.
Prior-art rows are verified against primary sources (§3). Builds on L0.1–L0.4 (green-thread
scheduler, nested scopes, I/O netpoller, result-carrying `task_fork`/`task_await`).

**Decisions locked:** cooperative delivery + scheduler-drop (no exceptions to inject);
explicit owner-only `scope_cancel` + binary `task_cancelled` (rich `task_status` deferred
to the deadlines increment); body-orchestrated fail-fast (auto fail-fast deferred to a
task-group layer, §11); downward propagation local-for-MVP (§10.1); resource cleanup on
drop = accept-and-document (§10.3); one net-new runtime piece = `sprout_poll_remove`.

---

## 0. TL;DR

- L0.4 shipped **join-always** structured concurrency with **manual** error handling:
  a failing task does not stop its siblings — `with_scope` waits for all of them.
- This increment adds **cancellation**: a way to stop a scope's still-running tasks
  early, so a request can fail fast instead of waiting on now-useless work.
- **The hard constraint: Sprout has no exceptions.** Every mainstream structured-
  concurrency system delivers cancellation *cooperatively*, and the exception-based
  ones (Kotlin/Swift/Trio) inject a `Cancelled` exception at suspension points. Sprout
  cannot inject an exception, so it follows **Go's model**: a cooperative cancellation
  flag on the scope, checked at yield/await points, **plus scheduler-drop** for tasks
  parked on I/O (the pump reclaims them instead of resuming — safe because Sprout has
  no `defer`/`finally` cleanup to run).
- **Recommended MVP:** an explicit `scope_cancel(scope)` primitive + a cooperative
  `task_cancelled()` check + runtime scheduler-drop of a cancelled scope's parked
  tasks. The **body** orchestrates fail-fast (await a result, on `Err` call
  `scope_cancel` and return the `Err`) — so cancellation needs **no new return types**
  and composes with L0.4's raw-`a` `task_await`. Automatic fail-fast (drop the manual
  `scope_cancel` call) is deferred to a `_try` combinator / the Layer-2 framework.

---

## 1. Problem statement

L0.4's honest limitation: with no cancellation, if `pg_query_user` fails, `with_scope`
still **waits for `redis_get_prefs`** to finish (or park forever) before returning. The
result is correct and nothing leaks, but there is no fail-fast latency — the §4.A
flagship ("two queries, fail together") is only half-delivered. Cancellation is what
lets a scope stop still-running siblings once their results are known to be unneeded
(a sibling failed, or a race resolved).

## 2. Goals / non-goals

**Goals**
- Let a scope **stop its still-running tasks early** and return promptly.
- Uphold the §0.5 ranking (SIMPLE / PREDICTABLE / DEBUGGABLE first): a predictable,
  cooperative model with no hidden control flow.
- Compose with L0.4 as-is: raw-`a` `task_await`, join-always `with_scope`, the two
  spawns. **No breaking changes.**
- Handle the case that actually matters — a task **parked on I/O** in a cancelled
  scope must stop without its fd ever becoming ready (the netpoller deregistration the
  L0.4 review flagged as the one net-new runtime piece).

**Non-goals (this increment)**
- Exception-style non-local unwind. Sprout has none; we will not add one for this.
- Forced preemption of a compute-bound task that never yields or checks (no system
  does this; cooperative is the ceiling).
- Automatic fail-fast wired into `task_await`/`with_scope` themselves (needs Result-
  awareness that fights raw-`a`; see §5.3 — deferred to a `_try` layer / framework).
- Timeouts / `task_sleep` (separate increment; a deadline is a cancellation *source*
  that layers on cleanly once this lands).

## 3. Prior-art survey (verified against primary sources, 2026-07-15)

| System | Delivery mechanism | Cooperative? | Trigger: child failure → siblings |
|---|---|---|---|
| **Kotlin** coroutines | `CancellationException` thrown at suspension points; check via `isActive` / `ensureActive()` / `yield()` | Yes | `coroutineScope` cancels siblings on child failure (`supervisorScope` does not) |
| **Swift** structured concurrency | sets an `isCancelled` flag (never cleared); `checkCancellation()` throws `CancellationError`; *"no effect at all unless something checks for cancellation"* | Yes — *"fully cooperative and synchronous"* | throwing task group cancels remaining children + rethrows |
| **Python Trio** | `Cancelled` raised at **checkpoints** (await points); cancel scopes, optional deadlines/shielding | Yes | nursery cancels all children on unhandled exception, re-raises grouped |
| **Go** `errgroup`+`context` | cooperative **token**: `ctx.Done()` channel — **no exception** | Yes — goroutine must check `ctx.Done()` | first non-nil error cancels the shared `Context` |
| **Java** `StructuredTaskScope` | thread **interrupt**; `Joiner` policy (`awaitAllSuccessfulOrThrow` default) | Yes — *"subtasks must be coded to finish ASAP when interrupted"* | failure Joiner shuts down scope → interrupts remaining subtasks |

**Consensus:** (a) cancellation is **universally cooperative** — no system force-stops a
running task; (b) delivery splits into *exception-injection* (Kotlin/Swift/Trio, needs
exceptions) vs *cooperative token* (Go, no exceptions); (c) the common trigger is
"first child failure cancels the siblings."

### Sources
- Kotlin cancellation: https://kotlinlang.org/docs/cancellation-and-timeouts.html
- Swift SE-0304 (structured concurrency, cancellation section):
  https://github.com/swiftlang/swift-evolution/blob/main/proposals/0304-structured-concurrency.md
- Trio core reference (checkpoints, nursery, cancel scopes):
  https://trio.readthedocs.io/en/stable/reference-core.html
- Go `errgroup` (WithContext, cancel-on-first-error):
  https://pkg.go.dev/golang.org/x/sync/errgroup
- Java `StructuredTaskScope` (Joiner, shutdown-interrupts, cooperative):
  https://docs.oracle.com/en/java/javase/25/docs/api/java.base/java/util/concurrent/StructuredTaskScope.html

## 4. The exception-less constraint (why this is the crux)

In Kotlin/Swift/Trio the cancellation exception is a control path **separate from the
return value**: cancelling a task throws *through* it, unwinding to the scope. Sprout
has only return values. Two consequences:

1. **Delivery** must be a cooperative flag (Go model), not an injected exception —
   *plus* scheduler-drop for parked tasks, since a task suspended in `kevent`/`epoll`
   can't "check a flag" until resumed, and we don't want to resume it just to make it
   bail. Sprout's lack of `defer`/`finally` is a gift here: dropping a parked task runs
   no cleanup, so it is safe to abandon (the one resource concern — a half-open socket
   — is addressed in §7 and Open Questions).

2. **The cancelled task's "result" has no natural value.** `task_await(t) -> a` must
   return an `a`; a cancelled task never produced one. The resolution that avoids
   inventing a bottom value or a new return type: **you don't await cancelled tasks.**
   The body cancels *after* it already has the result it needs (a sibling's `Err`), and
   returns that — the cancelled siblings are dropped, never awaited (L0.4's never-
   awaited-fork backstop already reclaims them). Cancellation stays out of the type of
   `task_await`.

## 5. Design

### 5.1 Recommended: explicit cooperative cancellation (MVP)

Two new primitives + runtime honoring of the flag:

```sprout
# request cancellation of every still-running task in `scope`
export fn scope_cancel(scope: Scope) -> Unit !{IO}

# does the current task's scope want it to stop? (cooperative check for compute loops)
export fn task_cancelled() -> Bool !{IO}
```

**Delivery — two halves:**
- **Cooperative check** (for compute-bound tasks): a task polls `task_cancelled()` at
  loop heads / between steps and returns early when it sees `true` — exactly Go's
  `ctx.Done()` / Kotlin's `isActive` / Swift's `isCancelled`. A task that never checks
  and never yields runs to completion (the cooperative ceiling everyone lives with).
- **Scheduler-drop** (for parked tasks — the common, important case): `scope_cancel`
  marks the scope cancelled and, for each of the scope's tasks that is **parked on I/O
  or sitting in the ready queue**, deregisters its fd from the poller, removes it from
  the queue, reclaims it, and decrements the scope's live-count. So a task blocked on a
  socket read in a cancelled scope stops *without* its fd ever becoming ready. The
  running canceller itself continues.

**Fail-fast is orchestrated by the body** — no new types, composes with raw-`a`:

```sprout
fn handle_user(pg, rd, id) -> Result AppError Response !{IO} =
  with_scope(\s ->
    do
      ut <- task_fork(s, \_ -> pg_query_user(pg, id))
      pt <- task_fork(s, \_ -> redis_get_prefs(rd, id))
      let u = task_await(ut)                          -- raw Result (L0.4)
      match u with
      | Err e   -> do { scope_cancel(s); Err(DbErr(e)) }   -- fail fast: stop prefs, return
      | Ok user ->
          let p = task_await(pt)
          match p with
          | Err e    -> Err(CacheErr(e))
          | Ok prefs -> render_user(user, prefs)
    )
```

After `scope_cancel(s)` the body returns `Err(...)` **without awaiting `pt`**; the prefs
task is dropped (parked → deregistered + reclaimed, or reclaimed at scope close). No
cancelled task is ever awaited, so `task_await` keeps its clean `-> a` type. `with_scope`
still joins unconditionally: once the cancelled siblings are dropped, live-count hits
zero and it returns the body's `Err`.

### 5.2 Why explicit-first (not auto-fail-fast) for the MVP

`task_await` returns `a` **raw**, so `with_scope`/`task_await` are failure-agnostic —
they cannot peek inside an opaque `a` to see an `Err` and auto-cancel. Making them
Result-aware is a real API expansion (§5.3). Per §0.6 (keep primitives simple,
ergonomics at Layer 2), the primitive layer gets the *mechanism* (`scope_cancel`); the
*policy* (first `Err` auto-cancels) is sugar on top. This mirrors L0.4's stance
(primitive stays dumb; the body/framework composes behavior).

### 5.3 Deferred alternative: auto fail-fast via a Result-aware `_try` layer

For callers who want Trio/Java-style automatic fail-fast without the manual
`scope_cancel`, a later additive layer — *not* this increment:

```sprout
task_fork_try(scope, work: Unit -> Result e a) -> Task (Result e a)
with_scope_try(body: Scope -> Result e a) -> Result e a   # first forked Err auto-cancels siblings
```

Here the scope *does* understand `Result`, so a forked task returning `Err`
auto-triggers `scope_cancel`. This is purely additive over §5.1 (it *calls*
`scope_cancel` internally) and can also live in the Layer-2 framework. Listed so the
MVP is a deliberate subset, not an accident.

### 5.4 Rejected: cancellation-as-value in every scope's return type

Making `with_scope` return `Result Cancelled a` universally would put cancellation in
*every* scope's type even when unused — noise against principle 1, and it forces the
`_try` shape on all callers. Kept optional (§5.3) instead.

## 6. Semantics

- `scope_cancel(s)` is idempotent; calling it on an already-cancelled scope is a no-op.
- A task **already finished** when `scope_cancel` runs is unaffected (its result stands;
  if it was an awaitable fork, it is still awaitable/reclaimed as in L0.4).
- A **nested** inner scope: cancelling an outer scope does **not** implicitly cancel a
  running inner scope in this MVP (explicit only). *Open question §10.1* — Trio/Kotlin
  propagate cancellation down the tree; we may follow, but it interacts with the
  single-pump/live-count model and deserves its own decision.
- `task_cancelled()` reflects the current task's own scope's flag.
- **Awaiting a dropped task is illegal and loud-fails.** The invariant is "a cancelled
  task is never awaited" (§4.2): the owner cancels *after* it has the result it needs and
  drops the rest without awaiting them. A body that violates this — `scope_cancel(s)` then
  `task_await(t)` on a fork `t` that was I/O-parked and hence dropped — aborts with a clear
  message rather than parking the owner forever (nothing would ever wake it). Enforced in
  `__task_await` via the dropped record's freed roots (`roots == NULL`).
- `with_scope` semantics are unchanged except that a cancelled scope drains faster
  (dropped tasks stop counting toward live).

## 7. Runtime implementation (`sprout_scheduler.c`) — IMPLEMENTED (L0.5 step 2, 2026-07-15)

- `Scope` gains a `cancelled` flag and an `owner` task pointer (set at `__scope_open` to
  `g_current_task`) — both landed in step 1.
- **Enumeration of droppable tasks is via the global `g_io_head` list, not a per-scope
  task list.** Only *I/O-parked* tasks are ever force-dropped, and every such task —
  `task_fork` and `task_spawn` alike — passes through `scheduler_park_on_fd`, which links
  it onto one global doubly-linked list of currently-poller-parked tasks (`g_io_head`; the
  pump unlinks on wake, cancel unlinks on drop). `scope_cancel` walks that list and drops
  the entries whose `->scope` matches. This is the single source of truth for "who is
  parked on I/O" (it also replaced the old `g_io_parked` counter), so it needs **neither**
  the earlier sketch's "extend the scope's task tracking to all its tasks" **nor** a second
  per-scope list — fire-and-forget tasks are enumerable for free, and done tasks never
  appear on it. Each `Task` records the `park_fd`/`park_interest` it is asleep on so the
  drop can call `sprout_poll_remove`.
- `scope_cancel(scope)`: **loud-fail if `g_current_task != scope->owner`** (owner-only,
  §10.2); otherwise set `cancelled`. The drop is **selective**, not force-drop-all —
  force-dropping a task suspended in a *nested join* would orphan its inner scope (the
  inner tasks' joiner vanishes → lost wake / UAF) under local propagation. So:
  - **I/O-parked tasks** → force-dropped: they *cannot* check a flag while suspended in
    the poller, so `scope_cancel` **deregisters the fd** (`sprout_poll_remove` — new) and
    reclaims, `live--`. This is the anchor use case (a sibling blocked on a socket).
    **Reclaim lifecycle (do NOT copy L0.4's *done* reclaim):** a task dropped mid-`tcp_*`
    is suspended with **live values rooted into its green stack** (L0.3's park contract),
    so its roots context must be **freed together with the stack for BOTH kinds** — unlike
    a *done* awaitable task (empty LIFO + result rooted in the record). A dropped task
    never completed, has no result, and is never awaited, so: fire-and-forget → free
    roots + stack + record; awaitable → free roots + stack, `roots = NULL`, keep the record
    in `forks`, and **guard scope-close** with `if (f->roots) sprout_roots_free(f->roots)`
    against double-free. Keeping the roots while freeing the stack is a **use-after-free**
    (`mark_roots` would scan freed stack). **The negative control must be run under ASan,
    not just `SPROUT_GC_STRESS=1`:** a freshly-freed green stack usually still holds valid
    root addresses, so `mark_roots` reads them without crashing and the buggy version can
    print "done" and exit 0 — a false pass. Verified 2026-07-15: the correct version is
    ASan-clean under GC stress; the buggy variant (roots kept, stack freed) is a
    deterministic ASan heap-use-after-free (`READ` in `sprout_gc_mark_root_list`, memory
    freed in `__scope_cancel`), which stress alone caught only at exit-finalize.
  - **Awaiter guard:** owner-only cancel stops the *owner* from awaiting a dropped task,
    but a *forked* task F can `task_await` a sibling A (F sits in `A->awaiter`). Dropping A
    would strand F (nothing wakes it → `live` never hits 0 → the owner's join deadlocks).
    So dropping a task with `awaiter != NULL` must **`sprout_fail`** with a clear message
    ("cancelling a task awaited by a sibling; await only from the scope owner"). Full
    awaiter-cascade stays deferred (§10.2).
  - **Single-thread no-race (comment it at the drop site):** the pump drains every
    `poll_wait` token out of `g_io_head` before running the owner, so a task still in
    `g_io_head` at `scope_cancel` time has not fired — `poll_remove` then reclaim is safe;
    `EV_DELETE`/`EPOLL_CTL_DEL` → `ENOENT` is ignored. During cancel the owner is running so
    `s->waiter == NULL` (no wake mid-cancel).
  - **ready / yield-parked tasks** → left in place; they resume normally and are expected
    to **check `task_cancelled()` and return**. A task that never checks never stops —
    the cooperative contract, exactly as Go's `ctx.Done()`.
  - **join-parked tasks** (blocked in a nested `__scope_join`) → left alone; the inner
    scope drains first (local propagation, §10.1), then the task returns from its join and
    can check `task_cancelled()`.
  - The running canceller is skipped. Waking the scope's join waiter when `live` hits zero
    reuses the existing path.
- `task_cancelled()`: read `g_current_task->scope->cancelled` — the mechanism by which a
  ready/yield-parked task cooperatively stops (only I/O-parked tasks are dropped for them).
- Poller: add **deregistration** `sprout_poll_remove(fd, interest)` (kqueue `EV_DELETE`
  by fd+filter; epoll `EPOLL_CTL_DEL` by fd, `interest` unused; both ignore `ENOENT`) —
  the piece the L0.4 review flagged as the sole net-new poller capability.
- GC-rooting: a dropped task is **not** reclaimed like a never-awaited L0.4 fork (those
  keep roots until scope-close because they *completed*). A dropped task is suspended with
  stack-pointing roots, so its roots are freed **immediately** with its stack (see the
  reclaim-lifecycle note above). No result is kept for a dropped task.

## 8. Impact — Design Change Process checklist

- **Syntax:** none — two new library functions.
- **Type system:** none for the MVP (§5.1). The optional `_try` layer (§5.3) adds
  `Result`-typed combinators later, still no new type machinery.
- **Effects:** none — both primitives are ordinary `!{IO}`.
- **Error messages:** two runtime loud-fails on the drop path (not type errors): (a)
  `scope_cancel` by a non-owner; (b) `task_await` on a task that `scope_cancel` dropped
  (`roots == NULL`) — a body that violates "a cancelled task is never awaited" (§4.2) gets
  a clear abort instead of a silent hang. Both uphold the codebase's loud-fail-over-silent
  ethos; neither fires for a correct program.
- **Compat/migration:** fully additive. L0.4 code is unchanged; a program that never
  calls `scope_cancel` behaves exactly as today.
- **Builtins:** `scope_cancel`/`task_cancelled` are thin Sprout wrappers over new
  scheduler externs (`__scope_cancel`, `__task_cancelled`); `sprout_poll_remove` is
  internal C (no new Sprout builtin). APPROVED_BUILTINS updated for the externs.
- **Tests:** (1) `scope_cancel` stops a sibling parked on I/O — assert it does not run
  to completion and the scope returns promptly; (2) fail-fast body pattern returns the
  `Err` without awaiting the cancelled sibling; (3) `task_cancelled()` observed by a
  compute loop; (4) cancel of an already-finished task is a no-op; (5) green under
  `SPROUT_GC_STRESS=1` with a negative control on the drop/reclaim path.
- **Spec/docs:** experimental; documented here + the exploration doc's status. Not in
  `docs/spec-v0.md` until the concurrency model graduates.

## 9. Naming — RESOLVED (2026-07-15)

`scope_cancel(scope)` (Kotlin `Job.cancel`, Go cancel func) and `task_cancelled() -> Bool`
(Swift/Kotlin `isCancelled`) — consistent with the L0.4 `task_`/`scope_` split
(`scope_cancel` acts on a scope; `task_cancelled` queries the current task).

**`task_cancelled` stays *binary* for the MVP** because there is exactly one stop-reason
(explicit `scope_cancel`). This matches the "should I stop?" cooperative-check need, which
every system keeps binary — including Go, whose rich model still exposes a **binary**
`ctx.Done()` alongside the reason-carrying `ctx.Err()`. A richer **`task_status() ->
TaskStatus`** (Go's `ctx.Err()`: `Canceled` vs `DeadlineExceeded`) is deliberately deferred
to the `task_sleep`/deadlines increment — that is when a *second* stop-reason (`TimedOut`)
first exists and the ADT stops being a degenerate `Running | Cancelled` isomorphic to
`Bool`. Adding it then is additive (keep `task_cancelled` as `status != Running`), not a
rename; and it avoids the churn of extending an exported ADT (breaks exhaustive matches)
before the variants are real. See §10.4.

## 10. Open questions (resolve before implementation)

1. **Downward propagation — RESOLVED (local for MVP, 2026-07-15).** `scope_cancel(s)`
   stops only `s`'s direct tasks; an inner scope keeps running until it finishes or is
   cancelled explicitly. Tree-cancel (Trio/Kotlin) is a clean additive follow-up: same
   API (`scope_cancel` recurses), `task_cancelled()` unchanged, plus a parent→child
   scope link added *then* (scopes are independent mallocs today). **Framing safeguard:**
   document this as "downward propagation not yet implemented" — a limitation — **not**
   as "inner scopes shield from outer cancellation." Nobody may rely on non-propagation,
   so enabling tree-cancel later is non-breaking. Real shielding, if ever wanted, is an
   explicit opt-in (Trio `shield=True`), never the default.
2. **Who may call `scope_cancel` — RESOLVED (owner-only, 2026-07-15).** Only the task
   that opened the scope (the `with_scope` body) may cancel it; the runtime records the
   owner at `__scope_open` (`scope->owner = g_current_task`) and **loud-fails** a
   non-owner caller. Why: the "no cancelled task is ever awaited" invariant (what keeps
   `task_await` at a clean `-> a`) holds *only* because the owner cancels while **running**
   — between awaits, after it has the result it needs — so nothing is parked awaiting a
   to-be-dropped task. A *forked* task cancelling while the owner is parked in
   `task_await(sibling)` would drop that sibling and the owner's await would never wake
   (a hang). Owner-only makes "no dropped task has a pending awaiter" true by construction.
   Does not restrict the use case: a forked task that wants to abort returns `Err`; the
   owner observes it and cancels. Loosening to non-owner cancel later (with awaiter-cascade
   handling) is additive.
3. **Resource cleanup on drop — RESOLVED (accept-and-document, 2026-07-15).** A task
   dropped mid-I/O leaks its fd; the MVP does not close it. Rationale: in the anchor web
   use case the connection fd is owned/closed by the framework/accept-loop, not the
   dropped handler task — so close-on-drop would be the *wrong* owner closing an fd still
   in use, a worse bug than a leak on the (rare, error-path) cancel. Document the leak.
   The real fix — scope-registered cleanup hooks (a `defer`-like on the scope) — is a
   clean additive follow-up; "leak for now" locks in no wrong ownership model.
4. **`task_sleep` / deadlines interaction (forward-note, not an MVP decision):** a
   sleeping task in a cancelled scope must also be droppable — the `poll_remove` drop
   path is designed to cover timers too. This increment also introduces the *second*
   stop-reason (`TimedOut` vs `Cancelled`), which is where the richer **`task_status() ->
   TaskStatus`** query lands (§9) — added alongside `task_cancelled`, not replacing it.

## 11. Relationship to a future task-group abstraction

The handle model here (`task_fork -> Task a`, `task_await -> a`) is the *heterogeneous,
fixed-arity* shape (Swift `async let`, Java `fork(Callable)` + `Subtask.get()`) — it fits
"fetch **user** and **prefs**" where the children have different types. A **task group**
(Swift `withTaskGroup(of: T)`, Java `StructuredTaskScope` + a `Joiner`) is the
*homogeneous, dynamic-arity* shape — "fan out N identical subtasks, reduce" — an additive,
**later** abstraction, not a replacement for the handles.

A task group is the natural home for **automatic** fail-fast: because it knows its child
type `T` and inspects results as they complete, it can apply a policy (all-succeed /
any-succeed / first-error) and cancel the rest automatically — this **is** §5.3's deferred
`_try` layer, generalized. Crucially, a task group changes nothing in this increment's
*mechanism*: when its policy fires, it stops siblings through the very same `scope_cancel`
+ scheduler-drop + `sprout_poll_remove` built here — it is the top **consumer** of these
primitives, not a substitute for them. Sequencing: **primitives (this increment) → task
group with policy-driven auto-fail-fast (next)**.
