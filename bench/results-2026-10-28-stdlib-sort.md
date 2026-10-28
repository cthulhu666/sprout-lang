# stdlib.sort against the list merge sorts — 2026-10-28

`bench/list_sort/bench.sh 5`, Apple M3 Pro, random Ints in [0, 65536), 5 interleaved rounds.
Load average 6–9 throughout; spreads stay at or under 3%, except `sort_by` at 1M (8.4%).
Design: `docs/list-toolkit-v0.md` §4.6.1.

| row | unit | min | median | spread |
|---|---|---:|---:|---:|
| sort_list 1k | ns/elem | 81.2 | 82.3 | 1.4% |
| sort_list 1M | ns/elem | 135.8 | 137.7 | 1.4% |
| sort_list 1M | MB peak | 88.0 | 88.2 | 0.2% |
| sort_vec 1k | ns/elem | 81.3 | 83.2 | 2.3% |
| sort_vec 1M | ns/elem | 134.5 | 137.3 | 2.1% |
| sort_vec 1M | MB peak | 87.7 | 88.0 | 0.3% |
| sort_by 1k | ns/elem | 154.8 | 156.3 | 1.0% |
| sort_by 1M | ns/elem | 433.1 | 469.4 | 8.4% |
| sort_by 1M | MB peak | 125.7 | 125.9 | 0.2% |
| usort_list 1k | ns/elem | 447.6 | 451.3 | 0.8% |
| usort_list 1M | ns/elem | 925.9 | 937.1 | 1.2% |
| usort_list 1M | MB peak | 470.2 | 478.2 | 1.7% |
| usort_vec 1k | ns/elem | 470.0 | 484.7 | 3.1% |
| usort_vec 1M | ns/elem | 953.3 | 979.3 | 2.7% |
| usort_vec 1M | MB peak | 551.9 | 560.3 | 1.5% |
| dsort_by 1k | ns/elem | 601.0 | 613.2 | 2.0% |
| dsort_by 1M | ns/elem | 1328.4 | 1356.8 | 2.1% |
| dsort_by 1M | MB peak | 655.6 | 662.3 | 1.0% |
| dsort_lean 1k | ns/elem | 555.6 | 566.6 | 2.0% |
| dsort_lean 1M | ns/elem | 1227.3 | 1242.6 | 1.2% |
| dsort_lean 1M | MB peak | 637.6 | 645.1 | 1.2% |

The prelude's `vec_sort` is gone, so it is not a row. Its last numbers, from the §4.6.1 prototype run
the same day: 560 ns/elem at 1k, 1256 at 1M, 563 MB; `vec_sort_by` 562, 1276, 564 MB. Against
those, `sort` is 6.8× faster at 1k and 9.1× at 1M; `sort_by` 3.6× and 2.7×. Peak RSS includes
the ~88 MB input.
