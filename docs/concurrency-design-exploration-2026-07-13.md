# Concurrency & Parallelism — design exploration (2026-07-13)

**Status: EXPLORATORY. Not an approved design.** This is a shared-understanding
document to decide direction, per the Design Change Process in `AGENTS.md`. No
implementation is committed. The prior-art survey rows are verified against
primary/secondary sources (see §9); re-confirm against language references before
this graduates to an approved design change.

---

## 0. TL;DR

- Anchor use case: a **high-performance HTTP server + web app** talking to
  **Postgres** and **Redis**. This is I/O-bound concurrency (C10k), not CPU-bound
  parallelism.
- Recommended shape: **green-threaded, share-nothing, cooperative** concurrency —
  Go's *no-function-coloring* ergonomics on Erlang's *share-nothing* memory model.
- **Architecture is layered and swappable**, which directly serves the three
  constraints Kuba raised (don't over-commit, minimal GC/effects change now, keep
  future models like a 3D game's job system possible):
  - **Layer 0 — Substrate (the only runtime/C work):** non-blocking sockets +
    readiness poller (epoll/kqueue) + a cooperative scheduler (park/wake/ready-queue).
    This is a *backend* behind a small stable interface.
  - **Layer 1 — Models (pure Sprout stdlib, coexisting):** structured concurrency,
    channels, actors — all implemented over the Layer-0 interface. Ship one now,
    add others later, or keep all three.
  - **Backends are replaceable:** single-thread cooperative now (zero GC change);
    share-nothing multi-core later (modest GC change); shared-memory parallel
    (for the game) later (full GC change, but *not precluded*).
- MVP = Layer 0 (single-thread backend) + **one** Layer-1 model (structured
  concurrency). Everything else is additive.
- **No-rewrite scaling guarantee (§5.4):** a web app written on the single-thread
  backend scales to multi-core share-nothing workers with *unchanged handler code*,
  provided two invariants hold from day one — tasks share only immutable data, and
  cross-request state goes through a swappable `stdlib.shared` abstraction (not raw
  globals). Structured concurrency is the surface that survives all three tiers.

---

## 0.5 Design principles — the ranking criteria (dominant over everything below)

Sprout's fundamentals rank the whole design. In priority order:

1. **SIMPLE**
2. **PREDICTABLE / DETERMINISTIC**
3. **EASY TO REASON ABOUT AND DEBUG**
4. **ERGONOMIC** — crucial, but may be delivered at a higher framework layer (see §0.6),
   so the *primitives* optimize for 1–3.
5. **PERFORMANCE** — important, but explicitly *comes after* the above.

Concurrency is the single feature that fights principles 1–3 hardest, so the model
choice is dominated by them, not by throughput. How the models score:

| Criterion | Structured | Channels | Actors |
|---|---|---|---|
| Simple (few concepts) | **scope + spawn + await** | chan + spawn + send/recv | actor + msg-protocol + reply-addr |
| Predictable control flow | **bounded, tree-shaped** | free-form graph | free-form message graph |
| Failure reasoning | **propagates up like a call stack** | manual; silent orphans | supervisor trees |
| Debuggable | **scope = stack-like trace** | leak-prone, hard | mailbox timing hard |
| Ergonomic (raw) | medium (framework sugars it) | high | low |

Structured concurrency wins on 1–3, which are the ranked-highest criteria.

**Determinism — what it can and cannot mean here.** No concurrency model can make
*I/O arrival order* deterministic (which of Postgres/Redis answers first is real-world
nondeterminism). What the recommended stack *can* guarantee:
- **Deterministic control structure** — the shape of the task tree is fixed by the
  source, not by the scheduler.
- **Deterministic failure semantics** — errors unwind the scope tree the same way
  every run.
- **Deterministic replay for tests** — cooperative scheduling interleaves *only* at
  explicit yield points; with a FIFO ready-queue + a mockable poller, concurrency
  tests become **reproducible**. Preemptive/parallel models structurally cannot offer
  this. This is a direct payoff of principles 2–3 and a reason to prefer cooperative +
  single-thread beyond just "minimal GC change."

**Performance-after** (principle 5) reinforces: single-core cooperative MVP is
acceptable; defer the parallel backend and preemption; choose the *simplest* task
representation that is correct, not the fastest.

## 0.6 Ergonomics belongs at the framework layer (Layer 2)

Ergonomics is crucial, but it does **not** have to live in the concurrency primitives.
A webapp framework (a Sprout "Axum/Phoenix/Rails") sits above the models and provides
the ergonomic surface: routing, request→task mapping, request-scoped structured
concurrency, timeouts, cancellation on client disconnect, and connection pooling. The
end user writes `fn handle(req) -> Response` and rarely touches `with_scope` directly —
the framework spawns each handler inside a scope for them.

Consequence for this design: keep the **primitives simple and predictable** (principles
1–3); let the **framework** carry ergonomics (principle 4). We must not distort the
primitive layer toward terseness at the cost of being easy to reason about.

---

## 1. Where Sprout stands today (verified 2026-07-13)

| Aspect | State | Evidence |
|---|---|---|
| Runtime threading | **Single-threaded.** `pthread.h` used only for stack-bounds capture. No `pthread_create`, mutexes, atomics. | `runtime/sprout_runtime.c:23,245-247`; grep: 0 matches for create/mutex/atomic |
| GC | Stop-the-world, single-threaded mark-sweep; all state is plain `static` globals | `runtime/sprout_runtime.c` globals; `docs/compiler-internals.md` |
| Effects | **Scaffolding, unenforced.** `Effect = EffectPure \| EffectIO \| EffectRow \| EffectVar` exists; local inference exists; no interprocedural propagation, no enforcement. `fn pure_leak(x)->Int = print_int(x)` compiles clean. | `stdlib/compiler/types.sprout:14-18`; `docs/effects-and-nonalloc-analysis-2026-07-11.md:22-28` |
| Concurrency surface | **None** in the native path. No `spawn`/`async`/`channel`/`actor`. The only "reactor" is in the Python reference interpreter, not the compiled runtime. | grep; `examples/http_echo_server.sprout:7` |
| HTTP server | Sequential blocking accept loop, one connection at a time | `stdlib/http_server.sprout:287-296` |
| Do-notation | Already **effect/monad-polymorphic**: binds raw `!{IO}` values *and* `Result` (short-circuits `Err`) | `http_server.sprout:291` (raw IO) vs `:148` (Result) |

Takeaways that constrain the design:
1. True shared-memory parallelism needs a GC rewrite → **defer it**; do not gate the
   MVP on it.
2. Effects can't currently carry any safety guarantee → **do not build concurrency
   on typed effects yet.** (OCaml 5 ships untyped effects and still implements a full
   scheduler — see §9.)
3. Immutability-by-default is an asset: share-nothing message passing and even a
   future parallel backend have far fewer data races to reason about. The only shared
   mutable hazard is `ref`/MutVec.

---

## 2. Goals / Non-goals

**Goals** (ordered by the §0.5 principle ranking)
- **Uphold SIMPLE / PREDICTABLE / DEBUGGABLE first** — prefer the model and scheduler
  that are easiest to reason about, even at some cost to raw throughput or terseness.
- Handle thousands of concurrent, mostly-I/O-bound connections on the anchor use case.
- **No function coloring**: DB drivers are written in plain blocking style; the
  runtime parks the task at I/O points. One driver codebase works everywhere.
- Minimal *now*: zero GC change, zero effect-system change for the MVP.
- **Deterministic replay in tests** (cooperative + single-thread + mockable poller).
- Deliver end-user **ergonomics at the framework layer** (§0.6), keeping primitives simple.
- A substrate boundary designed so additional models and a future parallel backend
  slot in without rewriting user code (or with a clearly bounded rewrite).

**Non-goals (for the MVP)**
- Multi-core parallelism of a single shared heap (tier 3). Off the table near-term.
- Preemptive scheduling. Cooperative yield-at-I/O only. (Go/Erlang preempt; that's a
  large runtime lift — defer.)
- Typed/enforced effects for concurrency safety. Layer later, optional.
- Distribution / clustering across machines.

---

## 3. The layered architecture

```
┌─────────────────────────────────────────────────────────────┐
│ Layer 2 — FRAMEWORK  (ergonomics live here — principle 4)    │
│   webapp framework: routing, request→task, request-scoped    │
│   concurrency, timeouts, cancel-on-disconnect, pooling.      │
│   User writes `fn handle(req) -> Response`; framework spawns  │
│   it inside a scope. Keeps primitives below simple.          │
└───────────────────────────▲─────────────────────────────────┘
                            │  uses one (or more) Layer-1 models
┌───────────────────────────┴─────────────────────────────────┐
│ Layer 1 — MODELS  (pure Sprout stdlib; import what you want) │
│   stdlib.task   structured concurrency (scopes/nurseries)    │
│   stdlib.chan   goroutines + channels                        │
│   stdlib.actor  actors + mailboxes                           │
│   (future) stdlib.jobs  data-parallel job system (the game)  │
└───────────────────────────▲─────────────────────────────────┘
                            │  small, stable substrate interface
┌───────────────────────────┴─────────────────────────────────┐
│ Layer 0 — SUBSTRATE  (runtime/C + thin Sprout wrappers)      │
│   scheduler: spawn / park / wake / yield / ready-queue       │
│   netpoller: register(fd, interest) → wake task on readiness │
│   BACKENDS (selectable):                                     │
│     • single-thread cooperative   ← MVP, zero GC change      │
│     • share-nothing N workers     ← later, modest GC change  │
│     • shared-memory work-stealing ← later, full GC change    │
└──────────────────────────────────────────────────────────────┘
```

### 3.1 The substrate interface (the load-bearing boundary)

Everything in Layer 1 is written against roughly this vocabulary, and *only* this:

```sprout
# ---- proposed stdlib.rt (runtime substrate) ----
type TaskId

fn rt_spawn(work: Unit -> Unit !{IO}) -> TaskId !{IO}   # schedule a new task
fn rt_yield() -> Unit !{IO}                             # cooperative yield point
fn rt_park() -> Unit !{IO}                              # suspend current task
fn rt_wake(t: TaskId) -> Unit !{IO}                     # mark a parked task runnable

# netpoller: park the current task until `fd` is ready, then resume it.
# This is where blocking-style I/O becomes non-blocking under the hood.
fn rt_await_ready(fd: Int, interest: Interest) -> Unit !{IO}
type Interest = | Readable | Writable
```

Design rule: **models never touch the OS or the poller directly** — only `rt_*`.
That is what makes backends swappable. Swap the single-thread scheduler for a
work-stealing one and the `rt_*` contract is unchanged; the models don't move.

### 3.2 What a green-threaded driver read looks like

`tcp_read` today blocks the whole process. Under the substrate it becomes:

```sprout
# stdlib.net, green-threaded version — SAME signature as today, new internals
fn tcp_read(conn: Int) -> Result NetError String !{IO} =
  match nb_read(conn) with                 # non-blocking syscall
  | WouldBlock  -> do { rt_await_ready(conn, Readable); tcp_read(conn) }
  | Done chunk  -> Ok(chunk)
  | Failed e    -> Err(e)
```

The Postgres/Redis drivers call `tcp_read`/`tcp_write` and inherit parking for free.
**No `async` keyword, no colored functions** — the driver looks blocking, behaves
non-blocking. This is the whole ergonomic payoff.

---

## 4. The three Layer-1 models (same task, compared)

All three solve: `GET /user/:id` → fetch user from Postgres **and** prefs from Redis
**concurrently** (independent), fail the request if either fails, render the result;
plus an accept loop that handles many connections at once.

### Shared surface

```sprout
type User  = | User Int String String        # id, name, email
type Prefs = | Prefs String Bool             # theme, email_opt_in

# green-threaded, blocking-STYLE driver surface (all !{IO})
fn pg_query_user(pg: PgConn, id: Int)   -> Result PgError (Maybe User) !{IO}
fn redis_get_prefs(rd: RedisConn, id: Int) -> Result RedisError (Maybe Prefs) !{IO}

type AppError = | DbErr PgError | CacheErr RedisError | NotFound
```

### 4.A Structured concurrency (scope / nursery) — RECOMMENDED default

```sprout
# ---- proposed stdlib.task ----
type Scope                                         # opaque; wrap over runtime handle
type Task a                                        # handle to a spawned computation

# Runs `body`; BLOCKS until body returns AND every child task finishes. If any
# child fails/raises, siblings are cancelled and the error propagates out. Nothing
# leaks. (This is the Trio-nursery / Swift-task-group semantics — see §9.)
fn with_scope(body: Scope -> a !{IO}) -> a !{IO}
fn scope_spawn(scope: Scope, work: Unit -> a !{IO}) -> Task a !{IO}
fn task_await(task: Task a) -> a !{IO}             # `a` is itself a Result here

fn handle_user(pg: PgConn, rd: RedisConn, id: Int)
    -> Result AppError HttpServerResponse !{IO} =
  with_scope(\scope ->
    do
      user_t  <- scope_spawn(scope, \_ -> pg_query_user(pg, id))     # starts now
      prefs_t <- scope_spawn(scope, \_ -> redis_get_prefs(rd, id))   # concurrent
      user    <- task_await(user_t)  |> map_error(DbErr)
      prefs   <- task_await(prefs_t) |> map_error(CacheErr)
      render_user(user, prefs)
  )

# accept loop: one scope for the whole server lifetime
fn serve(listener: Listener, pg: PgConn, rd: RedisConn) -> Unit !{IO} =
  with_scope(\scope -> accept_loop(scope, listener, pg, rd))

fn accept_loop(scope: Scope, listener: Listener, pg: PgConn, rd: RedisConn) -> Unit !{IO} =
  do
    conn <- tcp_accept(listener)                    # parks until a connection arrives
    scope_spawn(scope, \_ -> handle_connection(conn, pg, rd))
    accept_loop(scope, listener, pg, rd)
```

- **Safest by construction:** a `Task` can't outlive the scope that owns it →
  no orphaned tasks, automatic cancellation + error propagation.
- **Best req/resp ergonomics** — the "two queries, join, fail together" shape is native.
- Pairs cleanly with immutability: tasks share only immutable inputs.

### 4.B Goroutines + channels (Go model)

```sprout
# ---- proposed stdlib.chan ----
type Chan a
fn spawn(work: Unit -> Unit !{IO}) -> Unit !{IO}   # fire-and-forget
fn chan_new() -> Chan a !{IO}
fn chan_send(ch: Chan a, value: a) -> Unit !{IO}   # parks if bounded & full
fn chan_recv(ch: Chan a) -> a !{IO}                # parks until a value

fn handle_user(pg: PgConn, rd: RedisConn, id: Int)
    -> Result AppError HttpServerResponse !{IO} =
  do
    users  <- chan_new()   # : Chan (Result AppError (Maybe User))
    prefss <- chan_new()   # : Chan (Result AppError (Maybe Prefs))
    spawn(\_ -> chan_send(users,  pg_query_user(pg, id)   |> map_error(DbErr)))
    spawn(\_ -> chan_send(prefss, redis_get_prefs(rd, id) |> map_error(CacheErr)))
    user  <- chan_recv(users)
    prefs <- chan_recv(prefss)
    render_user(user, prefs)
```

- Maximally flexible/familiar, but **unstructured**: if `chan_recv(users)` returns
  `Err` and do-notation short-circuits, the prefs goroutine is **orphaned**.
  Cancellation is manual (context token). Same goroutine-leak class Go devs know.

### 4.C Actors (Erlang model)

```sprout
# ---- proposed stdlib.actor ----
type Pid msg                                       # typed mailbox handle
fn spawn_actor(init: s, step: s -> msg -> s !{IO}) -> Pid msg !{IO}
fn send(pid: Pid msg, m: msg) -> Unit !{IO}        # async; never blocks
fn self_receive() -> msg !{IO}

# request/response needs an explicit reply address in the message
type DbMsg    = | GetUser  Int (Pid (Result AppError (Maybe User)))
type CacheMsg = | GetPrefs Int (Pid (Result AppError (Maybe Prefs)))

fn db_step(pg: PgConn, msg: DbMsg) -> PgConn !{IO} =
  match msg with
  | GetUser id reply ->
      do { send(reply, pg_query_user(pg, id) |> map_error(DbErr)); pg }

fn handle_user(db: Pid DbMsg, cache: Pid CacheMsg, id: Int)
    -> Result AppError HttpServerResponse !{IO} =
  do
    ubox <- reply_pid()
    pbox <- reply_pid()
    send(db,    GetUser(id, ubox))
    send(cache, GetPrefs(id, pbox))
    user  <- await_reply(ubox)
    prefs <- await_reply(pbox)
    render_user(user, prefs)
```

- **Maps 1:1 onto share-nothing** and is the best fault-tolerance/supervision story
  → the model that most naturally scales to tier-2 multicore.
- But req/resp — the web common case — is **not native**: reply Pids, message ADTs,
  per-driver protocols. Heavy ceremony on the hot path.

### Comparison

| | Structured | Goroutines+chan | Actors |
|---|---|---|---|
| Concurrent 2-query join | native | manual channels | protocol + reply Pids |
| Task leak possible? | **no** (scope) | yes | yes |
| Cancellation / error propagation | **auto, typed** | manual | manual (supervisor) |
| Req/resp ergonomics | **best** | good | worst |
| Maps onto share-nothing multicore | good | good | **best** |
| Familiarity | growing | **highest** | niche |
| Fault-tolerance / supervision | ok | ok | **best** |

All three compile to the **same** Layer-0 substrate. Choosing one for the MVP does
not exclude the others.

---

## 5. Serving the three constraints

### 5.1 "Don't over-commit — PoC/MVP, may switch, or have all three"
The models are **stdlib libraries over a shared substrate**, not baked-in language
semantics. You can:
- Ship `stdlib.task` (structured) as the MVP.
- Add `stdlib.chan` / `stdlib.actor` later with zero changes to the substrate or to
  existing code.
- Keep all three in the tree; programs `import` whichever they want. They interop
  because they share `rt_*` (e.g. an actor can open a scope internally).

Nothing here is a one-way door. The only real commitment is the **substrate
interface** (§3.1), which is small and model-agnostic by design.

### 5.2 "Minimal GC/effects change now"
- **GC: zero change for the MVP.** The single-thread cooperative backend runs exactly
  one mutator, one heap, one collector — identical to today. Green threads are stacks
  (or CPS continuations) on that one OS thread. Stop-the-world stays correct because
  there is still only one thread.
- **Effects: zero change.** Every primitive is an ordinary `!{IO}` function. No new
  effect labels, no enforcement, no `!{Async}`. This is deliberate: v0 effects only
  support `!{IO}` and singleton `!{e}` (spec §4), and are unenforced anyway.
- **Later, effects can add value — optionally.** Once the effect system is real, it
  could gate share/send-safety (a `Send`/`Sync`-style capability, or region/handler
  effects à la OCaml) so the parallel backend is statically safe. But that is a *later*
  layer, never a *prerequisite*. (OCaml 5 proves a scheduler ships fine with untyped
  effects — §9.)

### 5.3 "Future models for other use cases, e.g. a 3D game — swappable backends"
A 3D game is the opposite workload: CPU-bound, latency-critical, wants **real
multi-core parallelism** (a job/task system over a fixed-timestep frame loop, ECS,
possibly SIMD). That is **tier 3** and needs the shared-memory parallel backend +
GC rewrite. The layered design keeps that door open:

- The **substrate interface stays the vocabulary**; a new `jobs` backend implements a
  work-stealing scheduler across OS threads behind the same `rt_spawn`/park/wake shape.
- A new Layer-1 model `stdlib.jobs` (parallel-for, fork-join) targets it.
- Immutability-by-default means most game data (meshes, transforms read per frame) is
  race-free to share; the rewrite's hard part is the collector + how `ref`/MutVec are
  shared, not the whole language.
- **Honest cost:** the parallel backend brings the concurrent-GC work *when its time
  comes*. The architecture doesn't make it free — it makes it **non-blocking for the
  web MVP** and keeps user-facing APIs stable across the transition.

Backend selection is a build/runtime choice (e.g. a compile flag or a runtime init
call), analogous to `SPROUT_NET_MODEL` in the reference interpreter — but reaching
the native path this time.

### 5.4 Forward-compatibility: scaling to multi-core without rewriting the app

**Hard requirement (Kuba):** a user writes a web app on the single-thread backend;
later hits the single-core throughput wall; must scale to multi-core **without
rewriting the app.** This is achievable — but it constrains what we commit to *now*,
because forward-compat cannot be bolted on; it must be designed into the abstraction
from day one.

**Two multi-core paths — and which the web app actually hits.**
- **Tier 2 — share-nothing workers (the web answer).** The framework runs N copies of
  the accept loop (one per core, `SO_REUSEPORT`). Each request + all its structured
  sub-tasks execute entirely on **one** worker/heap. For a stateless request/response
  handler — the dominant web shape, and exactly this use case where shared state lives
  in Postgres/Redis — the user's `handle(req)` is **byte-for-byte unchanged.** This is
  how nginx and Elixir/Phoenix scale. Needs only the modest GC change (thread-local
  globals), not the concurrent-GC rewrite.
- **Tier 3 — shared-memory parallel (rarely needed for web).** Only when a *single
  request* must parallelize CPU across cores, or tasks share *mutable in-process* state
  across cores. Needs concurrent GC. Usually avoidable for web.

**Transparency rests on two invariants we commit to from day one:**

1. **Tasks must not alias *mutable* heap state.** Tasks share *immutable* data freely
   (natural in Sprout — immutability by default); mutable communication goes only through
   the task/channel/shared-state abstractions, which are message/ownership based, never
   aliasing. Consequence: `ref`/MutVec stay **task-local**.
   *Enforcement is NOT free, and this is the subtle part.* The share vector is not a
   special API we can decline to ship — it is **ordinary lexical closure capture**:
   `scope_spawn(scope, \_ -> ... parent_ref ...)` aliases the parent's mutable cell with
   no special primitive at all. Safe under single-thread; the exact forbidden aliasing
   under any multi-core backend. So:
   - **MVP:** invariant 1 is a **documented convention** (lint-able, testable under
     `SPROUT_GC_STRESS`-style tooling), not a type-checked property.
   - **Later:** a capability / `Send`-style effect makes it a **static guarantee** — the
     natural payoff of the effects arc (cf. OCaml 5's explicit non-guarantee, §9). Copying
     capture is the other option but it silently changes mutation semantics, so we prefer
     the checked route. See Q9.
2. **A request + its structured sub-tasks are one scheduling unit** — a backend may place
   it on any single worker/core but never split it across workers. Cross-*request* state
   never uses a raw global mutable binding; it uses the swappable shared-state
   abstraction below.

**The one thing we must design in now: a swappable shared-state abstraction.**
The silent trap is in-process cross-request mutable state (a global counter, an in-memory
session cache, a rate-limiter map). On single-thread it "just works"; under N share-nothing
workers it fragments into N inconsistent copies — the classic rewrite-forcing bug. So the
*only* cross-request state primitive we expose has message/transaction semantics and a
**swappable backend**, mirroring the scheduler-backend story:

Critically, the mutation API must **not** take an arbitrary capturing closure. An
arbitrary `f: Maybe v -> v` cannot survive the share-nothing transition: to be atomic,
`f` must run *where the state lives* (the owner), but a Redis backend cannot execute a
Sprout closure, process workers have no shared address space to send it to, and a
thread-worker owner would have to copy `f`'s captured environment — reintroducing the
cross-heap `ref` aliasing that share-nothing exists to avoid. So mutation is a **fixed,
serializable vocabulary** (what Redis commands and Erlang GenServer calls effectively
are):

```sprout
# ---- proposed stdlib.shared ----
type Shared k v
type Op v = | Set v | Delete | CompareSwap v v      # serializable; extend cautiously
fn shared_new() -> Shared k v !{IO}
fn shared_get(s: Shared k v, key: k) -> Maybe v !{IO}
fn shared_apply(s: Shared k v, key: k, op: Op v) -> Maybe v !{IO}   # ATOMIC on the owner
```

- Single-thread backend: a plain in-heap map (`op` applied with no interleaving).
- Multi-worker backend: an owner-task/actor or an external store (Redis), selected at
  build time. Because `op` is a serializable value (not a closure), it crosses the worker
  boundary and runs on the owner — so **call sites do not change.**
- `CompareSwap old new` gives the user an atomic read-modify-write via a CAS-retry loop;
  richer atomic updates (if ever needed) go through a **top-level named pure function**
  the owner can resolve by name, never an ad-hoc capturing closure. The framework layer
  (§0.6) can sugar the CAS loop.

Because `shared_apply` is atomic *by contract*, it never depends on cooperative
single-thread atomicity, so it stays correct under true parallelism. This closes the
**cooperative-atomicity gotcha**: a non-yielding read-then-write is atomic under
single-thread but *not* under Tier 3 — putting the atomicity inside `shared_apply`
removes the accidental dependency before any user can rely on it.

**Why structured concurrency is the abstraction that survives both transitions.**
The identical `with_scope`/`scope_spawn`/`task_await` code:
- runs sub-tasks cooperatively on one thread (MVP);
- runs each *request* on one of N workers, unchanged (Tier 2);
- runs `scope_spawn`'d children on *different cores* under a parallel backend (Tier 3) —
  safe with no code change precisely because children share only immutable inputs and
  return results by value (invariant 1). Tier 3 becomes an implementation swap, not an
  API change.

**Honest limits.** "No rewrite" holds for code that follows the discipline: share
immutable data across tasks; route cross-request state through `stdlib.shared`. A user who
circumvents it — e.g. a module-level mutable singleton — breaks under workers. The design's
job is to make the disciplined path the *default and only easy* path (immutability-by-default
already does most of this) and to ship **no** global-mutable escape hatch. This is exactly
where enforced effects/capabilities later earn their keep: turning the discipline from
convention into a checked guarantee.

---

## 6. Implementation sketch (MVP, single-thread backend)

Rough sequencing — **for discussion, not committed**:

1. **Non-blocking I/O + netpoller** (runtime/C). Set sockets `O_NONBLOCK`; add an
   epoll (Linux) / kqueue (macOS) poller; expose `rt_await_ready`. *Reference: Go's
   netpoller.* This is the core new runtime capability and is model-independent.
2. **Cooperative scheduler** (runtime/C + `stdlib.rt`). Task representation
   (stackful green threads vs. CPS — an open question, §8), a ready queue, `rt_spawn`,
   `rt_park`, `rt_wake`, `rt_yield`. One OS thread; when the running task parks, pick
   the next ready task; when none are ready, block in the poller.
3. **Green-threaded `stdlib.net`.** Reimplement `tcp_accept`/`tcp_read`/`tcp_write`
   over non-blocking syscalls + `rt_await_ready`. Same signatures as today.
4. **`stdlib.task` (structured concurrency).** `with_scope` / `scope_spawn` /
   `task_await` over `rt_*`, with sibling-cancellation on error.
5. **Concurrent HTTP server.** Rework `serve_loop` to spawn a task per connection
   inside a server-lifetime scope.
6. **Toy Postgres/Redis drivers** (enough to prove the blocking-style ergonomics end
   to end for the anchor use case).

GC untouched throughout. Effects untouched throughout.

---

## 7. Impact summary (Design Change Process checklist)

- **Syntax:** none required for the MVP. Concurrency is library functions + lambdas
  (`\_ -> ...`) that already parse. (A future `spawn`/`select` sugar is optional.)
- **Type system:** none required. `Task a`, `Chan a`, `Pid msg`, `Scope` are ordinary
  parameterized types / `wrap`s. Later, optional send/share-safety typing.
- **Effects:** none required now (all `!{IO}`). Later, optional.
- **Error messages:** new diagnostics only when/if we add typed capabilities.
- **Compat/migration:** additive. `stdlib.net` signatures preserved; sequential
  programs keep working (a program that never spawns runs as one task).
- **Tests:** substrate unit tests (park/wake ordering, poller readiness); per-model
  behavior tests (structured cancellation on error; channel backpressure; actor
  mailbox ordering); an end-to-end concurrent-server test hitting toy drivers.
- **Spec/docs:** MVP is **experimental**; document under a new "Concurrency
  (experimental)" section; do not touch normative spec §9 evaluation semantics until a
  model graduates.

---

## 8. Open questions (to resolve before an approved design)

1. **Task representation — RESOLVED (spike, §8.5):** **stackful green threads**
   (`ucontext`-style). Validated against the real runtime. Hard constraint the spike
   pins: task stacks must be **non-moving** (root slots are addresses into the parked
   stack, read by the collector while suspended) — this rules out copying/segmented/
   growable green stacks unless the rooting scheme changes. CPS/continuation transform
   not needed (would only be revisited if stackful proved intractable — it didn't).
2. **do-notation × cancellation:** when `<-` short-circuits on `Err` inside a scope,
   the desugaring must trigger sibling cancellation *and* run cleanup before
   `with_scope` returns. Pin down exactly how the existing effect/Result-polymorphic
   `do` composes with structured cancellation.
3. **GC rooting of suspended tasks — RESOLVED (spike, §8.5):** per-task temp-root
   stacks, selected by a current-task pointer swapped at every context switch, with
   `sprout_gc_mark_roots` scanning ALL tasks' stacks. ~40 lines, **runtime-only, three
   functions, no codegen change** (generated push/pop already routes through these). The
   persistent-root list (`g_root_nodes`) stays global, untouched.
4. **Backpressure / bounded channels:** default channel semantics (unbounded vs.
   bounded-with-parking) and how they interact with connection pooling to Postgres.
5. **Connection pooling** (Postgres/Redis): pool as an actor? as a channel of
   connections? — the answer nudges which model earns its keep first.
6. **Backend selection mechanism:** compile flag vs. runtime init; and whether a single
   program can mix backends (probably not).
7. **Preemption:** confirmed deferred, but note the yield-point coverage needed so a
   CPU-heavy handler can't starve the loop (at minimum, yield at I/O + allocation
   safepoints).
8. **Shared-state abstraction semantics (§5.4):** exact API and consistency contract of
   `stdlib.shared` — atomic update only, or transactions? per-key ordering guarantees?
   This is load-bearing for the no-rewrite guarantee, so it must be nailed *before* users
   write apps against it, even though its multi-worker backend ships later.
9. **Enforcing task-locality of `ref` (invariant 1):** for the MVP we get it by *not
   shipping* a cross-task mutable-share API. Confirm nothing in the existing `ref`/MutVec
   surface already lets a closure captured by `scope_spawn` alias a parent's mutable cell
   — if it does, that is a forward-compat leak to close now.

---

## 8.5 Spike validation (2026-07-13) — the two hardest unknowns retired

Two throwaway C spikes (ucontext tasks + the *real* runtime/collector, under
`SPROUT_GC_STRESS=1` so every allocation collects) settled the load-bearing risks.

**Spike 1 — GC rooting of suspended tasks.** A scripted non-nested push/yield/pop
interleave (the pattern a single global root-LIFO cannot represent). Controlled, 3 runs:

| Rooting model | Result | Verdict |
|---|---|---|
| Single shared global root stack (baseline) | suspended task's live object collected | **RED** (bug real) |
| Per-task root stacks + mark-all-tasks | object survived | **GREEN** (fix works) |
| Per-task, but victim's ctx hidden from mark (negative control) | object collected again | **RED** (fix is load-bearing, not luck) |

Oracle: forced slot-reuse + value check (the region allocator is opaque to ASan, which
ran as a secondary net). Conclusion: **rooting works; ~40-line, runtime-only, no
collector rewrite** (see §8 Q1/Q3). Confirmed the key structural fact — because
*cooperative* tasks never switch mid-alloc/mid-collect, the temp-root LIFO is the **only**
shared runtime state a switch can corrupt; everything else needed zero change.

**Spike 2 — scheduler + `kqueue` netpoller + real I/O park.** A cooperative scheduler
(ready queue + park/wake) where a task doing blocking-style `read()` hits `EAGAIN`,
registers the fd, and parks; the scheduler blocks in `kevent` and wakes it on readiness.
A GC root held in an outer frame, across a **real park two frames deep**, survived a
200-allocation GC storm driven by a *second* task while the holder was suspended. The
scheduler owns the per-task root switch (one structural call site).

**Bonus finding (reasoned, verify in the real build):** because `mark_roots` scans *all*
tasks' contexts, value-liveness is *decoupled* from per-switch correctness — a mistimed
switch degrades to a **loud pop-accounting/underflow assert**, not a silent
use-after-free. That downgrades the switch-point-alignment risk from "silent corruption"
to "debuggable assert."

**Scope:** these retire the *rooting* and *netpoller-integration* unknowns only. Not
proven: many-task fairness, nested scopes, the `stdlib.task` API, integration with
*generated* Sprout code (spikes drove push/pop from C), and anything multi-core. Those
are the real implementation. Spikes are throwaway (kept in a scratchpad, not the tree).

---

## 9. Prior-art survey (verified 2026-07-13)

| System | Memory model | Ergonomics / coloring | Scheduler mechanism | Lesson for Sprout |
|---|---|---|---|---|
| **Go** | shared heap, concurrent GC | no coloring (goroutines look blocking) | M:N, work-stealing; netpoller uses epoll/kqueue/IOCP; blocked goroutine → `_Gwaiting`, OS thread runs others | The ergonomics target. Netpoller is the reference for Layer-0 §6.1. |
| **Erlang/BEAM** | **share-nothing**, per-process heaps, copy-on-send | actors only | preemptive (reduction counting) | The memory model that sidesteps concurrent GC and scales to cores. |
| **Node/libuv** | single-thread JS | `async`/`await` **coloring** | single event loop + libuv thread pool for some ops | Proves one core suffices for I/O-bound; also the cautionary coloring tax. |
| **OCaml 5 / Eio** | domains for parallelism | **untyped** effect handlers, direct style | user-level scheduler *implemented in the language itself* via effect handlers | You can ship a scheduler with **untyped** effects; type-safety of shared data is *not* checked (explicitly). Validates "don't gate on typed effects." |
| **Python Trio** | single-thread async | nursery-scoped structured concurrency | event loop | Nursery = the Model-A semantics: child exception cancels siblings, waits for cleanup, re-raises. |
| **Kotlin / Swift** | threads/tasks | structured concurrency (CoroutineScope / task groups, `async let`) | runtime executors | Mainstream precedent that structured concurrency is a good *default* surface. |

Design consensus across these: (a) no-coloring green threads (Go/Erlang/OCaml-Eio)
beat colored async for pervasive-I/O code; (b) structured concurrency is the modern
default safety surface; (c) share-nothing is the pragmatic route to multicore without
a concurrent-GC rewrite; (d) a real effect *type* system is **not** a prerequisite for
a working scheduler.

### Sources
- Go scheduler / netpoller (M:N, epoll/kqueue, work-stealing):
  https://internals-for-interns.com/posts/go-netpoller/ ,
  https://rickyboyd.dev/goscheduler/
- OCaml 5 effects untyped; Eio effects-based scheduler:
  https://ocaml.org/manual/5.5/effects.html ,
  https://github.com/ocaml-multicore/eio ,
  https://tarides.com/blog/2024-03-20-eio-1-0-release-introducing-a-new-effects-based-i-o-library-for-ocaml/
- Trio structured concurrency / nursery cancellation:
  https://vorpus.org/blog/notes-on-structured-concurrency-or-go-statement-considered-harmful/ ,
  https://trio.readthedocs.io/en/stable/reference-core.html

*Verification note (per AGENTS.md Design Change Process §3):* Go/Trio/OCaml claims
above are confirmed via the cited sources. Erlang per-process-heap and Kotlin/Swift
structured-concurrency rows are widely-established but should be re-confirmed against
the BEAM internals docs and the Kotlin/Swift language references before this becomes an
approved design change.

---

## 10. Recommendation & decision needed

**Recommendation:** build Layer 0 (single-thread cooperative backend + netpoller) and
ship **structured concurrency** (`stdlib.task`) as the one MVP model. It scores highest
on the ranked principles (§0.5): simplest to reason about, predictable tree-shaped
control flow, call-stack-like failure semantics, and — with cooperative single-thread
scheduling — **deterministic replay in tests**. It costs zero GC/effect change now,
delivers end-user ergonomics at the framework layer (§0.6) rather than distorting the
primitives, and leaves channels, actors, and a future parallel game backend fully open
as additive work. Performance (single-core for now) is deliberately deferred per
principle 5.

**Status update (2026-07-13):** the two hardest technical unknowns behind this
recommendation — GC rooting of suspended tasks, and scheduler/netpoller integration —
are now **empirically retired by spikes (§8.5)**. The green-threaded, cooperative,
single-thread substrate is validated as viable on the *current* runtime and GC, with a
small (~40-line, runtime-only) rooting change and no collector or codegen rewrite. The
exploration phase is complete; what remains is the real, test-first implementation.

**Decision needed from Kuba:** confirm (a) the layered substrate-plus-models
architecture, and (b) structured concurrency as the first model — or redirect. Once
confirmed, next step is a focused approved-design doc for Layer 0 + `stdlib.task`,
resolving the §8 open questions (esp. task representation and GC rooting of suspended
tasks) with the user before any code.
