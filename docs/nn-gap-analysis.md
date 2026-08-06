# Neural Network Feasibility — Gap Analysis

Status: **analysis** (2026-07-05). Identifies what the Sprout language and
stdlib are missing to write a rudimentary neural network. No implementation is
proposed here; this is a scoping document for a follow-up feature session.

Related: [`numeric-types-v1-draft.md`](numeric-types-v1-draft.md) (the design
that would fill the primary gap), [`spec-v0.md`](spec-v0.md) §numeric.

---

## 1. Framing

A neural network decomposes into four capability layers:

1. **Number system** — to hold weights, learning rate, activations, gradients.
2. **Elementary math** — the activation function (sigmoid/tanh need `exp`; shipped
   2026-08-06 in pure Sprout, see §3.2's correction).
3. **Collections + iteration** — vectors/matrices and the training loop.
4. **I/O** — printing results, loading a dataset.

Mapping Sprout against these layers separates *hard blockers* (layers 1–2,
largely absent) from *what already works* (layer 3, and most of layer 4). The
distance from "no NN possible" to "a rudimentary NN trains" is essentially a
single feature: a real-number type.

---

## 2. Blockers — the network cannot be written without these

### 2.1 No real-number type (the central gap)

Sprout has exactly one numeric type: `Int` (`i64`). A network is fractional by
nature — weights like `0.37`, a learning rate like `0.01`, normalized inputs.
None of these are representable.

- `docs/numeric-types-v1-draft.md:3` — *"Status: draft. No implementation yet."*
- `docs/spec-v0.md:434` explicitly excludes *"fractional arithmetic, or
  floating-point support in v0."*
- The lexer cannot tokenize `3.14`: number scanning is
  `take_while(is_ascii_digit)` (`stdlib/compiler/lexer.sprout:23`); a `.` is
  never consumed as part of a numeric literal.
- `/` truncates: `1 / 2 == 0`. Arithmetic lowers to integer LLVM ops
  unconditionally — `codegen.sprout:2063` hardcodes `add`/`sub`/`mul`/`sdiv i64`.

**Every quantity in the net (weights, activations, gradients, loss) has nowhere
to live.** This is the one true blocker; everything else is secondary.

Encouraging detail for the fix: the operand types (`left_ty`, `right_ty`) and
the result type (`ty`) already reach the arithmetic emission site
(`codegen.sprout:2047-2065`), so making `+ - * /` type-aware (emit
`fadd`/`fsub`/`fmul`/`fdiv` on `double` when the type is `Double`) is a bounded,
local change — **not** the numeric draft's full typeclass-dispatch milestone
(N1). This is a consequence of the completed typed-codegen (PR11) campaign.

### 2.2 No elementary / transcendental math

> **Resolved 2026-08-06 — this section describes the state before the `Double` math
> layer landed.** `stdlib.math` now provides `sqrt`, `cbrt`, `exp`, `ln`, `log2`,
> `log10`, `log(x, base)`, real `pow`, and trig, all pure Sprout with no builtin; the
> Int surface moved to `stdlib.math.int`. Only `tanh` is still absent, and it is one
> line over `exp`. See `docs/math-transcendental-v0.md`.

No `exp`, `log`, `sqrt`, `pow` (real), `tanh`, `sin`. `stdlib/math.sprout` is
entirely **Int-valued** (`abs`, `min`, `max`, `clamp`, `sign`, integer `pow`,
`gcd`, `lcm`). The runtime's `double` usages
(`runtime/sprout_runtime.c:184-190, 410-443, 1704-1713`) are GC-heuristic
internals, not exposed to Sprout. There is no `<math.h>` bridge for user code.

This is a **conditional** blocker — its severity depends entirely on the chosen
activation function:

| Activation | Needs | Status |
|---|---|---|
| **ReLU** `max(0, x)` + MSE loss | only `+ - * /` and comparison | unblocked once §2.1 lands — *no math builtin at all* |
| **Sigmoid** `1/(1+e^-x)` / tanh / softmax | `exp` | **UNBLOCKED 2026-08-06** — `math.exp` shipped in pure Sprout, no extern |

A 2-hidden-unit ReLU network trains XOR — a genuinely rudimentary, genuinely
trainable net — with zero transcendental functions.

**Correction (2026-08-06):** the sigmoid row above originally read "needs one C extern
(`exp`) + `APPROVED_BUILTINS` entry". That was wrong. `exp` is implementable in pure
Sprout by range reduction plus a Taylor series — `stdlib.math` now exports `exp`, `ln`,
`log2`, `log10`, `log`, `cbrt` and a `Double` `pow`, all with **no C builtin** and
`runtime/APPROVED_BUILTINS` untouched, at 8e-14 relative accuracy and ~9ns per `exp`
call. `1.0 / (1.0 + exp(0.0 - x))` is ordinary Sprout today, so sigmoid, tanh and
softmax are no longer blocked on anything. See `docs/math-transcendental-v0.md`.

---

## 3. Secondary gaps — workaroundable, but they shape the code

### 3.1 Random number generation is raw-bytes only

Only `crypto.random_bytes(count)` exists — cryptographic bytes from
`/dev/urandom` (`runtime/sprout_runtime.c:7336`, `stdlib/crypto.sprout:35`).
There is **no seedable PRNG and no uniform-float generator**. Weight
initialization would mean hand-deriving numbers from raw bytes, and — with no
seed — **runs are not reproducible**, which is painful when debugging a learner.
A small `rand`/`srand` or a pure LCG in stdlib would close this.

### 3.2 No float↔string / float↔int conversion

**Partly closed.** `to_double(Int) -> Double` now exists (a compiler intrinsic
lowering to inline `sitofp`, no runtime builtin), and `double_to_string` +
`instance ToString Double` render a `Double` as decimal text (shortest
round-tripping precision, `.0` appended for integrals — it was `%g`, i.e. 6
significant digits and therefore lossy, until 2026-08-06). Also `==`/`!=` now work on `Double`. Still missing:
`from_int`'s inverse `to_int` (`fptosi`). **string→float parsing** is now covered
by the pure-Sprout `parse_double` (prelude), so loading a dataset from text/CSV works.

### 3.3 No `zip`/`zip_with`, no matrix type

`Vec` is rich (`vec_get`, `vec_set`, `vec_map`, `vec_fold`, `vec_length`) but
there is **no `vec_zip_with`** — the natural way to express `weightᵢ · inputᵢ`.
Dot-products must index-zip manually via `vec_get`. There is also no 2D
`Matrix` type; a layer's weights would be a `Vec (Vec Double)` or a flat
`MutVec` with manual stride indexing. Ergonomic frictions, not blockers.

---

## 4. Ergonomic frictions — known limitations that will bite

- **Effectful iteration is a known gap.** `MutVec`/`Ref` ops are `!{IO}` and
  must live in `do` blocks; effectful *list* iteration has limitations. Mutable
  weight updates in a training loop must be structured as recursion + `MutVec`,
  not a `map` over an effectful body.
- **No `let … in` inside pure functions** (README "Not Yet Supported"); the
  word `not` is not an operator — use the `!` prefix, which works. Minor
  readability impact. Unary minus
  `-x` works on both `Int` (`IRINeg` → `sub i64 0, x`) and `Double` (`IRFNeg` →
  `fneg double`); the two dispatch on operand type in `ast_to_ir`, because a
  naive `sub i64 0` on a double's i64 bit-pattern silently corrupts it (`-3.0`
  would come out as `-1.5`).

