# `stdlib.linheap`: a pure binary min-heap — v0

**Status — landed** (`stdlib/linheap.sprout`). Experimental, as `LinVec` is (spec §5.8). A stdlib
module written in Sprout over `LinVec`: no builtin, no new trust boundary, so `spec-v0.md` is
untouched.

## 1. Problem

Sprout has no priority queue. Dijkstra, A*, schedulers, top-k and event simulation each hand-roll
one, and the A* benchmark's sorted-list open set is O(n) per insert. `docs/persistent-array-v0.md`
§5.3 lists a heap among the structures to write "directly" over `LinVec`; none exists.

The A* ports also need one rule on equal keys (BACKLOG *A\* benchmark ports break ties on equal
`f` differently*). A heap whose key carries an insertion counter gives every port the same search.

## 2. Goals and non-goals

**Goals**
1. A min-heap ordered by `Ord`, with O(log n) push and pop and O(n) build from a list, pure,
   writing in place.
2. Usable at any non-linear element type; fast at a key type that derives `Ord` (§5).

**Non-goals**
- **Stability.** Equal elements pop in no promised order. A caller who needs one puts a counter in
  the key (§4.4).
- **Max-heap, decrease-key, merge, capacity hints.** Max order is a key type whose `Ord` is
  reversed; the rest waits for a caller.
- **Linear elements.** `LinVec` rejects them, since a read copies the element; `LinHeap` inherits
  that.
- **Fast tuple keys.** Their cost is in the tuple `Ord` instance, not the heap (§5).

## 3. Prior art

| Language | Standard heap | Order | Equal keys | Build from a collection |
|---|---|---|---|---|
| Rust | `std::collections::BinaryHeap` | max by `Ord`; `Reverse` for min | docs silent | `From<Vec<T>>`, "in-place … O(n)" |
| Python | `heapq`, functions over a list | min by `<` | a listed challenge; docs add an entry count | `heapify`, "in linear time" |
| Go | `container/heap`, over the user's `heap.Interface` | min by the user's `Less` | up to `Less` | `heap.Init`, O(n) |
| Java | `java.util.PriorityQueue` | least first; natural order or a `Comparator` | "ties are broken arbitrarily" | `PriorityQueue(Collection)`, cost not stated |
| .NET | `PriorityQueue<TElement,TPriority>` | lowest priority first; `IComparer<TPriority>` | "does not guarantee first-in-first-out" | constructor from `IEnumerable<(TElement, TPriority)>` |
| OCaml 5.4 | `Stdlib.Pqueue`, functors `MakeMin`/`MakeMax` over an ordered type | min or max | "Nothing is guaranteed" | `of_array`, "linear time" |
| Haskell | none in `containers` | | | |

Every standard heap orders by a comparison and leaves ties unordered; Sprout's comparison is the
`Ord` class, so the heap takes `where Ord a`. Rust's `binary_heap` module docs build Dijkstra on a
`State` struct with its own `Ord`, the key shape §5 recommends. Sources: the Rust `BinaryHeap` and
`binary_heap` docs, Python `heapq` docs, `pkg.go.dev/container/heap`, the JDK 21 `PriorityQueue`
API, Microsoft Learn's `PriorityQueue<TElement,TPriority>`, OCaml 5.4 `Stdlib.Pqueue`, the
`containers` module list on Hackage.

## 4. Design

### 4.1 Representation and invariant

```sprout
export type linear LinHeap a = | HeapEmpty | Heap Int (LinVec a)   # size, storage
```

Exported without `(..)`, so it is abstract (spec §5.6.1; §5.8 *Publishing a linear type*).
**Invariant: `Heap n v` has
`1 ≤ n ≤ linvec.length(v)`, and `HeapEmpty` is the only empty heap.** A pop that removes the last
element drops the storage and returns `HeapEmpty`. `HeapEmpty` exists because `linvec.new` needs a
value to fill with and an empty heap has none: the first push allocates capacity 4, filled with its
element. It is nullary, so an empty heap allocates nothing.

