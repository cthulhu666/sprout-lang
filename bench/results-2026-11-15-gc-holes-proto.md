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

## Replay check

`docs/gc-stage2-v0.md` §5.7: peak ordinary regions at a collection's start under a pinned trigger,
prototype against the placement replay's `holes-ovf-tails`, within 10% or explained.

**Pinning.** `SPROUT_GC_THRESHOLD` sets `g_gc_threshold_pinned`: the footprint floor and the
nursery rule are off, so the next threshold is `max(3 x heap, T)`, with heap the live object count
after the sweep. The free count, whose unit changes under HOLES, is read only by the floor. The
minor/major choice (`sprout_gc_gen_choose_minor`, `sprout_gc_gen_record`) reads marked and live
object counts and the minor count, nothing placement-dependent. Checked on every run: cycle
count, per-cycle `heap_after`, `marked`, `threshold` and major/minor are identical in the
default-mode run (trace's configuration), the trace's `E` records and the HOLES run. Only the
sweep label differs: HOLES reports every minor `sweep=young`, the default run `sweep=full` for
the minor before a major. No other env was needed. Unpinned the counts diverge (the cycles
column of the first table).

Trace: `bench/gc_place/run.sh` with `EXTRA_ENV=SPROUT_GC_THRESHOLD=T`, default GC,
`SPROUT_GC_REGION_LISTS=1`. Prototype: same binary, `SPROUT_GC_HOLES=1 SPROUT_DEBUG_GC=1`; the
cycle line's `start_regions=` (HOLES only) counts ordinary regions before the mark. Replay figure:
`peak_reg@G` of `holes-ovf-tails`.

| workload | T | cycles trace / HOLES | peak regions at start: replay | prototype | proto / replay | within 10% | replay `base` |
|---|---|---|---|---|---|---|---|
| selfcompile | 2,200,000 | 26 / 26 | 222 | 222 | 1.00 | yes | 249 |
| rotate_classes | 4,096 | 8,796 / 8,796 | 2 | 2 | 1.00 | yes | 5 |
| rotate_classes_mixed | 4,096 | 8,800 / 8,800 | 2 | 2 | 1.00 | yes | 2 |
| rotate_classes_cap | 4,096 | 16,176 / 16,176 | 1 | 1 | 1.00 | yes | 1 |
| mutvec_push | 4,096 | 84 / 84 | 2 | 2 | 1.00 | yes | 14 |
| load_spike | 283,000 | 99 / 99 | 16 | 16 | 1.00 | yes | 16 |
| nqueens | 4,120 | 8,249 / 8,249 | 1 | 1 | 1.00 | yes | 2 |
| digit_recognizer | 48,500 | 8 / 8 | 3 | 3 | 1.00 | yes | 4 |
| rotate_classes | 300,000 | 209 / 209 | 23 | 23 | 1.00 | yes | 33 |
| rotate_classes_mixed | 300,000 | 209 / 209 | 17 | 17 | 1.00 | yes | 17 |
| rotate_classes_cap | 300,000 | 209 / 209 | 23 | 23 | 1.00 | yes | 27 |
| mutvec_push | 300,000 | 2 / 2 | 16 | 16 | 1.00 | yes | 16 |
| nqueens | 300,000 | 112 / 112 | 33 | 33 | 1.00 | yes | 35 |
| digit_recognizer | 150,000 | 3 / 3 | 6 | 6 | 1.00 | yes | 6 |

The first eight use `ab.sh`'s `W_THR` where it has one, else 4,096 (the floor-off value
`rotate_classes.sprout` names). Their peaks are 1-3 regions on five workloads, too coarse to
test a 10% bound, so the last six repeat those at a larger T. Every peak matches exactly. Over
the 42,982 collection starts in the 14 runs, prototype and replay agree at every start except
two:

| workload, T | cycle | prototype | replay | diff |
|---|---|---|---|---|
| selfcompile, 2.2M | 3 (minor) | 65 | 62 | 5% |
| mutvec_push, 300,000 | 2 (atexit major) | 16 | 15 | 7% |

**selfcompile cycle 3.** At the end of cycle 2 the prototype holds 65 regions (`arena_regions`
unchanged, none released); the replay's `E` line is 62. The prototype's survivors leave three
regions with 9, 7 and 2 live objects (a temporary per-region dump), which the replay's placement
does not produce. Ruled out by measurement: the overflow cursor (6 requests in the interval,
none behind the main cursor); region order (a throwaway replay variant with LIFO arena-chunk
reuse and address-order walk: the same single differing cycle, 62). Remaining difference: the prototype's region table is
address-sorted and a cursor continues from a region opened in the middle of it, so it re-walks
regions above it. In the interval before cycle 2, 4 regions open mid-table and 7,659 allocations
land in regions above them (later intervals: at most 399); the replay never goes back to an
earlier hole. This fits the one divergent cycle, but no controlled test isolates it.

**mutvec_push cycle 2.** The prototype takes freed tails last-in first-out (`g_hole_tails`),
the replay lowest region first. Replay with LIFO tails: 0 differing cycles at T = 300,000
(2 of 2) and at 4,096 (84 of 84). Not committed: the replay's min-heap order is the one the
earlier placement results used.

Replay's calibration: `base` matches the trace's ordinary and large region counts at every `E` in
every run. These runs reported CALIBRATION MISS on `digit_recognizer` (T = 48,500) and
`selfcompile`, with `base`'s free-slot count off by 1 at 2 of 8 and 1 of 26 `E`s. The cause was the
bump path's open-region hint, a table index that a large region inserted below it moves; the
trace now records it (`H`), and both runs calibrate exactly.

**Outcome.** Peak ordinary regions at a collection's start equal the replay's on all 14 runs;
the per-collection gap is at most 7%. The gate holds.
