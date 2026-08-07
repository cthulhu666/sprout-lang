# Linear `Task a` — design note (v0)

Status: **design note, pre-approval.** Written 2026-08-07. Proposes making `stdlib.task`'s `Task a`
a `type linear` (the M4 feature landed in PR #22/#23), so a forked task handle must be consumed
exactly once — awaited or detached — and double-await / dropped-result become *compile* errors.
This is the one resource in Sprout today that fits the current **first-order, borrow-less** linear
type system cleanly, and the API author already earmarked it (`stdlib/task.sprout:20–23`).

## 1. Problem statement

`task_fork(scope, work)` returns a `Task a` handle whose result is retrieved by `task_await`.
Today the discipline is documented but unenforced:

- **Double-await is a runtime fault.** *"Awaiting a given Task more than once is unsupported (a
  future linear `Task` will reject it statically)"* (`task.sprout:98`). Today the second await
  hits a dropped/reused task id and loud-fails at runtime.
- **A dropped result is silent.** A forked task you never await is scope-reclaimed — no crash, but
  you asked for a value and silently discarded it (a logic bug the type system could catch).

Both are exactly-once violations: await-twice (reuse) and never-consume (leak). Linearity is the
precise tool.

## 2. Goals / non-goals

**Goals.**
- Make `Task a` linear; `task_await` and a new `detach` are its two consuming operations.
- Turn double-await into a compile error and a never-consumed fork into a compile error.
- Keep the public API first-order and require **no new builtin** and **no borrowing**.
- Collapse `task_spawn` into derived sugar (`fork` + `detach`), per the author's forward note.

**Non-goals.**
- **Cross-body / cross-task handoff of a `Task` handle** (fork in one function, await in another via
  a captured handle) — that is higher-order linearity (M4.4, deferred). This note covers the
  fork→(branch)→await/detach-in-one-body shape, which is what all current `Task` users are.
- **Linearity of the captured `work` closure.** If `work` captures a linear value, that is the
  separate M4.4 lambda-capture case; out of scope here.
- Linear TCP connections (the higher-value resource) — blocked on borrowing; tracked separately.

## 3. Prior-art survey (primary-sourced)

The "consume exactly once, via one of two operations" discipline for thread/task handles is
established; both anchors verified against primary sources:

| Language | Handle | Discipline | Enforcement |
|----------|--------|-----------|-------------|
| **C++** `std::thread` | thread object | must `join()` **or** `detach()` before destruction, else the destructor calls `std::terminate()` ([thread.thread.destr]) | **runtime** (terminate). C++20 `std::jthread` auto-joins on destruction — an affine default-consume. |
| **Rust** `std::thread::JoinHandle` | join handle | `join(self)` **consumes** the handle (by value → cannot join twice); dropping the handle **detaches** | **compile-time** for no-double-join (move semantics); drop = detach is *affine*, not forced |

Sprout's proposal sits between them: like Rust, `await` consumes by value so double-await is a
compile error; unlike Rust's silent drop-detaches, Sprout is **strictly linear** — a handle must be
*explicitly* consumed (`await` or `detach`), so a forgotten handle is a compile error, not a silent
detach. `detach` is Sprout's explicit spelling of C++ `detach` / Rust's implicit drop. C++'s
runtime `terminate` becomes Sprout's compile-time rejection — the improvement linear types buy.

## 4. Design

### 4.1 The type and its two consuming operations

```
export type linear Task a =           # one-word change: `type` -> `type linear`
  | Task Int

# await CONSUMES the handle (already takes it by value; passing as an argument is a consume).
export fn task_await(task: Task a) -> a !{IO} = …          # unchanged signature

# detach CONSUMES the handle without awaiting: "I relinquish this; let the scope reclaim it."
export fn detach(task: Task a) -> Unit =
  match task with
  | Task _ -> Unit
```

`Task Int` makes `a` a **phantom** type parameter (the payload type is not stored). The constructor
stays module-private (no `(..)`), so a handle cannot be forged — essential for a linear resource
(reconstructing `Task(id)` from a borrowed id would alias one task under two linear handles and
break the invariant).

### 4.2 `detach` needs **no** builtin

A never-awaited fork is *already* reclaimed when the scope closes (`task.sprout:92`, and the runtime
force-drop on the `with_timeout` Expired path). So `detach` performs **no runtime action** — it is a
pure-Sprout value discard whose only job is to be a *consuming use* the linear checker counts. This
keeps the change within the "no new builtins without approval" rule: nothing is added to
`runtime/sprout_runtime.c` or `APPROVED_BUILTINS`.