---

## 5. What already works (the gap is narrow, not wide)

The structural half of a network is well-covered:

- **Collections:** immutable `Vec` with full map/fold/get/set
  (`stdlib/prelude.sprout:138-307`); `MutVec` with true in-place `mutvec_set`
  backed by the `vector_mutset` GC barrier (`prelude:1166-1178`) — ideal for
  weight matrices; plus `List`, `Dict`, `Set`, tuples.
- **Iteration:** `range_fold` (`prelude:104`), `vec_fold` (`prelude:205`),
  `list_fold` (`prelude:130`), and accumulator recursion cover epochs and layer
  passes. No loop keyword, but the folds suffice.
- **Mutable state:** `Ref a` cells (`ref_new`/`ref_read`/`ref_write`,
  `examples/ref_tutorial.sprout`).
- **I/O & printing:** `print`, `int_to_string`, a `ToString` typeclass, string
  interpolation `${x}` — enough to trace training once numbers are printable.

---

## 6. Priority order to unblock

| # | Missing capability | Severity | Needed for |
|---|---|---|---|
| 1 | **Real-number type (`Double`)** + type-aware `+ - * /` + printing | **Hard blocker** | Everything |
| 2 | ~~**`exp` math builtin**~~ | **DONE 2026-08-06 — and not a builtin.** `stdlib.math.exp` is pure Sprout | Classic activation |
| 3 | **Seedable PRNG / uniform random** | Friction | Reproducible weight init |
| 4 | **`Double`↔string parse/format** | Friction | Loading datasets, logging |
| 5 | **`vec_zip_with` (+ optional `Matrix` type)** | Ergonomic | Clean dot-products / layers |

