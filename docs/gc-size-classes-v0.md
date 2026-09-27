# Size-class freelists and the absence of coalescing (v0, 2026-09-27)

**Status:** records prior art and the design space. No collector change is proposed
here. Normative GC behaviour remains
[docs/compiler-internals.md §Non-moving GC](compiler-internals.md).

Written because commit 4535204c (inline Vec elements) moved vector element storage out
of `malloc` and onto these rules, and the two `BACKLOG` entries it produced
(retained-by-class bytes; exact-fit remainders) both ask "is this shape normal?"
without anywhere to record the answer. It is normal. §4 is where Sprout actually
differs, and it is not where it looks.

## 1. The two rules

**Rule 1 — exact-size reuse.** `g_freelist` is indexed by `slot_bytes / 16`
(`SPROUT_FREELIST_CLASSES` = 257, covering 16 B up to `SPROUT_LARGE_THRESHOLD` = 4096 B),
and `sprout_gc_alloc_block` pops only the requested class. A free 4064-byte slot is
invisible to a 4080-byte request beside it and to every 32-byte one.

**Rule 2 — no merging of adjacent free slots.** The sweep rebuilds the freelists from
its slot walk; two neighbouring FREE slots stay two slots of their own classes. Memory
leaves a class only when a whole 1 MiB region has no live and no poison slot, at which
point the sweep recycles the arena chunk (`arena_chunk_release`, which `madvise`s, so
RSS falls) or `free`s a malloc'd region.

## 2. Prior-art survey (primary-sourced)

Every row verified against the implementation's own source or reference manual; URLs in
§8. Immix is surveyed separately in [gc-generational-v0.md §3](gc-generational-v0.md).

| Runtime | Reuse | Coalescing of free small objects | Reclaim granularity |
|---|---|---|---|
| **Go** | ~70 size classes, "each of which has its own free set of objects of exactly that size" | none — a page is "split into a set of objects of one size class", tracked by a free bitmap | the span: "if all objects in the mspan are free, the mspan's pages are returned to the mheap" |
| **TCMalloc** | 60–80 size classes, one per span; does not split a larger free object to serve a smaller request | none between objects; adjacent **pages** are merged when returned to the pageheap | page |
| **jemalloc** | slabs of one size class with a per-slab bitmap; four classes per doubling | not within a slab; split/merge exist at the **extent** layer | slab / extent |
| **OCaml 5** | per-size-class pools of uniform blocks (`global_avail_pools[NUM_SIZECLASSES]`) | no — a free entry's `wosize` run-length encodes consecutive free blocks, it does not create a larger allocatable block | the pool, when entirely empty |
| **GHC `--nonmoving-gc`** | "a family of allocators, each serving a range of allocation sizes"; 32 KB segments of one fixed block size, liveness bitmap | none between blocks; the **block allocator** underneath coalesces bgroups with their neighbours in O(1) | the segment (`SEGMENT_FREE` → free pool, `SEGMENT_PARTIAL` → active list) |
| **GHC default** | n/a — "generational copying garbage collector for all generations" | n/a, objects move | evacuated space |
| **Rust** | none of its own: `System` is "based on `malloc` on Unix platforms and `HeapAlloc` on Windows" | yes, by delegation | chunk |
| **glibc malloc** | "for large (>= 512 bytes) requests, it is a pure best-fit allocator"; splits chunks | **yes, always**: "no consolidated chunk physically borders another one" | chunk, via consolidation |

**The rules are the majority design, and the exceptions are informative.** Every
non-moving managed heap in the table makes both of Sprout's choices. The two rows that
coalesce are general-purpose C allocators — and one of them, glibc, is exactly where Vec
element buffers lived before 4535204c. That commit did not invent an unusual rule; it
moved vectors from the family that merges into the family that does not, and inherited
that family's known cost.

**The dividing line is whether the heap moves objects.** GHC's default and Immix say yes
and need no size classes at all. Everything that says no — Go, TCMalloc, jemalloc,
OCaml 5, GHC's nonmoving collector, Sprout — converges on fixed classes, no coalescing,
and whole-span reclaim. Sprout's rules should be read as consequences of "non-moving",
not as separate decisions.

## 3. Rust is the case with no rule, and is still instructive

Rust has no GC, so the question becomes which allocator it delegates to, and the answer
is the platform's. Two second-order points transfer:

- **`Vec` growth is `realloc`.** `RawVec::finish_grow` calls `Allocator::grow`, which for
  `System` calls `realloc`. Asking the allocator to extend in place only works because
  free chunks next door have been consolidated into something big enough — the move a
  size-class heap cannot make, because there is no "next door" within a class.
- **Rust never has the defect 4535204c fixed**, because a `Vec` is always a header plus a
  separate buffer, so no inline area is ever vacated. Sprout inlines precisely to avoid
  that second allocation and buys the vacated-tail problem with it. A real trade, not an
  oversight.

## 4. Where Sprout actually diverges: regions are mixed-class

