# GC stage 2 — promotion age, nursery size, a young-only sweep (v0, 2026-11-04)

Status: steps 1, 2, 2b and 3 of §7 are implemented; the defaults are K = 3, s = 1, c = 2. Step 4
is implemented behind `SPROUT_GC_REGION_LISTS=1`. Stage 1
is [gc-generational-v0.md §14](gc-generational-v0.md); this doc replaces its §14.6.

## 1. Problem

Stage 1 (minor collections, on by default since §14.8 step 6) costs memory and some pauses:
self-compile RSS 1.07×, mutvec_int RSS 1.25× with longer pauses
([results](../bench/results-2026-11-04-gc-minor-pfv.md)). §14.8 blamed both on the minor sweep
walking the whole heap. That is wrong for the memory. There are three causes, not one.

All figures below are counts from one run each (`SPROUT_DEBUG_GC=1`). For these two workloads,
collection, slot and object counts do not change from run to run of one build, because collections
fire on allocation counts and neither program's allocation depends on time. They do change
between builds: the self-compile's peak slots as a ratio to minor-off range 1.07–1.58 across
seven commits at fixed runtime ([results](../bench/results-2026-11-08-gc-nursery-share.md)). That is not shown for the
rest of the §14.8 set; step 2 measures each workload's spread. RSS varies by up to 3% (self-compile on master: 310–319 MB over three runs). No timing
is quoted: load average was 13–21.

**Cause 1: promotion after one survival makes medium-lived data old.** mutvec_int builds a
6,000-cell list per round. A minor that lands mid-round promotes the part already built
(p50 6.4k objects), and that part dies a moment later. A minor cannot free a dead old object,
so it stays until the next major.

| mutvec_int | collections | majors | peak slots | RSS |
|---|---|---|---|---|
| minor off | 926 | — | 35,931 | 12.5 MB |
| minor on (master) | 550 | 198 | 74,532 | 15.7 MB |

**Cause 2: the trigger counts dead old objects as live.** The next collection runs when the heap
count reaches `heap_count × 3` (`sprout_gc_collect_with_reason`). After a minor that count holds
every old object that died since the last major, so the nursery grows with the dead objects.
The 2× major rule reads the same count, so in mutvec_int it fires once the dead old objects equal
the live set: 198 of 550 collections are majors, and each walks the inflated heap.

The self-compile shows the same cause another way. Its footprint depends on when the last major
happens. On master a major at cycle 65 resets the count. With promotion after two survivals the
last major (cycle 61) leaves a baseline of 1.195M; the heap count then tops out at 1.99M, under
2×, so no major follows, and the run ends on a 5.8M threshold computed mostly from dead objects:
peak 7.16M slots against master's 5.64M.

**Cause 3: a minor's cost is its sweep.** Self-compile minors, p50: 13k objects marked, 564k
slots walked, of which 348k were allocated since the last collection. The mark is almost free;
the sweep visits every old and free slot as well as the young ones.

Cause 3 also explains why cause 2 exists. The footprint floor and the `× 3` target amortise a
sweep that costs O(heap) over enough allocation to pay for it. While the sweep walks the whole
heap, a nursery sized independently of the heap makes each minor walk everything for a small
gain. Section 4 measures that.

## 2. Goals and non-goals

- **Goal:** with minor collections on, no workload in the §14.8 set has peak slots more than 5%
  above minor-off, nor peak RSS above it beyond run-to-run spread. Peak slots come first because
  they vary far less than RSS (§1). That holds within one build, not across builds: a ratio
  measured on one tree says little about the next (§7 step 2b). §4's mutvec_int at K = 3 (1.07×)
  missed this.
- **Goal:** p50 and p90 pause no higher than minor-off beyond spread, per workload, read by
  `bench/gc_minor/ab.sh`. K > 1 re-marks young survivors (§4), so this can fail.
- **Goal:** a minor's sweep is proportional to the young set, not to the heap.
- **Goal:** keep the self-compile's CPU and GC-time gain (0.88, 0.68).

  **Decision (step 2b):** the goal moved from 0.80 and 0.52. The K = 1 default measures 0.83 ~ and
  0.52 ~, but its peak slots are 1.07–1.58 of minor-off across seven commits with the runtime
  fixed, so reaching 0.80 failed the peak goal, and its RSS is 1.46. K = 3, s = 1, c = 2 holds
  peak slots at 0.72–0.95 across the same commits and costs CPU: 0.88 ~ and GC time 0.68 ~ over
  10 runs; paired per run, 1.05 and 1.27 of the K = 1 default. It re-marks survivors up to three
  times and runs 1.66× as many collections as minor-off. The first goal puts peak slots first.
- **Goal:** every new invariant has a runtime check under `SPROUT_GC_GENCHECK=1`.
- **Non-goal:** moving objects, a copying nursery, concurrent or incremental marking.
- **Non-goal:** language changes. The spec does not specify the collector, and no Sprout program
  can observe these changes except through memory use and timing.

## 3. Prior art

