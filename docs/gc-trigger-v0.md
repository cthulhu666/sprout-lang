# The GC trigger is a pure space policy (v0, 2026-09-29)

Design doc for issue #407. **No option here is approved or implemented.** §6 is a decision to be
made, and §9 is the measurement that has to precede it.

Companion to [gc-generational-v0.md](gc-generational-v0.md), which owns the *sweep's* cost and the
adapt-factor default. This doc owns the **trigger** only, and it is the cited exception to that
doc's §11.4 — see §4.

---

## 1. Problem statement

`sprout_gc_collect_with_reason` re-bases the collection threshold after every cycle:

```c
long long target = (long long)((double)g_managed_heap_count * g_gc_adapt_factor);
if (target < g_gc_threshold_base) target = g_gc_threshold_base;
if (g_gc_adapt_cap > 0 && target > g_gc_adapt_cap) target = g_gc_adapt_cap;
g_gc_threshold = target;
```

`g_managed_heap_count` post-sweep is the live set; `g_gc_adapt_factor` is 3.0. Read as a *space*
policy this is right, and it was a deliberate fix — the comment above it records the ratchet it
replaced, where an allocation-heavy program drove the threshold up without bound (multi-GB RSS to
emit a few MB of IR).

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
the same workload — they disagree by 25–40%, which is worth remembering before treating any single
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

**Three caveats, because this table is weaker than it looks.** The footprint column is a *residual*
(`measured − 100 ns × live`) from the fit in #407, divided by an assumed 10 ns/slot, so it is not an
independent measurement; `gc-frame-budget-v0.md` §4 fits the same game at 44–55 ns per live object
and 14.5 ns per dead slot, which moves these numbers by about 2×. The units are mixed — slots
walked against objects reclaimed — so the ratio is a work-per-garbage index, not a dimensionless
efficiency. And the mechanism behind the middle row is **not established**: "survivors scattered
across sparse regions" is the natural reading, but 9 full 1 MiB regions would be ~260k slots, not
50k, so those regions must be mostly low-bump for some other reason. §9 measures this directly
rather than inferring it.

Use the table for its order of magnitude and its ordering, which are what the argument needs.

## 4. What the repo already knows, including the part that argues the other way

`gc-generational-v0.md` §11.4 is the standing guard against floor-tuning:

> Raising `g_gc_threshold` 4096 → 6144 restored the cycle count to **exactly** 8,279 and bought
> **0.4 ms of 204**. […] **Reach for the floor only with a measurement that separates** [frequency
> from sweep volume], or the tuning looks principled and does nothing.

That guard stands and should not be softened; this doc is an exception to it, not a repeal of it.
#407 supplies the separation it demands, for one workload: same binary, same live set, only the
floor moved, 1,310 → 14 collections and 888 → 26 µs/frame.

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

**The condition under which a floor helps is therefore narrow and statable:** the heap must be
walking substantially more footprint than it reclaims. That is a measurable property, it is not
implied by a small live set, and **no current gate or benchmark reports it.**

This is the single most important correction to make to anyone's intuition about #407, including
the author's of this doc: the finding is not "the default floor is too low". It is "a program whose
retained footprint greatly exceeds its per-cycle garbage is served badly, and nothing measures
that".

## 5. Prior-art survey (primary-sourced)

