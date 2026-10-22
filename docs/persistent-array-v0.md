# A pure array with cheap index writes (v0, 2026-10-19)

**Status:** 5.3 landed 2026-10-22 as `LinVec`, core only (§12 step 14); Experimental, as spec §5.8
is. The rest is a proposal. Comes from [byte-buffer-v0.md](byte-buffer-v0.md) §5.4, *Beyond append*.
5.3 is a `wrap linear` over `Vector` with one builtin (§12 step 12).

## 1. Problem

The only pure way to write at an index is `vec_set`, which copies the whole vector: 1 µs at
1,000 elements, 80 µs at 100,000 (§3.1). A loop of k writes over n elements costs O(n·k).
`MutVec` writes in place, but every operation is `!{IO}`, and Sprout cannot hide `IO` behind a
pure signature. There is no other pure structure to fall back on: `Dict` takes only `String` keys,
and keeps every key it was given for the life of the process (BACKLOG P2 **A `Dict` key is
interned permanently**).

**Typical programs.** Sieves, graph searches, histograms and DP tables are what people write
first in a general-purpose language, and each writes to an array by index. §3.2 measures four:
with `vec_set` they take 1.5–19 s and up to 12 GB where `MutVec` takes 0–12 ms.

**Our own code** is not bottlenecked:

- `examples/nqueens.sprout` backtracks over three `Vec Bool`s with `vec_set`. N is at most 13,
  so each vector holds at most 25 elements, and `vector_set` was 1.1% of its CPU at N≤14.
- uncharted-suns, `grimward/world_view.sprout` `owners` and `grimward/world_map.sprout`: a
  `fold_indexed` of `vec_set` over the land cells. The map is 32×24 = 768 cells, so at most 768
  copies of 768 elements, under 1 ms by §3.1, once at load.
- uncharted-suns, `grimward/warren.sprout` `hops_in`: relaxes distances with `vec_set` over "a
  handful of rooms".
- uncharted-suns, `chess/board.sprout` `set_piece`: `vec_set` on 64 squares, so the search can keep
  a parent's position while it recurses. That needs old versions.
- uncharted-suns, `meadow/sim/grid.sprout` `relinked` and `kept_field`: a `list_fold` of `vec_set`
  over the cells a block touches.

Some code pays in effects instead. These are `!{IO}` only because a `MutVec` is scratch:

- uncharted-suns, `loam/graph.sprout`: a pure `hops`, O(nodes × diameter), which
  `grimward/world_map.sprout` calls, and an `!{IO}` `bfs`, O(n), over `MutVec`.
- uncharted-suns, `loam/bitset.sprout` `bitset_of_ints`: "The scratch buffer does not escape".
- uncharted-suns, `game/route.sprout`: "`!{IO}` only because the search mutates MutVecs in place".
- `stdlib/math/bigint.sprout` multiplies by product scanning, partly because it "keeps the loop
  pure"; operand scanning "wants a mutable accumulator".

uncharted-suns has 54 tracked files that use `MutVec`; only those above were read.

## 2. Goals and non-goals

Goals:

- A pure array with O(1) writes and reads on the version being worked on.
- Typical index-write programs run within a small factor of `MutVec`.
- No change to `Vec`. New syntax only where it removes a builtin: `wrap linear` (5.3).

Non-goals:

- Replacing `MutVec` where `IO` is fine.
- Making `Vec` itself persistent-in-place (§5.5 rejects it).
- Sharing an array between OS threads; the concurrency plan is share-nothing
  ([concurrency-design-exploration-2026-07-13.md](concurrency-design-exploration-2026-07-13.md)).

## 3. Measurements

All on macOS arm64, `-O2`, one process per row, median of 3 unless stated. Benches live in a
scratch copy of the runtime, not in the repo.

### 3.1 One operation

Four structures:

- **C Baker:** `pa_new`, `pa_get` and `pa_set` in C. Each version is a 4-word vector
  `[kind, index, value, next]`. A set reroots, writes in place and allocates the new version;
  the old one becomes a diff node in place. Rerooting walks the chain iteratively and allocates
  nothing, so no collection can start inside it.
- **Sprout Baker:** the same structure over `Ref` and `MutVec Int`.
- **Trie:** a 32-way trie in plain Sprout. A set copies one 32-slot node per level.
- **`vector_set`:** today's copy.

ns per operation, random indices, median of 5:

| elements | set: C Baker | set: Sprout Baker | set: trie | set: `vector_set` | get: C Baker | get: Sprout Baker | get: trie | get: `vector_get` |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 100 | 36 | 63 | 68 | 84 | 4 | 12 | 10 | 1 |
| 1,000 | 21 | 73 | 69 | 1,029 | 3 | 15 | 10 | 1 |
| 10,000 | 22 | 67 | 153 | 10,354 | 5 | 14 | 17 | 2 |
| 100,000 | 17 | 99 | 308 | 80,371 | 4 | 10 | 21 | 2 |

The 100,000-element reads are 3 million reads, median of 3. Shorter windows measured a cold
cache, as byte-buffer-v0.md §5.4 explains, along with why the C set is slower at 100 elements.

Reading two versions `d` sets apart, alternately, per pair:

| `d` | 1 | 10 | 100 | 1,000 |
|---|---:|---:|---:|---:|
| C Baker | 8 ns | 71 ns | 0.79 µs | 7.6 µs |
| Sprout Baker | 106 ns | 0.97 µs | 11.2 µs | 160 µs |
| trie | 22 ns | 23 ns | 23 ns | 22 ns |

Correctness of the C version: 2,000 versions, each a set on a randomly chosen earlier one, then
20,000 reads of random versions compared with a `vector_set` reference. They all match, with the
collector on and under `SPROUT_GC_STRESS=1 SPROUT_GC_HDRCHECK=1`. Writing 0 instead of the old
value in the reroot loop makes all 20,000 reads mismatch.

### 3.2 Typical programs

Four kernels, each written once against `arr_new`, `arr_get` and `arr_set`, with one
implementation of those per column:

- **sieve:** count the primes up to n.
- **BFS:** cells reached from a corner of a w×w grid, one cell in four a wall, with a two-list
  queue.
- **histogram:** k hashed values into b buckets, then the largest count.
- **LCS table:** the textbook (n+1)² table of two n-long strings, one write per cell.

ms; every column returns the same checksums:

| kernel | `MutVec` (`IO`) | linear array (§5.3) | C Baker | transient trie (§5.2) | trie | `Dict` | `vec_set` |
|---|---:|---:|---:|---:|---:|---:|---:|
| sieve, n = 100,000 | 0 | | 7 | 8 | 75 | 252 | 18,849 |
| BFS, w = 200 | 5 | | 6 | 9 | 19 | 64 | 10,273 |
| histogram, 1M into 1,000 | 12 | | 36 | 33 | 165 | 412 | 1,551 |
| LCS, n = 300 | 2 | | 4 | 7 | 38 | 141 | 7,442 |
| sieve, n = 1,000,000 | 6 | 7 | 73 | 94 | 1,565 | 4,753 | |
| BFS, w = 1,000 | 1,375 | 2,165 | 183 | 259 | 583 | 3,328 | |
| histogram, 1M into 100,000 | 12 | | 50 | 68 | 390 | 1,111 | |
| LCS, n = 2,000 | 110 | | 256 | 383 | 3,440 | 15,505 | |
| peak RSS, large sizes | 53 MB | 20 MB | 363 MB | 55 MB | 130 MB | 1.4 GB | 12 GB at the small sizes |

