# Nursery share and survivor floor A/B (2026-11-08)

[gc-stage2-v0.md](../docs/gc-stage2-v0.md) §5.5 and §7 step 2b, measured on `7ce5062c` plus the
step 2b runtime, where the nursery rule was behind switches: K (`SPROUT_GC_PROMOTE_AGE`), the
nursery mode (`SPROUT_GC_NURSERY=2`), s (`SPROUT_GC_NURSERY_SHARE`) and c
(`SPROUT_GC_NURSERY_SURVIVE`), as named arms of `bench/gc_minor/ab.sh -a …`, each against a
minor-off run back to back. `ab.sh` is macOS-only: it times with `/usr/bin/time -l`. The new
workload is `bench/gc_minor/load_spike.sprout`, which reproduces galaxy_client's load-then-steady
shape headless. The switches are retired; the arms cannot be rerun as written.

The next collection runs `max(s × heap, c × m, base / factor)` allocations on, where m is the last
minor's marked count. "Before" is the old default, K = 1 with the nursery off.

Stdout matched between arms on every workload and every run exited 0. The machine was loaded.
Load average at run start, median and max:

| run | median | max |
|---|---|---|
| round 1 (n = 6) | 12.08 | 21.85 |
| round 2 (n = 6) | 8.89 | 51.84 |
| galaxy_client (n = 3) | 5.93 | 6.54 |
| self-compile (n = 10) | 16.86 | 26.04 |