**Smallest path to a working rudimentary NN:** item 1 alone, with a ReLU
activation (which avoids item 2 entirely). Items 3–5 improve ergonomics and
reproducibility but are not required for a first trainable network.

---

## 7. Implementation path (minimal PoC, aligned with the numeric design)

**Guiding principle:** everything added here must be a forward *subset* of
[`numeric-types-v1-draft.md`](numeric-types-v1-draft.md) — same names, same
semantics, just narrower — so that when that doc's **N1** milestone (the
`Numeric`/`Real` typeclass tower) lands, it *generalizes* this code rather than
replacing it. This works because concrete `Double` math does not technically
depend on the typeclass tower; N1's "classes first" ordering is a code-sharing
and dispatch-cost decision, not a hard dependency. There is exactly one seam
where minimal and big-picture diverge — the `/` operator — called out in §7.3.

### 7.1 Ordered steps

1. **Lexer — float literals.** Tokenize `3.14` (`digits . digits`, optional
   exponent) at `stdlib/compiler/lexer.sprout:23`; emit a `Double` literal.
   *Aligned:* the draft mandates `3.14` is always `Double` (§6.6).
2. **Parser / AST — a `Double` literal node** (or an `is_float` flag on the
   existing number node). One new variant.
3. **Typechecker — `Double` base type, concrete-only arithmetic.** `3.14 :
   Double`; `+ - * /` require both operands the *same concrete numeric type* and
   return it; comparisons work on `Double`. **No `Numeric`/`Real` classes yet.**
   *Aligned:* this rule is a strict restriction of the future N1 rule
   (`Numeric a =>`); code that compiles now still compiles under N1. Consequence:
   `x + 1` where `x : Double` is a type error — write `x + 1.0`. That is exactly
   the draft's "no polymorphic literals" rule (§6.6), on-spec rather than a hack.
4. **Codegen — type-aware `emit_binary`.** The site
   (`codegen.sprout:2047-2065`) already has `left_ty`, `right_ty`, and the result
   `ty` in scope. Branch: `Double` → `fadd`/`fsub`/`fmul`/`fdiv` on `double`;
   else the existing `i64` path. Add `fcmp` for comparison, `sitofp` for
   `to_double`, double-literal emission, `ll_double()`. *Aligned:* these
   branches ARE the eventual `Double` instance bodies — when N1 routes `+`
   through `Additive`/`Multiplicative` dispatch, the Double instance's `add`
   still lowers to `fadd` right here. Not throwaway work.
5. **Runtime + prelude — printing & conversion.** One new C function
   `double_to_string` (formatting, *not* math — sidesteps the transcendental /
   `exp` builtin question), plus a `ToString Double` instance and a
   `to_double(x: Int) -> Double` helper in the prelude. *Aligned:* name it
   `to_double` exactly — the draft puts `to_double` on the `Integer` class
   (§6.2), so N1 turns the plain function into a class method with the same
   signature; call sites don't change. (Alternative avoiding the C function: add
   `fptosi` and format in pure Sprout — more work, listed only for completeness.)
