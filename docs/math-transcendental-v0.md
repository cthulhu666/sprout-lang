# Transcendental Math and the Int/Double Module Split (v0)

Status: supporting design doc. Records the design of the `Double` transcendental layer
in `stdlib/math.sprout` (`exp`, `ln`, `log2`, `log10`, `log`, `cbrt`, `pow`) and of the
`stdlib.math` / `stdlib.math.int` split that made room for it. `docs/spec-v0.md` §8 is
normative for the `Int` surface; `docs/math-partiality-v0.md` remains the reference for
the partiality rules this layer obeys.

## 1. Problem statement

`stdlib.math` exported `sqrt` for `Double` but no exponential, logarithm, cube root, or
`Double` power — verified across `stdlib/*.sprout` and `runtime/*.c`, where the only
`pow` was `pow(Int, Int) -> Maybe Int`. Ordinary numeric modelling was therefore
unevenly blocked:

| model | needs | before |
|---|---|---|
| Stefan-Boltzmann emittance, `j = σT⁴` | three multiplies | expressible |
| its inverse, `T = ⁴√(j/σ)` | `sqrt(sqrt(x))` | expressible |
| Tsiolkovsky Δv, `Isp·g₀·ln(m₀/m₁)` | `ln` | **blocked** |
| logistic sigmoid / softmax | `exp` | **blocked** |

`docs/nn-gap-analysis.md` recorded the second pair as requiring "one C extern (`exp`) +
`APPROVED_BUILTINS` entry". That turned out to be false, which is the first result below.

A second problem was structural. Adding a `Double` `pow` collides with the existing
`Int` `pow`, and Sprout has no overloading. The module had already met this twice and
answered with a C-style marker prefix — `abs`/`fabs`, `clamp`/`fclamp`. Extending that
would eventually mark `pow`, `min`, `max`, `sign` and `mod` as well: five more names
carrying a prefix that exists only to say "the Int one took the good name".

## 2. Goals and non-goals

**Goals.** Implement `exp`, `ln`, a logarithm family, `cbrt` and a `Double` `pow` in
Sprout with no new builtin; keep accuracy adequate for physical modelling; give both
numeric types plain unprefixed names; stay forward-compatible with the eventual numeric
typeclasses; decide the builtin question by measurement.

**Non-goals.** `Double` `min`/`max`/`sign` (they force the NaN-ordering decision — §7);
hyperbolics, `expm1`/`log1p`, `sigmoid` (no caller yet); bit-exact IEEE
rounding (the layer is IEEE *in spirit*, per partiality Rule 2); implementing the
`Numeric`/`Real` classes of `docs/numeric-types-v1-draft.md` (compiler-sized, §7).

> `asin`/`acos` were listed here as non-goals and have since shipped — see §15.

## 3. Prior art

### 3.1 Naming the logarithm family

Every claim checked against the language's own reference; sources in §10.

| Language | natural log | base 2 | base 10 | general base |
|---|---|---|---|---|
| C | `log` | `log2` | `log10` | — (compose) |
| Rust `f64` | `ln` | `log2` | `log10` | `log(self, base)` |
| Haskell | `log` | — | — | `logBase b x` |
| Python | `log(x)` | `log2` | `log10` | `log(x, base)` |
| Go | `Log` | `Log2` | `Log10` | — (compose) |
| Java | `log` | — | `log10` | — (compose) |

The split is real: C/Haskell/Python/Go/Java spell the natural log `log`, while Rust
spells it `ln` and gives `log` the general-base meaning. Sprout follows **Rust**, which
is the only surveyed language offering all four, and whose `ln` removes the standing
"is `log` natural or base-10?" ambiguity that trips users moving between C and a
calculator. `log(x, base)` takes the argument first so it reads as a generalisation of
`log2(x)`/`log10(x)` rather than as Haskell's flipped `logBase`.

### 3.2 Disambiguating Int and Double operations of the same name

| Language | mechanism |
|---|---|
| C | distinct spellings: `abs` (int, `stdlib.h`) vs `fabs` (double, `math.h`); also `fmod`, `fmin`, `fmax`, `fdim` |
| Rust | inherent methods on each type, so `i32::pow` and `f64::powf` never collide; `f64` further splits `powi`/`powf` by exponent type |
| Haskell | typeclasses: one `abs` in `Num`, one `(**)` in `Floating`, `(^)` for integral exponents |
| Go | package-per-type-ish: `math.Abs` is float64-only; integer abs is hand-written |
| Python | dynamic dispatch on one name |

