# LeetCode survey — stdlib and language gaps (2026-10-27)

31 LeetCode problems were solved in Sprout with the stdlib as it stands at `2e96dc69`, and every
point where the stdlib or the language forced a workaround was recorded. This file is the record of
what they found. Design follow-ups: `docs/non-empty-v0.md`; the list toolkit is not yet written.

## 1. Method

- **Pool**: all 4096 problems from LeetCode's GraphQL API; 3308 are free.
- **Weighting**: topic tags over the free problems. The top tags are array (1947), string (772),
  hash-table (715), math (619), dynamic-programming (600), sorting (465), greedy (434).
- **Selection**: the most-liked free problems of the top ~25 tags, preferring problems that span
  several tags. Database problems were excluded (they test SQL); pointer-heavy linked-list problems
  were covered through tree ADTs instead.
- **Solving**: each solution runs every example from its statement and prints actual against
  expected. Several were also checked on 10k–200k-element inputs against an independent reference.

| Group | Problems |
|---|---|
| strings / hash / counting | 3110, 1, 49, 3, 20, 14, 13, 347 |
| DP / prefix sums / greedy | 53, 121, 70, 198, 322, 238, 560, 55 |
| sorting / search / bits | 56, 15, 42, 300, 136, 338, 215, 67 |
| grid / graph / tree / backtracking | 200, 54, 207, 739, 102, 78, 46 |

**Result: 31 of 31 pass.**

## 2. Stdlib gaps, ranked by how many of the 31 problems hit them

Every "absent" below was checked against the stdlib source. "Tracked" names the `BACKLOG.md` entry
that already covers it.

| # | Missing | Problems | Notes | Tracked |
|---|---|---|---|---|
| 1 | Two-argument `max`/`min` in the prelude (plus `abs_diff`) | 9 | Only `stdlib.math.int.max`/`min` exist; 5 of 8 DP solutions import them. | "Deferred non-goals from `docs/min-max-combinators-v0.md` §2" (b) |
| 2 | `zip`, `zip_with`, `map_adjacent`, `enumerate` | 8 | `zip_with` + `drop(1)` measured equal to a hand-written loop for `map_adjacent`. | "Deferred non-goals from `docs/collection-combinators-v0.md` §7" (c) |
| 3 | `vec_at(i, v) -> a` that traps out of range | 7 | Today `vec_get_or(i, 0, v)` with a meaningless fallback. `mutvec_at` is the precedent; guideline §2 is the tension. | "No trapping `Vec` index read" |
| 4 | Map/Set keyed by anything but `String` | 6 | `Dict` sits on runtime externs (`map_get(m, key: String)`). Int keys cost an `int_to_string` per access, and `dict_entries` orders `"10"` before `"9"`. | "`Dict`/`Set` take only `String` keys" |
| 5 | `scanl` | 6 | Absent. | "List toolkit" |
| 6 | `dict_update`, `dict_get_or`, `group_by`, `frequencies` | 5 | Every counting solution hand-writes get-default-set. | "`Dict`/`Set` take only `String` keys" |
| 7 | `list_sort` / `list_sort_by` | 5 | Today a round trip through `Vec`. | "List toolkit" |
| 8 | `instance Ord Char`, and `Char -> Int` | 4 | `<` accepts `Char` (spec), but there is no `Ord Char`, so `vec_sort` on chars does not compile. | "No `instance Ord Char`, though `<` accepts `Char`"; "There is no `Char -> Int`" |
| 9 | A total Int remainder | 3, plus test-data generators | There is no `%`; `stdlib.math.int.mod` returns `Maybe`. | "No total Int remainder" |
| 10 | A table builder whose entries read earlier entries | 2 | Coin Change: a `vec_append` fold is quadratic; only `LinVec` with explicit recursion is fast. | "No fast pure table whose entries read earlier entries" |
| — | `transpose`, `fold1`, `sum`/`product`, bisect/`lower_bound`, `popcount`, heap `top_k`/`push_pop`, `zip_longest`, right fold, `common_prefix` | 1–2 each | | partly |
| — | FIFO queue | 0 | Three solutions avoided needing one; a shortest-path BFS would not. | no |

`head`/`tail`/`last` on `List` are not gaps: `docs/non-empty-v0.md` §4.2 puts them on `NonEmpty`
only.

## 3. Language findings

Checked by compiling a probe, unless marked otherwise.

| Finding | Problems | Evidence | Tracked |
|---|---|---|---|
| A lambda cannot destructure a tuple parameter | 8 | `\ ((a, b), x) -> …` is a parse error, "Expected parameter name". So nested patterns in the parameter list are unused syntax, and adding them changes no program. `\ (a, b) -> …` is a two-parameter lambda. | "No `fst` / `snd`" |
| `[x \| rest]` is a pattern only | 2 | As an expression: "Expected ]". | "Tail-spread syntax for list-literal expressions" |
| `match u with \| () -> …` fails with no location | 1 | `ast_to_ir`: "UnitPattern in match not yet supported" — no source position, against guideline §5. | "`match u with \| () -> …` fails with no source location" |
| `range_fold` has no `where linear b` | 2 | Its signature; `list_fold` has the bound. A `LinVec` accumulator needs explicit recursion. | "No fast pure table whose entries read earlier entries" |
| `0..n` is inclusive | 1 | Gave a silently wrong answer (Climbing Stairs). By design, `docs/ranges-v0.md`; a beginner trap. | — |
| `to_string` does not quote strings in a list | — | `to_string([[""]])` prints `[[]]`; `to_string(["", "a"])` prints `[, a]`. | "`to_string` does not quote strings inside a container" |
| A private prelude function is callable from user code | — | `list_length` is declared without `export` and `list_length([1, 2, 3])` compiles. | "A private prelude function is callable from user code" |
| `print` of a list shows constructors | — | `print(xs)` prints `Cons(…)`; `print(to_string(xs))` prints `[…]`. | "Reassess the `print` design" |

## 4. What worked

- `LinVec` and `linheap` handled the hard problems fast: Course Schedule on 200k nodes in 0.18 s,
  LIS and top-k on 100k elements in about 0.17 s (agent-measured, -O1).
- `list_fold` with a named step function over a `LinVec` accumulator compiled; only a `_`
  placeholder around a linear accumulator is rejected (agent-reported).
- Char-literal patterns, `let (a, b, c) = st`, comprehensions with tuple patterns, `linvec.accum`
  for bucketing, and `string.string_digits` needed no workaround.

## 5. Performance traps (agent-measured)

- `vec_sort` on 1M Ints: 1.8 s and 519 MB, since it sorts decorated tuples; a heap top-k on the
  same input: 0.43 s, 95 MB.
- A `Set` keyed by `int_to_string` was 2.2x slower than a `LinVec` visited array on 300x300 grids.
- `vec_append` is O(n) per call, as documented: a pure DP built with it took 4.7 s at 1e5.
- `fold_indexed` allocates a tuple per element; `list_append(a, b)` copies `a`; `List` has no
  indexing, so index-based algorithms convert to `Vec` first.