Go, TCMalloc, jemalloc, OCaml 5 and GHC's nonmoving collector all put **one size class
per span / slab / pool**. Sprout's 1 MiB regions hold **mixed** sizes — the bump
allocator carves whatever the next request needs. That single difference explains both
things about the design that look odd:

- **Why the slotmap exists.** In a uniform span an object's start is
  `(addr - base) / elemsize`; there is no start bit to store and none to clear. Sprout
  must record slot starts explicitly, and `sprout_heap_lookup`'s membership exactness
  rests on that record.
- **Why coalescing is harder here than in the prior art.** The others never face it:
  merging inside a uniform span is meaningless. Sprout's version must clear a slotmap
  bit, and that bit is load-bearing for pointer-vs-integer exactness — which is why the
  `BACKLOG` entry warns that coalescing without a `slotmap_clear` trips HDRCHECK's "no
  start bit strictly inside a step" assertion.

**Sprout also lacks the lower layer.** TCMalloc coalesces pages in its pageheap; GHC's
block allocator coalesces bgroups under the nonmoving heap — "when a bgroup is freed
(`freeGroup()`), we can check whether it can be coalesced with other free bgroups by
checking the neighbours … in O(1) time". Both give memory a route out of a class that
cannot reuse it. Sprout's regions are fixed at 1 MiB and never merge, so it has the
size-class layer and not the layer beneath. That gap — not the missing intra-region
coalescing — is the closer match to what the prior art does.

## 5. The option the prior art points at, which no `BACKLOG` entry names

**Make regions single-class.** Then coalescing is moot, the slotmap becomes derivable
rather than stored, and whole-region release gets likelier because a region's occupants
share a size and so tend to share a lifetime profile. It is a larger change than either
filed entry (re-carving remainders; coalescing in the sweep walk) and it interacts with
the arena lookup, but it is the shape five of six managed heaps actually use. Recorded
for consideration, not proposed: it must not be started before the instrument in §6
exists, because nothing today can say whether class retention costs anything.

## 6. Two things GHC ships that Sprout's backlog wants

**The class table is a tuning knob there, not a constant.**
`--nonmoving-dense-allocator-count=⟨count⟩`, default 16, documented as: "Increasing this
value is likely to decrease the amount of memory lost to internal fragmentation while
marginally increasing the baseline memory requirements." GHC's dense classes step by one
word from 8 bytes and go log₂ above that. Sprout's 257 classes step by 16 bytes
throughout — finer than GHC in the tail, coarser at the bottom, and chosen once without
measurement.

**The instrument already exists there, and is a usable template.**
`rts/sm/NonMovingCensus.c` is "a simple space accounting census useful for characterising
fragmentation in the nonmoving heap". Per allocator it reports active segments, filled
segments, live blocks, live words, and occupancy — "the fraction of space that is used
for useful data (that is, live and not slop)" — wired to eventlog class `n`, described in
the user's guide as "census information to characterise heap fragmentation".

That is the retained-by-class instrument the `BACKLOG` asks for, already specified by
someone who needed it for the same reason. The field set to copy, per class: live bytes,
slot bytes held, region count, and their ratio. **The instrument is a prerequisite for
every option in §5 and for both filed entries** — this allocator family's characteristic
failure is invisible without it, which is why GHC built one.

## 7. What this survey does not settle

Whether any of it matters for Sprout. No workload has been measured for class retention,
and 4535204c measured peak RSS *down* on two shapes. The survey establishes that the
rules are standard and that the real divergence is mixed-class regions. It does not
establish that there is a problem to fix.

## 8. Sources

- Go — `src/runtime/malloc.go`
  https://raw.githubusercontent.com/golang/go/master/src/runtime/malloc.go
- TCMalloc — `docs/design.md`
  https://raw.githubusercontent.com/google/tcmalloc/master/docs/design.md
- jemalloc — jemalloc(3) https://jemalloc.net/jemalloc.3.html
- OCaml 5 — `runtime/shared_heap.c`
  https://raw.githubusercontent.com/ocaml/ocaml/trunk/runtime/shared_heap.c
- GHC nonmoving GC — `rts/sm/NonMoving.c`, `NonMoving.h`, `NonMovingSweep.c`,
  `NonMovingCensus.c`, `BlockAlloc.c`
  https://gitlab.haskell.org/ghc/ghc/-/tree/master/rts/sm
- GHC RTS flags (`--copying-gc`, `--nonmoving-gc`,
  `--nonmoving-dense-allocator-count`) —
  https://downloads.haskell.org/ghc/latest/docs/users_guide/runtime_control.html
- Rust — `library/std/src/alloc.rs`, `library/std/src/sys/alloc/unix.rs`,
  `library/alloc/src/raw_vec/mod.rs`
  https://github.com/rust-lang/rust/tree/master/library
- glibc malloc — `malloc/malloc.c`
  https://raw.githubusercontent.com/bminor/glibc/master/malloc/malloc.c
