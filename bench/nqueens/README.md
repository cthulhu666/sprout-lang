# N-Queens Benchmark

A cross-language performance comparison for the N-Queens problem, written to
understand how Sprout's runtime characteristics (GC, persistent vectors) compare
to compiled and interpreted alternatives.

## The Problem

Place N queens on an N×N chess board so no two queens attack each other.
Count all distinct solutions. No output of arrangements — pure counting.

**Known answers:** N=8 → 92, N=10 → 724, N=12 → 14,200, N=13 → 73,712,
N=14 → 365,596, N=15 → 2,279,184, N=16 → 14,772,512, N=17 → 95,815,104.

---

## Three representations, measured separately

This is the one thing to understand before reading any number below. The
implementations fall into three groups by **how the constraint state is
represented**, and the groups do materially different amounts of work per node.
A bitmask implementation is 14× (Go) to 52× (Sprout) faster than a persistent
one *in the same language*, so a cross-group comparison measures the representation, not the
language. `bench.sh` prints them in three separate sections for that reason,
and the results below keep them apart.

| Representation | State | Allocation per placement | Search tree |
|---|---|---|---|
| **1. persistent / copy-on-write** | 3 boolean arrays, copied | O(n) | tries every column, tests each |
| **2. mutable in-place** | 3 boolean arrays, written and undone | none | tries every column, tests each |
| **3. bitmask** | 3 `Int`s | none | visits only safe columns |

Sprout appears in all three. Its group-2 entry uses `LinVec`, a linear vector
written in place, so the search stays pure. `MutVec` could express it too, but
every `MutVec` operation is `!{IO}`, which would make the whole search effectful.

---

## The Algorithm

Groups 1 and 2 share this backtracking recurrence:

```
queens(n, row, col, cols, pos_diag, neg_diag):
  if row == n  → found a solution: return 1
  if col >= n  → exhausted columns in this row: return 0
  otherwise    → return  (skip col)  +  (place at col if safe)
```

Three boolean constraint arrays give O(1) conflict checking:

| Array | Index | Size | Tracks |
|---|---|---|---|
| `cols` | `col` | n | column c is occupied |
| `pos_diag` | `row + col` | 2n−1 | positive diagonal (r+c = const) |
| `neg_diag` | `row − col + n − 1` | 2n−1 | negative diagonal (r−c = const) |

The **skip** branch is computed before any mutation so it always sees the
original constraint arrays. The **place** branch either copies the arrays
(group 1) or mutates-and-undoes them (group 2).

Group 3 uses the Richards encoding instead. Each of the three `Int`s is a set of
columns *in the current row*: `cols` is the permanently occupied columns, `ld`
and `rd` are the columns attacked along each diagonal direction. Descending a
row moves every diagonal threat sideways by exactly one column, so `ld` shifts
left and `rd` shifts right on the recursive call — a diagonal is tracked without
ever indexing `r+c`. `mask &^ (cols|ld|rd)` is then the set of safe columns, and
the loop takes them one at a time with `x & -x`. Limited to N ≤ 63.

---

## Files

```
bench/nqueens/
├── nqueens.hs          Haskell — group 1, UArray Int Bool (bit-packed, unboxed)
├── nqueens_boxed.hs    Haskell — group 1, Array Int Bool  (boxed, pointer-per-element)
├── nqueens_pure.rb     Ruby    — group 1 (Array#dup per placement)
├── nqueens_mut.rb      Ruby    — group 2
├── nqueens_pure.py     Python  — group 1 (list[:] slice copy per placement)
├── nqueens_mut.py      Python  — group 2
├── nqueens.go          Go      — all three groups in one file, selected by argument
├── bench.sh            Compile everything, then run and print by representation
└── .gitignore          Excludes bin/ from version control
```

The Sprout sources live at `../../examples/nqueens.sprout` (group 1),
`../../examples/nqueens_linvec.sprout` (group 2) and
`../../examples/nqueens_bitmask.sprout` (group 3).

---

## Requirements

