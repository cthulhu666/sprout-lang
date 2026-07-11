# Benchmark snapshot — 2026-07-11 (alloc fixpoint: interprocedural GC-root elision)

Measures the **per-function may-trigger-GC fixpoint** — the interprocedural extension of B2. Instead
of a hand-list of non-allocating externs, the rooting pass now computes, over the whole call graph, a
per-function summary of whether each Sprout function *may* trigger GC, and elides roots across calls
to functions **proven** non-allocating. This reaches the dominant idiomatic-code cost the two leaf
PRs (#162/#163) could not: the root around a *wrapper/worker call* (e.g. `mutvec_get_worker`), not
just the leaf read inside it.

**Design (Option A):** the active codegen path is streaming (one function live at a time, never the
whole `IRProgram`), so the summary is built by a pre-pass that re-runs translation with fresh Refs,
extracts each function's compact `(local-trigger, callee-set)` via the *exact* `op_triggers_gc`
oracle, and discards the IR. `ir_rooting.summary_from_raw` resolves + least-fixpoints it. Conservative
default: any callee not *proven* non-allocating (unknown/external, indirect closure call, recursion
mid-fixpoint) stays a trigger.

Same M1-class host and warm-sampling methodology as the sibling snapshots.

## Runtime — idiomatic code now benefits

| | Baseline | Alloc fixpoint | |
|---|---|---|---|
| **N-Queens** (N=12) | ~505 ms | **~310 ms** | **~1.6×** (305, 320, 322 warm) |
| **A\*** (100 runs) | ~305 µs | **~275 µs** | ~1.1× (263–297 warm) |
| **Digit recognizer** | ~1.6 s (B2) | **~1.30 s** | ~1.2×, accuracy 139/150 unchanged |
| **Unboxed-read witness** (`bench/unboxed_read/`) | 825 ms (B2-only) / 630 ms (#163) | **~270 ms** | **~3×** over B2-only (~2.7 ns/read) |

**Read:** the fixpoint collapses the whole read chain. For `mutvec_get`: `mutvec_raw` (field extract)
and `vector_get_unboxed` (leaf) are both proven non-allocating ⟹ `mutvec_get_worker` is proven
non-allocating ⟹ the caller's per-read root around the worker *call* vanishes. nqueens (worker-mediated
`vec_get`/`vec_set` reads) is the biggest winner; A* is read-light relative to its priority-queue work
so gains less; the recognizer's Double kernels were already B2-handled, so its extra gain is on the
scalar `mutvec_get` reads.

## Compiler self-compile — FASTER, despite the double-translation pre-pass

`compile_driver.sprout --emit-ir` (whole-compiler bundle):

| | wall | peak RSS |
|---|---|---|
| **Baseline** | ~19.8 s | ~298 MB |
| **Alloc fixpoint** | **~13.1 s** | **~279 MB** |

**~34% faster, slightly less memory** — **on the self-compile, which is the most favorable case.** The
compiler's own source is unusually dense with `Dict`/`List`/`Map` accessor calls through non-allocating
workers; proving them non-allocating strips a large volume of `IRRoot`/`IRUnroot` ops from the emitted
IR, so the rooting pass and text lowering both do far less work, more than repaying the second
translation. (Total `push_i64_root` in the self-compile IR: 46 639.) **This does not generalize:** a
root-*light* program pays the pre-pass's second translation with little compensating root reduction and
could compile net-slower. The pre-pass is a compile-time optimization tuned for accessor-heavy code;
for the runtime wins (above) it is unambiguously positive.

## Correctness

Full suite green; `just test-stress` (SPROUT_GC_STRESS=1) green — including the compiler self-compiling
with its own worker-reads root-elided (a mis-elided root would UAF here). compile-examples, smoke-shapes,
bundle-smoke, run-example-canary, verify-bootstrap-fixed-point all green. Seed reconverged (global
change). IR-shape regressions T23 (call to non-allocating Sprout fn is a non-trigger), T24 (allocating
Sprout fn STILL roots — over-approximation lock), T25 (self-recursive non-allocating fn stays a
non-trigger — recursion convergence) in `tests/stdlib/test_ir_rooting.spr`; `test_ir_tuple_result_rooting`
updated to use a genuinely-allocating intervening callee (an identity callee is now correctly a
non-trigger).
