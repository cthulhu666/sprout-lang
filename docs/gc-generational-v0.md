# Generational GC for Sprout — measurement and decision (v0, 2026-08-09)

**Status:** exploratory. Records a measurement and a recommendation; no collector
change is proposed here. Normative GC behaviour remains
[docs/compiler-internals.md §Non-moving GC](compiler-internals.md).

§1–§11 are about the nursery: whether to build one, and for what. **§12 answers a
separate question — why the collector is not switchable per workload — and §13 measures
pause rather than throughput**, which is the axis §12 identifies and the only one where
the intended workloads have any exposure. Size-class reuse and coalescing are a third
question, in [gc-size-classes-v0.md](gc-size-classes-v0.md). **Whether the pause §13
measures can be spread across frames instead of removed** is a fourth, in
[gc-frame-budget-v0.md](gc-frame-budget-v0.md).

## 1. Problem statement

GC is ~59% of self-hosted compile time (mark 42% / sweep pass 1 32% / pass 2 0.1%,
after the pass-3 removal). Tuning is exhausted: `SPROUT_GC_THRESHOLD=2000000` buys
−17% time for **+50% RSS**. BACKLOG:283 proposes a bump-region nursery as the next
structural lever, with "primary target: nqueens-class workloads".

Every figure behind that plan came from **one workload** — the self-hosted compiler.
This document measures the nursery's ceiling across seven workloads before anything
is built.

## 2. Goals / non-goals

**Goals.** Bound the mark work a minor collection could skip, per workload. Price the
write barrier a generational collector would need. Resolve BACKLOG:283's
barrier-surface contradiction. Decide whether to build a nursery, and for what reason.

**Non-goals.** Building the nursery, any write barrier, or changing the base
allocator. Anything moving — non-moving is load-bearing (§5).

## 3. Prior-art survey (primary-sourced)

Every row verified against the implementation's own reference; URLs in §9.

| Runtime | Young generation | Shape |
|---|---|---|
| **OCaml 5** | yes | "Each domain has its own domain-local minor heap arena into which new objects are allocated without synchronising with the other domains"; minor collection is a stop-the-world section in which all domains collect in parallel; major heap concurrent mark-sweep |
| **GHC** | yes | Generational copying, `-G2` by default ("The default of 2 seems to be good"); the allocation area `-A` is "generation 0 step 0", divisible into per-processor chunks with `-n`; `-qg` selects which generations use parallel GC |
| **Erlang/BEAM** | yes | "per process generational semi-space copying collector using Cheney's copy collection algorithm"; private per-process heaps, young + old heap split by a high-watermark; one process's GC does not affect another's |
| **Java ZGC** | **added** it | Generational ZGC implemented in JDK 21 (JEP 439), default in JDK 23 (JEP 474); JEP 490 is titled "ZGC: Remove the Non-Generational Mode". Motivation is the weak generational hypothesis: ZGC "must collect all objects every time it runs" |
| **Go** | **no** | Non-moving concurrent mark-sweep, size-segregated spans, no nursery |

