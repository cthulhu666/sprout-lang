# Double bit access (v0)

**Status:** experimental. Normative surface is `docs/spec-v0.md` §8; this document
holds the rationale.

## 1. Problem statement

`stdlib.math`'s range reduction is **13–20× libm** at the extremes of the exponent
range — `exp` near x=688 at 21.7 ns, `ln(1e-300)` at 20.9 ns, `sqrt(1e300)` at
19.1 ns — against 1–2 ns for normal magnitudes.

The cause is not the series and not the Newton iteration. libm obtains a double's
binary exponent with one load, shift and mask: **O(1)**. Sprout cannot express that
at all, so reduction walks the exponent in strides: the coarse power-of-two ladders
cut it from ~1070 steps to ~20, and ~20 compare-multiply-branch steps against one
mask is exactly the 13–20×.

**Direct evidence it is the reduction.** Making the Newton iteration ~3× faster
(2026-08-06, fixed 6-pass rewrite) moved `sqrt(1e300)` from 19.0 → 19.1 ns — i.e.
not at all — while normal-magnitude `sqrt` went 8.16 → 2.75 ns.

The only `Double`↔`Int` bridge in the language is `to_double` (a bare `sitofp`), and
before this change `double_to_string` was the only code in the entire runtime that
reinterpreted a Double's bits.

## 2. Goals and non-goals

**Goals**

- Make the binary exponent of a `Double` obtainable in O(1) from Sprout.
- Keep `stdlib.math` free of libm. That property is the module's premise, not a
  detail: the whole layer exists to be pure Sprout.
- Add no runtime symbol and no `runtime/APPROVED_BUILTINS` entry.

**Non-goals**

- **Exact shortest-round-trip `Double` printing.** `BACKLOG` previously listed this
  as a payoff of bit access. That is **false**, and was disproved directly: a
  ~45-line pure-Sprout float→decimal converter round-tripped only 5 of 7 sampled
  values. The blocker there is exact **wide-integer** arithmetic (128-bit or
  bignum, as Ryū/Grisu/Dragon4 require), which bit access does nothing for. See
  `runtime/APPROVED_BUILTINS` on `double_to_string`.
- **`Eq`/`Ord Double`.** Gated on the NaN-ordering *decision*, not on any missing
  capability — **decided 2026-08-29**, `docs/eq-ord-double-v0.md`. The prediction
  held: the instances needed no bit access at all. They are written over `==`,
  `<` and `! (x == x)`, which is also why they could live in the prelude, where
  `double_to_bits` deliberately does not.
- **NaN classification.** `is_nan` via `! (x == x)` is the canonical portable
  idiom, not a workaround; it needs nothing added.
- General-purpose integer bitwise operators (`&`, `|`, `<<`, `>>`). Sprout has
  none, and this change deliberately does not add any — see §5.

## 3. Prior-art survey

Every claim below is checked against the language's own reference.

| Language | Raw bit access | Structured decomposition |
|---|---|---|
| **Rust** | `f64::to_bits(self) -> u64`, `f64::from_bits(v: u64) -> f64` — **safe**, stable 1.20 (`const` since 1.83). Docs: "currently identical to `transmute::<f64, u64>(self)`" | none in `std` — left to `libm`/`num-traits` |
| **Go** | `math.Float64bits(f float64) uint64`, `math.Float64frombits(b uint64) float64` | `math.Frexp(f) (frac float64, exp int)`, `math.Ldexp(frac, exp) float64` |
| **Java** | `Double.doubleToLongBits`, `Double.doubleToRawLongBits`, `Double.longBitsToDouble` | none |
| **Haskell** | `castDoubleToWord64 :: Double -> Word64`, `castWord64ToDouble` (`GHC.Float`) | `decodeFloat :: a -> (Integer, Int)`, `encodeFloat`, `exponent`, `significand` — `RealFloat` class methods |
| **C** | `union` / `memcpy` type punning | `frexp` / `ldexp` (`math.h`) |

**Consensus: unanimous.** Every language surveyed exposes raw IEEE bit access, and
in every managed one it is an ordinary **safe** library function — Rust marks it
safe while documenting it as transmute-equivalent, which is the strongest available
statement that this is not considered an unsafe escape hatch.

**The one divergence is instructive.** Go and Haskell *also* ship the structured
`Frexp`/`decodeFloat` decomposition; Rust's `std` does not. So the settled shape is
that **bit access is the primitive and `frexp` is a convenience layered on it** —
which is precisely why a `frexp`/`ldexp` C builtin would be redundant here.

