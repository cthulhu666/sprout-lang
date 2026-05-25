# Numeric Type Classes — Design Draft

Status: **draft**. No implementation yet. This document exists to align on
goals and surface design decisions before writing any code.

---

## 1. Problem Statement

Sprout currently has one numeric type (`Int`) and no numeric typeclass.
The operators `+`, `-`, `*`, `/`, `%` lower directly to integer builtins.
There is no way to write a function that is generic over numeric types —
`sum_by`, `mean`, `dot_product`, and anything ML-adjacent must hard-code `Int`.

The consequence is that adding `Float` later would require either duplicating
every numeric helper (`vec_sum_int`, `vec_sum_float`, …) or a large retrofit.
Getting the typeclass boundary right now, before a second numeric type ships,
costs almost nothing. Getting it wrong at that point costs significant churn.

This document covers: what a mature multi-purpose language actually needs in
numeric types, how Haskell approached it and where it went wrong, what other
languages do, and a proposed approach for Sprout.

---

## 2. What a Mature Multi-Purpose Language Needs

### 2.1 Numeric Types

| Type | Precision | Primary use |
|---|---|---|
| `Int` | 64-bit signed integer | Counting, indexing, protocol fields |
| `Float` (f32) | 32-bit IEEE 754 | Neural network weights, GPU-adjacent |
| `Double` (f64) | 64-bit IEEE 754 | Statistics, scientific computing, pandas default |
| `BigInt` | Arbitrary precision integer | Cryptography, large combinatorics, exact factorial |
| `Decimal` | Arbitrary precision fixed-point | Financial arithmetic — never use floats for money |
| `Natural` / `UInt` | 64-bit unsigned | Bit manipulation, protocol parsing, file sizes |
| `Complex n` | Parameterized over numeric `n` | Signal processing, FFT, physics simulations |
| `Rational` | Exact p/q | Symbolic math, exact statistics, rarely needed |

Sprout currently has: `Int`. Immediate priorities when the need arises:
`Double` first (unblocks statistics, data frames, ML), then `Float` (GPU/ML
memory efficiency), then `Decimal` (financial apps), then the rest.

### 2.2 What Is Needed to Write Pandas in Sprout

Pandas is fundamentally a typed column store with arithmetic and statistics.
The minimal numeric surface it exercises:

- **Field arithmetic**: `+`, `-`, `*`, `/` on `Double` — every aggregation,
  every rolling window, every normalisation step
- **Statistical functions**: `sum`, `mean`, `std`, `var`, `median` — these
  require division (`mean = sum / count`), so `Int` alone is not enough even
  for integer columns (mean of `[1, 2, 3]` is `2.0`, not `2`)
- **Transcendental functions**: `log`, `exp`, `sqrt` for log-transforms,
  box-cox, softmax — needed for feature engineering and ML preprocessing
- **Ordering and comparison**: `min`, `max`, `clamp`, percentile — needs `Ord`
- **Conversion**: `Int` to `Double` and back (floor, ceil, round) — pervasive
- **NaN / missing value semantics**: IEEE 754 `NaN` propagates through
  float arithmetic; pandas uses it as the canonical "missing" marker.
  This is a sharp edge that needs explicit design: does Sprout surface IEEE NaN
  directly or wrap it in `Maybe`? (See §7 Open Questions.)

For machine learning specifically (gradient descent, loss functions, layer math):

- `Double` or `Float` arithmetic with `exp`, `log`, `sqrt`, `pow`
- Generic `dot_product`, `mat_mul` that work on any `Numeric n`
- Efficient `Vec n` and future `Matrix n` types
- No `BigInt`, no `Decimal` — ML is approximate by design, float is fine

### 2.3 What Is Needed to Write a Finance Library

Finance is the opposite of ML: correctness over speed.

- `Decimal` for all monetary values — IEEE 754 `0.1 + 0.2 ≠ 0.3` is not
  acceptable when counting money
- `BigInt` for interest calculations that accumulate over years at high precision
- Conversion between `Decimal` and `Double` with explicit rounding modes
- No approximation: `mean` of integer counts should return `Rational` or
  `Decimal`, not `Double`

---

## 3. How Haskell Does It (and Why It's Not Great)

