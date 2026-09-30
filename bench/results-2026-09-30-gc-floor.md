# What a raised GC floor costs the floor-pinned four (2026-09-30)

`docs/gc-trigger-v0.md` §9 item 2. §4 of that doc predicts that nqueens, http ×2 and
math — the four workloads `gc-generational-v0.md` §5.3 called *"unchanged by
construction"* under an adapt-**factor** change — are flat-to-worse under a raised
**floor**, because a floor change has the inverse blast radius and moves precisely them.

This is the measurement that gates Option A (a raised compiled-in default). It answers
what the floor costs; it does not decide whether to pay it.

**Predictions were written down before the first run** and are reproduced in §4 below,
including the one that was wrong.

**Machine:** Apple M3 Pro, macOS 15 (Darwin 24.6.0), `clang -O2`, whole-program linked
via `just compile-native`. One binary per workload; only `SPROUT_GC_THRESHOLD` differs
between arms, and it writes both `g_gc_threshold` and `g_gc_threshold_base`, so it
stands in exactly for a raised compiled-in default with no source edit.

Default floor 4,096 against the §6.5 candidate 100,000 (×24.4).

## 1. Results

Wall and RSS from `/usr/bin/time -l`, **without** `SPROUT_DEBUG_GC` — its per-cycle
stderr line contaminates the wall time it is meant to explain, and at 30,947 cycles
that is not a rounding error. Cycle and pause columns come from a separate pass with
it on (`bench/gc_pause/pause_stats.py --reps 3`).

Wall is the **minimum** over 11 interleaved reps (load can only add time); RSS the
maximum.

| workload | wall @4,096 | wall @100,000 | peak RSS | cycles | p50 pause |
|---|---|---|---|---|---|
| nqueens             | 1.320 s | **1.390 s (+5.3%)** | 5.4 → **17.4 MB (3.2×)** | 8,279 → 335 | 38 → **818 µs** |
| http_log_middleware | 4.120 s | 4.140 s (+0.5%)     | 4.4 → **8.5 MB (1.9×)**  | 30,947 → 1,261 | 25 → **509 µs** |
| math_transcendental | 0.160 s | 0.160 s (flat)      | 3.7 → 3.8 MB (1.02×)     | 1 → 1 | 13 → 4 µs |
| spawn_server (wrk)  | see §3  | see §3              | 31.3 → 35.4 MB (median)  | — | — |

**§4's prediction holds on all four. Nothing improved beyond noise.** The cost is
almost entirely footprint, and it is not uniform: nqueens pays 3.2× peak RSS *and* 5.3%
wall for a change that buys it nothing.

## 2. Why nqueens gets slower while its collector gets cheaper

Total GC time actually **falls** at the raised floor: 8,279 × 38 µs = 315 ms becomes
335 × 818 µs = 274 ms. The collector did less work and the program still took 5.3%
longer.

That is `gc-trigger-v0.md` §4's cache mechanism, observed directly rather than inferred
from a ns-per-slot trend: peak RSS goes 5.4 → 17.4 MB and the **mutator** pays for a
working set that no longer fits. http_log_middleware shows the same saving on the GC
side (774 → 642 ms) against a heap that only reaches 8.5 MB, stays in cache, and comes
out flat rather than worse.

**So the variable separating the two is not "dense heap". It is whether the raised heap
outgrows cache** — a threshold in *bytes*, which the trigger cannot see because it
counts objects. This is an argument for Option C (a byte-aware trigger) that is
independent of the work-per-garbage argument for Option B.

**The pause column is the other half, and it is the half a frame budget reads.** p50
per-collection pause rises ~20× on both non-trivial workloads: 38 → 818 µs and
25 → 509 µs. Total GC time is down, per-collection jitter is up 20×. For #407's own
constituency — a game holding a frame budget — that trade needs stating, not assuming:
818 µs is 5% of a 16.7 ms frame, so it is affordable here, but it is affordable by a
factor of 20, not by orders of magnitude.

## 3. The socket server does not resolve at this precision

`bench/http_worker_pool/spawn_server.sprout` under `wrk -t2 -c2 -d1s`, 8 interleaved
rounds with the harness's TIME_WAIT drain barrier:

| floor | rps median | rps min–max | rss median | rss min–max |
|---|---|---|---|---|
| 4,096   | 15,959 | 9,763–18,355 | 31.3 MB | 19.5–32.5 MB |
| 100,000 | 16,022 | 9,963–17,885 | 35.4 MB | 34.2–36.3 MB |

The medians differ by **0.4%** inside a within-arm spread of ±30%. That supports
"no large effect" and nothing finer; `bench/http_worker_pool/bench.sh`'s own header
explains why (at these rates the client's ephemeral-port recycling, not the server,
sets the tail). The RSS side is `ps -o rss=` sampled while the server runs, so it is a
late reading rather than a peak — but the 100,000 arm is notably tighter (34.2–36.3 vs
19.5–32.5), which is what a floor that pins the heap from below should look like.

Treat this row as a **negative result with a wide error bar**, not as a measurement of
+13% RSS.

## 4. Predictions, including the wrong one

Written before any run, from `gc-generational-v0.md` §5.1's sweep at ×8 (32,768) and
×64 (262,144), log-interpolated to ×24.4:

| workload | predicted wall | actual | predicted RSS | actual |
|---|---|---|---|---|
| nqueens             | +1.6% | **+5.3%** | ~50 MB (8×) | **17.4 MB (3.2×)** |
| http_log_middleware | +1.4% | +0.5% | ~20 MB (5×) | 8.5 MB (1.9×) |
| math_transcendental | identical | identical | identical | identical |

**The RSS interpolation was wrong by 3×, in the conservative direction.** §5.1 measured
nqueens at 17 MB for a 32,768 floor; this run measures 17.4 MB at 100,000. RSS did not
grow at all across that range, so treating it as log-linear in the threshold — which is
what produced the 50 MB figure — is not how it behaves. A floor sets a *minimum* heap;
what the process actually peaks at is set by the region high-water mark, and on nqueens
that had already saturated by 32,768.

**One falsification criterion fired and then retracted, which is the reason to record
rep counts.** At 5 reps http_log_middleware read **−5.0%** — past the stated 3% line for
falsifying §4. At 11 reps it reads +0.5%. The distribution is wide (4.12–5.02 s) and a
minimum over 5 draws from it is not stable. The finding is that §4 survives; the lesson
is that the min-of-reps summary `pause_stats.py` uses converges much faster for *pause*
than it does for whole-program wall time.

**A bias in the harness was caught by a prediction, not by the data.** The first run
timed both arms in a fixed order within each rep, which hands every cold-start cost to
whichever floor goes first; it read as a 50% "win" on math_transcendental, a workload
that collects once and marks zero objects. An interleave only cancels drift if the
*order* alternates. Fixed with a discarded warm-up per arm plus order flipped on
alternate reps.

## 5. What this does and does not settle

**Settled:** a 100,000 floor is not free. Its price is up to 3.2× peak RSS and ~5% wall
on nqueens, ~1.9× RSS at no wall cost on http_log_middleware, nothing on math, and a
~20× rise in per-collection pause wherever the workload was floor-pinned.

**Not settled:** whether that price is worth paying is §6.5's open call, and this
measurement is deliberately silent on it.

**Newly raised:** the cache-crossing explanation in §2 gives Option C (bytes) a
justification that does not route through work-per-garbage. `gc-trigger-v0.md` §9
item 1 — the work-per-garbage counter — is still the experiment that decides between
Option B and nothing, and it is untouched by any of this.
