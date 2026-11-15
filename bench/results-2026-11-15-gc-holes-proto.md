# Step 5 prototype (holes from occupancy maps) on the bench VM, 2026-11-15

`docs/gc-stage2-v0.md` §5.7's prototype gate. One binary, two arms:
`just bench-vm-gc 8ea02bf3 "def:SPROUT_GC_HOLES=0;holes:SPROUT_GC_HOLES=1" -n 6 -s none -w <12 workloads>`.
c4a-standard-2 (Neoverse-V2, 2 vCPU), clang 21.1.8, load 0.47 before the run. Six runs per arm,
median (min–max). Stdout hashes agree in every run.

`holes / def` by medians; `~` where the two ranges overlap.

| workload | GC ms def → holes | GC | CPU s def → holes | CPU | RSS MB def → holes | RSS | cycles def → holes |
|---|---|---|---|---|---|---|---|
| selfcompile | 1,418 → 596 | 0.42 | 4.50 → 4.26 | 0.95 | 252 → 234 | 0.93 | 138 → 133 |
| compiler_test | 1.36 → 0.34 | 0.25 | 0.23 → 0.23 | ~ | 80.5 → 80.4 | ~ | 19 → 19 |
| digit_recognizer | 8.61 → 1.92 | 0.22 | 0.41 → 0.41 | ~ | 4.85 → 4.46 | ~ | 42 → 48 |
| gc_roots | 158 → 5.99 | 0.04 | 0.75 → 0.65 | 0.87 | 4.50 → 4.47 | ~ | 5,028 → 5,028 |
| http_log_middleware | 1,670 → 338 | 0.20 | 8.38 → 7.46 | 0.89 | 4.47 → 4.47 | ~ | 49,847 → 49,847 |
| nqueens | 352 → 26.8 | 0.08 | 1.63 → 1.48 | 0.91 | 4.47 → 5.38 | ~ | 8,298 → 8,298 |
| astar | 5.17 → 0.78 | 0.15 | 0.01 → 0.01 | ~ | 4.47 → 4.47 | ~ | 140 → 140 |
| math_transcendental | 0.01 → 0.01 | ~ | 0.26 → 0.26 | ~ | 4.47 → 4.47 | ~ | 1 → 1 |
| mutvec_int | 228 → 83.8 | 0.37 | 0.44 → 0.33 | 0.75 | 10.7 → 10.6 | ~ | 1,338 → 1,774 |
| load_spike | 447 → 75.9 | 0.17 | 0.86 → 0.54 | 0.63 | 16.8 → 17.0 | ~ | 1,309 → 1,261 |
| rotate_classes | 1,004 → 76.1 | 0.08 | 1.83 → 1.33 | 0.73 | 40.6 → 4.79 | 0.12 | 6,421 → 12,398 |
| mutvec_push | 16.2 → 8.30 | 0.51 | 0.15 → 0.14 | 0.93 | 80.5 → 61.3 | 0.76 | 59 → 58 |

Pause percentiles fall with GC time: self-compile p90 25.9 → 13.1 ms, load_spike p90 1.26 →
0.084 ms. Full table, with the MINOR=0 baseline: the run's `summary.md` (`build/bench-vm/`, not
committed).

**Gate.** GC time falls by 49–96% on every workload that collects, against the one third §5.7
asks for. No workload's CPU or RSS rises outside its spread: nqueens' RSS median is 5.38 against
4.47, but its runs span 4.46–6.50 and overlap the default arm's. Self-compile CPU falls 5%; the
small-heap workloads, whose sweep walked a whole region per collection for a live set of under 100
objects, fall 9–37%.

**Collection count.** The free count's new unit shifts the trigger: rotate_classes collects 1.93×
as often, mutvec_int 1.33× and digit_recognizer 1.14×. Each collection is cheap enough that CPU
still falls on all three. The replay check (peak regions under a pinned trigger) is separate.
