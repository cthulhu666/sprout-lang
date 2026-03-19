# Sprout Specification v0

This is the normative v0 specification for Sprout core.

It is the source of truth for the stable v0 language surface described here.
Repository features that are implemented but not specified in this document
(for example, modules and typeclasses) are experimental extensions and are not
part of normative v0 until they are specified here or in another normative
spec document.

## 1. Scope

Sprout v0 is a statically typed, functional-first language with:

- Hindley-Milner style type inference (with explicit annotations where needed).
- Immutable bindings by default.
- Algebraic data types and pattern matching.
- Strict evaluation with deterministic order.

Out of scope for v0:

- Typeclasses/traits.
- Modules/package system.
- Laziness by default.
- Classes/object inheritance.

## 2. Lexical Structure

- Identifiers: `[a-zA-Z_][a-zA-Z0-9_]*`
- Keywords: `fn`, `let`, `type`, `match`, `with`, `if`, `then`, `else`, `true`, `false`
- Literals: integer, boolean, string
- Comments: line comments start with `#` and continue to end of line

## 3. Program Structure

A source file is a sequence of top-level declarations:

1. `type` declarations
2. `fn` declarations
3. top-level `let` declarations

Execution starts from `main`.

At top level, `let` initializers must be pure in v0. Effectful work is expected
to be placed inside functions and triggered from `main` or other function
calls.

## 4. Types

Built-in types:

- `Int`
- `Bool`
- `String`
- `Unit`

Effect annotations are attached to function types rather than encoded as an
ordinary type constructor.

Type forms:

- Type variable: `a`, `b`, `t`
- Function type: `a -> b`
- Effectful function type: `a -> b !{IO}`
- Restricted effect-polymorphic function type: `a -> b !{e}`
- Parameterized type: `Maybe a`, `Result e a`
- Tuple type: `(a, b, c)`

For this v0 milestone, effect annotations support only:

- closed rows: omitted annotation (pure) and `!{IO}`
- singleton effect variables: `!{e}`

Mixed or multi-entry rows such as `!{IO, e}` and `!{e, f}` are not part of the
language contract yet.

Type inference:

- Let-bound values are inferred.
- Ordinary function parameters and returns may be inferred.
- Explicit annotations are still allowed wherever they improve readability or diagnostics.
- Recursive declarations should infer when constraints are sufficient, but annotations may still be required in difficult or ambiguous cases.

## 5. Declarations and Expressions

### 5.1 Function declaration

```sprout
fn add(x: Int, y: Int) -> Int =
  x + y
```

Parameter and return annotations are optional for ordinary functions in v0 when
the typechecker can infer them.

Multiple parameters in a declaration are surface syntax for a function whose
type is written with nested arrows. For example, `fn add(x: Int, y: Int) ->
Int` has type `Int -> Int -> Int`.

Omitted effect annotations mean the function is pure. In v0, the only built-in
effect label is `IO`.

### 5.2 Let binding

```sprout
let answer = 42
```

Bindings are immutable.
At top level, `let` initializers must be pure.

### 5.3 Lambda expression

```sprout
\(x: Int, y: Int) -> x + y
```

Lambda expressions are anonymous functions.

- Syntax: `\(` parameter-list `)` `->` expression
- Single-parameter shorthand: `\x -> expression` and `\x: T -> expression`
- The parenthesized parameter list must contain at least one parameter.
- Parameter annotations are optional and follow the same rules as named functions.
- Lambdas capture surrounding lexical bindings by value.
- A lambda with parameters `x, y` has function type `tx -> ty -> tr`.
- v0 function application allows under-application for ordinary function values.
  Calling a function with fewer arguments than it declares returns a new
  function value that captures the supplied arguments. Calling a function with
  more arguments than it declares is a compile/runtime error in the current
  contract.

### 5.4 If expression

```sprout
if n > 0 then "pos" else "non-pos"
```

Both branches must type-check to the same type.