### 4.3 `task_spawn` becomes derived sugar

Per `task.sprout:20–23`, fire-and-forget collapses into fork-then-detach:

```
export fn task_spawn(scope: Scope, work: Unit -> Unit !{IO}) -> Unit !{IO} =
  detach(task_fork(scope, work))
```

One spawn primitive instead of two, with no call-site churn. (`__scope_spawn` can remain as the
underlying builtin, or `task_spawn` can route through `__task_fork`; either is behaviour-preserving.)

### 4.4 The branch-convergence interaction — the crux

Sprout's M4 linear checker enforces **branch convergence**: every arm of an `if`/`match` must consume
the identical set of linear bindings. `with_timeout` violates this *by design* today:
`finish_timeout` (`task.sprout:181`) awaits on the `Completed` branch but **not** on the `Expired`
branch (the runtime already force-dropped the child). Under linearity that is a divergence error —
one arm consumes `handle`, the other does not.

`detach` resolves it exactly: the `Expired` arm calls `detach(handle)`, so **both** arms consume the
handle and convergence holds.

```
fn finish_timeout(handle: Task a, completed: Bool) -> TimeoutResult a !{IO} =
  if completed then complete_with(handle)      # awaits  -> consume
  else do detach(handle); Expired              # detaches -> consume  (was: drop the handle)
```

This is the design working as intended: the type system *forced* us to name what happens to the
handle on the timeout path, and the answer (relinquish it) is now explicit.

### 4.5 The borrow snag in the existing plumbing — and the first-order fix

There is one real friction. `await_and_finish` (`task.sprout:171`) currently **peeks** the handle to
extract its raw `tid` (for `__await_deadline`) **and** forwards the whole `handle` to
`finish_timeout`:

```
fn await_and_finish(sid, handle: Task a, ms) =
  match handle with
  | Task tid -> do completed <- __await_deadline(sid, tid, ms)
                   finish_timeout(handle, completed)   # handle used AGAIN after the peek
```

Under linearity this is a **reuse**: the `match` scrutinee consumes `handle`, then `handle` is used
again in the arm. Peeking-a-field-while-forwarding is precisely a *borrow*, which M4 does not have.
Reconstructing `Task(tid)` to "give it back" is unsound (§4.1).

**Fix without borrowing:** fold the tid-extraction into the single consuming operation, so the handle
is inspected only at the point it is consumed. Introduce one internal consuming primitive:

```
# Consumes `handle`: arms the deadline on its task, parks, returns whether it completed in time.
fn await_within(handle: Task a, sid: Int, ms: Int) -> TimeoutResult a !{IO} =
  match handle with
  | Task tid -> do completed <- __await_deadline(sid, tid, ms)
                   if completed then complete_with_id(tid)   # child done: await its result
                   else Expired                              # child force-dropped: nothing to consume
```

Here `handle` is destructured exactly once and never forwarded — the `Task tid` match *is* its sole
consume. This keeps `with_timeout` first-order and borrow-free. (It is a small refactor of three
private helpers in `task.sprout`; the public `with_timeout` signature is unchanged.)

## 5. Type-system impact

- Uses the landed M4 machinery unchanged: `@linear:Task` sentinel + consume-exactly-once. The checker
  keys linearity on the *head* type name (`head_type_name` strips `TApp`), so a parametric `Task a`
  is handled by the *check* side already.
- **Pre-implementation check — RESOLVED by spike (2026-08-07).** M4.1's `type linear` was only *tested*
  on non-parametric types (`File`, `Pos`, `Wrap`, `Color`), leaving parametric `type linear Task a` (a
  phantom `a`) unverified. A throwaway spike drove three files through `--phase check` on the stage-1
  checker: a parametric `Task a` value used **once is accepted** (0 errors), used **twice is rejected**
  (`'t' used more than once`), and **never used is rejected** (`'t' is never used`) — identical to the
  non-parametric control. The reuse/leak errors *firing on the parametric type* prove enforcement
  engages (not a silent skip), and `head_type_name` strips the `TApp` to `Task`, so `@linear:Task`
  matches regardless of the type argument. **No M4.1 gap; the type-declaration layer is not a blocker.**
  A committed parametric case should still be added to `test_parser.spr` + `test_linear_type_decl.spr`
  when the feature lands (closes the pre-existing coverage gap the spike exposed).

## 6. Error-message impact

New rejections reuse M4's existing diagnostics:
- Double-await → `linear value 'h' is used more than once …` (the reuse message).
- Forgotten fork → `linear value 'h' is never used …` (the leak message).
- Await-on-one-branch-only (if a user writes it) → `… used in some branches but not others …` — with
  `detach` as the documented remedy.

