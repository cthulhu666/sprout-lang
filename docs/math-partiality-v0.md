# Math Partiality Convention (v0)

Status: supporting design doc. This doc is the reference for the partiality
convention in `stdlib/math.sprout` — the rules (§2), the rationale, the prior-art
survey, and the alternatives considered. `docs/spec-v0.md` remains normative for
the language core (including the `!{IO}` effect surface this convention will later
build on); the per-function library policy below lives here.

## 1. Problem statement

Several `math` functions have inputs outside their mathematical domain, and each
one handled that case a different way:

| function | true domain | old behavior | strategy |
|---|---|---|---|
| `mod(v, m)` | `m > 0` | `Nothing` | `Maybe` |
| `pow(b, e)` (Int) | `e >= 0` | `Nothing` | `Maybe` |
| `sqrt(x)` | `x >= 0` | returned **`0.0`** | silent in-band lie |
| `tan(x)` | `x != pi/2 + k*pi` | `+-inf` (IEEE) | IEEE sentinel |

Functions added later under the same convention (§8): `ln`, `log2`, `log10`,
`log(x, base)` and `Double` `pow` all follow Rule 2.

Four functions, three incompatible strategies — and one of them (`sqrt`) returned
a *silent wrong answer*: `sqrt(-4.0) == 0.0` is indistinguishable from a real
result. The problem was never that the strategies differed; it was that no rule
was written down, and `sqrt` violated the only sensible one.

## 2. The convention (two domains, two rules)

A partial `math` function follows exactly one rule, chosen by its result type:

- **Rule 1 — Int out-of-domain returns `Maybe`.** `mod`, `pow` — now in
  `stdlib.math.int` (see §8). `Int` has no spare bottom value (every bit pattern is a
  valid integer), so an out-of-domain result must be surfaced explicitly. This is
  **interim**: see §5.
- **Rule 2 — Double out-of-domain returns IEEE-style `NaN` / `±inf`.** `sqrt`,
  `tan`, and since 2026-08-06 also `ln`, `log2`, `log10`, `log` and `Double` `pow`
  (see §8). `Double` *has* a bottom value, and IEEE NaN self-propagates through
  downstream arithmetic, so a numeric pipeline can check once at the end (with
  `math.is_nan`). This is **terminal** — it matches every mainstream language and
  will not migrate. Two caveats: (a) the Double layer is pure-Sprout (~1e-8
  accuracy), so it is IEEE *in spirit*, not bit-exact — the in-domain edges that
  matter (`sqrt(+inf) = +inf`, `sqrt(-0.0) = -0.0`, `sqrt(NaN) = NaN`) are handled
  explicitly, but general rounding is not IEEE-guaranteed; (b) the functions are
  **strict** — there is no roundoff tolerance, so a caller that may produce a
  slightly-negative value (a discriminant, `sqrt(a - b)`) gets `NaN`, and must
  clamp at the call site if it wants `0`. Clamping inside `sqrt` is deliberately
  rejected: it would reintroduce exactly the silent-`0.0` lie this change removed.

Nothing returns a silent in-band lie. The rules diverge by domain because the
capability differs: sentinels require a spare value the type has (`Double` does,
`Int` does not), so the split is forced, not arbitrary.

`math` exports `nan` (the quiet NaN) and `is_nan(x)` so callers can produce and
detect the Rule-2 sentinel without reinventing the `!(x == x)` idiom.

## 3. Prior-art survey

Each row is drawn from the language's primary reference; the links are in
§7 Sources.

**Integer divide / modulo by zero** — the whole mainstream fails loud; none
returns an optional:

| Language | Behavior |
|---|---|
| Rust | panics ("will panic if `rhs` is zero") |
| Go | run-time panic, `"integer divide by zero"` |
| OCaml | raises `Division_by_zero` |
| Java | throws `ArithmeticException` when the right operand is zero (JLS §15.17.2/§15.17.3) |
| Python | raises `ZeroDivisionError` |
| C | undefined behavior (C11 §6.5.5p5) |

