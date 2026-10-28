# List toolkit — zip, adjacent pairs, scan, sort, min/max

Status: **step 1 landed** (§9): `Zip`, `zip_with`, `zip`, `min`, `max`, `list.map_adjacent`.
Approved, not yet built: a `Sort` class with `sort`/`sort_by` (§4.6); the undecorated merge for
`sort`, measured faster (§4.6.1); `scanl` (§4.5). `enumerate` deferred (§4.3). Open: retiring
`vec_sort`/`vec_sort_by` (§4.6.2).

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

Non-goals (v0):
- `group_by`. The survey's callers want keyed grouping into a map (`p0049`), which belongs with the
  "`Dict`/`Set` take only `String` keys" entry. Its groups will be `NonEmpty a`
  (`docs/non-empty-v0.md` §9). Adjacent-run grouping has no caller.
- `unzip`, `zip3`, `zip_longest`, a strict zip that fails on unequal lengths, `scanl1`, `scanr`.
  No survey caller; each is additive later.
- `abs_diff` (§4.7).
- `enumerate`, until Sprout has a lazy sequence type or decides not to (§4.3).
- A `Vec` `map_adjacent`. `Vec` callers index instead; add it when a caller asks.
- Faster sorting beyond what §4.6.1 gets for free. `docs/sorting-v0.md` owns that.
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
| `sort` | `f a -> f a where Sort f, Ord a` | prelude |
| `sort_by` | `(a -> k) -> f a -> f a where Sort f, Ord k` | prelude |
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
export class Sort f
  fn sort_values(xs: f a) -> f a where Ord a
  fn sort_by_values(key: local a -> k, xs: f a) -> f a where Ord k

export fn sort(xs: f a) -> f a where Sort f, Ord a =
  sort_values(xs)

export fn sort_by(key: local a -> k, xs: f a) -> f a where Sort f, Ord k =
  sort_by_values(key, xs)