Haskell's numeric hierarchy, simplified:

```
Num a:       (+), (-), (*), abs, signum, fromInteger, negate
  Integral a:    quot, rem, div, mod, toInteger
  Fractional a:  (/), recip, fromRational
    Floating a:  pi, exp, log, sqrt, sin, cos, tan, …
    RealFrac a:  properFraction, truncate, round, ceiling, floor
  Real a:        toRational
```

### What goes wrong

**`Num` bundles too many unrelated operations.**
`abs` and `signum` don't generalise cleanly to `Complex`: the absolute value of
a complex number is a real number, not a complex number. Haskell's instance for
`Complex` is technically lawful but practically confusing. Natural numbers (no
negation) and semirings (no subtraction) cannot implement `Num` honestly.

**`fromInteger` makes literals polymorphic — powerfully but magically.**
Writing `3` in Haskell is valid for `Int`, `Double`, `Complex Double`, or any
custom `Num` instance. This enables expression-level polymorphism (`pi + 1`)
but surprises newcomers: `show (3 :: MyType)` compiles fine until `MyType`
doesn't implement `Show`, producing an error that points at the literal, not
the display call.

**No clean exact/approximate split.**
`Integer`, `Rational` (exact) and `Float`, `Double` (approximate) all share
`Num`, implying similar semantics. But `1/3 :: Rational` is exact;
`1/3 :: Double` is 0.3333…. The typeclass surface suggests they are
interchangeable when they are not.

**The hierarchy grows into a thicket.**
`RealFloat` exists separately from `RealFrac` and `Floating` because POSIX
`isNaN`, `isInfinite`, and exponent-extraction functions needed a home.
It's a bolted-on extension to a hierarchy that wasn't designed for IEEE 754
from the start.

**`Num Bool` exists (via a GHC extension), `Num` for vectors does not.**
The class cannot cleanly express "element-wise arithmetic on a container" —
there's no law that prohibits it, but the scalar-biased operation set (`abs`,
`signum`) makes it awkward.

---

## 4. What Other Languages Do

### Rust (`std::ops` + `num-traits`)

Arithmetic in `std` is pure operator overloading: separate `Add`, `Sub`,
`Mul`, `Div`, `Rem`, `Neg` traits, each with an `Output` associated type.
The `num-traits` crate adds `Zero`, `One`, `Num`, `Integer`, `Float`.

Consequence: a generic numeric function signature becomes:
```rust
fn dot<T>(a: &[T], b: &[T]) -> T
where
    T: Add<Output=T> + Mul<Output=T> + Zero + Copy
```
Correct and composable, but verbose. In practice, `num-traits::Num` is a
convenience re-export of the common combination.

### Swift

Swift has a clean, layered hierarchy that's worth studying:

```
AdditiveArithmetic: zero, +, -
  Numeric: *          (no division — avoids the Int/Float divide ambiguity)
    SignedNumeric: negate()
    BinaryInteger: /, %, &+, &-, quotientAndRemainder, …
    FloatingPoint: /, sqrt, isNaN, isInfinite, infinity, nan, ulp, …
```

The key insight is that `Numeric` deliberately excludes division, because
integer division and float division behave differently enough that bundling
them causes confusion. Each downstream class adds the variant that applies.

### Scala / Spire

Spire uses the full abstract algebra hierarchy (`Semiring`, `Ring`,
`CommutativeRing`, `Field`, `EuclideanRing`, …). Mathematically principled,
excellent for library authors building generic algorithms, steep learning curve
for application developers. Appropriate for a language with a strong
type-theory culture; probably overkill for Sprout's current audience.

---

## 5. Naming: Why Not `Addable`

`Addable` describes only one operation. A name for the class should describe
what the *type* is, not what one of its operations does.

Rejected names:
- `Addable` — too narrow, describes one method not the concept
- `Summable` — implies collection-level aggregation, not element arithmetic
- `Arith` — abbreviation, unclear scope
- `Number` — reads like a type, not a class
- `Ring` — algebraically precise, unfamiliar to most programmers

**Recommendation: `Numeric` for the user-facing class.**

- Swift uses `Numeric` for the same role: "this type represents a quantity and
  supports arithmetic"
