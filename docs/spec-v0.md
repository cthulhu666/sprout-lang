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

Top-level `let` declarations must be pure in v0. Effectful work must be placed
inside functions and triggered from `main` or other function calls.

## 4. Types

Built-in types:

- `Int`
- `Bool`
- `String`
- `Unit`
- `IO a` (surface annotation for effectful APIs in v0)

`IO a` in v0 is a documentation-oriented surface type, not a first-class effect
system. It marks APIs that are expected to perform effects, but it does not
introduce effect isolation, delayed execution, purity tracking, or effect
sequencing semantics beyond ordinary strict evaluation.

Type forms:

- Type variable: `a`, `b`, `t`
- Function type: `a -> b`
- Parameterized type: `Maybe a`, `Result e a`
- Tuple type (optional v0.1): `(a, b)`

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

### 5.2 Let binding

```sprout
let answer = 42
```

Bindings are immutable.
At top level, `let` initializers must not have type `IO a`.

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

### 5.6 ADT declaration

```sprout
type Maybe a =
  | Just a
  | Nothing
```

## 6. Evaluation Semantics (Strict)

1. Function application: evaluate callee, then args left-to-right, then call.
2. Lambda expression: evaluating a lambda produces a closure that captures the current lexical environment.
3. `let`: evaluate RHS immediately, then bind.
4. Binary operators: evaluate left operand, then right operand.
5. Short-circuiting:
- `a && b`: evaluate `b` only if `a` is `true`.
- `a || b`: evaluate `b` only if `a` is `false`.
6. `if`: evaluate condition, then exactly one branch.
7. `match`: evaluate scrutinee once, then patterns in order, evaluate first matching branch.
8. Constructors/records/tuples: evaluate fields left-to-right before construction.
9. Top-level declarations evaluate in source order.

Effect note for v0:

- Calling a function typed as `IO a` behaves like any other strict function call.
- Effects happen when the call expression is evaluated.
- v0 does not provide a separate execution model for effectful values.
- A fuller effect system is deferred to v1.
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
8. `IO a` is treated as an ordinary type constructor in v0, with no special
   typing rules beyond normal type checking.

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

### 10.6 Generic map over Maybe

```sprout
fn map(m: Maybe a, f: a -> b) -> Maybe b =
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
fn main() -> IO Unit =
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

### 10.12 Using `stdlib.math`

```sprout
import stdlib.math as math

fn wrap(idx: Int, size: Int) -> Maybe Int =
  math.mod(idx, size)
```
