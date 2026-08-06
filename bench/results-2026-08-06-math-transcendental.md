# Transcendental math: pure Sprout vs C libm (2026-08-06)

Measurements for the `exp` / `ln` / `log2` / `log10` / `log` / `cbrt` / `pow` layer added
to `stdlib/math.sprout`. Harness: `bench/math_transcendental/bench.sh`
(`math_transcendental_bench.sprout` vs `libm_reference.c`, identical iteration count and
identical argument sweep).

**Machine:** Apple M3 Pro, macOS 15 (Darwin 24.6.0), Homebrew clang 22.1.6, both sides
`-O2`. 2,000,000 iterations per row, argument sweep `i * 5e-6` over `[0, 10)`.

> **REVISED same day, after a post-merge review.** The table below is the current one.
> The original run is kept as §"Superseded first run" at the end, because two of its
> figures were misleading in a way worth recording: the sweep ran only over `[0, 10)`, so
> it never exercised the range reductions and reported `exp` at 9.2 ns while `exp` near
> x=688 actually cost **1099 ns/call**; and `ln` measured 2.6 ns for a version that was
> less accurate than documented. See `docs/math-transcendental-v0.md` §11.

## Results

Per-call cost with the harness baseline (same loop, same argument arithmetic, no math
call) subtracted. Figures are the **minimum** across runs after a discarded warm-up.

| function     | Sprout ns/call | libm ns/call | ratio  |
|--------------|---------------:|-------------:|-------:|
| `exp`        |            9.2 |          1.1 |   8.4x |
| `ln`         |            5.0 |          1.7 |   2.9x |
| `log10`      |            5.1 |          1.8 |   2.8x |
| `cbrt`       |           11.9 |          1.2 |   9.9x |
| `pow` frac   |           39.7 |          5.9 |   6.7x |
| `pow` int    |            7.8 |          6.0 |   1.3x |
| `exp_wide`   |           21.7 |          1.1 |  ~20x  |
| `ln_wide`    |           20.9 |          1.6 |  ~13x  |
| `sqrt_wide`  |           21.5 |   `fsqrt` hw |    —   |

The `*_wide` rows sample the far end of the exponent range (`exp` near 688, `ln(1e-300)`,
`sqrt(1e300)`). They exist because their absence was itself the defect — a sweep confined
to small arguments cannot see a reduction that is linear in the binary exponent. `sqrt` has
no ratio row: libm's is a single hardware instruction, so baseline subtraction leaves ~0.

**Changes against the first run, all from the review fixes:**

- `cbrt` 22.2 → 11.9 ns and the wide rows 20–50x faster, from replacing one-factor-at-a-time
  reductions with coarse stride ladders.
- `ln` 2.6 → 5.0 ns — a real ~1.6x hot-path regression, accepted deliberately. Counting
  reduction steps and multiplying by `ln2` once (instead of accumulating it per step) makes
  `ln(2^k)` **exactly** correct where it previously carried 1.7e-11 absolute error, and it
  improved `pow`'s fractional accuracy 250x as a side effect. Measured A/B on the identical
  sweep: old flat loop 3.0–3.9 ns, new ladder 5.0–6.5 ns.
- `exp` unchanged at 9.2 ns. Getting there required ordering the ladder correctly: coarse
  strides listed *ahead* of the common case cost 4 extra comparisons plus 4 module-global
  loads per call and made `ln` 2.6x slower, so they are nested behind one cheap test and
  ordered by increasing magnitude.

Raw baseline: Sprout 1495 µs, libm 1568 µs over 2M iterations — i.e. the harness loop
itself costs the two languages the same, so the deltas above are the math.

**On using the minimum.** Per-run spread reaches 40–170% on a loaded laptop, but the
minimum is reproducible to within ~5% across every run of the session (`exp` landed at
9.1/9.2/9.5, `ln` at 5.0/5.2/5.4). The minimum estimates the cost; the mean estimates the
background load. `bench.sh` prints the spread alongside each row so a reader can see when a
figure should not be trusted to two significant figures. The `ln` and `exp` figures here
were additionally cross-checked with a standalone A/B binary holding both the old and new
implementations, so the hot-path regression is a measured delta rather than a run-to-run
difference.