### 4.2 Growth and retention

A push at `n = capacity` copies into a `LinVec` of `2 * capacity`. A pop that leaves
`4 * n <= capacity` copies into one of `capacity / 2`, which is at least `2 * n`, so the invariant
holds. There is no floor. Each copy drops the old storage. Both are amortised O(1): after a grow
the heap is half full and after a shrink half full, so at least `capacity / 4` operations separate
two copies, which pay for the O(capacity) copy.

Slots past `n` are not cleared: `LinVec` has no empty value to write. They hold copies of elements
that were in the heap, so up to `capacity - n` popped values stay reachable until a push overwrites
them or the storage shrinks. After every operation `capacity <= 4 * n` (the first push and a
one-element `linheap.from_list` meet it with equality), so at most `3 * n` stale slots remain.

The copying is a private helper here, not a `linvec.resize` in `stdlib.linvec`: that would extend
the `LinVec` API in spec §5.8 for one caller. It moves there when a second structure from
`docs/persistent-array-v0.md` §5.3 (ring buffer, hash map) needs it.

### 4.3 API

Data last (guidelines §6), called through the module as `stdlib.linvec` is:

```
linheap.new() -> LinHeap a
linheap.from_list(xs: List a) -> LinHeap a                 where Ord a   # O(n), Floyd
linheap.push(x: a, h: LinHeap a) -> LinHeap a              where Ord a   # O(log n) amortised
linheap.pop(h: LinHeap a) -> Maybe (a, LinHeap a)          where Ord a   # O(log n) amortised
linheap.peek(h: borrowing LinHeap a) -> Maybe a                          # O(1)
linheap.size(h: borrowing LinHeap a) -> Int
linheap.drop(h: LinHeap a) -> Unit
```

`linheap.pop` on an empty heap returns `Nothing`, and the heap is gone: the `Nothing` branch has
nothing to drop, and `linheap.new()` is free. The alternative, `(Maybe a, LinHeap a)`, keeps the
heap but makes every `Nothing` branch drop it.

`linheap.from_list` is in v0 because Rust, Python, Go and OCaml document linear-time builds, and a
top-k or a Dijkstra seeded from many sources otherwise pays O(n log n). Its capacity is the list's
length (at least 4), so the invariant holds from the start.

### 4.4 Ties

The heap compares whole elements. A caller who wants `(f, insertion order)` keeps a counter and
puts it in the key, as Python's docs show; counting up pops equal `f` oldest first, counting down
newest first. Which rule the A* ports share is decided in their PR, not here.

## 5. Cost

A* on a 100×100 grid, M1, 2026-10-24: the range of µs per run over 5–15 runs in one to three
sessions, and `closure_apply` per 100 runs. The two columns are the two tie-break rules. The
design's row is the first; the other heap rows are a grow-only prototype starting at capacity 16.
A* does not call `linheap.from_list`, so nothing here measures it.

| Open set | newer first | older first | `closure_apply`, older first |
|---|---:|---:|---:|
| **heap as designed: derived key, capacity 4, quarter shrink** | **59–62** | **1,336–1,392** | 7,792,500 |
| heap, hand-specialised compare (not an API) | 42–45 | 935–1,135 | 0 |
| heap, key type `deriving (Eq, Ord)` | 58–68 | 1,353–1,586 | 7,792,500 |
| heap, key type with a hand-written `Ord` | 57–66 | 1,283–1,485 | 7,792,500 |
| heap, comparator passed per call | 51–56 | 1,147–1,358 | not counted |
| heap, tuple key `(Int, Int, Int, Int, Int)` | 156–165 | 3,357–3,994 | 19,247,700 |
| persistent leftist heap, hand-written `Ord` key | 53–107 | 3,473–3,709 | 6,866,400 |
| linear sorted list (`examples/astar_linvec.sprout`) | 33–38 | 4,541–4,840 | 0 |
| Go, committed O(n) sorted slice | not run | 1,480–1,550 | — |

