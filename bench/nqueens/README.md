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
A bitmask implementation is ~50× faster than a persistent one *in the same
language*, so a cross-group comparison measures the representation, not the
language. `bench.sh` prints them in three separate sections for that reason,
and the results below keep them apart.

| Representation | State | Allocation per placement | Search tree |
|---|---|---|---|
| **1. persistent / copy-on-write** | 3 boolean arrays, copied | O(n) | tries every column, tests each |
| **2. mutable in-place** | 3 boolean arrays, written and undone | none | tries every column, tests each |
| **3. bitmask** | 3 `Int`s | none | visits only safe columns |

Sprout appears in groups 1 and 3. Group 2 is expressible — `stdlib/mutable.sprout`
has `MutVec` with `mutvec_new`/`mutvec_set`/`mutvec_at` — but every one of those
is `!{IO}`, so a mutable variant would turn the whole search effectful. It is
simply not written yet, not impossible.

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

The Sprout sources live at `../../examples/nqueens.sprout` (group 1) and
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

The script compiles Haskell and Go (plus both Sprout binaries via
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

Measured on Apple M1 (arm64-darwin), 2026-09-25, one machine and one session —
except the group-1 Sprout row, re-measured 2026-09-26 after element inlining
landed, interleaved against Go in that session (Go re-read 63.7 / 378.4 ms
against its recorded 64 / 381, so the two sessions are comparable).
Compiled languages are execution-only. **Every N=12 and N=13 figure is a median
of interleaved runs** — 5 for the compiled group-1 and group-3 entries, 3 for
the interpreted ones and group 2; the smaller-N progression is a single pass.
Interleaving is not ceremony: running one implementation five times and then the
next hands the second a differently-heated machine, which produced a spurious
2× gap during this very refresh.

### Representation 1 — persistent / copy-on-write

| Implementation | N=12 (ms) | N=13 (ms) | vs fastest |
|---|---:|---:|---:|
| Go — `[]bool` + copy | 64 | 381 | 1.0× |
| Haskell — `UArray Int Bool` (unboxed) | 96 | 556 | 1.5× |
| Haskell — `Array Int Bool` (boxed) | 162 | 945 | 2.5× |
| **Sprout — `Vec Bool`** | **196** | **1,157** | **3.1×** |
| Ruby — `Array#dup` | 1,215 | 6,986 | 19× |
| Python — `list[:]` | 1,612 | 8,574 | 25× |

### Representation 2 — mutable in-place

| Implementation | N=12 (ms) | N=13 (ms) | vs fastest |
|---|---:|---:|---:|
| Go — `[]bool` mutate/undo | 68 | 403 | 1.0× |
| Ruby — mutate/undo | 966 | 5,624 | 14× |
| Python — mutate/undo | 1,214 | 8,975 | 18× |

### Representation 3 — bitmask

| Implementation | N=12 (ms) | N=13 (ms) | vs fastest |
|---|---:|---:|---:|
| **Sprout — `Int` masks** | **4.6** | **24.9** | **1.0×** |
| Go — `int` masks | 4.5 | 24.7 | 1.0× |

Sprout and Go are indistinguishable here — 2% apart at N=12, 1% at N=13, both
inside the run-to-run spread.

### Smaller N (single pass)

```
                          N=8      N=10
group 1  Go pure          0.11 ms  2.2 ms
         Haskell UArray   0.3      5.2
         Haskell Array    0.4      7.6
         Sprout Vec       0.5     11.1
         Python list[:]   1.5     31.3
         Ruby dup         3.3     45.4

group 2  Go mutable       0.16     2.2
         Python mutate    1.2     25.9
         Ruby mutate      2.0     42.2

group 3  Sprout masks     0.0      0.4
         Go masks         0.01     0.20
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

Sprout's bitmask variant at N=12 (4.6 ms) is **14× faster than Go's persistent
variant** (65 ms), and 52× faster than Sprout's own persistent variant. The
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
| `nqueens_bitmask.sprout` | 67 | 1 |

The 67 are the result strings; the search itself allocates nothing. With no
allocation there is no GC, no rooting and no write traffic, so what remains is
integer ALU work in a tight recursion — and Sprout's LLVM backend emits that as
well as Go's does.

### Why Sprout trails on the persistent representation

3.1× behind Go and 2.0× behind Haskell's unboxed `UArray`, and the cost is
allocation, not codegen. Each `vec_set` allocates **two** objects: the `Vec`
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

### Mutable helps the interpreters and does nothing for Go

Comparing group 2 against group 1 within each language, at N=12:

| Language | persistent | mutable | mutable gain |
|---|---:|---:|---:|
| Go | 64 ms | 68 ms | **0.94×** (slower) |
| Ruby | 1,215 ms | 966 ms | 1.26× |
| Python | 1,612 ms | 1,214 ms | 1.33× |

In Python and Ruby the bottleneck is per-call overhead — frame creation and
bytecode dispatch, method dispatch — but the copy is a whole extra O(n)
interpreter-level operation per placement, so removing it is worth ~1.3×.

In Go it is worth nothing. The arrays are 8–23 bytes, well inside one cache
line, and Go's escape analysis keeps the copies off the heap; meanwhile the
mutable version pays six writes per placement (three to set, three to undo) that
the pure version does not. The two effects cancel, and the mutable variant came
out *slightly* slower in all three rounds.

This reverses what this file used to claim ("mutable barely helps in Ruby/Python
but a lot in Go", from a 71-vs-74 ms pair). The direction was wrong in both
halves.

### The bitmask ceiling

Group 3 beats group 1 by ~50× at N=12 in both Sprout and Go, and the gap holds
as N grows. Three reasons:

- No allocation: zero GC pressure regardless of tree size.
- A smaller tree: only safe columns are visited, where the array versions try
  every column and reject most of them.
- All state fits in three CPU registers.

The approach is limited to N ≤ 63 because the column mask must fit in a machine
word — the one respect in which it is less general than the array versions.
