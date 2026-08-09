# O(1) address→region lookup via a reserved arena

Status: **implemented.** §1–§11 are the original design; **§12 records what actually
happened when it was built**, including three ways the first implementation was slower than
the code it replaced. Read §12 before changing anything here.

## 1. Problem

`sprout_heap_lookup` answers *"is this arbitrary 64-bit word a live heap payload, and if so
where is its header?"* It is the foundation of the rooting protocol — the arena comment at
`runtime/sprout_runtime.c:642` calls this **"membership exactness"**, distinguishing a real
payload from an interior word. It is called on every traced edge, every scanned root slot,
and from the mutation hooks.

Its first step is `region_find`, a binary search over `g_regions` (sorted by base address,
one entry per 1-MiB region plus one per large object).

**Measured: that search is ~14% of self-hosted compile time.**

Because both functions are `static` and fully inlined at `-O2` (confirmed: neither symbol
appears in `nm` output for `compile_driver_bin_stage1`), a profiler cannot attribute to
them. So the cost was measured by *sensitivity* — a build with a second, redundant binary
search per call whose result escapes into a `volatile`, so it cannot be folded away. If
doubling the search moves runtime by X%, one search is ~X% of runtime:

| build | mean of 6 reps on `--emit-ir stdlib/compiler/ast_to_ir.sprout` |
|---|---|
| baseline | 2.232 s |
| second redundant search | 2.547 s (**+14.1%**) |

