# Green-task pooling — design v0

Status: **design A prototyped and measured (§5.3); not landed.** Measurements in §1, the
prototype results in §5.3, and the linear-types findings in §7 are reproducible today. Design B
(§6) is analysis only.

Companion to [linear-borrowing-v0.md](./linear-borrowing-v0.md),
[concurrency-channels-design-2026-07-16.md](./concurrency-channels-design-2026-07-16.md), and
[compiler-internals.md](./compiler-internals.md) (GC ABI invariants).

## 1. Problem statement

`examples/http_web_server.sprout` serves ~8,300 req/s on `/health` while spending **~4 µs per
request in the handler**. 97% of per-request CPU is green-task setup and teardown.

A `sample` profile under `wrk -t2 -c40` (6,384 main-thread samples) puts two symbols on top, and
neither is the handler or the collector:

```
madvise   2779   pump_loop → sprout_roots_free → free_medium → madvise
__bzero   2009   serve_forever_loop → task_spawn → task_create → makecontext → __bzero
```

That is **75% of CPU**, caused by per-task fixed sizing in `runtime/sprout_scheduler.c`:

| allocation | size | constant |
|---|---|---|
| green stack | 1 MiB | `SPROUT_TASK_STACK_BYTES` |
| GC root pool | 512 KiB (16384 × 32-byte `RootNode`) | `SPROUT_TASK_ROOT_SLOTS` |

Every accepted connection spawns one fire-and-forget task, so every request `malloc`s ~1.5 MiB
and `free`s it. Both land in macOS malloc's *medium* class, which returns pages to the kernel via
`madvise` on `free`, so the next request re-faults them. Separately,
`getcontext`/`makecontext` **zeroes the whole stack** — measured linear in `ss_size`:

| `ss_size` | ns per `makecontext` (buffer reused, cache-hot) |
|---|---|
| 4 KiB | 415 |
| 64 KiB | 947 |
| 256 KiB | 1,912 |
| **1 MiB** | **5,993** |
| 4 MiB | 25,086 |

6 µs is a *floor*: 1 MiB in 6 µs is ~175 GB/s, faster than DRAM, so that microbenchmark measures
L2 on one reused buffer. The real server rotates ~40 × 1.5 MiB and pays cold faults, which is why
the profile attributes ~44 µs/request.

### 1.1 Causal confirmation

Shrinking the two constants, interleaved A/B, `wrk -t2 -c40 -d4s`, with a **TIME_WAIT drain
barrier before every run** (without it the port table pollutes later runs and depressed the
baseline from ~8,300 to ~4,000 — the barrier is load-bearing for any measurement here):

| variant | req/s (median) | vs baseline |
|---|---|---|
| baseline (1 MiB stack, 16384 root slots) | 8,344 | — |
| 64 KiB stack only | 23,589 | 3.0× |
| 512 root slots only | 10,111 | 1.28× |
| both small | 32,951 | **3.9×** |

8/8 pairings favoured the small variant. The split is non-additive, consistent with allocator
size-class effects rather than a clean two-way attribution; the honest reading is that **both
allocations are on the per-request path and the stack is the larger contributor.** ~3.9× is the
headroom any design here is competing for.

Shrinking the constants is not the proposal — it cuts maximum recursion depth per task.

### 1.2 Scope

Entirely **outside the GC heap**: stacks and root pools are plain `malloc`, untouched by the
region arena. Invisible to compiler benchmarks (the self-hosted compiler spawns no green tasks).
Affects `task_spawn`/`task_fork` workloads only. Not a regression from any recent change — true
since L0 concurrency landed.

## 2. Goals and non-goals

**Goals**

1. Remove per-request `malloc`/`free` of task stacks and root pools.
2. Remove per-request `getcontext`/`makecontext`, i.e. the 1 MiB zeroing.
3. Keep 1 MiB stacks and 16384 root slots — no reduction in per-task recursion headroom.
4. Keep resource safety **in the type system** wherever the type system can express it (§7).
5. Be observable: counters good enough for a `just` gate, per the arena's
   `arena_regions`/`overflow_regions` lesson.

**Non-goals**

1. Growable or moving stacks — architecturally unavailable (§3.1).
2. Multicore work stealing; tier-2 share-nothing is a separate direction.
3. Changing `SPROUT_TASK_STACK_BYTES` / `SPROUT_TASK_ROOT_SLOTS` defaults.

