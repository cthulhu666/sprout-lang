# Sprout Specification v0

This is the normative v0 specification for Sprout core.

It is the source of truth for the stable v0 language surface described here.
Repository features that are implemented but not specified in this document
(for example, modules and typeclasses) are experimental extensions and are not
part of normative v0 until they are specified here or in another normative
spec document.

The current implementation also includes experimental `do` notation for
sequencing `Maybe` and `Result`, but that surface is not part of normative v0.

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
- Keywords: `fn`, `let`, `where`, `type`, `wrap`, `match`, `with`, `if`, `then`, `else`, `true`, `false`
- Literals: integer, boolean, string, unit (`()`)
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

A `String` value is always valid UTF-8 and contains no NUL byte. Builtins that
construct a `String` from raw external bytes (e.g. `read_file`) validate the
input and report malformed content through their error channel rather than
producing an invalid `String`.

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

Functions may also end with a local `where` block:

```sprout
fn score(n: Int) -> Int =
  x + y
where
  x = n + 1
  y = x * 2
```

Local `where` bindings in v0 follow these rules:

- They are allowed only on `fn` declarations.
- They are value bindings only; local type annotations are not part of v0.
- Binding patterns may be either a single name or a tuple pattern built from names, `_`, and nested tuples.
- Multiple bindings are allowed and are evaluated in source order.
- Each binding may use function parameters and earlier local bindings.
- Self-reference, mutual recursion, and forward reference are not part of v0.

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

A `match` must be **exhaustive**: the branch patterns must cover every value of
the scrutinee type, or it is a compile error. Coverage is checked per field
position ("column"): a constructor is covered when, for each of its fields, the
union of the sub-patterns appearing at that position covers the field's type;
`Bool` requires both `true` and `false` (or a catch-all); `Unit` requires the
unit pattern; the unbounded scalar domains (`Int`, `String`, `Char`) are covered
only by a catch-all. Because coverage is checked one column at a time,
non-exhaustiveness that arises only from a *combination* of field values —
e.g. `(true, true) | (false, false)` on `(Bool, Bool)` — is not yet rejected in
v0 (a full usefulness matrix is future work); everything else is.

Unreachable top-level branches are a compile error when they are
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

The unique value of `Unit` is written `()`:

```sprout
()
```

Tuple patterns are positional and arity-sensitive:

```sprout
match pair with
| (x, y) -> x
```

`()` is the unit pattern and matches only the `Unit` value.

Composition operators desugar to `rcompose` and `lcompose` from the prelude:

- `f >> g` desugars to `rcompose(f, g)`, meaning `\x -> g(f(x))` — apply `f` first, then `g`
- `f << g` desugars to `lcompose(f, g)`, meaning `\x -> f(g(x))` — apply `g` first, then `f`

They associate to the right, so `f >> g >> h` means `\x -> h(g(f(x)))`.
`rcompose` and `lcompose` are first-class functions and can be passed as values.

The pipe operator is a binary operator that threads a value into the final
argument position of the expression on its right:

- `value |> f` means `f(value)`
- `value |> g(a, b)` means `g(a, b, value)`

It associates to the left, so `x |> f(a) |> g(b)` means `g(b, f(a, x))`.

### 5.6 ADT declaration

```sprout
type Maybe a =
  | Nothing
  | Just a
```

Every type name referenced in a constructor field, record field, or type alias
RHS must be either:

- A locally-declared type (in the same module bundle).
- A built-in primitive type (`Int`, `Bool`, `String`, `Char`, `Unit`, `Bytes`,
  `IntRange`).
- A lowercase type variable (e.g. `a`, `b`) bound by the enclosing type
  declaration's parameter list.

Referencing an undeclared uppercase type name is a compile error at definition
time:

```
type-validation: unknown type name `Baz` in declaration `Foo`
```

Forward references between mutually recursive ADTs within the same module are
allowed — validation runs after all type names in the module have been
registered.

