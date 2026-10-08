# The GC trigger is a pure space policy (v0, 2026-09-29)

Design doc for issue #407. **Option B (§6.2), the damped footprint floor with `k = 3`, was approved
2026-10-08 and is implemented** in `sprout_gc_collect_with_reason`, tested by `just
gc-trigger-check`. §9 records the measurements on the build.

Companion to [gc-generational-v0.md](gc-generational-v0.md), which owns the *sweep's* cost and the
adapt-factor default. This doc owns the **trigger** only, and it is the cited exception to that
doc's §11.4 — see §4.

---

## 1. Problem statement

`sprout_gc_collect_with_reason` re-bases the collection threshold after every cycle:

```c
if (g_gc_adapt_ratio > 0.0) {
  long long target = (long long)((double)g_managed_heap_count * g_gc_adapt_factor);
  if (target < g_gc_threshold_base) target = g_gc_threshold_base;
  if (g_gc_adapt_cap > 0 && target > g_gc_adapt_cap) target = g_gc_adapt_cap;
  g_gc_threshold = target;
}
```

`g_managed_heap_count` post-sweep is the live set; `g_gc_adapt_factor` is 3.0. Read as a *space*
policy this is right, and it was a deliberate fix — the comment above it records the ratchet it
replaced, where an allocation-heavy program drove the threshold up without bound (multi-GB RSS to
emit a few MB of IR).

The enclosing guard matters for every option below. `g_gc_adapt_ratio` defaults to 0.2 and is
settable via `SPROUT_GC_ADAPT_RATIO`; at 0 the threshold is never re-based and freezes at its
initial value, so the runtime has a third mode — a fixed threshold — that is not the adaptive
policy. Option A must therefore change `g_gc_threshold`'s initialiser as well as
`g_gc_threshold_base`'s, or the first cycle of a frozen run fires at 4096; Options B and D have to
say what they do when the adaptive arm is switched off.

The defect is that this is the *only* thing deciding when to collect.
`sprout_gc_maybe_collect_threshold` fires on `g_managed_heap_count >= g_gc_threshold` and consults
nothing else. Bounding RSS to a multiple of live data and scheduling collector work are the same
decision only if cost-per-collection scales with the live set. It does not (§3), so a program can
make its live set smaller and its total GC time larger.

Reported in `cthulhu666/uncharted-suns#396`: replacing a 75,640-node `Dict` with a bitset cut the
live set 45× and total GC time went **up 4×** (207 → 888 µs/frame).

**The obvious alternative diagnosis is ruled out by the issue's own numbers.** If the bitset version
simply allocated more, the extra collections would be unremarkable. Allocation volume is
`cycles × (threshold − live)`: `Dict` ≈ 43 × 154,672 ≈ 6.7M, bitset ≈ 1,310 × 3,428 ≈ 4.5M, pinned
≈ 14 × 230,377 ≈ 3.2M. The regressing configuration allocates *less*. (The three are not exactly
the same workload — they disagree by up to 2×, which is worth remembering before treating any single
ratio as exact.)

## 2. Goals / non-goals

**Goals**

- Make total collector work bounded by something other than live-set size alone.
- Keep the existing RSS bound for programs whose live set genuinely is the constraint: whatever
  lands is an additional arm, not a replacement.
- Do not regress the workloads the current policy serves well (§4 shows there are several, and
  that this is the constraint most likely to be violated).

**Non-goals**

- **The sweep's own cost.** Filed as `BACKLOG.md` **"The freelists are still wiped and rebuilt from
  *all* regions every sweep"** and **"The class freelists are exact-fit"**. After any trigger fix
  the heap is large again and the pinned run's 2.2 ms pause is nearly all sweep.
- **Compacting or evacuating sparse regions**, which is the root-cause fix for §3's mechanism and
  is *structurally blocked*: [compiler-internals.md](compiler-internals.md) makes non-moving
  load-bearing — rooting pushes an `i64` into an alloca and never reloads it — so evacuation is "a
  sweeping rewrite affecting `ast_to_ir.sprout`, `ir_lowering.sprout`, and `ir_rooting.sprout`".
  Named here so its absence is a decision rather than an oversight.
- **Byte-awareness of the trigger** as a general property, filed as `BACKLOG.md` **"GC trigger is
  object-count-blind, not byte-aware"** (`P2`) and measured in `gc-generational-v0.md` §11. §6.3
  treats the narrower question of what *unit the floor* is in, which is decidable now.
- **Generational collection.** `gc-generational-v0.md` §8 owns that call.
- **Pause.** This is a throughput defect; the best configuration measured has a *larger* p50 than
  the regressing one (2,223 vs 797 µs) and is 34× better in total.

## 3. Why cost-per-collection does not follow the live set

Sweep pass 1, per region, in `sprout_gc_sweep`:

```c
size_t off = 0;
while (off < r->bump) { ... }
```

