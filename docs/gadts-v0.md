# GADTs and Existentials in Sprout — Analysis & Staging (2026-07-30)

**Status:** analysis / design exploration. **Non-normative.** This doc records
what Generalized Algebraic Data Types (GADTs) are, why full GADTs are a poor fit
for Sprout's inference model *today*, and a staged path whose first step
(existential constructors) is cheap, sound, and independently useful. It does
**not** commit Sprout to any of this — it exists so the decision, if revisited,
starts from verified prior art rather than from scratch. `docs/spec-v0.md`
remains the normative source of truth; nothing here changes the language
contract.

---

## 1. Problem statement

Every Sprout ADT constructor today returns the type head applied to the
declaration's parameters *verbatim*. This is spec §7 rule 7: *"ADT constructors
produce values of their declared type."* So for

```sprout
type Expr a =
  | IntLit Int
  | BoolLit Bool
  | Add (Expr a) (Expr a)
```

`IntLit : Int -> Expr a` and `BoolLit : Bool -> Expr a` for the **same,
fully-general `a`**. The index `a` is inert — nothing ties `IntLit`'s payload to
it, so an `Expr Int` may still contain a `BoolLit`, and `eval : Expr a -> a`
cannot be written (from `IntLit n` you learn only `n : Int`, never `n : a`).

Two capabilities are unreachable as a result:

1. **Type-indexed data** — a constructor whose result *refines* the index
   (`IntLit : Int -> Expr Int`), so pattern matching learns a fact about the
   value's type. Enables type-safe interpreters, length-indexed vectors, and
   invariant-carrying data (the classic GADT payoff).
2. **Existential data** — a constructor that *hides* a type variable behind a
   shared interface (`Boxed : ToString a => a -> Shown`), so a single
   collection can hold differently-typed values touched only through that
   interface. Enables heterogeneous lists, closures-over-hidden-state, and
   plugin/handler registries.

These are related — (2) is subsumed by GADT syntax — but they have *very*
different implementation costs against Sprout's type checker. This doc separates
them deliberately.

## 2. Goals and non-goals

**Goals**

- Define the two capabilities precisely and distinguish them.
- Survey how state-of-the-art languages handle each, from primary sources.
- Identify exactly which of Sprout's current mechanisms GADTs would stress.
- Propose a **staging** in which the first deliverable is small, sound, and
  useful on its own, and each later stage is optional.

**Non-goals**

- No syntax is ratified here. The surface forms below are illustrative.
- **Full inference of GADT-using functions without signatures is an explicit
  non-goal, permanently.** No mainstream language offers it (see §3); chasing it
  would destabilize Sprout's Hindley–Milner core.
- No implementation is started. Definition of Ready (a failing test) applies when
  work begins, not to this doc.

## 3. Prior-art survey

Every row below is verified against the cited primary source.

### 3.1 Full GADTs (index refinement)

- **GHC (Haskell)** — GADTs are an opt-in extension. The GHC User's Guide
  (§6.4.8) states type **refinement is carried out *only* from user-supplied
  type annotations**: without a signature "no type refinement happens and lots
  of obscure error messages will occur," and a variable gets a *rigid* type
  precisely by being given a signature. The design follows *Simple
  unification-based type inference for GADTs* (Peyton Jones, Vytiniotis, Weirich,
  Washburn, ICFP 2006); modern GHC solves the resulting constraints with the
  **OutsideIn(X)** framework (Vytiniotis, Peyton Jones, Schrijvers, Sulzmann, JFP
  2011), which handles *local assumptions* — exactly the branch-local
  equalities GADT matching introduces. **Takeaway: refinement is
  annotation-driven; a whole local-assumption constraint solver is required.**

- **OCaml** — GADTs pattern-matching "introduces local type equality
  assumptions, which are a source of ambiguities that may destroy principal
  types and must be resolved by type annotations" (Garrigue & Rémy, *Ambivalent
  Types for Principal Type Inference with GADTs*, APLAS 2013). OCaml adopted
  *ambivalent types* to detect the ambiguity and keep a principal solution *when
  one exists* — still gated on annotations. **Takeaway: same conclusion as GHC,
  reached by a different (ambivalence) mechanism; annotations are mandatory.**

- **Idris / Agda** — indexed data families subsume GADTs as a special case of
  full dependent types; inference is bidirectional and signature-driven by
  construction. **Takeaway: the "annotations required" posture is universal, not
  a Haskell/OCaml quirk.**

**Consensus:** no language infers GADT-refined functions without a signature.
The universal design is *checking-mode against a required signature*, plus a
solver that can carry local type equalities per branch.

