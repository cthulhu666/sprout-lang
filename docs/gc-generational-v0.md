# Generational GC for Sprout — measurement and decision (v0, 2026-08-09)

**Status:** exploratory. Records a measurement and a recommendation; no collector
change is proposed here. Normative GC behaviour remains
[docs/compiler-internals.md §Non-moving GC](compiler-internals.md).

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
retain-all must report a HIGH re-mark ratio and retain-none a LOW one (measured 73% vs
0%; the gate requires ≥70%, ≤15%, and ≥40pp separation). A stuck, inverted, or
live-vs-marked-confused counter fails. The runtime additionally aborts if
`live_by_age` disagrees with `g_managed_heap_count`, which is computed by different
code from different state.

## 5. Measured — the nursery's ceiling is a compiler fact, not a general one

`marks` counts objects marked across the whole run; `ge1%` is the share of that
marking spent on objects that had already survived a cycle — the **upper bound** on
what a minor collection could skip.

| workload | cycles | marks | **ge1%** | freed slots | died young | mut calls | ptr stores | old→young |
|---|---|---|---|---|---|---|---|---|
| compiler (`ast_to_ir.sprout`) | 482 | 32,272,682 | **97%** | 32,243,577 | 97% | 15,746 | 2,051 | 1,855 |
| digit_recognizer | 68 | 305,440 | **86%** | 309,856 | 86% | 10,584,110 | **0** | 0 |
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

### 5.1 GC is not the bottleneck outside the compiler

Two checks, both negative:

- **Raising the threshold does nothing.** nqueens 2.60s → 2.63s (×8) → 2.65s (×64)
  while peak RSS goes 6 MB → 17 MB → 125 MB; http_log_middleware 4.98s → 4.97s →
  5.20s, 4 MB → 8 MB → 43 MB. 64× fewer collections, no speedup.
- **Disabling the collector makes nqueens *slower*:** 2.50s → **2.72s**, peak RSS
  6 MB → 970 MB. Reusing hot memory beats allocating fresh pages, so GC's whole
  contribution there is at or below zero.

The adaptive threshold explains the shape: it is `max(4096, live × 2)`, and with ~23–60
objects live it sits at the floor forever — maximum collection frequency, minimum work
per collection. That is cheap, not expensive.

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

- **Barrier sites:** `ref_write` (`sprout_runtime.c`) and `vector_mutset`. All
  `MutVec`/`MutMatrix` writes route through the latter — `mutvec_set` calls
  `vector_mutset`, `mutmatrix_set` calls `mutvec_set`, and the fused
  `mutmatrix_row_sub_scaled_go` also calls `vector_mutset`. `stdlib/mutable.sprout`
  declares no writing externs of its own, so there is no bypass. **BACKLOG:283 is
  right and BACKLOG:1355 is wrong.**
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
3. A **typed** barrier (§6) at the two sites in §7, with the remembered set shaped
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
