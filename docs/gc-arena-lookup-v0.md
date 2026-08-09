# O(1) address→region lookup via a reserved arena

Status: **design, not implemented.** No code changes accompany this document. The
measurement in §1 is reproducible today; everything from §4 onward is a proposal awaiting
approval.

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