### 3.2 Existentials (the cheaper capability)

- **GHC (Haskell)** — `ExistentialQuantification` lets a constructor bind a
  `forall`'d variable absent from the result:
  `data Accum a = forall s. MkAccum s (a -> s -> s) (s -> a)`. When constructed,
  the hidden variable is instantiated to a concrete type; when matched, that type
  is **abstract** (rigid, cannot escape). Critically, for the *constrained*
  form, "the constructor has a hidden field that stores the type-class
  dictionary… when pattern-matching, that dictionary becomes available for the
  right-hand side." Existentials are **subsumed by GADTs** (enabling either
  extension suffices). **Takeaway: existentials need a hidden dictionary field +
  an abstract-on-unpack rule — no local *equalities*.**

- **Rust** — `dyn Trait` is an existential type: "the compiler does not know the
  concrete type… the type has been erased. This type erasure is what makes trait
  objects existential types" (The Rust Reference, *Trait object types*). A
  `Box<dyn ToString>` is a fat pointer — one pointer to the value, one to a
  **vtable** carrying the trait methods. The vtable *is* the packed dictionary.
  **Takeaway: the single most-used existential in a mainstream systems language
  is exactly Stage 0's constrained form — value + packed dictionary.**

- **Swift** — `any Protocol` existentials (an explicit `any` keyword since the
  Swift 5.6/5.7 era) box a value behind a witness table; `some Protocol` is the
  *opaque* (universal) dual. **Takeaway: modern languages find the distinction
  important enough to give existentials dedicated syntax.**

**Consensus:** existentials are a well-understood, self-contained feature that
predates and is independent of index refinement. Constrained existentials =
"an interface value with its dictionary boxed alongside it" — precisely a Rust
trait object.

## 4. Why full GADTs do not fit Sprout today

From `docs/hm-typechecker.md` and `docs/scoped-type-variables-analysis-2026-07-26.md`,
Sprout is **classic Hindley–Milner with implicit quantification**: no
user-written `forall`; `generalize` quantifies free variables at a definition;
`instantiate` replaces them with **fresh unification unknowns** at each use site;
there are **no rigid variables anywhere** and **no local type annotations**
(`where` bindings are value-only, and even `FnDecl` param/return annotations are
"not yet validated," spec §5.6). Against that baseline, full GADTs demand four
things Sprout lacks:

1. **Local type equalities per branch.** Standard unification assumes a variable
   has one solution across its scope; GADT matching makes `a` equal to different
   things in different branches. That is reasoning under *local hypotheses* — a
   different solver (OutsideIn(X) / ambivalence), not the current one.
2. **Loss of principal types → mandatory signatures.** Every language in §3.1
   requires them. Sprout has no local-annotation surface to *write* the required
   signature into a body yet.
3. **Bidirectional / checking-mode inference.** The signature must be pushed
   *inward* so branches are checked, not guessed. Sprout infers outward-only
   today.
4. **Constraint-aware exhaustiveness.** Sprout's coverage checker (spec §5.5) is
   column-based and does not even do the full usefulness matrix; GADTs make some
   constructors *unreachable given the index*, which the checker would have to
   prove.

Prerequisites (2)+(3) are the subject of the scoped-type-variables analysis,
which flags **local type annotations** as the real upstream gate. Full GADTs are
therefore **out of scope until that gate is resolved** and are sized as **XL**.

## 5. The staging

The insight that makes this tractable: **existentials (§3.2) require none of
§4's four items.** They introduce *one* new idea — a variable that is rigid and
must-not-escape *inside a single branch* — with no cross-branch equalities, no
principal-type loss, no mandatory signatures, no exhaustiveness change. That is a
strictly smaller, self-contained feature. So:

- **Stage 0a — unconstrained existentials. ✅ IMPLEMENTED (2026-07-30).** A
  constructor may bind a type variable absent from the head. De-risks the one
  novel mechanism (skolem + escape check) with zero runtime change. Shipped with
  the nominal-skolem representation of §7 (a rigid `TConst`, not a marked
  `TVar`), so the escape check is a diagnostic rather than soundness code. Spec
  §5.6 (experimental).