| Language | Tool | Tested version |
|---|---|---|
| Haskell | `ghc` | 9.10.1 |
| Go | `go` | 1.25.1 |
| Python | `python3` | 3.12.13 |
| Ruby | `ruby` | 3.1.4 |
| Sprout | `just compile-native` | stage-1 compiler |

---

## Running

```bash
# From the repo root — every section, ~2 minutes:
bash bench/nqueens/bench.sh

# One representation on a settled machine:
bash bench/nqueens/bench.sh bitmask      # or: persistent, mutable
```

The script compiles Haskell and Go (plus all three Sprout binaries via
`just compile-native`) into `bench/nqueens/bin/`, then runs every variant
grouped by representation.

**A single full pass is indicative, not a measurement.** The sections run in
order, so by the time the bitmask section starts, a minute of Ruby and Python
has heated the machine — enough to make Sprout's bitmask read ~8.4 ms instead of
~4.6 and invert its comparison with Go. Name a section to avoid this, and treat
the tables below (interleaved medians) as the real numbers.

The Go binary takes a variant and an optional explicit N ladder, which is how
the scaling study below was run:

```bash
bench/nqueens/bin/nqueens_go bitmask 13 14 15 16 17
```

---

## Results

Measured on Apple M1 (arm64-darwin), 2026-10-24, one machine and one session.
Compiled languages are execution-only. **Every figure is a median of interleaved
runs**: 5 rounds for the compiled entries, their order rotated each round, then
3 for the interpreted ones, run after them because they heat the machine. The
range column is min–max at N=13. The bitmask scaling study further down is
older (2026-09-25). Interleaving is not ceremony: running one implementation
five times and then the next hands the second a differently-heated machine,
which produced a spurious 2× gap during the 2026-09-25 refresh.

### Representation 1 — persistent / copy-on-write

| Implementation | N=12 (ms) | N=13 (ms) | N=13 range | vs fastest |
|---|---:|---:|---:|---:|
| Go — `[]bool` + copy | 62.3 | 373.7 | 370–376 | 1.0× |
| Haskell — `UArray Int Bool` (unboxed) | 92.2 | 533.1 | 527–538 | 1.43× |
| Haskell — `Array Int Bool` (boxed) | 156.4 | 895.5 | 889–910 | 2.40× |
| **Sprout — `Vec Bool`** | **207.1** | **1,215** | 1,208–1,218 | **3.25×** |
| Ruby — `Array#dup` | 1,185 | 6,856 | 6,841–6,901 | 18× |
| Python — `list[:]` | 1,545 | 8,526 | 8,391–8,928 | 23× |

### Representation 2 — mutable in-place

| Implementation | N=12 (ms) | N=13 (ms) | N=13 range | vs fastest |
|---|---:|---:|---:|---:|
| **Sprout — `LinVec Bool`** | **64.4** | **367.2** | 364–369 | **1.0×** |
| Go — `[]bool` mutate/undo | 64.6 | 392.3 | 388–398 | 1.07× |
| Ruby — mutate/undo | 965 | 5,622 | 5,614–5,623 | 15× |
| Python — mutate/undo | 1,209 | 8,489 | 7,965–8,990 | 23× |

Sprout and Go are level at N=12. At N=13 Go takes 7% longer, and the two
ranges do not overlap.

### Representation 3 — bitmask

| Implementation | N=12 (ms) | N=13 (ms) | N=13 range | vs fastest |
|---|---:|---:|---:|---:|
| **Sprout — `Int` masks** | **4.0** | **22.7** | 21.8–33.6 | **1.0×** |
| Go — `int` masks | 4.5 | 25.0 | 24.8–26.2 | 1.10× |

Sprout's median is 10% ahead at both sizes, but one of its five rounds was slow
(9.3 / 33.6 ms), and the scaling study below puts the two 3–12% apart. Read it
as parity, with Sprout marginally ahead.

### Smaller N (medians, same session)