- Scala's `Numeric[T]` typeclass is the standard generic-arithmetic abstraction
  in that ecosystem
- Reads naturally in constraint position: `where Numeric a`
- Broad enough to cover `Int`, `Float`, `Double`, `Decimal`, `BigInt`

For the underlying algebraic decomposition (used by library authors):
`Additive` (addition + zero + negate) and `Multiplicative` (multiplication +
one) as separate classes that `Numeric` requires as superclasses.

---

## 6. Proposed Design

### 6.1 Class Hierarchy

```sprout
# Additive monoid + inverse: supports +, - (via negate), and a zero element
class Additive a {
  fn add(x: a, y: a) -> a
  fn zero() -> a
  fn negate(x: a) -> a        # additive inverse; sub(x,y) = add(x, negate(y))
}

# Multiplicative monoid: supports * and a one element
class Multiplicative a {
  fn mul(x: a, y: a) -> a
  fn one() -> a
}

# Combined convenience class: the thing most code will constrain on
class Numeric a where Additive a, Multiplicative a, Eq a, Ord a {
  # No new methods — this is a constraint alias
}

# Integer-specific: exact division, remainders, conversions
class Integral a where Numeric a {
  fn quot(x: a, y: a) -> a    # truncates toward zero
  fn rem(x: a, y: a) -> a
  fn div_floor(x: a, y: a) -> a  # floors toward -∞ (matches `mod`)
  fn mod_floor(x: a, y: a) -> a
  fn to_int(x: a) -> Int
}

# Floating-point: division, IEEE 754 predicates, conversions
class Fractional a where Numeric a {
  fn div(x: a, y: a) -> a     # real division
  fn to_double(x: a) -> Double
  fn from_double(x: Double) -> a
}

# Transcendental: math functions — only for floats in practice
class Transcendental a where Fractional a {
  fn sqrt(x: a) -> a
  fn exp(x: a) -> a
  fn log(x: a) -> a
  fn pow(base: a, exp: a) -> a
  fn sin(x: a) -> a
  fn cos(x: a) -> a
  fn tan(x: a) -> a
  fn pi() -> a
}
```

Planned instances when each type ships:

| Type | Classes |
|---|---|
| `Int` | `Additive`, `Multiplicative`, `Numeric`, `Integral` |
| `Float` | `Additive`, `Multiplicative`, `Numeric`, `Fractional`, `Transcendental` |
| `Double` | same as `Float` |
| `BigInt` | `Additive`, `Multiplicative`, `Numeric`, `Integral` |
| `Decimal` | `Additive`, `Multiplicative`, `Numeric`, `Fractional` (no `Transcendental`) |
| `Natural` | `Additive` (no `negate`), `Multiplicative` |
| `Complex n` | `Additive`, `Multiplicative`, `Numeric` (where `Numeric n`) |

Note: `Natural` cannot implement `Additive` as defined above because it has
no `negate`. This requires either a separate `AdditiveMonoid` class (no
`negate`) with `Additive` as a subclass, or accepting that `Natural` sits
outside the main hierarchy. Decision deferred to when `Natural` is needed.

### 6.2 Operator Desugaring

Like `++` desugars to `append`, the arithmetic operators desugar:

| Operator | Desugars to |
|---|---|
| `a + b` | `add(a, b)` |
| `a - b` | `add(a, negate(b))` |
| `a * b` | `mul(a, b)` |
| `a / b` | `div(a, b)` (requires `Fractional`) |
| `-a` (prefix) | `negate(a)` |

Integer `div`/`mod` remain as named functions (`div_floor`, `mod_floor`,
`quot`, `rem`) — no operator syntax, consistent with current practice.

This requires the infer/lowering pass to route `+` through the `Additive`
class dispatch rather than the current `add_ints` direct call.

### 6.3 Numeric Literals

Currently `42` is always `Int`. Making literals polymorphic (Haskell-style)
is the most powerful option but adds inference complexity and produces
confusing error messages when the literal type cannot be determined.

**Preferred approach**: keep `42 : Int` as the default; require explicit
conversion for other types (`Double.from_int(42)`, `BigInt.from_int(42)`).
This is Rust's model and avoids most of the Haskell footguns. Revisit if the
verbosity becomes a real complaint.

