# Minor collections A/B after pointer-free vectors: the default flips (2026-11-04)

`docs/gc-generational-v0.md` §14.8 step 6, re-measured on master `82d4a520`: scalar `vector_mutset`
lowers inline without the barrier ([scalar-store-inline-v0.md](../docs/scalar-store-inline-v0.md)),
and scalar vectors carry the NOSCAN flag ([pointer-free-vectors-v0.md](../docs/pointer-free-vectors-v0.md)).
Same harness and method as [results-2026-11-01-gc-minor-step5.md](results-2026-11-01-gc-minor-step5.md);
default settings, N = 6 per arm. The machine was loaded (load average 13–27, median 17), so the
ranges are wide. Stdout matched between arms on every workload, and every run exited 0. The suite
passes under `SPROUT_GC_MINOR=1 SPROUT_GC_GENCHECK=1`.

galaxy_client was built from a copy of the game repo patched as `ab.sh` patches it, plus one
patch `ab.sh` gained here: `game.starcat.make_dirs` binds a `proc_run` Result in a `Unit` block.

## Default settings

`~`: the arms' ranges overlap.

| workload | cpu s off → minor | ratio | GC ms | p50 µs | p90 µs | RSS | collections |
|---|---|---|---|---|---|---|---|
| selfcompile | 5.98 → 4.77 | **0.80** ~ | 0.52 ~ | 0.72 ~ | **0.49** | **1.07** | 92 → 77 (63+14) |
| compiler_test | 0.71 → 0.71 | 1.00 ~ | 1.06 ~ | 0.94 ~ | 0.90 ~ | 1.00 ~ | 19 → 20 |
| digit_recognizer | 0.36 → 0.38 | 1.04 ~ | 0.90 ~ | 0.84 ~ | 0.92 ~ | 1.01 ~ | 16 → 15 |
| gc_roots | 0.54 → 0.57 | 1.05 ~ | 1.03 ~ | 0.96 ~ | 0.96 ~ | 1.00 ~ | 5,028 → 5,075 |
| http_log_middleware | 7.42 → 7.27 | 0.98 ~ | 0.99 ~ | 1.02 ~ | 0.97 ~ | 1.02 ~ | 50,164 → 50,579 |
| nqueens | 1.35 → 1.27 | 0.95 ~ | 0.88 ~ | 1.00 ~ | 0.93 ~ | 0.97 ~ | 8,279 → 8,363 |
| astar | 0.01 → 0.01 | 1.00 ~ | 1.03 ~ | 1.02 ~ | 0.95 ~ | 1.08 ~ | 140 → 149 |
| math_transcendental | 0.18 → 0.17 | 0.97 ~ | — | — | — | 1.00 ~ | 1 → 1 |
| mutvec_int | 0.32 → 0.29 | 0.92 ~ | **0.82** | **1.23** | **1.51** | **1.25** | 926 → 550 |
| galaxy_client (1200 frames) | 10.1 → 10.2 | 1.01 ~ | 1.01 ~ | 0.91 ~ | 0.99 ~ | 1.03 ~ | 138 → 150 |

The self-compile's CPU ranges overlap because of one loaded run, but each round runs its two
arms back to back, and minor is lower in all 6 (0.73–0.84). No workload's CPU regresses.

## Against step 5

- **astar** is back inside the spread: GC 1.50 → 1.03 ~, pause p50 1.64 → 1.02 ~. Its
  `MutVec Int` stores no longer take the barrier, so its minors rescan nothing.
- **mutvec_int**'s mode-off GC fell 2,190 → 179 ms: its vectors are no longer scanned at all, so a
  minor has little left to save. Its pauses rise (p50 1.23, p90 1.51) with RSS 1.25: minors run
  fewer, larger collections (926 → 550), and 198 of them are majors. The cause is dead old objects
  ([gc-stage2-v0.md §1](../docs/gc-stage2-v0.md)).
- **galaxy_client**'s pause gain is gone (p50 0.39 → 0.91 ~). Its steady live set was mostly a
  `Bitset`'s `Vec Int` read as pointers; flagged, it shrank from 96.7k objects to about 1.5k, so a
  minor has almost no old object to skip.

## Decision

§14.8 step 4's rule holds: the compiler gains (CPU 0.80, GC time 0.52, pause p90 0.49) and no
workload's CPU regresses beyond the spread. `SPROUT_GC_MINOR` is on by default; `0` turns it off.
The price is memory (self-compile RSS 1.07, mutvec_int 1.25) and mutvec_int's longer pauses.
Their causes are measured in [gc-stage2-v0.md §1](../docs/gc-stage2-v0.md).
