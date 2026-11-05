# Minor collections A/B after the young-only sweep (2026-11-05)

[gc-stage2-v0.md](../docs/gc-stage2-v0.md) §7 step 1, measured on master `abb44a5e`: a minor sweeps
only young slots and keeps the free lists. Same harness as
[results-2026-11-04-gc-minor-pfv.md](results-2026-11-04-gc-minor-pfv.md) (`bench/gc_minor/ab.sh -s
default -n 6`), which measured stage 1 on `82d4a520`. The machine was loaded (load average at run
start: median 14.6, max 28.9). Stdout matched between arms on every workload, and every run exited 0.

Each arm runs back to back with a minor-off run, so the ratios to minor-off are what compare across
the two days. Raw times do not.

## Default settings

`~`: the arms' ranges overlap. "Stage 1" is the 2026-11-04 ratio.

| workload | cpu | GC ms (stage 1) | p50 µs (stage 1) | p90 µs (stage 1) | RSS (stage 1) | collections |
|---|---|---|---|---|---|---|
| selfcompile | 0.78 ~ | **0.39** (0.52) | **0.70** (0.72) | **0.39** (0.49) | **1.09** (1.07) | 92 → 77 (63+14) |
| compiler_test | 0.93 ~ | 0.84 ~ (1.06) | **0.83** (0.94) | 0.87 ~ (0.90) | 0.98 ~ (1.00) | 19 → 20 |
| digit_recognizer | 1.02 ~ | **0.72** (0.90) | **0.47** (0.84) | 0.83 ~ (0.92) | 1.03 ~ (1.01) | 16 → 15 |
| gc_roots | 1.04 ~ | 0.95 ~ (1.03) | 0.94 ~ (0.96) | 0.96 ~ (0.96) | 0.97 ~ (1.00) | 5,028 → 5,075 |
| http_log_middleware | 1.03 ~ | 0.96 ~ (0.99) | **0.94** (1.02) | 1.00 ~ (0.97) | 0.99 ~ (1.02) | 50,322 → 50,579 |
| nqueens | 0.99 ~ | 0.87 ~ (0.88) | 0.96 ~ (1.00) | 0.98 ~ (0.93) | 0.99 ~ (0.97) | 8,279 → 8,363 |
| astar | 1.00 ~ | 1.03 ~ (1.03) | 1.00 ~ (1.02) | 0.98 ~ (0.95) | 1.06 ~ (1.08) | 140 → 149 |
| math_transcendental | 1.00 ~ | — | — | — | 0.97 ~ (1.00) | 1 → 1 |
| mutvec_int | **0.93** | **0.80** (0.82) | **1.24** (1.23) | **1.57** (1.51) | **1.23** (1.25) | 926 → 550 |
| galaxy_client (1200 frames) | 0.99 ~ | **0.71** (1.01) | **0.58** (0.91) | 0.81 ~ (0.99) | 0.97 ~ (1.03) | 139 → 144 |

## Which minors swept only young slots

From run 1 of each workload's minor arm. A young-only sweep walks fewer slots than `heap_before`,
since it skips every old one; a full sweep walks every slot, free ones included. On the
self-compile this split gives 48 / 12 / 3, the counts the runtime reported while the sweep was
built.

| workload | minors | young-only | full, before a major | full, a region would empty | peak regions |
|---|---|---|---|---|---|
| selfcompile | 63 | 48 | 12 | 3 | 1,226 |
| compiler_test | 14 | 9 | 5 | 0 | 2 |
| digit_recognizer | 10 | 7 | 3 | 0 | 4 |
| gc_roots | 4,585 | 4,097 | 488 | 0 | 1 |
| http_log_middleware | 38,728 | 26,822 | 11,906 | 0 | 1 |
| nqueens | 5,849 | 3,337 | 2,512 | 0 | 2 |
| astar | 98 | 49 | 49 | 0 | 1 |
| mutvec_int | 352 | 155 | 197 | 0 | 3 |
| galaxy_client | 116 | 98 | 18 | 0 | 10 |

## Reading

- **The gain is GC time and pause, where minors can skip old slots.** The self-compile's GC time
  falls to 0.39 of minor-off and its p90 pause to 0.39. galaxy_client (p50 0.91 → 0.58) and
  digit_recognizer (p50 0.84 → 0.47) gain most against stage 1: their live sets are small against
  the heap, so the young set is most of what is left to sweep.
- **CPU is unchanged** against stage 1 within the spread, including the reuse path's region
  lookup. The paired runs in the step-1 change gave paired-ratio medians of 1.000–1.012.
- **RSS and collection counts are stage 1's**, as the design requires: the trigger and region
  release are unchanged. galaxy_client's and http_log_middleware's counts vary from run to run
  in both arms on both days.
- **Full sweeps happen almost only before a major.** Where majors are frequent (astar, nqueens,
  mutvec_int, http_log_middleware), 31–56% of the minors still walk the whole heap.
- **mutvec_int is unchanged:** p50 1.24, p90 1.57, RSS 1.23, still outside the
  [§2](../docs/gc-stage2-v0.md) goals. 197 of its 352 minors precede a major, and its cost is dead
  old objects, not the sweep ([§1](../docs/gc-stage2-v0.md)). Step 2 (promotion age and nursery
  size) addresses it.
