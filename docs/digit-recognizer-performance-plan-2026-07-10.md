# Digit Recognizer Performance Plan

Status: plan
Date: 2026-07-10
Scope: `stdlib.mutable`, `examples/digit_recognizer`, later numeric representation work

## Problem

After CPR Tier-2, the digit recognizer improved from about 23.05s to about 2.91s, with
unchanged accuracy. The old per-access `Maybe` allocation cliff is gone, but profiling still
shows the remaining hot path in dense mutable vector/matrix access inside `forward` and
`backprop`.

Current bottlenecks are mostly:

- mutable vector/matrix get/set worker overhead,
- GC root push/pop around container access,
- closure/callback overhead from iteration combinators,
- boxed numeric/object allocation,
- GC as a secondary cost, not the dominant cost.

## Correct Implementation Order

### 1. Land mutable combinators first

Implement the safe stdlib API proposed in `docs/mutable-element-combinators-v1.md`:

- `mutvec_len`
- `mutvec_each`
- `mutvec_fold`
- `mutvec_zip_fold`
- `mutvec_map_inplace`
- `mutmatrix_fold`
- `mutmatrix_fill`
- `mutmatrix_row_zip_fold`
- `mutmatrix_row_zip_update`

Reason: this is pure stdlib, needs no language/runtime change, improves the recognizer's shape,
and removes user-level `Maybe` reads without adding new builtins.

### 2. Migrate the recognizer to those combinators

Rewrite `examples/digit_recognizer/recognizer.sprout` to use the new mutable combinators.
Confirm:

- final accuracy remains `89.3333% (134/150)`,
- fold/update order is unchanged,
- benchmark output stays deterministic.

Reason: this measures what the safe stdlib abstraction buys by itself before adding more
specialized machinery.

### 3. Re-profile before designing kernels

After migration, re-run:

- `bash bench/digit_recognizer/bench.sh`
- `SPROUT_DEBUG_ALLOC=1 bench/digit_recognizer/bin/recognizer_sprout`
- `mise exec -- just gc-profile examples/digit_recognizer/recognizer.sprout`
- CPU sampling against the recognizer binary

Reason: the bottleneck may shift after direct mutable reads replace the current user-level
`mutvec_get` / `mutmatrix_get` pattern.

### 4. Add narrow specialized numeric helpers if still justified

If profiling still points at row-shaped numeric loops, add the smallest recognizer-shaped
helpers first, for example:

- `mutmatrix_row_dot(m: MutMatrix Double, row: Int, v: MutVec Double) -> Double !{IO}`
- `mutmatrix_row_add_scaled_inplace(m: MutMatrix Double, row: Int, v: MutVec Double, scale: Double) -> Unit !{IO}`

Reason: these are fused dot/update kernels at the stdlib API level. They are safe and focused,
without committing Sprout to a broad linear algebra package.

### 5. Only then consider runtime/compiler support

If narrow helpers still leave boxed `Double`, root/call overhead, or allocation as the dominant
cost, move to representation/compiler work:

- unboxed `Double` arrays/vectors,
- specialized `MutVec Double` / `MutMatrix Double` storage,
- compiler recognition/lowering of row-dot and row-update patterns.

Reason: true fused-kernel performance requires direct numeric storage. Without that, kernels can
reduce generic access overhead, but still move boxed `Double` values around.

## Summary

Implement in this order:

1. mutable combinators,
2. recognizer migration,
3. benchmark and profile,
4. narrow numeric kernels,
5. unboxed numeric representation and compiler lowering.

The mutable combinators and fused dot/matvec kernels complement each other. The combinators are
the safe ergonomic layer and a stepping stone; fused kernels are the later specialized
performance layer.

---

# Addendum — profile-grounded next phase (2026-07-10, after `92db2f0`)

Steps 1–2 landed in `92db2f0` (9 combinators + recognizer migration + edge-case tests).
Steps 3's measurements are captured below and drive the refined step-4 plan.

## Verified baseline (branch `mutable-combinators-recognizer`, `-O2`)

- **Correctness:** `134/150 = 89.3333%`, fold order preserved. ✓
- **Wall:** median ~1.42s. GC-disabled ~1.18s → **GC is ~17% of wall** (secondary, as predicted).
- **Allocation counters** (`SPROUT_DEBUG_ALLOC=1`), the reliable signal:

  | category | count | note |
  |---|---|---|
  | **closures** | **1,131,559** | dominant; removable in *pure stdlib* |
  | sprout_obj | 340,427 | upper bound on Double-boxes + tuples |
  | vectors | 1,314 | negligible — backing stores already flat |
  | gc_cycles | 528 | swept ~1.5M objects |

