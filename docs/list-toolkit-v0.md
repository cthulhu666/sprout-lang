# List toolkit — zip, adjacent pairs, scan, sort, min/max

Status: **steps 1 and 2 landed** (§9): `Zip`, `zip_with`, `zip`, `min`, `max`,
`list.map_adjacent`; a `Sort` class with `sort`/`sort_by` in `stdlib.sort`, sorting in place over
`LinVec` (§4.6.1), and `vec_sort`/`vec_sort_by` retired (§4.6.2). Approved, not yet built: `scanl`
(§4.5). `enumerate` deferred (§4.3).

## 1. Problem

The LeetCode survey (`docs/leetcode-survey-2026-10-27.md` §2) ranks the stdlib gaps 31 solutions
hit. Four of the top seven are list operations every comparable stdlib has:

| Gap | Problems | Today |
|---|---|---|
| two-argument `max`/`min` in the prelude | 9 | `import stdlib.math.int (max)` |
| `zip`, `zip_with`, `map_adjacent`, `enumerate` | 8 | hand-written loops; `fold_indexed`, which allocates a tuple per element |
| `scanl` | 6 | hand-written (`examples/leetcode/p0238_…`) |
| a list sort | 5 | `vec_from_list(xs) \|> vec_sort \|> vec_to_list` |

`p0238_product_of_array_except_self.sprout` defines its own `scanl` and `zip_with`;
`p3110_score_of_a_string.sprout` hand-writes an adjacent-pairs walk; `p0046`, `p0049` and `p0078`
each define the same `sorted` round trip through `Vec`. The round trip is not only LeetCode's:
the compiler does it (`ast_to_ir.sprout`, `analysis_service_driver.sprout`), and `ide/filetree`
and `examples/tui_files` each define their own `sorted`.

## 2. Goals and non-goals

Goals:
- `zip_with`, `zip`, `sort` and `sort_by` that keep the container: `List` in, `List` out; `Vec`
  in, `Vec` out.
- `map_adjacent`, `scanl`, and a two-argument `min`/`max`.
- Every function total; none slower than the loop it replaces.
- Generic only where no container loses its fast path (§4.1).
- **Sorting is fast.** Callers sort in hot loops (a game scheduler, `docs/sorting-v0.md`), so
  `sort` is measured against `mutvec_sort`, not against today's `vec_sort`.

Non-goals (v0):
- `group_by`. The survey's callers want keyed grouping into a map (`p0049`), which belongs with the
  "`Dict`/`Set` take only `String` keys" entry. Its groups will be `NonEmpty a`
  (`docs/non-empty-v0.md` §9). Adjacent-run grouping has no caller.
- `unzip`, `zip3`, `zip_longest`, a strict zip that fails on unequal lengths, `scanl1`, `scanr`.
  No survey caller; each is additive later.
- `abs_diff` (§4.7).
- `enumerate`, until Sprout has a lazy sequence type or decides not to (§4.3).
- A `Vec` `map_adjacent`. `Vec` callers index instead; add it when a caller asks.
- Generic `Ord` dispatch, about 2× on every sort, and primitive sort builtins.
  `docs/sorting-v0.md` owns both (options A and C).
- Moving other `vec_*` functions out of the prelude's exports. See §4.6.2's last paragraph.

## 3. Prior art

Checked against each library's reference docs or source: Haskell `base`-4.22 and `semialign`-1.4,
OCaml 5.5, Elm core 1.0.5, Rust 1.99, FSharp.Core `main`, Gleam stdlib 1.0.5.

| | Haskell | OCaml | Elm | Rust | F# | Gleam |
|---|---|---|---|---|---|---|
| zip with fn | `zipWith` | `List.map2` | `map2` | `zip` + `map` | `List.map2` | `map2` |
| unequal lengths | truncates | raises | truncates | truncates | raises | truncates; `strict_zip` → `Result` |
| adjacent pairs | — | — | — | `windows(2)`, `array_windows` | `pairwise` | `window_by_2` |
| index pairing | `zip [0..] xs` | `mapi` | `indexedMap` | `enumerate` | `indexed` | `index_map` |
| index position | first | first | first | first | first | **second** |
| scan | `scanl`, n+1 | `Seq.scan`, n+1 | — | `scan`, no init | `scan`, n+1 | `scan`, n, no init |
| sort stable | yes | `stable_sort` | via JS spec | yes | yes | yes (source comment) |
| key once | `sortOn` | no key form | no | `sort_by_cached_key` | `sortBy` (source) | no key form |
| 2-arg min/max | `Ord` methods | `Stdlib`, polymorphic | `Basics`, `comparable` | `cmp::min`, `Ord::min` | `Operators` | `int.min`, qualified |

