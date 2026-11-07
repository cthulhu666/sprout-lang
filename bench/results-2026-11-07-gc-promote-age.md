# Promotion age and nursery size A/B (2026-11-07)

[gc-stage2-v0.md](../docs/gc-stage2-v0.md) §7 step 2, measured on `3202d82e` before its rebase
(master `abb44a5e`): `SPROUT_GC_PROMOTE_AGE` (K) and `SPROUT_GC_NURSERY` (N) as named arms of
`bench/gc_minor/ab.sh -n 6 -a …`, each against a minor-off run back to back. K1N0 is the current
default. galaxy_client ran in a separate pass on the same build (1200 frames). Stdout matched
between arms on every workload, and every run exited 0. The machine was loaded (load average at
run start: median 8.7 and 7.1, max 18.1 and 25.4).

Ratios are to minor-off. `~`: the arms' ranges overlap. Peak slots are the largest `heap_after +
free` of a run, the median over runs.

## K = 2, nursery on

| workload | peak slots | cpu | GC ms | p50 µs | p90 µs | RSS | collections |
|---|---|---|---|---|---|---|---|
| selfcompile | 0.89 | **0.83** | **0.55** | **0.52** | **0.49** | **0.96** | 1.13× |
| compiler_test | **1.07** | 0.98 ~ | **0.84** | **0.87** | 0.90 ~ | 0.99 ~ | 0.89× |
| digit_recognizer | 0.71 | 1.03 ~ | **0.89** | **0.49** | **0.69** | **0.91** | 1.38× |
| gc_roots | 1.02 | 1.03 ~ | 0.88 ~ | **0.90** | 0.93 ~ | 1.01 ~ | 0.98× |
| http_log_middleware | 1.01 | 0.99 ~ | **0.83** | **0.81** | **0.92** | 0.95 ~ | 0.99× |
| nqueens | 1.02 | **0.91** | **0.65** | **0.59** | **0.90** | 0.97 ~ | 0.99× |
| astar | **1.11** | 1.00 ~ | 0.89 ~ | 0.93 ~ | 1.03 ~ | **1.21** | 0.94× |
| math_transcendental | 1.00 | 1.00 ~ | — | — | — | 1.05 ~ | 1.00× |
| mutvec_int | 0.93 | 1.00 ~ | 0.86 ~ | **0.92** | **0.80** | 1.01 ~ | 0.95× |
| galaxy_client | **1.76** | 0.98 ~ | 0.91 ~ | **0.05** | 0.88 ~ | 0.89 ~ | 2.61× |

## K = 3, nursery on

| workload | peak slots | cpu | GC ms | p50 µs | p90 µs | RSS | collections |
|---|---|---|---|---|---|---|---|
| selfcompile | 0.87 | **0.84** | 0.60 ~ | **0.65** | **0.53** | **0.93** | 1.18× |
| compiler_test | **1.07** | 0.96 ~ | **0.82** | **0.88** | **0.90** | 0.99 ~ | 0.89× |
| digit_recognizer | 0.71 | 1.00 ~ | 0.89 ~ | **0.52** | 0.66 ~ | **0.88** | 1.38× |
| gc_roots | 1.02 | 1.03 ~ | **0.92** | 0.94 ~ | 0.95 ~ | 1.02 ~ | 0.98× |
| http_log_middleware | 1.01 | 0.98 ~ | 0.82 ~ | **0.81** | **0.92** | 0.98 ~ | 0.99× |
| nqueens | 1.02 | **0.91** | **0.65** | **0.59** | **0.87** | 0.99 ~ | 0.99× |
| astar | 1.05 | 1.00 ~ | **0.87** | 0.93 ~ | 1.00 ~ | 1.02 ~ | 0.94× |
| math_transcendental | 1.00 | 0.97 ~ | — | — | — | 1.01 ~ | 1.00× |
| mutvec_int | 0.93 | 0.98 ~ | 0.85 ~ | 0.91 ~ | **0.79** | 1.00 ~ | 0.95× |
| galaxy_client | **1.73** | 0.99 ~ | 0.89 ~ | **0.06** | 0.77 ~ | 0.88 ~ | 3.03× |

The self-compile's K1N0 row for comparison: cpu 0.78, GC 0.42, p50 0.79 ~, p90 0.40, RSS 1.08,
peak slots 1.07.

## Against the §2 goals

- **Pause: met.** No workload's p50 or p90 is above minor-off beyond spread at K = 2 or 3. The
  nursery takes galaxy_client's median pause from 1,010 µs to 48.5.
- **Peak slots: missed** on galaxy_client (1.76, 1.73), astar (1.11 at K = 2) and compiler_test
  (1.07). mutvec_int, the §4 miss, is now 0.93.
- **Self-compile CPU: missed.** 0.83 at K = 2 against the 0.80 goal; GC time 0.55 against 0.52.
  A minor re-marks the young survivors, and there are more collections (77 → 104 at K = 2).
- The nursery without K (K1N1) regresses mutvec_int (peak 1.65, cpu 1.12) and digit_recognizer
  (peak 1.37), as §4 found.

So the defaults stay K = 1, nursery off.

## Why galaxy_client's peak rises

From run 3 of each arm, steady state:

| arm | threshold | regions | slots held | live after |
|---|---|---|---|---|
| minor off | 82k | 8 | ~242k | ~1.5k |
| K2N1, at each major | 130k | 12 | ~380k | ~1.5k |

Both thresholds are the footprint floor, `heap + (heap + free) / k`, which grows with the slots
already held. The nursery runs minors during the load phase; what they promote is load-phase data
that lives until the next major. Startup ends with 12 regions instead of 8, the ~1.5k survivors
are spread over all of them so none is released, and the floor keeps the heap that size. RSS
does not show it: galaxy_client's RSS is ~1.4 GB, mostly graphics, against 4 MiB of extra regions.

## `SPROUT_GC_MAJOR_INPUT=old`

The 2× rule reading the old count instead of the heap count wins on no goal. At K = 2 its peak
slots are higher on four workloads (selfcompile 0.98 against 0.89, digit_recognizer 0.91 against
0.71, astar 1.14 against 1.11, mutvec_int 0.97 against 0.93), equal elsewhere, and 1.74 against 1.76
on galaxy_client. The self-compile's CPU is 0.83 in both; pause ratios move a few points either
way. The switch is removed.
