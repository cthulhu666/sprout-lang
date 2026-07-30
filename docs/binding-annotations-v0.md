# Binding-level Type Annotations (`let x : T = e`) — Design (2026-07-30)

**Status:** design proposal. **Non-normative.** Proposes adding a type annotation
to `let` bindings. `docs/spec-v0.md` remains the source of truth; nothing here
changes the language contract until accepted and folded into the spec. No code is
written by this doc — it specifies the work.

---

## 1. Problem statement

Sprout has **no way to annotate a binding**. The only surface where a type may be
written is a function parameter or return (`fn add(x: Int, y: Int) -> Int`,
spec §5.1). Everywhere else:

- `let answer = 42` — spec §5.2 shows only the bare form; there is no `let x : T = e`.
- `where` bindings are, verbatim, *"value bindings only; local type annotations are
  not part of v0"* (spec §5.2, line 124).
- Confirmed in source: the top-level `let` parser `parse_let_decl`
  (`parser.sprout:1573`) goes straight from the name token to `=`, and the AST node
  `LetDecl String Expr SourcePos` (`ast.sprout:138`) has no annotation slot.

This is an anomaly — every mainstream statically-typed language annotates bindings
(§3) — and it blocks or degrades several things:

1. **Existential / interface values (`any C`).** `docs/gadts-v0.md` proposes `any C`
   as Sprout's "interface value" (a boxed value seen only through a typeclass, like
   Rust `dyn Trait`). A heterogeneous collection `[42, "hi", true] : List (any ToString)`
   cannot be introduced at a bare `let`, because inference cannot invent `any ToString`
   on its own (it would try to unify `Int ~ String` and fail). The type must be
   *stated*, and today the only place to state it is a `fn` boundary or a nominal
   `type` wrapper — the latter is the clunky workaround this feature removes.
2. **Scoped type variables.** `docs/scoped-type-variables-analysis-2026-07-26.md`
   identifies "local type annotations" as the upstream gate for that whole feature
   ("scoped type variables are meaningless until `:: a` can be written somewhere in a
   body").
3. **GADTs Stage 1.** Index-refining GADTs require signatures on scrutinizing
   functions (`docs/gadts-v0.md` §4); a binding-level annotation is part of the
   annotation surface that unlocks needs.
4. **Everyday readability and disambiguation** — pinning a tricky `let`'s type,
   documenting intent, and steering a numeric/`Vec` literal at its binding site.

Three independent features converging on the same missing primitive is a strong
prioritization signal: build the general primitive, not a bespoke sugar for each.

## 2. Goals and non-goals

**Goals**

- `let x : T = e` — the value of `e` is checked against the written type `T`, using
  the existing type-expression grammar for `T`.
- Parity with existing ergonomics: a `let`-site annotation should drive the same
  context-directed literal lowerings (`Vec`, `StringTemplate`) that a `fn`-boundary
  annotation already drives.

**Non-goals**

- **No bidirectional / checking-mode inference.** Sprout's `infer_expr`
  (`infer.sprout:461-467`) is pure bottom-up synthesis with no expected-type
  parameter, and this feature does not add one (§6). Annotation is reconciled by
  *unification after synthesis*, exactly as `fn` return annotations already are.
- **`any C` boxing is out of scope** — it belongs to the gadts arc and needs
  machinery this feature does not (§7, and `docs/gadts-v0.md`).
- **`let…in` and `where` annotations are Phase 2** (§5) — those bindings have no AST
  node to hang an annotation on and are deferred.

## 3. Prior-art survey

Binding-level annotations are universal in statically-typed languages; Sprout's
absence is the outlier. (Every row is a stable, standard surface form.)

| Language | Binding annotation | Note |
|---|---|---|
| **OCaml** | `let x : t = e` | Annotation directly on the binding; also `let (x : t) = e`. |
| **Rust** | `let x: T = e;` | Standard; the annotation also drives inference of `e`. |
| **Haskell** | a signature adjacent to the binding — `let x :: T; x = e`, or a pattern signature `let (x :: T) = e` under `ScopedTypeVariables` | Not a single `let x :: T = e` token, but binding-level typing exists. |
| **F#** | `let x : int = e` | Standard. |
| **Scala** | `val x: T = e` | Standard. |

**Consensus:** the annotation sits on (or immediately beside) the binding and is
checked against the initializer. Nothing here is novel — this closes a gap rather
than inventing syntax. Sprout's chosen form, `let x : T = e`, matches the
OCaml/Rust/F#/Scala shape and reuses `:` exactly as parameter annotations already do.

## 4. High-level implementation overview

Phase 1 targets **top-level `let` bindings only** (single-name, irrefutable —
matching the current `LetDecl`). Four small, source-grounded changes:

1. **Parser** — in `parse_let_decl` (`parser.sprout:1573`), after the name, accept an
   optional `:` followed by a type. **Reuse the existing exported type parser**
   `parse_type_expr` (`parser.sprout:186`), which already handles `List a`,
   `Maybe a`, `Result e a`, arrows, and tuples → `ast.TypeExpr` (`ast.sprout:8-13`).
   `:` is an already-lexed symbol (`lexer.sprout:53`) currently unused in binding
   position, so this is an unambiguous new use.
2. **AST** — add a `Maybe TypeExpr` field to `LetDecl` (`ast.sprout:138`); keep
   `decl_pos` (`ast.sprout:153`) and every `LetDecl` match site across the compiler in
   sync. (The do-block `DoLetStep String Expr`, `ast.sprout:54`, has the same shape and
   can gain the slot as an optional Phase 1.5.)
3. **Checker (infer-then-unify)** — where `LetDecl` is typechecked (`typecheck_decl`,
   ~`infer.sprout:3961`/`3992`): resolve the annotation with
   `type_from_ast(T, local_vars)` (`infer.sprout:111-119`), **seeding `local_vars`**
   with fresh `TVar`s for the annotation's free lowercase names (otherwise
   `lookup_type_var`, `infer.sprout:121-128`, degrades a bare `a` to a `TConst`
   constant); synthesize the RHS type; then `unify_types` the inferred type against the
   resolved annotation. This mirrors `check_fn_body` (`infer.sprout:4614-4615`), which
   infers the body then unifies it with the declared return type, guarded by the
   existing `rigidity_violation` check (`infer.sprout:4557-4576`). **No expected-type /
   checking-mode plumbing is introduced.**
