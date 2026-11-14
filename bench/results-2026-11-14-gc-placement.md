# Placement replay: holes vs single-class regions, 2026-11-14

`docs/gc-stage2-v0.md` §5.7 options C (holes) and E (single-class regions), compared offline.
`bash bench/gc_place/run.sh <dir>` records each workload with `-DSPROUT_GC_ALLOCTRACE` and
`SPROUT_GC_REGION_LISTS=1` at default GC settings, then replays the trace (`bench/gc_place/replay.c`)
under each policy. Which objects each collection frees does not depend on placement, so every
policy replays the same allocations and frees at the same collections. Not replayed: how a
policy's free count would move the trigger.

Policies: `base` is today's per-region lists; `holes` is C with one forward cursor; `holes-ovf`
adds an overflow cursor for requests over 256 B that miss the current hole (Immix); `class-R` is
E with regions of R bytes. `base` reproduces the runtime's ordinary-region count exactly at every
collection on all nine workloads.

**Memory.** Held = regions × region size + live large bytes, at each collection's start, in MiB.
On the small workloads every 1 MiB policy holds 1–2 regions that are mostly unbumped, which RSS
does not count, so their small-R advantage there is mostly granularity.

| workload | base | holes | holes-ovf | class-16K | class-64K | class-256K | class-1M |
|---|---|---|---|---|---|---|---|
| selfcompile | 217.8 | 214.8 | 187.8 | 200.7 | 208.9 | 255.8 | 446.8 |
| load_spike | 15.0 | 15.0 | 15.0 | 14.2 | 14.3 | 14.5 | 16.0 |
| digit_recognizer | 3.0 | 2.0 | 2.0 | 2.0 | 2.1 | 3.2 | 10.0 |
| nqueens | 2.0 | 1.0 | 1.0 | 0.6 | 1.2 | 4.0 | 15.0 |
| mutvec_int | 2.0 | 2.0 | 2.0 | 1.1 | 1.1 | 1.5 | 3.0 |
| compiler_test | 1.0 | 1.0 | 1.0 | 0.6 | 2.1 | 8.3 | 33.0 |
| gc_roots, http_log, astar | 1.0 | 1.0 | 1.0 | 0.1–0.2 | 0.2–0.4 | 0.8–1.5 | 3.0–6.0 |

Peak regions for class-16K / class-64K: selfcompile 9,415 / 2,371, load_spike 912 / 229, the
rest 9–125 / 4–34. The self-compile uses all 256 classes at its peak.

**Sweep work per collection** (means). Today: slots a young-only minor walks (VM run of
2026-11-13, `rl`). Holes: holes in regions allocated into since the last collection, plus their
bitmap words. Class-R: bitmap words of those regions; nothing per dead slot or per hole.

| workload | today, slots walked | holes-ovf: holes + words | class-16K words | class-64K words |
|---|---|---|---|---|
| selfcompile | 537,239 | 182,542 + 33,035 | 28,211 | 29,449 |
| nqueens | 4,065 | 13 + 1,024 | 472 | 512 |
| http_log_middleware | 4,079 | 17 + 1,024 | 155 | 353 |
| gc_roots | 4,035 | 35 + 1,024 | 128 | 192 |
| mutvec_int | 15,093 | 2 + 1,073 | 355 | 399 |
| load_spike | 18,944 | 619 + 1,428 | 372 | 587 |

**Findings.**

- In these units holes cut sweep work 4–14× and class regions 9–51× on every workload but the
  self-compile, where holes cut it 2.5× and class regions 18–19×. The units differ in cost: a
  slot visit reads a header and writes two words, a hole clears bits over its range and writes
  one header, a bitmap word is one load and store. Holes fragment the self-compile's heap into ~200k holes per
  collection (~230 B each), and plain `holes` skips 1.3 GiB over the run (67 MiB peak per
  interval); the overflow cursor brings that to 379 MiB (9 MiB).
- Per-class retention, the cost feared for E, does not show at 16–64 KiB: the self-compile holds
  0.92–0.96× `base`, load_spike 0.95×. At 256 KiB it is 1.17×, at 1 MiB 2.05×.
- No workload releases an inline tail (`T` records: 0 in all nine traces), so E's lost tail
  split costs nothing here; only `tests/gc_gen/inline_tail_reuse.spr` exercises it.
- load_spike holds ~15 MiB under every policy: neither option releases its post-spike regions.
