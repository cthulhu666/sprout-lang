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

Median of 3 clean runs after CPR Tier-2 worker routing plus the mutable-combinator
recognizer migration. Every run reached the same final accuracy.

| Implementation | Wall (s) |
|---|---|
| Haskell (unsafe) | 0.09 |
| Go | 0.10 |
| Java | 0.11 |
| Scala (imperative) | 0.20 |
| Scala (idiomatic) | 0.68 |
| Haskell (pure) | 0.99 |
| Python (plain) | 1.68 |
| Sprout (clang -O2) | 1.49 |

Before CPR, this same benchmark measured Sprout at about 23.05s on this host. The
current 1.49s median is a ~15.5× speedup, with identical accuracy. CPR first removed
the boxed `Maybe`/`Result` return from matched and do-bound mutable reads; the
mutable-combinator migration then moved the row dot products and row updates to
closed library loops that read via `vector_get_direct`.

Sprout is still ~15× Go and slightly faster than plain CPython here. The remaining gap is mostly the
same numeric representation problem: dense `Double` code still pays boxed-value and GC
costs that the mature native ports avoid. The next large lever is unboxed float arrays
and fused dot/matvec kernels — see the ML-perf notes in `BACKLOG.md`.

### The purity tax, isolated twice

Both Scala and Haskell ship a **pure** and a **mutating** port that run the byte-identical
algorithm and reach the same 89.33%, so the gap between each pair is purely representation:

- **Scala:** imperative (mutable `Array`, `while`) ties Go; idiomatic (immutable `Vector`,
  `foldLeft`, a fresh case-class `Net` per SGD step) is **~3.4× slower**.
- **Haskell:** unsafe (flat unboxed `IOUArray`, `unsafeRead`/`unsafeWrite`, in-place) lands
  near Go; the pure port (immutable `[Double]` lists, `foldl'`, a fresh `Net` per step) is
  **~11× slower** — and trails idiomatic Scala because linked lists box every `Double` and
  chase a pointer per element, where `Vector` is a cache-friendlier trie.

The lesson repeats across two independent runtimes: on mature JIT/GC/native-code runtimes,
dropping to flat unboxed arrays closes the gap to Go entirely (Haskell-unsafe ≈
Scala-imperative ≈ Go). Pure representations still cost a constant factor, modest for Scala
`Vector` here and larger for Haskell lists because they box every `Double`.

Sprout used to pay the *same class* of overhead — boxed `Double`, plus a `Maybe` allocated
per array access — at **~100×**, because its codegen + GC are young. CPR removed the
per-access `Maybe` allocation from this path, and mutable row combinators removed another
layer of generic indexed-access overhead. The remaining boxed-`Double` cost is still the
distance the unboxed-float-array + fused-kernel work is meant to close; see the ML-perf
notes in `BACKLOG.md`.