```
                          N=8      N=10
group 1  Go copy          0.11 ms  2.1 ms
         Haskell UArray   0.2      3.5
         Haskell Array    0.3      5.9
         Sprout Vec       0.3      6.9
         Python list[:]   1.5     30.7
         Ruby dup         3.6     45.2

group 2  Sprout LinVec    0.1      2.2
         Go mutable       0.11     2.3
         Python mutate    1.2     25.6
         Ruby mutate      1.6     34.1

group 3  Sprout masks     0.0      0.1
         Go masks         0.01     0.2
```

### Bitmask scaling: does Go pull ahead at large N?

No. Medians of 3 interleaved rounds, N=13 through N=17 (95.8M solutions):

| N | Sprout (ms) | Go (ms) | Go / Sprout |
|---:|---:|---:|---:|
| 13 | 25.2 | 27.6 | 1.09 |
| 14 | 149 | 160 | 1.07 |
| 15 | 928 | 1,037 | 1.12 |
| 16 | 6,337 | 6,849 | 1.08 |
| 17 | 47,660 | 49,255 | 1.03 |

The ratio is flat — no crossover, and no trend toward one. Sprout is marginally
ahead at every point, by less than the gap between two consecutive rounds of the
same binary, so the honest reading is parity that holds as the search tree grows
four orders of magnitude.

---

## Analysis

### The representation dominates the language

Sprout's bitmask variant at N=12 (4.0 ms) is **16× faster than Go's persistent
variant** (62 ms), and 52× faster than Sprout's own persistent variant. The
single largest performance fact in this benchmark is which of the three groups
an implementation is in — not which language it is written in.

This is why the old single flat table here was misleading: it invited reading
"Sprout 928 ms" against "Go bitmask 4.6 ms" as a 200× language gap, when most of
that ratio was two different algorithms.

### Why Sprout reaches Go's speed on the bitmask representation

Because the representation removes the thing Sprout is slower at. Counted with
`SPROUT_DEBUG_ALLOC=1` over the full N=1..13 ladder:

| Variant | Allocations | GC cycles |
|---|---:|---:|
| `nqueens.sprout` (persistent) | 50,118,983 | 8,279 |
| `nqueens_linvec.sprout` (mutable) | 91 | 1 |
| `nqueens_bitmask.sprout` | 67 | 1 |

The 67 are the result strings; the search itself allocates nothing. The
`LinVec` variant adds three vectors and one board per N, 24 objects, and its
search allocates nothing either: every rebuild of the linear `Board` reuses the
cell it matched (`docs/linear-reuse-v0.md`). With no
allocation there is no GC, no rooting and no write traffic, so what remains is
integer ALU work in a tight recursion — and Sprout's LLVM backend emits that as
well as Go's does.

### Why Sprout trails on the persistent representation

3.25× behind Go and 2.3× behind Haskell's unboxed `UArray` at N=13, and the
cost is mostly allocation, not codegen. Part of the gap is recent: 94372cc0,
which retyped the CPR reads as `i128`, made this program 8% slower (its parent
runs N=13 in 1,121 ms against its 1,214, paired; issue #716, cause not yet
known). Each `vec_set` allocates **two** objects: the `Vec`
constructor wrapper and the `VectorVal`, which since 2026-09-25 carries its
elements inside its own slot. That is 16.7M placements × 2 = 33.4M allocations
at N=1..13.

It was three, and 50.1M. The third was the `long long*` element buffer, the one
payload the GC did not slot-allocate: `sprout_alloc_vector_data` called bare
`malloc` and the sweep called `free(v->data)`, so every `vec_set` made a full
round-trip to the system allocator while every OBJ, CLOSURE, TUPLE and MAP was a
freelist pop inside a region. That round-trip was ~23% of CPU
(`libsystem_malloc` plus `madvise`). Inlining removed it for vectors up to 508
elements — the largest that keeps the slot under `SPROUT_LARGE_THRESHOLD` — and
this benchmark's are 12–27. Peak RSS fell too, since one allocation per vector
retires the malloc heap's own bookkeeping.

