# Coercions & Literal Ergonomics — v1 Draft

**Status:** DRAFT / EXPERIMENTAL. Not normative. `docs/spec-v0.md` remains the
source of truth until a decision here is accepted and folded into the spec.

**Author context:** brainstormed 2026-07-13 from two ergonomics complaints:
(1) operating on `wrap`ped values is awkward (unwrap → operate → rewrap by
hand); (2) `Vec` construction reads poorly because `vec_from_list([...])` is
required at every site instead of a bare `[...]` literal.

## 0. Framing: two complaints, two *different* mechanisms

The load-bearing finding of this doc: these two cases look like one feature
("add coercions") but want **different** machinery, and bundling them would
smuggle a footgun in alongside a benign convenience.

- **Case A (Vec literals)** genuinely wants a *value-converting coercion*
  (`List a → Vec a`) — structurally identical to the one coercion Sprout
  already ships (`StringTemplate → String`, `spec-v0.md:395-400`).
- **Case B (wrap operations)** does **not** want a coercion. A `wrap`↔base
  coercion dissolves exactly the type distinction that gives `wrap` its reason
  to exist. What actually removes the awkwardness is **instance lifting** — let
  a `wrap` reuse its base type's typeclass instances *as the wrap type*, so the
  distinction is preserved. This is Haskell's `GeneralizedNewtypeDeriving`, not
  a coercion at all.

They are documented together because the user raised them together, but they
are independent and can ship independently. Case A is low-risk and faithful to
existing precedent; Case B is a `wrap` design decision with a genuine
safety/ergonomics tension that needs an explicit call.

---

## 1. Problem statement

**A. Vec literals.** `Vec a` (prelude:58, `Vec (Vector a)`) is the practical
indexed-sequence type, but there is no literal syntax for it. `[1, 2, 3]` is
desugared **at parse time** (`parser.sprout:865`, `desugar_list_literal`) into
`Cons(1, Cons(2, Cons(3, Nil)))` — unconditionally a `List a`, before the
typechecker runs. Every `Vec` value must therefore be written
`vec_from_list([...])` (or built with `vec_append`/`vec_prepend`). This is
verbose at call sites and pushes users toward `List` even where `Vec`'s O(1)
indexing is wanted.