Read 2026-11-04. §14.3 covers barriers and full-collection triggers; this adds the three
questions stage 2 turns on. JSC (JavaScriptCore's Riptide) is added: like ours it is non-moving
with sticky mark bits.

**Nursery size: what decides when the next minor runs.**

| Collector | Next minor when | Base grows with dead old objects? |
|---|---|---|
| CRuby 3.4 | free slots run out and no page may be added (`heap_prepare`) | yes: dead old slots stay marked |
| Lua 5.4.7 | `genminormul`% (20) of total bytes, measured after the last collection (`setminordebt`) | yes |
| MMTk StickyImmix | lines consumed since the last GC > a proportion of the heap size (`collection_required`) | no: counts allocation |
| Whippet | no hole or empty block left (`gc_allocate_slow`) | yes |
| bdwgc 8.3.0 | bytes allocated ≥ (2 × composite in use + atomic in use / 4 + roots) / 3 (`min_bytes_allocd`) | yes: in-use counts sticky marks |
| JSC | bytes allocated > a budget of at least `m_maxEdenSize`, which is set at the last full GC from its live size (`updateAllocationLimits`) | no |

Lua and Boehm size the nursery from a quantity that includes dead old objects, as we do. JSC
fixes the budget from the live size at the last full collection, which is §5.2's rule. Lua's
manual (§2.5.2) says the base is memory after the last major; the code uses current total bytes.

**Promotion age.**

| Collector | Promoted after | Parent promoted before its child |
|---|---|---|
| CRuby 3.4 | 3 (`RVALUE_OLD_AGE`) | remembered while marking: `rgengc_check_relation` remembers an old parent of a young child |
| Lua 5.4.7 | 2 (`G_NEW → G_SURVIVAL → G_OLD1`) | `markold` re-traverses `OLD1` objects at the next minor |
| MMTk, Whippet, bdwgc, JSC | 1 | cannot happen: the whole young closure is promoted together |

Only the two collectors that keep ages need a rule for a parent that becomes old before its
child; §5.1 takes CRuby's. No source states why its age was chosen.

**Bounding the minor sweep.**

| Collector | A minor sweeps | Free lists across a minor |
|---|---|---|
| CRuby 3.4 | every page, lazily, one bitmap word at a time (`gc_sweep_plane`) | per page, kept |
| Lua 5.4.7 | only the young list segments (`sweepgen`) | none: malloc per object |
| MMTk StickyImmix | every allocated block, per 256 B line (`Block::sweep`) | holes recomputed every GC |
| Whippet | every block not promoted; a block with < 5% holes is promoted and not allocated into until a major (`nofl_should_promote_block`) | none: holes found from per-granule metadata |
| bdwgc 8.3.0 | every block header; > 7/8 full skipped, the rest swept lazily (`GC_start_reclaim`) | cleared every collection, rebuilt lazily |
| JSC | only eden blocks: the eden bits are OR-ed into the unswept bits (`snapshotUnsweptForEdenCollection`); a block becomes eden when the allocator sweeps it into a free list | per block, built when the allocator takes the block |

Only JSC and Lua confine a minor's sweep to young memory. JSC does it per block, with zero cost
per allocation, because its allocator takes one block's free list at a time. Ours takes from
global per-class lists that interleave every region, which is why §5.3 records young slots
individually.

Sources: ruby v3_4_1 `gc/default/default.c`; lua 5.4.7 `lgc.c`, `lgc.h` and manual §2.5.2;
mmtk-core v0.33.0 `src/plan/sticky/immix/global.rs`, `src/policy/immix/{immixspace,block,line}.rs`,
`src/util/heap/gc_trigger.rs`; whippet `f9b54bb3` `src/mmc.c`, `src/nofl-space.h`; bdwgc 8.3.0
(master) `alloc.c`, `reclaim.c`; WebKit `e569bd4c` `Source/JavaScriptCore/heap/{Heap,BlockDirectory,MarkedSpace}.cpp`,
`MarkedBlockInlines.h`.

## 4. Prototype

A scratch copy of the runtime with two switches, measured on mutvec_int and the self-compile.
Every run's output is byte-identical to minor-off, and the self-compile passes
`SPROUT_GC_GENCHECK=1` at K = 2 with N = 1.

- **K, promotion age.** The sweep keeps an object's colour bit (makes it old) only once it has
  survived K collections; a younger survivor is swept back to young and re-marked by the next
  minor. A parent that is, or is about to become, old and has a child that stays young is added
  to the remembered set during the mark (§5.1). Without that rule K = 2 crashed the self-compile
  with a freed object read (`runtime error: non-exhaustive match`).
- **N, nursery size.** N = 0 is master's trigger. N = 1 runs the next collection after
  `max(4096, baseline × 2)` more allocations, where `baseline` is the live count after the last
  major (`g_gen.baseline`) and 2 is the adapt factor minus one.

`walked` is what the sweep visited. `young-only` models the §5.3 sweep: a minor visits the slots
allocated since the last collection plus the young survivors it re-marked; a major, and the
minor just before it (which clears the colour bits, §5.4), visit everything.

| run | collections | majors | marked M | walked M | young-only M | peak slots | RSS MB |
|---|---|---|---|---|---|---|---|
| self-compile, off | 92 | — | 34.60 | 140.92 | 140.92 | 5,290,286 | 298.8 |
| K1 N0 (master) | 77 | 14 | 5.42 | 136.21 | 86.70 | 5,635,942 | 314.3 |
| K1 N1 | 103 | 16 | 7.00 | 147.72 | 89.97 | 4,544,630 | 282.6 |
| K2 N0 | 74 | 11 | 7.81 | 121.70 | 87.59 | 7,157,960 | 376.5 |
| K2 N1 | 104 | 14 | 9.31 | 145.53 | 91.57 | 4,705,389 | 282.0 |
| K3 N0 | 77 | 10 | 9.99 | 116.48 | 86.46 | 6,397,668 | 351.9 |
| K3 N1 | 109 | 13 | 11.51 | 134.12 | 89.62 | 4,581,123 | 271.8 |
| mutvec_int, off | 926 | — | 5.73 | 30.62 | 30.62 | 35,931 | 12.5 |
| K1 N0 (master) | 550 | 198 | 3.38 | 28.34 | 24.78 | 74,532 | 15.7 |
| K1 N1 | 1,076 | 396 | 6.39 | 41.89 | 36.57 | 58,970 | 13.3 |
| K2 N0 | 908 | 70 | 5.11 | 30.90 | 21.06 | 42,366 | 13.0 |
| K2 N1 | 791 | 60 | 5.34 | 26.43 | 21.21 | 43,125 | 12.8 |
| K3 N0 | 926 | 65 | 5.72 | 30.62 | 21.36 | 35,931 | 12.6 |
| K3 N1 | 791 | 60 | 5.34 | 25.79 | 21.14 | 38,329 | 12.8 |

What it shows:
- **No single lever is enough.** N alone fixes the self-compile but doubles mutvec_int's majors
  (396), because promotion after one survival still feeds the 2× rule. K alone fixes mutvec_int
  but makes the self-compile's footprint worse (cause 2). K ≥ 2 with N = 1 puts the self-compile
  below minor-off (peak slots 0.87–0.89×, RSS 282 and 272 MB against 299) and brings mutvec_int
  close to it (peak slots 1.07× at K3, 1.20× at K2; RSS 12.8 MB against 12.5). These are single
  runs; step 2's A/B decides with spread.
- **N needs the young-only sweep.** With today's sweep, N = 1 raises the self-compile's walked
  slots (K2: 122 → 146M) because minors are more frequent and each walks the heap. With a
  young-only sweep K2 N1 visits 92M, 0.67× of master and 0.65× of minor-off.
- **K costs marking.** Re-marking young survivors raises the self-compile's marked objects at
  N = 1 from 7.0M (K1) to 9.3M (K2) and 11.5M (K3), still a third of minor-off's 34.6M.

## 5. Design

### 5.1 Promotion age

The header already keeps a saturating age in bits 9–12 (`sprout_hdr_age_bump`). The sweep keeps
the colour bit only when the survivor's age before this sweep is at least K − 1; otherwise it
clears it and the object stays young. K is `g_gen.promote_age`, fixed at 3 by the A/B (§7 step 2b);
1 reproduces stage 1, and a C test may set it.

**Old→young edges.** Stage 1 empties the remembered set after each minor because no young object
survives one. With K > 1 young survivors exist, and an old object can point at one in two ways:
1. a remembered object whose child was marked and stays young;
2. an object promoted by this sweep whose child stays young. Immutable construction cannot do
   this — a child exists before its parent, so it is never younger — but a store into a young
   object can (`ref_write` on a young `Ref`, which takes no barrier because the target is not old).

Both are caught where the mark scans a parent's children: if the parent is old or will be old
after this sweep (age + 1 ≥ K) and a child will stay young (age + 1 < K), the parent goes into the
remembered set. This is CRuby's rule (§3). It costs one header read per edge scanned, and minors
scan few edges.

Stage 1 can leave the set alone across a colour-clearing sweep because it empties the set before
the drain and nothing adds to it during a collection. This rule adds entries during the drain,
so when the next collection is a major the collector must also clear `REMEMBERED` on every entry
and empty the set before that sweep; the sweep clears only the colour bit. Otherwise the major
leaves a stale set: a dead entry is traced by the next minor (a freed-slot read), and a live one
keeps bit 13 set while old, so the barrier's masked compare never records it again.

**The live prediction.** `sprout_gc_gen_record` predicts the post-sweep count as
`last_live + marked`. With K > 1 a minor re-marks young survivors that `last_live` already
counts, so the prediction becomes `old after the previous sweep + marked`. The old count is kept,
not walked for: a young-only minor adds the slots it promotes, a full walk recounts it, and a
major's sweep that clears colour sets it to 0. GENCHECK's existing count check covers the new
formula, and the premise 2 walk checks the old count.

### 5.2 Nursery size

Step 2's rule (`SPROUT_GC_NURSERY=1`, since removed; §5.5's heap-share rule replaced it): after a
collection, the next one runs after `baseline × (SPROUT_GC_ADAPT_FACTOR − 1)` allocations, with
minor collections on. `baseline` is the live
count after the last major, which no dead object inflates. A major still runs on the 2× rule and
every `SPROUT_GC_MAJOR_EVERY` minors, so a growing live set raises the baseline at each major.
A pinned threshold (`SPROUT_GC_THRESHOLD`, which sets `g_gc_threshold_pinned`) turns the rule
off, as it turns the footprint floor off today.