## Interpretation

- **`ln` and `log10` are the closest to libm** (2.8–2.9x). The reduction is exact scaling
  and the atanh series has one real division, so there is little left to win — and what
  remains is deliberate: ~2 ns of the 5.0 ns buys exactness at powers of two.
- **`pow` with an integer exponent is at near-parity and more accurate on the cases that
  matter** (1.3x). libm's `pow` has no reason to special-case a small integer exponent, so
  it pays the full `exp(y·ln x)` cost; Sprout multiplies instead and avoids that path's
  truncation error entirely. It is **not** unconditionally bit-exact and must not be
  described that way — see `docs/math-transcendental-v0.md` §11.5.
- **`cbrt` halved** (22.2 → 11.9 ns) once its reduction used coarse strides. It is still
  ~10x libm because it is 7 Newton passes each carrying one unavoidable
  `x / (guess * guess)` division. A linear initial guess was implemented and measured:
  it reduces the worst case from 7 passes to 6, which did not justify the code.
- **`pow` with a fractional exponent (39.7 ns) costs more than `exp` + `ln` (14.2 ns)**
  because the two are *serially dependent* — `exp` cannot start until `ln` finishes — so
  the row pays full latency, whereas the standalone `exp` row has an independent argument
  stream and pipelines across iterations. This is a property of the measurement, not a
  defect: both languages are measured the same way, and libm's `pow` shows the same
  effect (5.9 ns against 1.1 ns for `exp` alone).
- **The `*_wide` rows are the ones to watch in future.** They are 13–20x libm, which is the
  honest cost of doing range reduction in Sprout rather than reading an exponent field —
  something the language cannot express, since there is no `Double → Int` conversion and no
  bit-level access to a `Double`. That, not the series arithmetic, is the remaining
  structural gap against libm.

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

## A refactor that did not survive measurement (added 2026-08-06)

The three range reductions (`sqrt_reduce`, `ln_reduce`, `cbrt_reduce`) thread the extracted
factor as a parameter and combine it with the series at the base case. The more legible
alternative returns the decomposition and lets the caller destructure it:

```sprout
fn ln_split(x: Double, k: Double) -> (Double, Double) = ... else (x, k)
fn ln_reduce(x: Double) -> Double = k * ln2 + ln_series(m) where (m, k) = ln_split(x, 0.0)
```

This was originally avoided only because a `where`-bound tuple did not type-check — `where`
bindings took their type from body usage rather than the right-hand side. That bug was fixed
on 2026-08-06, so the accumulator looked like a leftover workaround worth removing.

Measured A/B, both forms in one binary over the same 2M-call sweep
(`bench/math_transcendental/accumulator_vs_tuple_bench.sprout`):

| form                                   | 2M calls  | per call    | vs accumulator |
|----------------------------------------|-----------|-------------|----------------|
| baseline (loop + argument arithmetic)  | ~1.5 ms   | —           | —              |
| **A** accumulator-threaded (ships today) | ~10.2 ms  | **4.3 ns**  | —              |
| **B** tuple-returning, warm heap       | ~26.2 ms  | **12.4 ns** | **2.8x**       |
| **B** tuple-returning, cold heap       | ~60.5 ms  | ~29.5 ns    | ~6.8x          |

Checksums are **bit-identical** in every run (`3.27537e+06`), so this is purely a cost
difference — there is no accuracy argument in either direction.

Do not read row A against the 5.0 ns `ln` row in the main table above: this harness sweeps
`[1, 11)` rather than `[0, 10)`, and holds two `ln` implementations in one binary, so the
absolute numbers are not comparable across the two benchmarks. The **ratio** between A and B
is the result here, and both arms share the sweep, the binary and the series.

