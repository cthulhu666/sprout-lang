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

## Representative result (Apple Silicon, 2026-07-10)

Median of 3 clean runs (64→256→10, kernels path for Sprout). Every run reached the same
final accuracy, **92.67% (139/150)**, across all eight implementations.

| Implementation | Wall (s) |
|---|---|
| Java | 0.24 |
| Scala (imperative) | 0.34 |
| Haskell (unsafe) | 0.49 |
| Go | 0.53 |
| Scala (idiomatic) | 4.47 |
| Sprout (kernels, clang -O2) | 4.76 |
| Haskell (pure) | 11.07 |
| Python (plain) | 17.18 |

At this larger size the ordering among the fast ports shifts: **Java (JIT) now leads and Go
drops to ~0.53s** — the dense `double[][]` matmul is where HotSpot's autovectorization pays
off and Go's un-SIMD'd, bounds-checked slice access does not. Sprout (kernels) lands at
**4.76s**: ~9× Go, roughly tied with idiomatic Scala, ~2.3× faster than pure Haskell and
~3.6× faster than plain CPython.

Note the gap to the native ports **widened** with size (it was ~7× Go on the 24-node net).
That is the signal of where Sprout's remaining cost lives — see below.

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
  `4.76s` above is Sprout's *tuned* number** — comparable to the other *tuned* ports (Haskell-
  unsafe, Scala-imperative), not to a naive baseline.

This is not the kernel skipping work: it performs the same bounds-checked, unboxed
multiply-accumulate as Go's inline loop (and is *safer* than Haskell-unsafe's unchecked
`unsafeRead`), with bit-identical results. What it hides is expressibility — **idiomatic Sprout,
using the generic `mutmatrix_row_zip_fold` / `row_zip_update` combinators, runs at about
`13s`** at this size (a closure allocated per element: **7.4M** of them, vs 0.21M for the
kernels). That combinators→kernels spread (`13s → 4.76s`, ~2.7×) is Sprout's own version of
the naive→tuned gap this benchmark isolates for Scala (`Vector → Array`) and Haskell (lists →
`IOUArray`); the single row above simply reports the tuned end.

**Where the tuned cost actually is (a correction over the 24-node writeup).** The kernel hot
loop is *allocation-free*: at 256 hidden its allocation profile is byte-for-byte the same as at
24 (`0.21M` closures, `0.34M` `sprout_obj`, 177 GC cycles — all independent of the layer size,
because the matmul threads its `Double` accumulator unboxed and never boxes a per-element
value). So the 10× compute increase added *zero* allocation, and the ~9× gap to Go is **scalar
`Double` throughput**, not boxing or GC: every access pays a uniform-i64↔`double` ABI bitcast
and a per-element bounds-check branch, and the tail-recursive kernel is not vectorized. The
lever that closes this is contiguous unboxed `f64` storage (SIMD-friendly, no per-access
bitcast/bounds-check) plus loop-shaped codegen — the numeric-representation work tracked in
`BACKLOG.md` and `docs/digit-recognizer-performance-plan-2026-07-10.md`. (Making that inline
fast loop expressible in ordinary Sprout, so no bespoke kernel is needed, is the same lever.)

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
  **~22× slower** (was ~10×) — linked lists box every `Double` and chase a pointer per element,
  and both costs scale with the wider layer.

The lesson repeats across two independent runtimes: dropping to flat unboxed arrays keeps the
mutating ports near Go even as the net grows, while pure/boxed representations pay a factor
that *widens* with size. Sprout's kernels put it in the mutating tier on allocation (flat with
size), so its residual distance to Go is the scalar-throughput problem above — the flat-unboxed
`f64` step the mature ports already took.