**Reading (what the data does and does not say).** Closures outnumber even the upper bound on
Double-boxes by >3×, and are killable without any runtime/representation change. This *confirms*
this plan's existing ordering (kernels before unboxing) and only re-weights the README headline:
the cheapest near-term lever is removing per-callback closures, not unboxing floats. Unboxing
remains justified — by the 340K Double-box allocations — but is the later, larger, approval-gated
Phase D. (A CPU sample was attempted; its parse was unreliable — ranked by call-tree node count,
not self-time — so it is **not** cited. `-O2` inlines `softsign`/`dabs`, so their absence from
frames is an inlining artifact, not evidence they are cheap.)

**Why fused kernels attack the real cost — and do not reopen a closed question.** A fused kernel
cuts root/closure traffic by *removing frames and indirect calls*, not by reclassifying which
values get rooted. The latter (scalar-skip / type-reclassification rooting) was already found to
be a dead end; this plan does not revisit it.

## Resolved design decisions

1. **Kernel home:** same `stdlib.mutable` module, Double-typed, grouped under a
   `# Double numeric kernels` section. Relocate to a dedicated numeric module later only if the
   surface grows.
2. **Proposal drift:** trim `docs/mutable-element-combinators-v1.md` to the 9 combinators that
   actually shipped; the 3 unimplemented whole-matrix entries (`mutmatrix_map_inplace`,
   `mutmatrix_zip_inplace`, `mutmatrix_row_fold`) become a "future additions" note, not an
   implied contract.

## Phase B — fused numeric kernels (concrete)

Prototype **one** kernel, measure, then decide the rest. All safe (bounds-checked
`vector_get_direct` internally, no new builtins, pure Sprout). Acceptance gate on every step:
recompile, confirm **`134/150`**, and require bit-identical FP order (seed with bias, ascending
index, `acc + w*x` — no reassociation).

1. `mutmatrix_row_dot(m: MutMatrix Double, r: Int, v: MutVec Double, seed: Double) -> Double !{IO}`
   — replaces the two `mutmatrix_row_zip_fold(dot_step, b, …)` sites in `forward`. Inlines the
   multiply-accumulate; no closure param.
2. **Measure.** Rebuild, confirm accuracy, capture the new closure count + wall. Only if closures
   drop materially and wall improves does kernel #3 earn its place.
3. `mutmatrix_row_add_scaled_inplace(m: MutMatrix Double, r: Int, v: MutVec Double, scale: Double) -> Unit !{IO}`
   — replaces the two `\(cur, x) -> scale_update(scale, cur, x)` lambdas in `backprop`. Those two
   per-unit wrapper closures alone are ~34 allocations/sample × 12,500 ≈ **~425K of the 1.13M**,
   the single biggest concrete slice; a plain-`Double` scale removes them.

Bonus: a kernel with no closure parameter sidesteps currying-defect #2 on those hot paths.

## Phase B — RESULTS (landed)

Both kernels implemented in `stdlib.mutable` (TDD, `tests/stdlib/test_native_mutmatrix.spr`) and
wired into the recognizer's `forward` (dot) and `backprop` (SGD update). Accuracy stayed
`134/150` at every step (bit-identical FP order). Dead `dot_step`/`scale_update` helpers removed.

| metric | baseline | dot only | both kernels |
|---|---|---|---|
| accuracy | 134/150 | 134/150 | **134/150** |
| wall (median) | ~1.42s | ~0.94s | **~0.55s (~2.6×)** |
| closures | 1,131,559 | 653,859 | **211,859 (−81%)** |
| gc_cycles | 528 | 346 | **177 (−66%)** |
| sprout_obj | 340,427 | 340,427 | 340,427 (unchanged) |

The 81% closure drop confirms the thesis: per-callback closures were the dominant, stdlib-removable
cost. Both kernels earned their place (the measurement gate after kernel #1 was clearly met).

**Note discovered during Phase B:** `assert_eq` is a *silent no-op on `Double`* — its
`where Eq a, ToString a` dispatch resolves to nothing rather than faulting (the null-filled
unresolved-dict class of bug). Execution continues; the assert neither prints nor counts, so a
`Double` `assert_eq` test is a false green. The kernel tests use `assert_true(state, …, x == y)`
instead, which is a discriminating oracle (verified: a deliberate wrong case FAILs). This bug is
orthogonal to the kernels and worth its own regression test + fix.

## Phase C — re-profile (decision gate for Phase D)

Done as the Phase B table above. `sprout_obj` (Double boxes) is now the untouched residue —
`340,427` allocations, ~62% of the remaining `sprout_obj + closure` traffic. Root/closure traffic
fell with the kernels; the next dominant allocation category is boxed `Double`. **This is the
signal that promotes Phase D** (unboxed `Double` storage) from speculative to justified — but it
remains a large, separate, approval-gated effort. Recommend re-running a CPU profile (correctly
parsed this time) before committing to a Phase D design.

## Phase D — representation/compiler (deferred, own design + approval)

Unboxed `Double` storage for `MutVec Double`, or compiler lowering of row-dot/row-update. Large;
justified by the 340K Double-box allocations, not by this phase's counters.
