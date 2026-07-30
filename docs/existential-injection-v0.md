# Implicit existential injection — analysis & design (2026-07-30)

**Status:** analysis / design exploration. **Non-normative.** This doc records
a proposed ergonomic layer on top of constrained existentials (`docs/gadts-v0.md`
Stage 0b): letting the `any C` constructor be *inferred* at a syntactically-known
expected type, so callers write bare values instead of explicit `Wrap(...)`. It
does **not** commit Sprout to the feature — `docs/spec-v0.md` remains the
normative source of truth. Nothing here changes the language contract until
approved and implemented.

---

## 1. Problem statement

Stage 0b makes a constrained existential value only introducible through its
**explicit constructor**:

```sprout
type Cell = | Cell (any ToString)

fn row() -> List Cell =
  [Cell(42), Cell("ready"), Cell(true), Cell(Celsius(21))]
```

The `Cell(...)` wrapper on every element is pure boilerplate here: the return type
`List Cell` already fixes what each element must become. The question is whether

```sprout
fn row() -> List Cell =
  [42, "ready", true, Celsius(21)]        # ← inject Cell(...) around each element
```

can be made to typecheck, given that the expected type is **syntactically known**
at the return position.

Two independent things must hold for the bare form to work, and Sprout satisfies
neither today:

1. **The expected type must reach each element.** Sprout is classic
   Hindley–Milner with **outward-only** inference: a list's type is built from its
   elements, which are unified *with each other first*. `[42, "ready", …]` fails at
   "unify `Int` with `String`" **before** the `List Cell` annotation is ever
   consulted. Pushing the expected `Cell` *into each element position first* is
   *checking-mode* (bidirectional) inference — the exact machinery
   `docs/gadts-v0.md §4` and `docs/scoped-type-variables-analysis-2026-07-26.md`
   identify as Sprout's missing prerequisite.
2. **A constructor must be inserted implicitly.** `42 : Int` is **not** a `Cell`;
   `Cell(42) : Cell` is a distinct constructed value. So this is an *implicit
   coercion* (auto-wrapping a conforming value into the existential), not
   subtyping. Sprout has no implicit coercions today — construction is explicit.

## 2. Goals and non-goals

**Goals**

- Allow the `any C` wrapper to be **elided** where an expected existential type is
  **syntactically present** (a `fn` param/return annotation, a `let : T`, and the
  element/argument positions those annotations reach).
- Reuse Stage 0b's pack path unchanged: an injected `Cell(e)` must typecheck,
  resolve the `C` instance, and pack the dictionary **exactly** as if the user had
  written `Cell(e)`. Injection is *syntactic sugar over an existing feature*, not
  new runtime or dictionary semantics.
- Keep the explicit `Cell(...)` form valid forever.

**Non-goals**

- **General bidirectional inference is still out of scope.** Injection fires
  *only* at a syntactically-known expected type; it is a localized checking-mode
  fallback, not a pervasive mode. A bare `let xs = [42, "ready"]` with **no**
  annotation stays a type error (nothing names the target).
- No implicit injection where the target existential is **ambiguous** (see §6).
- Full GADTs / index refinement — unrelated, out of scope.

## 3. Prior-art survey

Every row verified against the cited primary source.

- **Swift — implicit (the "yes" case).** An existential `any P` is a box that
  holds a value of a conforming type; a concrete conforming value converts into it
  **without an explicit wrapper**. SE-0335 (*Introduce existential `any`*) uses
  exactly this throughout — e.g. `let p2: any P = S()` binds the concrete `S()`
  straight to `any P`. Collection literals get their element type from context and
  check each element against it: Swift's compiler notes that a literal's expected
  contextual type drives per-element conversion — "something else that is
  literal-convertible can then implicitly convert to the proper type"
  (`swift/docs/Literals.md`) — so `let xs: [any CustomStringConvertible] = [1,
  "a", true]` boxes each element. **Takeaway: the exact ergonomic Kuba asked for
  exists in a mainstream language, driven by a *contextual* (expected) type.**

- **Rust — explicit, coercion-site-only (the "no, but structured" case).** `T` →
  `dyn Trait` is an *unsized coercion* that "can only occur at certain coercion
  sites… places where the desired type is explicit" (Rust Reference, *Type
  coercions*). Array literals **are** coercion sites — "each sub-expression in the
  array literal is a coercion site for coercion to type `U`" — but the pointer
  still must be formed explicitly: you write `[Box::new(1), Box::new("s")]`, never
  `[1, "s"] as [Box<dyn ToString>]`. **Takeaway: coercion is gated to
  syntactically-known target positions — precisely the boundary this proposal
  adopts — but Rust stops short of auto-forming the box.**

