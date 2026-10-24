# A* Benchmark

A cross-language performance comparison for A* pathfinding, complementing the
N-Queens benchmark by exercising a different algorithmic profile: priority-queue
management, hash-table lookups, and the interplay between data structure choice
and the functional/imperative divide.

## The Problem

Find the shortest path from `(0,0)` to `(99,99)` on a 100×100 grid with a
deterministic wall pattern. Each port sets its own run count in its source and
reports µs per run.

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

| Concern | Sprout (`astar_linvec`) | Haskell | Go | Java | JS | Python | Ruby |
|---|---|---|---|---|---|---|---|
| Open set | Linear sorted list (O(n) insert, one allocation) | Sorted list (O(n) insert) | Sorted slice, in-place copy (O(n) insert, O(1) amortised alloc) | `PriorityQueue` (O(log n)) | Sorted array (O(n) insert) | `heapq` (O(log n)) | `bsearch_index` (O(n) insert) |
| g_score | `LinVec Int` — O(1) in-place write, pure | `IOUArray` — O(1) mutable | `[10000]int` — O(1) mutable | `int[]` — O(1) mutable | `Int32Array` — O(1) mutable | `list` — O(1) mutable | `Array` — O(1) mutable |
| Closed | `LinVec Int` (0/1) — O(1) in-place write, pure | `IOUArray` — O(1) mutable | `[10000]bool` — O(1) mutable | `boolean[]` — O(1) mutable | `Uint8Array` — O(1) mutable | `list` — O(1) mutable | `Array` — O(1) mutable |

`LinVec a` is a linear vector (Experimental, spec §5.8): a set consumes it and
returns it, so no one can see the old version, and `linvec.set` writes in place.
The search is a pure function. `astar.sprout` keeps the earlier version for
comparison: `MutVec Int` arrays, which make the search `!{IO}`, and a sorted list
of tuples.

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

The Sprout sources are `../../examples/astar.sprout` (`MutVec`) and
`../../examples/astar_linvec.sprout` (`LinVec`).

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

**The ports do not run the same search yet.** On equal `f` they break ties
differently, and on this grid that changes the work ~20× (see *Tie-breaks*
below). Only rows with the same rule compare. The cross-language table that
stood here mixed rules: its "Sprout beats Go" came from Go expanding 4,178 nodes
to Sprout's 198. It has been removed; one rule for every port is open work in
`BACKLOG.md`.

Apple M1 (arm64-darwin), 2026-10-24, medians of 7 interleaved rounds, order
rotated each round:

| Implementation | On equal `f` | µs/run |
|---|---|---:|
| Go, `<` in place of `<=` in `insert` (for this table only) | newer first | 18 |
| **Sprout, `astar_linvec.sprout`** | newer first | **35** |
| Sprout, `astar.sprout` (`MutVec`) | newer first | 161 |
| Go, as committed | older first | 1,533 |

Under one rule Go is 1.9× faster than Sprout's `LinVec` version, which is 4.6×
faster than the `MutVec` one (see *The LinVec version* below).

### Tie-breaks

Moving right or down raises `g` by 1 and lowers the Manhattan `h` by 1, so every
cell reachable from the start by right and down moves has `f = 198`: 4,179 of
the 7,045 open cells. A* pops nothing else before the goal, so the tie-break
decides the whole search order.

| Port | On equal `f` | Why |
|---|---|---|
| Sprout, Haskell | newer entry first | inserts before equal entries |
| Go, JavaScript, Ruby | older entry first | inserts after equal entries |
| Python | smaller `g` first | `heapq` compares the whole `(f, g, x, y)` tuple |
| Java | depends on heap layout | the `PriorityQueue` comparator reads `f` only |

Newer-first follows one right/down path to the goal; older-first is
breadth-first across the plateau. Counted in Go, per run:

| | newer first | older first |
|---|---:|---:|
| nodes expanded | 198 | 4,178 |
| inserts | 334 | 5,663 |
| average open-list length | 68 | 734 |
| entries walked per insert | 14 | 221 |

The list grows because older-first keeps a whole diagonal of `f = 198` entries
ahead of each insert, and the `f = 200` entries it pushes (one step away from
the goal) are never popped. Of the old table, only the Sprout and Haskell rows
shared a rule.

---

## Analysis

### Why open-set insert is O(n) in most variants

The sorted-list open set was chosen to match Sprout's stdlib, which has no
native priority queue. Go, JavaScript, and Ruby use the same O(n) sorted
insertion, but pop the older of two equal-`f` entries where Sprout pops the
newer, so they run a different search (see *Tie-breaks*). Java and Python use
real O(log n) heaps; their advantage in the open-set dimension is therefore a
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

Result: **676 µs/run**, a >170× improvement. The Go and Java numbers it was
read against used other tie-breaks (see *Tie-breaks*).

### The LinVec version (2026-10-24)

In `astar_linvec.sprout`, `g_score` and `closed` are `LinVec Int` and the open
set is a linear list. Linearity lets `linvec.set` write in place, and reuse
writes each rebuilt list node and `State` into the cell it was matched from
(`docs/linear-reuse-v0.md`). Over 100 runs it allocates 33,915 objects, one node
per insert, against the `MutVec` version's 536,314: that insert copies every
list cell it walks past and boxes each entry in a tuple.

The insert walks twice, onto an accumulator and back, in two tail-recursive
loops. A recursive insert (`Node(h, insert(rest))`) keeps a stack frame and two
GC roots for each entry it passes: 51 µs/run here against the loops' 35, and
under the older-first rule, where an insert walks 221 entries, 13,300 against
4,600. BACKLOG *Tail recursion modulo constructor (TRMC)* would make the
recursive form compile to one loop.
