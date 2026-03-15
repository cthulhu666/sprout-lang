# Effect System v1 Draft

This document is a draft design for Sprout v1 effect handling.

It is not part of normative v0. Its purpose is to define the first major v1
language milestone after the v0 core is stabilized.

## 1. Problem Statement

Sprout v0 uses `IO a` only as a lightweight surface annotation. That keeps v0
small, but it does not provide:

- purity boundaries,
- effect tracking,
- explicit sequencing guarantees at the type level,
- a principled way to extend effects beyond `IO`.

This leaves an important gap between the language’s safety goals and its effect
story.

## 2. Goals

1. Add a real effect system without changing Sprout’s default strict evaluation.
2. Make effectful code explicit in function types.
3. Keep the beginner model small enough to explain in one pass.
4. Support `IO` first, with room for more effects later.
5. Preserve readable diagnostics and predictable execution order.

## 3. Non-Goals

1. Do not introduce laziness by default.
2. Do not add a full algebraic-effects-and-handlers design in the first v1 milestone.
3. Do not require higher-kinded types or a typeclass-based effect library.
4. Do not redesign the entire stdlib in the first pass beyond what effect typing requires.

## 4. Proposed Direction

The first v1 milestone introduces explicit effect rows on function types.

Illustrative surface model:

```sprout
fn read_name() -> String !{IO}
fn parse_age(raw: String) -> Result String Int !{}
fn main() -> Unit !{IO}
```

Interpretation:

- `!{}` means the function is pure.
- `!{IO}` means the function may perform IO.
- Effects are attached to function types, not encoded as ordinary result values.

This replaces the v0 convention where `IO a` is just a surface annotation.

## 5. Core Syntax

Draft syntax for function effects:

```sprout
fn inc(x: Int) -> Int !{} = x + 1
fn print_name(name: String) -> Unit !{IO} = print(name)
```

Draft syntax for effect-polymorphic function types:

```sprout
fn apply_twice(f: Int -> Int !e, x: Int) -> Int !e =
  f(f(x))
```

Open question:

- Whether the syntax should be `-> T !{IO}`, `-> T raises {IO}`, or another
  beginner-friendlier form.

## 6. Semantics

Evaluation remains strict and left-to-right.

The effect system changes what programs are accepted, not the basic runtime
order. In particular:

1. Pure expressions may not call effectful functions.
2. Effectful expressions may call pure or effectful functions.
3. `main` is expected to be effectful when it performs observable work.
4. Effects happen when effectful expressions are evaluated under the existing
   strict execution model.

## 7. Typing Model

At a high level:

1. Every function type carries an effect set or effect variable.
2. Calling a function contributes its effect set to the calling context.
3. A function declared pure must typecheck with an empty effect set.
4. Effect-polymorphic helpers may abstract over effect variables.

Illustrative examples:

```sprout
fn id(x: a) -> a !{} = x

fn log_and_return(x: Int) -> Int !{IO} =
  keep(print_int(x), x)
```

Potential internal representation:

- extend function types from `a -> b` to `a -> b !e`,
- model `e` as either a concrete closed set (`{IO}`) or an effect variable.

## 8. Diagnostics

The first v1 milestone should prioritize a few high-value diagnostics:

1. Calling an effectful function from a pure context.
2. Declaring a function pure when its body performs effects.
3. Declaring a narrower effect set than the body requires.
4. Failing to propagate an effect variable through a higher-order helper.

Example style:

- what failed,
- where the effect escaped,
- what signature change would fix it.

## 9. Builtins and Stdlib Impact

The initial migration target is straightforward:

1. Builtins like `print`, terminal IO, file IO, TCP, and HTTP become `!{IO}`.
2. Pure helpers in `stdlib/prelude.sprout` stay pure.
3. Result-oriented error handling remains orthogonal to effects.
4. Existing v0 `IO a`-annotated APIs migrate to explicit effect annotations.

Illustrative migration:

v0:

```sprout
fn main() -> IO Unit = print("hello")
```

v1 draft:

```sprout
fn main() -> Unit !{IO} = print("hello")
```

## 10. Compatibility and Migration

Migration from v0 should be mostly mechanical:

1. `-> IO T` becomes `-> T !{IO}` for effectful APIs.
2. Pure functions that never relied on `IO` stay unchanged except for optional
   explicit `!{}` annotations.
3. Documentation must explain that effects moved from result types into function
   signatures.

Compatibility note:

- v0 programs using `IO` as an ordinary annotation will need signature updates.

## 11. Milestone Plan

Proposed first v1 milestone:

1. Finalize effect syntax.
2. Add effect annotations to the parser and AST.
3. Extend the typechecker with effect checking for pure vs `IO`.
4. Migrate builtins and a minimal stdlib slice.
5. Add effect diagnostics and conformance tests.
6. Update docs and examples.

Deferred beyond the first milestone:

- user-defined effect kinds beyond the initial built-in set,
- effect handlers,
- capability-style resource typing,
- interaction with modules/typeclasses if those become normative first.

## 12. Open Questions

1. Should purity be implicit by default, or should v1 require explicit `!{}`
   on function signatures?
2. Should local inference infer effect sets, or require function-level effect
   annotations for clearer diagnostics?
3. Should `main` require `!{IO}` explicitly, or may it omit the annotation as
   a special case?
4. Should effect syntax be row-based from day one, or start with a single
   built-in `IO` effect and generalize later?
