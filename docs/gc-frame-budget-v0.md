# GC under a frame budget — incremental collection for the game engine (v0, 2026-09-27)

**Status:** design record and a measurement. **Nothing here is proposed for
implementation yet**, and §9 says why the first fix is not in the collector at all.
Normative GC behaviour remains
[docs/compiler-internals.md §Non-moving GC](compiler-internals.md). Pause data and the
mechanism this builds on: [gc-generational-v0.md §13](gc-generational-v0.md).

## 1. Problem statement

A game has a hard per-frame deadline. `uncharted-suns` runs at 58.45 fps — a **17.1 ms
frame** — and its client's median collection is **7.7 ms** (§13.6). One frame in
seventeen carries it, so roughly three times a second a frame has 45% of its budget
taken by the collector.

For this workload the shape of the cost matters more than its total. A stop-the-world
collector amortises well and spikes badly; a frame deadline cares only about the spike.
The requirement, stated as the user did: **a small collection every frame, or none at
all — not a long pause every n-th frame.**

## 2. Goals / non-goals

**Goals.** Establish what fraction of Sprout's pause is mark and what fraction is sweep,
because that decides whether one mechanism or two are needed. Survey how comparable
runtimes bound a collection step. Say what Sprout specifically would have to build, and
what each piece costs. Decide whether to build it now.

**Non-goals.** Building any of it. Moving the collector — non-moving is load-bearing
([gc-generational-v0.md §3](gc-generational-v0.md), and §12 there for why it is not
switchable). Concurrent collection, which needs an OS thread Sprout does not have
(§7.3).

## 3. Prior-art survey (primary-sourced)

Every row verified against the implementation's own reference; URLs in §11.

| Runtime | Mechanism | Slice bound | Moving? |
|---|---|---|---|
| **Unity** (Boehm–Demers–Weiser) | incremental mark, **on by default** | `GarbageCollector.incrementalTimeSliceNanoseconds` — "the target duration of a collection step" | no |
| **Go** | concurrent mark with write barrier; sweep both lazy and background | 25% CPU target via the pacer; sweep is per span | no |
| **OCaml** | incremental major GC in slices | `Gc.major_slice n` — caller-driven, "`n` is the size of the slice"; `space_overhead` sets the automatic rate | major heap no (OCaml 5) |
| **GHC `--nonmoving-gc`** | concurrent mark on a separate thread | none needed — mark runs alongside the mutator | no |
| **ZGC / Shenandoah** | concurrent mark *and* relocation, sub-ms | n/a | yes |

**Unity is the closest analogue and the most useful row.** It is a non-moving
mark-sweep collector, like Sprout's, retrofitted with incremental marking, and it is
aimed at exactly this problem:

> "Incremental garbage collection spreads out the process of garbage collection over
> multiple frames. This is the default garbage collection behavior in Unity… the
> garbage collector splits up its workload over multiple frames and makes shorter
> interruptions to your application's execution."

**And Unity states the trade, which is the thing to keep hold of:**

> "Incremental mode doesn't make garbage collection faster, but because it distributes
> the workload over multiple frames, performance spikes related to garbage collection
> are reduced."

Total CPU goes **up**: a write barrier fires on every pointer store, on every frame,
including the frames that would have had no collection at all. At 58.45 fps against a
60 fps target there is roughly 2% of headroom to pay it out of.

**The two rows that need no slice budget are the ones with a spare thread.** GHC marks
concurrently; ZGC and Shenandoah relocate concurrently too, at the cost of being moving
collectors. Neither option is open to Sprout today (§7.3).

## 4. Measured — the pause is ~65% mark, ~35% sweep

The split decides whether incrementalising one phase is enough. It can be obtained
without the per-phase timer the `BACKLOG` asks for, because `threshold = live ×
SPROUT_GC_ADAPT_FACTOR` and `live` is a property of the world: **turning the factor
scales the dead slots the sweep walks while leaving the mark set alone.**

Galaxy client, `SPROUT_GFX_MAX_FRAMES=1200`, steady state:

| `SPROUT_GC_ADAPT_FACTOR` | cycles | live | heap slots | dead slots | p50 | p90 |
|---|---|---|---|---|---|---|
| 1.5 | 427 | 80,132 | 120,002 | 39,870 | 4,119 µs | 5,613 µs |
| 3 (default) | 34 | 86,166 | 268,059 | 181,893 | 7,361 µs | 10,275 µs |
| 6 | 5 | 77,325 | 463,852 | 386,527 | 9,141 µs | 11,437 µs |