**Go is the informative dissent.** Its stated reason: *"It isn't that the generational
hypothesis isn't true for Go, it's just that the young objects live and die young on
the stack… escape analysis is picking up a lot of those objects and sticking them on
the stack — objects that the generational collector would have helped with."* That
reason **does not transfer to Sprout**: there is no escape analysis and no stack
allocation, and `docs/linear-borrowing-v0.md` lists governing raw heap memory as an
explicit non-goal ("The GC owns allocations; borrowing here conserves **logical
resources** (sockets), never memory"). Sprout's allocation profile is GHC's and
OCaml's, not Go's.

Go's *other* lesson does transfer: *"The write barrier was fast but it simply wasn't
fast enough… The write barrier costs are constant so the cost of increasing the heap
size will drive that marking cost underneath the cost of the write barrier."* §6
prices that for Sprout instead of assuming it.

**The constraint that picks Sprout's design.** `docs/compiler-internals.md` makes
non-moving load-bearing: rooting pushes an `i64` into an alloca and never reloads it,
so a moving collector is "a sweeping rewrite affecting `ast_to_ir.sprout`,
`ir_lowering.sprout`, and `ir_rooting.sprout`". Sprout's only option is therefore
**in-place, sticky-mark-bit** generational — young objects keep their address and
promotion is a bit flip. That is a published, implemented design (Demers et al.;
Immix §5.3; MMTk's StickyImmix; Nofl/Whippet).

**And the literature's warning about exactly that design.** Immix (PLDI 2008) §5.3
evaluates sticky-mark-bit generational on two bases and finds:

> "G|IX-IX almost uniformly improves over IX. **However, G|MS-MS does not improve
> sufficiently over MS to justify its use, given the option of a regular copying
> generational collector.**"

> "While the mark-sweep in-place collector is 'interesting' … **changing the base from
> mark-sweep to immix transforms the idea into a serious proposition** for a
> performance-oriented setting."

Sprout's base — size-class freelists over 1 MiB regions — is `MS`, not `IX`.

## 4. Instrument

`SPROUT_GC_AGEPROF=1`, runtime-only, no build flag (unlike `-DSPROUT_GC_PROFILE`,
which needs a special build and over-reports GC by ~2.3×).

- **Age** lives in header bits 9–13 (the gap between the colour bit and aux, which
  `sprout_hdr_make` shifts to bit 14): a saturating 5-bit count of collections
  survived, bumped in the same store that clears the colour bit in sweep pass 1, so it
  costs no extra traversal or write. Reset to 0 whenever a slot is re-initialised.
  These are the same bits a real generational collector would use, so the instrument
  is a dry run of that mechanism.
- **Counters:** `marked_by_age[]` at the single mark choke point (`gc_mark_enqueue`),
  `freed_age0`/`freed_total` in the sweep's dead branch, `live_by_age[]` per cycle, and
  `mut_calls`/`ptr_stores`/`old_to_young` at the mutation primitives.

**Why `mut_calls` is separate from `ptr_stores`:** without it, a zero cannot be
distinguished from a hook that never ran — "a barrier would be free here" and "we did
not measure this" are different conclusions. §6 turns on that distinction.

**Validation.** Two synthetic workloads with known answers,
`tests/stdlib/test_gc_age_retain_{all,none}.spr`, gated by `just gc-ageprof-check`:
retain-all must report a HIGH re-mark ratio (≥70%, measured 73%) and retain-none a LOW
one, with ≥40pp separation. A stuck, inverted, or live-vs-marked-confused counter fails.
The runtime additionally aborts if `live_by_age` disagrees with `g_managed_heap_count`,
which is computed by different code from different state.

**The retain-none bound is a floor-corrected count, not the raw ratio.** A permanently
rooted global is re-marked every cycle, so it contributes one age≥1 mark per cycle to a
workload that retains nothing — and this fixture marks only ~6 objects per collection, so
each root moves the raw ratio by ~12pp. It read **0%** when the ≤15% bound was set, 12%
once `pow10_exact_unit` became a rooted global, and 24% at `list_builder_empty`, failing a
bound that nothing had regressed against: same cycle count, same `freed_total`, 99% still
dying young. The gate now reads the root count off age bucket 30 — a rooted object is
marked once at every age, so buckets 1..30 each hold exactly that count — subtracts
`roots × (cycles-1)` from `marked_age_ge1`, and bounds the remainder (≤5%) and the
separation. Three checks keep the subtraction honest: buckets 1..30 must really be flat
(a churn object that starts surviving breaks flatness instead of hiding in it), the floor
itself is bounded (≤8, so a steady-state leak cannot be absorbed into it — the histogram
alone cannot tell one from a rooted global), and retain-none must run >31 cycles for
bucket 30 to be reachable at all.

## 5. Measured — the nursery's ceiling is a compiler fact, not a general one

`marks` counts objects marked across the whole run; `ge1%` is the share of that
marking spent on objects that had already survived a cycle — the **upper bound** on
what a minor collection could skip.

| workload | cycles | marks | **ge1%** | freed slots | died young | mut calls | ptr stores | old→young |
|---|---|---|---|---|---|---|---|---|
| compiler (`ast_to_ir.sprout`) | 482 | 32,272,682 | **97%** | 32,243,577 | 97% | 15,746 | 2,051 | 1,855 |
| digit_recognizer | 68 | 305,440 | **86%** | 309,856 | 86% | 10,584,110 | **0** | 0 |
| `gc_roots` (game-tick shape) | 5,807 | 407,377 | **87%** | 23,375,910 | 99.8% | 0 | 0 | 0 |
| http_log_middleware | 37,567 | 871,652 | 48% | 153,000,085 | 100% | 0 | 0 | 0 |
| nqueens | 8,279 | 495,989 | 39% | 33,412,734 | 99% | 0 | 0 | 0 |
| astar | 157 | 440,989 | 17% | 536,824 | 31% | 53,300 | 0 | 0 |
| **http server** (real sockets, `serve_n` + `wrk`, 3,998 requests) | 308 | 11,791 | **13%** | 1,247,810 | 99% | 0 | 0 | 0 |
| math_transcendental | 1 | 0 | n/a | 95 | 100% | 0 | 0 | 0 |

Read the `marks` column with the ratio, not instead of it. The compiler marks
**32.3M** objects; the HTTP server marks **11.8k** — about **38 objects per
collection**. nqueens marks 60 per collection, http_log_middleware 23. **On every
workload except the compiler, marking is already nearly free, so a high ratio would
have bought nothing and the low ratios cost nothing.**

At 38 marks per collection the permanently rooted globals are themselves a few points of
the server's ratio — one root contributes one age≥1 mark per cycle whatever else runs — so
the low figures here are ceilings on a churn ceiling, and the conclusion holds with room to
spare. §4's retain-none note has the mechanism.

`gc_roots` (2026-09-20) is the case that makes the distinction sharpest, and it was
added because a game simulation tick was reported as GC-bound. Its ratio is high —
87%, second only to the compiler — and its mark pool is **70 objects per collection**,
the same order as nqueens. The 87% is one roster of ~61 long-lived records re-marked
5,807 times; the 23.4M dead slots are never marked at all, because the collector is
non-moving and sweeping them is not marking (§5.2). A nursery would skip those 61 and
change nothing measurable. What the workload was actually spending on was the root
stack — 47% of top-of-stack samples, now 37% faster at the same allocation rate
(bench/results-2026-09-20-gc-root-stack.md). **A high `ge1%` is a licence to look, not
a finding; multiply it by `marks/cycles` before believing it.**

### 5.1 GC is not the bottleneck outside the compiler

Two checks, both negative:

- **Raising the threshold does nothing.** nqueens 2.60s → 2.63s (×8) → 2.65s (×64)
  while peak RSS goes 6 MB → 17 MB → 125 MB; http_log_middleware 4.98s → 4.97s →
  5.20s, 4 MB → 8 MB → 43 MB. 64× fewer collections, no speedup.
- **Disabling the collector makes nqueens *slower*:** 2.50s → **2.72s**, peak RSS
  6 MB → 970 MB. Reusing hot memory beats allocating fresh pages, so GC's whole
  contribution there is at or below zero.

The adaptive threshold explains the shape: it is `max(4096, live × adapt_factor)`, and with
~23–60 objects live it sits at the floor forever — maximum collection frequency, minimum work
per collection. That is cheap, not expensive. (The factor default was 2.0 when this table was
measured and is 3.0 as of the follow-up below; these workloads are floor-pinned either way, so
none of the numbers above move.)

### 5.3 What the measurement *did* justify: the adapt-factor default

The same data that ruled the nursery out for these workloads argued for a one-line change.
Because the threshold is floored at `SPROUT_GC_THRESHOLD` (4096), the factor is inert for any
program whose live set is under ~2048 objects — i.e. four of the seven workloads above. Only the
compiler (66,955 objects live per collection, from `marked_total / cycles`), `digit_recognizer`
(4,492) and `astar` (2,809) are affected at all.

Measured before raising the default from 2.0 to 3.0:

| workload | time | peak RSS |
|---|---|---|
| compiler emit (`ast_to_ir.sprout`, 3 reps interleaved) | 2.19s → **1.88s** (−14%) | 90 → 101 MB (+13%) |
| compiler-test emit (recorded earlier, BACKLOG) | **−19%** | +18% |
| `digit_recognizer` | 0.53s → 0.53s (**flat**) | 6.15 → 7.55 MB (+1.3 MB) |
| `astar` | 0.03s → 0.02s (timer floor) | 4.4 → 4.4 MB |
| floor-pinned (nqueens, http ×2, math) | unchanged by construction | unchanged |

The predicted risk was that raising the factor would amplify the byte-blind trigger
(BACKLOG:1380) on `MutVec`-heavy code, retaining megabytes of `malloc`'d backing arrays invisible
to `g_managed_heap_count`. **It did not**: `digit_recognizer` pays +1.3 MB. Its 64→24→10 model
routes 10.6M scalar stores through a *handful* of long-lived small matrices, so its 4,492 live
objects are small ADT nodes, not big buffers. The amplifier needs many large *retained* vectors,
which no current workload has — it remains a live concern for future large-buffer churn, though
inlining shrank its surface to vectors past 508 elements or grown by a push (§11).

The mechanism, isolated on `test_gc_age_retain_all` (150k-node live chain):

| factor | cycles | marked_total | freed_total |
|---|---|---|---|
| 2.0 | 9 | 558,057 | 420,043 |
| 3.0 | 6 | 313,843 | **420,043** |
| 4.0 | 5 | 236,020 | **420,043** |

`freed_total` is identical while marking drops 44%. Sweeping is driven by how much garbage
*exists* (a workload property); marking by how *often* you look (a policy property). The factor
touches only the second, so nothing is deferred — only batched. The win is sublinear (F=4 gives
−29% for +38% RSS, worse than 1:1) because sweep pass 1 walks every slot: fewer collections, but
each sweeps a larger heap. 3.0 is the knee; `just gc-adapt-check` pins both the default and the
floor-inertness property.

#### The knob ate half the nursery's upside

Raising the factor and building a nursery attack **the same waste**, so they do not compose
additively. Re-measuring the compiler emit at both factors:

| factor | cycles | marked_total | age ≥ 1 |
|---|---|---|---|
| 2.0 | 482 | 32,272,682 | 96.7% |
| 3.0 | 252 | **16,199,233** | 94.1% |

The *ratio* is nearly unchanged — §5's 97% headline stands — but the absolute pool a minor
collection could skip fell from **31.2M re-marks to 15.2M**. Halving the collection count halves
the re-marking, because re-marking *is* per-collection work. So the one-line default change
captured roughly half of what the nursery was being proposed to capture, at none of the cost, and
the remaining prize is correspondingly smaller. This makes §8's recommendation stronger, not
weaker: measure again before building, and price the nursery against the 15.2M figure.

The same effect recalibrated the instrument's own gate (`retain_all` fell 73% → 52%), so
`gc-ageprof-check` now pins `SPROUT_GC_ADAPT_FACTOR=2` — it validates the counter, while
`gc-adapt-check` validates the policy. Any future factor change should re-read this section:
**re-mark ratios are only comparable at equal collection frequency.**

**So the "GC is 59%" finding is a property of the self-hosted compiler**, whose live
set is 85% immortal AST/IR, and it does not generalise. BACKLOG:283's "primary target:
nqueens-class workloads" is contradicted by the data.

### 5.2 Non-moving is what caps the churn workloads

For nqueens / http, the collector's work is proportional to **garbage**, not survivors:
each cycle walks ~4096 slots to reclaim ~4073 dead objects. A non-moving nursery cannot
change that — it must still visit each dead young slot to free it and rebuild freelists.
A **copying** nursery can: survivors are evacuated and the region's bump pointer is
reset, so cost becomes proportional to survivors. That is the concrete thing Sprout
gives up for the stable-address invariant of §3 — and, per §5.1, on these workloads it
currently costs nothing worth reclaiming.

## 6. The write barrier is nearly free — but only if it is typed

- The compiler, the one workload that would benefit, performs **15,746** mutation
  calls in total, of which 2,051 store a heap pointer and **1,855** would be recorded.
  A remembered set of that size is trivial; Go's "barrier too expensive" outcome does
  not reproduce here.
- `digit_recognizer` is the opposite and the reason `mut_calls` exists: **10,584,110**
  mutation calls, **zero** storing a heap pointer. Every value is an unboxed
  `Double`. A naive barrier on `vector_mutset` would fire 10.6M times and record
  nothing.

**Design consequence:** the barrier must be emitted only for **pointer-typed** element
stores, which the compiler knows statically — not unconditionally inside the runtime
primitive.

## 7. Barrier surface — BACKLOG:283's "correctness crux", resolved

BACKLOG:283 says the barrier goes in `ref_write` **+** `vector_mutset`; BACKLOG:1355
says the remembered set is "populated only in `ref_write` (the sole mutation
primitive)". Enumerated:

- **Barrier sites: `ref_write`, `vector_mutset`, and `vector_push`** (all in
  `sprout_runtime.c`). Indexed `MutVec`/`MutMatrix` writes route through
  `vector_mutset` — `mutvec_set` calls it, `mutmatrix_set` calls `mutvec_set`, and
  the fused `mutmatrix_row_sub_scaled_go` calls it directly. Appends route through
  `vector_push`, which stores a pointer into an already-allocated `VectorVal`
  exactly as `vector_mutset` does. **BACKLOG:283 is right about the first two and
  BACKLOG:1355 is wrong.**

  > **This list was wrong for one commit, and the way it went wrong is the point.**
  > Until 2026-08-15 it enumerated only the first two sites and closed the argument
  > with "`stdlib/mutable.sprout` declares no writing externs of its own, so there
  > is no bypass". Landing growable `MutVec` added `vector_push` to that module and
  > silently falsified the justification — nothing checks it, so the sentence went
  > on reading as verified. An implementer who had built the barrier from this
  > section in the interval would have shipped a nursery that frees live young
  > objects reachable only through a pushed slot. **Before implementing §8 step 3,
  > re-derive this list from the runtime rather than trusting it**, and treat
  > mechanising the check as part of the work: grep `sprout_runtime.c` for every
  > non-static function that writes into an existing object's payload
  > (`v->data[...] = `, `->value = `, and friends) and confirm each either carries
  > the barrier or is provably persistent.
  >
  > **`vector_push` gained a second thing to reason about on 2026-09-25.** Small
  > vectors keep their elements inside the `VectorVal`'s own GC slot, so the first
  > push past an inline capacity *moves the element storage out of the object* —
  > copies it to a malloc block, repoints `->data`, and hands the vacated tail back
  > to the arena as a free slot. For a card-marking design that is not a detail: a
  > card covering the `VectorVal` covers its elements before the growth and not
  > after, and the card that covered the vacated tail now covers a free slot. The
  > store itself is still the barrier site; where the stored-into memory *lives* is
  > no longer fixed for the object's lifetime.
- **Writes into a live object, but not a barrier site: `vector_truncate`**
  (added 2026-09-09, backing `mutvec_remove`/`truncate`/`clear`). It stores into an
  already-allocated `VectorVal` — `data[i] = 0` over the vacated slots, then a lower
  `->len` — so the grep above finds it, and it is listed here so the next reader does
  not have to re-derive why it is absent from the line above. A NULL store creates no
  old-to-young edge, and the `->len` write only *narrows* the scanned range. What a
  card-marking or incremental design must still reason about is that second point: the
  collector derives a vector's child count from `->len`, so this call retracts children
  from an object mid-mutator, and the slots it retracts are zeroed rather than left
  stale.
- **Not barrier sites — the scheduler's stores into task structs**
  (`r->chan_pending`, `st->chan_pending`, `self->chan_pending`, `t->result` in
  `runtime/sprout_scheduler.c`). They are rooted by address (`/* rooted via
  &r->chan_pending */`) and `sprout_gc_collect` scans every registered per-task root
  context, so a minor collection visits them regardless of generation.
- **Persistent, so no barrier:** `vector_set`, `map_set`, `native_set_insert` and the
  string `Builder` path all return new objects rather than writing into existing ones.

## 8. Recommendation

**Build the nursery only as a compiler/self-hosting optimisation, and say so.** The
evidence for it is narrow but strong: 97% of 32.3M marks are re-marks of objects that
already survived, and the barrier costs ~1,855 recorded stores for the whole
compilation. The evidence against it as a general feature is equally clear: on the
anchor use case it would skip 13% of 11.8k marks, and GC is not that workload's cost.

Sequencing, if it goes ahead:

1. Generation-scoped freelists first (recorded under the landed staging entry) — a
   minor collection that still rebuilds the whole heap's freelist is not proportional
   to the young set.
2. Sticky-mark-bit promotion using bits 9–13, reusing this instrument's age field.
3. A **typed** barrier (§6) at the sites in §7 — **re-derived from the runtime, not
   read off that list**, per the warning there — with the remembered set shaped
   **per-domain from day one** — BACKLOG:1355 proposes one global fixed-size array,
   while tier-2 share-nothing multicore is the declared direction
   (`docs/concurrency-design-exploration-2026-07-13.md`) and both Erlang and OCaml 5
   key the young generation per process/domain.

Do **not** expect it to move nqueens, astar, or HTTP throughput. If those need to get
faster, §5.1 says look outside the collector.

### Concurrency outlook

The nursery survives every tier of the declared plan, and is the enabling structure
for tier 2: green-threaded single-OS-thread today (stop-the-world minor GC, no atomics
in the barrier); share-nothing multicore next, which is precisely how Erlang and
OCaml 5 are built; and shared-memory parallel later, where generational and concurrent
compose (Generational ZGC, G1). It is not throwaway work under any tier.

## 9. Sources

- Go GC (Hudson, ISMM 2018 keynote) — https://go.dev/blog/ismmkeynote
- OCaml 5 parallelism manual — https://ocaml.org/manual/5.2/parallelism.html
- GHC RTS options (`-A`, `-G`, `-n`, `-qg`) — https://downloads.haskell.org/ghc/latest/docs/users_guide/runtime_control.html
- Erlang/BEAM garbage collection — https://www.erlang.org/doc/apps/erts/garbagecollection.html
- JEP 439 Generational ZGC — https://openjdk.org/jeps/439 · JEP 474 — https://openjdk.org/jeps/474 · JEP 490 — https://openjdk.org/jeps/490
- Immix, Blackburn & McKinley, PLDI 2008 (§5.3 sticky mark bit) — https://www.steveblackburn.org/pubs/papers/immix-pldi-2008.pdf
- MMTk (GenImmix / StickyImmix) — https://www.mmtk.io/status
- Nofl: A Precise Immix — https://arxiv.org/html/2503.16971

## 10. Open questions

- **The HTTP server measured 197 req/s** (3,998 requests in 20.2s, `wrk -t2 -c40`),
  against 5,612 req/s recorded in `bench/results-2026-07-19-http-log-middleware.md`
  for the CRUD server. ~5 ms/request suggests a fixed delay rather than compute. Not
  investigated — it does not affect the counters, which are counts — but a 28× gap
  is worth a look on its own.
- Whether moving the base toward non-moving mark-region (line marks + bump allocation
  into partially-free regions, cf. Nofl) is worth it for the compiler *instead of* a
  nursery. It would also address the 34% of pass-1 slot-steps that step over
  already-FREE slots, which a nursery leaves untouched. Immix §5.3 implies the two
  compose better than either alone.

## 11. The trigger is object-count-blind — first measured instance (2026-09-06)

`sprout_gc_maybe_collect_threshold` fires on `g_managed_heap_count >= g_gc_threshold`,
and the count increments by exactly 1 per managed object regardless of size. Many-small
allocations over-collect, few-but-large under-collect. The `adapt_factor` default of
3.0 amplifies it: the garbage budget between collections is `(factor − 1) × live`
*objects*, so a workload retaining large invisible payloads tolerates twice as many.

**Scope narrowed 2026-09-25.** A vector of up to 508 elements now carries them inside its
own slot, so those bytes are counted; only a longer or push-grown vector keeps an
invisible `malloc` buffer. The size-blindness itself is untouched — a 508-element vector
and a 1-element one still count 1 apiece.

### 11.1 The gap is ~100,000×

Compiling one function holding a 1,600-element `Vec Int` literal peaks at **3,188 MB
RSS to produce 767 KB of output**, while the collector reports the live set as 71,178
objects / 31.6 KB of strings. Scaling is clean quadratic in emitted IR bytes
(RSS ≈ 1.0×10⁻⁵ × bytes², ±15% across two program shapes over a 4× size range) and
confined to `emit-ir` — `bundle`/`check`/`lower`/`effects` are flat on the same inputs.

### 11.2 `SPROUT_GC_THRESHOLD` cannot investigate this and will mislead you

It sets only the *floor*, so with an adaptive target already at 7.7M objects, lowering
it changes nothing: 3188 MB → 3189 / 3205 / 3215 MB at 4096 / 512 / 64. That flat
result reads as "the memory must be live" and is worthless as evidence. The knobs that
bind are `SPROUT_GC_ADAPT_FACTOR` and `SPROUT_GC_ADAPT_CAP`.

### 11.3 A count-based cap is NOT the fix — it trades quadratic memory for a livelock

`SPROUT_GC_ADAPT_CAP=50000` collapses peak RSS to 10 MB, proving the garbage is
collectable, but the run never finishes: live (71,178) permanently exceeds the cap, so
every allocation triggers a full mark — 363,713 cycles at ~980 µs, `alloc_since_gc=1`,
`swept=0`, killed at 300 s. This is the concrete argument that the trigger must become
byte-aware rather than merely tighter, and it is a ready-made reproducer. Note the
livelock detector did not abort a textbook livelock.

### 11.4 Compensating the floor for a known object-count change is NOT the fix either

Measured 2026-09-25 while an intermediate design added one managed object per `vec_set`.
That raised nqueens' collection count 8,279 → 12,495, which looked like the whole cost of
the change. Raising `g_gc_threshold` 4096 → 6144 restored the cycle count to **exactly**
8,279 and bought **0.4 ms of 204** (203.8 vs 204.2 at N=12).

The lesson generalises past that design: collection *frequency* was not what the extra
objects cost — sweep *volume* was, and the sweep is proportional to objects, which a
threshold cannot change. Reach for the floor only with a measurement that separates the
two, or the tuning looks principled and does nothing.

## 12. Why not a switchable copying / non-moving collector (asked 2026-09-27)

The question: Sprout's intended workloads are a webapp over Postgres, a 3D game, TUI
apps, a chess engine, and compute-heavy programs with some ML — is it worth offering a
copying collector and a non-moving one, chosen per workload? **No**, for two independent
reasons. This section exists so the question does not have to be re-derived.

### 12.1 Those are the workloads §5 already measured

| intended workload | proxy in §5 | cycles | marks | marks/cycle |
|---|---|---|---|---|
| webapp + Postgres | http server (real sockets, `wrk`) | 308 | 11,791 | **38** |
| 3D game | `gc_roots` (game-tick shape) | 5,807 | 407,377 | **70** |
| chess engine | nqueens (search) | 8,279 | 495,989 | **60** |
| compute / ML | digit_recognizer | 68 | 305,440 | 4,491 |
| " | math_transcendental | 1 | 0 | — |

Three of the five mark under a hundred objects per collection, and §5.1 closes it from
the other side: raising the threshold 64× changes nothing (nqueens 2.60s → 2.65s), and
*disabling* the collector makes nqueens **slower** (2.50s → 2.72s, RSS 6 MB → 970 MB).
Where GC's total contribution is at or below zero, no choice of collector improves it.
A copying nursery would make cost proportional to survivors rather than garbage (§5.2)
— real, and worth nothing at 38 marks per cycle. The one workload where GC is 59% is the
self-hosted compiler, which is not on that list, and §5.3 already took roughly half of
its prize with a one-line default change.

### 12.2 The switch costs more than the replacement, because of the rooting ABI

[compiler-internals.md §Non-moving GC](compiler-internals.md) states the constraint:
codegen pushes an `i64` into an alloca and never reloads it, so making the GC moving
requires pairing every root push with a reload after its trigger op — "a sweeping
rewrite affecting `ast_to_ir.sprout`, `ir_lowering.sprout`, and `ir_rooting.sprout`".

A **runtime** flag cannot straddle that. If the collector *might* move, emitted code must
reload unconditionally, so every program pays the moving-GC codegen tax — extra loads, no
holding a heap pointer in a register across an allocation — including the workloads that
would never enable it. The flag is not free for the side that does not use it.

A **build-time** switch with two codegen modes avoids the tax and duplicates everything
downstream: golden IR, the smoke shapes, and `bootstrap/compile_driver.ll`, a committed
seed that would become mode-specific with `just verify-bootstrap-fixed-point` required to
hold in both. Two compilers, to serve workloads that measure GC at zero.

### 12.3 GHC can offer the flag because its default runs the other way

GHC's default is `--copying-gc` ("uses the generational copying garbage collector for all
generations"); `--nonmoving-gc` is the opt-in addition, sold on latency rather than
throughput — copying "can cause long pauses in execution during major garbage
collections", so the non-moving mode lets oldest-generation collection "proceed
concurrently with mutation". GHC pays the moving-collector codegen cost unconditionally,
which is what makes the non-moving mode cheap to bolt on. Sprout would have to adopt
GHC's baseline to earn GHC's flag: the switch runs downhill there and uphill here.

Note also what GHC's axis is not. It is throughput versus pause time, not
workload-shaped collector selection.

### 12.4 The axis that would matter is pause, and the mechanism to watch is the sweep

For a game the risk is the worst pause inside a frame, not throughput. The mechanism to
watch is not that the collector does not move — it is that **sweep pass 1 walks every
slot in every region**, which §5.3 measured directly (raising the adapt factor gives
sublinear wins because "fewer collections, but each sweeps a larger heap"). At a large
heap that is pause proportional to heap size on every collection, independent of how
little is live, and a copying nursery does not fix it either: the old generation is still
swept.

This is measurable today with no new instrument — `SPROUT_DEBUG_GC=1` logs `elapsed_us`
per cycle. §13 does it.

## 13. Measured — pause, not throughput (2026-09-27)

§12.4 said the axis worth measuring is pause and that no new instrument is needed. This
is that measurement. Harness: `bench/gc_pause/bench.sh`, which runs the workloads under
`SPROUT_DEBUG_GC=1` and summarises the `elapsed_us` the runtime already logs per cycle.
macOS arm64.

**Every cell is the minimum of that statistic over five runs**, and the reason is
§13.4: a single run measures the machine's load as much as the collector. Two earlier
drafts of this section were discarded for that — the first written from single runs,
where re-running the same binaries moved `gc_roots`'s p99 from 28 µs to 514 µs with
every GC counter identical, and the second from three runs, which still had one
configuration reported twice at 250 µs and 67 µs. Load can only add time, so the
minimum over repetitions is the statistic that converges; at five the two tables below
that measure `gc_roots` at the default threshold agree to within 1 µs. This answers
"how cheap does this configuration get", which is what a cost model needs, and it is
**not** a worst-case pause — §13.4 says why that is not available here at all.

### 13.1 Where the workloads sit today

| workload | cycles | p50 µs | p95 µs | p99 µs | live | swept |
|---|---|---|---|---|---|---|
| `gc_roots` (game tick) | 5,591 | 21 | 25 | 28 | 76 | 4,019 |
| http_log_middleware | 30,947 | 25 | 29 | 32 | 23 | 4,070 |
| nqueens (search) | 8,279 | 39 | 47 | 151 | 59 | 4,035 |
| digit_recognizer (ML) | 39 | 383 | 458 | 495 | 4,485 | 8,994 |
| `test_gc_age_retain_all` | 6 | 742 | 2,178 | 2,384 | 52,308 | 70,340 |

Nothing here threatens a 16 ms frame. The three small-live-set shapes collect in tens of
microseconds; only the 150k-node chain crosses a millisecond, and it is a synthetic
worst case rather than a program.

### 13.2 Pause tracks heap slots, not the live set

`gc_roots` holds ~70 objects live regardless of settings, so raising the GC floor grows
the heap the sweep walks while leaving the mark work alone. Over three decades:

| `SPROUT_GC_THRESHOLD` | cycles | p50 µs | p99 µs | live | swept | ns per slot (p50) |
|---|---|---|---|---|---|---|
| 4,096 | 5,591 | 22 | 29 | 76 | 4,019 | 5.5 |
| 40,960 | 550 | 203 | 225 | 63 | 40,855 | 5.0 |
| 409,600 | 55 | 1,936 | 2,051 | 69 | 408,548 | 4.7 |
| 4,096,000 | 6 | 20,500 | 21,407 | 59 | 3,745,017 | 5.5 |

Ten times the heap, ten times the pause, with the live set flat — **~5 ns per slot**,
constant to ±8% over a 1,000× range. This is the direct confirmation of §5.3's aside:
sweep pass 1 walks every slot, so pause is a function of heap size.

nqueens agrees on the proportionality and not on the coefficient: p50 38 → 7,300 →
79,775 µs at 4,096 / 409,600 / 4,096,000 for a flat ~58 live, which is 9.4 → 17.9 →
21.5 ns per swept slot. The top decade is clean (10× heap, 10.9× pause); the jump is
between the floor and 409,600, where the heap outgrows cache. That reading is
unestablished — what is established is that ~5 ns/slot is a `gc_roots` number and the
proportionality is the general one.

### 13.3 But the live set sets a floor the knobs cannot lower

If pause were only about heap size, shrinking the heap would fix any pause. It does not,
because the live set must still be marked and its slots still walked. Turning the adapt
factor down 4× on the 150k-node chain:

| `SPROUT_GC_ADAPT_FACTOR` | cycles | p50 µs | p99 µs | live | swept |
|---|---|---|---|---|---|
| 1.5 | 14 | 692 | 2,261 | 64,768 | 30,145 |
| 2 | 9 | 694 | 2,381 | 62,007 | 46,893 |
| 3 (default) | 6 | 716 | 2,254 | 52,308 | 70,340 |
| 6 | 4 | 1,030 | 1,818 | 44,032 | 105,510 |

Four times the factor leaves p99 flat — 1.8–2.4 ms across the whole range, with no
trend, while the heap it sweeps grows 3.5×. **The floor is ~35 ns per live object** at
p99 on this pointer-chasing shape (2,261 µs / 64,768), or ~11 ns at p50. Cheaper shapes
do better, so the coefficient depends on object layout and locality, but the form is
fixed: you cannot tune below the live set.

**What that means for a frame budget.** At ~35 ns per live object, a 2 ms slice of a
16 ms frame is spent at roughly **57,000 live objects**, and the whole frame at roughly
**460,000**. A game holding a level's geometry and entities resident is in that range.
Two caveats: a copying nursery does not help, because the old generation is still swept
(§12.4); and the coefficient is shape-dependent and must be re-measured on the real
heap. **§13.6 did that, and the real heap is 2.3× worse.**

### 13.4 The tail is not attributable, and this instrument cannot fix that

`http_log_middleware`'s slowest five collections in one run took 3,105–8,516 µs against
a 33 µs median **with identical `live`, `swept`, `marked` and region counts**. nqueens'
slowest five were 837–1,224 µs against 39 µs, likewise identical. No logged quantity
separates them from the median, and the tail does not reproduce: the same http binary
gave max = 182 µs in one run and 8,516 µs in the next.

On a laptop that is machine noise, page behaviour or preemption rather than collector
work — and it cannot be told apart from a real pause tail, which is why the tables above
stop at p99 and take a minimum across runs. It matters anyway: an 8.5 ms stall is a
dropped frame whatever caused it.

**And there is one independent sighting on real hardware doing real work.**
[green-task-pool-v0.md](green-task-pool-v0.md) measured an HTTP server under load and
recorded "max pause 6.9 ms, 21 ms total across 6 s", correctly dismissing GC as the
cause of an 18–120 ms client-side tail: "an order of magnitude short". That conclusion
stands for the question it answered. But 6.9 ms is *not* short of a 16 ms frame — the
same number that exonerates the collector for a throughput benchmark disqualifies it for
a frame budget, and unlike §13.4's outliers it was not measured on an idle laptop. Two
readings of one measurement, and which one applies depends entirely on the deadline.

Resolving this needs a per-phase timer inside `sprout_gc_collect` and a quiet machine,
filed in `BACKLOG`. **Until then no pause claim past p99 is available from this
instrument** — including a reassuring one.

### 13.5 Verdict on the question that prompted §12

On the **benchmark** shapes in §13.1 the collector is not a pause problem: tens of
microseconds. The exposure is a single mechanism — pause ∝ total slots, floored by the
live set — and it arrives with heap size, not with workload kind. That is an argument
for making the sweep cheaper. It is not an argument for a second collector.

**It is triggered today.** §13.6 measures a real Sprout game at 76,648 live objects and
a 6.1 ms median collection against a 17.1 ms frame. §13.1's `gc_roots` row is not that
game — it holds 70 objects live, and reading it as "the game shape" is what made an
earlier draft of this section conclude the mechanism was untriggered.

### 13.6 The real games, and what they do to §13.1

Everything above is benchmarks. `bench/gc_roots` is described in its own header as "the
shape of a game simulation tick", and it holds **70 objects live**. The galaxy game in
the `uncharted-suns` repo holds **77,653**. Measuring the real programs changes this
section's conclusion, so it is recorded rather than folded in.

**Method.** Each program run under `SPROUT_DEBUG_GC=1`, which the runtime already
supports; no change to the game repo. The client is
`just run-gfx game/app.sprout <catalog>` with `SPROUT_GFX_MAX_FRAMES=1200` — what a
player runs, and what that repo's `perf/baseline.json` measures at 58.45 fps, a
**17.1 ms frame**. Steady state excludes world seeding and the `atexit` cycle.

| program | live | heap slots | regions | p50 | p90 | min | ns/live |
|---|---|---|---|---|---|---|---|
| **galaxy client** (`game/app.sprout`) | **77,653** | 232,990 | 33 | **7,746 µs** | 9,475 | 5,614 | 100 |
| galaxy server (`game/serve_main.sprout`) | 76,648 | 229,944 | 28 | 6,097 µs | 10,720 | 5,365 | 80 |
| chess perft (`tests/slow/test_perft_deep.spr`) | 480 | 4,096 | 1 | 34 µs | 78 | 25 | 71 |
| grimward balance sweep | — | — | — | — | — | — | — |

The client collects roughly once every 17 frames over the run, so about three times a
second a frame carries an extra 7.7 ms on a 17.1 ms budget. Chess is the opposite and
confirms the model from the other end: a compute load with a 480-object live set pays
34 µs, and its per-object cost (71 ns) is in the same band as the game's.

Grimward has no row because `tools/balance_main.sprout` does not compile — `ast_to_ir:
record 'grimward.ward.Ward' has no field 'depths'`. Its `balance` recipe is disabled in
that repo for an unrelated reason, so the breakage is pre-existing and not ours to fix;
it is recorded so the gap in this table is not read as "measured and fine".

**The live set is one data structure.** The per-type census in the same log is flat
across every steady-state cycle:

```
types: obj=1804 closure=3 vec=118 map=75640 ref=0 cstr=111(9.2KB) bytes=1 tuple=58
```

**75,640 of 77,653 live objects — 97.3% — are `map`.** Not churn: the count is identical
cycle to cycle. So the game's GC pause is not diffuse pressure from a big program, it is
one resident map, and §13.3's floor is that map's mark-and-walk cost. That narrows the
fix a long way before any collector work: a structure that large and that static is a
candidate for being held outside the managed heap, or for a representation with fewer
objects in it, and either would move the floor further than anything in §13.5's list.

**§13.3's model held; its coefficient did not.** The predicted 2 ms at ~57,000 live came
from 35 ns/object on `test_gc_age_retain_all`. The real heaps cost 71–100 ns/object, so
77,653 objects produce 7.7 ms — which is what they measure. The form (pause ∝ live, with
a floor no knob lowers) transferred across three independent programs; the constant is
2–3× worse on a real heap, exactly the caveat §13.3 states and an earlier draft of §13.5
then ignored.

**The other reading of this number** — spread the pause across frames rather than remove
it — is worked through in [gc-frame-budget-v0.md](gc-frame-budget-v0.md), which also
decomposes the 7.7 ms into roughly 65% mark and 35% sweep using the adapt factor. Its
recommendation is the same as this section's: shrink the map first.

**Two traps worth naming.** First, the captured log holds **two processes**: the build
step runs the self-hosted compiler, itself a Sprout program with its own collector, so
the log is the compiler's cycles followed by the program's. Split on the cycle counter
resetting — a first pass at this did not, and reported the compiler's distribution as the
game's. Second, the **server is not the client**. They happen to land within 1,000 live
objects of each other here, which makes the mistake invisible if you only measure one;
the client is the one with the frame budget, and it is also the one whose log carries the
type census above.
