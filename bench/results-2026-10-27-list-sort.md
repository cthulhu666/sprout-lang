# Undecorated list sort — 2026-10-27

`bench/list_sort/bench.sh 7`, on `28ef3476` plus the bench itself. The measurement
[list-toolkit-v0.md](../docs/list-toolkit-v0.md) §4.6.1 asks for before the merge changes: is an
undecorated merge sort faster than today's `vec_sort`, which sorts `(key, index, value)` triples?

Machine: Apple M3 Pro (11 cores), macOS 15.7.5, Homebrew clang 23.1.1, built with
`just compile-native`. Load average 5.5–8.9 during the runs, so the 1M rows are noisy (spread up
to 17%); the 1k rows stay under 9%. Random Ints in [0, 65536). Time is the sort alone, input
built beforehand; peak is the process's max RSS, input included.

Variants:
- `vec_list` — List input, today's path: `vec_from_list |> vec_sort |> vec_to_list`.
- `usort_list` — List input, the prototype: singleton runs in input order, adjacent runs merged
  left with right, a tie takes the left run, one `compare` per comparison.
- `vec` / `usort_vec` — Vec input: `vec_sort`, against the prototype between `vec_to_list` and
  `vec_from_list`.
- `vec_by` — `vec_sort_by(negate, v)`, today's decorated sort.
- `dsort_by` — the same decorated sort through the prototype's merge, the comparison passed as a
  function, keeping input order through two extra reversals.
- `dsort_lean` — `dsort_by` without those reversals: the index breaks every tie, so order is moot.

| row | unit | min | median | spread |
|---|---|---:|---:|---:|
| vec_list 1k | ns/elem | 565.4 | 587.2 | 3.9% |
| usort_list 1k | ns/elem | 459.8 | 477.8 | 3.9% |
| vec 1k | ns/elem | 569.9 | 590.0 | 3.5% |
| usort_vec 1k | ns/elem | 473.7 | 515.6 | 8.8% |
| vec_by 1k | ns/elem | 558.2 | 585.4 | 4.9% |
| dsort_by 1k | ns/elem | 625.8 | 647.3 | 3.4% |
| dsort_lean 1k | ns/elem | 569.9 | 590.1 | 3.5% |
| vec_list 1M | ns/elem | 1277.2 | 1412.0 | 10.6% |
| usort_list 1M | ns/elem | 933.8 | 1003.5 | 7.5% |
| vec 1M | ns/elem | 1257.1 | 1410.3 | 12.2% |
| usort_vec 1M | ns/elem | 1016.7 | 1103.0 | 8.5% |
| vec_by 1M | ns/elem | 1268.0 | 1380.1 | 8.8% |
| dsort_by 1M | ns/elem | 1434.4 | 1491.2 | 4.0% |
| dsort_lean 1M | ns/elem | 1324.1 | 1552.4 | 17.2% |
| vec_list 1M | MB peak | 550.9 | 559.0 | 1.5% |
| usort_list 1M | MB peak | 467.2 | 471.8 | 1.0% |
| vec 1M | MB peak | 540.2 | 549.1 | 1.6% |
| usort_vec 1M | MB peak | 543.2 | 558.5 | 2.8% |
| vec_by 1M | MB peak | 542.5 | 548.3 | 1.1% |
| dsort_by 1M | MB peak | 644.5 | 654.8 | 1.6% |
| dsort_lean 1M | MB peak | 632.9 | 639.8 | 1.1% |

An earlier 7-round run of the first six variants, under less load, gave the same picture with
spreads under 8%: medians `vec_list` 590 / 1321, `usort_list` 475 / 992, `vec` 587 / 1354,
`usort_vec` 521 / 1044, `vec_by` 585 / 1338, `dsort_by` 650 / 1505 ns/elem at 1k / 1M.

## Reading

- **The undecorated sort is faster on both containers.** List: 19% at 1k, 25–29% at 1M, and
  16–19% less peak memory at 1M. Vec: 11–13% at 1k, about 22% at 1M; peak memory unchanged,
  since the two conversions cost what the triples saved. The §4.6.1 gate passes for both instances.
- **`sort_by` gains nothing from the shared merge.** `dsort_lean` ties `vec_by` at 1k (590 against
  585), so passing the comparison as a function costs nothing measurable there; at 1M the noise
  is larger than the gap. `dsort_by`'s extra 11% is its two reversals. The 90 MB more peak
  memory of both prototypes is their `vec_to_list` first; `vec_sort_by` reads the Vec directly.
- Checksums agree across all variants of a key, so every variant sorts.
- A trap found while tidying the bench: writing the comparison as `match (l, r) with
  | ((lk, li, _), (rk, ri, _)) -> …` made `dsort_lean` ~30% slower at 1k (≈780 against ≈600 ns
  per element), consistent with a tuple built per comparison. The bench uses a `let` group; a merge
  comparison must not match on a tuple it builds.
