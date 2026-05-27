# N-Queens Benchmark

A cross-language performance comparison for the N-Queens problem, written to
understand how Sprout's runtime characteristics (GC, persistent vectors) compare
to compiled and interpreted alternatives.

## The Problem

Place N queens on an N×N chess board so no two queens attack each other.
Count all distinct solutions. No output of arrangements — pure counting.

**Known answers:** N=8 → 92, N=10 → 724, N=12 → 14,200, N=13 → 73,712.

---

## The Algorithm

All implementations use the same backtracking recurrence:

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
(pure variants) or mutates-and-undoes them (mutable variants).

### Variants

| Variant | Description | Allocation per placement |
|---|---|---|
| **pure** | Copy all 3 constraint arrays on each queen placement | O(n) per step |
| **mutable** | Write `true`, recurse to next row, write `false` to undo | O(1) per step |
| **bitmask** (Go only) | Encode constraints as 3 integers; iterate only legal columns | 0 — no arrays |

The bitmask variant uses the Richards encoding: `cols` is a permanent bitmask
of occupied columns; `ld`/`rd` are diagonal masks that shift left/right by one
bit on each row descent.

---

## Files

```
bench/nqueens/
├── nqueens.hs          Haskell — pure, UArray Int Bool (bit-packed, unboxed)
├── nqueens_boxed.hs    Haskell — pure, Array Int Bool  (boxed, pointer-per-element)
├── nqueens_pure.rb     Ruby    — pure (Array#dup per placement)
├── nqueens_mut.rb      Ruby    — mutable backtracking
├── nqueens_pure.py     Python  — pure (list[:] slice copy per placement)
├── nqueens_mut.py      Python  — mutable backtracking
├── nqueens.go          Go      — pure + mutable + bitmask (one file, three variants)
├── bench.sh            Compile all languages, then run and print results
└── .gitignore          Excludes bin/ from version control
```

The Sprout source lives at `../../examples/nqueens.sprout`.

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
# From the repo root:
bash bench/nqueens/bench.sh
```

The script compiles Haskell and Go (plus the Sprout binary via
`just compile-native`) into `bench/nqueens/bin/`, then runs every variant and
prints internal per-N timings. Compiled-language times are execution-only;
Python and Ruby include interpreter startup (~50 ms).

To time only the pre-built Sprout binary (excluding compilation):

```bash
/usr/bin/time -l bench/nqueens/bin/nqueens_sprout
```

---

## Results

Measured on Apple M1 (arm64-darwin), 2026-05-27.

### N=12 (14,200 solutions) — primary benchmark point

| Implementation | Time (ms) | vs Sprout |
|---|---:|---:|
| Go bitmask | 4.6 | 353× |
| Go mutable | 71 | 23× |
| Go pure | 74 | 22× |
| Haskell `UArray` (unboxed) | 108 | 15× |
| Haskell `Array` (boxed) | 174 | 9× |
| Ruby mutable | 984 | 1.6× |
| Ruby pure | 1,192 | 1.3× |
| Python mutable | 1,266 | 1.2× |
| Python pure | 1,572 | 1.0× |
| **Sprout** (clang -O2, exec only) | **~1,620** | **1×** |

### Full progression across N

```
N=8 (92 solutions):
  Go bitmask     0.01 ms   Go mutable  0.12 ms   Go pure      0.15 ms
  Haskell UArray 0.2  ms   Haskell Box 0.6  ms
  Ruby mutable   1.6  ms   Ruby pure   3.2  ms
  Python mutable 1.2  ms   Python pure 1.5  ms

N=10 (724 solutions):
  Go bitmask     0.18 ms   Go mutable  2.5  ms   Go pure      3.5  ms
  Haskell UArray 4.6  ms   Haskell Box 11.8 ms
  Ruby mutable   34   ms   Ruby pure   44   ms
  Python mutable 26   ms   Python pure 32   ms

N=12 (14,200 solutions):
  Go bitmask     4.6  ms   Go mutable  71   ms   Go pure      74   ms
  Haskell UArray 108  ms   Haskell Box 174  ms
  Ruby mutable   984  ms   Ruby pure   1192 ms
  Python mutable 1266 ms   Python pure 1572 ms
  Sprout         ~1620 ms  (exec only; full pipeline including clang -O2 is ~7 s)

N=13 (73,712 solutions):
  Go bitmask     25   ms   Go mutable  416  ms   Go pure      416  ms
  Haskell UArray 559  ms   Haskell Box 913  ms
  (Python/Ruby/Sprout not measured at N=13 — too slow)
```

---

## Analysis

### Why Sprout compiles to native code but runs at Python speed

Sprout's `vec_set` copies the underlying `Vector` and allocates a new heap
object tracked by Sprout's GC. With millions of backtracking nodes at N=12,
this generates enormous GC pressure. The binary retired **29 billion
instructions** in 1.6 s — most of that is the conservative GC tracing and
sweeping short-lived `Vec` objects.

GHC's generational GC collects short-lived heap objects in its nursery at
near-zero marginal cost, which is why boxed Haskell (same copy-per-step
algorithm) is 9× faster despite also allocating per step.

### Why unboxed (UArray) beats boxed (Array) by only 1.6× instead of ~58×

For N=12 the total state is 58 booleans. `UArray Int Bool` stores these as
**58 bits = 8 bytes** (bit-packed `ByteArray#`). `Array Int Bool` stores 58
pointers (464 bytes) that all point to the two shared GHC singletons `True`
and `False` — no per-Bool allocation. So `//` on the boxed version copies
464 bytes of pointers; on the unboxed version it copies 8 bytes.

Despite the 58× difference in copy size, the runtime gap is only 1.6× because:

1. **Recursive call overhead dominates** — each backtracking node pays for
   two Haskell function calls (closures, stack frames, argument evaluation).
2. **Both fit in L1 cache** — 464 bytes is still tiny; no cache-miss pressure.
3. **GHC optimises Bool access well** — `not (arr ! i)` compiles to a branch
   on a known-small integer in both cases.

The unboxed advantage grows with N (larger arrays, more copying) and in
workloads that are bandwidth-limited rather than call-overhead-limited.

### Why mutable barely helps in Ruby/Python (~1.2–1.6×) but a lot in Go

In Go, allocating and copying a `[]bool` slice (even 8–23 bytes) still
invokes the allocator and GC write barriers. Removing that gives a measurable
speedup at N=12 but the ratio narrows at small N because call overhead
dominates.

In Python and Ruby, the bottleneck is per-call overhead (Python frame
creation, bytecode dispatch; Ruby method dispatch). Eliminating the list copy
saves ~20–25% work — noticeable but the interpreter is the real floor.

### The bitmask ceiling

The Go bitmask variant is 15–90× faster than the array variants depending on
N. At N=12 it beats Go-mutable by 15×; at N=13 by 17×. The gap widens with N
because:

- No allocation: zero GC pressure regardless of tree size.
- Only valid columns are iterated (the `while available` loop skips attacked
  positions entirely, vs the array versions which try every column and
  check).
- All state fits in three CPU registers.

The bitmask approach is limited to N ≤ 63 (or 31 on 32-bit) because the
column mask must fit in a machine word.