Every slot below the bump, in every region, every cycle. The walk is exhaustive because mark
colours live in object headers and there are no generations: finding this cycle's dead objects and
clearing the survivors' colour bits both require visiting every slot. (The `memset(g_freelist, 0,
sizeof(g_freelist))` that opens the sweep is a *consequence* of that walk, not its cause — the
freelists can be rebuilt for free once every slot is being visited anyway.)

Two properties make the walked footprint sticky:

- `r->bump` never retreats. It is written in exactly three places: `open_new_region` (to 0), the
  large-object path, and `+=` in `sprout_gc_alloc_block`. A freelist hit does not advance it and
  nothing lowers it.
- Pass 2 releases a region only when `r->live_count == 0 && r->poison_count == 0`. **One**
  surviving object keeps a region's entire high-water bump in the walk indefinitely.

So a cycle costs roughly `mark(live) + sweep(footprint)`, where footprint is slots below the bumps
of all retained regions — a quantity the trigger never reads.

This is not new in the repo. The comment on `g_gc_adapt_factor` already says *"the win is sublinear
because sweep pass 1 walks every SLOT: fewer collections, but each sweeps a larger heap."* That is
about raising the factor; run backwards it says shrinking the live set does not shrink the sweep.

### 3.1 The quantity that actually predicts the regression: work per unit of garbage

"Small live set" is a property of the *program*. What the collector's efficiency depends on is a
property of the *heap's shape*: how much footprint it walks per object it reclaims. The two
coincided in #407's workload, which is how they came to be conflated.

Slots walked per object reclaimed, from #407's table:

| | footprint walked | reclaimed per cycle | slots walked per object reclaimed |
|---|---|---|---|
| `Dict` | ~240,000 | 154,672 | **~1.5** |
| bitset | ~50,000 | 3,428 | **~15** |
| bitset + `SPROUT_GC_THRESHOLD=232000` | ~222,000 | 230,377 | **~1** |

The regression is the middle row: the collector walks an order of magnitude more heap per unit of
garbage than in either other configuration. Raising the floor does not make collections cheaper —
it makes each one *productive*, by letting enough garbage accumulate to be worth the fixed walk.

**Four caveats, because this table is weaker than it looks.**

1. The footprint column is a *residual* (`measured − 100 ns × live`) from the fit in #407, divided
   by an assumed 10 ns/slot, so it is not an independent measurement. `gc-frame-budget-v0.md` §4
   fits the same game at 44–55 ns per live object and 14.5 ns per dead slot, which moves these
   numbers by about 2×.
2. The units are mixed — slots walked against objects reclaimed — so the ratio is a work-per-garbage
   index, not a dimensionless efficiency.
3. Row three is **impossible as printed**: every object present at a trigger occupies a slot, so the
   footprint cannot be ~222,000 when the threshold is 232,000. The residual under-reads by at least
   4%, which is the size of the error in the model, not in the heap.
4. The mechanism behind the middle row was **not established**: 9 regions at ~36 bytes per slot is
   ~262,000 slots against the ~50,000 above. (A *slot* is one header-delimited cell of ≥ 16 bytes,
   not the 16-byte granule — `SPROUT_SLOTS_PER_REGION` = 65,536 counts granules.)

**Measured since** (§9 item 1, `bench/results-2026-10-03-gc-walk.md`): the middle row's sweep
walks **251,306 slots** per cycle to free 3,235 — ~78 per object reclaimed, 5× the table's ~15.
The pinned row walks 289,178 to free 230,240, ~1.3. So the ordering stands and the middle row's
footprint was under-read 5×; caveat 4's region arithmetic was the right one. The mechanism is the
sparse-region one: 8 regions stay alive under ~1,600 live objects, and every sweep walks 246,450
free slots through them. They are not a dead size class: a later run with a per-class probe put
246,747 of 246,829 in classes the game allocates from every cycle, mostly 16- and 32-byte —
most likely what the catalog load (peak live 158,637) left behind (§12 Q1). The
waste is a large, usable free pool that every collection walks, ~3,500 allocations apart.

Use the table for its ordering; the bench note has the measured magnitudes.

## 4. What the repo already knows, including the part that argues the other way

`gc-generational-v0.md` §11.4 is the standing guard against floor-tuning:

> Raising `g_gc_threshold` 4096 → 6144 restored the cycle count to **exactly** 8,279 and bought
> **0.4 ms of 204**. […] **Reach for the floor only with a measurement that separates** [frequency
> from sweep volume], or the tuning looks principled and does nothing.

That guard stands and should not be softened; this doc is an exception to it, not a repeal of it.
#407 supplies the separation it demands, for one workload: same binary, same program, only the
floor moved, 1,310 → 14 collections and 888 → 26 µs/frame. (Not quite the same live set — #407
reports medians of 1,714 against 1,623 — but ~90 objects cannot account for 888 → 26 µs/frame.
Nor was the unpinned run at the floor: its 5,142 heap slots are 1,714 × 3, set by the factor.)

**But §13.2 of the same doc ran the floor experiment on other workloads, and it comes out flat or
worse.** `gc_roots` holds ~70 live objects across a 1,000× threshold sweep:

| `SPROUT_GC_THRESHOLD` | cycles | p50 µs | cycles × p50 |
|---|---|---|---|
| 4,096 | 5,591 | 22 | **123 ms** |
| 40,960 | 550 | 203 | **112 ms** |
| 409,600 | 55 | 1,936 | **106 ms** |
| 4,096,000 | 6 | 20,500 | **123 ms** |

Flat within 15% over three decades. nqueens is worse: its ns-per-swept-slot rises 9.4 → 17.9 → 21.5
across the same range as the heap outgrows cache, so the same allocation volume costs about twice
as much total sweep at the top. (§13.2 gives no cycle column for nqueens; the ns/slot trend is the
direct evidence and needs no inference.)

**These results are consistent, and §3.1 says why.** `gc_roots` sweeps 4,019 of 4,096 slots — it is
already at ~1 slot walked per object reclaimed, the same regime as #407's first and third columns.
There is no waste for a floor to remove, so raising it buys nothing and eventually costs cache.
nqueens is not quite that regime — it walks 1.46 free slots per object freed — but that waste is
small next to #407's, and the floor still lost to cache (`bench/results-2026-10-03-gc-walk.md`).

**The condition under which a floor helps is therefore narrow and statable:** the heap must be
walking substantially more footprint than it reclaims. That is a measurable property, and it is not
implied by a small live set. `SPROUT_DEBUG_GC`'s `walked=` now reports it (§9 item 1).

This is the single most important correction to make to anyone's intuition about #407, including
the author's of this doc: the finding is not "the default floor is too low". It is "a program whose
retained footprint greatly exceeds its per-cycle garbage is served badly, and the trigger cannot
see it".

## 5. Prior-art survey (primary-sourced)

Scope: **what decides when a collection starts** — distinct from `gc-generational-v0.md` §3, which
surveys young generations. Quotes verified against each implementation's own reference; URLs in §13.

| Runtime | Space term | Floor | Frequency bound independent of live set |
|---|---|---|---|
| **Go** | `Live + (Live + roots) × GOGC/100` | **4 MiB, in bytes** — *"the Go GC has a minimum total heap size of 4 MiB, so if the GOGC-set target is ever below that, it gets rounded up"* | Only under `GOMEMLIMIT`: the *"roughly 50%, with a `2 * GOMAXPROCS` CPU-second window"* limiter is in the guide's **memory-limit** section, not normal operation |
| **.NET CLR** | *"memory used by allocated objects […] surpasses an acceptable threshold. This threshold is continuously adjusted as the process runs"* | not stated on the cited page | *"When the GC detects that the survival rate is high in a generation, it increases the **threshold of allocations** for that generation"* |
| **OCaml 5** | major GC paced by `space_overhead`, default **120**, *"expressed as a percentage of the memory used for live data"* | minor heap, `minor_heap_size`, default 256k **words** | the minor heap is a fixed allocation budget — but it bounds *minor* collections in a generational design, so the analogy to a non-generational floor is loose |
| **HotSpot** | footprint is met **last** | — | *"The ratio of garbage collection time to application time is 1/(1+nnn)"* (`-XX:GCTimeRatio`); *"If the throughput goal isn't being met, then one possible action […] is to increase the size of the heap"* |
| **Sprout** | `live × 3.0` | **4096, in objects** | none |

**The honest headline is about the floor's unit and magnitude, not its absence.** Sprout is not
alone in having only a space term — Go's default configuration is also live-proportional plus a
floor, which is structurally what Sprout has. What differs is that Go's floor is **4 MiB of bytes**
and Sprout's is **4096 objects**, a quantity that spans ~64 KB to ~16 MB across the non-large size
range, and more again once the large-object path is involved (§6.3).

Two rows still carry a lesson Sprout's design does not have:

*HotSpot inverts the priority order.* Goals are met as maximum-pause, then throughput, then
footprint: *"If the throughput and maximum pause-time goals have been met, then the garbage
collector reduces the size of the heap until one of the goals (invariably the throughput goal)
can't be met."* Footprint is what it spends last; Sprout spends it first and never reconsiders.

*.NET states the balance in one sentence:* **"The CLR continually balances two priorities: not
letting an application's working set get too large by delaying garbage collection and not letting
the garbage collection run too frequently."** Sprout implements the first clause only.

## 6. Options

### 6.1 Option A — raise the floor's default

Not a new mechanism: `SPROUT_GC_THRESHOLD` already sets `g_gc_threshold` and `g_gc_threshold_base`
together, which is why pinning it works. Option A is *changing two initialisers*, and calling it an
"allocation-budget floor" would overstate it — it is a heap floor in objects, the same quantity
that exists today.

- **For:** the effect is already measured (§4's third column), and the escape hatch comes free —
  `SPROUT_GC_THRESHOLD` set lower still overrides it.
- **Against, and this is close to disqualifying:** §4 predicts it is flat-to-harmful on every
  workload that is *not* in the high-waste regime, and §8 shows the constant #407 needs collides
  with an existing gate. It also does nothing about the unit problem (§6.3).

A variant with a *separate* `g_gc_min_alloc_budget` applied after `g_gc_threshold_base` was
considered and rejected: it silently overrides a user's low `SPROUT_GC_THRESHOLD`, destroying the
escape hatch, while being numerically identical to raising the one floor that already exists.

### 6.2 Option B — tie the floor to the measured footprint

```c
target = max(live × factor, base, live + (live + free) / k);   /* k = 3 */
```

`free` is the freelist length after the sweep. Setting `SPROUT_GC_THRESHOLD` turns the third term
off (§12 Q5). Collect once the program has allocated a fixed
fraction of what the last sweep had to walk. This targets §3.1's quantity directly: it bounds
work-per-garbage near `k` whatever the live set is.

- **For:** no guessed heap size. `k` bounds a ratio, the same for every program, where A's floor
  is a heap size that suits one. Needs no clock. Measured on a prototype
  (`bench/results-2026-10-04-gc-trigger-b.md`): the game's GC per allocation falls 12–16× with no
  added regions, `gc_roots` and nqueens run identical cycles, and the compiler goes 98 → 103 MB.
- **The signal separates.** Free slots walked per object freed (FREE/swept) is 0.00–1.46 on every
  ordinary workload measured and **52–76** on the game; pinning the game's threshold drops it to 0.25
  (§3.1, `bench/results-2026-10-03-gc-walk.md`). Under the damped floor it settles at
  `k·free / (live + free) − 1`: `k − 1` when the free pool dwarfs the live set, as on the game.
- **It terminates, and the undamped form does not.** `g_freelist` is exact-fit (`BACKLOG.md`
  **"The class freelists are exact-fit"**), so take `U` free slots no allocation can reuse. A
  cycle allocates `(live + U + A) / k` into the reusable pool `A`; allocations `A` cannot hold bump
  fresh slots, and if anything live pins their regions they outlive Pass 2 and join `A`. `A`
  settles at `(live + U) / (k − 1)`, so the whole free pool at `U + (live + U) / (k − 1)`, which
  the prototype hit to within 13 slots on a constructed adversary. At `k = 1` there is no fixed
  point: `A` grows by `live + U` every cycle.
- **Why `k = 3`.** k=2 already moves nqueens (8,279 → 5,637 cycles) and the compiler (98 →
  109 MB); k=3 leaves nqueens identical and the compiler at 103 MB. It matches the adapt factor,
  which is a coincidence rather than a reason.
- **The per-class repair an earlier draft proposed diverges.** It floored on `live` plus the free
  slots in classes with allocation demand last cycle. One allocation a cycle in the class holding
  `U` keeps all of `U` counted, so it ratchets exactly as the undamped form does: +100,000 slots a
  cycle on the adversary, to 1.3M when the program ended. It also took the compiler to 221 MB and
  tripled the game's pauses.
- **Units are not a problem**, contrary to an earlier draft: slots walked and `g_managed_heap_count`
  are the same count — one cell per object whatever its size, and a large object is one region
  walked as one.

### 6.3 Option C — express the floor in bytes

Orthogonal to A and B: whatever sets the floor, should it be counted in objects or bytes?

The argument for keeping objects, in an earlier draft, cited `gc-generational-v0.md` §11.3 — a
count-based *cap* livelocks. That does not transfer: §11.3 is about a cap (which can sit below the
live set and force a collection per allocation) and says nothing about a floor, which cannot.

The argument for bytes is concrete. 4096 objects is ~64 KB of minimum-size slots or ~16 MB of
`SPROUT_LARGE_THRESHOLD`-sized ones, and raising it to six figures multiplies that spread by the
same factor. Both halves of the counter already exist in the sweep and the allocator —
`needed_slot` at every `sprout_gc_alloc_block`, `ssize` at every step of the sweep walk, and
`g_debug_alloc_arena_bytes` already does the allocation half behind a debug flag. Every floor in
§5's survey is in bytes.

### 6.4 Option D — duty-cycle pacer

Choose the threshold so collector time stays under a target fraction of mutator time. Prior art:
HotSpot's `GCTimeRatio`, Go's limiter.

- **For:** the general form, and it self-corrects for any cost model rather than the one §3 happens
  to describe.
- **Against:** it is a feedback controller needing damping and a stability argument. It is not
  constant-free — HotSpot's has `GCTimeRatio` — the constant just becomes a portable one. And
  **it must carry a hard ceiling or it re-creates the bug the current policy was written to fix**:
  a pacer widens the threshold when cycles are expensive, which on the compiler is exactly when the
  live set is large, so without a cap it is the old grow-only ratchet and its multi-GB RSS. A `max`
  floors it; only a `min` caps it. Go pairs its limiter with `GOMEMLIMIT`, HotSpot with `-Xmx`.
- **Runtime prerequisite, stated precisely:** a **CPU-time** clock — `CLOCK_PROCESS_CPUTIME_ID`,
  with `getrusage(RUSAGE_SELF)` as the POSIX fallback. *Not* the per-phase timer filed as
  `BACKLOG.md` **"The GC pause tail is unattributable"** (`P3`), which asks for phase attribution
  and a quiet machine — the right requirement for diagnosing a pause tail, the wrong one for a duty
  cycle. A CPU-time clock makes `gc-generational-v0.md` §13.4's noise structurally irrelevant,
  since descheduled time is not counted. Go specifies its own limiter in CPU-seconds for this
  reason. Portability: both are
  POSIX and Windows is a live target (`windows-port-v0.md`, a `windows-latest` CI job), though not
  a blocker today — that job's comment says *"the runtime is POSIX-only — all three C translation
  units still fail to compile for Windows"*. The Windows equivalent is `GetProcessTimes`, and
  `sprout_poll.c` already platform-splits behind `_WIN32`.

### 6.5 Recommendation

**§9 item 2 is now measured** (`bench/results-2026-09-30-gc-floor.md`) and A's price is no longer
a guess: 3.2× peak RSS and +5.3% wall on nqueens, 1.9× RSS at no wall cost on http_log_middleware
(over five of its six phases in both arms: the sixth has trapped on Int overflow since 2026-09-23,
`BACKLOG.md` **"`http_log_middleware` overflows in `wall_loop`"**), nothing on math, no resolvable effect on the socket server, and a ~20× rise in per-collection
pause wherever a workload was floor-pinned. **§9 item 1 is measured too**: the game reads 52–76
free slots walked per object freed, against at most 1.46 elsewhere (§6.2). **And B has run**, on
a prototype (`bench/results-2026-10-04-gc-trigger-b.md`): the damped floor cuts the game's GC per
allocation 12–16× with no added regions; the ordinary workloads move by at most the compiler's
+4% RSS.

Given that, the ordering the evidence supports:

1. **Build Option B in its damped form (§6.2).** It does for the reporting workload most of what
   pinning did — 14 µs of GC per 1,000 allocations against 7–10 pinned at 232,000 and 169–220 at
   the default — without a raised minimum heap, so it costs nqueens nothing where A cost it 3.2×
   RSS. Its pauses rise ~30% on the game (807 → ~1,080 µs), where A's rose ~20× wherever a
   workload was floor-pinned.
2. **Option A is no longer needed as an interim.** It was one while B had no terminating form. Its
   price above stays the record of why it was not shipped.
3. **Option C should be folded into whatever lands**, and §9 item 2 strengthened its case from an
   unexpected direction. By cycles × p50 pause, nqueens' collector got *cheaper* at the raised
   floor (315 → 274 ms, a proxy that leaves out the tail) while the program got slower; the likely
   cost is cache, at 17.4 MB. http_log_middleware saw the same proxy saving at 8.5 MB and came out
   flat. The variable separating them is whether the raised heap outgrows cache — a threshold in
   **bytes**, invisible to a trigger that counts objects. That argument does not route through
   work-per-garbage, so C no longer depends on B.
4. **Option D stays the end state**, gated on the ceiling and the CPU clock, neither of which is
   large.

**What is still a judgement call:** `k`. 3 is where the ordinary workloads stop moving (§6.2), not
a derived value, and the prototype numbers are one run each outside the game; the real build is
measured again before it lands (§9).

This section has reversed three times — away from A on a gate collision that measurement shows
does not exist, back toward it, then to B once B had a form that terminates. §8's preamble names
the error the first two shared; the third came from running B instead of reading one cycle of it.

## 7. Impact

- **Syntax, type system, error messages, effects:** none for any option. No language surface moves,
  no Sprout source changes, no diagnostic changes.
- **`runtime/APPROVED_BUILTINS`:** not touched by A, B or C — no new `long long <name>(…)`.
  Definition of Done #10 does not apply.
- **Collaboration Rule 6:** no new builtin surface. The *policy* change needs a call, which is what
  this doc is for.
- **Semantics:** observable only as RSS and timing. No program's result changes; GC is not
  observable from Sprout by design (`test_gc_age_retain_none`: *"Object age is not observable from
  Sprout"*).

**Compatibility and migration.** Raising a floor raises the minimum heap of every program whose
live set is under `floor / factor`. The bound is `floor × average slot bytes` — the trigger fires
*at* the threshold (`g_managed_heap_count >= g_gc_threshold`), so peak managed objects equal the
floor, not the floor times the factor. Programs setting `SPROUT_GC_THRESHOLD` keep full control
under Option A, since it writes both the threshold and its base, and under B, because setting it
turns B off (§12 Q5).

**The pause side, measured.** A floor-pinned workload's per-collection pause scales with the floor:
at 100,000, nqueens goes 38 → 818 µs p50 and http_log_middleware 25 → 509 µs (five of six
phases, §6.5), both ~20×,
while collecting ~25× less often. GC time falls in both cases on a cycles × p50 proxy; jitter
rises. For #407's own
constituency — a program holding a frame budget — 818 µs is 5% of a 16.7 ms frame, so this is
affordable by a factor of 20 rather than by orders of magnitude, and a floor much above 100,000
spends that margin (`bench/results-2026-09-30-gc-floor.md` §2).

## 8. Blast radius — measured, not modelled

The symmetry with the last default change is the thing to hold onto. `gc-generational-v0.md` §5.3
justified the factor 2.0 → 3.0 on the workloads *above* the floor, and recorded that the
floor-pinned four — nqueens, http ×2, math — were *"unchanged by construction."* **A floor change
has the inverse blast radius**: it moves precisely those four. None of §5.3's evidence transfers.

§8.1–8.3 measure **Option A**, by exporting `SPROUT_GC_THRESHOLD`, which writes both
`g_gc_threshold` and `g_gc_threshold_base` and so stands in exactly for a raised compiled-in
default. Neither gate's probes set that variable, so simulating one needs no source edit. §8.4
measures **Option B** on the prototype.

**Read this before deriving any floor boundary from `gc-generational-v0.md` §13.3.** That table's
`live` column is a **mean over cycles** — `bench/gc_pause/pause_stats.py` computes
`sum(live) // len(live)` across every logged cycle, including the geometric build phase and the
`atexit` cycle where almost nothing is live. It is not a live set: `test_gc_age_retain_all` retains
150,000 objects (`build(150000, End)`). Four separate derivations of a "floor above which the gate
breaks" multiplied that mean by a factor and produced four different wrong answers. The measured
result is not even the same *shape* of claim.