`vec_set` ran once, at the small sizes only. The linear array is §5.3's prototype, run on the
sieve and BFS only. The transient trie is §5.2's prototype; its reads skip the bounds check.

**The large BFS is a GC measurement.** The trigger counts objects, not bytes (BACKLOG P2 **GC
trigger is object-count-blind**). With one large live array and small garbage, it stays at its
4,096-object floor and every collection re-scans the array: `MutVec` runs 868 collections, 1.2 s
of its 1.4 s. The C Baker escapes only through §3.3's retention, which keeps a million version
nodes live and lifts the trigger, so it collects 21 times. The linear array is slower than
`MutVec` for the same reason: it threads an `(array, queue)` tuple through every step, so it
sweeps 6.1 million objects against 3.6 million and collects 1,484 times against 868 (BFS alone,
`-DSPROUT_GC_PROFILE`). With the trigger raised out of the way
(`SPROUT_GC_THRESHOLD=50000000`), the large sizes read:

| kernel | `MutVec` | linear array | C Baker | transient trie | trie |
|---|---:|---:|---:|---:|---:|
| sieve, n = 1,000,000 | 5 | 7 | 39 | 102 | 652 |
| BFS, w = 1,000 | 81 | 101 | 107 | 167 | 304 |
| histogram, 1M into 100,000 | 12 | | 27 | 64 | 263 |
| LCS, n = 2,000 | 103 | | 205 | 377 | 3,305 |

The same trigger gives `vec_set` its 12 GB: each copy is one object however long, so a loop of
10,000 sets on 100,000 elements peaks at 8 GB, about every byte it allocated.

Three more results from the same runs:

- **DP does not need index writes.** Building each LCS row from a list, with no write at all,
  takes 4–6 ms at n = 300 and 195–422 ms at n = 2,000, within 2–4× of `MutVec`.
- **All four kernels only touch the newest version.** None reads an old one, so persistence
  buys them nothing, and the version check and history of a persistent array are pure cost.
- **The BFS is one self-recursive function.** Written as two, a visit step and a queue step, it
  ran out of GC roots at w = 1,000 (BACKLOG P3 **Phase B mutual-TCO**).

### 3.3 Memory

An old Baker version needs every newer diff to be read, so while it is reachable, the whole
chain is. That is Baker's design, not a bug. But reachable includes more than the programmer
sees. A caller roots each heap argument across a call to a Sprout function (BACKLOG P2 **Calls to
Sprout functions root their operands**). So in `tb <- step(pa_new(100, 0), 3000000)`, where
`step` sets in a self-tail-call loop, the first version stays rooted until `step` returns. Peak
RSS grows with the number of sets: 68 MB at 1 million, 197 MB at 3 million, about 65 bytes per
set. A `Vec` argument rooted the same way keeps one vector alive, not every version after it.

### 3.4 Reading through `Maybe`

Whether 5.3 needs a direct-read builtin. `MutVec` calls the two candidates: `mutvec_get` calls
`vector_get`, which returns `Maybe a`, and `mutvec_at` calls `vector_get_direct`, which does not.
1M reads per kernel, two runs, 2026-10-22:

| kernel | `Maybe` read | direct read |
|---|---:|---:|
| sieve, n = 1,000,000 | 7 ms | 5–6 ms |
| histogram, 1M into 100,000 | 4–5 ms | 3–4 ms |

The `Maybe` read allocates nothing: a `match` that binds the field routes the call to a CPR
worker over `vector_get_unboxed`, which returns tag and payload in one `i128`
([compiler-internals.md](compiler-internals.md) §CPR extern ABI). A literal under the constructor,
`| Just false -> …`, loses the worker, and the sieve takes ~400 ms, most of it the count-blind GC
trigger rescanning the live array (BACKLOG P2 **A literal inside a `Maybe` pattern loses CPR**).

## 4. Prior art

Each row checked against the paper, reference manual or library source, 2026-10-19.

| system | structure | newest version | old version |
|---|---|---|---|
| Baker, *Shallow binding makes functional arrays fast* (SIGPLAN Notices 26(8), 1991) | the array plus diff chains; rerooting from *Shallow Binding in Lisp 1.5* (CACM, 1978) | O(1) | with several live versions, "O(1) update complexity is lost" |
| Conchon & Filliâtre, *A Persistent Union-Find Data Structure* (ML Workshop 2007) | Baker's, with `reroot` | O(1) | the chain length, once: "we pay only once the cost of coming back to a previous version" |
| Rocq `PArray` (Coq 8.13, `kernel/parray.ml`) | Baker's, rerooting before every `get` and `set`; cites Conchon & Filliâtre §2.3 | O(1) | "O(n) access time" when every cell differs (reference manual, *Primitive arrays*) |
| Haskell `DiffArray` (package `diffarray`, split from `array` after 0.2) | diff chains in `MVar`s, never rerooted | O(1) read | reads walk the chain each time; "Updating an array which is not current makes a physical copy" |
| Clojure `PersistentVector` | 32-way trie plus a tail buffer; `assoc` copies one node per level | "log32N hops" | the same |
| Scala `immutable.Vector` | "radix-balanced finger tree of width 32"; updates "in O(log n) time" (2.13 source) | O(log n) | the same |
| Clojure transients (clojure.org *Transients*; `PersistentVector.java`) | the same trie; a node carries an `edit` token and `ensureEditable` copies a node only when its token differs, else writes in place; `transient` and `persistent!` are "O(1)" | in place after a node's first copy; "capture and use the return value in the next call" | after `persistent!`, "all operations will throw exceptions" |
| Clean (Language Report 2.2, §4.4) | unique array `*{#Int}`; `{a & [i] = v}`; a read `a![i]` "returns a tuple containing the demanded array element and the original array" | in place: "the original unique array is known not to be used anymore" | not expressible |
| Haskell `linear-base` 0.8.1, `Data.Array.Mutable.Linear` | `set :: Int -> a -> Array a %1 -> Array a`; `get` returns `(Ur a, Array a)`; allocation only through a continuation; `freeze` "O(1)" | "a pure linear interface for arrays with in-place mutation" | not expressible |
| Futhark (language reference §3.11) | `a with [i] = v` on a consumed `*[]i32` | cost "proportional to the size of the value(s) being written, not the size of the full array" | rejected: "the original array (a) is not used on any execution path following the in-place update" |
| Haskell `ST` (`base`, `Control.Monad.ST`) | `runST :: (forall s. ST s a) -> a`; `ST` "allows for destructive updates, but is escapable (unlike IO)" | in place | the `forall` "ensures that the internal state used by the `ST` computation is inaccessible to the rest of the program" |
| Koka local variables (book §3.2.4) | `var x := 0` and `x := …` inside a function whose inferred type stays `(n : int) -> int` | in place | "The lifetime of mutable local variable cannot exceed its lexical scope"; escaping through a closure is a type error |
| Lean 4 `Array.set` | in place "if there are no other references to it" (reference count 1) | O(1) | "requires time linear in the size of the array" (reference §21.16.2.1) |
| Futhark bulk operations (prelude, `soacs`) | `scatter (dest: *[k]t) (is: [n]i64) (vs: [n]t) : *[k]t`; `reduce_by_index` likewise over a consumed `dest`; `hist` "computes a generalised `k`-bin histogram" | in place on the consumed array; `scatter` work O(n) | not expressible |
| Haskell `vector` 0.13.2, `Data.Vector` | `accum :: (a -> b -> a) -> Vector a -> [(Int, b)] -> Vector a`; `(//)`, of which "The vector may not be modified in place"; `modify` "Perform[s] a safe destructive update in-place by applying an ST action to a mutable copy of the vector, then freezing the result" | a copy per bulk call | kept |
| Koka local heap effect (`lib/std/core/types.kk`) | `type local :: (H) -> X`, "A local heap effect", where `X` is "(effect type)" (Koka spec); `local-scope(action : forall<h> () -> <local<h> \| e> a) : e a` | in place | the `forall<h>` index, as `ST`'s `s` |
| Effekt regions (language tour, *Regions*) | `region r { var x in r = 1 }`: "Each mutable variable is allocated into an equally named region" | in place | the region's scope |
| Pure Borrow (Matsushita & Ishii, PLDI 2026; arXiv 2604.15290v4) | Linear Haskell vectors borrowed through `Mut` and `Share` references inside a `BO` monad, run by `runBO` (Fig. 4); `updateAt :: β ≤ α ⇒ Int → (a ⊸ BOβ (b, a)) ⊸ Mutα (Vector a) ⊸ BOβ (b, Mutα (Vector a))` (Fig. 10) | in place, and in parallel on split borrows (`parBO`, Fig. 5) | not expressible |

