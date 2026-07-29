# Operators as signatured functions (v0)

Status: **PROPOSED** (design only; not implemented). A narrow **stopgap fix has landed
separately** — see "§0. Immediate stopgap (landed)". This document proposes the target
architecture for how Sprout should treat operators going forward.

## 0. Immediate stopgap (landed)

The primitive operators `!` (logical not) and unary `-` (negation) had **no operand-type
constraint** in the typechecker. `infer_unary` copied the operand's type straight through as the
result type, so `! 3` typed as `Int` and reached codegen, where `!` lowers to `IRBoolNot`
(`xor i64 %x, 1`). That xor **flips the low bit of the raw integer**:

- `! true`(1) → 0 = `false`  ✓
- `! 3`(0b0011) → 0b0010 = `2`
- `! 9`(0b1001) → 0b1000 = `8`
- `! 4`(0b0100) → 0b0101 = `5`  (goes *up* — it is bit-flip, not `n - 1`)

Unary `-` had the identical hole: `- true` → `IRINeg` (`sub i64 0, 1`) = `-1`.

The stopgap tightens `infer_unary` to unify the operand with the fixed expected type (`!` : `Bool`;
`-` : `Int` or `Double`), mirroring the existing `infer_if` / `check_arith` idioms. This is a pure
typechecker fix — no codegen change — and it closes the soundness hole. It does **not** give
operators signatures; that is the subject of this document.

## 1. Problem statement

Sprout has two disjoint mechanisms for operators:

1. **Typeclass-backed operators** — `==`/`!=` (`Eq`), `<`/`>`/`<=`/`>=` (`Ord`), `++` (`Semigroup`).
   These **have real type signatures** (the class-method declarations). Their operand typing falls
   out of ordinary call resolution, and for concrete types they devirtualize to primitives
   (`==` on `Int` → `icmp`, see `is_primitive_eq_type`). This class of operator has *never* had the
   bug above, precisely because its type is written down.

2. **Primitive operators** — unary `!`, unary `-`, arithmetic `+ - * /`. These have **no signature
   anywhere**. They are hand-coded in the typechecker (`infer_unary`, `check_arith`) and lowered to
   dedicated IR. A hand-written path only enforces what someone remembered to enforce — which is
   exactly why §0's miscompile existed.

The asymmetry is the problem: operator types are not first-class, so (a) they are invisible to
tooling (LSP hover, error messages cannot say `!` : `Bool -> Bool`), (b) they are documented only
by prose and compiler internals, and (c) each new primitive operator re-opens the risk of an
unconstrained-operand miscompile.

## 2. Goals and non-goals

**Goals.**
1. Give every operator a **single source of truth for its type** — a signature the typechecker reads,
   the LSP can surface, and docs can point at.
2. Keep runtime cost **exactly zero** relative to today: `!x` must still lower to one `xor`, `a + b`
   to one `add`. No call overhead, no allocation.
3. Fixity/precedence declared alongside the operator, not hard-coded ad hoc in the parser.

**Non-goals (v0 fence).**
- **No change to the surface syntax users write.** `!x`, `-x`, `a + b` stay spelled the same.
- **No new numeric typeclass (`Num`) in v0.** Whether `+` becomes a `Num` method is deferred (§6);
  v0 keeps arithmetic monomorphic (`Int`/`Double`), matching `check_arith` today.
- **No general-purpose inliner as a prerequisite** (see §3, model B1).

## 3. Prior-art survey

How established languages treat operators, and — critically — how they keep "operator = function"
free at runtime. Every row verified against a primary source.

| Language | Operator = signatured function? | Zero-cost mechanism |
|---|---|---|
| **OCaml** | **Yes, literally**: `external not : bool -> bool = "%boolnot"`; `external ( + ) : int -> int -> int = "%addint"`; `external ( ~- ) : int -> int = "%negint"`; `external ( +. ) : float -> float -> float = "%addfloat"` | The `%`-primitive name is **intercepted by the backend** and lowered to a direct instruction. **No inliner involved.** |
| **Haskell (GHC)** | Yes: `(+)`/`not` are ordinary functions (`Num` class / normal defs) bottoming out in **wired-in primops** `(+#)` in `GHC.Prim` | Known-key/wired-in primops lowered directly **+** aggressive inliner/specializer erases the class indirection. |
| **Rust** | Yes, as **traits**: `!a` ⇒ `Not::not(a)`, `-a` ⇒ `Neg::neg(a)`, `a + b` ⇒ `Add::add(a, b)` | Builtin impls are intrinsic/lang-items, monomorphized + `#[inline]`d to LLVM `xor`/`neg`. Relies on the optimizer. |
| **Swift** | Yes: `prefix operator +++` / `infix operator +-: AdditionPrecedence` declared **separately** from the implementing `func` | Stdlib operator funcs on primitives are inlined to LLVM. |

**Consensus.** Operators-as-signatured-functions is the mainstream design (all four). Fixity is
declared separately from the function (Swift's `operator`/`precedencegroup`, Haskell's `infixl`).
Zero runtime cost is achieved by one of two mechanisms:

- **Known-key primitive lowering** (OCaml's `%`-externals): the signature governs *typing*; the
  primitive name governs *codegen*. **Needs no inliner.**
- **Inliner/specializer** (GHC, Rust): operators are real function bodies erased by the optimizer.

Sources:
- OCaml `stdlib.ml`: <https://github.com/ocaml/ocaml/blob/trunk/stdlib/stdlib.ml>
- Rust `std::ops`: <https://doc.rust-lang.org/std/ops/index.html>
- GHC `ghc-prim` / `GHC.Prim`: <https://hackage.haskell.org/package/ghc-prim>
- Swift — Advanced Operators: <https://github.com/swiftlang/swift-book/blob/main/TSPL.docc/LanguageGuide/AdvancedOperators.md>

## 4. High-level implementation overview (for approval before editing)

Two candidate models, mapping directly onto the two mechanisms above.

### Model B1 — signature governs typing, name governs codegen (recommended)

The OCaml model, and the one Sprout is already 90% set up for.

1. **Signature source of truth.** Declare the primitive operators in the prelude as `extern fn` with
   a real signature, e.g. `extern fn not(x: Bool) -> Bool = "@bool_not"` — mirroring OCaml's
   `external not : bool -> bool = "%boolnot"`. Sprout **already has this exact shape**: the prelude
   uses `extern fn` for runtime builtins, and lowering already recognizes primitives by name
   (`recognize_string_builtin("str_concat") → primitive IR`, `ast_to_ir.sprout`).
2. **Parser sugar.** `!e` desugars to a call of the known operator function; `a + b` likewise. Fixity
   / precedence move to a declaration table rather than being threaded through `parse_unary` /
   `parse_binary` by hand.
3. **Typing for free.** Inference types the desugared call through the ordinary call path against the
   operator's signature. The `!`-on-`Int` bug becomes **structurally impossible** — there is no
   special-case to forget, the signature *is* the constraint.
4. **Codegen intercept.** `ast_to_ir` recognizes the operator function's name (exactly like
   `str_concat`) and emits `IRBoolNot` / `IRINeg` / `IRAdd` directly. **Resulting IR is identical to
   today** — one instruction, no call, no allocation.

**Runtime cost: zero.** **No inliner required.** This is not new infrastructure — it generalizes the
`str_concat` / `== → icmp` treatment Sprout already ships to `!`, `-`, and arithmetic.

### Model B2 — operators are ordinary function bodies + a general inliner

The GHC/Rust model. Operators are real Sprout functions (`fn not(x: Bool) -> Bool = if x then false
else true`), no name interception; a **general function inliner + constant-folder** recovers the
primitive. Runtime cost is zero *after* the inliner exists — but without it every `!x`, and fatally
every `a + b` in a hot loop, becomes an out-of-line call. B2 therefore bundles a substantial,
independently-useful optimizer arc (an inliner is already a wanted lever for accessors and
heap-field tuples).

**Recommendation.** Ship **B1** as the operator architecture — it delivers the language surface
(operators have signatures) at zero runtime cost using machinery Sprout already has. Treat the
general inliner (B2) as a **separate follow-on** that *upgrades* B1 (operators could then have real
fallback bodies for non-intercepted types) rather than replacing it.

## 5. Syntax and semantics impact

- **Surface unchanged** in v0: `!x`, `-x`, `a + b` spelled identically.
- Internally, `UnaryExpr`/`BinaryExpr` desugar to calls of known operator functions. The typed AST
  may keep `TUnary`/`TBinary` as an optimization tag, or collapse to `TCall` with a name-intercept in
  lowering — an implementation choice settled at build time.
- Fixity/precedence become data (a declaration table), enabling future user-declared operators
  (post-v0; gated behind a separate design like Swift's `operator`/`precedencegroup`).

## 6. Type-system impact

- Primitive operators gain concrete monomorphic signatures: `not : Bool -> Bool`,
  `negate_int : Int -> Int`, `negate_double : Double -> Double`, and (if routed this way) arithmetic
  `Int -> Int -> Int` / `Double -> Double -> Double`.
- **Open decision (deferred):** whether arithmetic `+ - * /` becomes a `Num`/`Numeric` typeclass
  (polymorphic, dictionary-passing unless devirtualized) or stays two monomorphic primitives.
  v0 keeps it monomorphic (status quo of `check_arith`). A `Num` class is a larger inference-reach
  change and is out of v0 scope.

## 7. Error-message impact

- Operator misuse becomes an ordinary "no matching signature / type mismatch" at the call site,
  consistent with function-call errors, and able to *name the operator's type*
  (`!` : `Bool -> Bool`) because the signature exists.
- The stopgap's bespoke messages (`! (logical not) needs a Bool operand: …`) would be superseded by
  uniform call-mismatch diagnostics; the negative fixtures in `tests/conformance/type_error/` would
  be updated to the new wording at that time.

## 8. Compatibility / migration notes

- No user-visible source changes in v0 (surface syntax preserved).
- The `!=` desugar currently builds a `TUnary("!")` node directly in `check_eq`; under B1 it would
  build a call to the `not` operator function instead. Behavior-preserving.
- Requires a bootstrap seed refresh (compiler-source change).

## 9. Tests added / updated

- Stopgap (landed): `tests/conformance/type_error/unary_not_non_bool.{spr,err}`,
  `tests/conformance/type_error/unary_minus_non_numeric.{spr,err}`, `tests/stdlib/test_unary_not.spr`,
  plus the existing `tests/stdlib/test_unary_minus_double.spr`.
- B1: reuse the above as the behavioral contract (they must stay green through the refactor), add
  parser tests for the desugar and IR-shape tests asserting `!x` still lowers to a single `IRBoolNot`
  (no call).

## 10. Spec/docs status

- This document is **experimental / proposed**; `docs/spec-v0.md` remains normative. The stopgap's
  behavior (operators reject ill-typed operands) is the only part currently in force and is reflected
  in the spec's operator section.
- Adopting B1 will require updating `docs/spec-v0.md` operator typing to reference signatures, and a
  fixity/precedence table.