The footprint floor (`g_gc_footprint_k`) amortises a walk of the free pool. A young-only minor
does not walk the pool, but a major and the minor before it do, and their rate follows the live
count, not the pool's size: a free pool that dwarfs the live set (`gc-trigger-v0.md` §6.2's
adversary) would be walked at every major again. So the floor stays on the collection before a
major and on the major, and `just gc-trigger-check`'s adversary runs in the default mode, which
fails if it does not.

The 2× rule reads the heap count. Reading the old count instead won on no workload in §7 step 2's
A/B, so that switch was removed.

### 5.3 A young-only sweep

A minor must visit exactly the slots that are young: allocated since the last collection, plus
young survivors from earlier ones when K > 1. Free-list reuse puts those slots anywhere in the
heap, between old ones (§14.3's reason Lua's list-prefix bound does not transfer), so the young
set must be recorded at allocation.

Every managed allocation goes through `sprout_gc_alloc_block`, at three points: a large object,
a free-list reuse, a bump. That is the one place to record a young slot.

Candidates:

| | Per allocation | Memory | Minor visits | Changes placement? |
|---|---|---|---|---|
| **A. Young bitmap per region** | find the region and set one bit; bump knows the region, reuse calls `region_find` (the arena chunk index above 8 regions, `region_find_slow`'s search at or below) | 1 bit per 16 B, 1/128 of the heap | the set bits, found a word at a time | no |
| B. Allocation log | append the payload pointer | 8 B per allocation in the nursery: 16 MB at the self-compile's K2 N1 peak (2.04M) | the log | no |
| C. Eden regions (JSC) | none; a region is flagged when the allocator takes it | 1 bit per region | every slot of each flagged region | yes: per-region free lists |
| D. Lazy sweep (CRuby, Whippet, bdwgc, JSC) | sweep a region before allocating from it | none | moves the sweep out of the pause; does not shrink it | no |

**Recommendation: A.** It is exact like B at a fraction of B's memory, and it leaves allocation
order alone, so step 1 can require output and collection counts identical to stage 1, with only
slots visited changing. Scanning a bitmap a word at a time is how CRuby sweeps a page. Its cost is
the region lookup on the reuse path, which step 1 measures on the allocation-heavy workloads
(nqueens, http_log_middleware, gc_roots) and reports with each one's region and overflow-region
counts, since at 8 regions or fewer, and for a region outside the arena, the lookup is
`region_find_slow`'s search, not the index. B is the fallback if the lookup
shows up. The bitmap pointer lives in `SproutRegion` beside `slotmap`, so region-table inserts and
removals carry it.

C needs no per-allocation work and has JSC behind it, but its bound is a whole region: a 1 MiB
region with one hole is swept in full to reuse that hole, so it needs a density rule (JSC stops allocating
into blocks fuller than `minMarkedBlockUtilization`, Whippet promotes blocks with < 5% holes) and changes where objects land, which
moves footprint in ways step 1 could not isolate. It is the better shape if allocation locality
becomes a goal of its own. D reduces pause, not work, and is independent of this choice.

**A young bit's life.** Allocation sets it. A young-only minor visits every set bit and clears
it: the slot is freed, promoted, or (K > 1) stays young, and only the last sets the bit again.
A full walk (a major, or the minor before one) rebuilds the bitmaps from the headers. So a freed
slot never keeps its bit; a stale bit would make the next minor list the FREE slot a second time,
handing one slot to two objects. `vector_push`'s inline-tail split carves a FREE slot out of a
live one and leaves its bit clear.

**Persistent free lists.** Today every sweep wipes and rebuilds the free lists from every region
(`sprout_gc_sweep`, the staged build). A young-only minor pushes the young slots it frees onto
the existing lists and leaves the rest alone. This is the BACKLOG entry "The freelists are still
wiped and rebuilt from *all* regions every sweep". `vector_push`'s inline-tail split already
pushes onto the live lists between collections.

**Region release.** Pass 2 of today's sweep releases every ordinary region left with no live and
no poisoned slot, at every collection, and the staged build's rollback drops its slots from the
lists. A young-only minor cannot drop them: the lists interleave every region, and removing one
region's entries means walking the lists. So a minor first decides whether any region would
empty. Each region keeps an old count and a young count (allocation finds the region for its
young bit anyway), and the mark counts the young objects it reaches per region (its lookup,
`sprout_heap_lookup_region`, already finds the region). After the mark, a region empties when it
has no old and no poisoned slot and the mark reached none of its young ones. If any region would,
the minor runs today's full sweep instead, which releases it exactly as master does. In master's
logs no minor lowered the region count (self-compile 0 of 63, mutvec_int 0 of 352: promoting
every survivor rarely leaves a region empty); in the K2 N1 prototype 105 of mutvec_int's 791
minors did, so step 2 measures how often the fallback runs.

**The free count.** The footprint floor reads `g_gc_freelist_len`, which the staged build
counts during the walk (`fl_region_commit`). A young-only minor keeps it incrementally: the count
after the last sweep, minus free-list reuses, plus slots it lists, plus inline-tail splits. Only
listed slots count: in lineage mode (`sprout_gc_lineage_on`) a dead object is poisoned, not listed.
The reuse count is one increment on the reuse path. GENCHECK compares it with a full walk.

**Scan cost.** A region whose young count is 0 is skipped, so a minor touches only the bitmaps
of regions that hold young slots, not 1/128 of the whole heap.

**Large objects.** A large object is its own region. Young large objects are recorded the same
way and freed by a minor, which releases their region as today; a large slot is never on a free
list. Old ones are left to majors.

### 5.4 The walk before a major

When the next collection is a major, the minor before it sweeps with `sprout_gc_sweep(0)`, which
clears every colour bit as it walks, so the major runs no `sprout_gc_clear_marks` walk (§14.8
step 5). A young bitmap cannot find old objects, so that minor keeps the full walk; §4's model
counts it. One full walk per major is what stage 1 pays today.

Whippet avoids the walk by rotating the mark value at a major instead of clearing marks
(`nofl_advance_current_mark`, called from `nofl_space_prepare_gc` only for non-minor
collections). Here that would mean a global parity for bit 8: allocation writes the current
"unmarked" value, the barrier's fast path compares against the parity, and every colour test in
the runtime changes. That is a later step if step 1 shows the pre-major walk dominates; it is not
part of this design.

### 5.5 The nursery after a shrink, and for small live sets (step 2b)

Step 2 missed the peak-slots goal on galaxy_client (1.76), astar (1.11) and compiler_test (1.07).
There are two causes. galaxy_client's needs a display, so `bench/gc_minor/load_spike.sprout`
reproduces it headless: a load builds ~216k live objects, nearly all of them die, and long-lived
objects keep arriving in small numbers. At K = 2 with the nursery on its peak slots are 1.57× minor-off.

**Cause 1: the nursery outlives the live set it was sized for.** load_spike, K = 2, nursery on:

| cycle | kind | heap before | heap after | free | regions |
|---|---|---|---|---|---|
| 8 | major | 256,662 | 215,649 | 41,013 | 8 |
| 9 | minor | 646,947 | 127,560 | 191,707 | 10 |
| 10–27 | minor | ~557k each | ~125k | 235k → 432k | 12 → 17 |
| 28 | major | 555,636 | 944 | 555,538 | 17 |

The major at cycle 8 runs at the load's peak, so each later nursery is 2 × 215,649 = 431,298
allocations. The load data then dies, but it is old, and a minor cannot free it. Neither major rule
fires: the heap does not double, and `major_every` counts 19 minors, about 38 × the peak live set in
allocations. Every minor in that run walks ~557k slots against the 256,662 the last major held, so
the heap grows. Long-lived objects allocated meanwhile land in the new regions, and once the major
at cycle 28 leaves 944 live objects across 17 regions, none can be released. From then on the
footprint floor keeps the threshold at the slots held: 6,360 + (6,360 + 550,122) / 3 = 191,854.

Minor-off sees the drop at its next collection (cycle 7: heap 521,541 → 1,212) and settles at 10
regions. The step-2 results blamed promoted load-phase data. A load phase whose only survivors come
from the load does not reproduce the regression: the first major releases those regions (7 against
minor-off's 8).

**Cause 2: the base floors the nursery, not the target.** With the nursery on, the target is
`heap + max(base, baseline × 2)`; minor-off's is `max(base, heap × 3)`. Under a few thousand live
objects that is `heap + 4096` against `4096`: astar's cycle 101 runs at 4,363 = 267 + 4,096, and
compiler_test's cycle 11 at 4,642 = 546 + 4,096.

**Prior art: nursery size, and what notices a shrink.** Read 2026-11-08, from source.

| Collector | Next minor after | Major when | Reacts to a shrink? |
|---|---|---|---|
| Lua 5.4.7 (`lgc.c`) | 20% of total bytes, dead old ones included (`setminordebt`) | total > 2 × the size after the last major (`genstep`) | no |
| CRuby (`gc/default/default.c`) | the free slots run out; after a GC, free slots are kept at 20–40% of the heap | old objects > 2 × the old count at the last major; a minor that frees < 7/8 of the 20% floor (`GPR_FLAG_MAJOR_BY_NOFREE`) | through yield: dead old objects fill the heap, so a minor frees less |
| JSC (`Heap.cpp`) | `maxEdenSize = maxHeapSize − size after the last full`, fixed until the next full | eden < 1/3 of the heap (`updateAllocationLimits`); a timer (`FullGCActivityCallback`) | only by the timer |
| V8 (`heap.cc`) | young space doubles when survivors exceed it, shrinks below 1000 B/ms allocation (`ShouldResizeNewSpace`) | old space past a limit set after the last mark-compact | the memory reducer, on low allocation rate or a background page |
| Whippet (`mmc.c`) | the heap is full; no separate nursery | a minor's yield < 0.30 × the yield before the run (`determine_collection_kind`) | through yield, as CRuby |
| OCaml 5 | a fixed 256k-word minor heap | no trigger: a major cycle is always running | not needed: the running cycle sweeps dead old objects |

Our nursery has JSC's shape: a multiple of the size after the last major, fixed until the next one.
CRuby's and Whippet's yield rules do not transfer as they stand. Their heap does not grow during a
run of minors, so dead old objects use up the room and a minor's yield falls. Ours grows instead, and
each minor of cycles 9–27 frees about 430k slots, a high yield. Lua and CRuby size the nursery as a
share of the whole heap, dead old objects included, and the share (20–40%) is a tenth to a fifth of
ours (2 × baseline).

**Options for cause 1:**

- **A. Count `major_every` in allocations**, e.g. a major once minors have allocated c × baseline.
  A small c makes the self-compile's stable live set pay a major every minor or two, which is
  stage 1 lost. A large c leaves the run of oversized minors.
- **B. Size the nursery from survivors**, growing it when a minor's survivors fill a share of it and
  shrinking it toward the base when few survive. V8 grows on survivors, but it shrinks on allocation
  rate, so the shrink half is ours. The first minor after the load is still 431k, and the nursery
  shrinks only over the following minors.
- **C. Size the nursery as a share s of the heap after each collection**, as Lua and CRuby do:
  `heap + max(s × heap, nursery_min)`. The first minor after the load starts from the 215,649 objects
  the major left, so it runs at 270k–431k for s from 0.25 to 1, against the 256,662 slots held. It
  frees the young part of the dead load data, and the minors after it start from ~125k old objects:
  at s ≤ 1 they stay within the slots held. Dead old objects
  inflate the nursery as §1 cause 2 found, but by s × dead, not 2 × dead. The cost is more minors on
  a large live set: the self-compile's heap of 1.2–2M against a baseline-sized 2.4M nursery is
  1.2–4× as many minors at s = 0.5–1. A young-only minor's work follows the young set (§5.3), so the
  extra cost is each minor's fixed part (roots, the remembered set, young-region bitmaps), plus the
  survivors re-marked at K > 1.

**Recommendation: C**, with s picked by the A/B from {0.25, 0.5, 1}; it picked 1, and
`SPROUT_GC_NURSERY_SHARE` stays (§7 step 3). It bounds every minor after the
first one following the load, and at s = 0.25 the first one too, within 5% of the slots held. It is also
what Lua and CRuby do. JSC keeps our current shape and relies on a timer. A gives up the generational gain to
see the shrink, and B sees it only after several minors. Whether one oversized minor still pins
regions is for load_spike to show.

**C alone promotes medium-lived data.** The A/B
(s ∈ {0.25, 0.5, 1}, K ∈ {2, 3}) brought every workload's peak slots to ≤ 1.05 at s = 1. But
mutvec_int's CPU rose 1.18–1.93× and load_spike's 1.20–1.28×. mutvec_int, K = 2:

| arm | minors | majors | mean nursery | marked per minor | heap growth per minor |
|---|---|---|---|---|---|
| mode 1 | 802 | 67 | 16,753 | 5,904 | 97 |
| mode 2, s = 1 | 1,399 | 139 | 9,039 | 5,021 | 579 |
| mode 2, s = 0.25 | 2,656 | 441 | 3,614 | 4,356 | 1,209 |

Each minor marks the round's list still under construction, ~5k objects, whatever the nursery
size. A nursery under that promotes the list, which then dies old, and the 2× rule fires every few
minors. The nursery must outlast the young working set, and the heap's size does not measure it.
So mode 2 also takes a survivor floor: `nursery ≥ c × m`, where `m` is the marked count of the last
minor. A major's marked count is the whole live set, so only minors update `m`. That keeps the
first minor after a shrink from being sized off the peak. V8 grows its young space on the same
signal: when the objects that survived since the last resize exceed its capacity
(`Heap::ShouldResizeNewSpace`). c was `SPROUT_GC_NURSERY_SURVIVE`; the A/B picked 2 over 4 (c = 4
at K = 2, s = 1: mutvec_int peak 1.86, load_spike 1.14), and the switch is gone.

**Cause 2's fix** applies to every option: floor the target, not the nursery. With C that is
`max(base, heap + max(s × heap, c × m, nursery_min))`. A heap of dead old objects with a tiny live set
must not collect every few allocations, so `nursery_min` is `base / adapt_factor` (1,365). astar's
cycle 101 then runs at 4,096, as minor-off does.

**Out of scope: where objects land.** Minor-off also holds 10 regions for ~1.5k live objects
(327,680 slots), because survivors spread across regions keep each one from being released. The goal
is relative to minor-off, and placement that packs long-lived objects into fewer regions would help
both arms. JSC and Whippet's density rules (§5.3, option C) are the prior art for that.

### 5.6 Per-region free lists (step 4)

The §2 goal that a minor's sweep follows the young set holds except in two places. §5.4 keeps
one: the minor before a major. The other is `sprout_gc_young_sweep_ok`. A minor that would leave
an ordinary region empty walks the whole heap, because the global lists interleave every region
and only the full sweep's rebuild can drop one region's entries. On the default, per run:
nqueens 559 such minors, mutvec_int 33, digit_recognizer 12, the self-compile 9, load_spike 2.

The lists also decide where objects land (§5.5, out of scope there). LIFO lists hand out the most
recently freed slot wherever it lies, so survivors can spread over every region. Address order
fills low regions first. On load_spike it released no region (12 in both arms), so it does not
reach the galaxy client's walk after a load spike (`BACKLOG.md`); §5.7 does.

**Per-region lists.** Each ordinary region owns its free lists: a head per class (1..256), in a
block the region points to like `slotmap` (NULL for a large region), so region-table inserts and
removals still move a small `SproutRegion`. The block holds no mask and no count: a scan tests
the head, and a released region has no live and no poisoned slot, so every slot below `bump` is
FREE and listed. Releasing it drops its lists in O(1) and subtracts the popcount of its slotmap
bits below `bump` from `g_gc_freelist_len`. The free count stays in slots, so the footprint
floor, `just gc-trigger-check`, `bench/gc_trigger/measure.sh` and the peak-slots metric keep
their unit.

**Release in a minor.** A young-only minor releases an ordinary region left with no old, no
young and no poisoned slot, so `sprout_gc_young_sweep_ok` always allows the young-only sweep. It
decides after walking the region's young slots, since that walk frees dead objects' malloc'd
data and clears their singleton slots, and it removes the regions from the table after the walk
loop, as it already does for freed large objects, so the walk's indices do not shift. Only a
region the minor walked can empty; an untouched one with `bump == 0` is the open region Pass 2
reserves. If no ordinary region is left, the minor opens one, as Pass 2 does when `kept_normal`
is 0.

**Allocation order.** Class c allocates from the lowest-address region with a free slot of class
c. A per-class cursor holds the region index where the last search stopped and only moves up
between collections, relative to the regions. Every collection resets it to the first region. A
region-table insert at or below it (a large object, a bumped region) moves it up one, and a
removal below it moves it down one, so the regions below it stay the same. A push between
collections into a region below the cursor (`vector_push`'s inline-tail split,
`sprout_vec_release_inline_tail`) lowers the cursor to that region, so the slot is not stranded.
A miss in every region bumps, as today.

**The reuse path.** With the global lists it pops a list and calls `region_find` for the young
bit. With per-region lists `g_fl_cur[c]` caches the block of the cursor's region
(`g_fl_cur[c] == g_regions[g_fl_cursor[c]].fl`, NULL past the table). A hit pops that block's
head and decrements `g_gc_freelist_len`, nothing else; the young bit takes the region as
`g_regions[g_fl_cursor[c]]`, so `region_find` goes. A miss calls an out-of-line scan that tests
one head per region from the cursor's successor, at most once per region per class between
collections, and moves the cursor and the cached block. Blocks are heap-allocated, so a table
shift keeps them; the cache changes when the cursor moves or the region under it is removed.

**What stays.** The sweep is eager and per dead object; full sweeps list slots one by one, by
region. Lookups, heap walks, census counts, singleton invalidation, lineage poisoning, HDRCHECK
and the `just gc-trace` anchors see FREE headers exactly as before. Minor-off gets the same lists
and allocation order, so the A/B measures both arms with and without them.

| | Choice | Rejected, and why |
|---|---|---|
| list ownership | per region | global lists: interleave regions, force full walks to release one |
| list storage | a block the region points to | inside `SproutRegion`: 2 KB per entry in every table memmove |
| allocation order | lowest-address region first | LIFO: scatters survivors, few regions empty |
| merging adjacent free slots | not done | needs a new free-count unit; the free count is in slots |

Prior art, from source at the tags in §3: JSC keeps a free list per block and sweeps a block just
before allocating from it (`FreeList`, `specializedSweep`). mimalloc keeps lists per page and
retires a page whose `used` falls to 0 (`_mi_page_retire`). CRuby keeps a free list per page
(`gc_sweep_plane` calls `heap_page_add_freeobj`). Immix uses partly free blocks in address order
before free ones (PLDI 2008 §3.1), and Go's page allocator is address-ordered first-fit
(`mpagealloc.go`).

### 5.7 Lazy per-region sweep (step 5)

Designed after step 4 is measured. A lazy sweep defers a region's dead slots until the allocator
next takes a slot from it, so the young sweep's cost per dead slot leaves the collection, and so
does a major's walk of free slots (the galaxy client's ~1 ms after a load spike). JSC, Go, bdwgc
and CRuby all sweep a page or block before allocating from it, which needs step 4's per-region
allocation. Requirements, from what the eager sweep does today:

- a live bit per slot separate from the colour, since a young survivor's colour is clear;
- lookups (`sprout_heap_lookup`, `sprout_heap_lookup_region`) that read it, so an unswept dead
  slot is rejected like a FREE one;
- singletons registered as roots or invalidated eagerly, since their slots are cleared by the
  sweep today;
- an eager visit to each dead object that owns malloc'd data (vector buffers), or that data
  stays allocated until its region is swept;
- census counts, lineage poisoning, HDRCHECK and the free count settled per region at sweep time.

## 6. Premises

Each premise has a test or a check that fails when it breaks.

1. Every store of a heap pointer into an existing old object goes through the barrier — stage 1
   premise 1, unchanged.
2. **Restates stage 1 premise 4.** After every collection the young bits are exactly the young
   objects: a slot has its bit set if and only if its kind is live and its colour bit is clear.
   GENCHECK adds a full slot walk after each sweep and checks both directions. A set bit on a FREE
   slot is the double-listing failure of §5.3.
3. After a collection, an old object points at a young one only if it is remembered, and when the
   next collection is a major the set is empty and no header has bit 13. GENCHECK's reachability
   check catches a missed edge only when it is the only path, so the walk of premise 2 also scans
   every old, unremembered object's children and aborts on a young one. It skips OBJ fields that
   `CtorMeta.field_kinds` marks scalar; for raw words (tuples, closure captures, unregistered
   OBJs) a scalar equal to a young address is a false positive, reported with owner kind and field
   index as §14.5 does.
4. Free-list entries point only into live regions. With the global lists no ordinary region is
   released outside a full walk (a minor releases a dead young large object's region, which is
   never on a list); with per-region lists (step 4) a minor releases one too, and its entries go
   with its list block. `SPROUT_FL_VERIFY=1` checks the lists against a full walk after every
   collection, and `just gc-gen-check` gains a run with it on, beside GENCHECK.
5. The live prediction equals the post-sweep count — stage 1's GENCHECK count check, with §5.1's
   formula.
6. The incremental free count equals a full walk's — GENCHECK, at each young-only minor, run
   with and without `SPROUT_GC_LINEAGE=1`.
7. A young-only minor never keeps a region that master would release — the emptiness test of
   §5.3; GENCHECK's walk aborts on an ordinary region left with no live and no poisoned slot.
   Per-region lists keep the premise and drop the full-walk fallback: the minor releases the
   region. The check then exempts the one ordinary region with `bump == 0` the minor may open
   (§5.6).
8. **Step 4.** Every entry on a region's lists is a FREE slot of that region, of the list's class,
   and `g_gc_freelist_len` equals the entries over all regions (a released region has every slot
   below `bump` FREE and listed). `SPROUT_FL_VERIFY=1` checks each region's lists against a walk
   of that region, and the total against `g_gc_freelist_len`.
9. **Step 4.** Between collections, no region below a class's cursor has a free slot of that
   class, and each cached block is its cursor region's. `SPROUT_FL_VERIFY=1` checks both at each
   allocation that advances a cursor, and the cache after every collection.

## 7. Steps

1. **Young-only sweep and persistent free lists**, behind `SPROUT_GC_MINOR` as today (no new
   switch: the result must equal stage 1's except for slots visited). Premises 2, 4, 6 and 7 with
   their checks. Output, collection counts and every cycle line's `threshold`, `free` and region
   counts must match master exactly, since the trigger is unchanged, premise 6 keeps its input and
   premise 7 keeps region release; `walked` falls. Measure pause, peak RSS and the reuse path's
   cost with `bench/gc_minor/ab.sh`, reporting region counts (§5.3) and how often a minor fell back
   to the full sweep.

   **Implemented** (`sprout_gc_sweep_young`, `sprout_gc_young_sweep_ok`, `sprout_gc_check_sweep`).
   Against master's runtime, with `walked=` and timing removed, every `[sprout gc]` line and the
   stdout are identical on the self-compile, mutvec_int, the §14.8 programs that build from this
   repo except http_log_middleware, `tests/gc_gen/*` and `test_gc_freelist_reuse`.
   http_log_middleware's cycle lines differ between two runs of one binary, so equality cannot be
   shown for it. Walked slots: self-compile 136.2M → 82.5M, mutvec_int 28.3M → 24.1M, nqueens
   83.6M → 63.7M. Self-compile minors: 48 young-only, 12 full before a major, 3 full because a
   region would empty.

   Measured against minor-off ([results-2026-11-05-gc-young-sweep.md](../bench/results-2026-11-05-gc-young-sweep.md)):
   - GC time and pause fall against stage 1 wherever minors could skip old slots. Self-compile
     GC 0.52 → 0.39 and p90 0.49 → 0.39; galaxy_client p50 0.91 → 0.58; digit_recognizer
     p50 0.84 → 0.47.
   - Minors fell back to the full sweep only before a major, except the self-compile's 3.
   - RSS and region counts are stage 1's. mutvec_int still fails the §2 pause and memory goals
     (p50 1.24, p90 1.57, RSS 1.23), and the self-compile's RSS is 1.09; both are step 2's.
2. **Promotion age and nursery size**, together (§4: either alone regresses one workload). Two
   switches for the A/B, `SPROUT_GC_PROMOTE_AGE` and the nursery rule's on/off, premise 3 with
   its check, and the §14.8 workload set by `bench/gc_minor/ab.sh` (≥ 6 interleaved runs per arm)
   with K ∈ {1, 2, 3}, reporting peak slots, RSS and pause p50/p90 with each workload's spread.
   Pick K, and decide the 2× rule's input (§5.2). Flip only if the §2 goals hold.

   **Implemented behind the switches; defaults unchanged**
   ([results](../bench/results-2026-11-07-gc-promote-age.md)). The 2× rule keeps the heap count.
   At K = 2 and 3 with the nursery on, the pause goal holds on every workload (galaxy_client's p50
   falls to 0.05). Two goals miss: peak slots on galaxy_client (1.76), astar (1.11 at K = 2) and
   compiler_test (1.07), and the self-compile's CPU (0.83 against 0.80).

   **Step 2b: the nursery after a shrink, and for small live sets** (§5.5). Size the nursery as a
   share s of the heap with a survivor floor c, behind `SPROUT_GC_NURSERY=2` with s set by
   `SPROUT_GC_NURSERY_SHARE` and c by `SPROUT_GC_NURSERY_SURVIVE`, and
   floor the target rather than the nursery in both nursery modes. Run the §14.8 set plus
   load_spike through `bench/gc_minor/ab.sh` at K ∈ {2, 3} and s ∈ {0.25, 0.5, 1}, reporting peak
   slots, pause, CPU, and minors per major. Then run galaxy_client once on a display to confirm the
   chosen arm. Pick K and s, and flip if the §2 goals hold, the self-compile's CPU included.

   **Implemented; defaults flipped** to K = 3, s = 1, c = 2
   ([results](../bench/results-2026-11-08-gc-nursery-share.md)). Without the survivor floor the
   share arms fixed peak slots (≤ 1.06 on every workload) but cost mutvec_int 1.18–1.93× CPU and
   load_spike 1.20–1.28×. With c = 2 at K = 3, s = 1:
   - Peak slots are at most 1.03 (load_spike, galaxy_client); K = 2 leaves mutvec_int at 1.24 and
     astar at 1.05. c = 4 raises peaks (K = 2: mutvec_int 1.86).
   - Pause holds on every workload; mutvec_int is 0.60 / 0.84 (p50 / p90) and load_spike's p50 0.09.
     galaxy_client's median pause falls from 1,012 µs to 28, p90 0.51, CPU 1.01 ~.
   - The self-compile's CPU is 0.82 ~ (6 runs) and 0.88 ~ (10 runs) against the 0.80 goal. §2
     records the move of the goal to 0.88.
   - Across seven commits the self-compile's peak slots are 0.72–0.95 of minor-off, against
     1.07–1.58 for the old default.
   - With `SPROUT_GC_MINOR=0`, the self-compile's cycle lines (timing fields removed) and output
     equal those of the runtime before the flip.
3. **Done.** `SPROUT_GC_PROMOTE_AGE`, `SPROUT_GC_NURSERY` and `SPROUT_GC_NURSERY_SURVIVE` are
   retired: set to a non-empty value they abort at startup with a message naming the removal. K = 3
   and c = 2 are fields of `g_gen` that a C test may set. `SPROUT_GC_NURSERY_SHARE` stays, since s
   is the one setting that moves the memory-for-CPU trade by workload: s = 0.5 kept peak slots at
   or under 1.00× minor-off on every workload at 0.88 self-compile CPU, and s = 2 collects less
   often on `test_gc_trigger_adversary`. K and c have one measured best value across the set.
4. **Per-region free lists** (§5.6), behind `SPROUT_GC_REGION_LISTS=1` for the A/B, with release
   in young-only minors and address-ordered allocation (premises 7–9). Tests in `tests/gc_gen/`:
   a program where an ordinary region empties in a young-only minor, one where every ordinary
   region does, one where `sprout_vec_release_inline_tail` frees a slot that the next allocation
   of its class reuses, and one that frees a slot below a class cursor between collections
   (`cursor_lower_nostress`). Stdout must equal the global lists'. `free`, region and collection
   counts may differ: allocation order changes which regions empty, and the footprint floor reads
   `g_gc_freelist_len`. The VM A/B on the §14.8 set reports all three, with each workload's
   spread, and the reuse path's cost with minors off.

   **Implemented behind the switch** ([results](../bench/results-2026-11-12-gc-region-lists.md)).
   On a quiet VM at the default, against the global lists, stdout matched and every
   full-walk minor that was not before a major is gone. Collection counts are equal on every
   workload, and peak slots on every workload but the self-compile (1.01; 0.83 of minor-off).
   - GC time: digit_recognizer 0.66 (p50 pause 244 → 56 µs), nqueens 0.96, the self-compile 0.97
     (1,517 → 1,470 ms); gc_roots 1.03 and astar 1.02; the rest inside the spread.
   - CPU: load_spike 0.98, the rest inside the spread; the self-compile is 4.81 s in both arms.
   - The reuse path costs 0–2% CPU with minors off on every workload but compiler_test (1.08 ~, a
     0.27 s run); only nqueens' 1.01 is outside the spread.

   The young sweep's work per dead slot is unchanged; §5.7 is the step that moves it.
5. **Lazy per-region sweep** (§5.7), designed once step 4's numbers are in.
6. Retire `SPROUT_GC_REGION_LISTS` once steps 4 and 5 settle it, and update
   `compiler-internals.md` and `gates.md`. Retiring it removes the global lists with
   `sprout_gc_young_sweep_ok` and the staged build (`g_fl_saved`, `fl_push_staged`,
   `fl_region_commit`, `fl_region_rollback`).

## 8. On landing

Step 1:
- BACKLOG: closes "The freelists are still wiped and rebuilt from *all* regions every sweep".
- `compiler-internals.md` §Non-moving GC: the freelist paragraph ("rebuilt from scratch each
  sweep") and the inline-tail paragraph ("safe only because the sweep memsets and rebuilds the
  lists"); the same claims in `runtime/sprout_runtime.c` at the staged-build comment and
  `sprout_vec_release_inline_tail`.
- `gates.md`: `just gc-gen-check`'s FL_VERIFY and lineage runs.

Step 2:
- `compiler-internals.md` §Minor collections: promotion age, the remembered rule of §5.1 and the
  nursery rule; §Non-moving GC's "Nothing in the collector's behaviour depends on [age]", and the
  comment at `SPROUT_GC_AGE_MAX`.

Steps 2b and 3:
- `development.md` §Adaptive threshold: the two regimes, and the `SPROUT_GC_NURSERY_SHARE` and
  `SPROUT_GC_ADAPT_FACTOR` rows.
- `compiler-internals.md` §Object age, §Minor collections and the promotion-age paragraph: K fixed
  at 3, the nursery rule always on with minors on, the retired switches.
- `gates.md`: `just gc-gen-check`, `just gc-trigger-check` and `just gc-adapt-check` arms.
- BACKLOG: the cache-like long-running workload the A/B set lacks; closes "GC generational step"
  (stage 2, `gc-generational-v0.md` §14.6, is done).

Step 4:
- `compiler-internals.md` §Non-moving GC: per-region lists, address-ordered allocation and release
  in minors; the young-only sweep paragraph's `sweep=young|full` marker.
- `development.md`: the `SPROUT_DEBUG_GC` row's minor marker.
- `gates.md`: `just gc-gen-check`'s region-list runs and `*_nostress` programs.

Step 6, for step 4's switch: `sprout_gc_young_sweep_ok`, the staged build, and the
`fl_push_staged` warning in the comment above `sprout_vec_release_inline_tail`.