6. **Pure-Sprout NN scaffolding — no language change, no approval needed.**
   - `relu(x: Double) = if x < 0.0 then 0.0 else x` — activation; comparison
     only, **no `exp`**.
   - `vec_zip_with` / `dot` in stdlib (closes §3.3 cheaply).
   - A tiny deterministic LCG PRNG in `Int`, seeded by a constant, for weight
     init — sidesteps the PRNG gap (§3.1) *and* makes runs reproducible, all
     in-language.
7. **The PoC** — a 2-2-1 ReLU network, MSE loss, hand-written backprop, trained
   on XOR. Ordinary Sprout on top of steps 1–6.

### 7.2 Deferred (all forward-compatible with the draft)

`exp`/sigmoid, the `Numeric`/`Real` typeclasses, generic `sum`/`mean`/`dot`,
seedable PRNG as a *language* feature, float parsing, `Float` (f32).

### 7.3 The one divergence — `/` semantics (needs a decision)

The draft (§6.4) wants `/` to be **`Real`-only**, making `1 / 2` (Int) a *type
error* that forces `div(1, 2)`. Honoring that now is a breaking change to every
existing `Int`-using example and is not needed for the NN.

- **Recommended:** keep `/` type-aware on both (`Int` → `sdiv` truncating,
  `Double` → `fdiv`) for the PoC, and record the debt explicitly here: *Int `/`
  stays truncating; reconciled to `Real`-only when N1 removes `/` from `Int`.*
  Smallest change, breaks nothing — but a deliberate, documented deviation.
- **Purist alternative:** honor `Real`-only now — migrate every `Int` `/` in the
  codebase to `div()`. Larger churn, orthogonal to the NN.

### 7.4 Definition-of-Done cost

This is a compiler-source + runtime change: bootstrap-seed regen, full
`just test`, smoke-shapes, `compile-examples-stage1`, and — for
`double_to_string` — an `APPROVED_BUILTINS` entry with justification plus the
example canary. Non-trivial but well-bounded.

---

## 8. Related design docs & roadmap

- [`numeric-types-v1-draft.md`](numeric-types-v1-draft.md) — **the** design for
  the primary gap (§2.1/§2.2). Internal `Additive`/`Multiplicative` +
  surface `Numeric`/`Integer`/`Real` classes; `/` is `Real`-only; `3.14` is
  always `Double`. Milestones: **N1** (typeclass dispatch — "only load-bearing"),
  **N2** (`Double` + `Real`, blocked on "C runtime float support"), **N4**
  (`Float` f32). Status: draft, no implementation.
- [`spec-v0.md`](spec-v0.md) §8 — normative for the **`stdlib.math.int`** surface.
  `Double` arithmetic has since landed as an experimental extension, and `stdlib.math`
  is now the `Double` layer; the modules are split by numeric type because Sprout has
  no overloading. (This entry previously read "the `stdlib.math` surface is Int-only",
  which was true when written.)
- [`math-transcendental-v0.md`](math-transcendental-v0.md) — the `Double` elementary
  function suite that closed §2.2, and why it needed no builtin.
- [`haskell-lessons-learned.md`](haskell-lessons-learned.md) §11 — rationale for
  the draft's minimalist class count (avoid Haskell's `Num`/`Fractional`/…
  thicket).
- `BACKLOG.md:120` — `[~] Add vector utility combinators` — closest tracked home
  for `vec_zip_with` (§3.3).
- `BACKLOG.md:228` — `[ ] Route </<=/>/>= on ADTs through Ord` — relevant only if
  weights become wrapped types.
- **Tracking gap:** no `BACKLOG.md` item references the numeric design at all;
  the N1/N2 milestones live only in the draft. Filing them as backlog items that
  link the draft is the cheapest way to make "add floats" tracked work.