- **Stage 0b — constrained existentials via `any C`. ✅ IMPLEMENTED (2026-07-30),
  scoped to single-method, superclass-free classes.** A field `(any ToString)`
  desugars to a fresh existential var + a `ToString` constraint; constructing
  `Shown(v)` resolves and **packs the dictionary** (the class's method
  function-pointers) as a hidden `'p'` heap field, and matching `Shown x` restores
  it so `to_string(x)` dispatches through the packed witness — a Rust trait object /
  GHC hidden dict field, built by redirecting the existing per-method-pointer
  dict-passing path into a ctor field (no new runtime). This is the *useful* form —
  heterogeneous render/log lists. **Scope:** only the `any C` sugar landed (the
  explicit `exists a. <constraint> Shown a` prefix and its constraint keyword are
  deferred — see BACKLOG); and only classes contributing exactly **one** dictionary
  method (single method, no superclasses) are supported — the crude one-slot-per-
  constraint packing is exact there and a richer class is rejected with a located
  error pending the shared witness-slot enumerator (BACKLOG). Missing-instance
  construction (`Shown(NoShow)`) is rejected at the construction site with the
  standard "No instance" diagnostic (§8). Spec §5.6 (experimental).
- **Stage 1 — index refinement (full GADTs).** Deferred behind the
  local-annotations gate; needs the OutsideIn-style local-equality solver and
  constraint-aware exhaustiveness. **XL, out of scope for v0/v1.**

### 5.1 Where Stage 0 is (and isn't) useful

The clear, can't-express-it-otherwise win is **interface values that close over
hidden state**:

```sprout
type Widget =
  | W (exists s. { state: s, update: s -> Event -> s, render: s -> String })
```

A `List Widget` holds a counter (`s = Int`), a text field (`s = String`), and a
clock side by side; each is updated/rendered through the shared interface, and no
caller can see or unify the private state. Neither a closed sum (the set is meant
to be open/extensible) nor Struct-of-Arrays (the state types differ and are
private) can express this. Event handlers, actor/task private state,
iterators/generators, and parser-combinator accumulators share this shape.

The constrained form covers **heterogeneous "render/log these" collections**:

```sprout
type Shown = | Shown (exists a. ToString a => a)
let row = [Shown(42), Shown("hi"), Shown(true)]  # : List Shown, inferred from the Shown ctor
```

(No `let` annotation is used here — Sprout has none yet; the `Shown` constructor
fixes the element type, so inference alone suffices. See §6 for how existential
values are introduced given that limitation.)

**A deliberate counter-example — the ECS.** One might expect Sprout's scene/ECS
("N heterogeneous things, each with its own type," `ecs-v0.md:12`) to want
existentials. It does not: the ECS uses **Struct-of-Arrays** — `Scene` is a
record of fixed, *homogeneous* `MutVec` columns, one typed array per component,
every entity occupying a slot across all of them (`ecs-v0.md` §10.2). SoA
*removes* the heterogeneity instead of storing it. Existentials pay off only when
values of differing types must be stored *together* **and** cannot be
restructured into per-type columns **and** the type set is open. That is a
narrower niche than it first looks — which is why the scoped-tyvar analysis
records "no current demand." **Stage 0 is best framed as an *enabler* for
designs Sprout has not built yet (a widget / scene-graph layer, an extensible
handler registry), not a fix for anything currently in the tree.**

### 5.2 Tie-in with devirtualization

Concrete typeclass instances get their dictionary erased at compile time (the
`docs/devirtualization-v0.md` arc). A *constrained existential* is precisely the
**residual** case where the dictionary cannot be erased: the boxed types differ
at runtime, so the witness must travel *inside the box* and dispatch dynamically
— exactly a Rust trait-object vtable. Stage 0b is thus the honest home for the
one dictionary devirtualization is designed to leave behind, and it adds no new
runtime dispatch *mechanism* — it points the existing dict-passing path at a
heap field.

## 6. Syntax and semantics impact

**Ratified surface syntax (2026-07-30).** The binder is a **constructor-level
`exists` prefix**, with **`any C` as single-field sugar**:

```sprout
# Explicit prefix — scopes over ALL of the constructor's fields.
# Unconstrained (Stage 0a):
type Boxed  = | exists a. Boxed a
# Constrained (Stage 0b):
type Shown  = | exists a. ToString a => Shown a
# Shared hidden state — one `s` spanning several fields (needs the prefix):
type Widget = | exists s. W s (s -> Event -> s) (s -> String)

# `any C` sugar — the ergonomic 90% case (single hidden var, single
# constraint, single field). Lands with Stage 0b (it is inherently
# constrained; there is no unconstrained `any`).
type Shown2 = | Shown2 (any ToString)   #  ≡  | exists a. ToString a => Shown2 a
```

The prefix placement (before the constructor name, not wrapping one field) is
deliberate: it is the only form that lets the hidden variable scope over
multiple fields, which the `Widget` closure-over-state case requires. `any C`
desugars to a fresh prefix binder, so it is strictly a shorthand — the two
forms share one implementation.