Ratios are to minor-off. `~`: the arms' ranges overlap. Peak slots are the largest `heap_after +
free` of a run (atexit excluded), the median over runs; they do not vary within a build.

## Round 1: share and K, no survivor floor

s ∈ {0.25, 0.5, 1}, K ∈ {2, 3}, c = 0, and K2N1 (K = 2, the baseline-sized nursery).

Peak slots are fixed by the share. K2N1 misses on digit_recognizer (1.18) and load_spike (1.57);
the share arms are at 0.67–1.06 on every workload.

CPU regresses on two workloads, and the ranges do not overlap:

| arm | mutvec_int cpu | mutvec_int peak | load_spike cpu | load_spike peak |
|---|---|---|---|---|
| K3 s = 1 | 1.33 | 0.83 | 1.25 | 0.98 |
| K3 s = 0.5 | 1.54 | 0.68 | 1.28 | 0.79 |
| K3 s = 0.25 | 1.93 | 0.92 | 1.22 | 0.84 |

A nursery smaller than the young working set promotes it (§5.5). Hence the survivor floor.

## Round 2: survivor floor c

c = 2 at s ∈ {0.25, 0.5, 1}, K ∈ {2, 3}, plus s = 1 with c = 4 at K = 2.

The floor removes the CPU regressions: mutvec_int and load_spike are now 1.11 ~ at K3 s = 1
(1.15 ~ and 1.15 ~ at s = 0.5), the ranges overlapping.

Peak slots, K3 c = 2:

| workload | s = 0.25 | s = 0.5 | s = 1 |
|---|---|---|---|
| selfcompile | 0.54 | 0.63 | 0.79 |
| digit_recognizer | 0.77 | 0.67 | 0.65 |
| mutvec_int | 1.00 | 0.97 | 0.95 |
| load_spike | 1.06 | 0.96 | 1.03 |
| astar | 1.00 | 1.00 | 1.00 |
| compiler_test, gc_roots, http_log_middleware, nqueens | 1.00 | 1.00 | 1.00 |

s = 0.5 is at or under 1.00 on every workload (0.97 or lower where peak moves); s = 1 is 1.03 on
load_spike. s = 0.25 has the lowest peak on the self-compile only.

K = 3 against K = 2 at s = 1, c = 2: mutvec_int 0.95 against 1.24, astar 1.00 against 1.05,
digit_recognizer 0.65 against 0.78, load_spike 1.03 against 0.97, selfcompile 0.79 against 0.77.
c = 4 raises peaks (K2 s = 1: mutvec_int 1.86, load_spike 1.14, selfcompile 1.01).

Self-compile CPU rises as s falls, with more collections: K3 c = 2 cpu 0.82 ~ at s = 1, 0.88 ~ at
s = 0.5 and 0.97 ~ at s = 0.25; collections 1.66×, 2.85× and 4.99× minor-off's.

### K = 3, s = 1, c = 2 (the default)

| workload | peak slots | cpu | GC ms | p50 µs | p90 µs | RSS | collections |
|---|---|---|---|---|---|---|---|
| selfcompile | 0.79 | 0.82 ~ | 0.58 ~ | 0.49 ~ | 0.30 | 0.85 | 1.66× |
| compiler_test | 1.00 | 0.99 ~ | 0.84 | 0.82 | 0.85 ~ | 1.00 ~ | 1.00× |
| digit_recognizer | 0.65 | 1.02 ~ | 1.31 ~ | 0.46 | 0.47 ~ | 0.91 ~ | 2.69× |
| gc_roots | 1.00 | 1.07 ~ | 0.98 ~ | 0.96 ~ | 1.00 ~ | 1.08 ~ | 1.00× |
| http_log_middleware | 1.00 | 1.04 ~ | 0.97 ~ | 0.88 | 0.97 ~ | 0.97 ~ | 1.00× |
| nqueens | 1.00 | 0.92 ~ | 0.71 ~ | 0.62 | 0.93 ~ | 1.00 ~ | 1.00× |
| astar | 1.00 | 1.00 ~ | 0.97 ~ | 1.00 ~ | 1.29 ~ | 0.95 ~ | 1.00× |
| math_transcendental | — | 0.97 ~ | 1.00 ~ | — | — | 1.02 ~ | 1.00× |
| mutvec_int | 0.95 | 1.11 ~ | 1.04 ~ | 0.60 | 0.84 | 0.99 ~ | 1.44× |
| load_spike | 1.03 | 1.11 ~ | 1.07 ~ | 0.09 | 0.42 ~ | 0.92 | 5.19× |
| galaxy_client | 1.03 | 1.01 ~ | 1.02 ~ | 0.03 | 0.51 | 0.93 ~ | 7.09× |

galaxy_client ran in a separate pass, on a display, 3 runs per arm, peak slots 274,054 against
265,682. Its median pause falls from 1,012 µs to 28. math_transcendental collects only at exit.
digit_recognizer's GC time is above 1 at s = 1 (1.31 ~) and s = 0.5 (1.08 ~), within spread.

## Self-compile against the old default

10 runs each, minor-off, before (K = 1, nursery off) and K = 3, s = 1, c = 2, back to back.

| arm | cpu | GC ms | p50 µs | p90 µs | RSS | peak slots | collections |
|---|---|---|---|---|---|---|---|
| before | 0.83 ~ | 0.52 ~ | 0.48 | 0.57 ~ | 1.46 | 1.58 | 0.77× |
| K3 s1 c2 | 0.88 ~ | 0.68 ~ | 0.54 ~ | 0.35 | 0.86 | 0.79 | 1.66× |

Paired per run, K3 s1 c2 against before: cpu median 1.05, above 1.0 in 8 of 10 runs; GC time median
1.27, above 1.0 in 8 of 10. Peak slots are 5,307,016 minor-off, 8,409,407 before, 4,186,423 after.

## Peak slots across builds

Peak slots as a ratio to minor-off on the self-compile of seven commits, `stdlib/` and
`bootstrap/` from each, with the runtime held fixed at the old default's:

| tree | minor-off slots | before | K3 s1 c2 | K2N1 |
|---|---|---|---|---|
| `abb44a5e` | 5,290,286 | 1.07 | 0.72 | 0.85 |
| `bf913b90` | 5,174,724 | 1.35 | 0.79 | 0.82 |
| `c1c3c4e3` | 5,323,508 | 1.16 | 0.79 | 0.86 |
| `85b94f18` | 5,323,399 | 1.16 | 0.79 | 0.87 |
| `f1a7fcc5` | 5,181,882 | 1.54 | 0.93 | 1.05 |
| `e03bec5b` | 5,178,234 | 1.54 | 0.95 | 1.06 |
| `7ce5062c` | 5,307,016 | 1.58 | 0.79 | 1.05 |

The old default's ratio moves from 1.07 to 1.58 with the compiler source alone. K3 s1 c2 stays
within 0.72–0.95. A peak is stable within one build, not across builds.

## Against the §2 goals

- **Pause: met.** No workload's p50 or p90 is above minor-off beyond spread. astar's p90 is 1.29 ~.
- **Peak slots: met.** At most 1.03 (load_spike, galaxy_client), against the 5% goal.
- **RSS: met.** No workload is above minor-off beyond spread; the self-compile is 0.85 and 0.86.
- **Self-compile CPU and GC time: missed.** 0.82 ~ (round 2) and 0.88 ~ (10 runs) against 0.80;
  GC time 0.58 ~ and 0.68 ~ against 0.52. The before arm reaches 0.83 ~ and 0.52 ~, with peak
  slots 1.07–1.58 across trees and RSS 1.46. §2 records the move of the goal to 0.88.

So the defaults are K = 3, s = 1, c = 2.

## Not covered

No workload here is a long-running server with a large, slowly churning live set (a cache): there
each survivor is re-marked up to K times before it is old. digit_recognizer's GC time (1.31 ~ at
s = 1) is the one row that could point at it.