**B. wrap operations.** `wrap Foo = T` (`spec-v0.md:314-353`) is a zero-cost
distinct type: opaque to callers, no arithmetic or class operations of `T` are
available without explicit `match Foo x -> …` destructuring and reconstruction.
For a `wrap Age = Int`, `age1 + age2` is a type error; the user must write
`match (age1, age2) with | (Age m, Age n) -> Age(m + n)`. Likewise `++` on a
`wrap Name = String` (`name1 ++ name2`) is rejected even though it is just
`String` append under a distinct type. The spec currently forbids a `wrap`
from deriving typeclasses at all (`spec-v0.md:343-344`: "A `wrap` cannot derive
typeclasses; explicit `instance` declarations are required"), so even
hand-writing `instance Num Age` is the only escape, and it is pure boilerplate
that re-threads the base type's behavior.

## 2. Goals and non-goals

**Goals.**
- A. Allow `[e1, …, en]` to denote a `Vec a` in a `Vec`-expected context,
  without `vec_from_list`, while `List` contexts are unchanged.
- B. Allow a `wrap W = T` to reuse `T`'s typeclass instances *as `W`* with
  minimal ceremony, so `Age + Age : Age` works while `Age` stays distinct from
  `Int` and from every other `wrap` over `Int`.

**Non-goals.**
- Numeric widening (`Int → Double`). Deliberately out of scope; `spec-v0.md:508`
  closes it for v0 and it is a separate decision.
- General user-defined value coercions (a `From a b`/`Coerce` class the
  elaborator fires automatically). Explicitly rejected here as a footgun — see
  §3 (Scala's retreat) and §4.
- **wrap↔base coercion** (auto-wrap or auto-unwrap). Rejected as the mechanism
  for Case B — see §0 and §4.B. Auto-*wrap* (`Int → Age`) destroys mistake
  prevention; auto-*unwrap* (`Age → Int`) loses the `Age` type at the use site
  and still forces a rewrap; both directions together reduce `wrap` to a
  transparent `type alias`.
- Overloaded literals for arbitrary containers (`Set`, `Dict`) in this pass.
  The design should not *preclude* them (see §5.A on `IsList`-style
  generalization), but v1 targets `Vec` only.

## 3. Prior-art survey

All rows verified against primary sources (language reference / std-lib docs).

### A. Context-directed collection literals

| Language | Mechanism | Notes |
|---|---|---|
| **Sprout (today)** | `StringTemplate → String` elaborator coercion (`spec-v0.md:395-400`) | The internal precedent: convert at the expected type. Directional, statically resolved, total. |
| **Haskell** | `OverloadedLists`: `[x,y,z]` desugars to `fromListN 3 (x : y : z : [])` via class `IsList` (`Item`, `fromList`, `toList`, `fromListN`) | Type-directed literal, but still routes through a standard list — so it pays the same intermediate-list cost a `List→Vec` coercion would. Verified: GHC Users Guide, OverloadedLists. |
| **Swift** | `ExpressibleByArrayLiteral`: `[1,2,3]` takes its type from context; `Array`, `Set`, `Dictionary`, `OptionSet` conform via `init(arrayLiteral:)` | The cleanest "one literal syntax, many collection types, chosen by expected type" model. Verified: Swift stdlib docs. |
| **Rust** | No context-directed collection literal. `[1,2,3]` is a fixed-size array; `Vec` requires the `vec!` macro | Deliberate divergence — Rust keeps construction explicit and per-type. Verified: `std::vec!` docs. |

Consensus: the safety-first functional/typed languages that offer this
(Haskell, Swift) do it as a **type-directed literal keyed on the expected
type**, and even the "smart" version (Haskell) still funnels through a list.
Rust declines it entirely. This validates that a `List a → Vec a` coercion is
*not* a lossy or surprising feature — it is the pragmatic subset of what
`OverloadedLists`/`ExpressibleByArrayLiteral` already normalize.

### B. Operations on a distinct wrapper type

| Language | Mechanism | Notes |
|---|---|---|
| **Haskell** | `GeneralizedNewtypeDeriving`: `newtype Age = Age Int deriving (Num)` lifts `Int`'s instance to `Age` by reusing the same dictionary; **`Age` stays distinct from `Int`** and cannot be passed where `Int` is expected | Exactly Case B's target. Zero coercion; distinction preserved; runtime-free. Verified: GHC Users Guide, newtype deriving. |
| **Scala 3** | `opaque type Logarithm = Double`: not interchangeable with `Double` outside its scope; operations provided by **extension methods**, *not* inherited | A deliberate choice of opacity + explicit surfacing over any auto-lift/coercion. Verified: Scala 3 reference, Opaque Types. |
| **Rust** | Newtype `struct Age(i32)`: no automatic trait inheritance; you `derive`/`impl` per trait (community `derive_more` for delegation). Implicit `Deref` to the inner type is possible but discouraged for newtypes | No implicit coercion; distinction is the point. |

Consensus: no modern safety-first language reaches Case B's ergonomics via a
wrap↔base *coercion*. They either **lift instances while keeping the type
distinct** (Haskell) or **surface operations explicitly** (Scala 3 extension
methods, Rust impls). The footgun path — automatic value conversion between the
wrapper and its base — is the C++ implicit-conversion-constructor / Scala-2
`implicit def` lineage that Scala 3 gated behind an opt-in import precisely
because it burned local reasoning. Sprout should follow the Haskell/Scala-3
consensus, not the C++/Scala-2 one.

## 4. High-level implementation overview (for approval before editing)

### A. `List a → Vec a` list-literal lowering — IMPLEMENTED

> **Status: implemented** on `feat/vec-literal-coercion` (checker.sprout). This
> section was corrected after implementation to describe the real mechanism; the
> pre-implementation draft mis-described it as a unifier-level coercion firing on
> any `List` value — see "Mechanism correction" below.

Mirror the `StringTemplate → String` path — which is **not** a unifier-level
coercion but a **syntactic, pre-typecheck desugar pass** (`checker.sprout`
`desugar_program`). The pass threads an *expected type name* (a bare `String`,
never a full inferred type) down the AST: `desugar_decl_i` uses the fn's return
type name, `desugar_args_with_types` uses each param's type name from a fn-name
index. Lowering keys off the **AST node shape** plus that expected name. It never
sees inferred types. Concretely, as built:

1. `type_texpr_to_maybe_name` now returns the **head constructor** of an applied
   type (`Vec Int` → `"Vec"`, `Dict k v` → `"Dict"`), so a *parameterized*
   expected type can activate a context. (Previously it returned `Nothing` for
   any applied type — fine for the nullary `StringTemplate`, but the reason a
   naive coercion branch alone did nothing for `Vec a`.)
2. `desugar_expr_i`, when the expected name is `"Vec"` and the expression is a
   **syntactic list literal** — a `Cons`-headed `CallExpr` or a bare
   `VarExpr("Nil")` (the residue of parse-time list desugaring,
   `parser.sprout:865`) — wraps the (recursively desugared) expression in
   `CallExpr(VarExpr("vec_from_list"), [expr])`. `if`/`match` arms thread the
   context so a literal in either arm is lowered (`ctx_is_active` gates both the
   `"Vec"` and `"StringTemplate"` contexts through the shared arm-threading).

**Mechanism correction (why literal-only, and why that is correct — not a
compromise):** the pass cannot distinguish a `List`-typed variable from a
`Vec`-typed one without inferred types, and `vec_from_list : List a → Vec a` is
only well-typed on a `List`. Wrapping *unconditionally* in a `Vec` slot would
turn the common case — passing a real `Vec` to a `Vec` parameter
(`f(vec_empty())`) — into `vec_from_list(vec)`, a hard type error. A syntactic
`Cons`/`Nil` head is the **one shape provably a `List`** without type info, so it
is the *sound boundary*, not a limitation. This also matches the prior art
exactly: Haskell `OverloadedLists` and Swift `ExpressibleByArrayLiteral` affect
*literal syntax only*, never arbitrary values (§3.A). The pre-implementation
draft's claim that it "fires on any `List`-typed expression / that generality is
a feature" was wrong on both feasibility and prior-art grounds.

Detection is a **head-check**, not a recursive full-literal validation: any
`Cons`-headed application is a `List` by construction (its tail must typecheck as
`List a` regardless), so wrapping it is always sound even when the tail is a
variable — head-check is simpler *and* strictly more general at zero correctness
cost.

**Cost:** the literal still builds cons cells, then `vec_from_list` walks them
once (O(n) + one intermediate list) — *exactly* the cost of a hand-written
`vec_from_list([...])`, so the sugar adds convenience at zero additional runtime
cost. (Haskell's `OverloadedLists` pays the same cost; §3.A.) A future
zero-intermediate literal is deferred — §5.A.

**Deferred (not built):** coercing a `List`-typed *variable* (`f(some_list_var)`)
into a `Vec` slot. That was never the stated pain and is infeasible in a
pre-typecheck pass; it would need a post-typecheck rewrite over the typed AST
(à la `resolve_dispatch_typed_expr`). Left as a follow-up.

### B. `wrap` instance lifting (NOT a coercion)

Lift the base type's typeclass instances to the wrap. Two candidate surfaces —
pick one in review:

- **B-opt-1 (explicit, derive-style):** allow `wrap Age = Int deriving (Num, Ord, ToString)`.
  Relaxes the `spec-v0.md:343` prohibition for the *lift* case only. Each listed
  class generates `instance C Age` whose methods `match Age n -> …` unwrap,
  delegate to `C T`, and rewrap results typed at `Age`. Opt-in and visible at
  the declaration.
- **B-opt-2 (blanket):** every `wrap W = T` automatically gets `T`'s in-scope
  instances lifted. Less ceremony, but silent — a beginner cannot see *why*
  `age + 1`… (still a type error, since `1 : Int`, not `Age` — see open
  question) works. Weaker mistake-prevention story.

Recommendation: **B-opt-1** — explicit, aligns with Sprout's "explicit over
implicit" doctrine and with `deriving` v1 which already lands lifted instances
for ADTs. It reuses the `deriving.sprout` emitter shape (generate an `instance`
decl) rather than inventing coercion machinery.

**Scope confirmed (2026-07-13):** Case B is *interpretation (i)* — operators
whose **operands are all the wrap type**: arithmetic on wrapped numerics
(`age1 + age2 : Age`) and `++` on wrapped strings (`name1 ++ name2 : Name`).
There is **no** unwrap/coercion requirement (interpretation (ii), passing an
`Age` where an `Int` is expected, is out of scope). This makes B *purely* a
deriving/instance-lifting feature with zero coercion surface.

Operator routing falls out for free: Sprout's binary operators already desugar
to class-method dispatch — `+` to the `Num`-family method, `==`/`<` to
`Eq`/`Ord`, and `++` to `Semigroup.append`. So "lift `Semigroup` to
`wrap Name = String`" *is* the entire `++`-on-wrapped-strings feature; the
operator desugaring then dispatches to the lifted `instance Semigroup Name`
witness with no operator-site special-casing. (Implementation note: the append
witness must be the *lifted* instance's — see the recently-hardened
`translate_append_operands` witness routing so `name ++ name` does not fall
through to the `String` peephole with the wrong type.)

Out of scope (unchanged): a *mixed* `age + 1` with a bare `Int` literal — that
needs the literal `1` to take type `Age`, i.e. numeric-literal polymorphism,
which is a separate feature. Case B covers only all-operands-wrapped
expressions.

## 5. Syntax and semantics impact

**A.** No new surface syntax. `[…]` is unchanged; only a *syntactic list literal
appearing in a `Vec`-expected position* is rewritten (to `vec_from_list([…])`)
by the desugar pass. Evaluation semantics: the inserted `vec_from_list` is an
ordinary strict call, evaluated after the list is built (consistent with §6 of
the spec). `List`-typed variables/calls in `Vec` position are **not** rewritten
(they still error, as before); only literal `Cons`/`Nil` syntax is. No ambiguity:
the rewrite fires only when the expected type name is `"Vec"` and the node is a
list-literal head.

> §5.A generalization note (deferred): a future `IsList`-style class
> (`from_list`/`from_list_n`) would let the same literal target `Set`/`Dict`
> and enable a zero-intermediate `from_list_n` fast path. v1 hardcodes
> `vec_from_list` to avoid designing a class prematurely; the coercion site
> should be written so swapping the hardcoded call for a class-method dispatch
> later is localized.

**B.** New (or relaxed) surface: `deriving (…)` on a `wrap` decl (B-opt-1).
Semantics: generated `instance` decls, identical in status to hand-written ones;
the wrap stays distinct (no interchangeability with the base). Runtime
representation unchanged (wrap is already identity at IR level, `spec-v0.md:329-335`),
so lifted methods compile to the base type's code with the wrap as a no-op cast.

## 6. Type-system impact

**A.** No change to unification or the type system proper — the rewrite happens
in the pre-typecheck desugar pass, so by the time inference runs it simply sees
`vec_from_list([…]) : Vec a` and checks normally. The element type is checked by
the ordinary `vec_from_list : List a → Vec a` application (a `Vec String`
context around `[1,2]` fails at that application with a `List Int` vs `List
String` element mismatch — see §7.A). Because the rewrite is gated on a
concrete `"Vec"` expected *name* and a literal node shape, it never masks a
genuine `List`/`Vec` mismatch on a non-literal expression.

**B.** `wrap` gains typeclass membership via lifting. Coherence: a lifted
`instance C W` must not overlap a hand-written one — reuse the existing
`check_overlapping_instances` pre-pass (`infer.sprout`). The lift requires a
resolvable `C T` in scope at the wrap site; absence is a compile error naming
the missing base instance.

## 7. Error-message impact

**A.** (Verified against the compiler, not predicted.) The rewrite makes the two
error modes diverge in a useful way:
- A **literal** with the wrong element type surfaces an *element-level* error.
  `["a","b"]` in a `Vec Int` slot is lowered to `vec_from_list(["a","b"])`, and
  the checker reports `Call type mismatch: Type mismatch: Int vs String` — the
  element mismatch, not a bare `List`/`Vec` one. (Fixture:
  `tests/conformance/type_error/vec_literal_element_mismatch`.)
- A **non-literal** `List` value in `Vec` position is *not* coerced and reports
  `Call type mismatch: Type mismatch: Vec vs List` — the pre-existing
  constructor mismatch (correct: it genuinely is not coercible here). (Fixture:
  `tests/conformance/type_error/vec_nonliteral_list_not_coerced`.)

**B.** `wrap Age = Int deriving (Num)` where `Num Int` is not in scope ⇒
"cannot derive `Num` for `Age`: no instance `Num Int` to lift from". Deriving an
unliftable/undefined class should name both the wrap and the base.

## 8. Compatibility / migration notes

**A.** Purely additive. Existing `vec_from_list([...])` sites keep working; they
can be simplified opportunistically but need not be. No behavior change to any
`List`-typed program. The one behavior change is that a previously-rejected
program now type-checks when a `List`-*literal* sits in a `Vec` slot — the
intended new behavior, not a break. (Non-literal `List` values are unaffected.)

**B.** Additive relaxation of `spec-v0.md:343-344`. Existing hand-written
`instance C Age` decls remain valid and take precedence (overlap check guards
against double-definition). `spec-v0.md` §5.6.1 must be updated to describe the
lift and remove/qualify the blanket "cannot derive typeclasses" sentence.

## 9. Tests added/updated

**A.** (Implemented in `tests/stdlib/test_vec_literal_coercion.spr` — all
executable, all passing on stage-2.)
- Call-arg literal: `fn sum3(v: Vec Int) -> …; sum3([1,2,3])` type-checks, runs.
- Return-position literal: `fn make_vec() -> Vec Int = [10,20,30]`.
- Empty literal: `fn empty_vec() -> Vec Int = []` (exercises the `Nil` head).
- Negative guard: a real `Vec` value (`foldable_to_vec([...])`) passed to a `Vec`
  param passes through **unwrapped** — proves no double-wrap into
  `vec_from_list(vec)` (which would be a type error).
- Negative fixture (`tests/conformance/type_error/vec_literal_element_mismatch`):
  a wrong-element literal (`["a","b"]` in `Vec Int`) is rejected with the
  element-level `Int vs String` diagnostic (§7.A) — the lowering does not mask a
  real error.
- Negative fixture (`tests/conformance/type_error/vec_nonliteral_list_not_coerced`):
  a `List`-typed *variable* in a `Vec` slot is *not* coerced and stays a
  `Vec vs List` type error — verifies the literal-only boundary (§4.A "Deferred",
  spec §5.5.1).

**B.**
- `wrap Age = Int deriving (Num, Ord, ToString)`: `age1 + age2 : Age`,
  `to_string(age)` works, `age < age2` works (executable).
- `wrap Name = String deriving (Semigroup, Eq)`: `name1 ++ name2 : Name`
  dispatches to the lifted `Semigroup Name` witness (not the `String` peephole),
  result typed `Name` (executable — guards the `++`-on-wrapped-strings case and
  the witness-routing note in §4.B).
- Distinctness preserved: passing an `Age` where `Int` is expected still fails
  (negative fixture) — this is the guard that we did *not* accidentally build a
  coercion.
- Negative: `deriving` a class with no liftable base instance errors with the
  §7.B message.
- Overlap: hand-written `instance` + `deriving` the same class is rejected by
  the existing overlap pre-pass.

## 10. Spec/docs status

- **A. Implemented + spec updated.** `spec-v0.md` §5.5 (string interpolation)
  gains a short clause documenting the parallel `List`-literal → `Vec` lowering:
  a syntactic list literal in a `Vec`-expected position is lowered to
  `vec_from_list([…])`, literal-only (non-literal `List` values are not
  coerced). The `IsList`-generalization (§5.A) is noted as future work.
- **B. Experimental (not built).** On acceptance, revise `spec-v0.md` §5.6.1 to
  permit `deriving` on `wrap` for instance lifting, spell out that lifting
  preserves distinctness, and cross-reference `docs/deriving-v1-draft.md`.

Normative vs experimental status is stated explicitly in the spec edits; this
doc is the design record.
