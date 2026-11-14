# GC sweep share, 2026-11-13

Where a collection's time goes on the stage-2 default, to decide `docs/gc-stage2-v0.md` §5.7.
The cycle line's `sweep_us=` times the sweep call; the rest of `elapsed_us` is mostly marking.

`just bench-vm-gc 01b012f1 "on:SPROUT_GC_REGION_LISTS=0;rl:SPROUT_GC_REGION_LISTS=1" -n 3 -s none`
on a c4a-standard-2 VM: arms `off` (`SPROUT_GC_MINOR=0`), `on` (the default) and `rl` (per-region
lists), 3 runs each, interleaved. Stdout hashes agreed and every run exited 0. galaxy_client was
dropped (no display); math_transcendental runs no collection.

Per-run means. GC, sweep and the last two columns in ms; `pre-major` is the minor before a major,
which runs the full sweep to clear colour bits (§5.4); each pair is that kind's GC / sweep time.
Cycles with reason `atexit` are left out.

| workload | arm | CPU s | GC | sweep | sweep / GC | sweep / CPU | pre-major | major |
|---|---|---|---|---|---|---|---|---|
| selfcompile | off | 5.74 | 2557 | 1085 | 42% | 19% | – | 2557 / 1074 |
| selfcompile | on | 4.90 | 1541 | 894 | 58% | 18% | 467 / 294 | 307 / 126 |
| selfcompile | rl | 4.83 | 1460 | 813 | 56% | 17% | 190 / 107 | 306 / 125 |
| nqueens | off | 1.79 | 587 | 569 | 97% | 32% | – | 587 / 570 |
| nqueens | on | 1.61 | 353 | 337 | 95% | 21% | 67 / 65 | 31 / 30 |
| nqueens | rl | 1.59 | 340 | 323 | 95% | 20% | 32 / 31 | 30 / 30 |
| http_log_middleware | off | 8.24 | 1953 | 1898 | 97% | 23% | – | 1953 / 1895 |
| http_log_middleware | on | 8.30 | 1663 | 1613 | 97% | 19% | 101 / 99 | 101 / 98 |
| http_log_middleware | rl | 8.36 | 1662 | 1615 | 97% | 19% | 115 / 112 | 114 / 111 |
| load_spike | off | 0.77 | 430 | 415 | 97% | 54% | – | 430 / 417 |
| load_spike | on | 0.86 | 453 | 403 | 89% | 47% | 112 / 107 | 146 / 138 |
| load_spike | rl | 0.84 | 448 | 397 | 89% | 47% | 104 / 99 | 147 / 139 |
| mutvec_int | off | 0.43 | 244 | 189 | 78% | 44% | – | 244 / 190 |
| mutvec_int | on | 0.43 | 222 | 158 | 71% | 37% | 28 / 22 | 19 / 13 |
| mutvec_int | rl | 0.43 | 221 | 159 | 72% | 37% | 18 / 15 | 19 / 14 |
| gc_roots | off | 0.73 | 176 | 168 | 96% | 23% | – | 176 / 169 |
| gc_roots | on | 0.73 | 156 | 152 | 97% | 21% | 9 / 8 | 9 / 8 |
| gc_roots | rl | 0.73 | 157 | 153 | 98% | 21% | 9 / 8 | 9 / 8 |
| digit_recognizer | off | 0.41 | 6 | 5 | 79% | 1% | – | 6 / 5 |
| digit_recognizer | on | 0.41 | 9 | 7 | 86% | 2% | 5 / 5 | 2 / 2 |
| digit_recognizer | rl | 0.41 | 5 | 4 | 79% | 1% | 1 / 1 | 2 / 1 |
| compiler_test | off | 0.28 | 2 | 1 | 78% | 1% | – | 2 / 1 |
| compiler_test | on | 0.29 | 1 | 1 | 81% | 0% | – | – |
| compiler_test | rl | 0.29 | 1 | 1 | 81% | 0% | – | – |

**Findings.**

- The sweep is most of GC time everywhere: 56–98% with minors on. Only the self-compile
  (44%), whose live set is large, and mutvec_int (28–29%) spend more than a quarter of it marking.
- On the GC-heavy workloads it is 17–47% of CPU.
- A young-only minor's sweep is 60–98% of that minor's time: a minor marks the survivors, few,
  and sweeps the young set, all of it.
- With `rl`, every major on load_spike is preceded by a minor running a full sweep (231 of
  each over 3 runs), each walking ~354k slots, so the post-spike free slots are walked twice per major.
- Locally (macOS, nqueens, `sample`), `sprout_gc_free_dead_slot` and `slot_bytes` are out-of-line
  calls per dead slot. Forcing both inline cut the median sweep time ~15% and moved CPU less than
  the run-to-run spread.

**Caveat.** `ab.sh` runs with `SPROUT_DEBUG_GC=1`, so a full sweep also updates the `types:`
census per survivor (`sprout_gc_census_add`); the young sweep does it only for age-0 survivors.
Full-sweep times include that.