The cause is not tuples but a missing case in tuple-return CPR
(`docs/scalar-replacement-v0.md`). CPR *does* fire on the outer call: the `where`-destructuring
caller receives `{i64, i64}` by value with no allocation. It does **not** fire on the
function's own recursive edge, so each step round-trips through the boxed wrapper —
`sprout_alloc_tuple_blob(16)`, two loads to unpack, an `insertvalue` pair to repack. At ~20
steps per call that is ~20 heap allocations against zero, and the self-tail-call that TCO
would fold into a loop is lost too. The cold/warm gap for B (29.5 → 12.4 ns) is that GC cost
becoming visible; B also carries visibly higher run-to-run spread (~23% on one warm run,
against ~13% for A) for the same reason.

So the accumulator shape is kept, and `stdlib/math.sprout` now documents it as a measured
decision rather than a workaround. The CPR gap is filed in `BACKLOG.md` under *Sprout-IR /
Model-C Codegen*; both halves of the codegen behaviour are pinned by
`tests/stdlib/compiler/test_tuple_return_cpr.spr`, whose "KNOWN GAP" assertion is written to
fail loudly when the gap is closed, so this file and that comment get revisited.

## A third measurement that did not survive scrutiny — this one my own A/B (added 2026-08-06)

Recorded because the *wrong* number was quoted before it was checked, and the check is the
lesson. Claim made: module-level `let` constants cost `ln` ~39% of its hot path, measured at
**4.31 → 2.65 ns/call (1.63x)** by an A/B that replaced them with inline literals.

**The claim was wrong.** The two arms did not get the same inlining:

```
_main.loop_g: 371 asm lines   ... bl _main.ln_reduce_g     ← outlined call
_main.loop_l: 325 asm lines   ... (no call to ln_reduce_l) ← fully inlined
```

The global-loading arm was larger (940 vs 759 asm lines) and crossed LLVM's inline
threshold; the literal arm did not. So the 1.63x measured an inlining difference and was
attributed to constant folding. **Two arms in one binary over one sweep is necessary but not
sufficient — they must also be structurally equivalent after optimisation.** Check the
emitted asm for call-vs-inline before quoting a ratio; that check is what the
accumulator-vs-tuple comparison above passes and this one did not.

The mechanism behind the claim is real and verifiable: a module-level `let` becomes a
*mutable* `global i64 zeroinitializer` written by `@__sprout_init_globals`, and its address
escapes to `@sprout_gc_register_i64_root`, so LLVM cannot fold it and keeps a real
`adrp`/`ldr` at every use. Routing Double literals to the existing `private constant` path
(`eval_const_expr_ir`) was implemented, and it did exactly that — `ln_reduce`'s `adrp` 65→26,
`ldr` 86→41. It still lost:

| function | `adrp`+`ldr` | `mov`+`movk` | net instrs |
|---|---|---|---|
| `ln_reduce` | 151 → 67 | 181 → **358** | **+163** |
| `exp_scale` | 198 → 99 | 262 → **455** | +94 |
| `cbrt_reduce` | 103 → 33 | 134 → **255** | +51 |
| `ipow` | 69 → 10 | 121 → **229** | +49 |

The cause is the **i64-uniform value ABI**. `bitcast (double 0.693… to i64)` makes LLVM see
an *integer* constant, and materialising an arbitrary 64-bit integer immediate on arm64
costs `mov` + 3× `movk` = 4 instructions, against `adrp` + `ldr` = 2 plus an L1 hit. Powers
of two are cheap (`0x4070…` is one shifted MOVZ); `ln2`, `sqrt2`, `pi` are not. Wall clock
agreed as far as a loaded machine allowed — paired over 14 interleaved rounds, `ln` median
ratio 1.12 (slower); paired user CPU over 10 rounds, median 1.017, **never once faster**.
Load average was 7.27 with 40–269% spreads, so no confident regression figure is claimed;
the static instruction count is the deterministic part. **The change was reverted.**

Two dead ends ruled out for anyone revisiting this:

- **LLVM removed floating-point constant expressions** — `@g = private constant i64 bitcast
  (double fdiv (double 1.0, double 2.56e2) to i64)` is a hard parse error, `fdiv constexprs
  are no longer supported`. So `1.0 / two8` has no constexpr form.
- **Folding it in the compiler is blocked too** — it would mean printing a computed Double
  back as a decimal literal, and `double_to_string` is not round-trip exact (~6 significant
  figures), so it would silently corrupt the constant.

What survived is a different, type-directed fix that the investigation surfaced: the global
path registered **every** top-level `let` as a permanent GC root with no type check,
contradicting `docs/compiler-internals.md`'s "do not root non-heap scalars" invariant that
`ir_rooting` honours for SSA values. `stdlib/math.sprout` contributed 33 such roots, all
arithmetic `Double`s → now 0. That is a correctness-and-consistency fix, not a speed one:
counts across examples and the compiler itself are unchanged (6 → 6, all genuinely heap), so
**no wall-clock improvement is claimed for it either.**

## The root iterations: a real win, from the branch and not the arithmetic (added 2026-08-06)

`sqrt_iter` and `cbrt_iter` ran up to 60 Newton passes, exiting early on
`abs(next - guess) < 1e-15 * guess`. Replacing that guard with an unconditional **6 passes**:

| function | before | after | speedup | output vs before |
|---|---:|---:|---:|---|
| `sqrt`, normal magnitudes | 8.16 ns | **2.75 ns** | **2.97x** | **bit-identical**, 0 of 400k samples differ |
| `cbrt` (bench `cbrt` row) | 9.82 ns | **4.44 ns** | **2.21x** | ~1 ULP on 3.5% of inputs; residual *improves* |
| `sqrt_wide` (`sqrt(1e300)`) | 19.00 ns | 19.09 ns | **1.00x** | unchanged |

Baselines matched within 0.3% across the paired runs (1348 vs 1352 µs; 1322 vs 1318 µs), so
these are not the load artifacts that spoiled earlier measurements in this file.

**The cost was the branch, not the division.** Isolated on the reduced interval the guard was
worth 3.86x on `sqrt` (7.17 -> 1.86 ns) and 2.50x on `cbrt` (7.99 -> 3.20 ns), while the
inlined `fdiv` count barely moved (13 -> 11 for sqrt). The guard makes the trip count
input-dependent, so the loop-exit branch mispredicts on nearly every call (~15-20 cycles,
~5 ns at 3.5 GHz). A fixed count makes it statically known and lets LLVM fully unroll: arm64
`-O2` emits `fcmp=0` and no per-pass branch, against `fcmp=2` and 8 conditional branches for
the guarded form. Both arms were confirmed fully inlined before the ratio was quoted — the
check that the earlier globals A/B failed.

**Why 6.** The guarded version took up to 7 passes. Six unconditional passes match or beat its
accuracy everywhere; five does not — `sqrt` degrades to 9.3e-08 and `cbrt` to 3.7e-08, five
orders outside the module contract. Worst relative residual over 400k samples: `sqrt` 4.39e-16
across [1,4), `cbrt` 9.38e-16 across [1,8) (down from 1.005e-15).

**`sqrt_wide` not moving is the useful negative result.** At `sqrt(1e300)` the ~500-stride
range reduction dominates so completely that a 3x faster iteration is invisible. That is
direct evidence that the remaining `*_wide` gap is the reduction, not the series — the
structural limit discussed under "Interpretation" above, and the one thing here that cannot be
fixed without a new language capability.

**A division-free alternative was measured and rejected.** Iterating the inverse cube root
(`r' = r*(4 - x*r^3)/3`) uses only multiplies, but the five serially-dependent `fmul`s form a
*longer* latency chain than the single `fdiv` they replace: 5.41 ns/call against 3.20 for the
plain fixed-count division form. Removing a division is not automatically a win when the
replacement is a dependency chain.