Worth adding: a `Task`-specific hint appended to the leak message (*"await it or `detach` it"*) so the
resource discipline is discoverable. Optional; not required for correctness.

## 7. Compatibility / migration

- All current `Task` users live in `task.sprout` itself (`run_with_deadline` / `await_and_finish` /
  `finish_timeout` / `complete_with`). The §4.4/§4.5 refactor updates exactly these; no external
  caller exists to churn.
- `task_spawn` callers (e.g. `http_server.serve_loop`) are unaffected — the signature is preserved;
  only its body becomes `fork`+`detach`.
- Forward note in `task.sprout:20–23` is fulfilled and should be replaced with a "landed" pointer to
  this doc.

## 8. What this does *not* unlock

- **Cross-body handoff** (fork here, await in a spawned task / stored handle) → M4.4 higher-order.
- **Linear TCP connections** → needs borrowing (a connection is read/written many times before
  `close`), and the `http_server` accept→`task_spawn` handoff captures the handle → M4.4. The genuinely
  high-value resource use stays blocked on those two deferred features. This note is the *small,
  ready* proof-point, deliberately not the big one.

## 9. Tests

- **Positive (run):** fork→await once; fork→detach (no await); `if …/match …` where every arm
  awaits-or-detaches (convergence holds); `task_spawn` = fork+detach still fire-and-forgets;
  `with_timeout` Completed and Expired paths both compile and run.
- **Negative (`type_error`):** fork then await twice → reuse; fork then neither await nor detach →
  leak; await on one branch, drop on the other → divergence. Each verified RED on the pre-change tree.
- **Parser:** parametric `type linear Task a = Task Int` parses and records `Linear` (§5 risk).
- Full suite + `just compile-examples-stage1` (touches `stdlib/`); the `http_server` example must
  still compile and run.

## 10. Spec / docs

- `docs/spec-v0.md` §5.8 (Linear types, Experimental): add `Task a` as the worked resource example.
- Replace the `task.sprout:20–23` forward note with a landed pointer here.
- `docs/idiomatic-sprout.md` (if it covers concurrency): show the fork→await/detach idiom.

## 11. Honest value assessment

**Moderate, not dramatic** — and worth stating plainly. Double-await is already a runtime loud-fail;
this moves it to compile time and additionally catches silently-dropped results. It is not a
use-after-free-class safety win (that is the socket case, blocked on borrowing). Its real worth is
(a) a **self-contained, low-risk demonstration** that the M4 machinery pays off on a real API, (b)
the API simplification (two spawns → one), and (c) surfacing, on a small surface, the borrow snag
(§4.5) that the higher-value resources will hit at scale — cheap evidence toward the borrowing
decision. If we want linear types to earn their place on *something* now, this is the right first
something.

## 12. As landed (2026-08-07)

Implemented per this note, with three deltas found during implementation:

1. **A `lin_do_bind` soundness fix was required (and made).** The idiomatic `h <- task_fork(...)`
   binds `h : Task a`, but `linear_check` guessed the binder type via `payload_type` (last
   type-argument), stripping `Task a → a` — so a linear value bound with `<-` in an `!{IO}` do-block
   silently escaped consume-once. This was a **latent M4.2 hole**, not Task-specific (any effect-bound
   linear). Fixed with `do_bind_type` (use the full type when itself linear, else the payload). Without
   it, linear `Task` would catch double-await only for params/direct use, not the `h <-` form people
   write. Regression: `tests/conformance/type_error/linear_task_double_await.spr`.
2. **`task_spawn` collapse (§4.3, §11b) deferred.** `task_spawn` takes no `Task` handle, so linearity
   does not force a change; collapsing it into `fork`+`detach` would swap its underlying scheduler
   primitive (a behaviour change for aesthetic gain). Left distinct; a follow-up.
3. **`with_timeout` refactor (§4.4/§4.5) landed as folded id-driven helpers.** `await_and_finish`
   matches the handle once (extracting `tid`); `finish_by_id`/`complete_by_id` drive the outcome by
   raw id — so no `detach` is needed on the timeout path (the match is the sole consume).
   `test_task_timeout` confirms behaviour is preserved.

Two follow-ups filed in `BACKLOG.md`: a discarded-linear-expression-result leak gap (a bare
`task_fork(...)` statement whose result is dropped is not leak-checked — a general M4 limitation), and
an imported-linear-type annotation wart (`fn f(t: Task a)` on an imported `Task` fails with
`stdlib.task.Task vs Task`; inference works, so handle params are left unannotated).
