# Per-region free lists A/B on the stage-2 default (2026-11-12)

[gc-stage2-v0.md](../docs/gc-stage2-v0.md) §5.6 and §7 step 4. `just bench-vm-gc` on a quiet VM
(c4a-standard-2, load average 1.00–1.04), `bench/gc_minor/ab.sh`, 6 interleaved runs per arm,
at the default K = 3, s = 1, c = 2:

- `off`: `SPROUT_GC_MINOR=0`, global lists;
- `offRL`: `SPROUT_GC_MINOR=0 SPROUT_GC_REGION_LISTS=1`;
- `on`: the default, global lists;
- `rl`: `SPROUT_GC_REGION_LISTS=1`.

Stdout hashes agreed across arms and every run exited 0. Cells are medians; `~` means the two
arms' ranges overlap.

## Full-walk minors

Minors that walked the whole heap with no major next (`kind=minor sweep=full` not followed by
`kind=major`), per run. They are the minors `sprout_gc_young_sweep_ok` sends to the full sweep
because a region would empty.

| workload | on | rl |
|---|---|---|
| selfcompile | 9 | 0 |
| digit_recognizer | 12 | 0 |
| nqueens | 559 | 0 |
| mutvec_int | 33 | 0 |
| load_spike | 2 | 0 |
| others | 0 | 0 |

## rl against on

| workload | GC ms on → rl | ratio | CPU s on → rl | ratio | p50 µs on → rl | p90 µs on → rl |
|---|---|---|---|---|---|---|
| selfcompile | 1,517 → 1,470 | 0.97 | 4.81 → 4.81 | 1.00 ~ | 3,106 → 3,048 | 28,652 → 27,733 |
| digit_recognizer | 8.97 → 5.89 | 0.66 | 0.41 → 0.41 | 1.00 ~ | 244 → 56 | 392 → 386 |
| nqueens | 314 → 301 | 0.96 | 1.57 → 1.56 | 0.99 ~ | 31 → 32 | 64 → 58.5 |
| load_spike | 430 → 427 | 0.99 ~ | 0.83 → 0.81 | 0.98 | 109 → 110 | 1,334 → 1,289 |
| http_log_middleware | 1,704 → 1,690 | 0.99 ~ | 8.30 → 8.39 | 1.01 ~ | 34 → 34 | 40 → 40 |
| mutvec_int | 200 → 201 | 1.00 ~ | 0.41 → 0.41 | 1.00 ~ | 144 → 143 | 264 → 258 |
| gc_roots | 135 → 139 | 1.03 | 0.71 → 0.72 | 1.01 ~ | 26 → 27 | 33 → 33 |
| astar | 4.47 → 4.58 | 1.02 | 0.01 → 0.01 | — | 31 → 32 | 38 → 37.5 |
| compiler_test | 1.53 → 1.61 | 1.05 ~ | 0.27 → 0.29 | 1.07 ~ | 73 → 74.5 | 110 → 119 |

Against minor-off with the global lists, the self-compile's `rl` is GC 0.57, CPU 0.84, RSS 0.91,
p90 0.32; `on` is GC 0.59, CPU 0.84, RSS 0.90, p90 0.33.

## Peak slots and regions

Peak slots are the largest `heap_after + free` of a run (atexit excluded); they did not vary
across runs except http_log_middleware's (5,909–5,939). They are equal between `on` and `rl` on
every workload but the self-compile: 4,300,228 → 4,344,334 (1.01; 0.83 of minor-off). load_spike
holds 12 regions in both arms, minor-off 11: address order released no region there.

## Reuse cost (offRL against off)

CPU 1.00–1.02 on every workload but compiler_test (1.08 ~, a 0.27 s run), all inside the spread
except nqueens (1.01). GC time 0.97–1.00, except compiler_test's 1.06 ~.
