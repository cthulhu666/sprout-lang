# Effect System v1 Draft

Status note:

- The minimal `!{IO}` effect system and restricted singleton effect variables
  such as `!{e}` are now implemented in v0.
- This document is now about the next effect milestone after that baseline, not
  about introducing function effects for the first time.

This document is a draft design for Sprout v1 effect handling.

It is not part of normative v0. Its purpose is to define the first major v1
language milestone after the v0 core is stabilized.

## 1. Problem Statement

Sprout v0 now supports explicit closed effects on function types with the
built-in `IO` label plus restricted singleton effect variables such as `!{e}`.
That is a good baseline, but it still does not provide:

- purity boundaries,
- effect tracking,
- explicit sequencing guarantees at the type level,
- a principled way to extend effects beyond `IO`.

This leaves an important gap between the language’s safety goals and its effect
story.

## 2. Goals

1. Extend the current effect system without changing Sprout’s default strict evaluation.
2. Make effectful code explicit in function types.
3. Keep the beginner model small enough to explain in one pass.
4. Add richer rows or additional labels later, if they remain worth the complexity.
5. Preserve readable diagnostics and predictable execution order.

## 3. Non-Goals

1. Do not introduce laziness by default.
2. Do not add a full algebraic-effects-and-handlers design in the first v1 milestone.
3. Do not require higher-kinded types or a typeclass-based effect library.
4. Do not redesign the entire stdlib in the first pass beyond what effect typing requires.

## 4. Proposed Direction

The next effect milestone extends the current v0 effect system.

Illustrative surface model:

```sprout
fn read_name() -> String !{IO}
fn parse_age(raw: String) -> Result String Int !{}
fn main() -> Unit !{IO}
```

Interpretation:

- `!{}` means the function is pure.
- `!{IO}` means the function may perform IO.
- `!{e}` means a singleton effect variable.
- Effects are attached to function types, not encoded as ordinary result values.

This extends the current v0 convention where closed effects such as `!{IO}` are
already part of function types.

## 5. Core Syntax

Draft syntax for function effects:

```sprout
fn inc(x: Int) -> Int = x + 1
fn print_name(name: String) -> Unit !{IO} = print(name)
```

Draft syntax for effect-polymorphic function types:

```sprout
fn apply_twice(f: Int -> Int !{e}, x: Int) -> Int !{e} =
  f(f(x))
```

Current baseline:

- effect syntax stays row-shaped: `!{...}`
- pure-by-default remains the rule
- mixed/open rows remain deferred

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

- extend function types from `a -> b !{IO}` to richer row forms,
- model `e` as an effect-row variable.

## 8. Diagnostics

The next effect milestone should prioritize a few high-value diagnostics:

1. Calling an effectful function from a pure context.
2. Declaring a function pure when its body performs effects.
3. Declaring a narrower effect set than the body requires.
4. Failing to propagate an effect variable through a higher-order helper.

Example style:

- what failed,
- where the effect escaped,
- what signature change would fix it.

## 9. Builtins and Stdlib Impact

The next migration target is straightforward:

1. Existing `!{IO}` builtins remain valid.
2. Pure helpers in `stdlib/prelude.sprout` stay pure.
3. Result-oriented error handling remains orthogonal to effects.
4. Higher-order helpers gain effect-polymorphic signatures where needed.

Illustrative baseline:

```sprout
fn main() -> Unit !{IO} = print("hello")
```

Next milestone direction:

- keep the existing closed-effect syntax,
- add effect polymorphism,
- and decide whether additional built-in effect labels beyond `IO` are worth
  exposing.

## 10. Compatibility and Migration

Migration from the current v0 baseline should be incremental:

1. Existing `!{IO}` code remains valid.
2. Higher-order helpers gain effect-polymorphic signatures.
3. Documentation explains which new forms are additive versus required.

## 11. Milestone Plan

Proposed next effect milestone:

1. Explore whether open rows or only row variables are needed next.
2. Improve diagnostics around higher-order effect propagation.
3. Evaluate whether additional built-in effect labels beyond `IO` are worth exposing.
4. Update docs and examples.

Deferred beyond the first milestone:

- user-defined effect kinds beyond the initial built-in set,
- effect handlers,
- capability-style resource typing,
- interaction with modules/typeclasses if those become normative first.

## 12. Open Questions

1. Should the next effect milestone add open rows, or keep only singleton row variables?
2. Should local inference infer richer effect sets, or require more explicit
   function-level effect annotations for clearer diagnostics?
3. Should Sprout expose more than one built-in effect label after `IO`, or keep
   the surface minimal longer?