Shrinking costs nothing visible: 6 more `linvec.new` per run under older-first, within noise of
the grow-only row. Go's median, 1,533, is the one in `bench/astar/README.md`.

**Key type.** A derived key costs what a hand-written one does: one indirect `compare` per
comparison, from the heap's `where Ord a`, while the derived body calls `Ord Int` directly. A tuple
key costs about 2.5× more, because `Ord (a, b, c, d, e) where Ord a, …` reaches each field through a
context dictionary. `docs/specialisation-v0.md` §4 *Limits* does not copy instance bodies, so it
leaves tuple keys as they are. If it lands (it is a proposal, not approved), it removes a derived
key's remaining call, which should bring it near the hand-specialised row; that is a projection,
not built. Instance-body specialisation is open work in `BACKLOG.md`, after it.

**Rejected.** A comparator API is no faster than a derived key (one indirect call either way) and
adds a second ordering mechanism beside `Ord`. A persistent leftist heap, as
`stdlib/compiler/scc.sprout` has, needs no linearity but allocates a node per merge step: 7.16M
objects per 100 runs under older-first against 1.40M, and about 2.7× the time.

**Against the list.** Under newer-first the open set averages ~68 entries and the list wins
(33–38 µs): the heap allocates 2.2× the objects (74,525 against 33,915 per 100 runs). Under
older-first it averages ~730 entries (counted in Go). In one interleaved session the designed heap
ran 1,336–1,392 µs, the list 4,651–4,840 (3.3–3.6×) and Go's O(n) slice 1,514–1,550 (Go 9–16%
slower). That Go row is not a heap: the tie-break BACKLOG item moves every port to one.

## 6. Typing, linearity, diagnostics

No new rules. `LinHeap a` is linear, so every path consumes it (spec §5.8 leak rule). Borrowed
`match` binds the storage borrowed, so `linheap.peek` and `linheap.size` read without consuming.
An unbounded `a` may be copied and dropped (`docs/linearity-bound-v0.md`), so a heap of linear
elements is rejected at the call that instantiates it, with the existing message. `where Ord a` at
a type with no instance gives the existing missing-instance error.

## 7. Tests

`tests/stdlib/test_linheap.spr`, written before the module:
- draining a heap yields what was pushed, sorted (`vec_sort`): duplicates, negatives, 200+
  elements;
- growth across several doublings (past 16, 32, 64) with `linheap.size` checked at each, and
  shrinking as it drains;
- `linheap.pop` and `linheap.peek` on empty are `Nothing`; `linheap.peek` equals the next pop;
- push, pop to empty, then `linheap.size`, `linheap.peek`, `linheap.pop` and a push again;
- `linheap.from_list` at lengths 0, 1, 2, 7 and 13, each drained and each followed by push/pop
  sweeps across the grow and quarter thresholds; it drains in the same order as pushing one by
  one, with keys ordered on every field so the equality does not depend on layout;
- a derived key type with a counter pops equal first fields in counter order;
- keys other than `Int`: `String`, `Maybe Int`, `Double`;
- the heap called from a `where Ord a` caller (a `top_k(k, xs)`), at `Int` and at a derived key,
  so the forwarded-dictionary path runs.

Growth and shrinking are invisible through the API, so a case beside `tests/linear_reuse_smoke/`
fills and drains a 64-element heap and checks the runtime's `vector` counter against the grows
and shrinks that must happen. `scripts/linear_reuse_smoke.sh` compares `sprout_obj` across two
sizes today, so it gains a mode that checks one counter against an expected value.

`tests/conformance/type_error/`, after the `linvec_*` cases: `Heap`/`HeapEmpty` not constructible
or matchable outside the module; a heap of linear elements rejected (message shaped like
`linvec_nested.err`); a heap left unconsumed fires the leak rule.

## 8. Docs

`docs/stdlib-reference.md` §`stdlib.linheap` lists the API with §5's key-type advice;
`docs/persistent-array-v0.md` §5.3's table points its heap row here.
