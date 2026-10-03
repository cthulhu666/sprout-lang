# FREE slots a sweep walks, first readings (2026-10-03)

`docs/gc-trigger-v0.md` §9 item 1. A sweep's cost tracks the footprint it walks, not the garbage
it finds, and until `walked=` nothing reported the walk. Every slot walked is one of three
things: live (kept), swept (freed this cycle), or FREE (freed earlier and never refilled). All
three are now on the `SPROUT_DEBUG_GC` cycle line, so

    FREE = walked − live − swept

needs no counter of its own. **FREE/swept is the number that matters**: it is sweep work spent
on neither keeping nor reclaiming anything, and no trigger reads it. walked/swept alone mixes it
with live/swept, which the trigger already tracks — `threshold = live × factor` is built on it.

**Machine:** Apple M3 Pro, macOS 15 (Darwin 24.6.0), `clang -O2`, default GC settings
(`SPROUT_GC_THRESHOLD` unset). Counts are deterministic, so each row is one run. Ratios are
summed over cycles that swept anything.

## Results

| workload | sweeping cycles | live/swept | **FREE/swept** | FREE slots per cycle |
|---|---|---|---|---|
| `gc_roots` (game-tick shape) | 7,041 | 0.02 | **0.00** | 13 |
| `test_gc_age_retain_none` | 118 | 0.00 | **0.00** | 20 |
| `test_gc_age_retain_all` | 2 | 0.36 | **0.00** | 0 |
| math_transcendental | 1 | 0.01 | **0.00** | 0 |
| http_log_middleware | 30,947 | 0.01 | **0.29** | 1,173 |
| compiler (`ast_to_ir.sprout` emit) | 251 | 0.50 | **0.74** | 128,726 |
| nqueens | 8,279 | 0.01 | **1.46** | 5,900 |
| `test_gc_walk_sparse` (constructed) | 85 | 0.35 | **23.81** | 104,649 |
| uncharted-suns `game/app.sprout` | — | — | **not measured** | — |

## What it says

**`gc_roots` confirms §4 directly.** §4 inferred "~1 slot walked per object reclaimed" from
4,019 of 4,096 slots swept; FREE/swept is 0.00. No trigger change can remove waste that is not
there, which is why §13.2's thousand-fold threshold sweep left it flat.

**nqueens contradicts it.** §4 grouped nqueens with `gc_roots` as a dense heap. It steps over
~5,900 FREE slots per cycle to free ~4,000. nqueens copies a `Vec Bool` per placement and
freelists are exact-fit per slot class, so a freed vector of one length cannot hold a copy of
another. `bench/results-2026-09-30-gc-floor.md`'s conclusion stands — a raised floor still cost
nqueens wall and RSS — but "dense" was the wrong word for it.

**The compiler carries the most FREE slots in absolute terms**: ~129k per cycle, three-quarters
of a slot per object freed. It is the workload §5.3 tuned the adapt factor on, so a trigger that
reacted to FREE would move it — which makes it the regression check for any such trigger.

**The constructed fixture shows the mechanism exists; it does not show it is #407's.** ~105k
FREE slots of a dead size class, pinned by 1,562 survivors in 7 regions, cost a sweep that frees
~3,100 per cycle about 450 µs — against ~25 µs for http_log_middleware sweeping ~4,000 on a
dense heap. That is the separation #407 reported, built on purpose. Whether the game's regions
look like this is the open question: #407 estimates "roughly 50,000 slots to reclaim ~3,900"
with 1,714 live, a FREE/swept of about 11. That is an estimate, not a reading; the game row is
the measurement that replaces it.

**For Option B, the gap is the finding.** A trigger keyed on FREE/swept needs ordinary programs
on one side and #407 on the other. Measured, ordinary programs span 0.00–1.46. If the game reads
near its estimate, a bound around 4 separates them with margin both ways; if it reads under 2,
B has nothing to key on and the case for it falls.

## Reproducing

`SPROUT_DEBUG_GC=1 <binary> 2>log`, then over the `[sprout gc] cycle=` lines with `swept > 0`
sum `walked - live - swept` and `swept`. The game row needs a window:

```
SPROUT_DEBUG_GC=1 SPROUT_GFX_MAX_FRAMES=1200 just run-gfx game/app.sprout <catalog>
```

in uncharted-suns, with `SPROUT_ROOT` pointing at a checkout that has the counter.
