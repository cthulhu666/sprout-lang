# Benchmark snapshot — 2026-07-11 (Phase D B2 reach extension: IRCallUnboxed2)

Measures **B2 reach extension** — `op_triggers_gc` now also treats a verified allow-list of
non-allocating `IRCallUnboxed2` variants (`vector_get_unboxed`, `map_get_unboxed`,
`map_nth_key/value_unboxed`, `bytes_get_unboxed`, `str_char_at_unboxed`, `argv_get_unboxed`,
`env_get_unboxed`) as non-triggers. Same M1-class host and methodology as the sibling snapshots.

A/B is same-machine, same session: the compiler built once with the extension and once without
(stash the `ir_rooting` change, `bootstrap-from-seed`), each running the identical native binary of
`bench/unboxed_read/unboxed_read_bench.sprout`.

## Unboxed-read microbenchmark — the witness

A tight sum over a `MutVec Int` (1000 elements × 100 000 passes = 100M reads). Each iteration does a
do-bound `mutvec_get` (CPR-unboxes to `vector_get_unboxed`) with the heap vector live across the read
and no other trigger in the body — i.e. root-bound on the unboxed read.

| | per read | 100M-read wall (warm) |
|---|---|---|
| **Extension ON** | **~6.3 ns** | **~630 ms** (623, 623, 633, 642, 656, 658) |
| Extension OFF (B2 only) | ~8.3 ns | ~825 ms (821, 821, 830, 834, 850; 1002 outlier discarded) |

**Read: ~1.3× faster.** `mutvec_get` CPR-unboxes through `mutvec_get_worker`, which roots the raw
vector across `vector_get_unboxed`. That root fires **every read** (100M×); the extension removes it.
Per read drops from 3 root push/pops to 2 (the remaining two: `sum_go`'s root around the worker
*call*, and the worker's root around `mutvec_raw` — both Sprout `IRCall`s, out of this extension's
reach). Static root count moves only −21 (mostly cold), but the one removed root is on the 100M-hit
hot path — so static count badly undersells the dynamic win.

## Why A*/nqueens/recognizer are flat (no regression, no win)

Same mechanism, but these loops are **not** root-bound on unboxed reads:
- **A\*** — its hot loop calls `mutvec_get_worker` (4 `vector_get_unboxed` sites) but does far more per
  iteration (priority queue, `mutvec_set`, still-boxed `vector_get`); the one read-root is a tiny
  fraction. Static roots 1167 → 1146; wall flat (~305 µs).
- **N-Queens** — cost is persistent `vec_set` copies, outside scope.
- **Recognizer** — its Double kernels use `vector_get_direct` (plain `IRCall`, handled by B2 proper),
  not the unboxed path; flat vs the B2 snapshot (~1.6 s, 139/150).

## The remaining lever (out of scope here)

The bigger per-read root — `sum_go`'s push/pop around the `mutvec_get_worker` **call** — stays,
because the worker is a Sprout function `op_triggers_gc` conservatively treats as a trigger. Removing
it requires **interprocedural non-allocation inference** (recognizing `mutvec_get_worker` /
`mutvec_raw` as non-allocating): the Koka-style analysis tracked in BACKLOG. This extension gets the
worker-internal read-root; inference would get the caller root too.

## Correctness

Full suite green; `just test-stress` (SPROUT_GC_STRESS=1) green including `test_stress_cpr_tier2_worker`
and `test_stress_unboxed_maybe_heap_payload`. IR-shape regressions T20 (`vector_get_unboxed`
non-trigger), T21 (`regex_find_range_unboxed` STAYS a trigger — allocating), T22 (`env_get_unboxed`
non-trigger) in `tests/stdlib/test_ir_rooting.spr`.