## 3. Prior-art survey

Every row verified against a primary source; quotes are from the linked page.

| Runtime | Initial per-task stack | Growth | Pooling |
|---|---|---|---|
| **Go** | `stackMin = 2048` (2 KiB) | `copystack` — multiplicative | **Yes**, per-order free lists + global pool |
| **Erlang/BEAM** | 233 words heap+stack (327 words total, 2,616 bytes on 64-bit) | per-process GC grows the heap | n/a — the unit is too small to be worth pooling |
| **Java virtual threads** | not specified in the cited doc | not specified | **explicitly discouraged at the task level** |
| **Sprout today** | **1 MiB fixed** | none | none |

- **Go** ([`runtime/stack.go`](https://raw.githubusercontent.com/golang/go/master/src/runtime/stack.go)):
  "Global pool of spans that have free stacks. Stacks are assigned an order according to size…
  There is a free list for each order", and "stackcacherefill/stackcacherelease implement a
  global pool of stack segments. The pool is required to prevent unlimited growth of per-thread
  caches." Growth: "Allocate a bigger segment and move the stack. Stack growth is multiplicative,
  for constant amortized cost", via `copystack`, which "adjusts all pointers to reference the new
  location". Shrinks only when "gp is using less than a quarter of its current stack".
- **Erlang** ([Efficiency Guide, Processes](https://www.erlang.org/doc/system/eff_guide_processes.html)):
  "An Erlang process is lightweight compared to threads and processes in operating systems." A
  spawned process measures 327 words, of which 233 are "the heap area (including the stack)". The
  default is "quite conservative to support Erlang systems with hundreds of thousands or even
  millions of processes".
- **Java virtual threads** ([Oracle JDK 21 core docs](https://docs.oracle.com/en/java/javase/21/core/virtual-threads.html)):
  "virtual threads aren't scarce and therefore should never be pooled!" and "each should represent
  not some shared, pooled, resource but a task."
- **`sync.Pool`** ([pkg.go.dev](https://pkg.go.dev/sync#Pool)), prior art for object-pool *policy*:
  its "purpose is to cache allocated but unused items for later reuse, relieving pressure on the
  garbage collector"; "Any item stored in the Pool may be removed automatically at any time
  without notification"; and "a free list maintained as part of a short-lived object is not a
  suitable use for a Pool… It is more efficient to have such objects implement their own free
  list."

### 3.1 The decisive consequence: task cost dictates concurrency model

**The consensus is to make the unit cheap, not to pool expensive units.** Go starts at 2 KiB;
Erlang at 233 words. Sprout's 1 MiB fixed stack is **512× Go's initial stack** — that, not the
absence of a pool, is the outlier.

**Sprout cannot follow them.** Go can move stacks only because `copystack` "adjusts all pointers
to reference the new location". Sprout cannot: GC roots point *into* the green stack by address,
`ir_rooting` pushes an `i64` into an alloca and never reloads it, and `force_drop_task` states the
same invariant from the other side — "a parked task holds live values rooted INTO its green stack
(L0.3 park contract)". Non-moving rooting is load-bearing; a stack that moved would invalidate
every root pointing into it.

That has a consequence beyond stack management. **`goroutine-per-connection` is a model that cheap
tasks license.** Go can spawn one 2 KiB goroutine per connection and let concurrency be unbounded;
`stdlib/http_server.sprout` copies that shape while paying 1.5 MiB per connection, which makes
today's unbounded `serve` a memory-DoS: 1,000 concurrent connections is ~1.5 GB of stacks, driven
by the client. Sprout has two coherent choices — make tasks cheap (impossible, above) or **stop
pretending they are cheap** and bound concurrency, as languages with expensive threads do.

This reframes the problem. It is not only "task creation is slow"; it is "the server's
concurrency model assumes a task cost Sprout does not have."

## 4. Two designs

| | A — Sprout-level worker pool | B — runtime task pool |
|---|---|---|
| Where | `stdlib/http_server.sprout` | `runtime/sprout_scheduler.c` |
| Mechanism | N long-lived worker tasks pull owned connections off a `Chan TcpConnection` | `task_trampoline` loops; pump returns finished workers to a free list |
| Resource safety | **in the type system** — linearity governs the handoff (§7) | hand-written C invariants (§6) |
| Runtime change | none | substantial, 17 hazards (§6) |
| Concurrency | **bounded** by worker count (semantic change) | unbounded, preserved |
| Applies to | `http_server` and code written this way | every `task_spawn` workload |

**Recommendation: A, with B kept as a later, independent option.** A is smaller, needs no runtime
change, keeps the safety argument inside the checked layer, and additionally fixes the unbounded-
memory exposure identified in §3.1. Its cost is a real semantic change that needs your call (§5.2).

B is not wasted analysis: it is the only option that helps `task_spawn` workloads that are *not*
written as a worker pool, and §6 stands as the design if we ever want it.

## 5. Design A — Sprout-level worker pool (recommended)

N long-lived worker tasks are spawned once at server start. Each loops: receive an owned
connection from a channel, serve it, repeat. The accept loop owns the listener and moves each
accepted connection into the channel.

Because a worker never finishes, **`makecontext` runs once per worker rather than once per
request, and nothing is ever freed** — both §1 costs go to zero, with no scheduler change.

Verified to typecheck in pure Sprout today (§7.3), with real `TcpConnection`s:

```sprout
fn serve_one(ch: Chan TcpConnection) -> Int !{IO} =
  match chan_recv(ch) with
  | Got conn ->
      do
        close(conn)      # consumes the connection: obligation discharged on this arm
        1
  | Closed -> 0

fn worker_loop(ch: Chan TcpConnection, budget: Int, acc: Int) -> Int !{IO} = …   # loops

fn accept_loop(ch: Chan TcpConnection, listener: consuming TcpListener, n: Int) -> Unit !{IO} =
  …
  do
    conn <- accept(listener)
    chan_send(ch, conn)   # MOVES ownership into the channel
    accept_loop(ch, listener, n - 1)
```

### 5.1 Why this is the answer to "are we circumventing linearity?"

It is the opposite of circumvention: the connection handoff is expressed *through* linear types
and checked. §7 verifies that `chan_send` consumes, that a received connection must be consumed,
and that dropping or double-sending one is rejected. Design B, by contrast, moves lifetime
correctness into C invariants the checker cannot see (§6, G5/G10).

### 5.2 The cost: bounded concurrency — needs a decision

N workers means at most N concurrent in-flight requests. `stdlib/http_server.sprout:474`
currently promises the opposite: "a slow connection (one dribbling its request) cannot block
others". With N workers and N slow connections, connection N+1 waits.

Three sub-options, in preference order:

1. **Bounded workers + bounded channel = backpressure.** `chan_send` parks the accept loop when
   the buffer is full, so the server stops accepting instead of allocating without limit. This is
   a *safety improvement* over today (§3.1) but changes the documented guarantee, and a slow
   handler can head-of-line-block. Requires choosing defaults for worker count and buffer depth.
2. **Hybrid: workers for the common case, spawn on exhaustion.** Preserves unbounded concurrency
   exactly. Needs "all workers busy" to be observable from Sprout, which today it is not — would
   need a counter in `stdlib`, and the fallback path keeps the full 1.5 MiB cost for overflow
   connections.
3. **Keep `serve` unbounded and add `serve_pooled` alongside it.** No semantic change to existing
   code; the caller opts in. Weakest default — the memory exposure stays the out-of-the-box
   behaviour.

**Recommend 1, defaults chosen by measurement, with 3's naming so the change is opt-in for one
release.** This is the one genuine decision in this document.

### 5.3 Prototype results

Two minimal servers, **byte-identical request handling** (`read_avail` → fixed 200 response →
`close`), differing only in the concurrency model. Sources:
`spawn_server.sprout` (mirrors `serve_forever_loop`) and `pool_server.sprout`. Same drain-barrier
protocol as §1.1, `wrk -t2 -c40`.

| | task-per-connection | worker pool (w=8) |
|---|---|---|
| throughput | 5,394 req/s | **25,597 req/s** — 4.7× |
| RSS during sustained load (10 samples) | 130–237 MB | **4 MB** — ~40× less |
| p50 latency | 2.23 ms | **320 µs** |
| p90 latency | ~5 ms | **~600 µs** |
| p99 latency | ~14 ms | 18–108 ms (see below) |

Across sessions the throughput ratio ranged **3.7×–5.4×**, bracketing §1.1's 3.9× prediction. The
model holds: removing per-request `malloc`/`free` and per-request `makecontext` is worth what the
constant-shrinking experiment said it was worth.

**Worker count barely matters.** w=2, 8, 64 and 256 all land at 23k–32k req/s. **Two workers reach
full throughput**, because each request is ~4 µs of work — the server is accept-bound, not
worker-bound. This substantially lowers the cost of §5.2's bounded-concurrency decision: the bound
that buys the win is small.

**RSS went the opposite way from this document's own prediction** (§6.1 G11 predicted pooling would
*raise* steady-state RSS). Measured, it is ~40× *lower*, and the mechanism explains why:
`makecontext` zeroes the entire 1 MiB stack, so **every page of every task stack becomes resident**.
A task-per-connection server therefore pays full residency for every task, while a pooled worker
touches only the few KB its handler actually uses. The stack zeroing is not just the CPU cost — it
is the memory cost too. G11's concern survives only for design B's *grow-on-demand* pool; design A's
fixed worker count bounds memory by construction.

**The p99 tail is a benchmark-client artifact, not a server property.** It appears at *every* worker
count including w=2, and varies 18–120 ms run to run. Two hypotheses were tested and **both
rejected**:

- *Not GC.* `SPROUT_DEBUG_GC` shows max pause 6.9 ms, 21 ms total across 6 s — an order of
  magnitude short of the tail.
- *Not the listen backlog.* `runtime/sprout_runtime.c:8341` hardcodes `listen(fd, 16)`, which looked
  like an obvious culprit at ~30k accepts/s. Raising it to `SOMAXCONN` (128 here) changed p99 **not
  at all** (74–120 ms before and after, 3 interleaved rounds). Hypothesis discarded — recorded
  because it is a plausible-looking diagnosis that measurement killed, and the backlog is not the
  thing to go fix.

The actual cause is **client-side ephemeral-port exhaustion**. `Connection: close` means one TCP
connection per request; this machine has **16,384 ephemeral ports** (`net.inet.ip.portrange`
49152–65535), and a 4 s run at ~32k req/s opens **131,712** connections — eight times the port
range — so the client stalls waiting for `TIME_WAIT` to recycle. Isolated by varying only the
connection volume against a drained port table:

| run | connections | p50 | p99 |
|---|---|---|---|
| `-t1 -c2 -d1s`, drained | 25,035 | **35 µs** | **243 µs** |
| `-t2 -c40 -d4s`, table loaded | 131,712 | 536 µs | 66 ms |

With the client not port-starved there is **no tail at all**, and the pooled server's true p50 is
**35 µs**. So the §5.3 table's p99 column measures `wrk` plus the kernel, not Sprout — and the
protocol constraint is now documented in `bench/http_worker_pool/bench.sh`: **p99 is only meaningful
for runs that stay inside the ephemeral-port range.** The task-per-connection server never shows the
tail simply because task creation throttles it to ~7k req/s, so it never opens enough connections.

### 5.4 Gotchas for design A

- **A1 — the over-strict do-bind edge is in the way, and must be routed around.**
  `ch <- chan_new(s, cap)` in an `!{IO}` block is rejected for a container-of-linear payload
  (§7.1). Threading the channel as a parameter works today and is what the prototype does. The
  clean fix is `BACKLOG:1059`'s known over-strict edge; until then this is a documented shape
  constraint, and the stdlib code needs a comment saying *why* it is written that way, or someone
  will "simplify" it back into a bind.
- **A2 — worker count is now a capacity decision**, exposed in the public API and needing a
  documented default and tuning guidance.
- **A3 — a panicking/failing handler must not kill a worker permanently.** Today a task dies per
  connection, so a handler failure is contained. A worker that exits its loop silently reduces
  pool capacity until the server has zero workers and hangs. The loop must be failure-tolerant,
  and a worker exit must be loud.
- **A4 — cross-request stack residue.** A worker's stack retains the previous request's bytes.
  Same exposure as B's G10, but confined to one long-lived task rather than a shared pool. Only
  reachable via a bug reading uninitialised stack; worth stating, not worth zeroing.
- **A5 — `chan_recv` returning `Closed` must terminate the worker cleanly**, discharging no
  connection obligation (the `Closed` arm binds nothing) — already the shape in the prototype.
- **A6 — fairness.** All workers wait on one channel; delivery order is the scheduler's. Fine for
  a server, but it means no per-connection priority, and a `chan_select` variant would be needed
  if that is ever wanted.

## 6. Design B — runtime task pool (fallback)

`task_trampoline` becomes a loop; `pump_loop`'s reclaim branch returns the worker to a pool;
`task_create` pops an idle worker and resets its ~26 mutable fields, `malloc`ing only when the
pool is empty. **Grow on demand, never block**, so unbounded concurrency is preserved.

`ucontext_t ctx` is `Task`'s *first* field, so a pooled `Task` keeps `&t->ctx` at a stable
address for free — which is what makes "build the context once" sound (§8.1).

**Two pool tiers.** A force-dropped task (`scope_cancel`, `with_timeout`) is suspended *mid-job*
and can never reach the top of the loop, so it cannot become an idle worker. Its buffers are still
fine: an **idle-worker list** (reuse = field reset) and a **raw-buffer list** (reuse needs a fresh
`makecontext`). Cancellation is rare, so tier 2 is a correctness requirement, not a fast path.

### 6.1 Gotchas for design B

**G1 — `Task*` *is* the handle (ABA).** `task_of(h)` is `(Task*)(intptr_t)h`; `__task_fork`
returns the raw pointer. *Containment:* pool only `awaitable == 0`. `__scope_spawn` returns `0`
and never exposes the pointer, so fire-and-forget tasks have no handles — and they are 100% of the
server's. Extending to `task_fork` needs a generation counter in the handle.

**G2 — `roots == NULL` is the "cancelled" sentinel.** `__task_await` loud-fails on it, and
scope-close's double-free guard tests it. A pooled task never has `roots == NULL`, disarming both.
*Containment:* G1. If fork pooling is added, replace the sentinel with an explicit state **first**.

**G3 — a force-dropped worker cannot re-enter its loop.** Getting this wrong resumes a context
whose frame is mid-`read_request`.

**G4 — the two permanent roots.** `task_create` pushes `&t->work` and `&t->chan_pending`. A reset
to `pool_top == 0` would unroot the work closure and the delivery slot. Correct reset:
`pool_top = 0; head = NULL;` then re-push both.

**G5 — `t->work` must be zeroed while idle.** `&t->work` stays rooted for the worker's life, so a
stale handle pins the last request's closure and everything it captured, forever. A slow leak
visible only under load. *This is a bug class linear types would catch if the pool were in Sprout —
see §5.1.*

**G6 — idle workers must be invisible to scope live counts.** `with_scope` returns on
`s->live == 0`; `t->scope = NULL` while idle, and `s->live--` + joiner wake must happen *before*
parking idle.

**G7 — the deadlock detector.** `pump_loop` fails with "deadlock — tasks parked with no way to
make progress". Idle workers need their own list with `in_rq = 0`, `on_io_list = 0`,
`park_kind = PARK_NONE`, so they neither trigger a false deadlock nor mask a real one.

**G8 — `done` vs idle are different states.** The pump reclaims on `t->done`; a looping worker must
never set it at job end.

**G9 — root-pool imbalance becomes cumulative.** Today an imbalance dies with the task; on a
reused worker it accumulates to `"GC root pool exhausted"` thousands of requests later. Assert
`pool_top == 2` at job end, then reset — turning pooling into a codegen-leak detector we lack.

**G10 — stack hygiene.** A reused stack holds the previous job's bytes; on a *shared pool* the
blast radius of a stack-reading bug is one request seeing another's. Recommend zeroing a bounded
prefix. *Also a bug class the type system cannot see here.*

**G11 — peak concurrency becomes steady-state RSS.** Applies to design B's *grow-on-demand* pool
only: a one-off spike to 10,000 connections would pin those buffers for the process's life, so a
trim policy must ship *with* the pool — `sync.Pool` exists in that shape for exactly this reason.

**Correction:** this hazard was originally written as "the main regression risk" for pooling in
general, on the assumption that pooling raises steady-state RSS. §5.3 measured the opposite — a
fixed pool uses ~40× *less* memory, because `makecontext`'s stack zeroing makes every page of every
per-request stack resident. The prediction was wrong about the baseline, not about the trim policy:
an *unbounded* pool still needs one, but pooling is a large memory **win**, not a cost.

**G12 — multicore.** No locks needed today (one OS thread, cooperative); tier-2 needs the pool
per-scheduler-thread. Keep it per-scheduler-shaped so that is a field move, not a rewrite.

**G13 — GC interaction is neutral-to-positive.** Stacks are `malloc`, outside the GC heap and
arena; skipping `sprout_roots_free` means fewer registry mutations. Idle workers contribute two
roots holding `0`.

**G14 — task-0 stays excluded** (`stack == NULL`, `sprout_roots_main()`, already special-cased).

**G15 — `awaitable` pushes a third root** (`&t->result`); must join the reset if fork pooling is
added.

**G16 — observability is a requirement.** Counters for created / reused / idle / high-water on the
debug line. Without them, a pool that never hits is indistinguishable from success.

**G17 — grow on demand.** A fixed-size pool would silently break
`stdlib/http_server.sprout:474`'s guarantee.

## 7. Linear types: what is actually true

This section replaces an earlier, **incorrect** conclusion in this document that a Sprout-level
pool was blocked by linearity propagating from a type argument into its container. It is not.
Every claim below was checked against the compiler.

### 7.1 The real constraint is a known over-strict edge, not propagation

`Chan Res` (with `type linear Res`) *appears* linear:

```
ERROR: check: linear value 'ch' is used more than once
```

but the cause is the **effectful do-bind fallback**, not propagation. `BACKLOG:1059` already
records it: `do_bind_type` takes the RHS's last type argument as the binder type, so
`ch <- chan_new(…) : Chan Res !{IO}` types `ch` as `Res`. The entry names this exact case as the
"*Remaining over-strict edge (unchanged):* an effect bind of a non-linear container of a linear
(`x <- getBox()`, `Box File` non-linear) is still conservatively rejected via the payload
fallback."

Evidence it is not propagation:

- `List Res` used twice: **accepted** — no propagation through builtin containers.
- `borrowing Holder Res` (with `Holder a = | Holder Int`): rejected with
  "`borrowing`/`consuming` is only allowed on a parameter of a linear type" — so `Holder Res` is
  **not** linear, i.e. a user ADT's linearity does not come from its type argument either.
- `Chan Res` used twice **as a parameter** rather than a do-bind: **accepted**.

### 7.2 Ownership transfer through a channel is sound today

| probe | result |
|---|---|
| send a linear value, then use it again | **rejected** — "used more than once" |
| send the same linear value twice | **rejected** — "used more than once" |
| `Got r ->` and drop `r` without consuming | **rejected** — "is never used" |
| full round trip: send 3, worker loop receives and consumes 3 | **compiles and runs** (prints 42) |

So `chan_send` transfers ownership and the receiver inherits the exactly-once obligation. This is
the guarantee design A rests on, and it already works.

### 7.3 A worker-pool server typechecks in pure Sprout

The §5 shape — `Chan TcpConnection`, a long-lived `worker_loop`, an `accept_loop` that owns the
listener and moves each connection into the channel — typechecks against real `stdlib.net` types.
No language change, no runtime change.

### 7.4 What *is* still missing (and it is a real hole)

Containers launder a **dropped** linear value. A bare drop is caught; every container hides it:

| shape | dropped linear value |
|---|---|
| `let r = Res(1) in 7` | **caught** |
| `[Res(1)]` | silently dropped |
| `(Res(1), 2)` | silently dropped |
| `Box(Res(1))` (user ADT) | silently dropped |
| `Just(Res(1))` | silently dropped |

Consequence for pooling: the *contents* of a pool are protected (each acquired resource must be
consumed exactly once — §7.2), but **dropping the pool itself with resources inside is not
caught.** That is a documented limit, not a blocker — and it is materially weaker than the
"cannot be built" claim this section previously made.

Relation to known work: `BACKLOG:1066` added containment checking for discarded *do-steps*; these
are `let..in` binders, where no obligation attaches because `List Res` is not itself linear.
Adjacent to that fix and to the open Position A/B question at `BACKLOG:1100` (constructor-field
discard). The probes above are ready-made fixtures.

### 7.5 A generic `Pool a` — expressible, on the same footing as `Chan a`

Given §7.2, a generic pool is not blocked. The natural shape, mirroring `Chan`:

```sprout
pool_acquire(p: Pool a) -> a !{IO}                    # transfers ownership out
pool_release(p: Pool a, r: consuming a) -> Unit !{IO} # transfers it back
pool_with(p: Pool a, body: once a -> b !{IO}) -> b !{IO}   # scoped lease (preferred)
```

`pool_with` is preferred because `once` makes release the pool's obligation rather than the
caller's — the same reason `with_scope` owns the join. The guarantee: every acquired resource is
consumed exactly once. The gap: §7.4, dropping the pool itself. **Recommend deferring
implementation** until a real consumer exists (DB connections), but the design is unblocked, which
is the opposite of what §7 previously said.

## 8. Rejected alternatives

### 8.1 O(1) context reset via a pristine snapshot

Keep per-request `makecontext` but make it cheap: build once, snapshot, restore per task. Measured
**6,925 → ~700 ns/task, ~9×, correct on 20,000/20,000** — but unnecessary under both A and B,
since neither re-primes a context per request. Recorded because the mechanism it exposed is a trap:

- Copying the snapshot into a **different** `ucontext_t` fails `EINVAL` after exactly one run.
  `makecontext` plants a frame in the *stack* referencing the `ucontext_t` **by address**; on
  completion the trampoline follows that stack-resident pointer and zeroes `uc_mcsize` on the
  **original**. Measured: source `uc_mcsize` 816 → 0 while the copy kept 816.
- Restoring into the *same* address works — hence `ctx` being `Task`'s first field matters.

Therefore: **a `Task` must never be relocated.** Already true (non-moving `malloc`), now
load-bearing for a second reason.

### 8.2 Shrinking the constants

3.0–3.9× for a two-line change (§1.1), but it trades maximum recursion depth per task. Worth
keeping as an **env knob** (`SPROUT_TASK_STACK_BYTES` is a compile-time `#define` today) for
memory-constrained deployments — independent of A and B, not a substitute.

## 9. Syntax, type-system, and error-message impact

- **Syntax:** none in either design.
- **Semantics:** design A changes `serve`'s concurrency from unbounded to bounded — the §5.2
  decision, and the only observable change in this document. Design B changes nothing observable.
- **Type system:** no change. §7 is a set of findings about the existing system, not a proposal to
  extend it.
- **Errors:** design A needs a loud diagnostic when a worker exits its loop (A3). Design B adds
  two internal loud-fails (G9's imbalance assert, G3's invariant violation).
- **New env knobs:** design B needs a pool cap / trim watermark (G11) and optional prefix zeroing
  (G10); §8.2 optionally adds `SPROUT_TASK_STACK_BYTES`. Each documented in `docs/development.md`.

## 10. Compatibility and migration

Design A: a semantic change to `serve` (§5.2). Recommend landing as `serve_pooled` first so
existing code is unaffected for one release.

Design B: no source-level migration. `runtime/APPROVED_BUILTINS` untouched — no new
`long long <name>(…)` entry point. A `runtime/` edit does not change emitted IR, so
`bootstrap/compile_driver.ll` is unaffected; the DoD #11 example canary applies.

## 11. Tests

TDD order, failing test first, per Definition of Ready.

**Design A**

1. **Throughput:** the §1.1 A/B protocol (drain barrier included), pooled vs current `serve`.
   Target ≈3.9× without shrinking constants.
2. **Ownership safety, as conformance fixtures** — the four §7.2 probes promoted into
   `tests/conformance/type_error/`: send-then-use, send-twice, recv-and-drop (all must fail), plus
   the round trip as a positive.
3. **Bounded-concurrency behaviour:** with all workers occupied, an additional connection is
   queued and eventually served, not dropped (§5.2 sub-option 1).
4. **Worker survival:** a handler that fails does not permanently reduce pool capacity (A3).
5. **Clean shutdown:** channel close terminates every worker with no leaked connection (A5).

**Design B** (if ever taken)

6. **Reuse actually happens:** `workers_created ≪ tasks_run`, as a `just` gate mirroring
   `gc-arena-check`. Without it a never-hitting pool looks like success.
7. Unbounded concurrency preserved (G17); root-pool balance (G9); cancellation over pooled workers
   (G3); `with_scope` returns with idle workers outstanding (G6) and no false deadlock (G7); no
   stale retention as task count grows (G5); RSS returns to baseline after a spike drains (G11).

**Both:** `just test`, `just test-stress`, `just ci-fast-gates`,
`just compile-examples-stage1`, the DoD #11 canary, and `SPROUT_GC_STRESS=1
SPROUT_GC_HDRCHECK=1` green.

## 12. Status

**Experimental.** L0 concurrency is not part of normative v0, so neither design changes normative
text. §7.4's hole *does* touch `docs/spec-v0.md` §5.8's enforcement bullets, which state the
limits of discard checking — that wording needs review against the §7.4 table independently of
this design.

## 13. Follow-ups discovered

To be filed in `BACKLOG.md`:

1. **Linear values launder through containers** (§7.4). A `let..in` binder of a non-linear
   container holding a linear value drops it silently. Adjacent to `BACKLOG:1066`, related to the
   open Position A/B call at `BACKLOG:1100`. Probes ready as fixtures. **Highest-value item here** —
   it is a soundness gap, not a performance question.
2. **The over-strict do-bind edge** (§7.1, `BACKLOG:1059`) now has a concrete motivating consumer:
   it forces the worker-pool channel to be threaded as a parameter. Worth raising in priority.
3. **`serve` is an unbounded-memory exposure** (§3.1): ~1.5 MiB per concurrent connection, client-
   driven. Independent of which design lands, and arguably the most user-visible issue in this
   document.
4. **`chan_select`'s per-call `malloc`** — `malloc(n * sizeof(Chan*))` on every call, plus
   `malloc(n * sizeof(SelectWaiter))` on the parking path. A select loop pays the first every
   iteration; unmeasured.
5. **`SPROUT_TASK_STACK_BYTES` as an env knob** (§8.2) — 3× on the HTTP server for a memory
   tradeoff the deployment should own.
6. **The 1 MiB default is 512× Go's initial stack** (§3.1) and was never measured against real
   handler depth. Measure what depth handlers actually use.
7. **A generic `Pool a`** (§7.5) — unblocked; defer until a real consumer (DB connections) exists.
8. ~~**`connect()` is blocking, and that stalls the whole scheduler.**~~ **FIXED 2026-08-10.**
   `tcp_connect` went non-blocking before `connect()` and now parks on write-readiness, reading the
   outcome from `SO_ERROR`; regression in `tests/task_io_smoke/connect_park.spr`. The bug was found
   while trying to write a backlog regression test — that test *hung* instead of failing, and
   wrapping the dial loop in `with_timeout` did not help, because a blocking connect froze the pump
   so no timer could fire. Measured freeze: **~7.5 s per stalled connect** on macOS. Two hazards the
   fix had to handle, both worth remembering for any future park site: the in-flight socket is a bare
   fd no handle table owns (so a cancel-drop is its last reference → `scheduler_park_on_unowned_fd`),
   and `force_drop_task` frees only the green stack (so `getaddrinfo`'s malloc'd list had to be
   copied onto the stack and released before the first park). **Anything held across a park must live
   on the task stack.**
9. **`listen(fd, 16)`** — a 16-deep accept backlog, well below every convention (`SOMAXCONN`,
   nginx's 511). **Measured NOT to affect this workload's p99** (§5.3), so it is hardening rather
   than a fix, and it still has **no hermetic regression test available**: a too-small backlog
   manifests as an unbounded park — the kernel drops the SYN and no error reaches either side, so
   there is nothing to assert on. Follow-up 8's fix removes the *second* blocker (a park can now be
   timed out), but a timeout still cannot distinguish "backlog too small" from "peer slow".
10. **The accept loop is the next bottleneck** (§5.3): worker count is irrelevant from w=2 to
    w=256, so once task creation is off the per-request path the single accept task is the limit.
    Any further server work should start there, not at the handler.
11. **`Connection: close` makes any high-throughput HTTP measurement here port-bound.** 16,384
    ephemeral ports against ~32k req/s is about half a second of headroom, after which the client
    stalls on `TIME_WAIT` recycling — §5.3's p99 column. Server-side **keep-alive** would remove the
    constraint and is what would make tail numbers meaningful at all; until then read only
    throughput and p50/p90 from long runs.

## 14. Sources

- Go runtime stack allocator: <https://raw.githubusercontent.com/golang/go/master/src/runtime/stack.go>
- Erlang Efficiency Guide, Processes: <https://www.erlang.org/doc/system/eff_guide_processes.html>
- Oracle JDK 21, Virtual Threads: <https://docs.oracle.com/en/java/javase/21/core/virtual-threads.html>
- Go `sync.Pool`: <https://pkg.go.dev/sync#Pool>
</content>