- **Syntax / AST.** `TypeConstructor String (List TypeExpr)` (`ast.sprout:99`)
  gains an optional existential-binder payload: the bound variables
  `(List String)` and their constraints `(List TypeConstraint)` (empty for
  Stage 0a). The parser accepts the `exists <vars>. [<constraints> =>]` prefix
  in constructor position and desugars a field-position `any C` into it.
- **Construction (pack).** `Shown(v)` checks `v` against the bound variable
  instantiated to a fresh unification unknown; if constrained, the resolved
  dictionary for that type is captured into the constructor. The hidden variable
  does **not** appear in the result type (`Shown`).
- **Match (unpack).** Matching `Shown(x)` binds a **fresh rigid (skolem)
  variable** for the hidden type, scoped to that branch; `x` has that abstract
  type. It **must not** unify with any concrete or outer type, and **must not
  escape** into the branch result or into any unification variable outliving the
  branch. If constrained, the captured dictionary is brought into scope so class
  methods resolve on `x`.
- **Introduction (how a value gets its existential type).** Sprout has **no
  binding-level type annotation** — the only annotation surface is `fn`
  params/returns (spec §5.1). So a value of an existential type is introduced
  either (a) through a **nominal wrapper's constructor** (`Shown(42)` — the type
  is inferred from the constructor, no annotation needed, as in §5.1), or (b)
  where a **`fn`-boundary expected type** applies. The clean *anonymous* form —
  writing the existential type directly at a binding, `let row : List (any C) =
  …` — is **blocked** until binding-level annotations exist. That is a separate,
  general feature proposed in `docs/binding-annotations-v0.md` (three independent
  features, this arc among them, converge on it); it is the prerequisite for the
  friendlier `any C` spelling, not part of this arc.

## 7. Type-system impact

- Introduces Sprout's **first rigid/skolem types**. **As implemented (Stage 0a),
  a skolem is a fresh, uniquely-named rigid `TConst` (`$sk<n>`)** minted on the
  pattern/unpack path (`unifier.instantiate_ctor_pattern`) — *not* a marked
  `TVar`. This is the key simplification over the original sketch: because the
  existing unifier already treats two `TConst`s as equal only on name identity,
  a skolem unifies with a concrete type or a distinct skolem **nowhere**, so
  rigidity + the escape guard fall out of ordinary unification. No rank/level
  machinery and no post-hoc "did this var stay a variable" scan are needed; the
  "escape check" is purely a **diagnostic** (`types.type_mentions_skolem` at the
  mismatch site → the message in §8). A leaked skolem is an inert, unusable type,
  which is why nominal rigidity is sound without levels.
- The pack (expression) path instantiates the same constructor scheme normally
  (existential vars become flexible unknowns that *hide* the field type); only
  the unpack (pattern) path skolemizes. Existential vars are derived, not marked:
  they are exactly the ctor scheme's `type_vars` that do not appear in its result
  type (a head param always does; an existential never does). No interaction with
  the `!{e}` effect-row variable arose.
- **No** loss of principal types, **no** mandatory signatures, **no** cross-branch
  equalities. Exhaustiveness is unchanged: an existential constructor is still
  one ordinary constructor of its type.

## 8. Error-message impact