**Positions validated in this version:** `TypeDecl` constructor fields,
`RecordDecl` field types, `AliasDecl` RHS, `WrapDecl` inner type.  `ClassDecl`
method signatures, `InstanceDecl` constraint types, and `FnDecl` param/return
type annotations are not yet validated (tracked in BACKLOG.md).

### 5.6.1 `wrap` declaration

```sprout
wrap Age = Int
wrap UserId = Int
wrap BodyEnv = Dict types.Scheme
```

A `wrap` declaration introduces a **zero-cost distinct type**. `wrap Foo = T`
declares:

- A type `Foo` that is distinct from `T` and from every other `wrap` over `T`.
- A single constructor `Foo(x: T) -> Foo` named after the type.
- A destructor pattern `Foo x` usable in `match` expressions.

The runtime representation of `Foo` is identical to that of `T`. Construction
`Foo(x)` and destruction `match v with | Foo inner -> ...` are identity at the
LLVM IR level: no allocation, no field load, no tag check. The compiler emits
no `@sprout_register_ctor` call for wrap constructors and no boxing call for
construction. This guarantees that wrapping does not affect garbage-collection
behavior of the underlying value — a `wrap` over a heap-typed value remains
heap-typed at the same SSA register.

Restrictions:

- The right-hand side is a single type expression (no `|` alternatives).
- No type parameters on the wrap itself in v0; the inner type may be
  parameterized (`wrap MyDict a = Dict a`) but the wrap itself is monomorphic.
- The constructor name and type name are identical and cannot be set separately.
- A `wrap` cannot derive typeclasses; explicit `instance` declarations are
  required for class membership.

Wrap types primarily enable **mistake-prevention without runtime cost**: types
like `Metres` vs `Seconds`, `UserId` vs `OrderId`, or the `BodyEnv` /
`GlobalEnv` distinction in the self-hosted compiler can be enforced by the
typechecker while sharing the underlying representation.

`wrap` is distinct from `type alias`, which is transparent: `type alias Foo =
Int` makes `Foo` interchangeable with `Int` everywhere. A `wrap` is opaque to
callers and requires explicit construction or pattern matching.

### 5.7 Template literals (Experimental)

> **Experimental** — not part of normative v0 until Phase 5 of the string
> interpolation roadmap lands and the full test gate passes.  Full design
> rationale and the phased implementation plan live in
> `docs/string-interpolation-v1.md`.

Grammar:

```
template_literal  ::= "`" template_content* "`"
template_content  ::= template_char
                    | escape_seq
                    | "${" expr "}"
template_char     ::= any character except "`", "\", and "${"
escape_seq        ::= "\`" | "\${" | "\n" | "\t" | "\\"
```

A template literal `` `Hello, ${name}!` `` is a primary expression with type
`StringTemplate`.  Each `${expr}` slot requires a `ToString` instance for the
type of `expr`.  When a `StringTemplate` appears in a `String`-expected context
the elaborator inserts an implicit coercion `template_to_string :
StringTemplate -> String`; in a `StringTemplate`-expected context the parts
flow through unchanged.  Plain `"..."` string literals are unaffected.

Only the five `escape_seq` forms above are valid after a backslash inside a
template literal; there is no raw pass-through. A backslash followed by any other
character (e.g. `\r`, `\0`, `\z`) is a lexical error, mirroring how string and
char literals reject unsupported escapes.

## 6. Evaluation Semantics (Strict)

1. Function application: evaluate callee, then args left-to-right.
   If the call supplies all remaining parameters, call the function.
   If the call supplies fewer than the remaining parameters, return a new
   function value that captures those arguments.
   If the call supplies more than the remaining parameters, it is an error.
2. Lambda expression: evaluating a lambda produces a closure that captures the current lexical environment.
3. Function-local `where` bindings evaluate in source order after function parameters are bound, with each binding extending the environment for later bindings and the final body.
4. `let`: evaluate RHS immediately, then bind.
5. Binary operators: evaluate left operand, then right operand.
   - Integer division `/`: dividing by zero **panics** with a runtime error
     (`division by zero`) rather than producing an undefined result. `INT_MIN / -1`
     is also undefined for a machine `Int` and is excluded on the same basis.
     For a total, non-panicking division use `safe_div : Int -> Int -> Result
     DivByZero Int`, which returns `Err(DivByZero)` in exactly those two cases.
   - `Int` addition, subtraction, and multiplication **wrap** on overflow in the
     native backend (two's-complement `i64`); see §8.4. This is a temporary v0
     implementation constraint, not the intended long-term meaning of `Int`.
6. Short-circuiting:
- `a && b`: evaluate `b` only if `a` is `true`.
- `a || b`: evaluate `b` only if `a` is `false`.
7. `if`: evaluate condition, then exactly one branch.
8. `match`: evaluate scrutinee once, then patterns in order, evaluate first matching branch.
9. Constructors and tuples evaluate fields left-to-right before construction.
10. Top-level declarations evaluate in source order.

Effect note for v0:

- Calling a function typed with `!{IO}` behaves like any other strict function call.
- Effects happen when the call expression is evaluated.
- Host-implemented builtins participate in effect checking exactly like ordinary
  functions; their declared function type is the language contract.
- A builtin uses `!{IO}` when evaluating the call may interact with runtime or
  external state such as terminal IO, files, environment, network, randomness,
  or host-backed analysis services.
- A builtin stays pure when it only computes or transforms values, even if its
  result type is `Maybe ...` or `Result ...`.
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
13. In a function-local `where` block, each binding is checked in order using the
    function parameters and earlier local bindings; later bindings are not in scope.
14. Function-local `where` bindings may destructure tuples, but constructor, literal,
    and general pattern bindings are not part of v0.

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

## 8.5 Standard Prelude Typeclass Instances (Experimental)

The following typeclass instances are provided by `stdlib/prelude.sprout` and are
automatically available without an explicit import.  They are experimental in v0
(consistent with the module/typeclass extension status noted in §1).

### `ToString` instances

`to_string` is defined for the following types:

| Type | Result |
|---|---|
| `Int` | decimal string, e.g. `"42"` |
| `Double` | decimal string via `%g`; a `.0` is appended when the value is integral so it never reads as an `Int`, e.g. `"3.14"`, `"1.0"` |
| `Bool` | `"true"` or `"false"` |
| `String` | identity |
| `(a, b)` | `"(s_a, s_b)"` where `s_x = to_string(x)` — requires `ToString a`, `ToString b` |
| `(a, b, c)` | `"(s_a, s_b, s_c)"` — requires `ToString a`, `ToString b`, `ToString c` |
| `(a, b, c, d)` | `"(s_a, …, s_d)"` — requires `ToString` on all four element types |
| `(a, b, c, d, e)` | `"(s_a, …, s_e)"` — requires `ToString` on all five element types |
| `List a` | `"[s_0, s_1, …]"` — requires `ToString a` |
| `Maybe a` | `"Nothing"` or `"Just s"` — requires `ToString a` |

Tuple instances format nested tuples recursively.  A 6-tuple or larger has no
`ToString` instance in the current prelude; adding one requires an explicit
instance declaration.

## 8.6 Automatic Instance Derivation (`deriving`) (Experimental)

A `type` declaration may carry a `deriving (...)` clause between the optional
`(..)` constructor-export marker and the `=` sign.  The compiler synthesizes
instance declarations for each listed class, eliminating the boilerplate of
hand-writing instances whose body follows from the type's structure.

### Syntax

```
type Name (..) deriving (Class1, Class2, ...) =
  | Ctor1 ...
  | Ctor2 ...
```

`deriving` is a hard keyword.  The class-name list must be parenthesized and
non-empty.  Whitespace and line breaks inside the parentheses are allowed.

### Derivable classes (this version)

| Class | Scope | Synthesized method |
|---|---|---|
| `Eq` | all ADT shapes | `eq(left, right)` — `match (left, right) with` per-ctor pairs comparing fields with recursive `eq`; cross-ctor pairs return false.  The `==` and `!=` infix operators desugar to `Eq.eq` dispatch for all non-primitive types; `==` on `Int`, `Bool`, `Char`, and `String` uses the built-in comparison path. |
| `Ord` | all ADT shapes | `compare(left, right)` — nested match; constructors compared by declaration index (first-declared is least); same-ctor pairs compare fields lexicographically via `match compare(l0, r0) with \| 0 -> ... \| c -> c` chains |
| `ToString` | all ADT shapes | `to_string(value)` — renders as `"CtorName"` for nullary, `"CtorName(to_string(f0), ..., to_string(fN-1))"` for N-field |

For parametric types (e.g. `type Box a = | Hold a`), the synthesized instance
carries one instance constraint per type parameter, e.g. `instance Eq (Box a)
where Eq a { ... }`.  This is conservative — phantom type parameters get a
constraint they don't need; refining this is a future improvement.

Serialization (`Serialize`/`Deserialize`) and hashing (`Hash`) are intentionally
**not** in v1.  Both require design decisions the language hasn't made yet —
serialization needs a format-agnostic visitor abstraction (serde-style) rather
than baking S-expressions into a class name, and `Hash` waits on polymorphic-keyed
dicts.  Both are tracked in `BACKLOG.md`.

### Limitations (this version)

- Multiple `deriving (...)` clauses on the same type declaration are not
  supported (use one clause with all classes: `deriving (Eq, Ord, ToString)`).
- Records (`record`) do not support `deriving`; v1 targets `type` declarations
  only.

### Error conditions

- Unknown class in deriving clause: eager error at the deriving site:
  `unknown class in deriving clause for 'Foo': 'Bar' (v1 supports Eq, Ord,
  ToString)`.
- Missing field-class instance: the synthesized body references `eq(f)`,
  `to_string(f)`, etc. on each field. If the field's type has no instance of
  the derived class, the standard "no instance" error fires at the use site
  rather than at the `deriving` site (eager checking of field-class
  availability is a future improvement).

### See also

- `docs/deriving-v1-draft.md` — full design rationale, including the
  rejected alternatives (`Generic`-based approach, compile-time handler
  approach) and the trajectory toward v2 user-defined deriving.
- `BACKLOG.md` §1, §5 — companion items: strict type-name validation
  (improves deriving's phantom-type diagnostics), polymorphic-keyed dicts
  (unblocks `deriving (Hash)`), field-bearing Ord, format-agnostic
  serialization design.

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
  | Nothing
  | Just a

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

`main` is the conventional program entrypoint in v0. Executable entrypoints
accepted by `sprout run` and `sprout compile` must be exactly a zero-argument
`fn main() -> Unit !{IO}` after module qualification is resolved. Pure `main`
definitions and effect-polymorphic `main` definitions are rejected at the
executable boundary. Helper functions may still use shapes such as
`Maybe a !{IO}` or `Result e a !{IO}` and be handled explicitly from `main`.

### 10.11 Non-exhaustive match (compile error)

```sprout
type Maybe a =
  | Nothing
  | Just a

fn bad(m: Maybe Int) -> Int =
  match m with
  | Just x -> x
```

The compiler rejects this with a non-exhaustive-match error (see §5.5).

### 10.12 Unreachable match branch (compile error)

```sprout
type Maybe a =
  | Nothing
  | Just a

fn bad(m: Maybe Int) -> Int =
  match m with
  | Just x -> x
  | Nothing -> 0
  | _ -> 1
```

The compiler rejects the final branch as an unreachable match branch (see §5.5).

### 10.13 Using `stdlib.math`

```sprout
import stdlib.math as math

fn wrap(idx: Int, size: Int) -> Maybe Int =
  math.mod(idx, size)
```
