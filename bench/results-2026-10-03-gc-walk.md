# FREE slots a sweep walks, first readings (2026-10-03)

`docs/gc-trigger-v0.md` §9 item 1. A sweep's cost tracks the footprint it walks, not the garbage
it finds, and until `walked=` nothing reported the walk. Every slot walked is one of three
things: live (kept), swept (freed this cycle), or FREE (already free, and not handed out since). All
three are now on the `SPROUT_DEBUG_GC` cycle line, so

    FREE = walked − live − swept

needs no counter of its own — with `SPROUT_GC_LINEAGE` off, as in every row below. Lineage keeps
dead OBJs as POISON corpses that are walked every cycle after the one they die in, and the formula
counts them as FREE. **FREE/swept is the number that matters**: it is sweep work spent
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
| `test_gc_walk_sparse` (constructed), steady state | 83 | 0.50 | **33.81** | 105,894 |
| uncharted-suns `game/app.sprout`, steady state | 513 | 0.50 | **76.1** | 246,450 |
| — same, `SPROUT_GC_THRESHOLD=232000` | 43 | 0.01 | **0.25** | 57,178 |

Rows marked steady state leave out a one-off phase. The fixture's is the cycle where all 100,000
Wides die (swept 109,027, FREE 0); with it, the row reads 23.81. The game rows are #407's scene
(one host, 1,200 frames, audio muted), leaving out the first quarter of cycles — the catalog
load, peak live 158,637. Within a run the other three quarters agree to three figures.

**Across runs the game is not repeatable**, and the table shows one run of each. Four default runs
allocated 3.4M–14.2M objects in the same 1,200 frames and collected 685–3,153 times; steady-state
FREE/swept read 76.1, 69.7, 72.3 and 52.2, with FREE steady at 246k–253k and live varying
1,617–2,381. Three pinned runs collected 14–67 times. Per-frame GC time therefore compares runs,
not configurations; per 1,000 allocations it is 161–217 µs at the default and 7.2–9.6 at 232,000.
Why the allocation volume moves is not established.

## What it says

**`gc_roots` confirms §4 directly.** §4 inferred "~1 slot walked per object reclaimed" from
4,019 of 4,096 slots swept; FREE/swept is 0.00. No trigger change can remove waste that is not
there, which is why `gc-generational-v0.md` §13.2's thousand-fold threshold sweep left it flat.

**nqueens contradicts it.** §4 grouped nqueens with `gc_roots` as a dense heap. It steps over
~5,900 FREE slots per cycle to free ~4,000. nqueens copies a `Vec Bool` per placement and
freelists are exact-fit per slot class, so a freed vector of one length cannot hold a copy of
another. `bench/results-2026-09-30-gc-floor.md`'s conclusion stands — a raised floor still cost
nqueens wall and RSS — but "dense" was the wrong word for it.

**Of the ordinary programs, the compiler carries the most FREE slots**: ~129k per cycle,
three-quarters of a slot per object freed. It is the workload `gc-generational-v0.md` §5.3 tuned
the adapt factor on, so a trigger that
reacted to FREE would move it — which makes it the regression check for any such trigger.

**The constructed fixture and the game reach the same walk by different routes.** The fixture
holds ~105k FREE slots of a dead size class under 1,562 survivors in 7 regions, and a sweep
freeing ~3,100 costs ~450 µs, against ~25 µs for http_log_middleware sweeping ~4,000. The game
holds ~246k FREE slots under ~1,600–2,400 survivors in 8–9 regions, and frees ~3,200–4,800 for
~800–1,000 µs. #407 estimated "roughly 50,000 slots to reclaim ~3,900", a FREE/swept of about 11;
the walk is 5× that footprint, and FREE/swept 5–7× the estimate.

**The game's free slots are not a dead class.** A throwaway probe — per-class counters in
`sprout_gc_alloc_block` and the sweep, printed beside each cycle line and never committed — split
FREE by size class on the 1,297-cycle run, over its last three quarters:

| class | FREE per cycle | allocations per cycle |
|---|---|---|
| 16 B | 109,351 | 723 |
| 32 B | 106,544 | 2,616 |
| 48, 80, 96, 112, 128 B | 30,815 | 3–88 each |
| other classes with allocations | 37 | — |
| classes with no allocations | 82 | 0 |

246,747 of 246,829 FREE slots are in classes the game allocates from every cycle: a usable
surplus, not an unreusable one, and every collection walks it ~3,500 allocations after the last.
Run on `test_gc_walk_sparse` first, the probe put its 100,000-slot pool in the one class with no
allocations and summed to the cycle line's `walked − live − swept` exactly.

**For Option B, the gap is the finding.** Ordinary programs span 0.00–1.46 and the game reads
52–76, so any bound between ~2 and ~50 separates them. Raising the game's threshold drops it to
0.25: the signal switches off once the floor is high enough, which is what a feedback loop needs
to settle. B's per-class repair counts only free slots in classes with recent demand; on the
split above that is all but 82 of them, which read as a floor near 248,500. **Run as a loop it
does not stay there**: it averaged 321k–367k on the game and grows without bound on a constructed
adversary (`bench/results-2026-10-04-gc-trigger-b.md`), which is why a damped floor replaced it.

## Reproducing

`SPROUT_DEBUG_GC=1 <binary> 2>log`, then over the `[sprout gc] cycle=` lines with `swept > 0`
sum `walked - live - swept` and `swept`. The game rows need a window:

```
SPROUT_AUDIO_MUTE=1 SPROUT_DEBUG_GC=1 SPROUT_GFX_MAX_FRAMES=1200 \
  just run-gfx game/app.sprout <catalog>
```

in uncharted-suns, with `SPROUT_ROOT` pointing at a checkout that has the counter. Check the log
has `walked=` on every cycle line: an environment that sets `SPROUT_ROOT` itself overrides an
exported one, and the build then silently uses another runtime. The log holds two processes —
the compiler, then the game — split where `cycle=` resets to 1.
