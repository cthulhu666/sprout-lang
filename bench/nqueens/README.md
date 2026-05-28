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

Measured on Apple M1 (arm64-darwin). Sprout numbers updated 2026-05-28 post type-aware-rooting fix.

### N=12 (14,200 solutions) — primary benchmark point

| Implementation | Time (ms) | vs Sprout (post-P0) |
|---|---:|---:|
| Go bitmask | 4.6 | 202× |
| Go mutable | 71 | 13× |
| Go pure | 74 | 13× |
| Haskell `UArray` (unboxed) | 108 | 8.6× |
| Haskell `Array` (boxed) | 174 | 5.3× |
| Ruby mutable | 984 | 1.06× |
| Ruby pure | 1,192 | 1.28× |
| Python mutable | 1,266 | 1.36× |
| Python pure | 1,572 | 1.69× |
| **Sprout** (clang -O2, exec only) — post-P0 type-aware rooting (2026-05-28) | **928** | **1×** |
| Sprout — pre-P0 baseline (CPR-only, 2026-05-27) | ~1,500–2,600 | 1.6–2.8× slower than current |
| Sprout — pre-CPR baseline (2026-05-26) | ~1,620 | 1.7× slower than current |

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
  Sprout (post-P0)  928 ms  (exec only; clang -O2)

N=13 (73,712 solutions):
  Go bitmask     25   ms   Go mutable  416  ms   Go pure      416  ms
  Haskell UArray 559  ms   Haskell Box 913  ms
  Sprout (post-P0) 5,700 ms  (Python/Ruby not measured at N=13 — too slow)
```

Optimization trajectory and next steps: see [`docs/nqueens-optim-iteration-2026-05-28.md`](../../docs/nqueens-optim-iteration-2026-05-28.md).
Target: match Haskell UArray (~108 ms N=12). Remaining gap ~8.6× after P0; next iteration is P1 (inline GC root push/pop or enable LTO).

---

## Analysis

### Why Sprout still trails Haskell despite compiling to native code

**Updated 2026-05-28** — the prior framing (GC tracing dominates) turned out
to be wrong; the actual bottleneck was the GC root push/pop function-call
overhead. See [`docs/nqueens-optim-iteration-2026-05-28.md`](../../docs/nqueens-optim-iteration-2026-05-28.md) for the full profile-driven analysis.

Before any optimization (2026-05-26), the Sprout N=12 binary retired ~29
billion instructions in 1.6 s. The first hypothesis blamed `vec_set` copying
and GC sweep cost; that was partly correct but wasn't the dominant term.

After CPR unboxing (2026-05-27) the per-N=12 cost dropped ~10% and the
instruction count fell modestly. The next assumption — that
`register_managed_ptr` (per-allocation GC bookkeeping) was the residual
bottleneck — was overturned by an actual CPU sample profile.

The real residual cost was **GC root push/pop** (`sprout_gc_push_i64_root`,
`sprout_gc_pop_roots`) at **67% of CPU time** — every heap-valued temporary
in codegen emitted an external call into the C runtime to register a root.
Reading the emitted IR for `queens` showed that ~50% of those calls were
pure waste: the codegen pushed roots for `Int` arguments because Sprout's
`Int` is `i64` at the LLVM level, the same as boxed ADT handles, and the
push helper used the LLVM type rather than the source-level Sprout type.

The N-queens P0 fix (2026-05-28) added type-aware rooting that skips push
for `Int`/`Bool`/`Char` arguments. N=12 dropped from ~1,500 ms to **928 ms**;
total instructions retired across the full run dropped from 159 B to 114 B
(−28%). Post-P0 the push/pop CPU share is **~44%** — still dominant; the
remaining pushes are for genuine heap pointers (Vec args) that type
filtering can't eliminate. The next attack target is the function-call
boundary itself (P1: inline push/pop as IR, or enable `-flto`).

GHC's generational GC collects short-lived heap objects in its nursery at
near-zero marginal cost; once Sprout's per-push overhead is gone, the
remaining gap will need attacking allocation cost too (P3 True/False/Nil
singletons, P4 bump-allocated nursery — see backlog).

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
