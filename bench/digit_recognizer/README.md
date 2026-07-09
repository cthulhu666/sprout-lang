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

Requires `go`, `javac`/`java`, `python3`, and a built `build/compile_driver_bin_stage1`
(`just build-stage1`). Compiled languages are pre-built; the run phase times the full
run (compute dominates, so data-load + startup are sub-1% of wall-clock).

## Implementations

| File | Notes |
|------|-------|
| `../../examples/digit_recognizer/recognizer.sprout` | Sprout (compiled via `just compile-native`) |
| `recognizer.go` | Go — native `[][]float64` |
| `Recognizer.java` | Java — native `double[][]` |
| `recognizer_plain.py` | Python, no ML libraries — lists |
| `recognizer_sklearn.py` | Python + scikit-learn (reference only; different solver/activation, not timed) |

## Representative result (Apple Silicon, 2026-07)

| Implementation | Wall (s) |
|---|---|
| Java | 0.10 |
| Go | 0.20 |
| Python (plain) | 1.66 |
| Sprout (clang -O2) | 23.63 |

Sprout is ~120× Go and ~14× plain CPython here: the current numeric codegen boxes every
`Double` and allocates a `Maybe` per array access, so the dense inner loops pay allocation +
GC + indirection the others avoid. The lever is unboxed float arrays + fused dot/matvec
kernels — see the ML-perf notes in `BACKLOG.md`.
