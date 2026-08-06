# Transcendental math: pure Sprout vs C libm (2026-08-06)

Measurements for the `exp` / `ln` / `log2` / `log10` / `log` / `cbrt` / `pow` layer added
to `stdlib/math.sprout`. Harness: `bench/math_transcendental/bench.sh`
(`math_transcendental_bench.sprout` vs `libm_reference.c`, identical iteration count and
identical argument sweep).

**Machine:** Apple M3 Pro, macOS 15 (Darwin 24.6.0), Homebrew clang 22.1.6, both sides
`-O2`. 2,000,000 iterations per row, argument sweep `i * 5e-6` over `[0, 10)`.

## Results

Per-call cost with the harness baseline (same loop, same argument arithmetic, no math
call) subtracted. Figures are the **minimum** across 7 runs after a discarded warm-up.

| function   | Sprout ns/call | libm ns/call | ratio  |
|------------|---------------:|-------------:|-------:|
| `exp`      |            9.2 |          1.1 |   8.4x |
| `ln`       |            2.6 |          1.6 |   1.6x |
| `log10`    |            2.7 |          1.7 |   1.6x |
| `cbrt`     |           22.2 |          1.2 |  18.5x |
| `pow` frac |           36.5 |          5.6 |   6.5x |
| `pow` int  |            6.5 |          5.6 |   1.2x |

Raw baseline: Sprout 1495 µs, libm 1568 µs over 2M iterations — i.e. the harness loop
itself costs the two languages the same, so the deltas above are the math.

**On using the minimum.** Per-run spread reaches 40–170% on a loaded laptop, but the
minimum is reproducible to within ~5% across every run of the session (`exp` landed at
9.2/9.3, `cbrt` at 22.2/22.5/23.4). The minimum estimates the cost; the mean estimates
the background load. `bench.sh` prints the spread alongside each row so a reader can see
when a figure should not be trusted to two significant figures.

## Interpretation

- **`ln` and `log10` are effectively at parity** (1.6x). The reduction is exact halving
  and the atanh series has one real division, so there is little left to win.
- **`pow` with an integer exponent beats libm on accuracy at equal speed** (1.2x, and
  *bit-exact*). libm's `pow` has no reason to special-case a small integer exponent, so
  it pays the full `exp(y·ln x)` cost and returns an approximation where Sprout's binary
  exponentiation returns the exact value. `pow(T, 4.0) == T*T*T*T` exactly, which is the
  Stefan-Boltzmann case.
- **`cbrt` is the slowest row** (18.5x, ~22ns). It is 7 Newton passes, each carrying one
  unavoidable `x / (guess * guess)` division. A linear initial guess was implemented and
  measured: it reduces the worst case from 7 passes to 6, which did not justify the code,
  so it was dropped.
- **`pow` with a fractional exponent (36.5ns) costs more than `exp` + `ln` (11.8ns)**
  because the two are *serially dependent* — `exp` cannot start until `ln` finishes — so
  the row pays full latency, whereas the standalone `exp` row has an independent argument
  stream and pipelines across iterations. This is a property of the measurement, not a
  defect: both languages are measured the same way, and libm's `pow` shows the same
  effect (5.6ns against 1.1ns for `exp` alone).

## A hypothesis that did not survive measurement

`exp_series` originally wrote its Taylor coefficients as `r / k`. The emitted IR showed
11 `fdiv` instructions, and the expectation was a several-fold speedup from switching to
`r * (1.0 / k)` (literal-over-literal, so LLVM folds it to a constant multiply).

Measured, the change is worth **~20%**, not several times:

| form                | arm64 `-O2` | 2M calls |
|---------------------|-------------|----------|
| `r / k`             | 8 fdiv, 14 fmul | 8.1–8.4 ms |
| `r * (1.0 / k)`     | 0 fdiv, 22 fmul | 6.4–6.7 ms |

Two reasons the IR count overstated the win: clang already rewrites the divisors that are
exact powers of two (`/2`, `/4`, `/8`), so only **8** of the 11 divisions were ever real;
and the remaining divisions pipeline well in a throughput-bound loop. The change was kept
— it is free, and accuracy is identical at 7.98e-14 either way — but the comment in
`stdlib/math.sprout` now states the measured 20% rather than the guessed multiple.

## Conclusion: no new builtin

**No C builtin is justified by these numbers, and `runtime/APPROVED_BUILTINS` is
untouched.** Per `AGENTS.md` "Builtin vs Stdlib" rule 6, performance justifies a builtin
only against a *concrete, measured bottleneck*. A ratio against libm is not one:

- The two rows a caller is most likely to hit in bulk — `ln` and integer `pow` — are at
  1.2–1.6x, and integer `pow` is *more accurate* than libm.
- At 9–37ns, a single call is dwarfed by any surrounding allocation or I/O in the
  transform-scale and physical-modelling work this layer exists for. A Tsiolkovsky Δv or
  Stefan-Boltzmann evaluation is one or two calls, not millions.
- Should a real workload ever prove bottlenecked, `stdlib/math.sprout` already names the
  escalation: lowering to an LLVM intrinsic (`llvm.exp.f64`), which is a codegen change,
  **not** a C runtime function. So even that path leaves `APPROVED_BUILTINS` alone.

## Harness caveats, for whoever runs this next

- **Checksum comparison is weak.** Both binaries print an accumulator so no call can be
  optimised away, but Sprout's `to_string` for `Double` emits ~6 significant figures, so
  the printed checksums only confirm agreement to that precision. Accuracy is verified
  properly by `tests/stdlib/test_math_transcendental.spr` (115 assertions against
  libm-computed constants at 1e-12 relative), not by these checksums.
- **Two earlier versions of this harness produced invalid numbers.** Recorded so they are
  not reintroduced: (1) a `volatile double` accumulator in the C reference created a
  store/load latency chain that the libm call hid inside, making `exp` measure 0.03ns per
  call; (2) at 200k iterations the rows were 2–11ms and the *unchanged* C reference swung
  2x between runs. Both are why the accumulator is now plain and the count is 2M.
- Each Sprout loop is declared `!{IO}` although its body is pure, so the do-block
  sequences it strictly between the two `time_now_micros()` reads. A pure signature would
  let the call float outside the timed window.
