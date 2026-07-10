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

**Correction over the initial read.** I first took `sprout_obj = 340,427` to be per-element
`Double` boxes from the matmul and the residue Phase D would remove. A scaling experiment
disproves that: widening the hidden layer 24 → 256 (a ~10× increase in FLOPs) left the kernel
path's allocation profile **byte-for-byte identical** — `211,859` closures, `340,427`
`sprout_obj`, `177` GC cycles, all independent of the layer size — while wall grew ~0.55s → ~4.76s.
So the kernel hot loop is **allocation-free** (the `Double` accumulator threads unboxed; no
per-element value is boxed), and those `sprout_obj` are size-independent overhead (data loading,
sample list, tuples), not matmul boxes.

The consequence for Phase D: at scale the recognizer is **compute-bound**, and Sprout's residual
~9× gap to Go is **scalar `Double` throughput**, not allocation or GC — every access pays a
uniform-i64↔`double` ABI bitcast and a per-element bounds-check branch, and the tail-recursive
kernel is not vectorized. Phase D is still justified, but reframed: the lever is **contiguous
unboxed `f64` storage (SIMD-friendly, no per-access bitcast/bounds-check) + loop-shaped codegen**,
not "stop boxing Doubles in the hot loop" (the kernels already don't). A correctly-parsed CPU
profile should confirm where the scalar cost sits (bitcast vs bounds-branch vs call overhead)
before committing to a design.

## Phase D — representation/compiler (deferred, own design + approval)

Contiguous unboxed `f64` storage for `MutVec Double` / `MutMatrix Double`, and/or compiler
lowering of row-dot/row-update to vectorizable loops. Large; justified by the **scalar-throughput**
finding above (the allocation profile is already flat with problem size), not by allocation
counts.

## Phase D preparation — static-IR + asm + sample evidence (2026-07-10)

The Phase C gate ("a correctly-parsed CPU profile should confirm where the scalar cost sits
before committing to a design") is satisfied here — but the *decisive* artifact is **static**, not
sampled. A `sample` profiler aggregates the hot loop into one inlined symbol and can only report
the forward-vs-backprop split; it structurally cannot separate "bitcast vs bounds-branch vs call
overhead." The compiled IR and asm can, and do. Config profiled: the current scaled `64→256→10`,
`clang -O2`, wall ~4.7s, accuracy `139/150 = 92.6667%`.

### What the compiled dot kernel actually costs (ground truth)

`mutmatrix_row_dot_go` — the fused forward-pass kernel — lowers to a 22-instruction inner loop of
which **exactly 2 instructions are the arithmetic** (`fmul`, `fadd`). Per element it pays:

| cost | evidence | lever |
|---|---|---|
| **2× `bl _vector_get_direct`** | un-inlined C calls; bounds-check hidden inside, invisible to LLVM | call + bounds-branch |
| **3× GC-root `bl`** (`push_i64_root` ×2, `pop_roots`) | root-stack + `sp` juggling every iteration, in an allocation-free loop | rooting model |
| **4× `fmov` GPR↔FP** | `fmov d0,x24` / `fmov d1,x0` around the math | uniform-i64 ABI tax |
| **no SIMD** | scalar `d0/d1/d8`, one element/iteration | blocked by the opaque calls above |

The ABI bitcast (`fmov`) that the original Phase D framing named as *the* lever is in fact the
**smallest** of the four. The dominant cost is the **five `bl` calls per element**, and the largest
of those (`vector_get_direct`) is un-inlined.

### The `-flto` probe: proven ineffective (kills the cheapest hypothetical option)

Rebuilding the recognizer with `clang -O2 -flto` (throwaway binary) gave **no speedup**
(4.63–5.40s vs 4.65–4.73s baseline, same accuracy). Disassembly confirms why: the kernel loop is
**byte-for-byte unchanged** — LLVM's LTO inliner declines to inline `vector_get_direct`, and the
opaque `sprout_gc_push/pop` calls bracketing each access are optimization barriers. Conclusion: the
per-element calls must be removed at the **Sprout-IR / compiler** level (inlinable intrinsic access
+ leaf-loop root elision), not by a build flag.

### `sample` finding (the one thing static IR did not show): Phase B is not finished

The heaviest self-time frame is **`mutvec_get_worker` (66)** — the un-fused `Maybe`-returning
path — outweighing *both* fused kernels combined (`row_dot_go` 36, `row_sub_scaled_go` 23). Its
source is `backprop`'s `accum_dhidden.term` inner loop (`recognizer.sprout:181–184`):
`dhidden[h] += dout_o * W2[o][h]`, which runs `n_hid·n_out·samples·epochs ≈ 32M` times using
`mutmatrix_get` (double bounds-check) + `mutvec_get` + `mutvec_set` per element. **This loop was
never given a fused kernel** — Phase B only covered forward-dot and the SGD update. (The `Maybe`
box is already stripped by Tier-2 CPR/do-bind, so the residual cost is call + bounds-check + root
traffic, not allocation — consistent with Phase C's size-independent `sprout_obj`.)

### Consequence for the plan — an ordering correction + a reframed Phase D

1. **Phase B follow-up (pure stdlib, no approval-gated change), do first:** add a fused
   `mutmatrix_row_add_scaled_into(dst: MutVec Double, m: MutMatrix Double, r: Int, scale: Double)`
   kernel (`dst[i] += scale·m[r][i]`) and wire `accum_dhidden` to it. This removes the currently
   co-dominant `mutvec_get_worker`/`mutmatrix_get` traffic from the hottest loop. Measuring Phase D
   before this is measuring a half-kerneled program.

2. **Phase D, reframed by the asm** — the lever is the **5 `bl` calls per element**, ranked:
   - **B (biggest): compiler-level inlinable element access** — lower a monomorphic `Vector Double`
     read to inline `getelementptr`+`load` (bounds-check inline & hoistable) instead of an opaque
     `vector_get_direct` call, and **elide per-iteration GC root push/pop in allocation-free leaf
     loops**. Removes 5 calls/element and unblocks LLVM autovectorization.
   - **A (smaller): contiguous unboxed `f64` storage** — removes the `fmov` ABI shuffles and is the
     prerequisite for *hand/codegen* SIMD, but on its own leaves the calls (the dominant cost) in
     place.
   - Evidence says **B captures most of the win and A compounds it**; "storage alone" (the original
     framing) is the least of the three.

Raw evidence captured under the session scratchpad (`recognizer.ll`, `recognizer.asm`,
`recognizer_lto.asm`, `sample.txt`).

### Phase B follow-up landed — `mutmatrix_row_add_scaled_into` + re-profile

Closed the kernel gap the sample exposed. New fused kernel
`mutmatrix_row_add_scaled_into(dst, m, r, scale)` (`dst[i] += scale·m[r][i]`, ascending i,
bit-identical to the old `cur + dout_o·wh` loop) added to `stdlib.mutable` (TDD,
6 new cases in `tests/stdlib/test_native_mutmatrix.spr`), and `backprop.accum_dhidden`'s inner
`term` loop rewired to it. `mutmatrix_get` is now unused by the recognizer.

- **Accuracy:** `139/150 = 92.6667%`, bit-identical. ✓
- **Wall:** ~4.7s → **~3.18s median (~1.5×)** — the single biggest hot loop was indeed un-kerneled.
- **Re-profile (`sample`, fully-kerneled):** kernels now own the tight loops
  (`row_dot` 37 + `row_add_scaled_into` 13 + `row_sub_scaled` 27); `mutvec_get_worker` fell 66→44
  (residual scalar accesses in `forward`/`update_hidden`, not tight loops). GC-root push/pop appears
  in the most call-tree nodes (91+55), un-inlined `vector_get_direct` in 23. (These are node/frame
  *occurrence* counts, not summed self-time percentages — the same node-vs-self-time caveat that
  burned the first profiling attempt; the decisive backing is the **asm**, which shows 3 live
  GC-root `bl` + 2 `vector_get_direct` `bl` per element.)

This is the measurement Phase D should be designed against: on a fully-kerneled program the residual
hot-loop cost is **per-element GC-root traffic + the un-inlined element-access call** — both
lever B (IR/compiler), confirming storage-alone (lever A) is not the primary lever.
