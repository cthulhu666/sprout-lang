# Optimisation-pass A/B baseline (2026-09-11)

First run of `bench/optpasses/bench.sh` (`just bench-opt [pass]`), the M0 harness from
`docs/opt-passes-v0.md`. One compiler build; each program compiled twice, with every pass
ON and with the pass under test disabled via `SPROUT_OPT_OFF`.

**Machine:** Apple M3 Pro, macOS 15 (Darwin 24.6.0), Apple clang 17, `-O2` both sides.
`RUNS=3`, minimum reported (bench/results-2026-08-06 §"On using the minimum").

## Results — pass `dle` (dead-let elimination)

| program | nodes | removed | IR·on | IR·off | cc·on s | cc·off s | run·on s | run·off s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `astar` | 5 811 | 0 | 1 316 | 1 316 | 0.26 | 0.25 | 0.02 | 0.02 |
| `nqueens` | 4 391 | 0 | 775 | 775 | 0.21 | 0.22 | 2.24 | 2.26 |
| `math_transcendental` | 6 503 | 0 | 3 021 | 3 021 | 0.27 | 0.28 | 0.16 | 0.16 |
| `unboxed_read` | 5 308 | 0 | 384 | 384 | 0.23 | 0.23 | 0.22 | 0.22 |
| `digit_recognizer` | 7 721 | 0 | 3 561 | 3 561 | 0.33 | 0.34 | 0.51 | 0.52 |
| `compile_driver` | 110 054 | 0 | 380 592 | 380 592 | 13.80 | 13.84 | — | — |

`compile_driver` is the compiler emitting its own IR — the largest real Sprout program
there is. It has no run column: the harness times `--emit-ir`, which is the row's point.

## The finding: DLE removes nothing from real code

**Zero nodes, on every program, including the 110 054-node compiler.** ON and OFF emit
byte-identical IR everywhere in the corpus, so the two halves of the table agree by
construction rather than by measurement.

This is not a broken harness. `tests/opt_harness/dead_let.spr` — a deliberately wasteful
shape — reports `removed=6` and emits different IR with the switch flipped, and
`just opt-harness-check` asserts exactly that on every CI run. The pass works; it has
nothing to find. People do not write bindings they never read, and the earlier passes do
not manufacture any.

Two consequences:

1. `docs/opt-passes-v0.md` §M0 predicted that "`SPROUT_OPT_OFF=dle` must produce a
   measurably different binary on day one". On the corpus it does not. The self-validation
   it was meant to supply now comes from the fixture and the gate instead.
2. **M1 (CSE) is measured against a true zero.** Any node the first real pass removes is
   the first node this pipeline has ever removed from a real program, so the baseline needs
   no subtraction and no argument about attribution.

DLE also costs nothing: `compile_driver` compiles in 13.80 s with the pass and 13.84 s
without, i.e. the difference is below the noise floor of a single run. There is no case for
deleting it — it is free insurance against a future pass that *does* manufacture dead
bindings, which is precisely what a worker/wrapper LICM split (M2) would do.

## What this does not cover

`dce.elim_unreachable` — the other half of `dce.sprout`, which drops whole unreachable
declarations and is the reason a six-line program does not carry the entire prelude — runs
inside `ir_pipeline` and is **not** under the switch. It is the pass in this compiler known
to do large work, so putting it there would give the harness a non-zero A/B on real
programs. Filed in `BACKLOG.md`.