### 8.1 `gc-adapt-check` — a narrow red pocket, not a threshold

| floor | F=2 | F=3 / default | Property 1 |
|---|---|---|---|
| 4,096 (today) | 9 cycles / 558,065 marked | 6 / 313,849 | **green** |
| 100,000 | 4 / 400,017 | 3 / 250,009 | **green** (ratio 0.63) |
| ~138,000 | 3 / 288,009 | 3 / 288,009 | **RED** — both assertions |
| 232,000 | 3 / 300,017 | 2 / 150,009 | **green** (ratio 0.50) |

**The gate is green at 232,000.** The red region is a pocket where F=2 loses a churn cycle before
F=3 loses its only one, so all three probes collapse to the same cycle count and the factor appears
inert; it is green on both sides. Every boundary here is an integer cycle comparison, so it moves
with any change to the fixture's allocation volume — the gate is *fragile* at a raised floor rather
than broken by one, which is what §10's re-expression advice is for.

Property 2 (floor-pinned inertness on `retain_none`) passes at every floor tested.

### 8.2 `gc-ageprof-check` — the real collision, five figures lower

| floor | `retain_all marked_age_ge1` (needs ≥ 70%) | `retain_none` cycles | verdict |
|---|---|---|---|
| 4,096 | 73% | 118 | green |
| 12,000 | 68% | — | **red** |
| 100,000 | 62% | 5 | **red** |
| 232,000 | 49% | 3 | **red**, four ways |