Scope: **what decides when a collection starts** — distinct from `gc-generational-v0.md` §3, which
surveys young generations. Quotes verified against each implementation's own reference; URLs in §12.

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
and Sprout's is **4096 objects**, a quantity that can correspond to anywhere from ~64 KB to
hundreds of MB depending on object size (§6.3).

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
/* schematic, units unresolved — see below */
target = max(live × factor, base, footprint_walked_last_cycle);
```

Collect once the heap has grown to what the last sweep actually had to walk. This targets §3.1's
quantity directly: it drives work-per-garbage toward ~1 by construction, whatever the live set is.

- **For:** no guessed constant. RSS-neutral in principle — those slots are already committed, so
  the bound it sets is one the process is paying for regardless. Needs no clock. On #407's middle
  row it yields roughly the pinned configuration automatically.
- **Against — three real problems, none fatal but none free.** *Units*: footprint is in slots and
  the threshold is in objects, so the comparison above is a type error as written and needs an
  explicit conversion whose error term must be bounded. *Cost*: no unconditional slot counter
  exists — `g_debug_gc_swept` counts freed objects and `g_prof_sweep_visits` is compile-time gated
  — so this adds one increment per slot to the sweep's hot loop. *Ratchet*: `g_freelist` is
  exact-fit (`BACKLOG.md` **"The class freelists are exact-fit"**), so slots in size classes the
  program has stopped allocating stay in the footprint without being reusable; the budget then
  grows, the bump advances into fresh regions, and the footprint grows again. That feedback loop
  must be shown to terminate before this is buildable.

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
  cycle. A CPU-time clock makes §13.4's noise structurally irrelevant, since descheduled time is
  not counted. Go specifies its own limiter in CPU-seconds for this reason. Portability: both are
  POSIX and Windows is a live target (`windows-port-v0.md`, a `windows-latest` CI job), though not
  a blocker today — that job's comment says *"the runtime is POSIX-only — all three C translation
  units still fail to compile for Windows"*. The Windows equivalent is `GetProcessTimes`, and
  `sprout_poll.c` already platform-splits behind `_WIN32`.

### 6.5 Recommendation

**Measure §9 item 1 first — it is cheap and it decides between B and nothing.** The doc cannot
honestly rank the options while the quantity they all target (§3.1) has never been measured
directly, and the one number that would settle it is a counter in a loop that already exists.

Given that, the ordering the evidence supports:

1. **Option B is the design to pursue**, conditional on its ratchet terminating. It is the only
   option that targets the measured condition rather than a proxy for it, and it introduces no
   constant. Its three problems (§6.2) are engineering, not open research.
2. **Option C should be folded into whatever lands.** The unit question is decidable now and the
   answer is bytes.
3. **Option A is not recommended.** It is flat-to-harmful outside the high-waste regime (§4), and
   the constant #407 needs breaks an existing gate (§8). It remains available as a *documented
   workaround* — which is what `SPROUT_GC_THRESHOLD` already is, and uncharted-suns can set it
   today without any change to this repo.
4. **Option D stays the end state**, gated on the ceiling and the CPU clock, neither of which is
   large.

This reverses an earlier draft of this doc, which recommended A. The reversal is caused by §4
(§13.2's counter-evidence, which that draft did not engage) and §8 (the gate collision, which it
asserted did not exist).

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
under Option A, since it writes both the threshold and its base; under B the interaction has to be
specified rather than inherited.

## 8. Blast radius — and the gate collision that rules out A's constant

The symmetry with the last default change is the thing to hold onto. `gc-generational-v0.md` §5.3
justified the factor 2.0 → 3.0 on the workloads *above* the floor, and recorded that the
floor-pinned four — nqueens, http ×2, math — were *"unchanged by construction."* **A floor change
has the inverse blast radius**: it moves precisely those four. None of §5.3's evidence transfers.

**`just gc-adapt-check` Property 1 fails at the constant #407 needs.** Property 1 runs
`test_gc_age_retain_all` at the default, F=2 and F=3, and asserts `def_cyc < f2_cyc` and
`f3_marked × 100 < f2_marked × 70`. §13.3 gives that fixture's live set as 62,007 at F=2 and 52,308
at F=3, so the factor stops having any effect once the floor exceeds `62,007 × 2 ≈ 124,000`: all
three probes pin to the floor, the cycle counts equalise, and both assertions go red.

Putting numbers on the window:

| floor | Property 1 | compiler (66,955 live × 3 ≈ 201k) | delivers #407's win? |
|---|---|---|---|
| 4,096 (today) | green | unpinned | no |
| < ~124,000 | green | unpinned | no — far below the ~230k the game reclaims per cycle |
| ~124,000–201,000 | **red** | unpinned | no |
| ≥ ~201,000 | **red** | **pinned** | at 232,000, yes |

**There is no floor that both delivers #407's result and leaves the existing gate and the compiler
alone.** That is the concrete reason Option A is not recommended, and it also corrects this doc's
earlier claim that a floor change "leaves the compiler-class workloads alone" — at 232,000 the
compiler is inside the blast radius, not outside it.

| gate | effect |
|---|---|
| `just gc-adapt-check` | **Property 1 red above ~124k** (above). Property 2, which asserts floor-pinned inertness on `test_gc_age_retain_none`, still passes |
| `just gc-ageprof-check` | `test_gc_age_retain_none` allocates 480,000 objects total; a six-figure floor gives it a handful of cycles instead of ~118, so its ratio guards go degenerate, not merely recalibrated. Commit `88fcd286` retuned this fixture once already, and §5.3 warns *"re-mark ratios are only comparable at equal collection frequency"* |
| `just test` | full suite (Definition of Done #5) for any implementation |
| `just run-example-canary` | required — runtime edit (Definition of Done #11) |
| `just linux-smoke` | recommended before pushing; heap sizing is exactly what diverges by allocator |
| `just rooting-cost-gate` | prices the compiler via `arena_bytes` and `gc_swept`; `gc_swept` is floored only |
| golden IR | **not affected** — no emitted IR changes |

## 9. Measurement plan

**This precedes choosing an option, not implementing one.**

1. **Measure work-per-garbage directly** (§3.1), because every option targets it and nothing reports
   it. One unconditional counter in the sweep's existing slot walk, logged beside `swept`. Report
   it for the compiler, the floor-pinned four, `retain_all`/`retain_none`, and the game. **This is
   the cheap experiment that decides between B and nothing**, and it also falsifies §3.1's
   unestablished sparse-region mechanism: check the ratio against region occupancy.
2. **Falsify the floor prediction on dense heaps.** §4 predicts nqueens, http ×2 and math are
   flat-to-worse under any raised floor. Predictions written down *before* measuring; if they are
   wrong, §4's condition is wrong and B's rationale weakens with it.
3. **The compiler** (`ast_to_ir.sprout` emit, 3 reps interleaved), in `gc-generational-v0.md`
   §5.3's table format so the two changes stay comparable. The RSS side is the risk: −19% wall for
   +18% RSS was accepted once; nothing here should spend that budget again.
4. **The reporter's workload** — `uncharted-suns` `game/app.sprout`, both scenes.

**Report both axes on every row.** #407 exists because a change was evaluated on pause alone.

## 10. Tests

- **Regression test, written first and confirmed RED** (Definition of Ready #3): a fixture that
  holds allocation volume fixed and asserts a bound on work-per-garbage or cycle count. Today's
  runtime fails it; a correct fix passes.
- **`gc-adapt-check`**: whatever lands, §8's window means the gate needs revisiting rather than
  re-running. If a floor rises past ~124k, Property 1 must be re-expressed — for example by pinning
  `SPROUT_GC_THRESHOLD` inside the probe so the factor is exercised independently of the default.
- **Coverage gap closed** (Definition of Ready #4): no existing test asserts anything about
  collection frequency or sweep productivity as a function of heap shape; every GC gate today keys
  on cycles, marked and freed at one configuration.

## 11. Docs, spec and backlog

- Not spec-affecting. `docs/spec-v0.md` is silent on collection policy and should stay silent.
- **`gc-generational-v0.md` §11.4 stays as written.** A guard that gets amended every time a case
  clears it stops being a guard; this doc is the cited exception, and §4 explains what made it one.
- The `BACKLOG.md` `P1` galaxy-game entry is corrected in the same change that adds this doc: it
  recommended shrinking the live set, that route was taken, and #407 is the result.
- On implementing any option, delete the `BACKLOG.md` entry this doc's work becomes.

**Found in passing, filed separately.** The GC cycle timer measures elapsed time with the wrong
clock: `sprout_gc_collect_with_reason` brackets the collection with `sprout_now_micros()`, which is
`gettimeofday`/`CLOCK_REALTIME`, while that function's neighbour in the same file documents the rule
it breaks — *"NOT monotonic […] must not be used for elapsed-time measurement (use
`time_now_micros` for that)"*. Those two calls are its only elapsed-time uses, so the fix is one
call site. Scope it honestly: it removes NTP and clock-change artifacts from `SPROUT_DEBUG_GC`'s
`elapsed_us` and does **not** explain §13.4's pause tail, since both clocks count descheduled time.

## 12. Open questions

1. **Does Option B's ratchet terminate?** The exact-fit-freelist feedback loop in §6.2 is the one
   question that decides whether B is buildable, and it is analysable without writing it.
2. **What conversion does B use between slots and objects**, and what is that estimate's error
   under a mixed-size heap?
3. **Is §3.1's sparse-region mechanism real?** The arithmetic does not currently fit (§3.1's third
   caveat). §9 item 1 answers it.
4. **Does anything below the floor deserve the old behaviour?** A genuinely tiny program now pays a
   six-figure minimum heap under any of these. Go's answer is that 4 MiB is small enough not to
   matter; Sprout's floor is in objects, so the equivalent claim is not automatically true (§6.3).

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