**Corpus gap this exposed.** The change rewrote both Newton loops and moved **0 of 57** golden
IR files, because no corpus member imported the Double `stdlib.math` — the only
`stdlib.math` reference in all 57 was `examples/astar.sprout`, which imports
`stdlib.math.int`. `tests/smoke_shapes/10_double_math.spr` was added to close it; the corpus is
now 58 files and the gate was confirmed to **fire** (1 difference on a single pass-count
change), not merely to pass.

## Conclusion: no new builtin

**No C builtin is justified by these numbers, and `runtime/APPROVED_BUILTINS` is
untouched.** Per `AGENTS.md` "Builtin vs Stdlib" rule 6, performance justifies a builtin
only against a *concrete, measured bottleneck*. A ratio against libm is not one:

- The two rows a caller is most likely to hit in bulk — `ln` and integer `pow` — are at
  1.3–2.9x, and integer `pow` avoids the exp/ln truncation error libm pays.
- At 5–40ns (up to ~24ns at the exponent extremes), a single call is dwarfed by any surrounding allocation or I/O in the
  transform-scale and physical-modelling work this layer exists for. A Tsiolkovsky Δv or
  Stefan-Boltzmann evaluation is one or two calls, not millions.
- Should a real workload ever prove bottlenecked, `stdlib/math.sprout` already names the
  escalation: lowering to an LLVM intrinsic (`llvm.exp.f64`), which is a codegen change,
  **not** a C runtime function. So even that path leaves `APPROVED_BUILTINS` alone.

## Harness caveats, for whoever runs this next

- **Checksum comparison is weak.** Both binaries print an accumulator so no call can be
  optimised away, but Sprout's `to_string` for `Double` emits ~6 significant figures, so
  the printed checksums only confirm agreement to that precision. Accuracy is verified
  properly by `tests/stdlib/test_math_transcendental.spr` (148 assertions against
  libm-computed constants at 1e-12 relative), not by these checksums.
- **Two earlier versions of this harness produced invalid numbers.** Recorded so they are
  not reintroduced: (1) a `volatile double` accumulator in the C reference created a
  store/load latency chain that the libm call hid inside, making `exp` measure 0.03ns per
  call; (2) at 200k iterations the rows were 2–11ms and the *unchanged* C reference swung
  2x between runs. Both are why the accumulator is now plain and the count is 2M.
- Each Sprout loop is declared `!{IO}` although its body is pure, so the do-block
  sequences it strictly between the two `time_now_micros()` reads. A pure signature would
  let the call float outside the timed window.

## Superseded first run (kept for the record)

The original table, measured before the post-merge review fixes. Preserved because two of
its rows illustrate measurement traps rather than results:

| function   | Sprout ns/call | libm ns/call | ratio  |
|------------|---------------:|-------------:|-------:|
| `exp`      |            9.2 |          1.1 |   8.4x |
| `ln`       |            2.6 |          1.6 |   1.6x |
| `log10`    |            2.7 |          1.7 |   1.6x |
| `cbrt`     |           22.2 |          1.2 |  18.5x |
| `pow` frac |           36.5 |          5.6 |   6.5x |
| `pow` int  |            6.5 |          5.6 |   1.2x |

- **`exp` at 9.2 ns was the best case reported as the cost.** The `[0, 10)` sweep never
  reaches a large binary exponent, and `exp_scale` then moved one power of two per step, so
  `exp` near x=688 cost 1099 ns/call — 111x the published figure. The lesson is not "the
  measurement was noisy"; it is that a sweep is part of the claim, and a benchmark that
  samples only the cheap region will confidently report the wrong number. Hence the
  `*_wide` rows above.
- **`ln` at 2.6 ns was faster because it was wrong.** It accumulated `ln2` once per
  reduction step, which is cheaper per step than the correct `k * ln2` only in the sense
  that it did less bookkeeping — and it carried 1.7e-11 absolute error at the extremes
  against a documented ~1e-13. The current 5.0 ns buys exactness.
- `pow` int at 6.5 ns was also labelled "bit-exact" in the surrounding text. It is not; see
  `docs/math-transcendental-v0.md` §11.5.
