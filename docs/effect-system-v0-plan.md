# Effect System v0 Plan

This document proposes promoting a minimal real effect system into Sprout v0.

It replaces the earlier assumption that effects are purely a v1 concern. It is
not yet normative, but it is intended to become the design basis for the next
foundational language milestone.

## 1. Problem Statement

Sprout currently uses `IO a` as an annotation-only surface type.

That creates a real mismatch:

- some runtime-interacting builtins are marked `IO`,
- others still return plain values or `Result ...` directly,
- effectful code cannot be tracked honestly in function types,
- migration to signatures like `env_get : String -> IO (Maybe String)` is not
  usable because v0 has no way to sequence or consume `IO a` cleanly.

This is no longer a theoretical concern. It blocks straightforward cleanup of
the builtin surface and pushes the language away from the “solid foundations”
goal.

## 2. Goals

1. Make runtime interaction explicit in function types.
2. Preserve Sprout’s strict evaluation model.
3. Keep the first milestone small enough to implement and explain.
4. Separate effects from domain errors:
   - effects belong in function types,
   - `Maybe` / `Result` remain ordinary value-level types.
5. Enable a clean migration of runtime-bound builtins such as `env_get`,
   `http_request`, and `crypto_random_bytes`.

## 3. Non-Goals

1. Do not introduce laziness.
2. Do not add algebraic effect handlers in the first milestone.
3. Do not add user-defined effect kinds in the first milestone.
4. Do not redesign the entire stdlib beyond what is necessary for effect typing.
5. Do not keep the current `IO a` container model as the long-term contract.

## 4. Proposed Direction

Promote a minimal effect system into v0 by attaching effects to function types.

Illustrative surface:

```sprout
fn parse_age(raw: String) -> Result String Int !{}
fn env_get(name: String) -> Maybe String !{IO}
fn http_request(url: String) -> Result HttpError HttpResponse !{IO}
```

Interpretation:

- `!{}` means the function is pure.
- `!{IO}` means the function may interact with the outside world.
- Effects are properties of function calls, not ordinary return wrappers.

This replaces the current annotation-only meaning of `IO a`.

## 5. Why This Fits Better Than `IO a`

Under the proposed model:

- `env_get("TOKEN")` still yields a `Maybe String`,
- `http_request(...)` still yields a `Result HttpError HttpResponse`,
- pattern matching stays natural,
- the caller becomes responsible for carrying the `IO` effect.

That avoids awkward shapes like:

```sprout
IO (Maybe String)
IO (Result HttpError HttpResponse)
```

and aligns more closely with effect systems such as Koka, while keeping the
first Sprout milestone much smaller than a full Koka-style design.

## 6. Initial Scope

The first milestone should support exactly one built-in effect:

- `IO`

The syntax should be row-shaped from day one:

```sprout
!{}
!{IO}
```

That keeps the surface extensible without requiring multiple effect kinds
immediately.

## 7. Surface Syntax

Function declarations:

```sprout
fn inc(x: Int) -> Int !{} = x + 1
fn print_name(name: String) -> Unit !{IO} = print(name)
```

Function types:

```sprout
Int -> Int !{}
String -> Result String Int !{IO}
```

Higher-order examples:

```sprout
fn apply_twice(f: Int -> Int !e, x: Int) -> Int !e =
  f(f(x))
```

Open question:

- whether effect-polymorphic syntax should ship in the first parser milestone,
  or whether the first implementation should support only closed effects and add
  effect variables afterward.

## 8. Typing Model

At a high level:

1. Every function type carries an effect set or effect variable.
2. Calling a function contributes its effect set to the surrounding context.
3. A function declared pure must typecheck with `!{}`.
4. A function declared with `!{IO}` may call pure or `IO`-effectful functions.
5. `Result` and `Maybe` remain ordinary value types and do not imply effects.

## 9. Evaluation Semantics

Evaluation remains strict and left-to-right.

The effect system changes what programs are accepted, not when expressions run.

In particular:

1. Effects happen when effectful expressions are evaluated.
2. `let` remains eager.
3. Function calls remain strict.
4. The effect system is about static tracking, not delayed execution.

## 10. `main`

Recommended rule:

- `main` should be allowed to have effects.
- In the first milestone, require the effect to be explicit:

```sprout
fn main() -> Unit !{IO} =
  print("hello")
```

This keeps the language honest and avoids another special case.

## 11. Builtin Migration

Pure builtins stay pure:

- `parse_int`
- `str_concat`, `str_len`, `str_slice`, `str_find`, `str_starts_with`
- byte/vector/map value transforms
- `json_stringify`
- hashing / encoding helpers such as `crypto_sha256`

Effectful builtins move to `!{IO}`:

- `print`
- `print_int`
- `read_lines`, `read_file`, `read_int_lines`
- `env_get`, `argv_get`
- `term_*`
- `tcp_*`
- `http_request`
- `crypto_random_bytes`

Illustrative target signatures:

```sprout
env_get : String -> Maybe String !{IO}
argv_get : Int -> Maybe String !{IO}
http_request : String -> String -> String -> String -> Int -> Result HttpError HttpResponse !{IO}
crypto_random_bytes : Int -> Result CryptoError Bytes !{IO}
```

## 12. Migration from Current v0

This is a language-contract change, not a hidden implementation refactor.

Expected migration steps:

1. Replace `-> IO T` with `-> T !{IO}`.
2. Migrate runtime-bound non-`IO` builtins into the same effect model.
3. Update `main` signatures from `IO Unit` to `Unit !{IO}`.
4. Update docs/examples/tests in the same change series.

Compatibility stance:

- prefer a clean break rather than a long compatibility layer
- if a temporary compatibility shim is needed, keep it narrow and explicitly transitional

## 13. Staged Implementation Plan

Recommended order:

1. Finalize syntax and typing rules.
2. Extend the parser / AST / pretty-printing for function effects.
3. Extend the typechecker with effect tracking for pure vs `IO`.
4. Migrate builtins.
5. Update examples and stdlib wrappers.
6. Update the normative spec.

## 14. Diagnostics

The first milestone should prioritize a small number of high-value errors:

1. calling an `IO`-effectful function from a pure function
2. declaring a function pure when its body requires `IO`
3. failing to propagate a higher-order function’s effect requirement

Diagnostics should say:

- what failed
- where the effect escaped
- what signature change would fix it

## 15. Open Questions

1. Should purity be implicit by default, with omitted effects treated as `!{}`?
2. Should effect-polymorphic function types ship in the first milestone, or
   follow immediately after closed-effect support?
3. Should the syntax stay row-shaped from day one even if only `IO` exists at
   first?
4. Should `Unit !{IO}` replace `IO Unit` everywhere immediately, or is there
   value in a very short parser-level transition period?