4. **Desugar (coercion parity)** — add a `LetDecl` arm to `desugar_decl_i`
   (`desugar_ctx.sprout:85-93`) that threads the annotation's head-constructor name
   (`type_expr_bare_name`, `desugar_ctx.sprout:319-332`) as the expected-name into the
   RHS via `desugar_expr_i` (`desugar_ctx.sprout:112`). This makes the **existing**
   `Vec`/`StringTemplate` literal lowerings (`desugar_ctx.sprout:132-157`) fire at
   `let` sites, exactly as they already do for `fn` params/returns. This is the one and
   only place the "§5.5.1 machinery" is reused.

## 5. Syntax and semantics impact

- **Syntax:** `let <name> : <TypeExpr> = <expr>`. The `: <TypeExpr>` is optional; its
  absence is today's behavior unchanged.
- **Scope (Phase 1):** top-level `let` only, single-name binding. `let…in` bindings
  (`parser.sprout:655`/`686`) and `where` bindings (`parser.sprout:1219`) are desugared
  to `MatchExpr` at parse time and have **no binding AST node**, so annotating them is
  materially more invasive and is deferred to **Phase 2**.
- **Semantics:** unchanged evaluation; the annotation is a *typing constraint only*. A
  bare `let` and an annotated `let` with a correct annotation produce identical
  programs. Generalization is unchanged (the existing value-restricted `generalize`,
  `unifier.sprout:291-311`); the annotation simply adds a unification obligation.

## 6. Type-system impact

- **No new type-system power.** No rigid variables beyond the existing signature
  handling, no local type equalities, no new `Type` variant. `Type` stays as-is
  (`types.sprout:49-54`: `TVar | TConst | TApp | TFunc | TTuple`).
- **Mechanism is infer-then-unify**, the same posture `fn` return annotations already
  use. The checker synthesizes `e`'s type and unifies it with `T`.