Fitting p50 against dead slots gives a slope of **14.5 ns per dead slot**. At the
default that is ~2,640 µs of sweeping, leaving ~4,720 µs of mark-and-live-walk — **54.8
ns per live object**. The same fit at factor 1.5 gives 44.2 ns per live object, so call
it 44–55 ns, and the split **roughly 65% mark / 35% sweep**.

**Caveats.** `live` is not perfectly constant across the three runs (77.3k–86.2k), the
factor-6 row is only five collections, and §13.4's warning about the tail applies. This
is a decomposition good to about ±10%, not a calibration. It is enough for the decision
it is here to make.

**The consequence: incrementalising only the mark caps the spike reduction at ~3×.** A
200 µs slice out of a 7.4 ms collection needs ~37×. Both phases have to become
interruptible, or the sweep becomes the new spike.

## 5. Incremental mark

Standard tri-colour with a write barrier. What Sprout already has for it:

- **The barrier is priced and nearly free, if typed.**
  [gc-generational-v0.md §6](gc-generational-v0.md) measured the compiler at 15,746
  mutation calls of which 1,855 would be recorded, and `digit_recognizer` at 10,584,110
  mutation calls storing **zero** pointers. The conclusion there — emit the barrier only
  for pointer-typed stores, which the compiler knows statically — applies unchanged.
- **The barrier sites are enumerated** in §7 there: `ref_write`, `vector_mutset`,
  `vector_push`. The same section records that this list was silently wrong for a month
  and instructs a future implementer to **re-derive it from the runtime** rather than
  trust it.
- **Non-moving is an asset here.** The hard parts of incremental collection in a moving
  collector are the read barrier and pointer fixup mid-slice; a mark-sweep heap has
  neither. That both Unity (Boehm) and GHC (nonmoving) added interruptible marking after
  the fact, and ZGC needed a load barrier to do it, is the evidence.

What is not free:

- **A grey worklist that survives between slices.** Marking today runs to completion
  inside `sprout_gc_collect` with a local queue. Incremental needs it heap-allocated,
  rooted, and bounded.
- **`vector_push` moves element storage.** Since 4535204c a small vector's elements live
  inside its own slot and the first growth past the inline capacity **moves them out**.
  §7's blockquote spells out what that costs a card-marking design: a card covering the
  `VectorVal` covers its elements before the growth and not after. Any incremental design
  has to reason about this; it is the newest thing in the runtime that interacts with it.

## 6. Incremental sweep

Go's model, and the closest fit to what Sprout already does:

> "The heap is swept span-by-span both lazily (when a goroutine needs another span) and
> concurrently in a background goroutine… when a goroutine needs another span, it first
> attempts to reclaim that much memory by sweeping."

Sprout's sweep is **already staged per region** — `fl_push_staged` /
`fl_region_commit` / `fl_region_rollback` exist so that a region released in pass 2
drops exactly its own freelist entries. Sweeping a region at a time is closer than it
looks.

The blocker is filed already: *"The freelists are still wiped and rebuilt from all
regions every sweep."* A sweep that rebuilds the whole heap's freelists cannot be
resumed halfway. That entry is listed as the nursery's prerequisite; it is equally the
prerequisite here, and that is an argument for its priority independent of either
feature.

## 7. What Sprout cannot do yet

### 7.1 A caller-driven slice

OCaml exposes `Gc.major_slice n`: "Do a minor collection and a slice of major
collection. `n` is the size of the slice." For a game this beats any automatic pacer,
because the frame loop knows where its slack is — after present, before the next
simulate — and a pacer does not.

Sprout cannot do even the crude version of this: **`sprout_gc_collect` is `static`**, so
there is no way for a program to ask for a collection at a chosen point in its frame.

> **This would be a new builtin, and per AGENTS.md "Builtin vs Stdlib" it needs explicit
> approval before anyone writes it.** It cannot be done in Sprout — it is a request to
> the host collector — so rules 4 and 6 are satisfied on the "impossible in Sprout"
> ground rather than on performance. It is small (expose an existing static function
> behind a name in `APPROVED_BUILTINS`), and it is useful on its own before any
> incremental work: a game that can place the 7.7 ms itself is strictly better off than
> one that cannot, even while the pause stays 7.7 ms.

### 7.2 A pause tail anyone can trust