- New diagnostic: **existential variable escaping its scope** — the rigid type
  (or a value of it) appears in a branch's result type or a longer-lived unknown.
  Must be a clear, located, loud error (Sprout's "fail loudly" posture), with a
  message that names the offending binding rather than surfacing a raw
  unification failure.
- Constrained form: constructing `Shown(v)` where `v`'s type lacks the required
  instance reuses the existing "no instance for `C t`" diagnostic at the
  construction site.

## 9. Compatibility / migration notes

- **Purely additive.** No existing program's meaning changes; ordinary ADTs are
  the no-existential-binder case.
- **Runtime.** Unconstrained (0a): zero representation change — same tagged
  union, indices erased. Constrained (0b, as implemented): **no new reified
  dictionary struct was needed after all.** Lowering flattens each class into
  *per-method hidden parameters* (`__tc_<Class>_<idx>_<method>`) — i.e. a dict is
  already just a tuple of method function-pointers threaded through the code. Stage
  0b simply **redirects those same pointers into the constructor's payload**: pack
  stores them as extra `'p'` (traced) heap fields on `IRMakeCtor` (ctor arity/fks
  grow by the witness count), and unpack binds them back and seeds the branch's
  forward-map so a class method on the unpacked value dispatches through them
  unchanged. The residual dynamic dispatch is exactly the dictionary
  devirtualization is designed to leave behind (§5.2). Single-method scope keeps
  the witness count a crude one-per-constraint; the general count awaits a shared
  enumerator (BACKLOG).
- **Bootstrap.** Any implementation touches `stdlib/compiler/` (parser + infer),
  so it carries the seed-refresh + likely 2-step-bootstrap tax and the
  smoke/bundle gates — independent of the feature's intrinsic difficulty.

## 10. Effort estimate (T-shirt)

| Scope | Size | Rationale |
|---|---|---|
| Stage 0a (unconstrained) | **M** | Parser/AST + pack + unpack + **escape check** (the one new mechanism); no runtime change. |
| Stage 0b (constrained, `any C`) | **M** (actual) | Estimated L for a "reified dictionary that doesn't exist"; in practice **no struct was needed** — the per-method hidden-param pointers are the dictionary, redirected into a ctor field. Real work was the pack obligation (reused the `where C a` dict-injection path), the unpack forwarding contract (skolem-keyed given marker + a stable `$ex_<var>` forwarding identity shared by infer and lowering), and the ctor arity/fks bookkeeping. Scoped to single-method classes; multi-method awaits the witness-slot enumerator. |
| Stage 1 (index refinement) | **XL** | Local-equality solver + bidirectional checking + mandatory signatures + constraint-aware exhaustiveness; blocked on the local-annotations gate. Out of scope. |

**Recommended path:** ship **0a as a spike** to validate the skolem-escape
machinery cheaply, then add **0b**. Headline for "existentials in Sprout" is
therefore **L**, decomposable to an **M** first cut. Swing risk lives entirely in
the escape check's interaction with `generalize`/`instantiate` and the effect-row
variable — prototype before committing to the number.

## 11. Tests to add when work begins

Illustrative, not yet written (Definition of Ready requires the failing test to
exist *before* implementation):

1. **Parser** — existential binder (constrained and unconstrained) parses into
   the expected AST shape.
2. **Infer, positive** — a heterogeneous `List Shown` constructs and each element
   renders via its own `ToString` dictionary; run to observable output.
3. **Infer, negative (must fail)** — a program that lets the rigid variable
   escape (returns `x` from a `Shown(x)` branch as a concrete type) is rejected
   with the located escape diagnostic. This is the soundness test; it must fail
   on the unfixed compiler for the *right* reason.
4. **Infer, negative** — constructing a constrained existential with a type
   lacking the instance is rejected at the construction site.
5. **Compiler-source gates** — smoke shapes, bundle smoke, seed refresh, per
   AGENTS.md Definition of Done for `stdlib/compiler/` edits.

## 12. Spec/docs status

This doc is **non-normative**. If Stage 0 is ever implemented, `docs/spec-v0.md`
§5.6 (ADT declaration) gains a normative subsection for existential constructors
marked **experimental**, and §5.5 (match) notes the abstract-on-unpack rule.
Stage 1 would require resolving the local-annotations gate first and is not
specified here.

## See also

- `docs/binding-annotations-v0.md` — binding-level `let x : T = e`, the
  prerequisite that unblocks the anonymous `any C` introduction form (§6).
- `docs/scoped-type-variables-analysis-2026-07-26.md` — the local-annotations /
  scoped-signature-variable gate that Stage 1 depends on.
- `docs/hm-typechecker.md` — the `generalize`/`instantiate` model Stage 0 must
  not perturb.
- `docs/devirtualization-v0.md` — the arc whose residual dictionary Stage 0b is
  the natural home for.
- `docs/ecs-v0.md` §10.2 — the SoA design that is the instructive *non*-use of
  existentials.

## Primary sources

- GHC User's Guide §6.4.8, *Generalised Algebraic Data Types* —
  <https://downloads.haskell.org/ghc/latest/docs/users_guide/exts/gadt.html>
- Peyton Jones, Vytiniotis, Weirich, Washburn, *Simple unification-based type
  inference for GADTs*, ICFP 2006.
- Vytiniotis, Peyton Jones, Schrijvers, Sulzmann, *OutsideIn(X): Modular type
  inference with local assumptions*, JFP 2011.
- Garrigue & Rémy, *Ambivalent Types for Principal Type Inference with GADTs*,
  APLAS 2013 — <http://gallium.inria.fr/~remy/gadts/Garrigue-Remy:gadts@aplas2013.pdf>
- GHC User's Guide, *ExistentialQuantification* / GADT syntax —
  <https://ghc.gitlab.haskell.org/ghc/doc/users_guide/exts/gadt_syntax.html>
- The Rust Reference, *Trait object types* —
  <https://doc.rust-lang.org/reference/types/trait-object.html>
