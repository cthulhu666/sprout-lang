# Double → Int conversion, and the rounding family (v0)

**Status:** implemented in `stdlib/math.sprout`. Supporting doc — `docs/spec-v0.md` remains
normative for the language core; nothing here changes the language.

## 1. Problem statement

The stdlib had **no** `Double → Int` conversion and **one** rounding function
(`math.floor : Double -> Double`). There was no `ceiling`, no `truncate`, no `round` at any
type. A private `round_to_int` existed inside `stdlib/math.sprout`, added for `exp`'s scale
factor, deliberately unexported.

Downstream (`uncharted-suns`, the one real Sprout consumer) the only conversion available
was `gfx.double_to_int`, in the graphics shim. That is not merely inconvenient: a module
importing `loam.gfx` drags in every `extern fn gfx_*`, which resolve only in a GL build,
so **any module needing `Double → Int` could not appear in a headless test suite**. Star
catalogue arithmetic — sector indexing, sector labels, corridor padding, all pure
computation with no rendering — was pinned to the graphics side of the module boundary.

Reported in `docs/bug-report-uncharted-suns-2026-08-14.md` §4.

## 2. Goals and non-goals

**Goals.** A conversion usable from a headless build. A complete, conventionally-named
rounding family. One place — and only one — where out-of-range inputs are decided.

**Non-goals.** No new builtin or runtime symbol. No change to `round_to_int`, which is on
`exp`'s hot path. No convenience wrappers (`floor_to_int` and friends) on speculation.

## 3. Prior-art survey

Verified against primary sources, 2026-08-14.

| Language | Rounding | Float→int conversion | Out of range | NaN |
|---|---|---|---|---|
| [Rust](https://doc.rust-lang.org/reference/expressions/operator-expr.html) | `trunc`/`floor`/`ceil`/`round` on `f64`, all → `f64` | `as` (total), `to_int_unchecked` (unsafe) | saturates to min/max | `0` |
| [Go](https://go.dev/ref/spec#Conversions) | none in the language | `int(f)`, truncating | "the conversion succeeds but the result value is implementation-dependent" | same |
| [Haskell 2010](https://www.haskell.org/onlinereport/haskell2010/haskellch6.html) | — | `truncate`/`round`/`ceiling`/`floor`, four `RealFrac` methods | n/a — targets unbounded `Integer` | n/a |

**Two things the survey settles, and one it does not.**

*Rounding and conversion are separate layers.* Rust makes `trunc`/`floor`/`ceil`/`round`
all `f64 -> f64` and conversion a distinct operation. Sprout does the same.

*Rounding direction is a solved naming question.* Haskell's four names are the ones every
reader recognises, and Sprout adopts them — with the caveat that `round` here is
**ties-to-even** (Haskell's rule), not Rust's ties-away-from-zero.

*Out of range is NOT settled by prior art.* Haskell's family targets unbounded `Integer`,
so it never faces the question. Go explicitly declines to answer. Rust saturates — but
`as` was a pre-existing **total** operator that had to stay total, so saturation was a
repair for undefined behaviour rather than a fresh design choice, and Rust std offers no
checked float→int conversion at all (there is no `TryFrom<f64> for i64`).

The decision therefore rests on Sprout's own precedent, which is unambiguous:
`parse_int : String -> Maybe Int` is a fallible conversion to `Int` and already returns
`Maybe`; `vec_get`/`vec_get_or` already establish the `Maybe`-plus-default pair.

## 4. Design

```sprout
export fn floor(x: Double) -> Double      # pre-existing
export fn ceiling(x: Double) -> Double
export fn truncate(x: Double) -> Double
export fn round(x: Double) -> Double      # nearest, ties to even
export fn to_int(x: Double) -> Maybe Int
export fn to_int_or(x: Double, fallback: Int) -> Int
```

The rounding family is **total**: it stays in `Double`, so NaN and the infinities pass
through and no range question arises. `to_int` is the **one** partial function, so it is
the one place deciding what NaN, ±inf and `|x| >= 2^63` mean.

Callers compose: `to_int(floor(x))` floors, `to_int(ceiling(x))` rounds up. `to_int` alone
truncates toward zero — Rust's, Go's and C's convention — so it differs from
`to_int(floor(x))` **only on negative non-integral input**, which is exactly the sector
indexing case in the bug report.

Convenience wrappers are deliberately absent. Adding `floor_to_int` later is
backwards-compatible; removing it is not.

## 5. Implementation

Pure Sprout over the existing `double_to_bits` intrinsic. No builtin, no runtime symbol,
no `APPROVED_BUILTINS` entry, no change to the bootstrap seed's declare list.

`ceiling` is `floor` with the comparison flipped. `truncate` is `floor` on the positive
side and `ceiling` on the negative — its guard is `x < 0.0`, false for `-0.0`, so the sign
of a negative zero survives. `round` exposes the existing private `round_nearest`.

`to_int` truncates, guards, then converts. The conversion cannot use `round_to_int_pos`
directly: that adds `2^52`, and at magnitudes at or above `2^52` there is no mantissa room
left, so it returns garbage. The magnitude is split at `2^32` into `floor(m / 2^32)` and
the residual, putting **both halves under `2^52`** where the magic constant is exact.
Every step is exact in `Double` — dividing by a power of two only shifts the exponent, and
the residual is a difference of integers below `2^32`.

### Two things a correct-looking implementation gets wrong

**NaN cannot be folded into the range guard.** Every comparison against NaN is false, so
`t >= two63 || t < -two63` is false for a NaN and falls through to convert garbage. NaN is
tested first, via `is_nan` (itself written `!(x == x)` for the same reason).

**The range boundary is asymmetric.** `Int`'s maximum, `2^63 - 1`, is *not representable
as a Double* — spacing at that magnitude is 1024, so the neighbouring Doubles are
`2^63 - 1024` and `2^63` itself. `Int`'s minimum, `-2^63`, is a power of two and **is**
exact, so it converts — and it cannot go through the negate-and-re-sign path, whose
positive half would be `2^63`, so it is answered directly.

## 6. Impact

**Syntax / type system / spec:** none. Six stdlib functions; no language change.

**Error messages:** none. `to_int` reports absence through `Maybe`, not a diagnostic.

**Compatibility:** purely additive. No existing name changes meaning; `math.floor` keeps
its `Double -> Double` signature, which is why nothing new competes for the name.

## 7. Tests

`tests/stdlib/test_math_to_int.spr`, 43 assertions. Beyond the ordinary cases:

- both boundary edges — `to_int(2^63)` → `Nothing`, `to_int(-2^63)` → `Just minInt`,
  and `2^63 - 1024` converting on both signs
- `to_int(NaN)`, `to_int(±inf)`, `to_int(-0.0)`
- the composition contrast: `to_int(-3.7)` → `Just -3` against
  `to_int(floor(-3.7))` → `Just -4`
- three round-trip sweeps asserting `to_int(to_double(i)) == Just i` — over `±2^k` for
  `k = 0..62`, densely over `-5000..5000`, and at `2^k ± 1` for `k = 0..52`

The sweeps exist because the split is invisible to point assertions. Removing it leaves
**37 of 43 assertions still passing** — everything below `2^52` is unaffected — and only
the two boundary points and the sweeps fail. Both this mutation and folding NaN into the
range guard were run against the suite and confirmed to fail it.
