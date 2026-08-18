# HM Typechecker Guide (Human-Friendly)

This guide explains the current Sprout typechecker in practical terms, without assuming prior language-theory background.

Scope note:

- This guide explains the core HM-style checker that underpins normative v0.
- Prototype features that sit outside `docs/spec-v0.md`, including the current
  typeclass surface, should be treated as implementation extensions rather than
  part of the stable v0 spec.

## What "HM" means here

HM (Hindley-Milner) is a style of static typing where the compiler can infer many types automatically.

In Sprout v0 this means:

- You still get compile-time type safety.
- You write fewer annotations for local values.
- Ordinary functions can often omit parameter and return annotations.
- Functions can be generic (polymorphic) without manual templates.

## Mental model

The checker is solving constraints.

Example:

```sprout
fn inc(x: Int) -> Int = x + 1
```

From `x + 1`, it adds constraints like:

- `x` must be `Int`
- result must be `Int`

If constraints conflict, you get a type error.

## Core building blocks in code

Self-hosted typechecker source lives in `stdlib/compiler/`:

- `types.sprout` defines `Type` (`TVar`, `TConst`, `TFunc`, `TApp`, `TTuple`), `Scheme`, and `Effect`.
- `unifier.sprout` is the pure substitution + unification engine.
- `infer.sprout` runs constraint generation and solving over the AST.
- `checker.sprout` wraps `infer.typecheck_decls` behind a `CheckResult` ADT.

The terms:

- `TVar`: unknown type variable (like `t0`, `t1`).
- `TConst`: concrete type (`Int`, `Bool`, `String`).
- `TFunc`: function type (`a -> b`).
- `TApp`: type application (`Maybe a`, `IO Unit`).
- `Scheme`: generalized polymorphic type (`forall a. a -> a`).

`Effect` is a first-class field on `TFunc`, not a surface type constructor, and it
is checked — see §Effects below.

## Inference workflow

1. Parse source into AST.
2. Build type info for `type` declarations (ADTs and constructors).
3. Seed environment with builtins (`print`) and constructors (`Just`, `Nothing`, etc.).
4. Infer/check each function body.
5. Infer/check top-level `let` values.
6. Report success with inferred types, or fail with first clear error.

In v0, inferred top-level `let` types are also checked for the language's
limited top-level effect rule: a top-level binding may not have type `IO a`.

## Unification (the engine)

`unify(left, right)` tries to make two types equal.

- `Int` unifies with `Int`.
- `a -> b` unifies structurally with another function type.
- Unknown variables (`TVar`) can be bound to concrete/compound types.

If impossible, it raises `TypeCheckError`.

### Occurs check

Prevents impossible recursive types (like `a = a -> a`).

This is why `bind_var` rejects cases where a variable appears inside the type it is being bound to.

## Generalization vs instantiation

This is the key HM idea.

- `generalize`: when defining a value, free type variables become universally quantified (`forall ...`).
- `instantiate`: when using that value later, quantified variables are replaced with fresh unknowns.

This lets one function work at multiple types safely.

## Effects

An effect is not a type — it is a **third field on the arrow**, alongside the parameter
and result types. `fn f(x: Int) -> Int !{IO}` is `TFunc(Int, Int, EffectIO)`; the same
function without the annotation is `TFunc(Int, Int, EffectPure)`. `Effect` has four
forms in `types.sprout`: `EffectPure`, `EffectIO`, `EffectVar` (the singleton variable
`!{e}`), and `EffectRow` (which spec §7 does not define, and which exists so the checker
can *reject* it with a good message rather than fail to parse it).

Effects are inferred and enforced, and the two halves are deliberately separate.

**Inference is permissive.** `unify_effects` (`unifier.sprout`) binds effect variables
and treats `Pure` and `IO` as compatible in both directions — it never rejects a program
on its own. At arrow positions it is wrapped by `unify_arrow_effects`, which *swallows*
even the one error `unify_effects` can produce. That looks strange until you see why:
Sprout's rule at a declaration boundary is **subsumption, not equality**. A pure body
under an `!{IO}` signature is legal and idiomatic, so two arrows whose effects differ are
not thereby a type error. Keeping rejection out of unification means turning effect
inference on can make a *report* wrong, but never the accept/reject decision.

Effect variables are quantified like type variables: `Scheme` carries
`scheme_effect_vars` alongside `scheme_vars`, and `instantiate` freshens both.

**Enforcement happens in exactly one place** — `enforce_effects` in `checker.sprout`,
a post-pass over the declared-vs-inferred census that inference accumulated:

- **Rule 8** — a body that performs IO under a pure signature is rejected:

  ```
  3:1: ERROR: check: `main.save` performs IO but is declared pure — add `!{IO}` after its return type (spec-v0.md §7 rule 8)
  ```

  Every offending declaration is named, not just the first.
- **Rule 9** — a signature may quantify at most one effect variable, and may not
  write a row. Two variables gets you their source spellings back, not `$e0`/`$e1`.

It is a post-pass rather than an error raised at the boundary for two reasons: it reports
*every* offending declaration in one compile (migrating a codebase is then a morning, not
a week), and it leaves `--phase effects` — the census instrument — working, because that
phase calls the un-enforcing entry point directly. Both paths share
`unifier.effect_report_is_gap`, so the census cannot drift from what the checker rejects.

Two consequences worth knowing:

- **Over-declaring is free.** The rule is *inferred ⊑ declared*, not equality. `!{IO}` on
  a function that turns out to be pure is accepted.
- **`panic` is pure.** Aborting is not an effect (spec §6), so an unreachable-by-invariant
  arm does not make its function effectful. `docs/effect-enforcement-v0.md` §6 has the
  rationale; `docs/guidelines.md` §2 has the discipline this removes the brake from.

Rows and open effects are still unsupported: an annotation is one concrete effect
(`!{IO}`), one effect variable (`!{e}`), or omitted for purity.

## ADTs and pattern matching

For:

```sprout
type Maybe a =
  | Just a
  | Nothing
```

the checker creates constructor types:

- `Just: forall a. a -> Maybe a`
- `Nothing: forall a. Maybe a`

In `match`, each branch:

1. checks pattern compatibility with scrutinee type,
2. extends branch-local environment with pattern-bound names,
3. checks branch expression type,
4. unifies all branch result types.

The checker also performs a basic ADT exhaustiveness check and reports missing constructors.

## Current limitations

Corrected 2026-08-13. This section previously claimed "No typeclasses/traits" and
"No effect system beyond the simple `IO a` surface annotation"; both were long stale.

- **Typeclasses exist** and are implemented by dictionary passing: `class`/`instance`
  declarations, `where C a` constraints on functions and instances, superclasses,
  overlapping-instance rejection, and a post-resolve verifier
  (`stdlib/compiler/verify_dispatch.sprout`). `Scheme` carries a first-class constraint
  list. Concrete instances are devirtualized (`docs/devirtualization-v0.md`), so a test
  using a concrete type does not exercise the dictionary path at all — write a function
  polymorphic over the class to do that.
- **Effects are enforced.** Corrected 2026-08-18; this bullet previously said effects
  were represented but not enforced and that `unify_effects` had zero call sites. Both
  became false on 2026-08-16, when spec §7 rules 8 and 9 landed as real rejections. See
  §Effects below for how it works.
- **Exhaustiveness is per-column, not a full usefulness matrix.** W5 checks each column's
  value space (`Bool` needs both literals, `Int`/`String`/`Char` need a catch-all, nested
  constructor fields recurse), plus a sound top-level unreachable-branch check. A gap
  arising only from a *combination* of field values — `(true, true) | (false, false)` on
  `(Bool, Bool)`, or the catch-all-masked `(O, _) | (_, Z)` — is not rejected; it aborts
  at runtime. Spec §5.5 documents this as intentional v0 over-acceptance.
- The type system also carries **ownership/parameter modes** (`consuming`/`borrowing`/`once`,
  invariant on the arrow), **linear types** (`docs/linear-task-v0.md`), **existentials and
  skolems** (`docs/gadts-v0.md`), and **records with functional update** (`docs/records-v0.md`).
- Diagnostics are improving but still early-stage; internal names (`$sk<n>`, `$t<n>`) can
  still leak into user-facing messages.

Known soundness gaps at the checker's seams are tracked in `BACKLOG.md` §1 under
"Type-system review findings" and under "Dispatch Soundness & Diagnostics"; the review that
found them is `docs/type-system-review-2026-08-13.md`.

## Practical reading order for contributors

1. `typecheck_program` (entrypoint)
2. `infer_expr` (expression typing)
3. `infer_pattern` + `ensure_exhaustive_match`
4. `unify`, `bind_var`, `apply`
5. `generalize` / `instantiate`
6. `unify_effects` / `unify_arrow_effects` (unifier) then `enforce_effects` (checker)

## Quick examples

Valid:

```sprout
fn id(x: a) -> a = x
let n = id(42)
let s = id("hi")
```

Invalid:

```sprout
fn bad(x: Int) -> Int =
  if x > 0 then x else false
```

The two `if` branches do not unify (`Int` vs `Bool`).
