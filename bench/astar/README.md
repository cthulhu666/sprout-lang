# A* Benchmark

A cross-language performance comparison for A* pathfinding, complementing the
N-Queens benchmark by exercising a different algorithmic profile: priority-queue
management, hash-table lookups, and the interplay between data structure choice
and the functional/imperative divide.

## The Problem

Find the shortest path from `(0,0)` to `(99,99)` on a 100×100 grid with a
deterministic wall pattern. Each language uses its own iteration count tuned for
~1 s total; see the results table for per-language ITERS.

**Wall rule:**
```
is_wall(x, y) = x > 0 && y > 0 && x < 99 && y < 99 && (x*5 + y*3) % 13 < 4
```
~31% of interior cells are walls; the border is always clear, so a path always
exists. The optimal path is 198 steps (Manhattan distance from corner to corner),
and A* typically finds it through partially clear interior cells.

---

## The Algorithm

All implementations use the same lazy-deletion A* variant:

```
open ← [(h(start), 0, start)]    # sorted by f ascending
g_score[start] ← 0

while open not empty:
  pop (_, g, x, y) with smallest f
  if (x,y) == goal → return g
  if closed[x,y]   → skip (lazy deletion handles stale entries)
  close (x,y)
  for each cardinal neighbour (nx, ny):
    ng ← g + 1
    if ng < g_score[nx,ny]:
      g_score[nx,ny] ← ng
      push (ng + h(nx,ny), ng, nx, ny) onto open
```

**Lazy deletion** allows duplicate entries in the open set: when a better path
to a node is found, a new entry is pushed without removing the old one. Stale
entries are discarded when popped (closed check). This avoids the O(n)
"decrease-key" scan needed on a plain sorted list.

---

## Data Structures

| Concern | Sprout | Haskell | Go | Java | JS | Python | Ruby |
|---|---|---|---|---|---|---|---|
| Open set | Sorted `List` (O(n) insert) | Sorted list (O(n) insert) | Sorted slice, in-place copy (O(n) insert, O(1) amortised alloc) | `PriorityQueue` (O(log n)) | Sorted array (O(n) insert) | `heapq` (O(log n)) | `bsearch_index` (O(n) insert) |
| g_score | `MutVec Int` — O(1) in-place write | `IOUArray` — O(1) mutable | `[10000]int` — O(1) mutable | `int[]` — O(1) mutable | `Int32Array` — O(1) mutable | `list` — O(1) mutable | `Array` — O(1) mutable |
| Closed | `MutVec Int` (0/1) — O(1) in-place write | `IOUArray` — O(1) mutable | `[10000]bool` — O(1) mutable | `boolean[]` — O(1) mutable | `Uint8Array` — O(1) mutable | `list` — O(1) mutable | `Array` — O(1) mutable |

`MutVec a` is Sprout's mutable array type (`MutVec (Vector a)` ADT wrapping a
GC-managed `VectorVal`). `mutvec_set` writes directly into the existing slot
via the `vector_mutset` C primitive — no allocation, no copy. The constructor
is unexported so the backing store can never be aliased through a `Vec` handle.

---

## Files

```
bench/astar/
├── astar.hs        Haskell — mutable IOUArray, sorted-list open set, getSystemTime
├── astar.go        Go      — mutable arrays, sorted-slice open set
├── Astar.java      Java    — mutable arrays, PriorityQueue (O(log n))
├── astar.js        JavaScript (Node.js) — TypedArrays, sorted-array open set
├── astar.py        Python  — mutable lists, heapq (O(log n))
├── astar.rb        Ruby    — mutable arrays, bsearch_index open set
├── bench.sh        Compile all languages, then run and print results
└── .gitignore      Excludes bin/ from version control
```

The Sprout source lives at `../../examples/astar.sprout`.

---

## Requirements

| Language | Tool | Notes |
|---|---|---|
| Haskell | `ghc` | Requires `array` package (bundled with GHC) |
| Go | `go` | No external dependencies |
| Java | `javac` + `java` | JDK 11+ |
| JavaScript | `node` | Node.js 12+ (`process.hrtime.bigint`) |
| Python | `python3` | 3.8+ |
| Ruby | `ruby` | 2.7+ (`bsearch_index`) |
| Sprout | `just compile-native` | stage-1 compiler |

---

## Running

```bash
# From the repo root:
bash bench/astar/bench.sh
```

The script compiles Haskell, Go, Java, and the Sprout binary into
`bench/astar/bin/`, then runs all languages and prints timing.

---

## Results

Measured on Apple M1 (arm64-darwin), 100×100 grid, ~1 s per language.
Path length 198 steps (optimal, confirmed by all implementations).
Each language uses its own ITERS so the total run time is comparable.