Every probe repetition exceeded every baseline repetition, so this is signal rather than
noise. Internal consistency check: ~7 iterations per search (≈`log2` of a ~110-region
table at this workload's peak RSS) puts the marginal cost near 2.7 cycles per iteration,
which is the right order for a dependent load, a compare, and an unpredictable branch.

### 1.1 A one-entry hint does not help — and that is informative

The obvious cheap fix is to remember the previously-found region and check it first,
following the existing `g_open_region_hint` discipline (a validated *index*, never a
pointer, so a table realloc cannot make it unsafe). Implemented and measured: **no
improvement** (2.12 s vs 2.14 s, within noise; tried before the sensitivity probe above).

Both results together say the search is expensive **and** its locality is poor. That is
what should be expected: marking drains a worklist in *object-graph* order, which bears no
relation to the address order in which the heap was allocated, so consecutive lookups land
in effectively random regions among ~110. **Any caching scheme fails for the same reason —
the fix must make a cold lookup cheap, not a repeated one.**

Do not re-attempt the hint; it is recorded here so the negative result is not rediscovered.

## 2. Goals / non-goals

**Goals**

1. Make the common-case address→region resolution O(1) with no search.
2. Preserve membership exactness exactly. This is a safety property, not a performance one:
   a false positive turns an integer into a traced pointer.
3. Never dereference an untrusted address to decide membership.
4. Degrade gracefully — if the reservation cannot be obtained, fall back to today's
   behaviour rather than aborting.
5. Keep the collector **non-moving**. `docs/compiler-internals.md` makes this load-bearing
   (`ir_rooting` pushes an `i64` into an alloca and never reloads it).

**Non-goals**

- Moving, copying, or compacting anything.
- Changing the 1-MiB region size, the 16-byte slot granularity, the slotmap, or the header
  layout.
- Generational collection (see `docs/gc-generational-v0.md`; that item is now priced
  against 15.2M re-marks, not 31.2M, after the adapt-factor default changed).
- Reducing the *number* of lookups. This proposal makes each one cheaper, which is why it
  composes with the adapt-factor change and with a future nursery instead of competing.

## 3. Prior art

How do production collectors map an arbitrary address to page metadata, and do they reserve
contiguous address space? Each option considered for Sprout has a real exemplar.

| system | reservation | address→metadata | primary source |
|---|---|---|---|
| **Go runtime** | 64 MiB arenas, *"Each arena's start address is also aligned to the arena size"*; hint-driven so that *"The allocator attempts to keep arenas contiguous so that large spans (and hence large objects) can cross arenas"* | two-level L1/L2 arena map, though *"since arenas are large, on many architectures, the arena map consists of a single, large L2 map"* | `go/src/runtime/malloc.go` |
| **MMTk** | contiguous by default on 64-bit: `heap_start` `0x0000_0200_0000_0000`, `heap_end` `0x0000_2200_0000_0000`, `log_address_space` 47; `force_use_contiguous_spaces = true`, *"Each space should own a contiguous piece of virtual memory"* | `address_mask()` takes *"a few bits from address, and use it as index to the space map table"* | `mmtk-core/src/util/heap/layout/vm_layout.rs` |
| **Boehm–Demers–Weiser** | none required | two-level page table: *"The page address part of the candidate pointer is looked up in a table. Each table entry contains either 0, indicating that the page is not part of the garbage collected heap, a small integer n, indicating that the page is part of large object, starting at least n pages back, or a pointer to a descriptor for the page."* | `hboehm.info/gc/gcdescr.html` |

Reading of the consensus: **a contiguous 64-bit reservation plus bit-extraction indexing is
the mainstream choice**, and where a system declines it (Boehm, and MMTk under 32-bit /
compressed layouts) the stated reason is address-space scarcity — MMTk's 32-bit branch says
plainly *"We don't have enough virtual memory, so this should be set to false."* Sprout
targets 64-bit darwin and linux, so that constraint does not apply.

Boehm is the exemplar for the two-level side table, and Go for the two-level arena map;
both accept a dependent-load chain on every lookup. The reservation approach is preferred
here for a reason specific to Sprout, given in §4.1.

Checked and *not* usable as a source: `openjdk/jdk` `src/hotspot/share/oops/compressedOops.hpp`
documents the base+shift encoding modes (unscaled below 4 GB, zero-based below 32 GB,
disjoint-base, heap-based) but states no rationale for contiguity, so HotSpot is omitted
from the table rather than represented by an inferred claim.

## 4. Design

Reserve a large contiguous range of *address space* (not memory) at startup and carve 1-MiB
region chunks from it. Membership then needs no search and no load:

```c
/* membership: one subtract, one unsigned compare, no memory access */
uintptr_t off = (uintptr_t)p - (uintptr_t)g_arena_base;
if (off < g_arena_committed) {
  SproutRegion* r = &g_regions[off >> SPROUT_REGION_SHIFT];   /* 20 */
  ...
}
```

Reserved-but-uncommitted address space costs no physical memory and no swap: the
reservation is `mmap(PROT_NONE)`, and each chunk is committed with `mprotect` on first use.

### 4.1 Why reservation beats a side table *here*

The rooting protocol queries **arbitrary words**, so the overwhelmingly common answer is
"not a heap pointer". A side table (Boehm, Go) must perform at least one load to say no; a
range check says no in two register operations with a perfectly predicted branch. Sprout's
query mix is dominated by exactly the case where the reservation wins most.

It is also the only option that never touches memory derived from an untrusted value, which
discharges goal 3 by construction rather than by argument.

### 4.2 Large objects stay outside the arena

`sprout_gc_alloc_block` gives any object with `slot_bytes > SPROUT_LARGE_THRESHOLD` (4096)
its own `malloc` block of arbitrary size, recorded as a region with `is_large = 1`,
`slotmap = NULL`. Such a block may exceed 1 MiB, so a single 20-bit shift cannot index it.

**Proposal: split the region table in two.**

- **Arena chunks** — indexed O(1) by shift, as above. This is the hot path.
- **Large regions** — remain a sorted array with today's binary search, in a *separate*
  table.

This keeps the large-object allocation path completely unchanged, and it makes the fallback
cheaper than today's search even when it is taken, because the searched table no longer
contains the ~110 normal regions. When no large objects exist, the fallback is one
`lo < hi` test.

The alternative — allocating large objects from the arena as multi-chunk runs, with every
covered chunk index pointing at the head region (Go's approach, whose arena map exists
precisely so *"large spans … can cross arenas"*) — is strictly more invasive and is not
proposed for a first step.

### 4.3 Graceful degradation (goal 4)

Two failure paths, both falling back rather than aborting:

- **Reservation unavailable** at startup → `g_arena_committed` stays 0, every membership
  test fails immediately, and all regions are allocated by `malloc` into the large/overflow
  table. Behaviour is today's, plus one always-false compare.
- **Arena exhausted** at run time → further normal regions are `malloc`'d into the overflow
  table. Correctness is unaffected; only the fast path is lost for those regions.

This makes the change safe to land before the reservation-size policy is fully tuned.

**Reservation size** is a policy question. Observed peak for the heaviest workload is
~130 MB (compiler emit at the new `adapt_factor` default). A default reservation in the low
GiB range, overridable by an env var and reduced by halving on `mmap` failure, gives
generous headroom at zero physical cost. The exact figure should be settled with the
implementation, not here.

### 4.4 Region release

Today both release sites call `free(r->base)` (`runtime/sprout_runtime.c:2040` for a dead
large object, `:2201` for an empty normal region). Under the arena, a released chunk returns
to a free-chunk list instead of being `free`d; physical pages may additionally be dropped
with `madvise` (`MADV_DONTNEED` on linux, `MADV_FREE` on darwin) so that RSS still falls
when the heap shrinks. Large objects keep `free()` unchanged.

`tests/stdlib/test_gc_region_release.spr` already covers the shrink path and must keep
passing.

## 5. Syntax and semantics impact

None. This is entirely below the language surface — no Sprout program can observe it except
through timing and RSS.

## 6. Type-system impact

None.

## 7. Error-message impact

No user-facing diagnostics change. New internal failure modes are `mmap`/`mprotect`
failures, which take the fallback paths in §4.3 rather than producing errors. One new
optional diagnostic line under `SPROUT_DEBUG_GC` reporting reservation size and arena vs
overflow region counts.

## 8. Compatibility / migration

No source, ABI, or IR change: `sprout_heap_lookup`'s signature and contract are untouched,
so emitted IR must remain byte-identical (`just ir-golden-diff`). Runtime-internal only, so
no seed refresh and no `APPROVED_BUILTINS` entry (no new `long long` builtin).

Platform surface: `mmap`/`mprotect`/`madvise` on darwin and linux, both already POSIX
targets for this runtime. No Windows support is implied because none exists today.

## 9. Tests

1. **Membership exactness is the safety property, so it gets adversarial coverage first:**
   non-heap words (stack addresses, small integers, an address just below `g_arena_base`,
   one just past `g_arena_committed`), interior words of a live object, and pointers to
   freed-but-slotmap-set slots must all be rejected — matching today's behaviour exactly.
   Existing coverage must be located first and extended, not duplicated.
2. **The fast path must be provably taken.** Per the lesson recorded from PR #48, an
   optimisation that silently falls back is indistinguishable from one that works, and every
   existing test would still pass. Add a counter for arena-resolved vs overflow-resolved
   lookups and a gate asserting the arena path dominates on a normal workload. Without this,
   a mis-sized reservation turns the whole change into a no-op that looks green.
3. **Both degradation paths exercised:** an env var forcing reservation failure, and one
   forcing a tiny arena so overflow is hit, each with the suite still passing.
4. **Large objects:** allocate `> 1 MiB` payloads and confirm they resolve via the overflow
   table and are released correctly.
5. `SPROUT_GC_HDRCHECK=1` and `SPROUT_GC_STRESS=1` stay green; `just test-stress`,
   `just ci-fast-gates`, `just compile-examples-stage1`, `just run-example-canary`.
6. **Re-measure.** The ~14% is a bound on the search's cost, not a promise of the win. The
   same interleaved A/B from §1 should be re-run; part of the 14% is the `is_large` test,
   slotmap probe, and header read that this change does not touch.

## 10. Spec / docs status

**Experimental, non-normative.** `docs/spec-v0.md` says nothing about heap layout and needs
no change. On implementation, update `docs/compiler-internals.md` (the GC ABI and region
arena section) and `docs/development.md` (any new env var), and record the re-measured
result here in §1.

## 11. Sources

- `go/src/runtime/malloc.go` — arena size and alignment, contiguity hints, two-level arena map.
- `mmtk-core/src/util/heap/layout/vm_layout.rs` — 64-bit contiguous layout constants,
  `force_use_contiguous_spaces`, `address_mask()`.
- `hboehm.info/gc/gcdescr.html` — two-level page table for candidate pointers.
- `openjdk/jdk` `src/hotspot/share/oops/compressedOops.hpp` — checked; documents base+shift
  encoding modes but not a contiguity rationale, so not cited as support above.

## 12. Measured outcome

The design works, but **the first three implementations of it were slower than the binary
search they replaced.** Every regression came from a fixed cost added to a hot inlined path,
not from the algorithm. Each is recorded here because none of them is visible in a
correctness test, and a future edit can reintroduce any of them.

### 12.1 Results

Interleaved before/after, same session, `min` over repetitions (medians agree in direction).
"Before" and "after" are the *same emitted IR* linked against the two runtimes, so nothing
but the runtime differs.

| workload | regions | before | after | delta | after-wins |
|---|---|---|---|---|---|
| **compiler emit** (`ast_to_ir.sprout`) | **47** | 2096 ms | **1985 ms** | **−5.3% (med −7.3%)** | **97%** |
| `digit_recognizer` | — | 516 ms | 515 ms | −0.2% | — |
| `math_transcendental` | — | 164.5 ms | 163.6 ms | −0.6% | — |
| `http_log_middleware` | — | 4853 ms | 4876 ms | +0.5% | — |
| `nqueens` | — | 2304 ms | 2309 ms | +0.2% (med +0.5%) | 39% |
| `retain_none` | — | 10.91 ms | 10.83 ms | −0.8% | 33% |
| **`astar`** | **1** | 25.24 ms | 25.64 ms | **+1.6% (med +1.3%)** | 22% |

"after-wins" is the share of all before/after pairings in which the arena build was faster;
50% means no effect. The compiler's 97% and astar's 22% are both real signals — the middle
rows are noise-dominated and should be read as "no change".

**The −5.3% is well short of §1's ~14% bound, exactly as §1 predicted**: the bound covered
every caller of `region_find`, and the arena removes only the search, not the `is_large`
test, slotmap probe, or header read that follow it.

**astar's +1.6% is a real, accepted cost.** It holds exactly *one* region for its entire run
(100 logged cycles), so its "binary search" was a single iteration — already O(1). No
arena can beat that, and the region-count gate (§12.4) only reduces the residual to the two
inline gate tests. Single-region programs pay ~1.3%; programs with a heap large enough for
GC to matter gain 5–7%.

### 12.2 Lost inlining cost more than the algorithm gained

`sprout_heap_lookup` is inlined into the mark loop and the root scan, and only while it
stays small. Putting the arena fast path and the binary search in one function pushed it
past the inliner's threshold — it appeared in `nm` output where the original had no symbol
at all — and that alone cost **astar +7% and cut the compiler's win from ~14% to 4–6%**.

Fixed by moving the search into an out-of-line `region_find_slow`, so the inlined footprint
is *smaller* than the original rather than larger. **Any future edit here must preserve
that**; `nm <obj> | grep heap_lookup` finding a symbol is the warning sign, though note it
is a weak signal in both directions — one cold caller can keep an out-of-line copy alive
while the hot sites are still inlined. Trust the A/B, not the symbol table.

### 12.3 The reindex was O(arena_chunks), not O(regions)

`arena_reindex` initially cleared the whole chunk→index map (4096 entries at the default
reservation) before rebuilding. Region churn makes that hot: nqueens releases and reopens
regions on nearly every one of its 8,279 collections, so the clear ran to tens of millions
of stores — the same order as the lookups the arena exists to remove. Measured as a
1.6–6.9% regression across nqueens/astar/digit_recognizer.

Fixed by `arena_reindex_from(start)`, which repairs only entries at or above the shifted
position and never clears: a chunk given to a new region is fixed by that walk, a released
chunk is set to NONE by `arena_chunk_release`, and an unused chunk holds NONE from init.

### 12.4 The win scales with log2(regions); the cost does not

Hence `SPROUT_ARENA_MIN_REGIONS` (8): below it, the search is already ~O(1) and the arena's
range test plus chunk-map load is pure loss. Correctness is independent of the threshold —
`region_find_slow` searches the table that holds arena regions too, so either branch answers
correctly for any address. Only speed depends on it.

### 12.5 The dominant query is "this is not a pointer at all"

The rooting protocol hands `sprout_heap_lookup` every scalar in every root slot, and small
`Int`s dominate. Those used to walk the entire binary search before returning NULL. A
conservative global `[g_heap_lo, g_heap_lo + g_heap_span)` bound now rejects them with one
unsigned compare, inline, with no call — worth astar +6.9% → +2.7% on its own. The bound is
grown on insert and never shrunk: a stale-wide span merely falls through to the search,
which then answers correctly, whereas a too-narrow one would reject a real pointer.

### 12.6 What was measured wrong along the way

Two methodology errors, both caught, both worth avoiding next time:

- **Linking `.ll` without `-O2`.** `just compile-native` passes `-O2` alongside the IR, so it
  optimises the *Sprout* code too. Linking pre-built runtime objects against an
  unoptimised `.ll` made `digit_recognizer` 10× slower (5.21 s vs 0.53 s) and shifted GC's
  share of runtime, producing a fabricated −12.9% "win".
- **Non-interleaved repetitions.** A first pass attributed +2.1% to `madvise` on nqueens;
  interleaved runs put all four variants within ±0.5%. `madvise` was never the cost.
