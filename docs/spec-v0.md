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
- Function parameters/returns may be inferred unless ambiguity hurts diagnostics.
- Recursive declarations may require annotations in early compiler milestones.

## 5. Declarations and Expressions

### 5.1 Function declaration

```sprout
fn add(x: Int, y: Int) -> Int =
  x + y
```

### 5.2 Let binding

```sprout
let answer = 42
```

Bindings are immutable.

### 5.3 If expression

```sprout
if n > 0 then "pos" else "non-pos"
```

Both branches must type-check to the same type.

### 5.4 Match expression

```sprout
match m with
| Just x -> x
| Nothing -> 0
```

Patterns are checked top-to-bottom; first match wins.

### 5.5 ADT declaration

```sprout
type Maybe a =
  | Just a
  | Nothing
```

## 6. Evaluation Semantics (Strict)

1. Function application: evaluate callee, then args left-to-right, then call.
2. `let`: evaluate RHS immediately, then bind.
3. Binary operators: evaluate left operand, then right operand.
4. Short-circuiting:
- `a && b`: evaluate `b` only if `a` is `true`.
- `a || b`: evaluate `b` only if `a` is `false`.
5. `if`: evaluate condition, then exactly one branch.
6. `match`: evaluate scrutinee once, then patterns in order, evaluate first matching branch.
7. Constructors/records/tuples: evaluate fields left-to-right before construction.
8. Top-level declarations evaluate in source order.

Effect note for v0:

- Calling a function typed as `IO a` behaves like any other strict function call.
- Effects happen when the call expression is evaluated.
- v0 does not provide a separate execution model for effectful values.
- A fuller effect system is deferred to v1.

## 7. Typing Rules (High Level)

1. Every expression has exactly one type after inference/checking.
2. `if` condition must be `Bool`.
3. Function application requires argument types to unify with parameter types.
4. `match` branches must have a unified result type.
5. Pattern-bound variables are scoped to their branch.
6. ADT constructors produce values of their declared type.
7. `IO a` is treated as an ordinary type constructor in v0, with no special
   typing rules beyond normal type checking.

## 8. Errors

Compiler diagnostics must include:

1. What failed.
2. Where it failed (line/column).
3. A concrete likely fix.

Prefer one clear root-cause error over cascading follow-up errors.

## 9. Canonical Examples

### 9.1 Basic function

```sprout
fn inc(x: Int) -> Int = x + 1
```

### 9.2 Inferred let

```sprout
let x = 10
let y = x + 5
```

### 9.3 Boolean short-circuit

```sprout
fn safe_div_ok(a: Int, b: Int) -> Bool =
  b != 0 && (a / b) > 0
```

### 9.4 If expression

```sprout
fn sign(n: Int) -> String =
  if n > 0 then "positive" else "zero-or-negative"
```

### 9.5 ADT + match

```sprout
type Maybe a =
  | Just a
  | Nothing

fn with_default(m: Maybe Int, d: Int) -> Int =
  match m with
  | Just x -> x
  | Nothing -> d
```

### 9.6 Generic map over Maybe

```sprout
fn map(m: Maybe a, f: a -> b) -> Maybe b =
  match m with
  | Just x -> Just(f(x))
  | Nothing -> Nothing
```

### 9.7 Recursive function

```sprout
fn fact(n: Int) -> Int =
  if n == 0 then 1 else n * fact(n - 1)
```

### 9.8 Top-level binding order

```sprout
let a = 1
let b = a + 1
```

### 9.9 Main entrypoint

```sprout
fn main() -> IO Unit =
  print("hello")
```

### 9.10 Non-exhaustive match (compile error)

```sprout
type Maybe a =
  | Just a
  | Nothing

fn bad(m: Maybe Int) -> Int =
  match m with
  | Just x -> x
```

Compiler should report non-exhaustive pattern matching.