```

- **Semantics**: stable, ascending, O(n log n), recursion depth O(log n). `sort_by` calls `key`
  once per element, as Haskell's `sortOn` and Rust's `sort_by_cached_key` do (§3).
- **Two methods, not one.** `sort` as `sort_by(\x -> x, …)` would decorate every element, the
  cost §4.6.1 removes. Same reasoning as `Foldable`'s two methods. The constraint sits on the
  method, as `Traversable`'s `Applicative` does: `f` alone picks the instance.
- **Instances**: `List` and `Vec`; `NonEmpty` after `docs/non-empty-v0.md` lands. `MutVec` is not
  one: its sort works in place, has an effect and returns `Unit`, so `mutvec_sort` stays apart.
  `LinVec` fits the method's shape (consume, return sorted, in place behind `unsafe_pure` as
  `linvec.set` is) but is deferred with its `Zip` instance (§4.2).
- **Prior art**: none of the six languages has a sort class. Each has one sort per module or type
  (`Data.List.sort`, OCaml's `List.sort`/`Array.sort`, Rust's slice method). Sprout has no methods
  and no overloading; a class is how it gives one name to several containers, as for `map`,
  `filter` and `zip_with`.

#### 4.6.1 One merge, undecorated where it can be

Today `vec_sort` is `vec_sort_by(\v -> v, …)`. It builds an `(a, Int, a)` triple per element and
a strip pass, and `decorated_key_lt` compares keys through `ord_lt` and then `ord_gt`: up to two
`compare` calls per comparison. The survey measured it on 1M Ints at 1.8 s and 519 MB.

The proposal: `sort_values` does not decorate. A merge keeps stability without the index when it
seeds the runs in input order, merges adjacent runs left with right, and takes from the left run
on a tie. The merge helpers (`merge_runs`, `merge_pass`, `merge_all`, `to_runs`) take the
comparison as a parameter, as `mutvec_sort_with` does in `stdlib.mutable`, so the decorated
`sort_by_values` and the undecorated `sort_values` share one merge. The undecorated comparison
calls `compare` directly: `ord_lt` costs ~40% in a sort (`docs/sorting-v0.md` §Where the time
goes, item 2).

The `Vec` instance converts to a list, sorts, and converts back, as `vec_sort` does now.

**Measured** (`bench/results-2026-10-27-list-sort.md`, 1k and 1M random Ints). The undecorated
sort beats today's `vec_sort` on both instances: on `List` by 19% at 1k and 25–29% at 1M, with
16–19% less peak memory; on `Vec` by 11–13% at 1k and about 22% at 1M, peak memory unchanged.
So `sort_values` takes the undecorated merge in both instances.

`sort_by` gains nothing from the change. Through the shared merge it ties today's `vec_sort_by`
at 1k (590 against 585 ns per element); at 1M the noise (load 5.5–8.9) is larger than the gap. The
implementation step re-measures `sort_by` on the shared merge against today's, under low load,
and keeps today's decorated merge for `sort_by` if the shared one is slower. The `Vec` instance's
`sort_by_values` keeps reading the `Vec` directly, as `vec_sort_by_collect` does: converting it
to a list first cost the prototype ~90 MB of peak memory at 1M.

#### 4.6.2 Retiring `vec_sort` and `vec_sort_by` (option, not decided)

With `sort` and `sort_by` working on `Vec`, the prelude exports two names for each operation. The
option: move every caller to the generic names, then drop `export` from `vec_sort` and
`vec_sort_by`. Their code stays, as the `Vec` instance's helpers; an instance in another module
would be compiled only into programs that import it, so `sort(v)` would stop working without an
import.

- **Callers**: 41 calls in 22 files outside the prelude. Four are in the compiler
  (`ast_to_ir` ×2, `bind_census`, `analysis_service_driver`), so the move needs a reseed.
- **Un-exporting hides nothing yet.** A private prelude function is still callable from user code
  (BACKLOG, "A private prelude function is callable from user code"). The callers must move
  before that is fixed, or the fix breaks them.
- **Downstream** repos are not checked.

The same reasoning covers every `vec_*` that duplicates a generic function (`vec_map`,
`vec_filter`, `vec_fold`, `vec_any`, …), and a `stdlib.vec` module for the `Vec`-only operations
(`vec_get`, `vec_length`, …, used in 165 files). That is a separate change, not this toolkit's.

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
  `Foldable` for fixed results.

## 6. Impact

- **Syntax**: none.
- **Semantics**: two classes, `Zip` and `Sort`, each with `List` and `Vec` instances; `zip_with`,
  `zip`, `sort`, `sort_by`, `scanl`, `min`, `max` in the prelude; `map_adjacent` in
  `stdlib.list`.
- **Type system**: none.
- **Errors**: none new. A `List` and a `Vec` passed to one `zip` fail to unify, with the existing
  `List`/`Vec` hint.
- **Generated code**: unused prelude code is dropped. The bundler renames every local binder that
  shares a prelude value's name (`bundler.local_name`, `docs/prelude-name-identity-v0.md` §5),
  and parameter names reach IR. In `stdlib/`, `min` is a binder in `stdlib/log.sprout` and `max`
  in `stdlib/terminal.sprout`; the compiler has none of the new names outside comments. Golden IR
  that includes those modules moves by name. No binder in the tree is named `sort` or `sort_by`.
  The shared merge (§4.6.1) changes `vec_sort_by`'s IR for real.

## 7. Compatibility and migration

Every name is new. Existing definitions with these names shadow the prelude inside their file:

- `fn zip_with` and `fn scanl` in `examples/leetcode/p0238_…`; migrate it to the prelude ones.
- `fn min`/`fn max` in `stdlib/math/int.sprout`: unchanged; qualified uses read `imath.max`.

`p0046`, `p0049`, `p0078` and `p0347` replace their `sorted` round trip with `sort`; `p3110` uses
`list.map_adjacent`. `vec_sort` and `vec_sort_by` keep working; §4.6.2 is the option to retire
them.

Downstream, step 1 found none of the step-1 names defined or bound in repbit, repbit-cards,
sprout-postgres, sprout-sqlite or uncharted-suns. uncharted-suns imports `stdlib.math.int`'s
`min`/`max` selectively, which keeps them; all 369 of its entry files type-check.

## 8. Spec and docs

- `docs/spec-v0.md`: the prelude section gains `Zip`, `Sort`, their instances and the prelude
  functions.
- `docs/stdlib-reference.md`: `stdlib.list.map_adjacent`.
- `docs/idiomatic-sprout.md`: `zip_with` and `scanl` in place of index loops.
- `BACKLOG.md`: on landing, delete "List toolkit"; delete (b) of "Deferred non-goals from
  `docs/min-max-combinators-v0.md` §2" and the `zip` part of (c) in the collection-combinators
  entry.
- Status: the same as the rest of spec §8.5, which documents the prelude's classes.

## 9. Implementation steps

1. ~~`Zip` with the `List` and `Vec` instances, `zip_with`, `zip`, `min`, `max`;
   `stdlib.list.map_adjacent`. Measure `max` against `imath.max` in a DP loop.~~ Landed;
   `max` measured (§4.7).
2. `Sort` with the `List` and `Vec` instances, `sort`, `sort_by`, the undecorated merge
   (§4.6.1, measured). Re-measure `sort_by` on the shared merge under low load
   (`bench/list_sort/bench.sh`) before it replaces today's.
3. After `NonEmpty`: `scanl`, the `NonEmpty` `Zip` instance.
4. Migrate the LeetCode examples (§7).
5. If §4.6.2 is taken: move the `vec_sort`/`vec_sort_by` callers to `sort`/`sort_by`, reseed,
   then drop the two exports.

The instances may write `match (xs, ys) with`: a match on a tuple literal builds no tuple
(`docs/compiler-internals.md` §Tuple-CPR, tuple-literal split).

## 10. Tests

`tests/stdlib/test_list_toolkit.spr`:
- `zip_with` and `zip` on `List` and `Vec`: equal lengths, either side shorter, either side empty;
  `g` not called past the shorter length; dictionary forwarding through a `where Zip f` caller.
- `list.map_adjacent` on 0, 1, 2 and many elements.
- `sort` on `List` and `Vec`: empty, one, sorted, reversed, duplicates; stability of `sort_by`
  on a key with ties, and of `sort` under a key-only `Ord`; agreement with `vec_sort`; 200k
  elements (recursion depth); `sort_by` calls `key` once per element.
- `min`/`max` on `Int`, `String`, tuples; on a tie under a key-only `Ord`, `min` returns the first
  argument and `max` the second.
- `scanl` (step 3): empty input gives `NonEmpty(init, [])`; order is left to right.

Failure tests (`tests/conformance/type_error/`): `zip` of a `List` and a `Vec`; `zip` of a
`MutVec` and `sort` of one (no instance); `max('a', 'b')` while there is no `Ord Char`; and
`max("a", "b")` under `import stdlib.math.int (max)`, which shows the selective import still
gives the `Int` one.