[§13.4](gc-generational-v0.md) records collections two orders of magnitude slower than
the median (3,105–8,516 µs against 33 µs) with identical `live`, `swept`, `marked` and
region counts, which do not reproduce between runs.
Any incremental design is judged on its tail, so the per-phase timer filed in `BACKLOG`
is a prerequisite for evaluating this work, not a nicety.

### 7.3 Concurrency

"No pause at all" means marking on another thread while the mutator runs. Sprout is
green threads on **one** OS thread (`stdlib/task.sprout`); share-nothing multicore is
the declared tier-2 direction
(`docs/concurrency-design-exploration-2026-07-13.md`). GHC's `--nonmoving-gc` is the
model to copy when that lands, and it is the right model precisely because it is
concurrent mark over a non-moving heap.

## 8. Impact if it is built

- **Syntax and types:** none for the barrier — it is emitted, not written. A slice API
  (§7.1) adds one effectful stdlib function and one `APPROVED_BUILTINS` line.
- **Diagnostics:** none.
- **Compatibility:** the barrier changes emitted IR for every pointer-typed store, so
  `just ir-golden-diff` will show a diff in nearly every file and the bootstrap seed must
  be refreshed. That is a large review surface and a reason to land the barrier as its
  own commit, separate from anything that consumes it.
- **Throughput:** worse, by the barrier's cost on every pointer store. §6's data says
  that is small for the measured workloads, but it has not been measured for the game,
  which is the one that cannot afford it.
- **Tests:** the oracles this needs do not exist. `SPROUT_GC_HDRCHECK` checks the slot
  walk's agreement with the slotmap; nothing checks the tri-colour invariant, and a
  broken barrier loses a live object *occasionally*, which is the failure mode least
  likely to be caught by a passing suite. An incremental collector should not land
  without an invariant checker in the same change.

## 9. Recommendation — not yet, and here is what instead

**Do the bitset first.** The galaxy client's live set is 97.3% one AVL-tree membership
map (§13.6, and `uncharted-suns#396`). Replacing it with a bitset over `MutVec Int`
takes the live set from 77,653 to ~2,014 and the pause from 7.7 ms to roughly 0.2 ms —
under any frame budget, with no collector change and no barrier cost on the other
16.9 ms of every frame.

Everything in §5–§6 is a lot of machinery — a barrier at re-derived sites, a rooted grey
worklist, a resumable sweep, a pacer, an invariant checker, a seed refresh and a
whole-tree IR diff — to solve a pause that one data-structure change removes. And it
would make the *median* frame slower, which at 2% headroom is the wrong direction.

**Then re-measure.** The case for incremental collection rests entirely on a pause
caused by a data structure that should not be on the managed heap. If a future world
still holds hundreds of thousands of live objects once it is shaped correctly, this
document is the starting point and §4's split is the number to design against.

**Two things worth doing regardless of that outcome:**

1. **The per-phase timer** (§7.2). It is needed to evaluate any of this, and §4 only
   approximates it.
2. **Generation-scoped freelists** (§6). Already filed as the nursery's prerequisite; it
   is the prerequisite for a resumable sweep too, which is a second independent reason
   to want it.

## 10. What this document does not settle

Whether the barrier is affordable for *this* game. §6's measurements are the compiler,
`digit_recognizer` and friends; none of them run at 58 fps with 2% headroom. Before any
barrier work, the number to get is the game's pointer-store rate per frame — which the
`SPROUT_GC_AGEPROF=1` counters (`mut_calls` / `ptr_stores`) already report, and which
nobody has run against it.

## 11. Sources

- Unity — Incremental garbage collection
  https://docs.unity3d.com/Manual/performance-incremental-garbage-collection.html
- Unity — `GarbageCollector.incrementalTimeSliceNanoseconds`
  https://docs.unity3d.com/ScriptReference/Scripting.GarbageCollector-incrementalTimeSliceNanoseconds.html
- Go — `src/runtime/mgc.go` (GC cycle, concurrent and lazy sweep)
  https://raw.githubusercontent.com/golang/go/master/src/runtime/mgc.go
- OCaml — `stdlib/gc.mli` (`major_slice`, `space_overhead`)
  https://raw.githubusercontent.com/ocaml/ocaml/trunk/stdlib/gc.mli
- GHC — `rts/sm/NonMoving.c` and the `--nonmoving-gc` RTS flag
  https://gitlab.haskell.org/ghc/ghc/-/tree/master/rts/sm ·
  https://downloads.haskell.org/ghc/latest/docs/users_guide/runtime_control.html
- JEP 439 Generational ZGC — https://openjdk.org/jeps/439
