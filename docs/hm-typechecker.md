# HM Typechecker Guide (Human-Friendly)

This guide explains the current Sprout typechecker in practical terms, without assuming prior language-theory background.

## What "HM" means here

HM (Hindley-Milner) is a style of static typing where the compiler can infer many types automatically.

In Sprout v0 this means:

- You still get compile-time type safety.
- You write fewer annotations for local values.
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

File: `sprout/typechecker.py`

- `TVar`: unknown type variable (like `t0`, `t1`).
- `TConst`: concrete type (`Int`, `Bool`, `String`).
- `TFunc`: function type (`a -> b`).
- `TApp`: type application (`Maybe a`, `IO Unit`).
- `Scheme`: generalized polymorphic type (`forall a. a -> a`).

## Inference workflow

1. Parse source into AST.
2. Build type info for `type` declarations (ADTs and constructors).
3. Seed environment with builtins (`print`) and constructors (`Just`, `Nothing`, etc.).
4. Infer/check each function body.
5. Infer/check top-level `let` values.
6. Report success with inferred types, or fail with first clear error.

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

## Current limitations (intentional v0)

- No typeclasses/traits.
- Exhaustiveness checking is basic (ADT constructor coverage + catch-all).
- Diagnostics are improving but still early-stage.
- No effect typing beyond the simple `IO a` surface type.

## Practical reading order for contributors

1. `typecheck_program` (entrypoint)
2. `infer_expr` (expression typing)
3. `infer_pattern` + `ensure_exhaustive_match`
4. `unify`, `bind_var`, `apply`
5. `generalize` / `instantiate`

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