- **Haskell — explicit, always.** `ExistentialQuantification` introduces an
  existential only by **applying its data constructor** (`MkT e`); there is no
  implicit conversion of a conforming value into the box (GHC User's Guide,
  *Existentially quantified data constructors*; already cited in
  `docs/gadts-v0.md §3.2`). **Takeaway: the pure-HM lineage keeps construction
  explicit — the conservative default Sprout ships in Stage 0b.**

**Consensus.** The design axis is *where* the target type is known and *whether*
the wrapper is auto-formed. Rust and Swift agree the **coercion site is a
syntactically-known target** (never full inference); they differ only on
auto-forming the box (Swift yes, Rust no). This proposal takes Rust's *boundary*
and Swift's *auto-forming* — the narrowest form that delivers the ergonomic.

## 4. Why this is smaller than "add bidirectional inference"

The scary framing is "Sprout would need checking mode." The narrow truth: it needs
checking mode **only at boundaries where the expected type is already written
down**, and only as a *fallback* when ordinary inference would otherwise fail.
Sprout already has a precedent for expected-type-driven elaboration: the
`desugar_ctx` **coercion-parity** seam threads a syntactic expected-type *name*
into an expression and rewrites `Vec` literals and string templates accordingly
(`type_expr_bare_name(anno)` → `desugar_expr_i(body, expected, …)`; the same hook
`let x : T = e` and `fn` returns use). Existential injection is the same shape of
rewrite — "at an expected type `W`, rewrite element `e` to `W(e)`" — with two
added requirements the Vec/template cases don't have: it must know `W` is an
existential wrapper, and it must fire **per element before the list's homogeneity
check**.

## 5. High-level implementation overview (for approval before any edit)

**One elaboration rule, applied at a fixed set of syntactic boundaries.**

- **Trigger.** At a position with a syntactically-known expected type whose
  (element) type is a **single-constructor** existential wrapper
  `W = | W (any C)` (equivalently `| exists a. C a => W a`), an expression `e`
  that does **not** already have type `W` is rewritten to `W(e)`. The rewrite then
  typechecks by the ordinary Stage 0b rules: it requires a `C` instance for `e`'s
  type and packs the witness. **No new pack/unpack machinery** — injection is a
  front-end rewrite; everything downstream is Stage 0b unchanged.
- **Boundaries (v0 scope, in priority order).**
  1. **Function arguments.** `fn log(cells: List Cell)` called `log([42, "hi"])` —
     the param type is always syntactically present; highest value, least
     ambiguity.
  2. **`fn` return annotation.** The `row()` example.
  3. **`let x : T = …`.** Rides the binding-annotation seam directly.
  Each propagates *one level* into a list/collection literal's element positions
  (and, recursively, nested literals) — not into arbitrary sub-expressions.
