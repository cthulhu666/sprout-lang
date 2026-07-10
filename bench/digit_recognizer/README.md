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

## Representative result (Apple Silicon, 2026-07)

| Implementation | Wall (s) |
|---|---|
| Java | 0.15 |
| Go | 0.22 |
| Scala (imperative) | 0.20 |
| Haskell (unsafe) | 0.30 |
| Scala (idiomatic) | 0.68 |
| Haskell (pure) | 1.14 |
| Python (plain) | 1.68 |
| Sprout (clang -O2) | 23.05 |

Sprout is ~130× Go and ~14× plain CPython here: the current numeric codegen boxes every
`Double` and allocates a `Maybe` per array access, so the dense inner loops pay allocation +
GC + indirection the others avoid. The lever is unboxed float arrays + fused dot/matvec
kernels — see the ML-perf notes in `BACKLOG.md`.

### The purity tax, isolated twice

Both Scala and Haskell ship a **pure** and a **mutating** port that run the byte-identical
algorithm and reach the same 89.33%, so the gap between each pair is purely representation:

- **Scala:** imperative (mutable `Array`, `while`) ties Go; idiomatic (immutable `Vector`,
  `foldLeft`, a fresh case-class `Net` per SGD step) is **~3.4× slower**.
- **Haskell:** unsafe (flat unboxed `IOUArray`, `unsafeRead`/`unsafeWrite`, in-place) lands
  near Go; the pure port (immutable `[Double]` lists, `foldl'`, a fresh `Net` per step) is
  **~3.8× slower** — and trails idiomatic Scala because linked lists box every `Double` and
  chase a pointer per element, where `Vector` is a cache-friendlier trie.

The lesson repeats across two independent runtimes: on a mature JIT/GC, the cost of FP purity
+ per-step allocation is a small **constant factor (~3–4×)**, and dropping to flat unboxed
arrays closes it entirely (Haskell-unsafe ≈ Scala-imperative ≈ Go).

Sprout pays the *same class* of overhead — boxed `Double`, a `Maybe` allocated per array
access — but at **~100×**, because its codegen + GC are young. That ~100× vs the ~3–4× a
mature runtime charges for the same idiom is exactly the distance the unboxed-float-array +
fused-kernel work is meant to close; see the ML-perf notes in `BACKLOG.md`.