### 6.4 Stdlib Functions That Become Generic

Once `Numeric` exists, these functions become generic over their container
and element type:

```sprout
fn sum(xs: f a) -> a where Foldable f, Additive a =
  fold(add, zero(), xs)

fn sum_by(f: a -> n, xs: f a) -> n where Foldable f, Additive n =
  fold(\ (acc, x) -> add(acc, f(x)), zero(), xs)

fn mean(xs: f a) -> a where Foldable f, Fractional a =
  div(sum(xs), from_double(float_of_int(length(xs))))

fn dot(xs: f a, ys: f a) -> a where Foldable f, Numeric a =
  sum(zip_with(mul, xs, ys))
```

The `vec_sum`, `vec_sum_by` in the prelude become convenience aliases over the
generic forms rather than standalone functions.

---

## 7. Open Questions

### 7.1 NaN Handling

IEEE 754 `NaN` is unordered: `NaN == NaN` is `false`, `NaN < 1.0` is
`false`, `NaN > 1.0` is also `false`. This breaks `Ord` — a `Float` that
implements `Ord` is technically lawful only if NaN never appears.

Options:
- **Ignore and inherit**: Surface IEEE semantics as-is; `Float` implements
  `Ord` with the caveat that NaN comparisons produce `false`. This is what
  Rust and Swift do for `PartialOrd`/`FloatingPoint`.
- **Wrap**: Require `Option Float` or `Result` for operations that might
  produce NaN (e.g. `0.0 / 0.0`, `sqrt(-1.0)`). Correct but verbose;
  makes ML code painful.
- **Separate `PartialOrd`**: Add a `PartialOrd` superclass of `Ord` that
  allows comparison to fail. `Float` implements `PartialOrd`, only total-order
  types implement `Ord`.

Decision needed before `Float` ships.

### 7.2 Division Safety

`a / 0` is undefined for integers (crash or undefined behavior) and `Inf` for
floats. Options: `div` returns `Maybe a`, panics, or returns a sentinel.
`Maybe a` is correct but makes arithmetic expressions verbose.
Rust panics in debug, wraps in release. Deferred.

### 7.3 Operator Overloading Cost

Routing `+` through the `Additive` typeclass adds a dictionary-passing
overhead on every integer addition unless the specialiser monomorphises it
away. The stage-1 compiler currently has no specialiser. A temporary
`@inline` or monomorphise-at-call-site hint may be needed to avoid
performance regressions in integer-heavy inner loops.

### 7.4 `fromInteger` / Numeric Literals

Deferred: the explicit-conversion approach (`Double.from_int(42)`) is
preferred for now. Revisit once there is a concrete use case where the
verbosity blocks natural expression.

### 7.5 `Complex` Parametrisation

`Complex n` is a record `{ re: n, im: n }` where `n` is `Fractional`.
Implementing `Multiplicative` requires `add` and `mul` on `n` — a
straightforward constraint. The interesting question is `abs(z)` for
complex `z`: mathematically it's `sqrt(re*re + im*im)`, which is `n` not
`Complex n`. This means `abs` cannot live in `Additive` as defined — it needs
its own class or a separate `Normed` class. Deferred.

---

## 8. Milestones

| # | Deliverable | Prerequisites |
|---|---|---|
| N1 | `Additive`, `Multiplicative`, `Numeric` class declarations + `Int` instances; `+`/`-`/`*` desugar through class dispatch | Operator desugaring infrastructure |
| N2 | `Double` type + `Fractional`, `Transcendental` instances; `double_of_int` conversion | C runtime float support |
| N3 | Generic `sum`, `sum_by`, `mean`, `dot` in prelude; `vec_sum`/`vec_sum_by` become aliases | N1 + N2 |
| N4 | `Float` (f32) + instances | N2; demand-driven |
| N5 | `Decimal` type + `Fractional` instance (no `Transcendental`) | Demand-driven (finance) |
| N6 | `BigInt` + `Integral` instance | Demand-driven (crypto/finance) |
| N7 | `Complex Double` + instances | Demand-driven (signal processing) |

N1 is the only milestone where the typeclass infrastructure is load-bearing.
N2–N7 are additive — each can ship independently once N1 is in place.
