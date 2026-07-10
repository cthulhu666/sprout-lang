# Digit-recognizer benchmark

Cross-language timing of the handwritten-digit recognizer (`examples/digit_recognizer/`).
Every implementation trains the **same** model — a 64→24→10 softsign MLP, MSE loss,
stochastic gradient descent, 25 epochs over 500 samples — with the same deterministic
LCG weight initialisation, so all reach the identical final accuracy (**89.33%**,
134/150). This isolates raw execution cost from algorithm/accuracy differences.

## Run

```sh
bash bench/digit_recognizer/bench.sh
```

Requires `go`, `javac`/`java`, `scalac` (Scala 3), `ghc` (GHC), `python3`, and a built
`build/compile_driver_bin_stage1` (`just build-stage1`). Any missing toolchain is skipped.
Compiled languages are pre-built; the run phase times the full run (compute dominates, so
data-load + startup are sub-1% of wall-clock).

## Implementations

| File | Notes |
|------|-------|
| `../../examples/digit_recognizer/recognizer.sprout` | Sprout (compiled via `just compile-native`) |
| `recognizer.go` | Go — native `[][]float64` |
| `Recognizer.java` | Java — native `double[][]` |
| `Recognizer.scala` | Scala 3 — imperative: mutable `Array[Double]` + `while` (apples-to-apples with Java) |
| `RecognizerIdiomatic.scala` | Scala 3 — idiomatic: immutable `Vector`, `foldLeft`, case-class `Net` |
| `Recognizer.hs` | Haskell — pure: immutable lists, `foldl'`, a fresh `Net` per SGD step |
| `RecognizerUnsafe.hs` | Haskell — unsafe: flat unboxed `IOUArray` via `unsafeRead`/`unsafeWrite`, in-place |
| `recognizer_plain.py` | Python, no ML libraries — lists |
| `recognizer_sklearn.py` | Python + scikit-learn (reference only; different solver/activation, not timed) |

## Representative result (Apple Silicon, 2026-07-10)

Median of 3 clean runs after CPR Tier-2 worker routing, the mutable-combinator recognizer
migration, and the fused Double matvec kernels (`mutmatrix_row_dot` /
`mutmatrix_row_sub_scaled_inplace`). Every run reached the same final accuracy.

| Implementation | Wall (s) |
|---|---|
| Haskell (unsafe) | 0.10 |
| Go | 0.11 |
| Java | 0.12 |
| Scala (imperative) | 0.20 |
| Scala (idiomatic) | 0.72 |
| Sprout (kernels, clang -O2) | 0.76 |
| Haskell (pure) | 1.02 |
| Python (plain) | 1.69 |

Before CPR, this same benchmark measured Sprout at about 23.05s on this host. The current
0.76s median is a ~30× speedup, with identical accuracy. CPR first removed the boxed
`Maybe`/`Result` return from matched and do-bound mutable reads; the mutable-combinator
migration moved the row dot products and updates to closed library loops reading via
`vector_get_direct`; the fused Double kernels then inlined the multiply-accumulate, removing
the per-callback closure entirely (about **1.13M → 0.21M closure allocations**, a ~2× cut of
this stage on its own).

### Reading the Sprout row — it is the *tuned* idiom, not the naive one

The Sprout number is not apples-to-apples with the *inline* array loops the other fast ports
write, and it should not be read as "idiomatic Sprout." The distinction:

- **Go / Java / Scala-imperative / Haskell-unsafe** write the inner dot product and weight
  update as a plain inline loop over the array — `z += w[h][i] * x[i]` — the obvious code any
  programmer reaches for in that language.
- **Sprout** cannot express that inline loop in user code today. An unboxed *and* bounds-checked
  array read (`vector_get_direct`) is private to `stdlib.mutable`, and the generic iteration
  combinators allocate a closure per element. So the fast path is only reachable through the
  hand-written stdlib kernels `mutmatrix_row_dot` / `mutmatrix_row_sub_scaled_inplace`. **The
  `0.76s` above is Sprout's *tuned* number** — comparable to the other *tuned* ports (Haskell-
  unsafe, Scala-imperative), not to a naive baseline.

This is not the kernel skipping work: it performs the same bounds-checked, unboxed
multiply-accumulate as Go's inline loop (and is *safer* than Haskell-unsafe's unchecked
`unsafeRead`), with bit-identical results. What it hides is expressibility — **idiomatic Sprout,
using the generic `mutmatrix_row_zip_fold` / `row_zip_update` combinators, runs at about
`1.42s`** (a closure allocated per element). That combinators→kernels spread (`1.42s → 0.76s`)
is Sprout's own version of the naive→tuned gap this benchmark isolates for Scala (`Vector →
Array`) and Haskell (lists → `IOUArray`); the single row above simply reports the tuned end.
Making that inline fast loop expressible in ordinary Sprout — so no bespoke kernel is needed — is
the point of the unboxed-float / numeric-representation work tracked in `BACKLOG.md`.

Sprout is now ~7× Go, sits between idiomatic Scala and pure Haskell, and is ~2.2× faster
than plain CPython here. The remaining gap is mostly the same numeric representation problem:
dense `Double` code still pays boxed-value and GC costs that the mature native ports avoid —
`sprout_obj` (Double-box) allocations were untouched by the kernels. The next large lever is
unboxed float arrays; see the ML-perf notes in `BACKLOG.md` and
`docs/digit-recognizer-performance-plan-2026-07-10.md`.

### The purity tax, isolated twice

Both Scala and Haskell ship a **pure** and a **mutating** port that run the byte-identical
algorithm and reach the same 89.33%, so the gap between each pair is purely representation:

- **Scala:** imperative (mutable `Array`, `while`) ties Go; idiomatic (immutable `Vector`,
  `foldLeft`, a fresh case-class `Net` per SGD step) is **~3.6× slower**.
- **Haskell:** unsafe (flat unboxed `IOUArray`, `unsafeRead`/`unsafeWrite`, in-place) lands
  near Go; the pure port (immutable `[Double]` lists, `foldl'`, a fresh `Net` per step) is
  **~10× slower** — and trails idiomatic Scala because linked lists box every `Double` and
  chase a pointer per element, where `Vector` is a cache-friendlier trie.

The lesson repeats across two independent runtimes: on mature JIT/GC/native-code runtimes,
dropping to flat unboxed arrays closes the gap to Go entirely (Haskell-unsafe ≈
Scala-imperative ≈ Go). Pure representations still cost a constant factor, modest for Scala
`Vector` here and larger for Haskell lists because they box every `Double`.

Sprout used to pay the *same class* of overhead — boxed `Double`, plus a `Maybe` allocated
per array access — at **~100×**, because its codegen + GC are young. CPR removed the
per-access `Maybe` allocation from this path; mutable row combinators removed a layer of
generic indexed-access overhead; and the fused Double kernels removed the per-callback
closure. What remains is the boxed-`Double` cost itself — the flat-unboxed-array step that
closed the gap to Go for Haskell and Scala above, and the one lever Sprout has not yet pulled.
See the ML-perf notes in `BACKLOG.md`.
