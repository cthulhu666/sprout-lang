# Option B on a prototype: the per-class repair diverges, a damped floor holds (2026-10-04)

`docs/gc-trigger-v0.md` §12 Q1. The per-class probe (`bench/results-2026-10-03-gc-walk.md`) read
what §6.2's repaired floor *would* be from one cycle's state. A trigger is a feedback loop — its
floor decides when the next collection runs, which decides the free pool the next floor reads —
so only running the loop shows whether it settles. This runs it.

**Machine:** Apple M3 Pro, macOS 15 (Darwin 24.6.0), `clang -O2`, separate-unit link (not
`just compile-native`). Programs compiled by master `e6553023`'s stage-1 compiler, and the
compiler workload is that commit's seed.

## What was run

A throwaway copy of `runtime/sprout_runtime.c`, never committed, with one switch,
`SPROUT_GC_FLOOR`. Both arms add a floor after the sweep, beside the existing
`max(live × factor, base)` and under the cap:

- **per-class** (§6.2's repair): `live + Σ free_c` over classes with at least one allocation since
  the previous collection.
- **damped**: `live + (live + free) / k`, `k = 3` (`SPROUT_GC_FLOOR_K`; 2 for reference).

`free` is the freelist length after the sweep: slots in surviving regions only, this cycle's
freed slots included. It is counted in `fl_push_staged` and undone in `fl_region_rollback`.

Two constructed fixtures, both `tests/stdlib/test_gc_walk_sparse.spr` with 1,000,000 churn rounds
instead of 34,000 and one Link kept forever per 2,000 rounds, so the fresh regions the churn bumps
into are pinned:

- **adversary**: one short-lived Wide per 1,000 rounds, so the class holding the 100,000 dead
  Wide slots has demand in every cycle while the garbage is Links.
- **pin-only**: the same without the Wide trickle.

**The pins are what make it an adversary.** Without them per-class settles (heap 161,280): the
churn's garbage fills fresh regions that die whole, and Pass 2 releases them.

## Results

**Adversary** — free slots after each sweep:

| arm | cycles | free pool | regions | peak RSS |
|---|---|---|---|---|
| off | 2,218 | — | 7 | 10.4 MB |
| per-class | 17 | 109,028 → 209,002 → 308,970 → … → **1,307,554** | 44 | 47.3 MB |
| damped k=3 | 162 | 109,028 → 136,858 → 146,130 → … → 151,018 | 8 | 11.8 MB |
| damped k=2 | 84 | 109,028 → … → 202,018 | 10 | 13.7 MB |

Per-class grows the pool by ~100,000 — the dead Wide pool — every cycle, and stops only because
the program ends. On pin-only it does not (78 cycles, 13.5 MB): the ratchet needs the trickle.

Damped settles where the arithmetic says. With `U` slots no allocation can reuse and `L` live, a
cycle allocates `(L + U + A) / k` into a reusable pool `A`, so `A` grows to the fixed point
`(L + U) / (k − 1)`. At `U` = 100,000 and `L` ≈ 2,060 that predicts 151,031 at k=3 and 202,066
at k=2; measured 151,018 and 202,018.

**Ordinary workloads**, one run each:

| workload | arm | cycles | max heap | peak RSS |
|---|---|---|---|---|
| `gc_roots` | off / per-class / damped | 7,041 / 6,924 / **7,041** | 4,096 / 4,161 / 4,096 | 4.2–4.5 MB |
| nqueens | off / per-class / damped | 8,279 / 5,212 / **8,279** | 4,096 / 12,710 / 4,096 | 5.8 / 6.8 / 5.7 MB |
| http_log_middleware | off / per-class / damped | 31,843 / 19,814 / 31,450 | 4,096 / 6,578 / 4,096 | 4.4–4.7 MB |
| compiler (`ast_to_ir.sprout` emit) | off / per-class / damped | 252 / 92 / 224 | 1.20M / **4.70M** / 1.20M | 98.4 / **221.0** / 102.6 MB |

Damped leaves `gc_roots` and nqueens identical cycle for cycle; its floor stays under theirs.
At k=2 it moves nqueens (5,637 cycles) and the compiler (195, 109.1 MB), which is why k=3.

**The game** (#407's scene, 1,200 frames, muted), from cycle 16 — past the catalog load — two
runs per arm, interleaved:

| arm | cycles | GC µs per 1k allocations | mean pause | mean threshold | regions |
|---|---|---|---|---|---|
| off | 1,213 / 4,255 | 220 / 169 | 807 µs | 5,497 / 7,146 | 8 / 9 |
| per-class | 20 / 10 | 7.2 / 8.7 | 2,570 / 2,521 µs | 366,793 / 321,269 | 13 / 11 |
| damped k=3 | 170 / 58 | 13.8 / 14.6 | 1,052 / 1,110 µs | 79,427 / 79,413 | 8 / 8 |

Damped cuts the game's GC cost per allocation 12–15× with no added regions. FREE/swept settles at
1.97–1.99, which is `k − 1`. Pinning the threshold at 232,000 read 7.2–9.6 µs
(`bench/results-2026-10-03-gc-walk.md`): damped's GC costs 1.5–2× the pinned figure, at a third
of its threshold.

Per-class's mean threshold of 321k–367k is not the ~248,500 the probe predicted from one cycle.
Its free pool is not split across classes in the proportions the game allocates in, and every
class short of slots bumps new ones that join its pool.

## Caveats

- `elapsed_us` varies ~20% between runs with identical cycle counts (`gc_roots`: 377, 351 and
  309 ms over three arms that all ran 7,041 cycles), so the ordinary rows compare cycles and heap, not time. The game's 12–15× is far
  outside that.
- One run per ordinary workload. RSS is from `/usr/bin/time -l` with `SPROUT_DEBUG_GC` on. The
  game's regions come from the GC log: `time` on `just` reports the largest descendant, here the
  compiler (142 regions against the game's 8).
- The game's allocation volume varies up to 7× between runs (2.9M–20.3M in the window), which is
  why it is compared per allocation.
- `http_log_middleware` exits on `Int overflow in +` in `wall_loop` in every arm, which sums
  `time.wall_micros()` (~1.76e15) across iterations. That is a bench bug, not a GC one. Its rows
  cover the run up to there, and the arms' allocation counts differ by 1.3% for a reason not
  established.
- The prototype is not the implementation: the real build is measured again before it lands.

## Reproducing

Patch the two floors into a copy of the runtime as described above, then build each program
against it and run it with `SPROUT_DEBUG_GC=1 SPROUT_GC_FLOOR=<off|class|damped>`. For the game,
point `SPROUT_ROOT` at a tree holding that runtime, using the `mise exec -- env SPROUT_ROOT=…` form
from `bench/results-2026-10-03-gc-walk.md`.