Sprout's own `/` operator already **traps** (`ast_to_ir.finish_checked_div`
emits a `"division by zero"` panic), so `mod`'s `Maybe` is the outlier even
within Sprout.

**sqrt of a negative** — floating-point languages let NaN be the in-band answer:

| Language | Behavior |
|---|---|
| C / POSIX | domain error, **returns NaN**, `errno = EDOM` |
| Rust `f64::sqrt` | returns `NaN` |
| Java / JavaScript | returns `NaN` |
| Python `math.sqrt` | raises `ValueError` |

No language returns `0.0`. Rule 2 follows the C/Rust/Java/JS majority.

**Integer pow with a negative exponent:**

| Language | Behavior |
|---|---|
| Rust `i32::pow` | exponent typed **`u32`** — negative is unrepresentable |
| Haskell `(^)` | runtime error `"Prelude.^: negative exponent"` |
| Python `2 ** -1` | returns `0.5` (silently widens to float) |

## 4. Alternatives considered

- **Panic (trap) for Int.** Matches `/` and Rust/Go, total signature. Rejected as
  the *default* because it is non-recoverable: a bad divisor takes the whole
  process down, which is wrong for a server or actor loop. Retained conceptually
  as the "unhandled" case of the future Exn effect (§5).
- **Sentinel for Int.** Unavailable: `Int` has no spare value; a magic return
  (`0`, `-1`) is the silent-lie trap Rule 2 exists to forbid.
- **Refinement types** (`{v:Int | v > 0}`, Liquid Haskell / F* style). Would make
  `mod` total and check-free, but replaces syntactic unification with semantic
  subtyping backed by an SMT solver — undecidable in general, annotation-heavy,
  and it breaks Sprout's self-hosted bootstrap (no external solver in the seed).
  Out of scope; documented so it is not re-proposed casually.
- **Exceptions / algebraic effects.** The right long-term home (exceptions are the
  zero-shot/abortive corner of algebraic effect handlers; see
  `docs/effect-system-handlers-draft.md`). Deferred: it is a language-sized
  feature that must be designed on its own merits, not driven by one stdlib
  function.

## 5. Migration path (why Rule 1 is non-breaking)

Rule 1 is labeled interim, but adopting the future Exn effect will **not** break
the `Maybe` API. When the abortive `Exn` effect lands, effectful variants are
added *alongside* the `Maybe`-returning functions (the pattern of Rust's `/`
panicking while `checked_div` returns `Option`) — the existing signatures are
permanent. Because nothing that exists changes signature, there is no breaking
change to fear; the forward compatibility is a standing commitment, not a
pre-emptive rename.

Rule 2 needs no migration: `NaN` is the terminal end state for floats.

## 6. This iteration

Minimal first pass — coherence without overcommitment:

1. Wrote this convention down (§2) and mirrored it as a header comment in
   `stdlib/math.sprout`.
2. Fixed the one violator: `sqrt(x < 0)` now returns `NaN`, not `0.0`. In-domain
   IEEE edges are handled too — `sqrt(+inf) = +inf` (Newton alone would give
   `inf/inf = NaN`) and `sqrt(-0.0) = -0.0` (sign preserved). Regression tests in
   `tests/stdlib/test_math_double.spr`.
3. Exported `nan` and `is_nan` so callers can detect the Rule-2 sentinel, and a
   `tan`-pole test pins its out-of-domain behavior.
4. Internal tidy: `is_even` and `gcd_loop` call the total private
   `euclidean_remainder` directly instead of matching `mod`'s `Maybe` for a
   `Nothing` case that cannot occur.

No effect-system, refinement, panic, or signature changes were introduced beyond
the `sqrt` bugfix.

## 7. Sources

Primary references for §3. Integer divide/modulo and `sqrt`-of-negative rows were
checked against these:

- Rust `i32` (integer `/`/`%` panic; `pow` exponent `u32`): https://doc.rust-lang.org/std/primitive.i32.html
- Rust `f64::sqrt` (NaN for negative): https://doc.rust-lang.org/std/primitive.f64.html
- Go `test/zerodivide.go` (integer divide by zero panics): https://go.dev/test/zerodivide.go
- OCaml `Stdlib` (`Division_by_zero`): https://ocaml.org/manual/5.2/api/Stdlib.html
- Java JLS §15.17 (`ArithmeticException` on zero divisor): https://docs.oracle.com/javase/specs/jls/se21/html/jls-15.html
- Java `Math.sqrt` (NaN for negative): https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/lang/Math.html
- Python `ZeroDivisionError`: https://docs.python.org/3/library/exceptions.html
- Python `math.sqrt` (`ValueError`) / `**` negative-exponent → float: https://docs.python.org/3/library/math.html · https://docs.python.org/3/reference/expressions.html
- POSIX `sqrt(3)` (domain error, NaN, `EDOM`): https://man7.org/linux/man-pages/man3/sqrt.3.html
- MDN `Math.sqrt` (NaN for negative): https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Math/sqrt
- Haskell `Prelude` `(^)` (negative exponent errors): https://hackage.haskell.org/package/base/docs/Prelude.html
- Ada `Positive` subtype / range checks (`Constraint_Error`): http://www.ada-auth.org/standards/aarm12_w_tc1/html/AA-3-5-4.html

## 8. Follow-up: the module split and the new Rule-2 members (2026-08-06)

The convention above was written when one module, `stdlib.math`, held both numeric
types. It now holds only the `Double` layer; the `Int` surface moved to
`stdlib.math.int`. The rules did not change — but they now line up exactly with the
module boundary, which is a happier arrangement than it sounds:

- **Rule 1 governs `stdlib.math.int` entirely.** `mod` and `pow` there return `Maybe`.
- **Rule 2 governs `stdlib.math` entirely.** `sqrt` and `tan` were joined by `ln`,
  `log2`, `log10`, `log(x, base)` and a `Double` `pow`.

So "which rule applies?" is now answerable from the import line alone, rather than from
the return type of the individual function. Rationale for the split itself is in
`docs/math-transcendental-v0.md` §4.

New Rule-2 domains:

| call | result |
|---|---|
| `ln(x)` for `x < 0` | `NaN` |
| `ln(0.0)` | `-inf` (the pole, not an error) |
| `log2`/`log10`/`log` of a negative | `NaN`, inherited from `ln` |
| `log(x, 1.0)` | `±inf` — base 1 has no logarithm |
| `pow(x, y)` for `x < 0` with fractional `y` | `NaN` — no real value exists |

Two notes on how this interacts with §2's caveats.

**`cbrt` is not a Rule-2 function**, despite looking like `sqrt`'s sibling. A negative
argument is *in* domain (`cbrt(-8.0) == -2.0`), because the real cube root is defined on
the whole line. It is listed here only to forestall the natural assumption that it
mirrors `sqrt`.

**`Double` `pow` follows C99/IEEE F.9.4.4 rather than Python**, which matters for two
edges §2 does not cover: `pow(0.0, -1.0)` is `+inf` (Python raises `ValueError`), and
`pow(x, ±0)` / `pow(1.0, y)` are `1.0` even when the other operand is `NaN`. The latter
means those two cases must be tested *before* any NaN check — a NaN operand does not
poison them. This is a deliberate widening of §2's "NaN self-propagates" framing: it
propagates, except where IEEE says a total answer exists.

**Unresolved tension, recorded not settled.** `docs/numeric-types-v1-draft.md` §6.2
declares `Integer.mod` and `Real.pow` as **total** (`-> a`), which contradicts §5 above,
where the `Maybe`-returning signatures are a standing commitment. Whoever implements
those classes has to reconcile the two documents; nothing in this change picks a side.
