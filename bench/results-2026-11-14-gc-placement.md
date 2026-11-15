# Placement replay: holes vs single-class regions, 2026-11-14

`docs/gc-stage2-v0.md` §5.7 options C (holes) and E (single-class regions), compared offline.
`bash bench/gc_place/run.sh <dir>` records each workload with `-DSPROUT_GC_ALLOCTRACE`,
`SPROUT_GC_REGION_LISTS=1` at default GC settings, then replays the trace
(`bench/gc_place/replay.c`) under each policy. Which objects a collection frees does not depend on
placement, so every policy replays the same allocations and frees at the same collections.

Policies: `base` is today's per-region lists; `holes` is C with one forward cursor; `holes-ovf`
adds an overflow cursor for requests over 256 B that miss the current hole (Immix);
`holes-ovf-tails` also reuses a freed inline tail at once from a list by exact class, dropped at
each collection; `class-R` is E
with regions of R bytes, also accounted as carved from the runtime's 1 MiB arena chunks. C is
costed in its leanest form: walks use the bitmaps, so a sweep updates bitmap words and writes no
header per hole.

**Calibration.** `base` matches the trace at every collection of all 14 workloads: ordinary
regions, large regions and free slots, zero mismatches (`run.sh` fails a workload otherwise).

**Workloads.** The nine of the GC A/B set, plus three that target each option's risk:
`rotate_classes` (`bench/gc_trigger/`; slot classes take turns, so exact fit retains), its
`mixed` and capped (`cap` = 100) variants, `bench/gc_place/mutvec_push.sprout` (150,000
`mutvec_new` + push, each releasing an inline tail), and `tests/gc_gen/inline_tail_reuse.spr`.
No workload of the A/B set releases an inline tail.

**Memory**, peak held at a collection's start in MiB: regions × size + live large objects
(chunked: 1 MiB chunks × 1 MiB). On workloads that fit in 1–2 regions every 1 MiB policy holds
mostly unbumped space, which RSS does not count; those rows are left out.

| workload | base | holes | holes-ovf | holes-ovf-tails | class-16K (chunked) | class-64K (chunked) | class-1M |
|---|---|---|---|---|---|---|---|
| selfcompile | 217.8 | 214.8 | 187.8 | 187.8 | 200.7 (202.8) | 208.9 (209.8) | 446.8 |
| rotate_classes | 37.0 | 15.0 | 15.0 | 15.0 | 18.0 (19.0) | 18.9 (19.0) | 21.0 |
| load_spike | 15.0 | 15.0 | 15.0 | 15.0 | 14.2 (15.0) | 14.3 (15.0) | 16.0 |
| mutvec_push | 14.0 | 33.0 | 33.0 | 5.0 | 36.1 (37.0) | 39.2 (40.0) | 202.0 |

Per-region metadata is 0.4–2.3% of held on the self-compile in every policy.

**Sweep work**, mean per collection over all kinds: words of one bitmap (both C and E update
four per region), and regions swept. Full sweeps (majors and the minor before one) count every
region, young-only minors every region holding a young object or allocated into since the last
collection, as `sprout_gc_sweep_young` does. Today: slots
walked, from the same run's `walked=`.

| workload | today, slots | holes-ovf-tails words / regions | class-16K words / regions | class-64K words / regions |
|---|---|---|---|---|
| selfcompile | 660,103 | 36,056 / 35 | 15,694 / 2,016 | 15,664 / 530 |
| load_spike | 58,476 | 2,524 | 598 | 939 |
| rotate_classes | 24,286 | 1,853 | 242 | 280 |
| mutvec_push | 12,984 | 2,239 | 469 | 478 |
| nqueens | 4,676 | 1,024 | 87 | 105 |

The replay keeps a full sweep for the minor before a major; the design in §5.7 sweeps that minor
young-only, so these overstate both options there.

**Allocation side**, self-compile, per allocation: `holes-ovf` examines 0.49 holes and reads
0.57 occupancy words; `class-16K` tests 1.00 regions and reads 1.03 bitmap words; `base`'s
cursor tests 0.93 regions.

**Trigger bias.** Collection points are the trace's. The footprint floor reads the free count,
so a policy with more free space would delay a major. Recomputing each floor with the policy's
own free measure: `base` and `class-16K` move no collection on any workload; `class-64K` moves 7
of 29 on the self-compile (≤ 1,468 allocations). Holes, with free granules normalised by the mean
live slot, move 14 of 29 on the self-compile (mean 23,619 allocations against a mean interval of
420,546), 237 of 650 on rotate_classes and all 5 on mutvec_push. So C's memory is understated a
little; in raw granules it would be understated much more, and C needs the normalised count.

**Findings.**

- Against today's slots walked, holes update 4.6–23× fewer words and class regions 28–100×
  fewer (units differ: a slot visit reads a header and writes two words; a word is one load and
  store). On the self-compile class regions update 2.3× fewer words than holes but sweep 15–58×
  more regions per collection.
- Memory: holes-ovf is lowest on the self-compile (0.86× base, against 0.93–0.96× for class
  regions chunked). On rotate_classes both beat today's exact fit (holes 0.41×, class 0.49–0.51×).
- Inline tails: mutvec_push holds 2.4× base under holes and 2.6–2.9× under class regions, since
  today reuses a freed tail at once. With the tail lists holes hold 0.36× (111,352 tail reuses,
  0.37 per allocation); class regions cannot split a tail, since the vector cannot move. With no
  tails (the self-compile) `holes-ovf-tails` equals `holes-ovf` exactly.
- 256 KiB and 1 MiB class regions retain: 1.17× and 2.05× on the self-compile.