- **Two candidate homes, with a recommendation.**
  - **(A) Checker-time (recommended).** Add a localized "check `e` against expected
    existential `W`" rule in `infer`: if `e`'s inferred type is not `W` but `W` is
    a single-ctor `any C` wrapper and `e`'s type has a `C` instance, elaborate
    `W(e)` (reusing the Stage 0b construction path) instead of failing
    unification. This is *checking mode confined to one rule* and has the full type
    environment (it already knows `W`'s scheme + the `C` instance). It fires only
    on the failure path, so well-typed code is untouched. Cost: the first
    checking-mode hook in `infer` — must be written so it cannot leak into general
    inference (guarded strictly on "expected type is syntactically an existential
    wrapper").
  - **(B) Desugar-time (parallel to Vec/template).** Precompute the set of
    existential-wrapper ctor names + their field class, thread the expected-type
    *name* through `desugar_ctx`, and rewrite bare elements to `W(e)` before
    inference. Sidesteps `infer` entirely but **duplicates type knowledge in the
    desugarer** (which ctors are existential) and cannot see instance availability,
    so a missing-`C` case would surface as a worse downstream error. Prefer (A).
- **Prototype first.** Build the `log([42, "hi"])` argument case end-to-end behind
  (A), confirm it lowers identically to the explicit form, then widen to return/let.

## 6. Syntax and semantics impact

- **Syntax:** none — no new surface. Bare element/argument expressions become
  admissible where they were rejected.
- **Semantics:** `[e0, …]` at expected `List W` (single-ctor `any C` wrapper) means
  `[W(e0), …]`; `f(e)` at expected param `W` means `f(W(e))`. Defined by
  desugaring to the existing explicit form — **no new dynamic semantics**.
- **Determinism / ambiguity.** Injection fires **only** when `W` names a *unique*
  constructor. A multi-constructor type (`type T = | A (any C) | B Int`) is
  **not** an injection target — the wrapper is ambiguous — and the bare form stays
  an error there. This keeps the rule predictable and rejects the "which
  constructor?" hazard by construction.
- **Layering with existing coercions.** Where an element is *itself* an existing
  coercion target (a `Vec` literal, a string template), the existential injection
  composes outermost (`W(<coerced e>)`); the two seams do not conflict because
  injection keys on the expected type being an existential wrapper specifically.

## 7. Type-system impact

- Introduces Sprout's **first checking-mode rule** (approach A) — but *strictly
  bounded*: it applies only when the expected type is a syntactically-present
  single-ctor existential wrapper and ordinary inference has failed to make `e : W`
  directly. It adds **no** new type, no rigidity, no principality loss for any
  program that does not use it: injection is a pure fallback elaboration.
- The injected `W(e)` reuses Stage 0b's pack path verbatim, so the constraint
  `C τ` (for `e : τ`) is discharged by the *existing* obligation machinery. If
  `τ` lacks a `C` instance, the same "No instance of `C` for `τ`" diagnostic fires
  — at the element, not as a cryptic unification failure.
- No interaction with the effect row; injection is value-level and effect-neutral.

## 8. Error-message impact

- **Missing instance:** `log([42, some_no_show])` → "No instance of `ToString` for
  `NoShow`" located at the offending element (reuses Stage 0b §8).
- **Ambiguous target:** if a future multi-ctor type is somehow reached, a clear
  "cannot inject an existential wrapper for `T`: more than one constructor" rather
  than a silent wrong choice.
- **No regression on the common typo.** A bare heterogeneous literal with **no**
  expected existential (`let xs = [42, "hi"]`) must keep today's
  "elements have different types" error — injection must not fire without a
  syntactic existential target, so an accidental heterogeneous list is still caught.

## 9. Compatibility / migration notes

- **Purely additive.** Injection fires only where code currently **fails** to
  typecheck *and* a syntactic existential target is present, so no existing
  program changes meaning. The explicit `Cell(...)` form is always valid and is
  the desugared target.
- **Tension to weigh (the reason this is a decision, not a default).** Implicit
  injection cuts against Sprout's *explicit-over-implicit* posture: a reader of
  `log([42, "hi"])` cannot see the `Cell` boxing without knowing `log`'s signature
  and the injection rule. The mitigations — unique-constructor-only, instance
  required, syntactically-known target only, failure-path-only — keep it
  predictable, but it *is* implicitness. This is the crux tradeoff for the call.

## 10. Effort estimate (T-shirt)

| Scope | Size | Rationale |
|---|---|---|
| Argument-position injection only (approach A) | **S–M** | One guarded checker rule on the unify-failure path + reuse of Stage 0b construction; the risk is confining checking-mode to the rule. |
| + return / `let : T` boundaries | **M** | Same rule at the binding-annotation seam; mostly plumbing the expected type to element positions. |
| Desugar-time variant (B) | **M** | Avoids `infer` but duplicates existential-ctor knowledge + worse missing-instance errors; not recommended. |

## 11. Tests to add when work begins

1. **Positive (behavioral).** `fn log(cells: List Cell)` called `log([42, "hi",
   true])` runs and renders identically to the explicit `[Cell(42), …]` form.
2. **Return / let boundaries.** The `row()` example and `let xs : List Cell = [42,
   …]` both compile and run.
3. **Negative — missing instance.** A bare element whose type lacks `C` is rejected
   at the element with the "No instance" diagnostic.
4. **Negative — no target, no injection.** `let xs = [42, "hi"]` (no annotation)
   still fails with the "different element types" error (guards against injection
   leaking into general inference).
5. **Ambiguity.** A multi-constructor type is not an injection target (bare form
   rejected).
6. **Lowering parity.** Injected and explicit forms emit identical IR
   (differential), proving injection is sugar.

## 12. Spec/docs status

Non-normative. If implemented, `docs/spec-v0.md §5.6` (the existential subsection)
gains an **experimental** paragraph on expected-type injection, marked as fallback
elaboration; `docs/gadts-v0.md` cross-links here.

## See also

- `docs/gadts-v0.md` — constrained existentials (Stage 0b), the feature this
  sugars.
- `docs/scoped-type-variables-analysis-2026-07-26.md` — the general
  local-annotations / checking-mode gate this proposal deliberately *avoids* by
  staying a bounded fallback.
- `docs/binding-annotations-v0.md` — the `let : T` seam one boundary rides.

## Primary sources

- SE-0335, *Introduce existential `any`* —
  <https://github.com/swiftlang/swift-evolution/blob/main/proposals/0335-existential-any.md>
- Swift compiler docs, *Literals* (collection-literal contextual typing) —
  <https://github.com/apple/swift/blob/main/docs/Literals.md>
- The Rust Reference, *Type coercions* (coercion sites; array-literal
  sub-expressions; unsized `Box<T>`→`Box<dyn Trait>`) —
  <https://doc.rust-lang.org/reference/type-coercions.html>
- GHC User's Guide, *Existentially quantified data constructors* —
  <https://downloads.haskell.org/ghc/latest/docs/users_guide/exts/existential_quantification.html>