**One design detail prior art settles.** Java's split between `doubleToLongBits`
(collapses every NaN payload to canonical `0x7ff8000000000000`) and
`doubleToRawLongBits` (preserves the payload) shows the NaN question is real. For
exponent extraction we want the **raw** behaviour, and a bitcast is raw by
construction — so Sprout gets one function, matching Rust/Go/Haskell rather than
Java's pair.

## 4. Decision, and why the alternatives lost

Three options were on the table (`BACKLOG` "The `*_wide` gap is range reduction").
Two of the three rested on claims that turned out to be false when measured.

### (a) Lower to `llvm.frexp` / `llvm.ldexp` — REJECTED

The backlog called this "a *codegen* change, so `APPROVED_BUILTINS` stays
untouched", and `stdlib/math.sprout`'s own header names an LLVM intrinsic as the
sanctioned escalation. True on paper, false in effect. Verified:

```
llvm.frexp.f64.i32  →  bl _frexp
llvm.ldexp.f64.i32  →  b  _ldexp
undefined symbols in the object file:  _frexp  _ldexp
```

These intrinsics **do not lower to inline instructions on arm64 — they lower to
libm calls.** The runtime today pulls *zero* libm math symbols and no link line in
the repo passes `-lm`, so this option introduces a brand-new external dependency:
`-lm` on Linux, and the end of "`stdlib.math` is libm-free."

It is also self-defeating. Once the build links libm for `frexp`, the argument
against calling libm's `exp` and `log` directly collapses — we would have conceded
the module's founding premise to buy back one narrow case.

### (b) Expose Double bit access — CHOSEN

The backlog called this "more general … but a real surface-area commitment",
implying it was the heavier build. **It is by far the lightest**, for a reason the
backlog missed:

> Under Sprout's i64-uniform value ABI, `Double` and `Int` are **already the same
> LLVM type**. Verified — these emit byte-identical code:
>
> ```llvm
> define i64 @id_d(i64 %x) { ret i64 %x }   ; fn id_d(x: Double) -> Double = x
> define i64 @id_i(i64 %x) { ret i64 %x }   ; fn id_i(x: Int) -> Int = x
> ```

So a `Double`↔`Int` bitcast is not a cheap operation, it is **no operation** —
zero instructions, purely a type-system surface change. There is no `bitcast`
instruction to emit, because there is nothing to convert.

`to_double` is the exact implementation precedent: an `extern fn` declared only to
carry a type, intercepted in `ast_to_ir` (`translate_to_double_call`) and lowered
to an inline `sitofp` with **no runtime symbol and no `APPROVED_BUILTINS` entry**
(documented at `stdlib/prelude.sprout:1420`). The bit-access pair is the same trick
with an even cheaper body: it emits nothing at all and returns the argument's SSA
name unchanged.

### (c) A C builtin for `frexp`/`ldexp` — REJECTED

Disfavoured by "Builtin vs Stdlib" rules 4–6, and made unnecessary by (b): with
bit access, `frexp`/`ldexp` equivalents are ordinary pure-Sprout stdlib functions,
which is exactly how Go layers `math.Frexp` over `math.Float64bits`.

### The justification is capability, not speed

Stated plainly because rule 6 requires it: **there is no measured application
bottleneck**, so speed alone would *not* justify this. 20 ns at the exponent
extremes is dwarfed by any surrounding allocation or I/O, and a Δv or emittance
evaluation is one or two calls.

This lands as a **capability** decision — a primitive every comparable language
exposes, which costs zero instructions, adds no dependency, and lets `stdlib.math`
become self-sufficient at the exponent extremes rather than structurally slow
there. The performance improvement is a consequence, not the argument.

## 5. Syntax and semantics

> **Superseded 2026-08-15 — these are no longer prelude functions.** Both declarations moved to
> `stdlib/math.sprout`, the only consumer in the tree, as part of the prelude-extern relocation
> (see `BACKLOG.md` §*Prelude extern relocation*). Reaching them now requires `import stdlib.math`.
> Everything below about their *semantics* is unchanged and still normative; only the "in the
> prelude" framing in §5 and §8 is historical.

Two functions, named per house convention (`char_to_string`,
`char_from_codepoint`), which also reads closest to Rust's `to_bits`/`from_bits`:

```sprout
extern fn double_to_bits(x: Double) -> Int
extern fn double_from_bits(bits: Int) -> Double
```