The 3-point margin at today's default comes entirely from build-phase cycles re-marking the chain
built so far, so it erodes as soon as a floor removes those cycles. At 232,000 the gate also
reports `separation 32pp < 40pp — the counter does not discriminate`: the instrument stops telling
the retain-all fixture from retain-none at all, because `retain_none` no longer runs enough cycles
to count.

**This is a calibration artifact, not an argument against a floor.** That gate's own comment says it
*"validates the age COUNTER; `gc-adapt-check` covers the threshold POLICY"*, so pinning
`SPROUT_GC_THRESHOLD=4096` inside its probes is what it should already be doing. Any floor change
carries that pin.

### 8.3 The compiler is not in A's blast radius

Contrary to an earlier draft of this section. It is in B's, mildly: 252 → 224 cycles and 98 → 103
MB on the emit run (`bench/results-2026-10-04-gc-trigger-b.md`). A floor pins only the cycles where
`live × factor < floor`, which for a compile is the early phase while the live set is still
growing — and fewer early cycles is a time win. The emit run's late-phase target stays above any
candidate floor, so peak RSS is unchanged. The cost falls on *small* compiles instead: a
test-suite file's peak heap becomes `floor × slot bytes`, roughly 4 MB at 100,000.

| gate | effect |
|---|---|
| `just gc-adapt-check` | green at 100,000 and at 232,000; red only in the ~138,000 pocket (§8.1). Property 2 passes throughout |
| `just gc-ageprof-check` | **red above ~10,000** (§8.2) — `retain_none`'s cycles collapse 118 → 5 → 3 and the counter stops discriminating. Needs the threshold pin. Commit `88fcd286` retuned this fixture once already, and §5.3 warns *"re-mark ratios are only comparable at equal collection frequency"* |
| `just test` | full suite (Definition of Done #5) for any implementation |
| `just run-example-canary` | required — runtime edit (Definition of Done #11) |
| `just linux-smoke` | recommended before pushing; heap sizing is exactly what diverges by allocator |
| `just rooting-cost-gate` | prices the compiler via `arena_bytes` and `gc_swept`; `gc_swept` is floored only |
| golden IR | **not affected** — no emitted IR changes |

### 8.4 Under B — one gate goes red, by design

Each GC-sensitive gate run twice against the prototype runtime (via `just runtime_src=…`;
`rooting_cost_gate.sh` against the seed linked to it), floor off and damped:

| gate | off | damped |
|---|---|---|
| `gc-adapt-check` | green | green, every probe identical (6 / 9 / 6 cycles, `retain_none` 118) |
| `gc-ageprof-check` | green | green, identical (73%, 24% / 99%) — needs no pin under B |
| `gc-walk-check` | green | **red**: `walk_sparse` 88 → 10 cycles, 25 → 2.8 slots walked per object swept |
| `render-cost-gate` | green | green: 3 objects in 3.1M differ |
| `rooting-cost-gate` | green | green: 4 objects in 75,026, `gc_swept` 185,213 → 185,209 |

The dense fixtures never let B's floor rise above `max(live × factor, base)`, which is the §6.2
claim seen from the gate side. `gc-walk-check` fails because B removes the very waste its known
answer depends on: it asserts the sparse fixture walks at least 10 slots per object swept, so the
counter is shown to see FREE slots. That is a counter test, so it pins `SPROUT_GC_THRESHOLD=4096`
under B — today's default, so its readings do not move, and B is off for it (§12 Q5).

## 9. Measurement plan

Items 1 and 2 preceded the choice; items 3 and 4 **precede landing B**, measured on the build
rather than the prototype, which ran the compiler once and only #407's scene. Make them one script
over arms × workloads, reporting per allocation, so the next trigger change re-runs it rather than
re-deriving it by hand.

1. ~~**Measure work-per-garbage directly.**~~ **DONE** — `walked=` on `SPROUT_DEBUG_GC`'s cycle
   line, gated by `just gc-walk-check`; readings in `bench/results-2026-10-03-gc-walk.md`. B
   survives (§6.2) and §3.1's sparse-region mechanism is confirmed. Not read: `spawn_server` and
   the game's in-system scene.
2. ~~**Falsify the floor prediction on the floor-pinned four.**~~ **DONE** —
   `bench/results-2026-09-30-gc-floor.md`. §4 held on all four: nothing improved beyond noise, and
   the cost is §6.5's. Two things the run is worth reading for beyond its table — the cache
   crossing that makes the byte case (§6.5 item 3), and a −5.0% reading at 5 reps that became
   +0.5% at 11, which is why the rep count is in the record.
3. ~~**The compiler**~~ **DONE** — `ast_to_ir.sprout` emit by the seed compiler, 3 reps
   interleaved, `bench/gc_trigger/measure.sh`. The floor costs nothing here; the RSS risk the
   prototype priced at +4% did not appear on the build:

   | workload | time | peak RSS | cycles | GC ms |
   |---|---|---|---|---|
   | compiler emit, off → floor | 1.47s → 1.51s (noise) | 105.9 → **103.5 MB** | 175 → 153 | 661 → 633 |

   Wall is within run-to-run spread (1.41–1.54s over both arms). The prototype ran an earlier
   compiler (`e6553023`); the build's heap is shaped differently, so its +4% is not a prediction
   for this one.
4. ~~**The reporter's workload**~~ **DONE** — `uncharted-suns` `game/app.sprout`, 1,200 frames
   muted, from cycle 16, 2 reps interleaved, `bench/gc_trigger/measure.sh`. `system` starts
   in-system with `perf.py`'s `belt` flags (`--system=00232 --ship-view`).

   | scene | arm | cycles | GC µs per 1k allocations | mean pause | regions | mean threshold |
   |---|---|---|---|---|---|---|
   | galaxy | off | 1,654 / 1,113 | 191.8 / 211.2 | 753 / 755 µs | 9 / 8 | 5,889 / 5,364 |
   | galaxy | floor | 31 / 96 | **15.7 / 13.5** | 1,145 / 1,041 µs | 8 / 9 | 76,188 / 79,622 |
   | system | off | 116 / 116 | 13.4 / 13.4 | 1,526 / 1,526 µs | 10 / 10 | 170,645 / 170,668 |
   | system | floor | 117 / 151 | 15.2 / 12.7 | 1,729 / 1,123 µs | 12 / 10 | 170,666 / 90,584 |

   The galaxy scene repeats the prototype: 12–16× less GC per allocation, no added regions, and
   pauses up ~45% (the prototype read ~30%). **The in-system scene does not reverse §6.5**: its
   live set already holds the threshold near 170,000, so the floor rarely binds, and GC per
   allocation stays within run spread. One unexplained reading: the second floor run's mean
   threshold is 90,584. The floor can only raise a target, so that run took a different path
   through the scene rather than being lowered by B; it was not chased.

**Report both axes on every row.** #407 exists because a change was evaluated on pause alone.

## 10. Tests

Landed as `just gc-trigger-check` (in `ci-fast-gates`), described in [gates.md](gates.md).

- **Regression test, written first and confirmed RED** (Definition of Ready #3):
  `test_gc_walk_sparse` under the default trigger must walk fewer than 4 slots per object swept.
  It read 25 before B and 2.77 after; it is also the "B is on by default" assertion below.
- **A termination test**: `test_gc_trigger_adversary` — a dead class with a trickle of demand,
  and live objects pinning the churn's fresh regions — must reach a fixed free pool: at most 2%
  growth over the second half of the run. It reads 150,802 → 150,842 under B. With the divisor
  set to 1 it grew 210,570 → 718,250 and the gate went red, so it tells a settling floor from a
  diverging one.
- **`gc-walk-check` pins `SPROUT_GC_THRESHOLD=4096` in its probes** (§8.4). It tests the counter,
  and B removes the walk its known answer needs. The pin is the default and turns B off (§12 Q5),
  so the gate's readings stay where they were.
- **`gc-ageprof-check` and `gc-adapt-check` need nothing under B**: every probe reads identically
  with the damped floor on (§8.4). The rest of this bullet is A's, kept for the record. Under A,
  `gc-ageprof-check` would need the same 4096 pin (§8.2 measures it red above roughly 10,000, and
  its comment says it validates the age counter, not the policy). `gc-adapt-check` is green at
  A's candidate but fragile: §8.1's boundaries are integer cycle comparisons over its **three**
  assertions — `def_cyc == f3_cyc && def_marked == f3_marked`, `def_cyc < f2_cyc`,
  `f3_marked × 100 < f2_marked × 70`. Property 1 would need re-expressing to exercise the factor
  over many cycles instead of two or three.
- **B on by default** is the first bullet's assertion: the pins above turn B off wherever they
  apply, and the sparse fixture runs unpinned there, so a revert fails the bound of 4.
- **Coverage gap closed** (Definition of Ready #4): before this, no test asserted how *often*
  collections run as a function of heap shape — the thing a trigger fix changes. Both probes above
  do (sparse: 88 → 10 cycles).

## 11. Docs, spec and backlog

- Not spec-affecting. `docs/spec-v0.md` is silent on collection policy and should stay silent.
- **`gc-generational-v0.md` §11.4 stays as written.** A guard that gets amended every time a case
  clears it stops being a guard; this doc is the cited exception, and §4 explains what made it one.
- The `BACKLOG.md` `P1` galaxy-game entry is corrected in the same change that adds this doc: it
  recommended shrinking the live set, that route was taken, and #407 is the result.
- ~~On implementing any option, delete the `BACKLOG.md` entry this doc's work becomes.~~ Done:
  the `P1` "The GC trigger is a pure space policy" entry was deleted when B landed.

**Found in passing, filed separately.** The GC cycle timer measures elapsed time with the wrong
clock: `sprout_gc_collect_with_reason` brackets the collection with `sprout_now_micros()`, which is
`gettimeofday`/`CLOCK_REALTIME`, while that function's neighbour in the same file documents the rule
it breaks — *"NOT monotonic […] must not be used for elapsed-time measurement (use
`time_now_micros` for that)"*. Those two calls are its only elapsed-time uses, so the fix is one
call site. Scope it honestly: it removes NTP and clock-change artifacts from `SPROUT_DEBUG_GC`'s
`elapsed_us` and does **not** explain `gc-generational-v0.md` §13.4's pause tail, since both
clocks count descheduled time.

## 12. Open questions

1. ~~**Does §6.2's repair hold?**~~ No — the per-class repair diverges; the damped form holds
   (§6.2, `bench/results-2026-10-04-gc-trigger-b.md`). A one-cycle probe had read the per-class
   floor at ~248,500 on the game, because 246,747 of its 246,829 free slots are in classes it
   allocates from. Run as a loop, it averaged 321k–367k: the pool is not split across classes in
   the game's allocation mix, so short classes keep bumping new slots. A trickle of demand into a
   dead class, with live objects pinning the fresh regions, makes it grow without bound.
2. ~~**What constant, if A ships?**~~ Moot while A is not recommended (§6.5). ~100,000 was where
   §8.1 measures green with margin on both sides of the pocket, at the cost §9 item 2 priced.
3. ~~**Is §3.1's sparse-region mechanism real?**~~ Yes — measured (§3.1).
4. **Does anything below the floor deserve the old behaviour?** A genuinely tiny program pays a
   six-figure minimum heap under A. Go's answer is that 4 MiB is small enough not to matter;
   Sprout's floor is in objects, so the equivalent claim is not automatically true (§6.3). Moot
   under B, which raises no minimum: its floor rises only with free slots the program already holds.
5. ~~**What does `SPROUT_GC_THRESHOLD` mean under B?**~~ **Decided: setting it turns B off.** It
   sets the threshold and its base, so a low value forces frequent collection. Taking the larger
   of that base and B's floor, as the first prototype did, would stop it forcing anything
   wherever B's floor is higher — §6.1's escape-hatch objection, aimed at B. Turning B off keeps
   the variable meaning what it means today, and it is what lets a gate pin a threshold (§10).

## 13. Sources

Primary sources for §5, fetched 2026-09-29:

- Go — *A Guide to the Go Garbage Collector*, https://go.dev/doc/gc-guide (heap goal formula;
  4 MiB minimum heap; the 50% CPU limiter, in the memory-limit section)
- .NET — *Fundamentals of garbage collection*,
  https://learn.microsoft.com/en-us/dotnet/standard/garbage-collection/fundamentals
  (conditions for a collection; allocation threshold adjusted on survival rate; the two-priority
  balance)
- OCaml 5.2 — `Gc` module reference, https://ocaml.org/manual/5.2/api/Gc.html
  (`space_overhead` = 120, as a percentage of live data; `minor_heap_size` = 256k words)
- HotSpot — *Ergonomics*, JDK 17 GC Tuning Guide,
  https://docs.oracle.com/en/java/javase/17/gctuning/ergonomics.html
  (`-XX:GCTimeRatio`, goal priority order, heap grown to meet a time goal)

`GCTimeRatio`'s default is not stated on that page and is deliberately omitted rather than taken
from a secondary source.

In-repo references are by identifier per AGENTS.md §Docs & Spec 6: `sprout_gc_collect_with_reason`,
`sprout_gc_maybe_collect_threshold`, `sprout_gc_sweep`, `sprout_gc_alloc_block`, `open_new_region`,
`sprout_now_micros`, `time_now_micros`, `g_gc_threshold`, `g_gc_threshold_base`,
`g_gc_adapt_factor`, `g_gc_adapt_cap`, `g_freelist`, `g_debug_gc_swept`, `g_prof_sweep_visits`.
