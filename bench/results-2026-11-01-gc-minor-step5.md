# Minor collections A/B after the fused clear: the churn regressions are gone, scalar `MutVec` stores remain (2026-11-01)

`docs/gc-generational-v0.md` §14.8 step 5: the sweep before a major clears the colour bits, so a
major runs no `sprout_gc_clear_marks` walk. Same harness, machine and method as
[results-2026-11-01-gc-minor.md](results-2026-11-01-gc-minor.md); default settings only, N = 6
per arm, load average 4–10 (median 7.3). Stdout matched between arms on every workload. The suite
passes under `SPROUT_GC_MINOR=1 SPROUT_GC_GENCHECK=1`, which now also checks the predicted
post-sweep count on every collection.

## Why step 5

Timing the phases in nqueens and http_log_middleware put the whole step-4 regression in the
clear walk: 94–128 ms of nqueens' ~440 ms of GC, 354–372 ms of http_log's ~1,760 ms. Their mark
is 7–8% of GC; the sweep is the rest.

## Default settings

| workload | cpu s off → minor | ratio | GC ms | p50 µs | p90 µs | RSS | collections |
|---|---|---|---|---|---|---|---|
| selfcompile | 4.43 → 3.71 | **0.84** | 0.58 | 0.85 ~ | 0.63 | **1.21** | 91 → 71 (58+13) |
| compiler_test | 0.54 → 0.55 | 1.04 ~ | 1.02 ~ | 0.93 ~ | 0.97 ~ | 1.00 ~ | 19 → 20 |
| digit_recognizer | 0.32 → 0.33 | 1.02 ~ | 0.74 | 0.69 | 0.70 | 1.01 ~ | 16 → 15 |
| gc_roots | 0.59 → 0.60 | 1.01 ~ | 0.99 ~ | 0.96 ~ | 0.98 ~ | 1.03 ~ | 5,028 → 5,075 |
| http_log_middleware | 7.10 → 7.06 | 0.99 ~ | 0.99 ~ | 0.97 ~ | 0.98 ~ | 1.01 ~ | 50,161 → 50,493 |
| nqueens | 1.23 → 1.26 | 1.02 ~ | 1.05 ~ | 0.99 ~ | 1.00 ~ | 1.04 ~ | 8,279 → 8,363 |
| astar | 0.02 → 0.02 | 1.00 ~ | **1.50** | **1.64** | **1.57** | 1.17 ~ | 140 → 149 |
| math_transcendental | 0.17 → 0.17 | 1.03 ~ | — | — | — | 1.04 ~ | 1 → 1 |
| mutvec_int | 2.42 → 1.48 | **0.61** | 0.59 | 1.02 ~ | 1.04 ~ | 1.26 | 926 → 550 |
| galaxy_client (1200 frames) | 9.29 → 9.32 | 1.00 ~ | 0.84 | **0.39** | **0.42** | 0.99 ~ | 72 → 140 |

Against step 4: nqueens CPU 1.08 → 1.02 ~ and http_log 1.04 → 0.99 ~, both now inside the
spread; the gains hold. No workload's CPU regresses beyond the spread.

## astar: the barrier on scalar stores

astar's collection pause rises 1.6× (70 → 118 µs per minor, same slots walked, fewer objects
marked). It keeps two 10,000-element `MutVec Int` and writes them every step.
`sprout_gc_on_store` runs the barrier on every `vector_mutset`, whatever the value, so both
vectors are remembered and each minor rescans all 20,000 elements, though an `Int` holds no
pointer. §6 already said the barrier must be emitted only for pointer-typed stores.

Tested in a scratch runtime, GC ms (off / minor / minor with the barrier skipped for a value that
is not a heap pointer), N = 4:

| workload | off | minor | filtered |
|---|---|---|---|
| astar | 9.8 | 14.5 | 10.3 |
| mutvec_int | 2,190 | 1,300 | 575 |
| digit_recognizer | 5.5 | 4.0 | 4.2 |

The cause holds, but filtering at run time does not pay: a `sprout_heap_lookup` per store costs
digit_recognizer's 10.6M `Double` stores 0.31 → 0.36 s user CPU, and the inline heap-range
compare alone 0.355 → 0.405 s (load ~14). The barrier has to be left out where the compiler knows
the element type is a scalar.