- **Truncation** is the majority; OCaml's and F#'s list functions raise. Only Gleam has a checked
  variant.
- **Container-keeping zip as a class**: `semialign`'s `class Semialign f => Zip f` (`zipWith`;
  instances for lists, `NonEmpty`, `Vector`, `Map`, …) takes "the intersection of non-uniform
  shapes". `base` has only `MonadZip`, which needs `Monad`. Scala cats has no `Zip` class; zipping
  goes through `Parallel` to `ZipList`. Haskell's `NonEmpty.zipWith` returns `NonEmpty`.
- **Scan** splits: Haskell, F# and OCaml include `init`; Rust and Gleam do not. Haskell's
  `NonEmpty.scanl :: Foldable f => … -> f a -> NonEmpty b` is §4.5's signature.
- **Ties in min/max**: Haskell's defaults and Rust's documented rule return the first argument
  from `min` and the second from `max`, so `(min(x, y), max(x, y))` holds both inputs.
- **Grouping** has no agreement: Haskell `groupBy` and Rust `chunk_by` take adjacent runs (Haskell
  compares with the group's first element, Rust with the neighbour); F# `groupBy` and Gleam `group`
  key the whole list, and neither keeps group order. Another reason to leave it out (§2).
- **`abs_diff`**: Rust only (`i64::abs_diff -> u64`, "without overflow or panics").

## 4. Design

### 4.1 Where each function lives

The rule `docs/collection-combinators-v0.md` §3 set, applied to the new names:

- **A result in the input's container** needs a class that can rebuild it. `Foldable` only tears a
  container down, which is why `Filterable` exists. `zip_with` and `sort` are this case: two new
  classes, `Zip` and `Sort`.
- **A result of a fixed type** (`List`, `NonEmpty`) is `Foldable`-generic when one fold produces
  it with no allocation beyond the result: `scanl`.
- **List only** otherwise. `map_adjacent` goes in `stdlib.list`, next to `take` and `drop`.

| Function | Type | Where |
|---|---|---|
| `zip_with` | `(a -> b -> c) -> f a -> f b -> f c where Zip f` | prelude |
| `zip` | `f a -> f b -> f (a, b) where Zip f` | prelude |
| `scanl` | `(b -> a -> b) -> b -> f a -> NonEmpty b where Foldable f` | prelude, after `NonEmpty` |
| `sort` | `f a -> f a where Sort f, Ord a` | `stdlib.sort` (§4.6) |
| `sort_by` | `(a -> k) -> f a -> f a where Sort f, Ord k` | `stdlib.sort` (§4.6) |
| `min`, `max` | `a -> a -> a where Ord a` | prelude |
| `map_adjacent` | `(a -> a -> b) -> List a -> List b` | `stdlib.list` |

Function parameters are `local`, as `list_map`'s are.

### 4.2 `Zip`, `zip_with`, `zip`

```sprout
export class Zip f
  fn zip_with_values(g: local a -> b -> c, xs: f a, ys: f b) -> f c

export fn zip_with(g: local a -> b -> c, xs: f a, ys: f b) -> f c where Zip f =
  zip_with_values(g, xs, ys)

export fn zip(xs: f a, ys: f b) -> f (a, b) where Zip f =
  zip_with(ap_pair, xs, ys)
```

The method name stays out of the global namespace, as `fmap`/`map` and `filter_values`/`filter`.

- **Length**: the shorter input's. The extra elements of the longer one are dropped, and `g` is
  never called on them. This is total; failing on unequal lengths would need a `Maybe` result that
  most callers would unwrap with a fallback that cannot happen.
- **Order**: `g` runs left to right, once per output element.
- **Instances**: `List` walks both lists once into a reversed accumulator and reverses it. `Vec`
  reads both by index up to the shorter length and builds the result as `vec_map` does, through
  one list and `vec_from_list`. `NonEmpty` (after `docs/non-empty-v0.md` lands): head with head,
  then the tails with the `List` instance, so the result is non-empty, as §9 there asks.
- **Not instances**: `MutVec`, whose reads are `!{IO}` while the method is pure, as for
  `Functor` and `Foldable`; it has `mutvec_zip_fold`. `LinVec` is deferred: spec §5.8 allows an
  instance at a linear type, but `LinVec` is in no class yet and nothing has exercised a linear
  instance of a constructor class. Its uses (sieves, DP tables) write by index and do not zip.
- **Why a class, not `Foldable`**: a fold walks one structure. Walking two in step means turning
  one into a list first, and the result could only be one fixed container. `Zip` is the same
  answer `Filterable` gave for `filter`.

`zip_with(g, xs, list.drop(1, xs))` is `map_adjacent` on a list; the survey measured it equal to a
hand-written loop.

### 4.3 `enumerate` (deferred)

```sprout
enumerate(["a", "b"]) == [(0, "a"), (1, "b")]
```

Not in v0. Sprout is strict and has no iterators, so `enumerate` would build the whole list of
pairs before anything reads it: a pair and two list cells per element (the reversed list, then
the reverse). Rust's `enumerate` is a lazy adapter and allocates nothing. When the pairs go
straight into a fold, `fold_indexed` is cheaper, at one `(Int, acc)` tuple per element. Counts are
from the code, not measured.

A `Foldable`-generic version would fix its result to `List`. If a lazy sequence type lands
(`docs/laziness-v0.md` §8 says when to look again), `enumerate` should return that type, or keep
the input's container. Deciding now would fix the wrong result type. Until then the index form is
`fold_indexed`.

### 4.4 `map_adjacent`

```sprout
import stdlib.list as list

list.map_adjacent(\ (a, b) -> imath.abs(a - b), [1, 4, 2]) == [3, 2]
```

`g` gets each element and its successor, left to right. A list of n ≥ 1 elements gives n − 1
results; `[]` and a one-element list give `[]`, as F#'s `pairwise` and Gleam's `window_by_2` do.
Built as `zip_with(g, xs, drop(1, xs))`.

Named for the map, not the pairs: F# and Gleam return pairs, which costs a tuple per element that
the caller then takes apart. The pair form is `zip(xs, drop(1, xs))`.

`stdlib.list`, not the prelude, because it is `List`-only and the prelude's unprefixed names are
generic. `stdlib.list` is in the compiler's import closure (`ast_to_ir` imports it), so a change
there goes through the seed gate even when the IR does not move.

### 4.5 `scanl`

```sprout
scanl(\ (acc, x) -> acc + x, 0, [1, 2, 3]) == NonEmpty(0, [1, 3, 6])
```

The result holds `init` and then every intermediate accumulator: n + 1 values for n elements. It
is `NonEmpty b` because it always holds `init` (`docs/non-empty-v0.md` §9). `tail(scanl(…))` drops
`init` in O(1), which is the prefix-sum form most callers want.

The fold's accumulator is the reversed result; its head is the current value, so each step is
one cell. The last step reverses and splits off the head into `NonEmpty`.

`scanl` lands after `NonEmpty`. Shipping it on `List` first would change its result type later.

### 4.6 `Sort`, `sort`, `sort_by`

```sprout
module stdlib.sort

export class Sort f
  fn sort_values(xs: f a) -> f a where Ord a
  fn sort_by_values(key: local a -> k, xs: f a) -> f a where Ord k

export fn sort(xs: f a) -> f a where Sort f, Ord a =
  sort_values(xs)

export fn sort_by(key: local a -> k, xs: f a) -> f a where Sort f, Ord k =
  sort_by_values(key, xs)
```

- **Semantics**: stable, ascending, O(n log n), constant stack (every loop is a tail call).
  `sort_by` calls `key` once per element, as Haskell's `sortOn` and Rust's
  `sort_by_cached_key` do (§3).
- **Two methods, not one.** `sort` as `sort_by(\x -> x, …)` would pair every element with itself,
  doubling the array. Same reasoning as `Foldable`'s two methods. The constraint sits on the
  method, as `Traversable`'s `Applicative` does: `f` alone picks the instance.
- **Instances**: `List` and `Vec`; `NonEmpty` after `docs/non-empty-v0.md` lands. `MutVec` is not
  one: its sort works in place, has an effect and returns `Unit`, so `mutvec_sort` stays apart.
  `LinVec` fits the method's shape but is deferred with its `Zip` instance (§4.2).
- **Not in the prelude.** The fast sort writes in place (§4.6.1), and the prelude cannot reach
  in-place writes without exceptions to three rules (§4.6.3). Nothing in the prelude sorts.
  Haskell's `Prelude` exports no `sort`, `sortBy` or `sortOn` either (`import Data.List`); OCaml's
  is `List.sort`. A class still gives one name to several containers, as for `map` and `zip_with`.

How six languages sort an immutable collection (each checked in its source or reference docs):

| language | how |
|---|---|
| Haskell `Data.List.sortBy` | merge sort over cons lists (`sequences`, `merge_all`), no array |
| Haskell `Data.Vector.modify` | an `ST` action on a pure vector: "performed in place if it is safe to do so", else on a copy |
| OCaml `List.stable_sort` | merge sort over lists, "constant heap space … and logarithmic stack space" |
| F# `List.sortWith` | `List.toArray` → `Array.stableSortInPlaceWith` → `List.ofArray` |
| Scala 2.13 `SeqOps.sorted` | `copyToArray` → `java.util.Arrays.sort` → rebuild |
| Clojure `sort` | `to-array` → `java.util.Arrays/sort` → `seq`; "Guaranteed to be stable" |
| Elm `List.sortWith` | `_List_toArray` → JS `Array.prototype.sort` → `_List_fromArray` |

Copy into a fresh array, sort it in place, hand back an immutable result: four of the six. Those
four do it unchecked, in impure languages. `Data.Vector.modify` is the checked form, through
`ST`'s rank-2 type. §4.6.1 is the checked form through linearity, which Sprout already has.

#### 4.6.1 In place, over `LinVec`

The algorithm is `mutvec_sort`'s: insertion sort on 16-element blocks, then bottom-up merge
passes into one scratch array. A tie takes the left run, so the sort is stable with no index.
The arrays are `LinVec`s, private to `stdlib.sort`:

- **`List`**: `linvec.from_list(xs)`, sort, `freeze`, `vec_to_list`.
- **`Vec`**: `linvec.from_vec(v)` — new, a copy (§4.6.4) — sort, `freeze`.
- **`sort_by`**: a `LinVec` of `(key(x), x)` pairs — written straight from a `Vec`, through a
  `list_map` for a `List`; the merge compares keys only; the keys are stripped into the result.
- The comparison calls `compare` directly: `ord_lt` costs ~40% in a sort
  (`docs/sorting-v0.md` §Where the time goes, item 2).
- The merge passes swap the two arrays. At the end the one holding the result is frozen and the
  other dropped, so there is no copy back, which `mutvec_sort` has to do.

**Why it is safe.** Linearity checks it: a `LinVec` has one owner, so no one sees it while it is
written, and `freeze` ends the writes. `stdlib.sort` calls no `unsafe_pure` and declares no
writer. The trusted code stays `linvec`'s three writers (`docs/unsafe-primitives-v0.md` §4.3).

**Measured** (prototype, 2026-10-28; random Ints in [0, 65536); median of 5 interleaved rounds,
load ~4; ns per element, and peak RSS at 1M including the ~88 MB input):

| | 1k | 1M | 1M MB |
|---|---:|---:|---:|
| today's `vec_sort` | 560 | 1256 | 563 |
| the undecorated list merge this section first proposed ¹ | 457 | 952 | 478 |
| raw writes behind `unsafe_pure` (§4.6.3), `List` input | 94 | 154 | 88 |
| **`LinVec`, `List` input** | **83** | **140** | **88** |
| raw writes, `Vec` input | 93 | 147 | 88 |
| `LinVec`, `Vec` input, through a list ² | 95 | 179 | 118 |
| today's `vec_sort_by` | 562 | 1276 | 564 |
| raw writes, `sort_by` | 166 | 455 | 126 |
| `LinVec`, `sort_by`, pairs through a list ² | 248 | 735 | 284 |

¹ First measured in `bench/results-2026-10-27-list-sort.md`. This row is from the first of two
runs that day, the rest from the second. ² The prototype had no `linvec.from_vec` and built the
pairs as a list; the implementation writes both directly.

The module, through `bench/list_sort` (`bench/results-2026-10-28-stdlib-sort.md`, load 6–9):
`sort` 82 / 137 ns at 1k / 1M on either input, 88 MB; `sort_by` 156 / 469 ns, 126 MB. The direct
writes close both ² rows to the raw ones. Against `vec_sort`, 6.8× at 1k and 9.1× at 1M; against
`vec_sort_by`, 3.6× and 2.7×.

#### 4.6.2 Retiring `vec_sort` and `vec_sort_by` (decided)

Every caller moves to `sort`/`sort_by` with `import stdlib.sort`, and the two leave the prelude
with their helpers (`vec_sort_by_collect`, `decorated_key_lt`, `merge_runs`, `merge_pass`,
`merge_all`, `to_runs`). Nothing else in the prelude calls them. Two names for one operation
would remain otherwise, and the prelude's one would be the 8× slower.

- **Callers in this repo**: the compiler (`ast_to_ir` ×2, `bind_census`,
  `analysis_service_driver`), `ide/filetree`, examples, benches and tests. The compiler's move
  needs a reseed and makes its own sorts faster.
- **Downstream**: uncharted-suns, 15 uses in 11 files, migrated in a commit there that lands with
  this change. repbit, repbit-cards, sprout-postgres and sprout-sqlite are not checked.

The same reasoning covers every `vec_*` that duplicates a generic function (`vec_map`,
`vec_filter`, `vec_fold`, `vec_any`, …), and a `stdlib.vec` module for the `Vec`-only operations
(`vec_get`, `vec_length`, …, used in 165 files). That is a separate change, not this toolkit's.

#### 4.6.3 Rejected

- **The undecorated list merge** (this section's first plan): 1.2–1.3× over `vec_sort`, against
  8× for an in-place sort. Haskell and OCaml merge lists; their collectors are built for
  short-lived cons cells, and Sprout's per-element cost says its allocator is not there yet.
- **Raw writes behind `unsafe_pure`, in the prelude.** As fast, but each part bends a rule. The
  prelude would join `source.unsafe_pure_users`. Its module name is `""`, which every headerless
  entry file shares, so the gate would have to match it by path. It cannot `import stdlib.unsafe`
  (it is parsed with no imports), so the bundler would load that module unasked, or the gate's
  rule 1 would be rebuilt off extern privacy. And every prelude extern is exported
  (`bundler.exported_name_of`), so declaring `vector_mutset` there would put an in-place write
  on any `Vec` one call away from every module. Nothing would check that the array is fresh.
- **Raw writes behind `unsafe_pure`, in `stdlib.sort`.** The designed way to add an
  `unsafe_pure` user, with none of the prelude's exceptions. No faster than `LinVec`, and the
  freshness of the array is then a reviewer's job, not the checker's.
- **Option E of `docs/sorting-v0.md`** (local mutation in pure code, `ST`-style): linearity
  already gives it for this case.

#### 4.6.4 `linvec.from_vec`

`export fn from_vec(v: Vec a) -> LinVec a`: a fresh copy, so the result has one owner however
many hold `v`. One `memcpy` (`vector_concat(raw, vector_empty())`), no new builtin. It sits in
`stdlib.linvec`, inside the trust boundary that already unwraps vectors.

`mutvec_sort` keeps its own copy of the algorithm: a `MutVec`'s array is shared by design, so it
cannot be a `LinVec`. Two implementations of one merge, each about 80 lines.

### 4.7 `min`, `max`

```sprout
export fn min(x: a, y: a) -> a where Ord a =
  if compare(y, x) < 0 then y else x

export fn max(x: a, y: a) -> a where Ord a =
  if compare(y, x) >= 0 then y else x
```

On a tie `min` returns `x` and `max` returns `y`, as Haskell and Rust do (§3), so the pair
`(min(x, y), max(x, y))` never holds one input twice. That matters for a hand-written `Ord` that
calls distinct values equal. `imath.max` returns `x` on a tie, but equal `Int`s cannot be told
apart, so a file that drops `import stdlib.math.int (max)` gets the same answers.

Costs, stated before approval:
- **Dispatch.** A `where Ord a` function calls `compare` through a dictionary and roots its
  type-variable arguments; there is no specialisation (`docs/specialisation-v0.md`, blocked).
  `imath.max` is a direct `>=`. Measured in Kadane's loop: ~10× slower, ~6.5 ns more per call
  (`bench/results-2026-10-28-min-max.md`). A hot `Int` loop keeps `imath.max`.
- **`Char`**: no `Ord Char` yet, so `max('a', 'b')` does not compile though `'a' < 'b'` does
  (BACKLOG, "No `instance Ord Char`").
- **Shadowing**: the 9 files that `import stdlib.math.int (max)` or `(min)` keep working; a
  selective import beats the prelude name, as `tests/stdlib/test_string.spr` already shows with
  `find`. They stay `Int`-only.

`abs_diff` is dropped. `imath.abs(a - b)` is as short. Rust's `abs_diff` exists because it returns
an unsigned type that cannot overflow; Sprout has no unsigned `Int`, and `a - b` traps on overflow
(spec §8.1), so `abs_diff` would add a name and nothing else.

## 5. Alternatives at each layer

- **Caller**: keep hand-writing `zip_with`, `scanl` and the `sorted` round trip. The survey shows
  the same helpers written again in separate files; nothing is shared.
- **Function, `List` only**: `list_zip_with`, `list_sort`, … in the prelude, as `list_map`
  is. No class, but `Vec` gets nothing and a later generic `zip_with` needs a second name.
- **Function, `stdlib.list` only**: no prelude change, so no reseed and no binder renames (§6).
  But the most-used names need an import, and `scanl` and `min`/`max` are not `List`-specific.
- **Representation** (this design): `Zip` and `Sort` classes for the container-keeping case,
  `Foldable` for fixed results. For sorting, the representation also changes underneath: a
  `LinVec` in place of a list of runs (§4.6.1).

## 6. Impact

- **Syntax**: none.
- **Semantics**: `Zip` with `List` and `Vec` instances, and `zip_with`, `zip`, `scanl`, `min`,
  `max` in the prelude; `map_adjacent` in `stdlib.list`. A new module `stdlib.sort`: `Sort` with
  `List` and `Vec` instances, `sort`, `sort_by`. `linvec.from_vec`. `vec_sort` and `vec_sort_by`
  leave the prelude.
- **Type system**: none.
- **Errors**: none new. A `List` and a `Vec` passed to one `zip` fail to unify, with the existing
  `List`/`Vec` hint. A call to `vec_sort` becomes an unknown name.
- **Generated code**: unused prelude code is dropped. The bundler renames every local binder that
  shares a prelude value's name (`bundler.local_name`, `docs/prelude-name-identity-v0.md` §5),
  and parameter names reach IR. In `stdlib/`, `min` is a binder in `stdlib/log.sprout` and `max`
  in `stdlib/terminal.sprout`; the compiler has none of the new names outside comments. Golden IR
  that includes those modules moves by name. `sort` and `sort_by` are not prelude names, so they
  rename nothing. The compiler's four sorts move to `stdlib.sort`, so its IR changes for real,
  and so does every golden program that sorted.
- **Trust**: no new `unsafe_pure` user, no new extern. `just linvec-smoke`'s BCE check, which
  covers `stdlib.linvec.*` and `stdlib.linheap.*`, adds `stdlib.sort.*`.

## 7. Compatibility and migration

Every name except the two retired ones is new. Existing definitions with these names shadow the
prelude inside their file:

- `fn zip_with` and `fn scanl` in `examples/leetcode/p0238_…`; migrate it to the prelude ones.
- `fn min`/`fn max` in `stdlib/math/int.sprout`: unchanged; qualified uses read `imath.max`.

`p0046`, `p0049`, `p0078` and `p0347` replace their `sorted` round trip with `sort`; `p3110` uses
`list.map_adjacent`.

**`vec_sort(v)` → `sort(v)`, `vec_sort_by(f, v)` → `sort_by(f, v)`**, each file adding
`import stdlib.sort (sort, sort_by)` as it needs. The result type is unchanged. A `List` caller
that went through `Vec` drops the round trip: `vec_to_list(vec_sort(vec_from_list(xs)))` is
`sort(xs)`.

Downstream, step 1 found none of the step-1 names defined or bound in repbit, repbit-cards,
sprout-postgres, sprout-sqlite or uncharted-suns. uncharted-suns imports `stdlib.math.int`'s
`min`/`max` selectively, which keeps them; all 369 of its entry files type-check. Step 2 migrated
its 15 `vec_sort`/`vec_sort_by` uses (§4.6.2; uncharted-suns #479).

## 8. Spec and docs

- `docs/spec-v0.md`: the prelude section gains `Zip`, its instances and the prelude functions.
  The `Double` ordering and `min_by`/`max_by` paragraphs, which state their laws through
  `vec_sort` and `vec_sort_by`, restate them through `sort` and `sort_by`.
- `docs/stdlib-reference.md`: `stdlib.list.map_adjacent`; a `stdlib.sort` section; `vec_sort`
  and `vec_sort_by` removed; `linvec.from_vec`; the `mutvec_sort` paragraph's comparison moves
  to `sort`.
- `docs/idiomatic-sprout.md`: `zip_with` and `scanl` in place of index loops.
- `docs/sorting-v0.md`: the pure sort is in place now (§4.6.1); its "reworking `vec_sort`'s list
  sort" item is done.
- `BACKLOG.md`: on landing, delete "List toolkit"; delete (b) of "Deferred non-goals from
  `docs/min-max-combinators-v0.md` §2" and the `zip` part of (c) in the collection-combinators
  entry. Step 2 deleted "`vec_sort` sorts lists of `(key, index, value)` tuples", and renamed
  `vec_sort` in the `Ord Char` and primitive-sort entries.
- Status: the same as the rest of spec §8.5, which documents the prelude's classes.

## 9. Implementation steps

1. ~~`Zip` with the `List` and `Vec` instances, `zip_with`, `zip`, `min`, `max`;
   `stdlib.list.map_adjacent`. Measure `max` against `imath.max` in a DP loop.~~ Landed;
   `max` measured (§4.7).
2. ~~`stdlib.sort`: `Sort` with the `List` and `Vec` instances, `sort`, `sort_by`, in place over
   `LinVec` (§4.6.1); `linvec.from_vec` (§4.6.4). Move every caller, delete `vec_sort` and
   `vec_sort_by` from the prelude (§4.6.2), reseed. Measured through `bench/list_sort/bench.sh`
   (§4.6.1). Migrate uncharted-suns in a matching commit.~~ Landed; uncharted-suns #479.
3. After `NonEmpty`: `scanl`, the `NonEmpty` `Zip` instance.
4. Migrate the LeetCode examples (§7).

The instances may write `match (xs, ys) with`: a match on a tuple literal builds no tuple
(`docs/compiler-internals.md` §Tuple-CPR, tuple-literal split).

## 10. Tests

`tests/stdlib/test_list_toolkit.spr`:
- `zip_with` and `zip` on `List` and `Vec`: equal lengths, either side shorter, either side empty;
  `g` not called past the shorter length; dictionary forwarding through a `where Zip f` caller.
- `list.map_adjacent` on 0, 1, 2 and many elements.
- `min`/`max` on `Int`, `String`, tuples; on a tie under a key-only `Ord`, `min` returns the first
  argument and `max` the second.
- `scanl` (step 3): empty input gives `NonEmpty(init, [])`; order is left to right.

`tests/stdlib/test_sort.spr`:
- `sort` and `sort_by` on `List` and `Vec`: empty, one, sorted, reversed, all equal, duplicates.
- Lengths around the block and pass boundaries — 15, 16, 17, 31, 32, 33, 64, 65, 100 — so the
  result ends in either array, and the last run is short.
- Agreement with `mutvec_sort` on random input; stability of `sort_by` on a key with ties and of
  `sort` under a key-only `Ord`, both across merge boundaries.
- The input `Vec` is unchanged after `sort` and `sort_by` (`from_vec` copies).
- 200k elements (stack depth); dictionary forwarding through a `where Sort f` caller.
- `linvec.from_vec` (in the linvec suite): a write to the copy leaves the source alone.

Failure tests (`tests/conformance/type_error/`): `zip` of a `List` and a `Vec`; `zip` of a
`MutVec` and `sort` of one (no instance); `max('a', 'b')` while there is no `Ord Char`; and
`max("a", "b")` under `import stdlib.math.int (max)`, which shows the selective import still
gives the `Int` one.
