# Digit-recognizer benchmark

Cross-language timing of the handwritten-digit recognizer (`examples/digit_recognizer/`).
Every implementation trains the **same** model — a 64→256→10 softsign MLP, MSE loss,
stochastic gradient descent, 25 epochs over 500 samples — with the same deterministic
LCG weight initialisation, so all reach the identical final accuracy (**92.67%**,
139/150). This isolates raw execution cost from algorithm/accuracy differences. (The
hidden layer was 24 nodes in an earlier revision; it was widened to 256 to make the
benchmark more compute-heavy — see git history for the smaller variant.)

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

## Representative result (Apple Silicon, 2026-07-11)

Median of 3 clean runs (64→256→10, fully-kerneled path for Sprout). Every run reached the same
final accuracy, **92.67% (139/150)**, across all eight implementations.

| Implementation | Wall (s) |
|---|---|
| Java | 0.24 |
| Scala (imperative) | 0.36 |
| Haskell (unsafe) | 0.47 |
| Go | 0.54 |
| Sprout (kernels, clang -O2) | 3.31 |
| Scala (idiomatic) | 4.59 |
| Haskell (pure) | 12.39 |
| Python (plain) | 17.95 |

At this larger size the ordering among the fast ports shifts: **Java (JIT) leads and Go
drops to ~0.54s** — the dense `double[][]` matmul is where HotSpot's autovectorization pays
off and Go's un-SIMD'd, bounds-checked slice access does not. Sprout (kernels) lands at
**3.31s**: ~6× Go, now ~1.4× faster than idiomatic Scala, ~3.7× faster than pure Haskell and
~5.4× faster than plain CPython.

The Sprout number improved from `4.76s` (~9× Go) after fusing the last un-kerneled hot loop —
`backprop`'s `dhidden` accumulate — into `mutmatrix_row_add_scaled_into` (2026-07-11), which
removed ~125K per-output closures and cut GC cycles 177→78. The residual ~6× gap to the native
ports is where Sprout's remaining cost lives — see below.

### Reading the Sprout row — it is the *tuned* idiom, not the naive one

The Sprout number is not apples-to-apples with the *inline* array loops the other fast ports
write, and it should not be read as "idiomatic Sprout." The distinction:

- **Go / Java / Scala-imperative / Haskell-unsafe** write the inner dot product and weight
  update as a plain inline loop over the array — `z += w[h][i] * x[i]` — the obvious code any
  programmer reaches for in that language.
- **Sprout** cannot express that inline loop in user code today. An unboxed *and* bounds-checked
  array read (`vector_get_direct`) is private to `stdlib.mutable`, and the generic iteration
  combinators allocate a closure per element. So the fast path is only reachable through the
  hand-written stdlib kernels `mutmatrix_row_dot` / `mutmatrix_row_sub_scaled_inplace` /
  `mutmatrix_row_add_scaled_into`. **The `3.31s` above is Sprout's *tuned* number** — comparable
  to the other *tuned* ports (Haskell-unsafe, Scala-imperative), not to a naive baseline.

This is not the kernel skipping work: it performs the same bounds-checked, unboxed
multiply-accumulate as Go's inline loop (and is *safer* than Haskell-unsafe's unchecked
`unsafeRead`), with bit-identical results. What it hides is expressibility — **idiomatic Sprout,
using the generic `mutmatrix_row_zip_fold` / `row_zip_update` combinators, runs at about
`13s`** at this size (a closure allocated per element: **7.4M** of them, vs **82K** for the
kernels). That combinators→kernels spread (`13s → 3.31s`, ~3.9×) is Sprout's own version of
the naive→tuned gap this benchmark isolates for Scala (`Vector → Array`) and Haskell (lists →
`IOUArray`); the single row above simply reports the tuned end.

**Where the tuned cost actually is.** The kernel hot loops are *allocation-free* — each threads
its `Double` accumulator unboxed and boxes no per-element value — so the whole run's allocation
(**82K** closures, `0.21M` `sprout_obj`, **78** GC cycles) is dominated by data loading and
outer-loop bookkeeping, not the matmul, and Phase C confirmed it is independent of layer width.
So the ~6× gap to Go is **scalar `Double` throughput**, not boxing or GC. Disassembly of the dot
kernel (Phase D prep) shows the inner loop is 22 machine instructions per element of which only
**2 are arithmetic**: every access pays two un-inlined `vector_get_direct` calls (bounds-check
hidden inside), a per-element GC-root push/pop, and `fmov` GPR↔FP shuffles from the uniform-i64
ABI, and the tail-recursive kernel is not vectorized. The levers that close this — inlinable
monomorphic element access, leaf-loop GC-root elision, then (secondarily) unboxed `f64` storage
and loop-shaped codegen — are the Phase D work designed in
`docs/phase-d-numeric-fastpath-design-2026-07-11.md` (evidence in
`docs/digit-recognizer-performance-plan-2026-07-10.md`). (Making that inline fast loop
expressible in ordinary Sprout, so no bespoke kernel is needed, is the same lever.)

### The purity tax, isolated twice — and it scales super-linearly

Both Scala and Haskell ship a **pure** and a **mutating** port that run the byte-identical
algorithm and reach the same 92.67%, so the gap between each pair is purely representation —
and at 256 nodes that gap is far larger than on the 24-node net, because allocation volume
grows with the parameter count:

- **Scala:** imperative (mutable `Array`, `while`) stays near the top; idiomatic (immutable
  `Vector`, `foldLeft`, a fresh case-class `Net` per SGD step) is now **~13× slower** (was
  ~3.6×).
- **Haskell:** unsafe (flat unboxed `IOUArray`, `unsafeRead`/`unsafeWrite`, in-place) lands
  near Go; the pure port (immutable `[Double]` lists, `foldl'`, a fresh `Net` per step) is now
  **~26× slower** (was ~10×) — linked lists box every `Double` and chase a pointer per element,
  and both costs scale with the wider layer.

The lesson repeats across two independent runtimes: dropping to flat unboxed arrays keeps the
mutating ports near Go even as the net grows, while pure/boxed representations pay a factor
that *widens* with size. Sprout's kernels put it in the mutating tier on allocation (flat with
size), so its residual distance to Go is the scalar-throughput problem above — the flat-unboxed
`f64` step the mature ports already took.