Two levers are left, and they are independent. The `Vec` constructor wrapper is
still a separate one-field box around a `VectorVal` that already has a header —
half of what remains, and a representation change rather than a runtime one. And
GC rooting is ~20% of the whole-program-linked binary; the recursive `queens(…)`
re-roots three vectors its caller already holds, which is a precision problem for
the liveness pass in `stdlib/compiler/ir_rooting.sprout`.

Two claims that used to be in this section are now wrong and have been removed:
that GC root push/pop is ~44% of CPU (whole-program linking inlines both
entirely — they do not appear in the profile at all), and that enabling LTO is
the next step (`just compile-native` already whole-program links). Both came
from [`docs/archive/nqueens-optim-iteration-2026-05-28.md`](../../docs/archive/nqueens-optim-iteration-2026-05-28.md),
which was accurate when written and has since been overtaken by the work it
recommended.

### Why unboxed (UArray) beats boxed (Array) by only 1.7×

For N=12 the total state is 58 booleans. `UArray Int Bool` stores these as
**58 bits = 8 bytes** (bit-packed `ByteArray#`). `Array Int Bool` stores 58
pointers (464 bytes) that all point to the two shared GHC singletons `True`
and `False` — no per-Bool allocation. So `//` on the boxed version copies
464 bytes of pointers; on the unboxed version it copies 8 bytes.

Despite the 58× difference in copy size, the runtime gap is only 1.7× because:

1. **Recursive call overhead dominates** — each backtracking node pays for
   two Haskell function calls (closures, stack frames, argument evaluation).
2. **Both fit in L1 cache** — 464 bytes is still tiny; no cache-miss pressure.
3. **GHC optimises Bool access well** — `not (arr ! i)` compiles to a branch
   on a known-small integer in both cases.

The unboxed advantage grows with N and in workloads that are bandwidth-limited
rather than call-overhead-limited.

### Mutable helps Sprout and the interpreters, and does nothing for Go

Comparing group 2 against group 1 within each language, at N=12, all from the
session above:

| Language | persistent | mutable | mutable gain |
|---|---:|---:|---:|
| Sprout | 207 ms | 64 ms | **3.2×** |
| Go | 62 ms | 65 ms | **0.96×** (slower) |
| Ruby | 1,185 ms | 965 ms | 1.23× |
| Python | 1,545 ms | 1,209 ms | 1.28× |

In Sprout the copy is the expensive part. Each `vec_set` allocates two heap
objects (see "Why Sprout trails on the persistent representation" above), and
the mutable variant drops the N=1..13 run from 50 million allocations and 8,279
collections to 91 and one. It pays three extra writes per placement to undo, as
Go's does, and still runs 3× faster.

In Python and Ruby the bottleneck is per-call overhead — frame creation and
bytecode dispatch, method dispatch — but the copy is a whole extra O(n)
interpreter-level operation per placement, so removing it is worth ~1.25×.

In Go it is worth nothing. The arrays are 8–23 bytes, well inside one cache
line, and Go's escape analysis keeps the copies off the heap; meanwhile the
mutable version pays six writes per placement (three to set, three to undo) that
the pure version does not. The two effects cancel, and the mutable variant came
out *slightly* slower in all five rounds, at both N=12 and N=13.

This reverses what this file used to claim ("mutable barely helps in Ruby/Python
but a lot in Go", from a 71-vs-74 ms pair). The direction was wrong in both
halves.

### The bitmask ceiling

Group 3 beats group 1 at N=12 by 52× in Sprout and 14× in Go, and the gap holds
at N=13. Sprout's ratio is larger because its group 1 also pays for allocation.
Three reasons for the gap itself:

- No allocation: zero GC pressure regardless of tree size.
- A smaller tree: only safe columns are visited, where the array versions try
  every column and reject most of them.
- All state fits in three CPU registers.

The approach is limited to N ≤ 63 because the column mask must fit in a machine
word — the one respect in which it is less general than the array versions.