- **Honest limitation.** Because there is no downward checking mode, the annotation
  does **not** *drive synthesis* of an expression that needs its expected type to be
  inferable at all. In practice this is a non-issue: ordinary values synthesize fine
  and then unify against `T` (e.g. `let e : List Int = []` — `[]` synthesizes `List a`,
  unifies, binding `a := Int`). The one ergonomic case that needs the *expected type* —
  a `Vec`/`StringTemplate` literal — is handled by the **desugar-pass hook** (§4.4), not
  the checker. Any future need to *check* (not synthesize) an expression against `T`
  would be new bidirectional-inference work, explicitly out of scope here.

## 7. Why `any C` boxing is NOT part of this feature

`docs/gadts-v0.md`'s `any C` needs a value of an existential type to be *introduced*
where `any C` is expected. That is a **type-directed** rewrite, and it cannot ride the
machinery above:

- Boxing `42` into `any ToString` requires selecting the `ToString`-for-`Int`
  dictionary, which requires `42`'s **inferred type**. The context-directed lowering
  in `desugar_ctx.sprout` is a **pre-typecheck** pass that "never sees inferred types"
  (`docs/coercions-and-literals-v1-draft.md:148-159`), so it structurally cannot pick a
  dictionary.
- There is no boxed dictionary *value* to pack today: lowering flattens each class into
  **per-method hidden parameters** (`__tc_<Class>_<idx>_<method>`,
  `lowering.sprout:427-465`). `any C` would first need a **reified** dictionary.
- And there is no `any`/existential type in the language at all (no lexer/parser/AST/
  `types.sprout` support; a new `Type` variant would be required).

So `any C` boxing is a post-typecheck typed-AST rewrite (à la
`resolve_dispatch_typed_expr`, `infer.sprout:4347`) plus a reified-dictionary
representation — genuinely new plumbing, tracked in the gadts arc, **not** this doc.
This feature's contribution to that arc is narrow but essential: it provides the *place
to write* `any C` (a `let` annotation), which is otherwise limited to `fn` boundaries.

## 8. Error-message impact

- A mismatch reuses the existing unification-failure path (inferred type vs. annotated
  type), phrased as "binding `x` has type … but is annotated …".
- An over-general annotation (annotating a binding with a type more general than its
  value supports) reuses the existing `rigidity_violation` diagnostic family
  (`infer.sprout:4557-4576`), the same guard `fn` signatures already use.

## 9. Compatibility / migration notes

- **Purely additive.** Every existing program is unchanged; the annotation is optional
  and bare `let x = e` keeps its current meaning and inference.
- **Bootstrap.** Touches `stdlib/compiler/` (parser + AST + infer + desugar), so an
  implementation carries the seed-refresh + likely 2-step-bootstrap tax and the
  smoke/bundle Definition-of-Done gates — standard for any parser+infer change.

## 10. Tests to add (when implemented)

Per Definition of Ready, a failing test precedes implementation. At minimum:

1. **Parser** — `let x : Int = 5` parses; `LetDecl` carries `Just` the annotation.
2. **Checker, positive** — `let x : Int = 5` typechecks; run to output.
3. **Checker, negative** — `let x : Int = "s"` is rejected with the mismatch diagnostic
   (must fail on unannotated-baseline for the right reason).
4. **Polymorphic annotation** — `let f : List a = list_empty()` seeds a fresh `a`
   (regression against the `TConst` degradation of §4.3).
5. **Coercion parity** — `let xs : Vec Int = [1, 2, 3]` lowers to `vec_from_list([...])`
   via the desugar hook (the observable payoff of §4.4).
6. **Compiler-source gates** — smoke shapes, bundle smoke, seed refresh (AGENTS.md
   Definition of Done for `stdlib/compiler/` edits).

## 11. Spec/docs status

Non-normative. If implemented, `docs/spec-v0.md` §5.2 gains a `let : T` subsection
marked **experimental**, and §5.1's "annotations optional" note is cross-referenced.
Phase 2 (`let…in`/`where`) would be specified separately when undertaken.

## See also

- `docs/gadts-v0.md` — the existential/`any C` arc this feature unblocks (its §6
  introduction forms and §10 cost note reference this doc).
- `docs/scoped-type-variables-analysis-2026-07-26.md` — flags "local type annotations"
  as the upstream gate; this feature is that gate's first concrete step.
- `docs/coercions-and-literals-v1-draft.md` — the context-directed literal-lowering
  mechanism (`Vec`/`StringTemplate`) that §4.4 extends to `let` sites.