Semantics: **raw reinterpretation** of the IEEE 754 binary64 encoding as a
two's-complement `Int`, and back. Both are total. Round-trip is exact in both
directions for every bit pattern, including every NaN payload (nothing is
canonicalised) and both zeros. Neither is a numeric conversion — `double_to_bits(1.0)`
is `4607182418800017408`, not `1`. `to_double` remains the numeric bridge.

Like `print` and `to_double`, both are compiler intrinsics with no runtime symbol.

> **Amended 2026-09-06.** This paragraph originally said they could not be used as
> first-class values, `map(double_to_bits, xs)` failing at link. They can now: the
> wrapper the compiler synthesises for a first-class reference lowers the operation
> rather than calling a symbol. Only a reference whose argument type is still a type
> variable is refused, and that is now a located compile error rather than a link
> failure. See `docs/bitwise-int-ops-v0.md` §5.4.

### Why no bitwise operators

> **Answered elsewhere since 2026-08-17.** The general gap this section defers is now
> filled by `stdlib.bits` (`docs/bitwise-int-ops-v0.md`), which takes the named-function
> route for exactly the reason given below — the spellings are taken — so there are
> still no bitwise *operators*. Everything below remains accurate about why *this*
> change did not need them, and the no-mask precondition it documents is still what
> makes the reduction correct.

`&`, `|`, `<<`, `>>` are absent from Sprout, and `>>`/`<<` are already taken —
they are **function composition** (`rcompose`/`lcompose`, `prelude.sprout:1112`).
Adding integer bitwise operators would therefore need new spellings and a
precedence decision, and this change does not need them:

For a **positive** `x`, the sign bit is 0, so the biased exponent is exact integer
division, no mask required:

```
biased = double_to_bits(x) / 4503599627370496      # 2^52
```

and the mantissa is recovered by *subtraction*, not masking — force the exponent
field to 1023:

```
m = double_from_bits(bits - (biased - 1023) * 4503599627370496)   # m in [1, 2)
```

Reduction only ever runs on positive magnitudes (`sqrt` requires `x >= 0`, `ln`
requires `x > 0`, `cbrt` takes the magnitude and re-signs), so the no-mask
precondition holds by construction. This is documented at the use site because it
is a real precondition, not an accident.

**Subnormals need an explicit guard.** `biased == 0` means subnormal, where the
formula gives the wrong exponent and `2^e` is unrepresentable. Handled the standard
way: scale by `2^54` into the normal range, extract, then subtract 54. Verified
exact for every finite positive double down to `5e-324` (`4.94e-324` →
exponent −1074, mantissa 1.0, exact round-trip), matching libm's `frexp` exponent
on every sample.

## 6. Type-system impact

None. Both functions are monomorphic `Double -> Int` and `Int -> Double`. No new
type, no class, no inference change. The i64-uniform ABI means no representation
change either.

## 7. Error-message impact

None added. Wrong-arity or wrong-type calls are ordinary check errors. The
intercepted-intrinsic first-class-value restriction surfaces as a link error, the
same as `to_double` and `print` today.

## 8. Compatibility and migration

Purely additive: two new names in the prelude. No existing program changes meaning.

`double_to_bits` and `double_from_bits` become reserved-ish in the sense that a
user-defined function of the same name would now shadow an intrinsic —
the same pre-existing consideration as `to_double`.

**Seed impact.** Adding a prelude `extern fn` adds a `declare` line to
`bootstrap/compile_driver.ll` (`ir_lowering.lower_extern_decls` emits one for every
bundled prelude extern). Per AGENTS.md this requires a full `just refresh-seed`,
**not** the `seed-fp-ack` bypass — even though the additive `declare` is the only
seed change from the prelude edit.

## 9. Tests

- `tests/stdlib/test_double_bits.spr` — round-trip exactness, known bit patterns
  from the IEEE spec, both zeros distinguished, and the arithmetic identities the
  reduction relies on.
- `tests/stdlib/test_math_wide_reduction.spr` — the reduction results are unchanged
  (or improved) at the exponent extremes, including subnormals.
- Existing `tests/stdlib/test_math_root_accuracy.spr` and
  `test_math_transcendental.spr` must stay green: the reduction is exact, so
  results should be bit-identical or better.

## 10. Spec/docs status

- `docs/spec-v0.md` §8 — the two functions, marked **experimental**.
- `docs/math-transcendental-v0.md` — reduction section and the perf table.
- `runtime/APPROVED_BUILTINS` — **no entry**, by design; noted there so the absence
  reads as deliberate rather than as an omission.