### 5.5 Match expression

```sprout
match m with
| Just x -> x
| Nothing -> 0
```

Patterns are checked top-to-bottom; first match wins.
In v0, unreachable top-level branches are a compile error when they are
shadowed by an earlier `_` or variable pattern, repeat an earlier top-level
literal pattern, repeat an earlier top-level constructor branch that already
covers that constructor completely, or appear after all constructors of an ADT
have already been covered. More advanced nested-pattern subsumption is not part
of the v0 contract.

Tuple expressions use comma-separated elements inside parentheses:

```sprout
(x, y, z)
```

`(x)` remains ordinary grouping, not a 1-tuple.

Tuple patterns are positional and arity-sensitive:

```sprout
match pair with
| (x, y) -> x
```

Composition operators are ordinary binary operators on functions:

- `f >> g` means `\x -> g(f(x))`
- `f << g` means `\x -> f(g(x))`

They associate to the right, so `f >> g >> h` means `\x -> h(g(f(x)))`.

The pipe operator is a binary operator that threads a value into the final
argument position of the expression on its right:

- `value |> f` means `f(value)`
- `value |> g(a, b)` means `g(a, b, value)`

It associates to the left, so `x |> f(a) |> g(b)` means `g(b, f(a, x))`.

### 5.6 ADT declaration

```sprout
type Maybe a =
  | Just a
  | Nothing
```

## 6. Evaluation Semantics (Strict)

1. Function application: evaluate callee, then args left-to-right.
   If the call supplies all remaining parameters, call the function.
   If the call supplies fewer than the remaining parameters, return a new
   function value that captures those arguments.
   If the call supplies more than the remaining parameters, it is an error.
2. Lambda expression: evaluating a lambda produces a closure that captures the current lexical environment.
3. `let`: evaluate RHS immediately, then bind.
4. Binary operators: evaluate left operand, then right operand.
5. Short-circuiting:
- `a && b`: evaluate `b` only if `a` is `true`.
- `a || b`: evaluate `b` only if `a` is `false`.
6. `if`: evaluate condition, then exactly one branch.
7. `match`: evaluate scrutinee once, then patterns in order, evaluate first matching branch.
8. Constructors and tuples evaluate fields left-to-right before construction.
9. Top-level declarations evaluate in source order.

Effect note for v0:

- Calling a function typed with `!{IO}` behaves like any other strict function call.
- Effects happen when the call expression is evaluated.
- v0 provides only restricted effect polymorphism via singleton effect variables
  such as `!{e}`.
- v0 does not provide delayed execution, mixed/open effect rows, or handlers.
- Because top-level `let` bindings must be pure, imported modules do not perform
  effectful initialization merely by being loaded.

## 7. Typing Rules (High Level)

1. Every expression has exactly one type after inference/checking.
2. `if` condition must be `Bool`.
3. Function application requires argument types to unify with parameter types.
4. Lambda expressions introduce parameter bindings for their body and infer a function type from parameters to body result.
5. `match` branches must have a unified result type.
6. Pattern-bound variables are scoped to their branch.
7. ADT constructors produce values of their declared type.
8. Effect annotations are checked on function types; omitted annotations mean purity.
9. Function types may quantify a singleton effect variable `!{e}`; use sites
   instantiate it with either purity or a concrete closed effect supported in v0.
10. `main` must use a concrete effect annotation when effectful; it may not be
    effect-polymorphic.
11. A pure function body may not call `!{IO}` functions unless it is allowed by
    the surrounding singleton effect variable instantiation.
12. Tuple expressions and tuple patterns use structural, exact-arity typing.

## 8. Standard Library Math Module

Sprout v0 includes a normative integer-only standard library math module:
`stdlib.math`.

Exports:

- `abs(x: Int) -> Int`
- `min(x: Int, y: Int) -> Int`
- `max(x: Int, y: Int) -> Int`
- `clamp(x: Int, lo: Int, hi: Int) -> Int`
- `sign(x: Int) -> Int`
- `pow(base: Int, exp: Int) -> Maybe Int`
- `mod(x: Int, n: Int) -> Maybe Int`
- `gcd(x: Int, y: Int) -> Int`
- `lcm(x: Int, y: Int) -> Int`
- `is_even(x: Int) -> Bool`
- `is_odd(x: Int) -> Bool`

Semantics:

- `stdlib.math` does not introduce additional numeric types in v0.
- `mod(x, n)` uses Euclidean modulo.
- If `n > 0`, `mod(x, n)` returns `Just r` where `0 <= r < n`.
- If `n <= 0`, `mod(x, n)` returns `Nothing`.
- `pow(base, exp)` returns `Nothing` when `exp < 0`; otherwise it returns
  `Just` of the integer power.
- In the interpreter, `Int` arithmetic currently follows host arbitrary-precision
  integer behavior.
- In the current native backend, `Int` values are lowered to machine `i64`
  values. Overflow-sensitive results for `abs`, `pow`, `gcd`, and `lcm` are
  therefore not yet fully backend-independent once computation leaves the
  backend's current representable range.
- This backend range limitation is a temporary implementation constraint in v0,
  not the intended long-term meaning of `Int`.
- The presence of `pow` and `mod` in `stdlib.math` does not imply implicit
  numeric coercions, fractional arithmetic, or floating-point support in v0.

## 9. Errors

Compiler diagnostics must include:

1. What failed.
2. Where it failed (line/column).
3. A concrete likely fix.

Prefer one clear root-cause error over cascading follow-up errors.

## 10. Canonical Examples

### 10.1 Basic function

```sprout
fn inc(x: Int) -> Int = x + 1
```

### 10.2 Inferred let

```sprout
let x = 10
let y = x + 5
```

### 10.3 Boolean short-circuit

```sprout
fn safe_div_ok(a: Int, b: Int) -> Bool =
  b != 0 && (a / b) > 0
```

### 10.4 If expression

```sprout
fn sign(n: Int) -> String =
  if n > 0 then "positive" else "zero-or-negative"
```

### 10.5 ADT + match

```sprout
type Maybe a =
  | Just a
  | Nothing

fn with_default(m: Maybe Int, d: Int) -> Int =
  match m with
  | Just x -> x
  | Nothing -> d
```

### 10.6 Tuple match

```sprout
fn swap(pair: (Int, Bool)) -> (Bool, Int) =
  match pair with
  | (x, y) -> (y, x)
```

### 10.6 Generic map over Maybe

```sprout
fn map(f: a -> b, m: Maybe a) -> Maybe b =
  match m with
  | Just x -> Just(f(x))
  | Nothing -> Nothing
```

### 10.7 Recursive function

```sprout
fn fact(n: Int) -> Int =
  if n == 0 then 1 else n * fact(n - 1)
```

### 10.8 Lambda with capture

```sprout
fn make_adder(base: Int) -> Int -> Int =
  \(x) -> base + x
```

### 10.9 Top-level binding order

```sprout
let a = 1
let b = a + 1
```

### 10.10 Main entrypoint

```sprout
fn main() -> Unit !{IO} =
  print("hello")
```

### 10.11 Non-exhaustive match (compile error)

```sprout
type Maybe a =
  | Just a
  | Nothing

fn bad(m: Maybe Int) -> Int =
  match m with
  | Just x -> x
```

Compiler should report non-exhaustive pattern matching.

### 10.12 Unreachable match branch (compile error)

```sprout
type Maybe a =
  | Just a
  | Nothing

fn bad(m: Maybe Int) -> Int =
  match m with
  | Just x -> x
  | Nothing -> 0
  | _ -> 1
```

Compiler should report the final branch as unreachable.

### 10.13 Using `stdlib.math`

```sprout
import stdlib.math as math

fn wrap(idx: Int, size: Int) -> Maybe Int =
  math.mod(idx, size)
```