Three strategies exist: **prefix/suffix the name** (C, and Rust's `powi`/`powf`),
**scope the name** (Rust's inherent methods, Go's package boundary), or **unify via
typeclass** (Haskell). Sprout cannot do the third yet (§7). It chose the second —
scope by module — over the first, for the reasons in §4.

## 4. Decision: split the modules, do not prefix the names

`stdlib.math` is the `Double` layer; `stdlib.math.int` is the `Int` layer; both use
plain names. `fabs` → `abs`, `fclamp` → `clamp`.

Three arguments, in ascending order of weight:

1. **The prefix does not scale.** It was already on two names and the next additions
   would put it on five more. A convention whose only content is "this type lost the
   race for the name" gets less informative the more it spreads.

2. **The library value is asymmetric.** Int `abs`/`min`/`max` are one-liners — which is
   precisely why three files in-tree re-implement them locally instead of importing
   (`stdlib/mutable.sprout:67`, `examples/sentry_issue_browser_tui.sprout:54-57`).
   Nobody hand-rolls `exp` or `cbrt`. The short, discoverable names should belong to the
   functions that genuinely need a library.

3. **It is the forward-compatible shape.** `docs/numeric-types-v1-draft.md` §6.2 designs
   `class Real a` with methods named `sqrt`, `exp`, `log`, `pow` — exactly the names
   `stdlib.math` now uses. When that lands, the migration moves definitions into an
   instance and call sites drop a qualifier. Under the prefix, every call site would
   need renaming (`fabs` → `abs`) at the same moment.

Cost: two consumer files migrated (`examples/astar.sprout`, `tests/stdlib/test_math.spr`
→ `test_math_int.spr`), and `fabs`/`fclamp` callers renamed. The `Int`/`Double`
boundary made this safe — the types never implicitly coerce (`spec-v0.md` §8), so a
missed call site is a compile error, not a silently different number.

**Implementation cost: zero compiler change.** `module_loader.module_name_to_path` maps
module names to paths by replacing dots with slashes, so `stdlib.math.int` resolves to
`stdlib/math/int.sprout`; and `stdlib/compiler.sprout` beside `stdlib/compiler/` is
existing precedent for a file-module next to a same-named directory.

## 5. Algorithms

Every function is range reduction into a narrow interval plus a series or Newton
iteration there. The reductions move only by exact powers of two, so they contribute no
rounding error and the residual is series truncation alone.

A language constraint shaped all of them: **there is no `Double → Int` conversion**
(only `to_double`, an `sitofp`). Every reduction counter is therefore a Double-valued
integer. This is a feature rather than a workaround — `×2` and `÷2` are exact in binary
floating point.

Every reduction climbs a **coarse stride ladder** rather than moving one factor at a time,
bounding the step count at ~20 instead of ~1070 (§11.3 for why that matters and what it
cost). The strides are nested behind one cheap test and ordered by increasing magnitude, so
the common near-1 caller exits after a single comparison.

- **`exp(x)`** — `k = round(x/ln2)`, `r = x − k·ln2` so `|r| ≤ ln2/2`; Taylor through
  `r¹²`, Horner-nested; scale by `2^k` with exact doublings (strides 512/64/8/1).
- **`ln(x)`** — halve or double into `[1/√2, √2)` (strides 512/64/8/1) while **counting**
  the powers of two, then `k·ln2` once plus the atanh series `2(s + s³/3 + …)` through
  `s¹⁵` with `s = (m−1)/(m+1)`. Counting rather than accumulating `ln2` per step is what
  makes `ln(2^k)` exact (§11.4). Centring on `√2` rather than `[1,2)` caps `|s|` at 0.172
  instead of 1/3 — half the terms for equal accuracy.
- **`log2`/`log10`/`log`** — `ln` divided by a constant, or by `ln(base)`.
- **`sqrt(x)`** — reduce into `[1,4)` by exact powers of **four** (strides 512/64/8/4, all
  even powers of two so each has an exact square root), then Newton. The reduction is what
  keeps Newton's seed within a factor of two of the root; without it the iteration cap was
  reached first and large arguments returned silently wrong answers (§11.1).
- **`cbrt(x)`** — odd symmetry on the sign; reduce by exact factors of **eight** into
  `[1,8)` (strides 384/48/12/3 — the powers of two divisible by three, so each has an
  exact cube root); Newton `g' = (2g + x/g²)/3`. Deliberately **not** `exp(ln(x)/3)`, so
  `cbrt` neither inherits `exp`/`ln` truncation error nor depends on them.
- **`pow(x, y)`** — C99/IEEE edge cases (§6), then binary exponentiation for an integer
  `|y| ≤ 1024` (exact when every intermediate product is — **not** unconditionally, §11.5),
  else `exp(y·ln x)`.

### 5.1 Two non-obvious details

**`exp`'s overflow guards are load-bearing for termination**, not merely IEEE etiquette.
Without them a `±inf` argument yields `k = ±inf`, and the scaling loop never exits
because `inf − 1.0 == inf`.

**`exp` scales in two steps below `k = −1021`.** Halving one place at a time re-rounds a
*subnormal* on each of the last ~54 steps, and the round-to-even cascade flushes
`exp(-745)` to `0.0` instead of `≈5e-324`. Scaling to 54 places above the target and
dividing by `2⁵⁴` once collapses those roundings into one. Regression-tested.

### 5.2 Measured accuracy

Prototyped against libm before implementation, then re-verified by
`tests/stdlib/test_math_transcendental.spr` (115 assertions at 1e-12 relative).

| function | max relative error | range |
|---|---|---|
| `exp` | 8.0e-14 | `[-708, 709]` |
| `exp` | 5.9e-12 | subnormal tail `[-745, -708]` |
| `ln` | 1.9e-14 | `1e-300 … 1e300` |
| `ln` | 0.0 absolute | at exact powers of two (see §11) |
| `sqrt` | ~1e-16 | whole range, after the §11 reduction fix |
| `cbrt` | 1.3e-14 | `1e-300 … 1e300`; exact on perfect cubes |
| `pow`, integer exponent | exact when every intermediate product is | see §11 — **not** unconditionally bit-exact |
| `pow`, fractional exponent | ~6.4e-14 | after the §11 `ln` fix |

**Accuracy is not uniform across `stdlib.math`, and the header must not be read as if it
were.** The figures above cover the functions this document adds. The pre-existing
**trigonometric** functions in the same module are ~1e-8 — five orders looser, because
their Taylor series are truncated for transform-scale use. A caller sizing a tolerance
must take it from the right group. This is stated in the module header and repeated here
because a first version of this document tightened the header to a single `~1e-13` figure
that was true only of the new functions (§11).

## 6. Semantics and error behaviour

Partiality follows `docs/math-partiality-v0.md` Rule 2 — out-of-domain `Double` gives
IEEE `NaN`/`±inf`, never a clamped value, detected with `is_nan`. New members:

| call | result | why |
|---|---|---|
| `ln(-1.0)`, `log2/log10/log` of a negative | `NaN` | outside the real domain |
| `ln(0.0)` | `-inf` | the pole |
| `pow(-2.0, 0.5)` | `NaN` | negative base, fractional exponent: not real |
| `pow(-2.0, 3.0)` | `-8.0` | negative base, integer exponent: sign by parity |
| `cbrt(-8.0)` | `-2.0` | **in** domain — `cbrt` is odd, unlike `sqrt` |
| `log(x, 1.0)` | `±inf` | base 1 has no logarithm |

`pow` follows **C99/IEEE F.9.4.4**, which differs from Python in two visible ways:
`pow(0.0, -1.0)` is `+inf` where Python raises `ValueError`; and `pow(x, ±0)` and
`pow(1.0, y)` are `1.0` even when the other operand is `NaN`, so both are ordered ahead
of the NaN test in the implementation.

No diagnostics change: every addition is an ordinary stdlib function, so the only new
messages are the existing "Unknown variable" for a missing import.

## 7. Why not typeclasses (the alternative that would have avoided all of this)

`docs/numeric-types-v1-draft.md` §6.2 already designs `class Numeric` / `class Integer` /
`class Real`, which would let one `abs`, one `sqrt`, one `pow` serve both types and make
both the prefix and the split unnecessary. It is blocked on four counts:

1. It is unimplemented — `Status: draft. No implementation yet` — and its milestone N1
   (class infrastructure, operators desugaring through dispatch, `/` removed from `Int`)
   is a compiler and spec change, not a stdlib one.
2. `Double` has exactly one instance in the whole stdlib: `ToString`
   (`prelude.sprout:814`). There is no `Eq Double` and no `Ord Double`, so even
   `min`/`max` cannot be written generically today.
3. That absence is a *flagged open question*, not an oversight. The draft's §7.1 states
   IEEE NaN "breaks the `Ord` contract that `Numeric` requires" and concludes "Decision
   needed before `Double` ships". `Double` shipped without it. Prior art diverges: Rust
   gives `f64` only `PartialOrd`; Haskell defines `Ord Double` and it is a known footgun.
4. **Even a finished hierarchy would not unify `pow`.** `Int` `pow` returns `Maybe Int`
   (Rule 1) and `Double` `pow` returns `Double` (Rule 2). One class method has one
   signature modulo the class variable, so expressing both needs associated types, which
   Sprout does not have.

Recorded conflict for whoever implements those classes: the draft declares `Integer.mod`
and `Real.pow` **total** (`-> a`), while `math-partiality-v0.md` §5 commits to the
`Maybe`-returning signatures as permanent. Those two documents disagree and the
disagreement is not resolved here.

## 8. Performance, and the builtin question

Full numbers in `bench/results-2026-08-06-math-transcendental.md`. Per call on an M3
Pro, 2M iterations, harness baseline subtracted:

| function | Sprout | libm | ratio |
|---|---:|---:|---:|
| `exp` | 9.2 ns | 1.1 ns | 8.4x |
| `ln` | 5.0 ns | 1.7 ns | 2.9x |
| `log10` | 5.1 ns | 1.8 ns | 2.8x |
| `cbrt` | 4.4 ns | 1.2 ns | 3.7x |
| `pow` fractional | 39.7 ns | 5.9 ns | 6.7x |
| `pow` integer | 7.8 ns | 6.0 ns | 1.3x |
| `sqrt`, normal magnitude | 2.8 ns | hardware `fsqrt` | — |
**No function in this layer is magnitude-dependent any more** (§13, 2026-08-07). Every
range reduction now extracts or applies the binary exponent through the IEEE bit pattern
in O(1) via `double_to_bits`, instead of walking it in power-of-two strides. The old
`*_wide` rows are gone because the distinction they measured no longer exists — the
cost at 1e±300 is the cost at 2.0.

| function | wide-input cost before | after |
|---|---|---|
| `ln`, x = 1e-300 | 22.5 ns | **3.7 ns** |
| `sqrt`, x = 1e300 | 25.1 ns | **5.9 ns** |
| `sqrt`, x = 1e-300 | 27.0 ns | **5.8 ns** |
| `cbrt`, x = 1e300 | 20.7 ns | **8.0 ns** |
| `exp`, x ≈ 688 | 21.5 ns | **9.3 ns** |
| `exp`, x = -700 | 26.6 ns | **9.6 ns** |

Results are **bit-identical** to the stride ladder at every sample tested, which is the
bar the cross-check test enforces — both forms are exact, so any difference at all would
mean one of them is wrong. Normal magnitudes are unchanged to within noise (`sqrt(2.0)`
1.00x, `cbrt(2.0)` 1.03x, `exp(1.0)` 1.04x), so there is no trade.

`cbrt` 11.9 → 4.4 ns and `sqrt` 8.2 → 2.8 ns come from §12: both Newton iterations now run
a fixed 6 passes instead of testing for convergence each pass. Note that `sqrt` at **1e300**
did not move at all (19.0 → 19.1 ns) — at that magnitude the ~500-stride range reduction
dominates so completely that a 3x faster iteration is invisible, which is the sharpest
evidence available that the remaining `*_wide` gap is reduction cost and not series cost.

The last three rows exist because their absence was a defect: the original sweep ran only
over `[0, 10)`, so it never exercised the range reductions' step count and reported the
best case as the cost. Before §11's ladder fix, `exp` near x=688 actually cost **1099
ns/call** against the 9.2 ns published. `sqrt` has no meaningful ratio row because libm's
is a single hardware instruction.

**Conclusion: no new builtin, and `runtime/APPROVED_BUILTINS` is untouched.**
`AGENTS.md` "Builtin vs Stdlib" rule 6 requires a concrete measured bottleneck, and a
ratio against hand-tuned libm is not one. The rows bulk callers hit are the closest to
parity: `sqrt` 2.8 ns, integer `pow` 1.3x, `ln` 2.9x, `cbrt` 3.7x. Integer `pow` is also
*more accurate* than libm's on the cases that matter, because libm has no reason to
special-case a small integer exponent and pays the full `exp(y·ln x)` — but it is **not**
unconditionally bit-exact and must not be described that way; see §11.5. At 3–40ns a call is
dwarfed by any surrounding allocation or I/O in the work this layer serves; a Δv or emittance
evaluation is one or two calls. Should a workload ever prove bottlenecked, the escalation
named in
`stdlib/math.sprout` is an **LLVM intrinsic** (`llvm.exp.f64`) — a codegen change, which
leaves `APPROVED_BUILTINS` alone even then.

Two negative results worth keeping, both from measuring rather than reasoning:

- Rewriting `exp_series`' coefficients from `r / k` to `r * (1.0 / k)` was expected to be
  a several-fold win, because the emitted IR showed 11 `fdiv`. Measured: **~20%**. clang
  already rewrites the power-of-two divisors, so only 8 divisions were ever real, and
  those pipeline in a throughput-bound loop. Kept (it is free, accuracy is identical) but
  the source comment states the measured figure.
- A linear initial guess for `cbrt`'s Newton iteration cuts the worst case from 7 passes
  to 6. Not kept — it does not pay for the code.
- A **division-free** cube-root iteration — Newton on the inverse cube root,
  `r' = r*(4 - x*r³)/3`, recovering the root as `x*r²` — removes every `fdiv` from the loop
  and is *slower*: 5.41 ns/call against 3.20 for the plain fixed-count division form. The five
  multiplies are serially dependent, so they form a longer latency chain than the single
  division they replace. Removing a division is not automatically a win.
- Const-folding `stdlib/math.sprout`'s module-level `Double` constants into LLVM
  `private constant`s, so LLVM can fold them instead of reloading a mutable global, was
  implemented and **reverted**: net +163 instructions in `ln_reduce`, because the i64-uniform
  value ABI makes LLVM treat them as *integer* constants and an arbitrary 64-bit integer
  immediate on arm64 costs `mov` + 3× `movk` against `adrp` + `ldr`. See
  `bench/results-2026-08-06-math-transcendental.md`.

## 9. Compatibility and migration

Breaking, with a compile error at every affected site rather than a behaviour change:

| change | migration |
|---|---|
| Int surface moved to `stdlib.math.int` | `import stdlib.math (abs)` → `import stdlib.math.int (abs)` |
| `fabs` → `abs` | rename; still in `stdlib.math` |
| `fclamp` → `clamp` | rename; still in `stdlib.math` |

In-tree consumers migrated: `examples/astar.sprout`, `stdlib/test.sprout`,
`tests/stdlib/test_linalg_vec3.spr`, `tests/stdlib/test_math_double.spr`, and
`tests/stdlib/test_math.spr` renamed to `test_math_int.spr` for symmetry with
`test_math_double.spr`. `stdlib/linalg.sprout` needed no change — `sqrt`, `tan` and
`radians` keep their names and their module.

`stdlib.math` is not bundled into `bootstrap/compile_driver.ll` (only the compiler and
prelude are, and no `stdlib/compiler/*` module imports it), so none of this moves the
bootstrap seed.

## 10. Sources

Primary references for §3; each row was checked against these.

- C: `math.h` / `stdlib.h` as shipped in the macOS SDK — `int abs(int)` in `_stdlib.h`,
  `double fabs/fmod/fmin/fmax/fdim/fma(double…)` and `double log/log2/log10/pow` in
  `math.h`. Also ISO C99 §F.9.4.4 for `pow` special cases.
- Rust `f64` (`ln`, `log2`, `log10`, `log(base)`, `powi`, `powf`, `cbrt`; `PartialOrd`
  but not `Ord`): https://doc.rust-lang.org/std/primitive.f64.html
- Haskell `Prelude` (`log`, `logBase`, `(^)`, `(^^)`, `(**)`, `Floating`):
  https://hackage.haskell.org/package/base/docs/Prelude.html
- Python `math` (`log(x[, base])`, `log2`, `log10`, `pow` raising on `pow(0.0, -1.0)`):
  https://docs.python.org/3/library/math.html
- Go `math` (`Log`, `Log2`, `Log10`, `Pow`, `Abs` float64-only):
  https://pkg.go.dev/math
- Java `java.lang.Math` (`log`, `log10`, `pow`, `cbrt`):
  https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/lang/Math.html
- POSIX `asin(3)` / `acos(3)` — used for §15's range and special-value requirements:
  `acos` returns in `[0, pi]` and `acos(+1)` returns `+0`; `asin` returns in
  `[-pi/2, pi/2]`, `asin(±0)` is `±0`, and an argument outside `[-1, 1]` is a domain
  error returning NaN: https://man7.org/linux/man-pages/man3/acos.3.html ·
  https://man7.org/linux/man-pages/man3/asin.3.html
- In-repo: `docs/math-partiality-v0.md` (Rules 1 and 2),
  `docs/numeric-types-v1-draft.md` §6.2/§7.1/§8 (the class design and its open
  questions), `docs/spec-v0.md` §8 (normative Int surface).

## 11. Post-merge review corrections (2026-08-06)

A high-effort adversarial review ran *after* the original PR merged and found ten
confirmed defects. All were fixed in the follow-up PR; recorded here because several were
wrong *claims* in this document, and a design doc that quietly edits away its own errors
is worse than one that shows them.

### 11.1 Two silent wrong-answer bugs

**`sqrt` was grossly wrong above ~1e35 — pre-existing, and this document made it worse.**
Newton was seeded with `x` itself. For a guess far above the root the Heron step only
roughly *halves* it, so reaching the right order of magnitude alone took ~log2(x) steps:
past ~1e35 the 60-iteration ceiling was hit first and the unconverged guess was returned.
`sqrt(1e40)` gave `8.674e21` instead of `1e20`, with no NaN and no error — the exact
"silent in-band lie" the module header forbids, which meant `linalg`'s vec3 length was
~130 orders out at astronomical scales. The original PR did not cause this, but it renamed
the function's neighbourhood and tightened the block header to vouch for `~1e-13` across
the full exponent range, so it newly *certified* the bug.

Fixed with an exact power-of-four range reduction (stride 4 because `sqrt(4) = 2` is
representable, which keeps the scale-back error-free), mirroring what `cbrt` already did.
Newton now starts within a factor of two of the root for every input. Round-trip error at
1e300 is ~1e-16.

**`pow` returned `+0.0` where C99 requires `-0.0`.** The odd-integer negative-base arm
negated with `0.0 - v`, and `0.0 - 0.0` is `+0.0` — negation of zero needs unary `-`,
which lowers to `fneg` and flips the sign bit. So `pow(-2.0, -1075.0)` came back `+0.0`,
and a caller recovering the sign of an underflowed result via `1.0 / result` read `+inf`
and inferred the wrong direction.

### 11.2 Two C99 conformance gaps

`pow(-inf, non-integer)` returned `NaN`. C99 F.9.4.4 confines the negative-base-NaN rule
to **finite** `x < 0`; for an infinite base the magnitude saturates and the result is
`±inf` or `±0` by the exponent's sign and parity. Infinite bases now have their own arm,
placed after the `x == 0.0` test because `x * 0.5 == x` is also true of both zeros.

`abs(-0.0)` returned `-0.0` and `floor(-0.0)` returned `+0.0` — both pre-existing, both
inverted from IEEE. The cause is that `-0.0 < 0.0` is **false** (IEEE compares the zeros
equal), so a `x < 0.0` guard never sees negative zero. Both now special-case zero.

### 11.3 A ~110x per-call cost cliff the benchmark never sampled

`exp_scale`, `ln_reduce` and `cbrt_reduce` moved one power of two (or eight) per recursive
step, so cost was linear in the argument's binary exponent — up to 1076 steps. Measured:
`exp` near x=688 cost **1099 ns/call** against the 9.2 ns this document published, and
`ln(1e-300)` cost 673 ns against 2.6 ns. The published figures were not wrong for the
sweep used; the sweep was wrong, because `[0, 10)` never leaves the cheap region.

Fixed with coarse stride ladders (512 / 64 / 8 / 1, and 384 / 48 / 12 / 3 for `cbrt`),
bounding every reduction at ~20 steps: `exp` at 688 is now 21.7 ns, `ln(1e-300)` 20.9 ns.
The benchmark grew `exp_wide` / `ln_wide` / `sqrt_wide` rows so the far end of the exponent
range is sampled from now on.

**Two ordering lessons from doing this, both measured:** listing the coarse strides *ahead*
of the common case cost 4 extra comparisons and 4 module-global loads per call and made
`ln`'s hot path 2.6x slower; nesting them behind one cheap test and then ordering them by
*increasing* magnitude (so the common case exits after a single comparison) recovered
almost all of it. `exp` is back to 9.2 ns. `ln` settles at 5.0 ns against 3.1 ns for the
old flat loop — a genuine ~1.6x hot-path cost, accepted for a 25x improvement at the
extremes plus the exactness win below.

### 11.4 `ln` was less accurate than claimed — and fixing it fixed `pow` too

`ln_reduce` added or subtracted one `ln2` per step, accumulating one rounding each time:
1.7e-11 absolute error at `ln(2^-1070)` where the comment claimed ~1e-13, which made
`floor(log2(x))` return the wrong integer for roughly half of all exact powers of two.
Counting the steps and computing `k * ln2` once is a single rounding regardless of distance
travelled — strictly more accurate *and* ~1070 fewer operations. Absolute error at
`ln(2^±1070)` is now **0.0**.

This turned out to dominate `pow`'s fractional path as well, exactly as the review
predicted: `ln`'s relative error at `|ln x| ≈ 700` is an *absolute* error in `exp`'s
argument, and `exp` converts that into relative error in the result. `pow(1e300, 0.5)²`
deviated 1.29e-11 before and 5.2e-14 after — a 250x improvement from a change made for
accuracy in a different function.

### 11.5 The exactness claim that was simply false

This document's accuracy table read "`pow` | **exact** | integer exponents", and the
claim was repeated in the normative spec, the builtins reference and the benchmark results.
It is not true. Binary exponentiation computes `pow(T,4)` as `(T*T)*(T*T)` — two roundings
— where `T*T*T*T` is `((T*T)*T)*T` — three. `pow(T,4.0) == T*T*T*T` is **false** for
T=6726.387968863355, and a correctly-rounded libm `pow` differs from both. The original
test passed only because `5772^4` happens to be exactly representable: a special case was
verified and written up as a general guarantee. Corrected everywhere, and the test suite
now pins both halves — the identity holding at 5772 *and* failing to hold in general.

### 11.6 Stale golden IR

The module split invalidated `tests/golden/ir/examples__astar.sprout.ll`, which still
referenced `@stdlib.math.fabs` and `@stdlib.math.int_abs`. This escaped CI because
`scripts/ir_golden_diff.sh` was wired into **no** `just` target and no workflow, so
`just test` and `gate-quick` were both green over a stale corpus. Snapshots were
regenerated as part of this work.

**Resolved 2026-08-06** (same day, separate change): the gating gap itself is closed.
`just ir-golden-diff` now runs in `gate` and `ci-fast-gates`, so CI blocks a stale
golden. The interesting part was the second-order finding — the corpus, both scripts,
and 57 goldens had all been *built*, and the `BACKLOG.md` item requesting them was
still open, because nobody had wired the last step. So `gate-audit` gained assertion B:
every `scripts/*.sh` must be reachable from the justfile or a `.claude` hook, or be
allowlisted with a stated reason. Two further dormant gates surfaced immediately
(`ir_byte_identical_check.sh`, and `gate-audit` itself, which CI had never run).

## 12. The root iterations run a fixed pass count (2026-08-06)

`sqrt_iter` and `cbrt_iter` originally ran up to 60 Newton passes, exiting early once
`abs(next - guess) < 1e-15 * guess`. That guard has been replaced with an unconditional
**6 passes** in both.

**It was a 3x cost, and the cost was the branch.** Measured on the reduced interval, dropping
the guard is worth 3.86x on `sqrt` (7.17 → 1.86 ns/call) and 2.50x on `cbrt` (7.99 → 3.20);
end to end on the benchmark's own sweeps, `sqrt` at normal magnitudes goes 8.16 → 2.75 ns
(2.97x) and the `cbrt` row 9.82 → 4.44 ns (2.21x). The inlined `fdiv` count barely changes
(13 → 11 for `sqrt`), so the arithmetic was never the issue: the guard makes the trip count
depend on the input, so the loop-exit branch is unpredictable and mispredicts on nearly every
call — ~15–20 cycles, ~5 ns at 3.5 GHz. A fixed count makes it statically known, which also
lets LLVM fully unroll. arm64 `-O2` emits `fcmp=0` and no per-pass branch for the fixed form,
against `fcmp=2` and 8 conditional branches for the guarded one.

**Why 6, and not 5 or 7.** The guarded version took at most 7 passes over the reduced
interval. Six unconditional passes match or beat its accuracy everywhere; five does not, and
fails loudly rather than subtly — `sqrt` degrades to 9.3e-08 and `cbrt` to 3.7e-08, five
orders outside this module's contract. Worst relative residual over 400k samples: `sqrt`
4.39e-16 across `[1,4)`, `cbrt` 9.38e-16 across `[1,8)`.

**Compatibility, and the one place it is not bit-clean.** `sqrt` is bit-identical to the
guarded version at every one of 400k samples — it had already converged before the guard
fired, so this is a pure cost removal. `cbrt` is **not**: its guard fired at different pass
counts for different inputs (3 passes near x=8, 7 elsewhere), so no fixed count can reproduce
it, and ~1 ULP moves on about 3.5% of inputs. `cbrt`'s worst-case residual *improves*
(1.005e-15 → 9.38e-16) and every existing assertion passes (`test_math_transcendental.spr`
compares via `check_approx` at 1e-12 relative), but a caller comparing exact `cbrt` bit
patterns against pre-2026-08-06 output will see a difference. This was an explicit choice, not
an oversight.

**Tests.** `tests/stdlib/test_math_root_accuracy.spr` — residual *sweeps* across each
function's reduced interval plus 200 doublings and 200 halvings to exercise the reduction. The
gap it closes: existing coverage checked only hand-picked points against hardcoded constants
(`cbrt(27) == 3`), and a Newton error peaks in the interval's interior, not at tidy cubes — a
wrong pass count exact at 8, 27 and 1000 would have passed the whole suite. Sample density is
part of the assertion: a 10x coarser sweep reports `cbrt`'s worst residual as 9.30e-16 instead
of the true 1.005e-15, simply stepping over the peak.

**A corpus gap this exposed.** Rewriting both Newton loops moved **0 of 57** golden IR files.
No corpus member imported the Double `stdlib.math`: the only `stdlib.math` reference across
all 57 goldens was `examples/astar.sprout`, which imports `stdlib.math.int`. So the
change-detector wired into CI in §11 was structurally blind to `sqrt`/`cbrt`/`exp`/`ln`/`pow`.
`tests/smoke_shapes/10_double_math.spr` closes it — the corpus is now 58 files, and the gate
was confirmed to **fire** (1 difference on a single pass-count change) rather than merely to
pass.

**Rejected alternative.** A division-free cube root — Newton on the inverse cube root,
`r' = r*(4 - x*r³)/3`, recovering the root as `x*r²` — removes every `fdiv` from the loop and
measured *slower*, 5.41 ns/call against 3.20. The five multiplies are serially dependent and
form a longer latency chain than the single division they replace.

## 13. O(1) range reduction for `ln` (2026-08-07)

`ln_reduce` walked the binary exponent in power-of-two strides (512 → 64 → 8 → 1),
~20 compare-multiply-branch steps. That was the whole of the `*_wide` gap: making the
Newton iteration ~3x faster in §12 moved `sqrt(1e300)` from 19.0 to 19.1 ns — i.e. not
at all — while normal-magnitude `sqrt` went 8.16 → 2.75 ns.

It now reads the exponent straight out of the IEEE bit pattern, in constant time, using
the `double_to_bits`/`double_from_bits` intrinsics (`docs/double-bit-access-v0.md`).
Those lower to **nothing** — under the i64-uniform ABI a `Double` and an `Int` are
already the same LLVM type — so this adds no runtime symbol, no `APPROVED_BUILTINS`
entry, and no libm dependency. Keeping `stdlib.math` libm-free is why the obvious
alternative was rejected: `llvm.frexp` does not lower to inline instructions on arm64,
it lowers to a call to libm's `frexp`.

Measured over 2M-call warm sweeps, averaged across 3 runs, A/B in one binary against
the retained stride ladder:

| input | O(1) | stride ladder | speedup |
|---|---|---|---|
| `ln(2.0)` | 3.73 ns | 4.02 ns | 1.1x |
| `ln(pi)` | 4.04 ns | 4.51 ns | 1.1x |
| `ln(1e-300)` | 3.65 ns | 22.48 ns | **6.2x** |
| `ln(1e300)` | 3.86 ns | 22.26 ns | **5.8x** |

The headline is the **flatness**, not the peak speedup: cost is now independent of
magnitude, and normal magnitudes got slightly faster too, so there is no trade.

### Two things that cost time to get right

**Bind the biased exponent once.** The first version called the extraction helper per
branch. Each call is a signed division that LLVM lowers to `add/cmp/csel/asr` — five
instructions — and recomputing it made normal-magnitude `ln` go 5 → 7 ns, giving back at
the common case exactly what the change won at the extremes. A `where` binding fixed it.

**Fold the subnormal lift into the exponent, not into the result.** Subnormals are
scaled by 2^54 into the normal range and the 54 taken back off. Doing that as
`ln_reduce(x * two54) - 54.0 * ln2` *after the fact* introduces a second rounding, where
the stride ladder does a single `k * ln2`. `tests/stdlib/test_math_wide_reduction.spr`
caught it — 5 subnormal samples disagreed with the oracle. Threading the correction into
`k` before the multiply (`ln_reduce_norm`'s `adj` parameter) restores the single-rounding
property and bit-identity.

### Why the old ladder is still in the tree

`ln_reduce_strided` / `ln_strided` are retained and exported, as the accuracy oracle for
the cross-check test. They are an independently-derived implementation of the same
mathematical split, which is what makes them worth keeping rather than deleting — a
test that compares a function against itself proves nothing.

## 14. O(1) reduction for `sqrt`, `cbrt` and `exp` (2026-08-07)

§13 converted `ln`. This completes the layer. All results stay **bit-identical** to the
retained stride ladders (`sqrt_strided`, `cbrt_strided`, `exp_strided`, exported solely
as oracles for `tests/stdlib/test_math_wide_reduction.spr`).

| input | O(1) | stride ladder | |
|---|---|---|---|
| `sqrt(2.0)` | 5.77 ns | 5.75 ns | 1.00x |
| `sqrt(1e300)` | 5.94 ns | 25.12 ns | **4.2x** |
| `sqrt(1e-300)` | 5.77 ns | 26.98 ns | **4.7x** |
| `cbrt(2.0)` | 7.20 ns | 7.45 ns | 1.03x |
| `cbrt(1e300)` | 8.04 ns | 20.72 ns | **2.6x** |
| `exp(1.0)` | 9.03 ns | 9.41 ns | 1.04x |
| `exp(688)` | 9.34 ns | 21.46 ns | **2.3x** |
| `exp(-700)` | 9.56 ns | 26.61 ns | **2.8x** |

(Absolute figures are inflated relative to §13's: this harness passes the functions as
first-class values to A/B them in one binary, which blocks inlining. Both arms pay it
equally, so the ratios are sound.)

### The roots: floor division, and why it is branchless

`sqrt(x) = sqrt(m)·2^j` with `x = m·2^(2j)`, and `cbrt` likewise with `2^(3j)`. The
exponent must split into a part divisible by 2 (or 3) plus a remainder that stays with
the mantissa, so **`j = floor(e/2)`** — and Sprout's `/` truncates toward **zero**, which
is not floor for negative numerators.

This is not a corner case. At `x = 0.5` the exponent is −1: floor gives `j = −1` and
`m = 2.0`, inside `[1,4)`; truncation gives `j = 0` and `m = 0.5`, **outside** the
interval Newton is seeded for. Every negative odd exponent hits it — half of all inputs
below 1.0, and for `cbrt`'s mod-3 split, two in every three.

The obvious correction (`if a < 0 && q*n != a then q-1`) needs a branch and, with no `%`
operator, a multiply to test the remainder. It measured: `cbrt` at normal magnitude went
**7.41 ns against the ladder's 6.66** — a 10% regression, precisely the "gave back at the
common case what it won at the extremes" trap §13 records for `ln`. The fix biases the
numerator non-negative first, where truncation *is* floor:

```
floor(e/n)  ==  (e + 1074) / n  -  1074/n        for every e >= -1074
```

A binary64 exponent never goes below −1074, and 1074 is divisible by both 2 and 3, so the
shift comes back out exactly. Verified exhaustively over `e ∈ [-1074, 1023]` for both
divisors. That restored `cbrt(2.0)` to 1.03x.

### `exp`: the inverse direction, and a Double→Int conversion

`exp` needs the opposite operation — build `2^k` from an integer `k` rather than extract
`k` from a double. That exposed a gap: `k` is a **Double**-valued integer (the language
has no `Double → Int`), while the exponent-field construction needs an `Int`.

Bit access supplies one, via the classic magic-number trick: for integral `d` in
`[0, 2^52)`, `d + 2^52` has exponent field exactly `52+1023` and its *mantissa* bits are
`d`, so subtracting the bit pattern of `2^52` leaves `d` exactly. `round_to_int` remains
**private** to `stdlib.math`: it answers a narrower question than the public conversion —
round-to-nearest only, `|v| < 2^52` only, total — and sits on `exp`'s hot path, so it must
not acquire the guards a general conversion needs.

The public conversion arrived later, as `to_int`, and is documented in
[`docs/double-to-int-v0.md`](double-to-int-v0.md). The rounding-mode and out-of-range
questions this section left open are answered there; the short version is that they
**separate**. `floor`/`ceiling`/`truncate`/`round` stay in `Double` and are total, so
the rounding-mode question never reaches the conversion; `to_int` returns `Maybe Int`,
which is where the out-of-range question is answered, once.

### The overflow boundary, which the sweep missed

A first version short-circuited `k > 1023` to `+inf`, reasoning that the exponent field
cannot hold more. Wrong: `y = exp_series(r)` lies in `[0.707, 1.415]`, so `y·2^1024` is
finite whenever `y < ~1.34`. **`exp(709.78)` is exactly that case** — `k = 1024`, result
~1.79e308, just under `DBL_MAX`.

`test_math_transcendental`'s "exp(709.78) is finite" caught it. The new wide-reduction
sweep did **not**, because its 0.37 step from −745 stopped at 698 and never reached the
overflow boundary — a reminder that a sweep's bound is part of its assertion. The sweep
now runs past 710, and the scaling splits into two exact factors (`2^1023 · 2^(k-1023)`)
so the trailing multiply overflows to `+inf` exactly when it should.

## 15. `asin` and `acos` (2026-08-11)

§2 deferred these as "no caller yet". They are now implemented, in the shape BACKLOG
predicted — over `atan` and `sqrt`, no new machinery:

```sprout
asin(x) = 2 * atan(x / (1 + sqrt(1 - x²)))     # half-angle, from tan(t/2) = sin t / (1 + cos t)
acos(x) = pi/2 - asin(x)
```

> **Superseded in part by §16.** `acos` gained a second arm for `x > 0.5` shortly after,
> to fix the relative-accuracy caveat §15.3 records below. The `asin` analysis in this
> section stands unchanged.

Three things about this were not obvious in advance, and each is a trap a later
"simplification" can walk back into.

### 15.1 The textbook form does not work over *this* `atan`

The identity every reference gives is `asin(x) = atan(x / sqrt(1 - x²))`. Over libm that
is fine. Over the `atan` in this module it fails at exactly the two most-used inputs: at
`|x| = 1` the denominator is `0`, the quotient is `±inf`, and `atan_reduce`'s halving step
`x / (1 + sqrt(1 + x·x))` evaluates `inf / inf` = **NaN**. So `asin(1.0)` would be NaN
rather than `pi/2`.

The half-angle form has no such point: its denominator `1 + sqrt(1 - x²)` is bounded below
by `1` across the whole domain, so the argument handed to `atan` never exceeds `1` in
magnitude and the endpoints reduce to the finite `2·atan(±1)`.

### 15.2 Accuracy is *better* than the trig row it is built on

Measured against libm over 2M samples spanning `[-1, 1]`: worst absolute error **1.6e-15**
for `asin`, **1.8e-15** for `acos`. That is four orders tighter than the `~1e-8` the module
header quoted at the time for `sin`/`cos`/`atan` as one group, which looks impossible for a
function whose entire body is one `atan` call. (That grouped row was itself wrong for
`atan`, which this section's own reasoning should have flagged sooner — §16.2 corrects it.)

The resolution is that `atan`'s figure is a *whole-line* worst case. Its three halving
passes saturate: they shrink `x = 1e6` only to `0.199`, where the `x¹³`-truncated series
still carries ~1.6e-11, but they shrink `x = 1` to `0.098`, where the same series is at
8e-16. `asin` only ever produces arguments of magnitude ≤ 1, so it lives entirely in
`atan`'s accurate regime and never pays for the large-argument saturation.

The lesson generalises: a composite's accuracy is set by the *sub-range* it drives its
components over, not by their published worst case. Sizing a tolerance from the header row
would have under-claimed these by seven orders.

### 15.3 Small error does not imply in-range — the endpoints need a branch

The bare half-angle form is accurate to 1.6e-15 *and still wrong* at one input.
`2·atan(1)` lands on `1.5707963267948974`, one ulp **above** `pi/2`, which makes

```
acos(1.0)  ==  -8.9e-16      # a NEGATIVE angle
```

POSIX requires `acos` to return within `[0, pi]` and to return `+0` for `acos(1)`, and
`asin` to return within `[-pi/2, pi/2]`. More practically, `acos(dot(u, v))` on unit
vectors is *the* canonical caller, and parallel inputs land on exactly `1.0` — so this is
the common case, not a corner. A caller taking `sqrt` of that angle, or testing it `> 0.0`,
breaks.

The defect is confined to exactly `±1`, and provably so: `acos` leaves `1` like
`sqrt(2·eps)`, so one ulp inward the true value is already `1.5e-8` — seven orders above
the error floor. Answering the two endpoints directly is therefore a *complete* fix rather
than a patch on a gradient:

```sprout
if x == 1.0 then half_pi else if x == -1.0 then -half_pi else <half-angle form>
```

Branching beats clamping the result into range: it returns the correctly-rounded constant
instead of the nearest in-range approximation, it makes `acos(±1)` exactly `+0.0` and `pi`,
and it leaves NaN propagation untouched (`NaN == 1.0` is false, so out-of-domain inputs
still fall through to the path that produces NaN). Re-verified after the branch: zero
out-of-range results across the same 2M-sample sweep.

### 15.4 Rule 2 falls out, but only for this spelling

`|x| > 1` makes the radicand negative, `sqrt` returns NaN, and NaN propagates — Rule 2 for
free. The tidier-looking `atan2(x, sqrt(1 - x·x))` would silently return `pi/2` instead;
`docs/math-partiality-v0.md` §8 records why, and the test suite pins it.

### 15.5 Verification

`tests/stdlib/test_math_double.spr` covers: the standard angles, odd symmetry,
`asin(sin x)` / `acos(cos x)` round-trips, the `asin + acos == pi/2` identity, `is_nan` on
all four out-of-domain and both NaN inputs, exactness at `±1` and `0`, in-range one ulp
inside the endpoints, and POSIX's `asin(±0) = ±0` signed-zero clause. The endpoint
assertions were confirmed RED against the unbranched implementation — they are the
regression guard for §15.3, and `close`-style tolerance testing cannot see that defect.

The range guarantee is additionally swept in-repo rather than only in the throwaway C
oracle: `range_violations` counts inputs where `asin`/`acos` escape their POSIX range over
20001 samples of `[-1, 1]` (endpoints included) and must return `0`. It also goes RED
without the endpoint branch, so the guarantee is checked as a property, not just at the
handful of points a reviewer thought to name.

## 16. `acos` relative accuracy, and the accuracy table was wrong (2026-08-11)

§15 shipped `acos` with a documented caveat rather than a fix: absolute accuracy 1.8e-15,
but poor *relative* accuracy as `x -> 1`, where the result approaches 0 and the complement
`pi/2 - asin(x)` cancels. Closing that turned into two changes, because measuring the
accuracy table in order to correct one row showed a second row was wrong in a far more
dangerous direction.

### 16.1 The `acos` fix, and why the split point is not a tuning knob

```sprout
acos(x) = if x > 0.5 then 2 * asin(sqrt((1 - x) / 2)) else pi/2 - asin(x)
```

The half-angle identity comes from `sin(t/2) = sqrt((1 - cos t)/2)`. The obvious objection
is that it just relocates the cancellation — `1 - x` looks every bit as bad as
`pi/2 - asin(x)`. It is not, and the reason is **Sterbenz's lemma**: `a - b` is computed
*exactly* in binary floating point whenever `b/2 <= a <= 2b`. For `x` in `[0.5, 1]` that
condition holds, so `1 - x` carries **no** rounding error at all, and halving is exact
besides (a power of two), so `sqrt` receives an exactly-representable argument.

That is what fixes the threshold at `0.5`: it is the Sterbenz boundary. Below it the
subtraction would begin to round and the identity would buy nothing; above it the identity
is free. Verified rather than assumed — 0 of 52 sampled `1 - 2^-k` subtractions were
inexact.

Measured worst relative error over `x = 1 - 2^-k`, `k = 1..52`:

| | `pi/2 - asin(x)` | half-angle arm |
|---|---|---|
| worst relative error | **2.8e-08** | **4.7e-16** |
| at `k = 52` | 2.77e-08 | 0.0 |
| at `k = 32` | 2.18e-11 | 3.1e-16 |

Eight significant digits recovered, with no trade anywhere else: whole-domain worst
absolute error stays 1.8e-15, zero out-of-range results, and every exact value survives —
`acos(1.0)` is `+0.0`, `acos(-1.0)` is `pi`, `acos(0.0)` is `pi/2`, `acos(0.5)` is
bit-identical to libm. Rule 2 survives both arms (§15.4's reasoning covers the complement
arm; in the half-angle arm a real `x > 1` makes `1 - x` negative and `sqrt` yields NaN).

One incidental gain: `acos(1.0)` now reaches `+0.0` through `2*asin(sqrt(0.0))`, so the
range guarantee no longer depends solely on `asin`'s endpoint branch.

### 16.2 The accuracy table said `~1e-8` for `tan`. It is up to 98% wrong.

The module header bucketed `sin`, `cos`, `tan`, `atan` and `atan2` together at `~1e-8`.
Measuring each row separately — which §15.2 should have prompted, since it had already
found the bucket wrong for `asin`/`acos` — shows the grouping was hiding two errors.

`atan`/`atan2` were over-stated: 1.6e-11 whole-line and 8e-16 on `[-1, 1]`, not 1e-8 (and
`atan`'s own comment claimed ~1e-9). Harmless in direction, but it is what led the first
draft of `asin` to be documented at seven orders worse than it measures.

`tan` was **under**-stated, which is not harmless. It is `sin/cos`, so `cos`'s ~2.2e-8
*absolute* error becomes an unbounded *relative* error as `cos -> 0`:

| distance from `pi/2` | `tan(x)` | relative error |
|---|---|---|
| 5e-1 | 1.83e+00 | 1.7e-09 |
| 5e-4 | 2.00e+03 | 4.5e-05 |
| 5e-6 | 2.00e+05 | 4.5e-03 |
| 5e-8 | 2.00e+07 | **3.1e-01** |
| 5e-10 | 2.00e+09 | **9.8e-01** |

The failure is quiet: the result stays large, finite and plausible the whole way down, so
nothing distinguishes a good answer from a 98%-wrong one. The prior comment described the
hazard as a point singularity — "undefined at odd multiples of pi/2; callers in that domain
must guard" — which invites guarding against the *pole* when what is actually required is
bounding one's *distance* from it. Both the header and `tan`'s comment now carry the table
and say so.

The general lesson, and the reason this section exists rather than a one-line figure edit:
**a grouped accuracy row is a claim about every member, and it decays silently.** Nothing
fails when a documented bound drifts from the truth — no test asserts a doc comment. The
only way it surfaces is someone re-measuring, which happened here only because a *different*
function's figure looked implausible.
