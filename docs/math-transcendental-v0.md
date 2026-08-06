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
hyperbolics, `asin`/`acos`, `expm1`/`log1p`, `sigmoid` (no caller yet); bit-exact IEEE
rounding (the layer is IEEE *in spirit*, per partiality Rule 2); implementing the
`Numeric`/`Real` classes of `docs/numeric-types-v1-draft.md` (compiler-sized, §7).

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

- **`exp(x)`** — `k = round(x/ln2)`, `r = x − k·ln2` so `|r| ≤ ln2/2`; Taylor through
  `r¹²`, Horner-nested; scale by `2^k` with exact doublings.
- **`ln(x)`** — halve or double into `[1/√2, √2)`, accumulating `±ln2`; then the atanh
  series `2(s + s³/3 + …)` through `s¹⁵` with `s = (m−1)/(m+1)`. Centring on `√2` rather
  than `[1,2)` caps `|s|` at 0.172 instead of 1/3 — half the terms for equal accuracy.
- **`log2`/`log10`/`log`** — `ln` divided by a constant, or by `ln(base)`.
- **`cbrt(x)`** — odd symmetry on the sign; reduce by exact factors of 8 into `[1,8)`;
  Newton `g' = (2g + x/g²)/3`. Deliberately **not** `exp(ln(x)/3)`, so `cbrt` neither
  inherits `exp`/`ln` truncation error nor depends on them.
- **`pow(x, y)`** — C99/IEEE edge cases (§6), then exact binary exponentiation for an
  integer `|y| ≤ 1024`, else `exp(y·ln x)`.

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
| `ln` | 5.6e-17 absolute | near `1.0` |
| `cbrt` | 1.3e-14 | `1e-300 … 1e300`; exact on perfect cubes |
| `pow` | **exact** | integer exponents |
| `pow` | 1.1e-13 | fractional exponents |

That is ~5 orders better than the `~1e-8` the module previously advertised, so the
header contract was tightened to `~1e-13`.

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
| `ln` | 2.6 ns | 1.6 ns | 1.6x |
| `log10` | 2.7 ns | 1.7 ns | 1.6x |
| `cbrt` | 22.2 ns | 1.2 ns | 18.5x |
| `pow` fractional | 36.5 ns | 5.6 ns | 6.5x |
| `pow` integer | 6.5 ns | 5.6 ns | 1.2x |

**Conclusion: no new builtin, and `runtime/APPROVED_BUILTINS` is untouched.**
`AGENTS.md` "Builtin vs Stdlib" rule 6 requires a concrete measured bottleneck, and a
ratio against hand-tuned libm is not one. `ln` and integer `pow` — the rows bulk callers
hit — are at 1.2–1.6x, and integer `pow` is *more accurate* than libm's (bit-exact,
where libm pays the full `exp(y·ln x)`). At 9–37ns a call is dwarfed by any surrounding
allocation or I/O in the work this layer serves; a Δv or emittance evaluation is one or
two calls. Should a workload ever prove bottlenecked, the escalation named in
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
- In-repo: `docs/math-partiality-v0.md` (Rules 1 and 2),
  `docs/numeric-types-v1-draft.md` §6.2/§7.1/§8 (the class design and its open
  questions), `docs/spec-v0.md` §8 (normative Int surface).