Cautions from the sources:

- Conchon & Filliâtre: "it is not thread-safe", since rerooting assigns "all over the data
  structure". Baker 1978: processes can share one structure "so long as rerooting is an
  indivisible operation".
- Conchon & Filliâtre: with several versions used at once, "the reroot function is going to
  spend much time in list reversals". §3.1's alternation table is that cost.
- Futhark §3.11.2: "A function that consumes one of its arguments may not be passed as a
  higher-order argument to another function, unless that other function explicitly expects a
  consuming function." Clean, `linear-base` and Futhark all make reads hand the array back or
  borrow it; none lets a consumed array flow through an ordinary fold.

- Pure Borrow §7: its metatheory "does not completely establish purity". Relating the in-place
  semantics to one over plain values (§5.3) is a bisimulation it leaves as Conjecture 5.5.

Lean's approach needs reference counts, and Sprout's collector traces. Clean, `linear-base` and
Futhark need a linearity or uniqueness check, and Sprout has one (spec §5.8).

## 5. Options

One per layer, as `AGENTS.md` asks.

### 5.1 Caller: `MutVec` under `IO`, or build instead of write

Write the loop with `MutVec` and pay `!{IO}` on its callers: 1–2 ns per write. But it changes the
signature of every function up the call chain, and a pure function such as `owners` stops being
one.

Where each new value depends only on earlier ones, build it instead: §3.2's LCS by rows needs no
write. That covers DP, not a sieve, a search or a histogram, which write wherever the data says.
It belongs in `idiomatic-sprout.md` whatever else is chosen.

### 5.2 Library: a trie in plain Sprout

A `PVec a` in `stdlib/`: a 32-way trie over `Vector`, as Clojure and Scala do.

- **For:** no builtin, no runtime change, no GC question. Fully persistent: every version reads
  at the same cost. Pure by construction. It is the only option for writes inside a fold or
  lambda, an array stored in a record, undo or snapshots, branching search with many live
  versions, and handing a snapshot to another task.
- **Against:** on §3.2's kernels it is 3–21× slower than the C Baker, and 14–260× slower than
  `MutVec` on the sieve, histogram and LCS. The BFS is GC-bound (§3.2): 3.8× slower at w = 200,
  faster at w = 1,000.

