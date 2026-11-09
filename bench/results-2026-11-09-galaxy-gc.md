# Galaxy client GC, re-measured on the stage-2 default (2026-11-09)

Re-measures [gc-generational-v0.md](../docs/gc-generational-v0.md) §13.6 on master `832f5482`
(GC stage 2 landed, K = 3, s = 1) with uncharted-suns `df276ef7`. `bench/gc_minor/ab.sh -n 5 -s
default -w galaxy_client`: `game/app.sprout` on the catalog, `--system=232`, 1,200 frames, on a
display, minors on (the default) against `SPROUT_GC_MINOR=0`, interleaved. Stdout matched on all
10 runs and every run exited 0. Load average 9–15.

## Whole run

Median over 5 runs (min–max). GC per frame is the run's GC total over 1,200 frames, load included.

| arm | GC total ms | GC per frame µs | pause p50 µs | pause p90 µs | RSS MB | collections |
|---|---|---|---|---|---|---|
| minors off | 164 (158–164) | 137 | 1,030 (1,016–1,087) | 1,393 (1,314–1,514) | 1,437 | 142 |
| minors on | 170 (168–173) | 142 | 31 (30–32) | 652 (598–669) | 1,377 | 993 (939 + 54) |

The longest pauses fall while the world loads, collections 9–14, when the heap peaks near 185k
objects and then drops: 4.1–12.0 ms, one or two per run in both arms.

## Steady state

Collections after the 20th, all 5 runs pooled.

| arm, kind | n | p50 µs | p90 µs | max µs | mean live |
|---|---|---|---|---|---|
| minors off | 603 | 1,037 | 1,387 | 2,178 | 1,535 |
| minors on, minor | 4,615 | 30 | 512 | 1,443 | 2,640 |
| minors on, major | 245 | 1,057 | 1,485 | 1,945 | 1,532 |

§13.6's premise no longer holds: the steady live set is about 1,550 objects, not 77,653. The
type census counts `map=2` where §13.6 counted 75,640 (uncharted-suns #407 shrank the map).

A full collection still costs about 1 ms, because it walks every slot the load peak left:
264,858 slots in 9 regions for 1,577 live (cycle 140, minors off). The live objects are spread
over all 9 regions, and the sweep returns a region only when it holds none. Minors skip that
walk; each run has 49 majors in its 1,200 frames.

Against §13.6: p50 pause 7,746 µs → 31 µs, and GC per frame about 142 µs on a 17.1 ms frame.
