# Numeric Type Classes — Design Draft

Status: **draft**. No implementation yet. This document exists to align on
goals and surface design decisions before writing any code.

> **Update 2026-08-06 — what shipped in the meantime, and two conflicts to resolve.**
>
> `Double` has landed (N2's prerequisite, "C runtime float support", is met), along with
> a pure-Sprout elementary-function suite: `sqrt`, `cbrt`, `exp`, `ln`, `log2`, `log10`,
> `log(x, base)`, `pow`, and trig. These are ordinary module functions, **not** class
> methods — N1 remains unimplemented, so there is still no `Numeric`/`Integer`/`Real`.
> Because one name cannot serve two types without overloading, the math surface was split
> by type instead: `stdlib.math` is the `Double` layer, `stdlib.math.int` the `Int` layer,
> both using the plain names. That was chosen partly to be forward-compatible with this
> draft — `stdlib.math` already uses the exact names §6.2's `Real` methods have, so the
> migration moves definitions into an instance rather than renaming call sites. Rationale:
> `docs/math-transcendental-v0.md` §4 and §7.
>
> **Conflict 1 — total vs `Maybe`.** §6.2 declares `Integer.mod` and `Real.pow` total
> (`-> a`). But the shipped `stdlib.math.int` returns `Maybe Int` from both, and
> `docs/math-partiality-v0.md` §5 commits to those signatures as *permanent* (the
> forward-compatibility promise is that an Exn-effect variant is added *alongside*, never
> replacing). Implementing §6.2 as written would break that promise. Needs a decision:
> either the class methods take different names (`div`/`rem`/`quot` already differ from
> `mod`), or the partiality doc's commitment is renegotiated. Note this is not merely a
> signature quibble — `Real.pow` returning `a` and `Integer.pow` returning `Maybe a`
> cannot both be one class method without associated types, which Sprout lacks.
>
> **Conflict 2 — §7.1 is now overdue, not pending.** §7.1 says the NaN/`Ord` decision is
> "needed before `Double` ships". `Double` shipped without it, so `Double` today has
> exactly one instance in the whole stdlib (`ToString`, `prelude.sprout:814`) — no `Eq`,
> no `Ord`. Two live consequences: `check_eq` does not type-check on `Double`
> (`stdlib/test.sprout` carries `check_approx` to work around it), and `stdlib.math`
> deliberately ships **no** `Double` `min`/`max`/`sign`, because they would force the
> decision. Tracked in `BACKLOG.md`.

---

## 1. Problem Statement

Sprout currently has one numeric type (`Int`) and no numeric typeclass.
The operators `+`, `-`, `*`, `/`, `%` lower directly to integer builtins.
There is no way to write a function that is generic over numeric types —
`sum_by`, `mean`, `dot_product`, and anything ML-adjacent must hard-code `Int`.

The consequence is that adding `Float` or `Double` later would require either
duplicating every numeric helper (`vec_sum_int`, `vec_sum_float`, …) or a
large retrofit. Getting the typeclass boundary right now, before a second
numeric type ships, costs almost nothing. Getting it wrong at that point costs
significant churn.

This document covers: what a mature multi-purpose language actually needs in
numeric types, how Haskell approached it and where it went wrong, what other
languages do, and a proposed approach for Sprout that is mathematically correct
under the hood while remaining instinctive to use.

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
- **Elementary functions**: `log`, `exp`, `sqrt` for log-transforms,
  box-cox, softmax — needed for feature engineering and ML preprocessing
- **Ordering and comparison**: `min`, `max`, `clamp`, percentile — needs `Ord`
- **Conversion**: `Int` to `Double` and back (floor, ceil, round) — pervasive
- **NaN / missing value semantics**: IEEE 754 `NaN` propagates through float
  arithmetic; pandas uses it as the canonical "missing" marker. This needs
  explicit design: does Sprout surface IEEE NaN directly or wrap it in `Maybe`?
  (See §7 Open Questions.)

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
Num a:         (+), (-), (*), abs, signum, fromInteger, negate
  Integral a:    quot, rem, div, mod, toInteger
  Fractional a:  (/), recip, fromRational
    Floating a:  pi, exp, log, sqrt, sin, cos, tan, …
    RealFrac a:  properFraction, truncate, round, ceiling, floor
  Real a:        toRational
```

### What goes wrong

**`Num` bundles too many unrelated operations.**
`abs` and `signum` don't generalise cleanly to `Complex`: the absolute value
of a complex number is a real number, not a complex number. Natural numbers
(no negation) and semirings (no subtraction) cannot implement `Num` honestly.

**`fromInteger` makes literals polymorphic — powerfully but magically.**
Writing `3` in Haskell is valid for `Int`, `Double`, `Complex Double`, or any
custom `Num` instance. This enables expression-level polymorphism (`pi + 1`)
but surprises newcomers: the error points at the literal when the real issue is
a missing instance somewhere else in the expression.

**No clean exact/approximate split.**
`Integer`, `Rational` (exact) and `Float`, `Double` (approximate) all share
`Num`, implying similar semantics. But `1/3 :: Rational` is exact;
`1/3 :: Double` is 0.3333…. The typeclass surface suggests they are
interchangeable when they are not.

**The class hierarchy grows into a thicket.**
`RealFloat` exists separately from `RealFrac` and `Floating` because POSIX
`isNaN`, `isInfinite`, and exponent-extraction functions needed a home. It is
a bolted-on extension to a hierarchy that was not designed for IEEE 754 from
the start.

---

## 4. What Other Languages Do

### Rust (`std::ops` + `num-traits`)

Arithmetic in `std` is pure operator overloading: separate `Add`, `Sub`,
`Mul`, `Div`, `Rem`, `Neg` traits, each with an `Output` associated type.
Composable and correct, but verbose in generic code:

```rust
fn dot<T: Add<Output=T> + Mul<Output=T> + Zero + Copy>(a: &[T], b: &[T]) -> T
```

### Swift

Swift has a clean layered hierarchy worth studying:

```
AdditiveArithmetic: zero, +, -
  Numeric: *             (no division — avoids the Int/Float ambiguity)
    SignedNumeric: negate()
    BinaryInteger: /, %, quotientAndRemainder, …
    FloatingPoint: /, sqrt, isNaN, isInfinite, infinity, nan, …
```

The key insight: `Numeric` deliberately excludes division, because integer
division and float division behave differently enough that bundling them in
one operator causes ambiguity and surprises. Downstream classes each add the
variant of division that applies to them.

### Scala / Spire

Spire uses the full abstract algebra hierarchy (`Semiring`, `Ring`,
`CommutativeRing`, `Field`, `EuclideanRing`, …). Mathematically principled,
excellent for library authors, steep learning curve for application developers.

---

## 5. Design Goals and the Two-Layer Principle

Sprout's goal: **mathematically correct under the hood, instinctive on the
surface, no ambiguity at any level**.

"Instinctive" means users write `sqrt(x)` and `[1.0, 2.0].mean()` without
thinking about type class constraints. "No ambiguity" means there is exactly
one meaning for every operator: `1/2` cannot silently be `0`.

These goals are compatible if the design has two layers:

**Internal layer** — algebraically correct building blocks used by the stdlib
implementation and advanced library authors. Uses precise mathematical
vocabulary. Rarely appears in user code or error messages.

**Surface layer** — three classes with plain English names that users and
library authors write in `where` constraints. Backed by the internal layer
algebraically, but the backing is invisible.

The surface classes are defined as superclass constraints over the internal
ones — no new methods of their own. The math is correct underneath; the names
are friendly on top.

---

## 6. Proposed Design

### 6.1 Internal Layer (algebraic building blocks)

```sprout
class Additive a {
  fn add(x: a, y: a) -> a
  fn zero() -> a
  fn negate(x: a) -> a      # additive inverse; sub(x,y) = add(x, negate(y))
}

class Multiplicative a {
  fn mul(x: a, y: a) -> a
  fn one() -> a
}
```

These are the algebraic primitives. They appear in the stdlib source and in
documentation for library authors writing truly generic numeric algorithms.
They do not appear in error messages for ordinary application code.

### 6.2 Surface Layer (user-facing names)

Three classes with plain names. Each is a superclass constraint that pulls in
the algebraic structure from §6.1 — no new methods at the `Numeric` level.

```sprout
# Any number: supports +, -, *, ==, <
# Algebraically: Ring + Ord  (Additive + Multiplicative + Eq + Ord)
class Numeric a where Additive a, Multiplicative a, Eq a, Ord a

# Whole-number arithmetic: integer division, modulo, conversion
# Algebraically: Euclidean domain
class Integer a where Numeric a {
  fn div(x: a, y: a) -> a       # floor division (toward -∞)
  fn mod(x: a, y: a) -> a       # always non-negative
  fn quot(x: a, y: a) -> a      # truncation division (toward zero)
  fn rem(x: a, y: a) -> a       # sign matches dividend
  fn to_double(x: a) -> Double  # lossless for Int/BigInt up to 2^53
}

# Real-valued arithmetic: division, elementary functions
# Algebraically: Ordered field + elementary function suite
class Real a where Numeric a {
  fn div(x: a, y: a) -> a
  fn sqrt(x: a) -> a
  fn exp(x: a) -> a
  fn log(x: a) -> a
  fn pow(base: a, exp: a) -> a
  fn sin(x: a) -> a
  fn cos(x: a) -> a
  fn tan(x: a) -> a
  fn pi() -> a
  fn from_int(x: Int) -> a
}
```

**On the name `Integer` (class):** distinct from the type `Int`. Users
encountering `where Integer a` read it as "a whole-number type" — which is
exactly the intent. The type `Int` implements this class; so will `BigInt`.

**On the name `Real`:** refers to the mathematical intent — these types model
real-valued arithmetic — not to a promise of infinite precision. `Double` is
not literally ℝ (it is discrete and bounded), but every `Double` value is
intended to represent a real number. When a user sees `no instance of Real for
Int`, the meaning is immediately clear: you need a decimal type, not a whole
number. The approximate nature of IEEE 754 is a property of the type, not a
contradiction of the class name.

**On the name `Numeric`:** a number that supports basic arithmetic. This is
the constraint most generic code will use. `where Numeric a` reads naturally
and covers all of `Int`, `Double`, `Float`, `BigInt`, `Decimal`.

### 6.3 Instance Table

| Type | `Numeric` | `Integer` | `Real` |
|---|---|---|---|
| `Int` | ✓ | ✓ | — |
| `BigInt` | ✓ | ✓ | — |
| `Double` | ✓ | — | ✓ |
| `Float` | ✓ | — | ✓ |
| `Decimal` | ✓ | — | ✓ (no trig — see §7.3) |
| `Natural` | partial — see §7.4 | — | — |
| `Complex Double` | ✓ | — | — (separate) |
| `Rational` | ✓ | — | ✓ (no trig) |

### 6.4 Operator Policy (no ambiguity)

The critical rule: `/` belongs exclusively to `Real`. It does not exist for
`Integer` types. Integer division is always a named function call.

| Expression | Desugars to | Requires |
|---|---|---|
| `a + b` | `add(a, b)` | `Numeric` |
| `a - b` | `add(a, negate(b))` | `Numeric` |
| `a * b` | `mul(a, b)` | `Numeric` |
| `a / b` | `Real.div(a, b)` | `Real` |
| `-a` (prefix) | `negate(a)` | `Numeric` |

Integer division — always named, never operator syntax:
`div(7, 2)` → `3`, `quot(-7, 2)` → `-3`, `mod(7, 2)` → `1`, `rem(-7, 2)` → `-1`.

Concrete consequences:

```sprout
1 + 2            # → 3        (Int, Numeric)
1.0 + 2.0        # → 3.0      (Double, Numeric)
1.0 / 2.0        # → 0.5      (Double, Real)
1 / 2            # TYPE ERROR: / requires Real; Int implements Integer.
                 #   For integer division use div(1, 2).
sqrt(9.0)        # → 3.0      (Double, Real)
sqrt(9)          # TYPE ERROR: sqrt requires Real; Int implements Integer.
                 #   Try sqrt(to_double(9)).
[1, 2, 3].sum()     # → 6    (Int, via Additive)
[1.0, 2.0].mean()   # → 1.5  (Double, via Real.div)
```

The error messages carry the UX load. They explain the problem and suggest the
fix — no mental model of the class hierarchy required.

### 6.5 Stdlib Functions That Become Generic

```sprout
fn sum(xs: f a) -> a where Foldable f, Numeric a =
  fold(add, zero(), xs)

fn sum_by(f: a -> n, xs: f a) -> n where Foldable f, Numeric n =
  fold(\ (acc, x) -> add(acc, f(x)), zero(), xs)

fn mean(xs: f a) -> a where Foldable f, Real a =
  Real.div(sum(xs), from_int(length(xs)))

fn dot(a: Vec n, b: Vec n) -> n where Numeric n =
  sum(vec_zip_with(mul, a, b))
```

The existing `vec_sum` and `vec_sum_by` become aliases over the generic forms.

### 6.6 Numeric Literals

`42` is always `Int`. `3.14` is always `Double`. No polymorphic literals.

Explicit conversion is required for other types: `to_double(42)`,
`BigInt.from_int(42)`. This is Rust's model and avoids the Haskell footgun
where a type error manifests at a literal rather than the actual problem site.
Revisit if verbosity becomes a real pain point.

---

## 7. Open Questions

### 7.1 NaN Handling

IEEE 754 `NaN` is unordered: `NaN == NaN` is `false`. This breaks the `Ord`
contract that `Numeric` requires via `Eq` and `Ord`. Options:

- **Ignore and inherit**: Surface IEEE semantics as-is; `Double` implements
  `Ord` with the caveat that NaN comparisons produce `false`. What Rust and
  Swift do via `PartialOrd` / `FloatingPoint`.
- **Separate `PartialOrd`**: Add `PartialOrd` as a superclass of `Ord`.
  `Double` implements `PartialOrd`, only total-order types implement `Ord`.
  `Numeric` requires `Ord`; a separate class covers NaN-carrying types.
- **Wrap**: `0.0 / 0.0` returns `Maybe Double`. Correct but makes ML
  arithmetic expressions verbose.

Decision needed before `Double` ships. Preference: `PartialOrd` split, so
`Numeric` stays total-order and `Double` sits in a `PartialNumeric` variant,
or NaN is simply excluded from valid `Double` values by convention.

### 7.2 Division Safety for `Integer`

`div(n, 0)` is undefined. Options: return `Maybe a`, panic at runtime, or
document as undefined behaviour. `Maybe a` is correct but makes arithmetic
expressions verbose. Panic with a clear message is pragmatic. Deferred.

### 7.3 `Decimal` and the Possible `Fractional`/`Real` Split

`Decimal` supports `div` and `from_int` but not `sin`/`cos`/`exp`.
If `Real` bundles all of these, `Decimal` cannot implement `Real` fully.
The clean fix is splitting `Real` into two:

```
Fractional a where Numeric a:   div, from_int         # Decimal, Double, Float
Real a where Fractional a:      sqrt, exp, log, sin, … # Double, Float only
```

`Fractional` covers "supports exact-enough division"; `Real` covers "supports
the full elementary function suite." Most statistical code needs `Fractional`;
signal processing and ML need `Real`.

This split is clean and worth adopting once `Decimal` ships. Until then,
`Fractional` and `Real` would be identical and the split adds no value.

### 7.4 `Natural` (unsigned integers)

`Natural` has no `negate` — it is an additive *monoid*, not a group. It
cannot implement `Additive` as defined, and therefore cannot be `Numeric`.
Options:

1. Split `Additive` into `AdditiveMonoid` (no `negate`) and `AdditiveGroup`
   (with `negate`). `Numeric` requires `AdditiveGroup`. Algebraically clean;
   `Natural` cannot be used where `Numeric` is expected — surprising to users.
2. `Natural` implements `Numeric` with `negate` clamping at zero (saturating
   subtraction). Pragmatic but dishonest.
3. Defer `Natural` until a concrete use case forces the decision.

Option 3 is the right answer right now.

### 7.5 Operator Routing Cost

Routing `+` through `Additive` class dispatch adds a dictionary-passing
overhead on every integer addition unless the compiler monomorphises it away.
The stage-1 compiler currently has no specialiser. A `@specialize` hint or
call-site monomorphisation step may be needed to avoid performance regressions
in integer-heavy inner loops. Measure before committing to N1.

---

## 8. Milestones

| # | Deliverable | Prerequisites |
|---|---|---|
| N1 | `Additive`, `Multiplicative` internal classes; `Numeric`, `Integer` surface classes; `Int` instances; `+`/`-`/`*` desugar through class dispatch; `/` removed from `Int` (becomes a type error) | Operator desugaring infrastructure |
| N2 | `Double` type + `Real` instance; `to_double`/`from_int` conversions | C runtime float support |
| N3 | Generic `sum`, `sum_by`, `mean`, `dot` in prelude; existing `vec_sum`/`vec_sum_by` become aliases | N1 + N2 |
| N4 | `Float` (f32) + `Real` instance | N2; demand-driven |
| N5 | `Decimal` type; revisit `Fractional`/`Real` split (§7.3) | Demand-driven (finance) |
| N6 | `BigInt` + `Integer` instance | Demand-driven (crypto/finance) |
| N7 | `Complex Double` + `Numeric` instance | Demand-driven (signal processing) |

N1 is the only milestone where new infrastructure is load-bearing.
N2–N7 are additive — each can ship independently once N1 is in place.