**A transient mode**, as Clojure's: nodes carry an owner id; a transient writes in place to the
nodes it owns and copies any other node once, then owns the copy. Conversions both ways are
O(1). Prototype over `Vector` with `!{IO}` writes: same checksums as every other column, and a
check that writes through a transient leave the persistent version unchanged, which fails when
ownership is ignored. A million in-order sets copied each of the 32,259 nodes once, then
allocated nothing. On §3.2 it is 2–9× faster than the persistent trie and 2–20× slower than
`MutVec`, since every access still walks four levels. Its write mode needs linearity (or
Clojure's run-time check) and pure-signature builtins, as 5.3 does.

### 5.3 Types: a linear array

An abstract linear type with pure operations that write in place. Linearity (spec §5.8)
guarantees no one holds the old version, so the write cannot be observed, as in Clean and Futhark.

```
export wrap linear LinVec a = Vector a     # abstract: only its module unwraps one
linvec_new(n: Int, x: a) -> LinVec a
linvec_get(i: Int, arr: borrowing LinVec a) -> Maybe a
linvec_set(i: Int, x: a, arr: LinVec a) -> LinVec a
linvec_length(arr: borrowing LinVec a) -> Int
linvec_freeze(arr: LinVec a) -> Vec a
```

**Representation.** A `wrap` is its inner value at runtime (spec §5.6.1), so a `LinVec` is a
`Vector` with no box, and `wrap linear` ([linear-wrap-v0.md](linear-wrap-v0.md)) gives it the
linear rules. Four operations are Sprout in `LinVec`'s module, over externs that exist:
`linvec_get` and `linvec_length` unwrap the borrowed array and call `vector_get` and
`vector_length`, and `linvec_new` wraps a fresh `vec_make_filled`. That one was `!{IO}`
(`stdlib/mutable.sprout`) and is now pure, with `mutvec_new` keeping the `!{IO}`, as
`vector_empty` is pure under an `!{IO}` `mutvec_empty`; building from a list instead costs ~94 ms
per 1M elements against the fill's ~1 ms. One is a builtin: `linvec_set` writes in place and returns
the same pointer, and an index out of range traps, as `mutvec_set` does (§12 step 13).

Its type takes and returns `LinVec`, never a bare `Vector`, so no Sprout code holding a shared `Vec`
can reach it. An `extern fn` line is trusted code, though: a module that does not import
`LinVec`'s may declare `linvec_set` over `Vector` itself, as any module may declare `vector_mutset`
pure today (spec, *Externs are outside the module system*). And inside `LinVec`'s module,
unwrapping a borrowed array binds the `Vector` untracked, so returning it would alias the array:
that module is trusted code too.

A read borrows, so it does not hand the array back as Clean's and `linear-base`'s do, and it
allocates nothing when the caller matches it with a binding (§3.4). A set consumes the array and
returns it; the builtin returns the same object, so a set allocates nothing. `linvec_freeze` ends
the array's life as an ordinary `Vec`, in O(1): `Vec a` is `Vec(Vector a)`
(`stdlib/prelude.sprout`), so freezing unwraps the `Vector` and wraps it as a `Vec`, one box,
whose constructor is in scope everywhere. `LinVec` itself cannot live in the prelude: a prelude
constructor is in scope everywhere too, so an abstract wrap there would let any code build an
`LinVec` around a shared `Vector` (spec §3.1). It gets its own module. A thaw from `Vec` must copy,
since a `Vec` may be shared. A drop needs no builtin: `linvec_drop(arr) = let _ = linvec_freeze(arr)
in ()`, in Sprout, O(1), since a `Vec` may be discarded.

**Prerequisites.** The purity rests on linearity being sound. Today a linear value can be used
twice in six ways, each verified running on master. With `LinVec`, the second use would see the
first one's writes, so a pure function could return two answers. These BACKLOG P1 entries land
first:

- ~~**A lambda's `once` promise is never checked.**~~ Landed: checked as a declared one is.
- ~~**Generic code can duplicate or drop a linear value**: `fn dup(x: a) -> (a, a) = (x, x)`.~~
  Landed: an unbounded type variable admits no linear type ([linearity-bound-v0.md](linearity-bound-v0.md)
  §12 steps 2 and 3), including through a generic instance's dictionary.
- ~~**One call can borrow and consume the same linear value**: `f(x, x)` with
  `f(b: borrowing L, c: L)`.~~ Landed: a borrow lasts the whole call (spec §5.8).
- ~~**A top-level `let` can hold a linear value that every function consumes.**~~ Landed:
  rejected at the declaration.
- ~~**Reading a borrowed generic container hands out its linear element**~~ Landed with the
  bound: a generic `get` at a linear element type is rejected unless it opts in.
- ~~**An existential hides a linear value from the checker**~~ Landed with the bound: packing a
  linear value into `exists a` is rejected.

These are §5.8 bugs, not `LinVec` bugs: each lets a `File` be closed twice today. A leak is not one
of them: a wildcard that drops an array loses it but cannot expose a write. `LinVec` raises their
stakes: a double close fails loudly, while a double use of a `LinVec` makes a pure function return
two answers in silence. So their rejection tests gate `LinVec`.

They split by cost. Three were missing checks in existing code, and have landed: `once`, the
borrow-and-consume call and the top-level `let`. The other three share one cause, a type variable that hides
linearity: generic duplication, the generic container and the existential. One design closes all
three, a linearity bound on type variables at instantiation and at packing
([linearity-bound-v0.md](linearity-bound-v0.md)). M4.2 made polymorphism over linear types a non-goal;
Swift added such a bound in SE-0427, making every generic parameter `Copyable` unless it opts out.

**Invariants.** Three rules that make `linvec_set` safe to call pure, for any later pass to keep:

- No pass moves a call that borrows a linear value across the call that consumes it. Today no
  pass moves a call: `dce.sprout` only deletes pure ones, which is safe here, since a dropped set
  writes an array no one can read. [opt-passes-v0.md](opt-passes-v0.md) declined CSE and
  proposes LICM; either must treat a shared linear argument as ordering the two calls.
- `linvec_set`'s Sprout purity never reaches LLVM as a purity attribute (`readnone`, `memory(none)`),
  which would let LLVM merge or reorder two reads across a write. The compiler emits none today.
- A call whose result is or contains a linear type is an allocation: no pass merges two of them
  (CSE), hoists one out of a loop (LICM) or duplicates one. Hoisting a per-iteration
  `linvec_new(n, 0)` with a fixed `n` would make every iteration share one buffer. The rule
  covers every call that returns a fresh buffer, linear or not: `linvec_new`'s `vec_make_filled`
  returns a plain `Vector`, and only the wrap makes it linear; `vector_empty` under
  `mutvec_empty` is the same case today.

`linvec_set`, and in-place reuse below, are also write-barrier sites for a generational collector,
as `vector_mutset` is ([gc-generational-v0.md](gc-generational-v0.md) §7).

**Compiler tables.** Three key on `vector_mutset` by name, and `linvec_set` joins each, since a
`MutVec` loop would otherwise beat a `LinVec` one: `ir_rooting.is_nonallocating_read`, or every
live pointer is rooted around each write (landed); the barrier sites of gc-generational-v0.md §7,
with the `sprout_ap_on_store` call `vector_mutset` makes (landed, in `linvec_set`'s C body); and
B1's `ast_to_ir.recognize_vector_op` with `bce.direct_shape`, so a `LinVec Double` write inlines
and loses its bounds check as a `MutVec` one does. The third is out of the first landing (§12
step 14), and stays a BACKLOG entry.

**Bulk operations**, in Sprout over `linvec_set`, as Futhark's `scatter` and `reduce_by_index`
(§4): one call consumes the array and returns it, so a loop threads it once per batch rather
than once per element. They need no builtins.

```
linvec_scatter(is: List Int, x: a, v: LinVec a) -> LinVec a          # sieve: a prime's multiples
linvec_accum(f: a -> b -> a, pairs: List (Int, b), v: LinVec a) -> LinVec a   # histogram
```

An index out of range traps, since both call `linvec_set` (`linvec_accum` reads first and traps in
that read).

They fit the sieve and the histogram. They do not make a BFS easier: picking a level's unvisited
neighbours reads the array inside a lambda (`list_filter`), which was rejected ("borrowed value
'dist' captured by a lambda is not yet supported") until the combinators became `local` (landed,
`docs/local-closures-v0.md` §8), so that read now compiles. Scatter also keeps duplicates:
a cell next to two frontier cells enters the next frontier twice. A BFS stays explicit recursion,
as §3.2's is. DP builds by rows (5.1) and needs neither.

**Structures over `LinVec`.** Other mutable structures are linear types written in Sprout over
it. A binder whose type contains a linear type carries the use-once obligation
([linearity-virality-v0.md](linearity-virality-v0.md)), and "destructuring a borrowed value
binds its linear fields as borrowed too" (spec §5.8), so a read can `match` a borrowed wrapper.

| Structure | Over `LinVec` | Limit |
|---|---|---|
| matrix, ring buffer, heap, bitset, open-addressing hash map | directly: `type linear LMatrix a = LMatrix Int Int (LinVec a)` | one wrapper allocation per write, until reuse (below) |
| growable vector | copy into a larger `LinVec` | amortised O(1) |
| graphs, union-find, linked lists with shared nodes | index arenas: nodes are indices | nothing collects an unreachable node; free slots are managed by hand |
| an array of mutable arrays, `LinVec (LinVec a)` | no: `linvec_get`, `linvec_new` and `linvec_freeze` would duplicate a linear element, so `LinVec` rejects one (*Prerequisites*) | needs swap or take-and-put-back operations, and the generic-container fix |
| state shared between tasks | no: one owner at a time | ownership can move to a task, not be shared |

Not arrays at all: files, sockets and handles, which are linear types of their own already.

**In-place reuse of linear values.** The wrapper allocation is the 767 ms below. When a function
consumes a linear constructor and builds one of the same shape, the compiler can reuse its
memory, since the type guarantees nothing else holds it. Koka does this for values whose count is
1 ("functional but in-place", [byte-buffer-v0.md](byte-buffer-v0.md) §4); Sprout would decide it
from the type, at compile time. Then `LinVec` is the array case of one rule, a linear value is
updated in place, and every linear type gets it. Not designed.

**What it can grow into** without an API break: push and bulk fill or copy; 5.2's transient
mode, whose writes need the same pure, linearity-guarded builtins; and a compiler pass that
writes a provably unshared `vec_set` in place, which would emit the same builtin.

Prototype: `type linear LinVec = LinVec (MutVec Int)` with `!{IO}` operations, since a pure signature
needs a builtin. The sieve and the BFS type-check and run (§3.2, sieve 7 ms against `MutVec`'s 6).
A first cut re-boxed `LinVec(m)` on every set and took 767 ms on the sieve: the allocation per write
fed the count-blind trigger, so neither the builtin nor the type around it may allocate: hence
a `wrap`, not an ADT.

- **For:** within 1.2–1.6× of `MutVec` on the sieve and BFS (§3.2), with a pure signature. No
  slow case, no history to retain, no reroot writes into old objects.
- **Against:**
  - Linear types are Experimental (spec §5.8), and the type inherits that.
  - No lambdas: "a lambda may take a linear parameter iff it borrows it" (spec §5.8), so a set
    inside `list_fold` or `fold_indexed` is rejected and the loop must be explicit recursion.
    `owners` and meadow's folds would need rewriting. BACKLOG P2 **Higher-order linearity**
    lists consuming linear lambda parameters as left to do; whether `list_fold` would then take
    a linear accumulator was not checked. Reads are fine: the combinators are `local` now, so
    a lambda may capture the array to read it (bulk operations, above).
  - Threading: every set returns the array, extra results travel in a tuple (the BFS queue),
    and every path must end in `linvec_freeze` or `linvec_drop`, or the leak rule fires.
  - No old versions. Backtracking copies explicitly.
  - One builtin, `linvec_set`, with a `runtime/APPROVED_BUILTINS` entry, and one language
    change, `wrap linear`. Once unsafe primitives exist (BACKLOG P3 **Unsafe primitives**),
    `linvec_set` is Sprout over `vector_mutset` and the builtin goes (§12 step 12).

### 5.4 Runtime: a Baker array in C

An opaque `PArray a` with pure operations, as in §3.1's C Baker:

```
parray_new(n: Int, x: a) -> PArray a
parray_get(i: Int, arr: PArray a) -> Maybe a
parray_set(i: Int, x: a, arr: PArray a) -> PArray a
parray_length(arr: PArray a) -> Int
```

- **For:** no linearity discipline; works in folds and lambdas. 3–21× faster than the trie on
  §3.2's kernels. Backtracking, the common use of old versions, costs one reroot step per undone
  write.
- **Against:**
  - A cliff: reading two versions far apart, alternately, costs O(distance) each time. No
    diagnostic can catch it, as with byte-buffer-v0.md §5.4's append cliff.
  - Retention: §3.3. Until operands stop being rooted across Sprout calls, an array threaded
    through a loop keeps its whole history alive: 363 MB against `MutVec`'s 53 MB in §3.2.
  - 8× slower than `MutVec` on the sieve and 2× on the histogram, with the trigger out of the
    way.
  - The operations are pure, but rerooting writes into old objects: a generational collector
    would need a write barrier on every reroot write
    ([gc-generational-v0.md](gc-generational-v0.md)), and a shared heap a lock. Green threads
    are safe, since no `pa_*` function yields.
  - Four builtins, needing approval.

### 5.5 Representation: make `Vec` a Baker array

Keep `vec_set`'s signature and make it O(1) underneath, so existing code speeds up unchanged.

- **For:** no new API; `owners` and `hops_in` would gain without an edit.
- **Against:** every `vec_get` pays the version check: 1–2 ns becomes 3–5 ns (§3.1). And every
  `Vec` gets the cliff and the retention, including code that never keeps an old version. Two
  optimisations rely on a `Vec` read being a plain load, and would break: B1's inlined
  `Vector Double` reads and bounds-check elimination's unchecked clone
  ([bounds-check-elimination-v0.md](bounds-check-elimination-v0.md)). Rejected.

### 5.6 Effects: scoped mutation, as Haskell's `ST` (`run_local`)

**Rejected 2026-10-19 in favour of 5.3; §12 records why and when to revisit.**

Let a function use a mutable buffer inside and stay pure outside, as `runST` does, provided the
buffer cannot escape. Two designs follow: A with a `Local` effect, B without one.

- **What exists today is unsound.** The checker takes a builtin's declared type "on trust"
  (spec §6, *Effect note for v0*), so a module can re-declare `vector_mutset` without `!{IO}`.
  A sieve written that way compiles and is correct. But nothing stops the vector escaping or being
  shared, and `let _ = vector_mutset(…)` is, to the compiler, an unused pure call that an
  optimiser may delete.
- **Design A exists:** [byte-buffer-v0.md](byte-buffer-v0.md) §5.5, `run_local` over a `local`
  buffer parameter with a `Local` effect, which replaces the rank-2 type with Sprout's `local`
  escape walk. It needs the effect and three checker changes; the combinators are already
  `local`, so a lambda may capture the buffer; its soundness is "a reading of the paper's rules", not a proof.
- **For:** existing `MutVec` code and its speed, behind a pure signature; no new array type.
  Writes return `Unit`, so nothing is threaded, where 5.3 returns the array from every set and
  carries extra results in tuples. The combinators are `local` now, so writes inside `list_each`
  would be allowed; 5.3 needs consuming linear lambda parameters, left open under BACKLOG P2
  **Higher-order linearity**, an entry it calls known-hard. One effect covers `Ref` and
  `MutMatrix` too, where 5.3 needs a linear twin per type. Every kernel in §3.2 and the three
  `!{IO}` cases in §1 are scratch inside one call, which is this option's shape.
- **Against:** effect rows are only pure, `!{IO}` or one variable (spec §4), so a second
  named effect, and discharging it, is an effect-system extension. The buffer cannot leave
  the run: a result that keeps an array alive across calls (a pure `step` updating a grid in
  place each time) must freeze it and copy it back, O(n) per call. 5.3 passes and returns it.
  No case of that shape was found in our code or §3.2.

**Size of design A** (read from the compiler, 2026-10-19; nothing built):

| Piece | Where | Size |
|---|---|---|
| A third effect, pure ⊑ `Local` ⊑ `IO` | `types.Effect` has `EffectPure`, `EffectIO`, rows and variables; the parser rejects any label but `IO` (`types.effect_label_is_known`) | ~35–45 functions across `types`, `unifier`, `infer`, `checker`, `parser`, `iface_codec`; ~60% new match arms |
| … of which the risk | bounded effect variables (`unifier.record_lower`, `record_upper`) hold one value per side; `infer.merge_effects` cannot reduce `Local` with a variable; `types.effect_declared_rank` is a total order | a rework of the code whose earlier fixes [effect-subsumption-v0.md](effect-subsumption-v0.md) §6.4a–§6.5d record |
| `local` on a data parameter | `linear_check.bad_modifier_here` rejects it; the escape walk is by name | ~20 lines, two fixtures, spec §5.8 |
| `local` on a lambda's parameter, walk over lambda bodies | `infer.seed_param_modes` seeds `borrowing` only; the walk runs from `infer.fn_linear_gate`, which skips a body with no written mode | ~80–150 lines; a missed gate means the walk never runs. Shares the work with BACKLOG P1's `once` fix |
| Reads that cannot alias the buffer | the walk fails a field read or `match` on the buffer | small with an opaque type and trusted externs |
| `run_local`, `local_*` | an extern's name is its C symbol (`ast_to_ir.collect_extern_decls`), and a redeclaration must match its effect | C forwarders (APPROVED_BUILTINS) or call-site intrinsics; `run_local` trusted |
| `bce.sprout` | `decl_node` skips pure functions; only `vector_push` and `vector_truncate` change a length | nothing for a fixed-length buffer; a growable one must be seen through `run_local` |
| Combinators `local` | BACKLOG P2 entry; ~15 prelude signatures, `Functor.fmap` | small code; 2-step bootstrap, golden IR |

5.3 needs none of the effect work; its prerequisite, the `once` fix, is shared with the lambda
row above.

**Design B: no effect.** Spec §6 already fixes evaluation order ("evaluate callee, then args
left-to-right"; a `let` evaluates its RHS immediately), so the effect's only remaining job is to
stop the optimiser deleting or merging a write. The `local` marker can do that from the types: a
buffer reaches code only as a `local` parameter, and a lambda that captures one only at a
`local` slot (`types.OwnLocal` on the arrow, which unification keeps invariant). The rule: a
call may be deleted or merged only if its arrow is pure and no `local` appears in its callee's
or arguments' types. The code that relies on purity is `dce.callee_is_pure`,
`bind_census.rhs_purity`, `bce.decl_node` and `cse_census`. B drops the effect rows of the size
table; the rest stays. It over-approximates: an unused call through a `local` combinator is
kept even when its lambda captures nothing.

Under B, with today's C functions declared pure as stand-ins for the builtins:

```
type ScratchVec a = ScratchVec (Vector a)

fn scratch_set(v: local ScratchVec a, i: Int, x: a) -> Unit =
  match v with
  | ScratchVec raw -> vector_mutset(raw, i, x)

fn run_local_vec(n: Int, x: a, f: local (local ScratchVec a) -> Unit) -> Vec a =
  let raw = vec_make_filled(n, x)
      _ = f(ScratchVec(raw))
  in Vec(raw)

fn mark(v: local ScratchVec Bool, j: Int, step: Int, n: Int) -> Unit =
  if j > n then ()
  else
    let _ = scratch_set(v, j, false)
    in mark(v, j + step, step, n)

fn histogram(buckets: Int, xs: List Int) -> Vec Int =
  run_local_vec(buckets, 0, \counts -> list_each(\x -> bump(counts, x), xs))
```

As written the checker rejects it at the first data `local`: "`local` applies only to a
function-typed parameter". With every `local` removed it compiles and, at `-O2`, counts 78,498
primes below a million and a correct histogram; that version is unsound, since nothing stops `v`
escaping.

**`var` sugar** (`var total = 0` and `total := …` in a `do` block; a function body is one
expression, so it needs `do`) fits B as a one-cell scratch buffer, as Koka's `var` does. Not
pursued: it is the imperative style the language avoids. BACKLOG P2 **Ref sugar in
do-notation** already claims `var x = expr` for `IO` refs, so the two would have to be one
feature if `var` returned.

**Why 5.3 over this option.** Inside a run, `scratch_get(v, 5)` can read `true`, then `false`
after a `scratch_set`: one expression, two values, so substitution fails in the body and holds
again only at the boundary. Under 5.3 the old name is dead after a set, so every expression that
can still be written has one value. The option's gains, no threading and writes from lambdas,
are paid for with exactly that. 5.3 is also cheaper, and its prerequisite is shared.

### 5.7 Types: branded indices (a layer over 5.3)

Not an alternative to 5.3. It replaces 5.3's out-of-range trap (§12 step 13) with a check the
compiler places. A `LinVec` never changes length, so an index checked once is in range for that
vector's whole life. A brand ties the two together: an existential gives each length a type `s`
no other length has, and abstract wraps make `Idx s` constructible only by the check.

```sprout
export wrap Len s = Int
export wrap Idx s = Int
export wrap linear BVec s a = LinVec a
export type Sized(..) = | exists s. Sized(Len s)

export fn bvec_index(i: Int, len: Len s) -> Maybe (Idx s)     # the one check
export fn bvec_set(i: Idx s, x: a, v: BVec s a) -> BVec s a  # total

fn histogram(buckets: Int, xs: List Int) -> Maybe (Vec Int) =
  match sized(buckets) with
  | Sized(len) ->
      let is = try traverse(\x -> bvec_index(x, len), xs)
      in Just(bvec_new(len, 0) |> bvec_accum(\n -> n + 1, is) |> bvec_freeze)
```

A prototype in plain Sprout (2026-10-22) runs this, and rejects a raw `Int` index (type
mismatch), an index checked against another length (existential escape) and `Idx(9)` (not
exported). The indices are checked before the vector exists, so a failure has no vector to drop.

- Index arithmetic (`i + 1`, BFS neighbours, LCS's `i - 1`) leaves the brand: each result is
  checked again. A `bvec_indices(len) -> List (Idx s)` covers whole-range loops. Measure on
  §3.2's BFS and LCS before pricing it.
- The brand appears in every helper's signature (`Len s`, `BVec s a`).
- `linvec_set`'s own check stays until unsafe primitives exist; under the brand it cannot fire.
- Unsound over `MutVec`: `mutvec_push` changes the length.

Prior art: Rust's `indexing` crate ("The container and its indices and ranges are 'branded'
with a lifetime parameter 'id"); Haskell's `justified-containers` ("type-checked proofs of key
presence"); Idris's `index : Fin n -> Vect n a -> a`, which also proves index arithmetic but
needs dependent types.

## 6. How they combine

No design is fastest everywhere. Any array that keeps old versions readable at reasonable space
pays an Ω(log log n) worst-case read, for n elements (Straka, *Functional Data Structures and
Algorithms*, 2013: partially persistent arrays, cell-probe model, O(m logᵏ m) space for m
updates). Dietz (*Fully persistent arrays*, WADS 1989) reaches O(log log m). Both as summarised
by Wikipedia's *Persistent array*; the papers themselves were not read. 5.3 is fast because it
gives old versions up; 5.2 pays a little on every operation; 5.4 pays unpredictably on old ones.

So 5.2 and 5.3 answer different needs: 5.3 for a loop that only ever uses the newest array, which
is every kernel in §3.2; 5.2 for everything that needs values that can be stored, passed and
kept. `linvec_freeze` and a `PVec` builder connect them. 5.6 covers 5.3's cases with a different
trade (§12, step 7); one of the two ships. 5.2's transient mode could stand in for
5.3 with O(1) conversions, at 2–20× its cost. 5.4 tries to serve both needs, and pays with the
cliff, the retention and the version check. 5.1's build-by-rows needs neither.

Whatever ships, the count-blind GC trigger decides the large BFS for every in-place option; it
is worth as much as the choice between them.

**What other changes would do.** Most move every option by a constant. Only knowing that a
value is unshared moves the ranking.

| Change | Effect | Changes the ranking? |
|---|---|---|
| Byte-aware trigger (BACKLOG P2 **GC trigger is object-count-blind**) | BFS w=1000: `MutVec` 1,375 → 81 ms, linear 2,165 → 101 ms, taking §3.2's raised trigger as its stand-in | No: there, sieve and BFS are linear 7 and 101, transient 102 and 167, trie 652 and 304. `vec_set` stays O(n) per write. |
| Pointer-free vectors | `sprout_gc_drain_marks` looks up every element of a vector, `Int` or not; a flag could skip a 1M-`Int` array. Not measured. | No: it helps every flat array alike. |
| Nursery | Cheaper trie allocation per write | No: [gc-generational-v0.md](gc-generational-v0.md) §8 builds one for the compiler only, and non-moving is the invariant. |
| Reference counting (Lean 4, Koka) | `vec_set` writes in place when nothing else holds the vector; no linear type needed | Yes, but it needs counts, which Sprout's tracing collector does not keep ([byte-buffer-v0.md](byte-buffer-v0.md) §5.4); no doc prices adding them. |
| Compiler proves a `vec_set` argument unshared | `owners` and meadow's folds speed up unedited | Partly. Needs escape analysis through lambdas; not designed. Would emit 5.3's builtin. |
| Consuming linear lambda parameters (BACKLOG P2 **Higher-order linearity**) | Lifts 5.3's "no lambdas" | Removes the main point for the transient over 5.3. |

## 7. Recommendation

1. Document 5.1's build-by-rows form in `idiomatic-sprout.md` now; it needs no approval.
2. Ship 5.2, the trie, as the general persistent array. It needs no builtins and no
   linearity, so nothing below blocks it.
3. ~~Fix the three linearity holes that are missing checks (5.3, *Prerequisites*).~~ Landed.
4. ~~Design the linearity bound on type variables~~ Designed in
   [linearity-bound-v0.md](linearity-bound-v0.md); its steps 2 and 3 closed the other three.
5. Ship 5.3, the linear array, with its bulk operations, as the one mechanism for in-place
   writes in pure code (`MutVec`, `Ref` and `MutMatrix` stay for `IO` code), after steps 3 and
   4, and keep 5.3's *Invariants*. **Landed 2026-10-22, core only**: the module, the builtin
   `linvec_set`, the `ir_rooting` entry, the bulk operations, tests and `examples/sieve.sprout`.
   Out of it, as a BACKLOG entry: B1 and bounds-check elimination for `LinVec Double` writes.
   Marking the combinators `local`, so that a lambda can read the array, **landed after**
   (`docs/local-closures-v0.md` §8); `linvec_accum` takes its `f` `local`.
   Other mutable structures are linear types over it. It needs `wrap linear`
   ([linear-wrap-v0.md](linear-wrap-v0.md), landed) and one builtin, `linvec_set`; §3.2 is the measured
   case: typical programs, not our own; in our own code it removes `!{IO}` from the three cases
   in §1.
6. Follow with in-place reuse of consumed linear constructors (5.3), which makes the wrappers
   allocation-free and generalises `LinVec`'s rule to every linear type.
7. Do not build 5.4, 5.5 or 5.6. The transient (5.2) can come later on 5.3's builtin.

§12 records how this was decided and when to revisit it.

## 8. Impact

| | 5.2 trie | 5.3 linear array | 5.4 C Baker |
|---|---|---|---|
| Syntax | none | `wrap linear` | none |
| Semantics | new opaque type, pure operations | new abstract linear type, pure operations | the same as 5.2, plus a performance cliff on far-apart versions |
| Types | none | uses §5.8 linearity; Experimental | none |
| Errors | none new | linear errors (reuse, leak, a set inside a lambda, the array captured by a lambda at a non-`local` parameter), and the bound's error for a linear element type | none new |
| Runtime | none | one builtin, `linvec_set` | four builtins; 4-word version nodes, no new heap kind |
| Migration | additive | additive | additive |

A read out of range gives `Nothing`, as `vec_get` does, in all three. A 5.3 set out of range
traps, as `mutvec_set` does.

## 9. Tests

- Version semantics (5.2, 5.4): many versions, each a set on a random earlier one, every version
  read against a `vec_set` reference. Run under `SPROUT_GC_STRESS=1 SPROUT_GC_HDRCHECK=1` for 5.4.
Where each 5.3 item lives: `tests/stdlib/test_linvec.spr` (run by `just test`),
`tests/conformance/type_error/linvec_*` and `just linvec-smoke` (`scripts/linvec_smoke.sh`,
`tests/linvec_smoke/`).

- Linearity (5.3): reuse after a set, a set inside a lambda, and a path that neither freezes nor
  drops are each rejected (`type_error/linvec_{reuse_after_set,set_in_lambda,leak}`); a borrowed
  read followed by a set is accepted (`test_linvec`).
- Aliasing (5.3, *Prerequisites*): one rejection test per hole, each with a linear value that a
  second use would observe, are the linear grid's and [linearity-bound-v0.md](linearity-bound-v0.md)'s,
  not `LinVec`'s. `LinVec` adds `LinVec (LinVec Int)` (`type_error/linvec_nested`).
- §3.2's four kernels as executable tests, checked against a `MutVec` reference (`test_linvec`).
- Value semantics as an oracle (5.3): `linvec-smoke` runs `test_linvec` against a copy of the
  stdlib root whose `linvec_set` copies through `vec_set`, in plain Sprout. Both builds must print
  the same; a difference is a linearity hole seen as a wrong answer. It finds only holes a test
  reaches, so it adds to the per-hole rejection tests above. The method is Pure Borrow's argument
  for purity (§4).
- Ordering (5.3 *Invariants*): a borrowed read, then a set of the same cell, returns the old
  value (`test_linvec`), at `-O0` under `just test` and at `-O2` under `linvec-smoke`. Nothing
  else runs a test at `-O2`: `o2-codegen-smoke` only compiles.
- Cost (5.3): a set allocates nothing (the runtime's debug counters, `SPROUT_DEBUG_ALLOC`), and a
  `linvec_get` matched with a binding compiles to a worker over `vector_get_unboxed` (an IR
  check), both in `linvec-smoke`.
- Abstraction (5.3): a module that imports `LinVec` cannot build one from a `Vector` or unwrap one
  (`Unknown variable: LinVec`, `type_error/linvec_build_outside`).
- Range (5.3): `linvec_set`, `linvec_scatter` and `linvec_accum` out of range trap, and
  `linvec_new` allocates one vector (`linvec-smoke`).
- Bulk operations (5.3): `linvec_scatter` and `linvec_accum` against a `vec_set` reference, with
  repeated indices (`test_linvec`). Out-of-range indices trap, so they are in the range item.

## 10. Spec and docs

`spec-v0.md` gains the type and its operations, normative when they land; for 5.3, Experimental
while §5.8 is. `stdlib-reference.md` gets a row in *Collection costs*. `idiomatic-sprout.md`
shows build-by-rows for DP and the chosen array for the rest, in place of a fold of `vec_set`.
`examples/sieve.sprout` runs the `vec_set` and `MutVec` sieves and 5.3's two versions, element by
element and with `linvec_scatter`. Landed with the type; `idiomatic-sprout.md` and
`stdlib-reference.md` have their rows.

## 11. Open questions

- ~~Approve 5.3's builtins on §3.2's evidence, or wait for a program of ours to need them?~~
  One builtin, `linvec_set`, over a `wrap linear` (§12 step 12).
- ~~Names: `LArr`, `LinArray` or `Array`.~~ `LinVec` (§12 step 14). `PVec` or `PArray` for 5.2 is
  still open.
- Nested arrays: the shape of swap or take-and-put-back for `LinVec (LinVec a)`. Pure Borrow's
  `updateAt` (§4) is one: `linvec_update(i: Int, f: a -> (b, a), arr: LinVec a) -> (b, LinVec a)` takes
  the element out and puts back what `f` returns. `f` consumes a linear parameter, which waits on
  BACKLOG P2 **Higher-order linearity**.

## 12. Decision record

Decided 2026-10-19 with Kuba, one question at a time. Each step, and what moved it:

1. **Fast path: 5.3 over 5.2's transient.** The transient gives one type with O(1)
   conversions, but is 2–20× slower on §3.2. 5.3's builtins are what a transient's writes
   would need, so it can still come later.
2. **5.6 raised: is `ST` redundant next to 5.3?** Not for ergonomics: inside a run, writes
   return `Unit` (no threading), lambdas can write now that the combinators are `local`, and one
   mechanism covers `Ref` and `MutMatrix`. Every kernel in §3.2 and every `!{IO}` case in §1 is
   scratch inside one call, 5.6's shape; 5.3's own niche, an array that outlives a call, had no
   user. Recommendation moved to "size 5.6 before approving 5.3".
3. **Sized** (5.6's table): the `Local` effect is the dominant cost and risk.
   Recommendation: 5.3 now, 5.6 later.
4. **Constraint: one mechanism, not two**, for in-place writes in pure code. With "later"
   gone, 5.6 was chosen for its fit to the measured cases and its ergonomics.
5. **Naming the effect** led to Koka's `local :: (H) -> X`, an effect indexed by a scope. Its
   two jobs, ordering writes and stopping escape, split in Sprout between an effect and the
   `local` mode. Kuba: it "sits between an effect and a pure function". That gave design B,
   with no effect, and moving its optimiser rule onto `local` in the types kept it type-level,
   as Kuba prefers.
6. **`var` considered** as B's surface syntax; rejected as imperative.
7. **Substitution.** In any run body `scratch_get(v, 5)` changes value across a `scratch_set`.
   Under design B the read is typed pure, so the type hides the change. Under design A it is
   typed `!{Local}`, so the type shows it, as `mutvec_at`'s does in `IO` code, but the body is
   then effectful code. Kuba: substitution inside the body matters. That rejects B outright, and
   A for putting effectful code inside a pure function, and reversed step 4: 5.3, which keeps
   substitution, with bulk operations (as Futhark's `scatter` and `reduce_by_index` on unique
   arrays), so threading is per batch, not per element.
8. **Generality**, 5.6's remaining advantage: answered by structures over `LinVec` (5.3's
   table, which also records its limits) and by in-place reuse of linear values, which makes
   the rule general rather than array-specific.
9. **Review.** An outside review found four more holes in the linearity that 5.3's purity rests
   on, beyond the `once` one, each reproduced on master. They became *Prerequisites* and BACKLOG
   P1 entries. It also showed that bulk operations do not rescue a BFS, which moved the
   combinators' `local` into §7's `LinVec` step. The direction stood.
10. **Second review.** It found a sixth hole, through existentials (reproduced), and the
    allocation invariant, and corrected the trie's range and the lower bound's source. The
    direction stood again: the checker holds where a linear type is visible in a type, and
    fails where one is hidden (generics, existentials).
11. **What the holes change.** Not the ranking: they are §5.8 bugs either way, the trie
    depends on none of them, and 5.6 would rest its escape check on `local`, which a type
    variable hides as it hides linearity (inferred: `local` on data does not exist to probe).
    They change the cost, from one fix to three fixes and a type-system design, so §7 was
    reordered with the trie first; the stakes, a silent wrong answer instead of a double close;
    and the fallback: 5.4 is the only in-place option whose purity rests on the runtime, not on
    types.
12. **Fewer builtins** (2026-10-22, after the bound landed). Kuba: as few builtins as possible,
    a generic one over several specific ones, performance paramount, and unsafe primitives
    later, so a builtin now should be one an unsafe primitive can delete without an API change.
    Five opaque builtins became `wrap linear LinVec a = Vector a` and one builtin: a `wrap` is
    the raw inner value at runtime, so four operations are Sprout over existing externs, and
    only the in-place write needs C. A direct-read builtin was measured out (§3.4).
    Goal "no new syntax" gave way to it. Once unsafe primitives exist, `linvec_set` is Sprout over
    `vector_mutset` and the last builtin goes.
13. **Review** (2026-10-22). `linvec_new` over a list cost ~94 ms per 1M elements, so it uses
    `vec_make_filled`, made pure: no new builtin. Kuba: an out-of-range `linvec_set` traps rather
    than returning the array unchanged as `vec_set` does. An off-by-one fails where it happens,
    the later Sprout version is `vector_mutset` with no added check, and linear-base's `set`
    (`assertIndexInRange`) and `mutvec_set` do the same. That departs from guidelines.md #2,
    Total over partial, for a write with no meaningful result. The compiler tables and the
    unsafe-primitives entry came from the same review.
14. **Name and first landing** (2026-10-22). Kuba: the type is `LinVec`, in `stdlib.linvec`, with
    `linvec_*` functions. Linearity is the property a user must act on (thread it, no copy,
    freeze or drop) and the only one that separates it from `Vec` (pure, shared, O(n) write) and
    `MutVec` (`IO`, shared, O(1) write). "Mut" would contradict purity; "Vec" is Sprout's own
    family word (`Vec`, `MutVec`, raw `Vector`), and `linvec_freeze` returns a `Vec`. Scope: core
    only. In: the module, the one builtin, `vec_make_filled` made pure, the `ir_rooting` entry,
    the bulk operations, tests and the sieve example. Out, each its own BACKLOG entry: B1 and
    bounds-check elimination for `LinVec Double` writes (§5.3 *Compiler tables*), and marking the
    combinators `local` (since landed). A scatter or accum index out of range traps, since they call the set.
15. **Out-of-range writes, later** (2026-10-22). Kuba: the bulk operations keep trapping, and
    branded indices (5.7) are the way to rule the trap out, filed at P2. A `Result`-returning
    write was set aside: on failure it must return the vector or drop it, and every caller pays
    for that. Consuming the vector on failure composes with `try` today if a caller needs it.

**Revisit when** any of these holds; each would reopen step 7:

- Programs written against 5.3 need recursion and tuples where a fold would do, often enough
  to show in review; consuming linear lambdas (BACKLOG P2 **Higher-order linearity**) not
  landing makes this likelier.
- A nested mutable structure is needed and swap or take does not make it workable.
- In-place reuse proves infeasible, leaving every structure over `LinVec` an allocation per
  write.
- Mutable state other than arrays is wanted in pure code: counters, cells, maps not built
  over `LinVec`.

Separately, if the linearity bound on type variables (§7 step 4) proves infeasible, `LinVec`
cannot be made sound. Then reopen 5.4: its faults (§3.3's retention, the cliff on far-apart
versions, the version check) still hold, but its purity needs no types.