| Implementation | ITERS | Total (ms) | Per run (µs) | vs Haskell |
|---|---:|---:|---:|---:|
| Haskell (ghc -O2, mutable IOUArray, sorted list) | 60,000 | **848** | **14** | 1× |
| Java (JIT warmed, PriorityQueue) | 9,000 | 866 | 96 | 6.9× |
| **Sprout (clang -O2, MutVec O(1) writes, sorted list)** | **100** | **69** | **692** | **49×** |
| JavaScript (V8 JIT, sorted array, TypedArrays) | 1,300 | 968 | 744 | 53× |
| Go (sorted slice in-place, mutable arrays) | 600 | 1019 | 1,698 | 121× |
| Python 3 (heapq, mutable lists) | 200 | 854 | 4,267 | 305× |
| Ruby (bsearch\_index, mutable arrays) | 120 | 1,177 | 9,812 | 701× |

Sprout's `MutVec` implementation (2026-05-31) moves from ~116,000 µs/run
(estimated with `Vec` persistent copies) down to **~690 µs/run** — a >160×
improvement — and places Sprout between Java and JavaScript, **faster than Go**.
Numbers use 100 iterations (stable, ±5% across runs).

### Why Sprout beats Go (and why Go is slower than JavaScript)

All three use O(n) sorted open-set insertion. The difference is in HOW the
elements are moved:

- **Sprout**: cons-list insert allocates one 32-byte node and sets one pointer;
  LLVM -O2 compiles the recursive traversal without bounds checks.
- **JavaScript**: `Array.splice` shifts 8-byte pointers using V8's native
  SIMD-optimized memmove; binary-search finds the insert position in O(log n).
- **Go**: `copy(s[i+1:], s[i:...])` shifts 32-byte `entry` structs — 4× more
  bytes per element than JS's 8-byte pointer shifts, with AOT-compiled code
  that optimizes less aggressively than V8's JIT.

With ~600–2000 inserts per A* run and average open-set sizes of 50–150,
the per-element cost compounds into a 2.4× disadvantage for Go relative
to Sprout. JavaScript's pointer-shift advantage and V8's aggressive JIT
specialization (1300+ iterations) explain why Go trails JS as well.

### Why Haskell is 45× faster

Haskell's `IOUArray Int Int` stores unboxed `Int` values — no heap allocation
per element, fully cache-line friendly, single `STG store` instruction per
write. Sprout's `MutVec` stores `i64` values as `long long` in a
GC-managed `VectorVal` on the heap; GC pressure and pointer chasing add
overhead that unboxed storage avoids entirely. Closing this gap would require
unboxed `MutVec` storage, a deeper runtime change.

### Why Java is 7× faster

Java's JIT performs escape analysis, devirtualises `PriorityQueue` operations,
and eliminates boxing for `int` at hotspots. The O(log n) heap also reduces
open-set work by ~3–4× compared to Sprout's O(n) sorted-list insert.

### Observed scaling from 60×60 to 100×100

| Language | 60×60 µs/run | 100×100 µs/run | Observed scaling | Theory |
|---|---:|---:|---:|---|
| Haskell | 15 | 15 | **1.7×** | O(W²) to O(W⁴) |
| Java | 78 | 94 | **1.2×** | O(W² log W) |
| **Sprout (MutVec)** | — | **692** | — | O(W⁴) expected |
| JavaScript | 223 | 744 | **3.3×** | O(W⁴) |
| Go | 243 | 1,698 | **7.0×** | O(W⁴) |
| Python | 1,541 | 4,637 | **3.0×** | O(W² log W) |
| Ruby | 3,171 | 8,258 | **2.6×** | O(W⁴) |

---

## Analysis

### Why open-set insert is O(n) in most variants

The sorted-list open set was chosen to match Sprout's stdlib, which has no
native priority queue. Go, JavaScript, and Ruby use the same O(n) sorted
insertion for a fair apples-to-apples comparison. Java and Python use real
O(log n) heaps; their advantage in the open-set dimension is therefore a
deliberate "best of breed" measurement, not a controlled variable.

### The Go open-set allocation fix

The original Go `insert` used the three-index-slice idiom
`append(s[:i:i], e)` which **caps capacity at `i`**, forcing Go to allocate a
new backing array on every single call. Over 150 runs × ~600 inserts each,
that created ~90,000 backing arrays as garbage — a ~6× slowdown from GC
pressure alone (317 ms → 38 ms after the fix). The corrected implementation
extends the slice by one zero element with `append(*o, entry{})` (amortised
O(1) allocation when capacity > length), then shifts the tail in-place with
`copy`. This is the standard in-place sorted-insert pattern in Go.

### The MutVec transition (2026-05-31)

The original Sprout implementation used `Vec Int` (persistent functional
arrays) for `g_score` and `closed`. Each `vec_set` on a 10,000-element `Vec`
allocated a fresh copy — ~80 KB per write. With ~1,667 relaxations per run and
8 runs that was ~2.1 GB allocated and copied per benchmark execution, putting
Sprout at an estimated ~116,000 µs/run (~7,700× behind Haskell).

`MutVec Int` replaces `Vec Int` with an opaque wrapper around a raw
`VectorVal*`. `mutvec_set` calls `vector_mutset` — a single C store into the
existing slot. `mutvec_new` calls `vec_make_filled` — one `malloc` + `memset`
equivalent, O(n) total rather than O(n²) via repeated `vec_append`.

Result: **676 µs/run**, a >170× improvement, landing Sprout ahead of Go and
within ~7× of Java.
