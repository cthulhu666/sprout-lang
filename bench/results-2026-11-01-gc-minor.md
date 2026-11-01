# Minor collections A/B: the compiler gains, two churn workloads regress (2026-11-01)

`docs/gc-generational-v0.md` §14.8 step 4: `SPROUT_GC_MINOR=0` vs `1` before flipping the default.

**Machine:** Apple M3 Pro, macOS 15.7.5. Master `b77ef76c`. Harness `bench/gc_minor/ab.sh`: every
binary built once at `-O2`, arms interleaved in pairs with the first arm alternating, each run under
`SPROUT_DEBUG_GC=1` and `/usr/bin/time -l`. Cells are the median with min–max; ratios are minor/off
of medians, `~` where the two min–max ranges overlap. Load average 4–7 during the main runs (other
sessions were active), 10–22 during the `MAJOR_EVERY` sweep, which is therefore read for cycle
counts, not times. Stdout matched between arms on every workload.

## Verdict

**Not flipped.** §14.8's rule is "the compiler gains and no §5 workload regresses beyond run-to-run
spread". The compiler gains (self-compile CPU 0.83×, GC time 0.56×); nqueens (CPU 1.08×) and
http_log_middleware (1.04×) regress outside the spread.

## Default settings (N = 6 per arm)

| workload | cpu s off → minor | ratio | GC ms | p50 µs | p90 µs | RSS | collections |
|---|---|---|---|---|---|---|---|
| selfcompile | 4.21 → 3.51 | **0.83** | 0.56 | 0.76 | 0.78 | **1.36** | 92 → 70 (57+13) |
| compiler_test (`test_ir_codegen_match`) | 0.52 → 0.51 | 0.98 ~ | 1.12 | 0.93 ~ | 1.28 | 1.01 ~ | 19 → 20 |
| galaxy_client (1200 frames) | 9.84 → 9.81 | 1.00 ~ | 0.88 | **0.36** | **0.51** | 1.08 ~ | 70 → 149 (127+22) |
| mutvec_int | 2.32 → 1.44 | **0.62** | 0.60 | 1.03 | 1.09 | 1.23 | 926 → 550 (352+198) |
| digit_recognizer | 0.31 → 0.31 | 1.00 ~ | 0.84 ~ | 0.71 | 0.80 ~ | 1.01 ~ | 16 → 15 |
| gc_roots | 0.57 → 0.57 | 1.00 ~ | 1.02 ~ | 0.94 | 1.19 | 0.99 ~ | 5,028 → 5,075 |
| http_log_middleware | 6.61 → 6.88 | **1.04** | 1.15 | 1.00 ~ | 1.47 | 0.98 ~ | 50,161 → 50,726 (35,827+14,863) |
| nqueens | 1.17 → 1.27 | **1.08** | 1.25 | 1.03 ~ | 1.75 | 1.02 ~ | 8,279 → 8,363 (5,849+2,514) |
| astar | 0.01 → 0.02 | 1.33 ~ | 1.59 | 1.60 | 1.59 | 1.11 | 140 → 149 |
| math_transcendental | 0.17 → 0.17 | 0.97 ~ | — | — | — | 0.99 ~ | 1 → 1 |

The galaxy client's frame-time cost is the pause: p50 2,634 → 954 µs, p90 3,416 → 1,753 µs on a
17 ms frame. Its total CPU does not move (the run is vsync-bound). It is built from a copy of
uncharted-suns with three calls unwrapped (`env.get`, `argv_get`, `args.parse_argv`), because that
repo does not yet compile against this stdlib's Bytes-based env and argv API.

## Why the churn workloads regress

- **The 2× rule makes a third of their collections majors.** Their live sets are tens of objects,
  so the heap doubles over the last major's baseline within a few collections: nqueens runs 2,514
  majors in 8,363 collections, http_log 14,863 in 50,726, where `MAJOR_EVERY=19` alone would allow
  about 5%.
- **A major costs more than a collection with the mode off:** `sprout_gc_clear_marks` walks every
  slot before marking. In one nqueens run a major averages 73 µs, against 41 µs per collection
  with the mode off. That is the p90 rise (1.47–1.75×) and the GC-time rise.
- **A minor saves nothing there:** the same run's minors average 40 µs. Marking is already nearly
  free (§5: 23–60 objects per collection), and stage 1's minor still sweeps every slot.

So the 2× rule needs a floor, which §14.4 left open. A floor would remove most of these majors;
the remaining cost would be a minor that costs what a plain collection does, so the expected result
is neutral, not a gain. That is unmeasured.

## Other findings

- **mutvec_int gains from fewer collections, not cheaper ones.** Per-collection p50 is 1.03×: the
  barrier remembers the whole 1M-element vector, so every minor rescans it. The win is that minors
  leave old garbage counted, the adaptive trigger sees a bigger heap and collects less often
  (926 → 550). Under a fixed threshold the same effect runs the other way (600 → 839, CPU 1.38×).
- **A fixed threshold does not equalise collections.** `SPROUT_GC_THRESHOLD` compares the heap
  object count, and after a minor that count still includes old objects that died since the last
  major, so the minor arm collects more often (self-compile 50 → 58, digit_recognizer 10 → 26).
  §14.8 assumed otherwise. Per-collection cost is read from p50/p90 instead: self-compile pause
  0.42× at both, galaxy client 0.79× p50.
- **`SPROUT_GC_MAJOR_EVERY` does not bind.** At 19, 50 and 1000 every swept workload ran the same
  collections (self-compile 57 minor + 13 major): the 2× rule chooses every major. At 5 the
  self-compile ran 61+16, with RSS 1.06× instead of 1.36× and less of the gain. The default stays 19.
- **Self-compile RSS rises 36%** (261 → 354 MB): old objects that die wait for a major.
- **HTTP server row not run.** §5's `serve_n` + `wrk` program is not in the repo, and a time-bound
  load generator gives a different allocation count per run.
- **The suite under `SPROUT_GC_MINOR=1 SPROUT_GC_GENCHECK=1` passes:** `just test` exits 0, with
  no failure and conformance 262 of 262. The verifier found no missing barrier.
