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
- A `.` **extends** an identifier token, so a qualified name (`string.trim`) and a
  dotted field path (`p.x`, `line.from.x`) are each a **single** token — including a
  trailing dot, which is retained so that `p.` reports a missing field name rather
  than two adjacent expressions. The one exception is `..`: an identifier token
  **ends** before it, so the range operator needs no surrounding spaces and
  `lo..hi` is three tokens (`lo`, `..`, `hi`), never one. Ranges therefore lex
  uniformly whatever their endpoints are — `0..n`, `lo..hi`, `p.x..p.y`,
  `first(xs)..last(xs)` — and `a..b` is the canonical spelling the formatter emits.
- Keywords: `fn`, `let`, `where`, `type`, `wrap`, `match`, `with`, `if`, `then`, `else`, `true`, `false`
- Literals: integer, boolean, string, unit (`()`)
- Integer literals are decimal (`255`), hexadecimal (`0xFF`), or binary (`0b1010`).
  The `0x`/`0b` prefix and hex digits are case-insensitive, so `0XFF`, `0xff` and
  `0xFF` are the same literal. The digit run after a prefix must be non-empty; `0x`
  alone is not an integer literal. There is **no** digit separator — `1_000` is the
  literal `1` followed by the identifier `_000`. All three forms denote the same
  kind of value and are interchangeable in expressions and patterns.
  A literal too large for `Int` currently **wraps to the low 64 bits** and is read
  as signed, in every base (`0xFFFFFFFFFFFFFFFF` is `-1`; decimal
  `9223372036854775808` is `INT_MIN`). This is what makes every 64-bit pattern
  writable as a mask, and is slated for revisit together with the
  literal-overflow decision (`docs/int-overflow-policy-decision.md`), which must
  rule on radix literals explicitly rather than by a general fits-in-`Int` test.
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

### Imports

A file's `module` and `import` declarations form a header block before the
first ordinary declaration. An import is one of:

```sprout
import stdlib.compiler.ast as ast          # aliased: names reached as `ast.<name>`
import stdlib.collections (Vec, vec_get)   # selective: the listed names, unqualified
```

A **selective list may span lines**. The list continues until its closing
parenthesis, however many lines that takes, and the continuation lines need no
marker:

```sprout
import stdlib.math (pi, sin,
                    cos, sqrt)
```

A `#` comment ends the scan for the line, so a parenthesis inside a trailing
comment does not extend the list. Import lists get long — a real module carries
over a hundred imports, several past 200 columns — and requiring one line made
them unformattable.

### Externs are outside the module system

An `extern fn` declaration is **not a module-scoped name**. It is never
qualified, never renamed, and never enters a module's exported set, so:

- `export` on an `extern fn` has no effect. It parses and is discarded.
- An extern is reachable by its **bare name** from anywhere its declaring module
  is part of the build. Importing the module — under any alias, or selectively
  for some unrelated name — is what puts it there.
- A selective import list has no bearing on an extern either way. Naming one is
  accepted and does nothing; omitting one does not hide it. Given
  `extern fn read_file` and `export fn read_text` in `stdlib.fs`, both
  `import stdlib.fs (read_file)` and `import stdlib.fs (read_text)` compile, and
  under either one a bare `read_file(…)` call resolves.

The consequence for diagnostics is worth stating plainly, because it differs from
the rule for type names above: a missing import for an extern produces **no
import-related error**. Nothing checks value references against the import graph,
so the failure surfaces later and elsewhere — as `Unknown variable` from the
typechecker, or as an undefined symbol at link time.

Where a builtin is declared therefore decides whether a program can reach it.
The governing rule:

> An extern is declared in the prelude if the prelude's own code calls it, if it
> is a hardcoded compiler intrinsic, or if it is language core. Otherwise it is
> declared in the module that owns its surface — but only if that module is a
> **leaf**, or one its consumers would import anyway.

The leaf qualifier is normative rather than stylistic because there is no
cross-module dead-code elimination: importing a module emits every definition in
it and in everything it imports, called or not. Homing an extern in a module that
its consumers do not otherwise want makes every one of them carry that module's
whole body.

## 4. Types

Built-in types:

- `Int`
- `Double`
- `Bool`
- `String`
- `Unit`

`Int` is a machine word integer; `Double` is an IEEE 754 double-precision float.
`Double` has literal syntax (`3.14`), a `ToString` instance (`to_string`/
`double_to_string`), and arithmetic; `to_double` converts an `Int` to a `Double`.
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

**Type names must resolve.** A type name written in any type position — a
parameter or return annotation, a constructor field, a record field, an alias
right-hand side, a constraint, a lambda parameter — must name a type or class
that is *in scope at that point*: declared in the same module, brought in by an
import, or supplied by the prelude. A name that resolves to nothing is an error
reported at the declaration that writes it. It is **not** admitted as a fresh
opaque type.

A lowercase name in a type position is a type variable and is always in scope;
this rule concerns uppercase names only. The qualified spelling is subject to the
same rule: `mod.Name` must name an exported type of a module imported under the
alias `mod`.

The distinction that makes this worth stating is between *declared somewhere* and
*in scope here*. A type exported by a module that the current module imports
**for some other name** is not in scope: importing `mod (make)` does not bring
`mod.T` into scope, and writing bare `T` is an error even though `mod.T` exists.
Where the compiler can identify a module exporting a matching name, the
diagnostic says which one.

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

**A name may be declared at most once at a module's top level.** A second `fn`
with the same name is rejected at that second declaration:

```
`twice` is defined more than once in this module
```

The rule is keyed on the **name alone, not on (name, arity)**: Sprout has no
overloading (§5.3), so `fn scale(x: Int)` and `fn scale(x: Int, y: Int)` in one
module are two definitions of one name, not two overloads. Imported definitions
do not participate — every module's definitions carry their module prefix, so a
local `map` and the prelude's `map` are distinct names and coexist.

This is a well-formedness rule about the module, so it is reported at the
duplicate declaration. Before it existed, a duplicate lowered to two `define`
blocks for one symbol, which the front end accepted and only the LLVM verifier
rejected — and where the arities differed, the second declaration shadowed the
first, so a *caller* was blamed with an arity error for a defect in the module.

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
- **A binding's type is determined by its right-hand side**, not by how the body
  uses it. For a tuple pattern the right-hand side's element types are distributed
  positionally. `where` and `let … in` (§5.2.1) are the same binding construct and
  are specified to type identically: both desugar to a single-arm `match` on the
  bound value, so a `where`-bound `Double` needs no help from the body to be a
  `Double`. (This is normative because the reverse direction is a real hazard: an
  earlier implementation desugared `where` to an applied lambda with an inferred
  parameter, so a binding whose body use was ambiguous defaulted to `Int` and
  `where a = 2.5` failed to type-check.)

### 5.2 Let binding

```sprout
let answer = 42
```

Bindings are immutable.
At top level, `let` initializers must be pure. (**Not yet enforced** — see the note in
§6; an effectful top-level `let` is accepted today.)

**Type annotation (experimental).** A top-level `let` binding may carry an
optional type annotation between the name and `=`:

```sprout
let answer : Int = 42
```

The initializer is checked against the written type: its inferred type must
unify with the annotation, or it is a compile error
(`type annotation mismatch for \`<name>\`: …`). The annotation is applied before
generalization, so it narrows the binding's type — and, like a function
parameter/return annotation, it drives the same expected-type-directed literal
lowering, so `let xs : Vec Int = [1, 2, 3]` yields a `Vec` (§5.5.1) at the
binding site. Free lowercase names in the annotation are ordinary type variables
(`let id_pair : (a, a) = …`).

This version annotates **top-level** `let` bindings only; annotations on
`let … in` (§5.2.1), function-local `where` bindings, and do-block `let` steps
are not yet part of the language. See `docs/binding-annotations-v0.md`.

### 5.2.1 `let … in` binding block

In pure expression position, a `let … in` block introduces one or more local
bindings before a body expression. Bindings are layout-aligned under `let`; `in`,
dedented to the `let` column, closes the block:

```sprout
fn first_or(xs: List Int, dflt: Int) -> Int =
  let Cons h _ = xs else dflt
  in h
```

Each binding is `<pattern> = <expr>` (irrefutable, no `else`),
`<pattern> = <expr> else <expr>` (**constant else**), or
`<pattern> = <expr> else <residual-pat> -> <handler>` (**binding-else** — the
residual pattern names the refuted value). A binding must account for **its whole
line**: a token after the end of a binding (or after its `else` clause) is a
compile error, not a second statement and not something to ignore — one binding
per line, as one step per line in a `do` block. Writing
`let a = 1 print("hi")` is rejected rather than quietly dropping the call. Bindings are **sequential**: each is
in scope for later bindings and the body (a binding's own right-hand side sees
the *previous* meaning of any name it rebinds). With continuation `<rest>` (the
remaining bindings and body), a binding desugars to a single `match`:

```
<pat> = <e> else <fb>            →  match <e> with | <pat> -> <rest> | _      -> <fb>
<pat> = <e> else <rpat> -> <h>   →  match <e> with | <pat> -> <rest> | <rpat> -> <h>
<pat> = <e>                      →  match <e> with | <pat> -> <rest>
```

Thus a non-matching refutable pattern short-circuits the whole block to that
binding's `else`.

Rules:

- The right-hand side has **any** type — refutability is a property of the
  pattern versus its type, so `Result`/`Maybe` and bare-ADT bindings compose
  uniformly; no wrapper is required. It must be **pure** (an effectful RHS is an
  error; use `do`).
- A **refutable** pattern **requires** an `else`; a refutable pattern without one
  is a non-exhaustive match (error). An `else` on an **irrefutable** pattern is
  an error (its wildcard arm is unreachable). An irrefutable pattern without an
  `else` is an ordinary local binding.
- Each `else` supplies its own value, so distinct bindings may fail to distinct
  results. A **binding-else** (`else <residual-pat> -> <handler>`) matches the
  refuted value against `<residual-pat>`, a full pattern spliced verbatim into the
  second arm: `else Err msg -> use(msg)` names the failing payload, and a bare
  variable binds the whole scrutinee (no failure constructor is ever injected).
  The residual is checked for exhaustiveness like any match arm — a residual that
  leaves cases uncovered is a non-exhaustive-match error. Constant vs binding-else
  is disambiguated on the `->` after the else. (Monadic propagation — a no-`else`
  form — remains planned; see `docs/let-else-and-monadic-binding-plan.md`.)
- Every `else` and the body must unify to the block's result type. At least one
  binding is required.
- `let … in` is an ordinary expression (usable anywhere), and **complements**
  `where`: a function may use both, with `where` as the outer scope (its bindings
  are visible in a `let … in` RHS/body; `let … in` bindings are not visible in
  `where`).
- "Anywhere" includes a step of a `do` block, where the whole `let … in` is one
  expression step. Up to its `in` it is written exactly like the multi-name `let`
  *statement* of §5.2.1a, so the `in` is what distinguishes them — see §5.2.1a.

### 5.2.1a Multi-name `let` as a `do` statement

A `let` statement in a `do` block may carry **several bindings**, layout-aligned
under the first one exactly as in `let … in` (§5.2.1), and binds every name:

```sprout
do
  let a = 1
      b = a + 1
      c = b + 1
  print(to_string(a + b + c))
```

Bindings are **sequential**, matching §5.2.1: each is in scope for the ones below
it and for the rest of the block. The statement is equivalent to writing one
`let` statement per name, and the split between bindings uses the same layout
rule the expression form uses, so a right-hand side may span lines.

A binding carrying an `else` (§5.2.2) must stand alone as a single-binding
statement: its desugaring places the remaining steps inside a `match` arm, which
is a shape one binding among several cannot have.

**Statement or expression: the `in` decides.** A step beginning with `let` is
this statement form *unless* an `in`, dedented to the `let` column, closes the
binding group — then the step is one `let … in` expression (§5.2.1) and nothing
here applies to it. The two are written identically up to that point, so the
reading is not fixed by the `let`: it is fixed by whether the step turns out to
be a whole `let … in`.

```sprout
do
  let a = 1          # statement: `a` stays in scope for the steps below
      b = a + 1
  print(to_string(a + b))

  let c = 1          # expression: `c` and `d` scope over the body only,
      d = c + 1      # and the step's value is that body
  in print(to_string(c + d))
```

The `else` restriction above is what makes the difference observable rather than
academic: a multi-binding group carrying an `else` can only ever be the
expression form.

**One step per line.** A `do` step must consume the whole of its line (and any
continuation lines indented under it). Trailing tokens are an error — they used
to be discarded in silence, so a second statement written on the same line as
the first simply never ran.

**Where a step ends.** A step runs up to the next line that both starts at the
block's indentation column and begins with a token that can head an expression:
an identifier, a literal of any kind, an opening `(`/`[`/`{`, a `\` lambda, a
prefix `-` or `!`, a backtick template, or one of `let`/`if`/`match`/`do`/
`true`/`false`. A line indented further — or one starting at the block column
with a token that cannot head an expression, such as an operator or a `|` match
arm — continues the step above it.

### 5.2.2 Refutable binds in `do` blocks *(experimental)*

Inside a `do` block, both the effectful bind (`<pattern> <- <e>`) and the pure
local bind (`let <pattern> = <e>`) may carry a refutable pattern with an `else`,
using the same constant/binding-else forms as §5.2.1:

```sprout
fn infer_range(…) -> InferResult !{IO} =
  do
    InferOk lo s1 _ _ <- infer_expr(env, e_lo) else InferErr p e -> InferErr(p, e)
    let Ok s2 = unify_int(s1, lo) else Err e -> InferErr(pos, `not Int: ${e}`)
    InferOk(build(lo), s2, …)
```

A refutable `do` step short-circuits the block to its `else`; the remaining steps
form the success continuation. With continuation `<rest>` (the following steps as a
nested `do`), a step desugars to a single `match`:

```
<pat> <- <e> else <fb | rpat -> h>   →   __t <- <e>
<rest>                                    match __t with | <pat> -> do <rest> | <else-arm>
let <pat> = <e> else <fb | rpat -> h>  →  match <e> with | <pat> -> do <rest> | <else-arm>
```

The effectful form runs `<e>`'s effect exactly once (via the ordinary `<-`) before
the branch; the pure form matches the value directly. Exhaustiveness is enforced by
the same non-exhaustive-match rule (a residual leaving cases uncovered is an error).
A refutable step with **no following step** is an error (it would have no success
continuation). RHS effect handling is inherited from `<-` and is unchanged — this is
purely a parse-time rewrite (`docs/effectful-let-else-v0.md`). Monadic propagation (a
no-`else` form) remains planned.

An **irrefutable** pattern needs no `else`, and per §5.2.1 that is decided against
the pattern's *type*, not its syntax. A `wrap` or single-constructor ADT pattern is
therefore destructured directly in either form:

```sprout
Cents(c) <- earn()       # single-constructor: total, no `else`
let Boxed(n) = stash(7)
```

Both desugar to the same single `match` shown above, carrying one branch. A
genuinely refutable pattern with no `else` reaches that match with cases uncovered
and is rejected as a non-exhaustive match, naming the constructors it missed.

### 5.3 Lambda expression

```sprout
\(x: Int, y: Int) -> x + y
```

Lambda expressions are anonymous functions.

- Syntax: `\(` parameter-list `)` `->` expression
- Single-parameter shorthand: `\x -> expression` and `\x: T -> expression`
- The parenthesized parameter list must contain at least one parameter.
- Parameter annotations are optional and follow the same rules as named functions.
  *(Not yet enforced: inference currently ignores a lambda parameter's annotation.
  In argument position the parameter type comes from the callee's parameter slot,
  so the annotation is redundant there; elsewhere the parameter stays unconstrained
  — see `BACKLOG.md`.)*
- Lambdas capture surrounding lexical bindings by value.
- A lambda with parameters `x, y` has function type `tx -> ty -> tr`.
- **In argument position a lambda's parameter types come from the callee.** The
  checker resolves a call's non-lambda arguments before inferring any lambda body,
  so a slot fixed by a *later* argument is still known in time:

  ```sprout
  # `acc` is fixed by the seed, `b` by the list element -- both before the body
  # is inferred, so `.volume_m3` resolves and `+` picks Double rather than Int.
  list_fold(\(acc, b) -> Hold(volume_m3 = acc.volume_m3 + b.volume_m3), no_hold, bays)
  ```

  A lambda in any other position (a `let` initializer, a record field) has no such
  slot, and its parameters are inferred solely from its body.
- Function application is **n-ary**: a call saturates its callee. **Under-application
  of a known function** — supplying fewer arguments than its declared arity — is a
  **compile error** (`'f' expects N arguments, got M`); use a `_`-placeholder partial
  (above) to partially apply. A function whose declared return type is itself a
  function (e.g. `-> Int -> Int`) is not under-application: it is saturated at its own
  parameter count and returns that function.
- **Applying a function-typed *value* is checked at run time, not compile time.** A
  function type does not determine how many arguments one application consumes: a
  two-parameter lambda `\ (x, y) -> …` and a nested pair of one-parameter lambdas
  `\x -> \y -> …` both have type `Int -> Int -> Int`. So where the callee is a value
  rather than a name with a declared arity — a parameter, a `let` binding, an element
  of a data structure — the compiler cannot decide the question, and the argument count
  is compared against the value's actual arity when the application runs. A mismatch in
  either direction **aborts with a diagnostic and a non-zero exit status**; it is never
  silently completed, and never a partial application. Both spellings below are
  well-typed, and each is an error for the other's callee:

  ```sprout
  fn pair_add() -> Int -> Int -> Int = \ (x, y) -> x + y   # one application, two args
  fn nested_add() -> Int -> Int -> Int = \x -> \y -> x + y # two applications, one each
  ```

  Making this a *compile* error instead requires function types to carry their arity;
  that is a follow-up — see `BACKLOG.md`.

**Placeholder partial application.** A bare `_` in a call-argument position is a
*hole*. A call containing one or more holes desugars, at parse time, to a lambda
that binds the holes left-to-right:

```sprout
add(_, 3)      # ≡ \p -> add(p, 3)
add(1, _)      # ≡ \p -> add(1, p)  (any position, not only leftmost)
add3(_, _, 3)  # ≡ \(p, q) -> add3(p, q, 3)  (multiple holes -> multi-param lambda)
```

- A `_` binds to the **innermost enclosing call**: in `f(g(_), 3)` the hole
  belongs to `g`, giving `f(\p -> g(p), 3)`.
- Multiple holes in one call produce a multi-parameter lambda, one parameter per
  hole, in source order.
- Non-hole arguments are captured by expression and re-evaluated on each call.
- A `_` outside call-argument position — in function position (`_(x)`), as a
  bare expression, or inside a list/record/tuple literal — is not a placeholder
  and is rejected. Operator sections (`_ + 1`) are not part of v0.
- The result is an ordinary lambda: placeholders add no type-system, evaluation,
  or runtime semantics beyond the desugaring.
- Composed with the pipe operator, a placeholder gives **positional** control:
  because `f(a, _)` is already a lambda, `x |> f(a, _)` applies it to `x`, i.e.
  `f(a, x)` — the piped value fills the hole rather than being appended as the
  final argument. Bare multi-argument pipe stays append (§5.5); `_` is the
  explicit way to place the piped value elsewhere. This settles the `|>` multi-arg
  question of `docs/currying-and-pipe-decision-v1.md`.

### 5.3b Prefix operators

Two prefix operators are built in:

- `!e` — logical negation. The operand must have type `Bool`, and the result has
  type `Bool`. `! 3` is a type error (the operand is not `Bool`).
- `-e` — arithmetic negation. The operand must have type `Int` or `Double`, and
  the result has that same type. `- true` is a type error.

The operand type is checked, not inferred through: applying a prefix operator to
an operand of the wrong type is rejected at compile time. (`!` and `-` are
compiler primitives in v0; `docs/operators-v0.md` proposes giving all operators
first-class signatures.)

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
- `value |> g(a, b)` means `g(a, b, value)` — with no placeholder, the piped value
  is appended as the final argument (data-last convention).
- `value |> g(a, _)` means `g(a, value)` — a `_` placeholder in the piped call
  fills at the hole's position instead of the final position (§5.3), the explicit
  way to thread a value into a non-final argument. Append and hole-fill coincide
  only when the hole is last.

It associates to the left, so `x |> f(a) |> g(b)` means `g(b, f(a, x))`.

### 5.6 ADT declaration

```sprout
type Maybe a =
  | Nothing
  | Just a
```

A constructor carries at most **255 fields**; exceeding it is a compile error
naming the constructor. Records share the ceiling (§5.6.3). The limit is the
width of the runtime object header's field-count byte, not a design rule; it is
far above any practical product type.

Every type name referenced in a constructor field, record field, or type alias
RHS must be either:

- A locally-declared type (in the same module bundle).
- A built-in primitive type (`Int`, `Double`, `Bool`, `String`, `Char`, `Unit`,
  `Bytes`).
  > `IntRange` was in this list until 2026-08-19. It is now an ordinary type
  > declared in the prelude (§8.3), so it satisfies the *first* bullet instead.
  >
  > The *name* still resolves in a file that receives no prelude, because the
  > bundler admits the prelude's exported type names in that case rather than
  > rejecting every signature mentioning `Maybe` or `Result`. What such a file
  > cannot do is *build* a range: `a..b` lowers to a call to the prelude's
  > `range_up`, which compiles and then fails at link with an undefined symbol —
  > the same outcome as calling any other prelude function from such a file.
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

#### Existential constructors (experimental — Stage 0a)

A constructor may bind an **existential** type variable with a constructor-level
`exists` prefix. The variable scopes over the constructor's fields but is
**absent from the type head**, so constructing a value *hides* the field's type:

```sprout
type Boxed = | exists a. Boxed a

# Each element's type (Int / String / Bool) is hidden behind `Boxed`, so this
# heterogeneous list typechecks as `List Boxed`:
let row = [Boxed(1), Boxed("hi"), Boxed(true)]
```

**Construction (pack).** `Boxed(v)` accepts a value of any type; the bound
variable is instantiated to that type and then hidden — it does not appear in the
result type `Boxed`.

**Match (unpack).** Matching `Boxed x` binds `x` to a fresh **abstract** type,
distinct from every concrete type and from every other unpacked existential.
`x` may be moved, ignored, or re-packed, but it **must not be used at a concrete
type or unified with a different existential** — doing so is a compile error:

```
existential type escapes its scope: a hidden `exists`-bound type cannot be used
at a concrete or a different hidden type
```

So `match b with | Boxed x -> x` returned at a concrete type, `x + 1`, and
merging two separately-unpacked existentials are all rejected; re-packing
(`Boxed x -> Boxed(x)`) and ignoring (`Boxed _ -> ...`) are allowed.

The hidden type must also not **outlive the unpack**: it may not appear in the
type of the enclosing declaration, whether that type was written or inferred.
Omitting the annotation is not an exception —

```
fn unbox(b: Boxed) = match b with | Boxed x -> x       # rejected at the decl
let leaked = match Boxed("hi") with | Boxed x -> x     # rejected at the binding
```

are both errors, reported as `existential type escapes its scope in <name>`.
Were either accepted, the abstract type would be fixed into that declaration's
type and then shared by every one of its call sites — the opposite of the
per-unpack freshness the rule above depends on, and enough to make two unpacks
unify through the one shared declaration.

**Runtime.** Zero cost — the same tagged representation as an ordinary
constructor; the hidden type is erased.

**Constrained existentials via `any C` (experimental — Stage 0b).** A
constructor field written `(any C)` hides a value of any type that has an instance
of the class `C`, and makes that instance available on the unpacked
value:

```sprout
type Shown = | Shown (any ToString)     # ≡ hides a value with a ToString instance

let cells = [Shown(42), Shown("hi"), Shown(true)]   # : List Shown

fn render(s: Shown) -> String =
  match s with
  | Shown x -> to_string(x)             # dispatches on the packed witness
```

Construction resolves the class instance at the value's concrete type and **packs
its dictionary** into the constructor; constructing with a type that has no
instance is rejected at the construction site with the usual "No instance of `C`
for `T`" error (§8). Matching binds the value at the abstract existential type
(as above), but the packed dictionary is in scope, so a call to a `C` method on it
dispatches through the packed witness — each element renders as its own concrete
type would. A **multi-method** class packs one witness per method, and a class
**with superclasses** packs its transitive-superclass methods too, so an inherited
(superclass) method also dispatches on the unpacked value.

**Explicit `exists … where` prefix.** The same feature is also spelled with an
explicit constructor-level `exists` prefix and a trailing **`where`** constraint
clause; `(any C)` is single-field sugar for it:

```sprout
type Shown  = | exists a. Shown a where ToString a     # ≡ Shown (any ToString)
type Q      = | exists a. Q a where ToString a, Eq a   # several constraints on one var
type Bag    = | exists a. Bag (List a) where Describe a # hidden var nested in a field
```

The prefix scopes over **all** the constructor's fields, so it additionally
expresses a hidden variable spanning several fields (shared hidden state) and a
constrained variable that appears only nested inside a compound field. The `where`
keyword matches every other constraint site in the language; `=>` is not used. A
`where` clause is valid only on an existential: each constraint must apply a class
to a variable bound by the `exists` prefix — otherwise it is a parse error (it must
not constrain a head parameter).

**Runtime.** The packed dictionary is the class's method function-pointer(s)
stored as hidden constructor fields (traced by the GC); a method call on the
unpacked value forwards them. No new runtime mechanism — the existing
dictionary-passing path is redirected into the heap value.

**Status (experimental).** Stage 0a (unconstrained), Stage 0b (`any C` and the
explicit `exists … where` prefix, including multi-method / superclass classes and
a hidden var nested in a compound field) are implemented. An *ambiguous*
existential construction whose type is undetermined (e.g. `Bag([])` — an empty
container) is currently reported as an opaque constructor-arity mismatch rather
than a located "ambiguous" error. Full GADTs (index refinement) are out of scope.
Design and staging: `docs/gadts-v0.md`.

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

### 5.6.2 Type identity

Two types are equal **iff they have the same canonical identity assigned by name
resolution** — not iff their unqualified names match. Name resolution maps every
surface type reference to the declaration it denotes, using the current module,
its imports, and the prelude, with the current module taking precedence (a
module-local type shadows a prelude type of the same short name). A bare name is a
resolution convenience, never an identity.

Consequently, two type declarations that share a short name in different modules
are **distinct types**: their values do not unify, each has its own constructor
set for exhaustiveness, and each may carry its own typeclass instances. A program
that redefines a prelude/stdlib type name gets a genuinely new type, not the
stdlib one.

> The v0 implementation realizes canonical identity as the module-qualified name
> (e.g. `main.Maybe`; the prelude, having no module header, canonically owns the
> unqualified name). That representation is an implementation choice — the
> normative rule is that identity is canonical and resolver-assigned, which leaves
> room for a future generative identity (functors / path-dependent types) without
> a spec change.

### 5.6.3 Record declaration

```sprout
type Point = (x: Int, y: Int)
```

A **record** is a `type` declaration whose right-hand side is a parenthesised,
labelled field list. It introduces a nominal product type with a fixed, ordered
set of named, heterogeneously-typed fields known at compile time. Records join
the parenthesis family (`(a, b)`, `f(a)`, `Just(x)`); the `label: Type` form uses
`:` because a field is a **type annotation**, the same `:` as a function
parameter. A parenthesised list distinguishes a record type from a tuple type by
its labels: `(x: Int, y: Int)` is a record, `(Int, Int)` is a tuple. Records may be
**parametric** (`type Boxed a = (value: a, tag: String)`); construction infers the
type arguments (`Boxed(value = 5, tag = "n")` has type `Boxed Int`) and a field
declared `a` reads back at the record's instantiated argument. A parametric record's
type variable is shared across its fields, so two fields declared `a` must receive
the same type. Records may be **used across module boundaries**: an imported
record supports construction, field access, and `with` update at the use site.

**Construction** is tag-prefixed, with `=` (a value binding, the same `=` as
`let`):

```sprout
fn origin() -> Point = Point(x = 0, y = 0)
```

Every field must be supplied exactly once; there are no defaults, no partial
construction, and no positional construction. The `:` (declaration) / `=`
(construction) split is deliberate: `:` means *has type*, `=` means *has value*.

**Field access** is dot access on a variable chain:

```sprout
fn manhattan(p: Point) -> Int = p.x + p.y
```

Because the lexer absorbs `.` into identifiers, `p.x` and `p.from.x` lex as a
single dotted token, resolved by a name-resolution rule: split on `.`; if the
head is an in-scope value the name is a field-access chain on it, otherwise it is
a module qualification (`stdlib.string.length`). A local binding wins over a
same-named module in head position. An **in-scope value** is any binding the name
resolves to — a parameter, a local, *or* a module-level binding, whether declared
by a top-level `let` in the current module or imported from another. Access is
**total** — `p.x` always yields the field's value (no `Maybe`), in contrast to
`dict_get`.

`.field` is **also a postfix operator**, so the receiver need not be a bare
variable: `f(x).field`, `(e).field` and `Point(x = 1, y = 2).x` are all
well-formed. A dot is read as this operator exactly when it does not continue an
identifier — `p.x` and `stdlib.string.length` are still single tokens settled by
the resolution rule above. The two spellings agree: both build the same
field-access chain, so `let p = f(x) in p.y` and `f(x).y` are equivalent.

Postfix `.field` binds as tightly as call application and record update, and the
three may be interleaved, associating left to right: `f(x).y.z(w)` and
`(p with (x = 1)).x` both parse. A `.` not followed by an identifier is a parse
error.

A **function-typed field may be called inline** — `p.render(x)` loads the closure
from the field and applies it, using the same head-first resolution (`p` an
in-scope value ⇒ field-call; otherwise a module-qualified function call).

**Typing.** Because records are nominal and Sprout has no row polymorphism, a
field name does not name a type: `.x` is only typeable once the type of the value
it reads from is known. That type does **not** have to be known at the point the
access is written — inference runs left to right, so a later expression in the
same declaration may be what determines it, and both orders must give the same
answer:

```sprout
fn early(p) = str_len(p.x) + zero(p)   # `.x` read before `zero` pins `p` to `P`
fn late(p)  = zero(p) + str_len(p.x)   # `zero` first — same program, same verdict
```

Both are rejected, for the same reason: `P.x` is an `Int`, not a `String`. (The
wording of the two diagnostics differs — one is reported against the call, the
other against the field read — but neither program compiles.) What is required is
that the receiver's type be determined *somewhere in the declaration*.
When nothing determines it, the access is an error rather than an open obligation:

```sprout
fn coerce(p) = p.x   # error: cannot infer the record type of `.x`
```

Annotating the parameter (`fn coerce(p: Point) = p.x`) resolves it. Accepting the
unannotated form would make `coerce` a function from any type to any type, since
the field's type would be free to be generalized.

"Somewhere in the declaration" includes the **declared return type** and a `let`
annotation, not only a parameter or a call — `fn f(p) -> Point = if p.x > 0 then p
else p` is accepted, the return type being the only thing that determines `p`.

Once the receiver's type IS known, the field must exist on it. A deferred access
whose receiver turns out to lack the field — or not to be a record at all — is an
error for the same reason, since leaving it untyped would generalize its result:

```sprout
fn missing(p) = (zero(p), p.zzz)   # error: Unknown record type or field: Point.zzz
```

**Semantics.** Records are nominal (two records with identical fields but
different names are distinct types), immutable, and strict (field values are
evaluated eagerly at construction). Field scoping is per-record: a field `x` of
`Point` is unrelated to a field `x` of another type. At runtime a record is a
boxed single-constructor product laid out at fixed field offsets, sharing the ADT
object model (and therefore ordinary garbage-collection behavior).

Records are distinct from `Dict v` (String-keyed, homogeneous-valued, open,
partial `dict_get -> Maybe v`) on every axis and are not interchangeable with it.

**Functional update.** `base with (field = value, ...)` produces a **new** record
with the named fields replaced and all others copied from the base; the base is
unchanged (records are immutable). `with` reuses the match keyword — update-`with`
follows a value expression, match-`with` follows `match`, and they are
distinguished by the token after `with` (`( ident =` is an update, never a match
branch). The base is evaluated once; updates chain left-to-right
(`p with (x = 1) with (y = 2)`). Only declared fields may be named — an unknown
field is a compile error. A `..`-spread is rejected (`..` is the range operator).

```sprout
fn shift_right(p: Point) -> Point = p with (x = p.x + 1)
```

Restrictions (v0): no row polymorphism or extensible records, no structural
subtyping, and no field punning or defaults. (Records *do* support `deriving` —
see §12.)

Records work across module boundaries. Construction, field access, `with`
update, parametric instantiation at differing type arguments, nested records,
function-typed fields, use as an ADT constructor's field, and derived-instance
resolution all hold for an imported record, as does the linear-record
consumption rule; `tests/stdlib/test_imported_records.spr` covers each.

A record carries at most **255 fields**, the same ceiling an ADT constructor's
field list has (§5.6); exceeding it is a compile error naming the record. The
limit is the width of the runtime object header's field-count byte, not a design
rule; it is far above any practical record.

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

### 5.5.1 List-literal lowering in a `Vec`-expected context

A list literal `[e1, …, en]` normally denotes a `List a`. When a list literal
appears in a position whose expected type is `Vec a` — a function argument for a
`Vec`-typed parameter, or a function/method body whose return type is `Vec a` —
the elaborator lowers it to `vec_from_list([e1, …, en])`, so it denotes a `Vec a`
without an explicit `vec_from_list` call. This is a **literal-only** lowering,
directed by the expected type name: only syntactic list literals are affected. A
`List`-typed variable or other `List`-valued expression in a `Vec`-expected
position is *not* coerced and remains a type error (a pre-existing `List` vs
`Vec` mismatch), because a syntactic literal is the only expression form
statically known to be a `List` prior to type inference. Empty `[]` in a
`Vec`-expected context denotes an empty `Vec`. Plain `List`-expected and
inference-driven contexts are unaffected. This parallels the `StringTemplate`
lowering above (both are context-directed literal lowerings). Rationale and
prior art: `docs/coercions-and-literals-v1-draft.md` (Case A).

### 5.8 Linear types (Experimental)

> **Experimental** — not part of normative v0. Syntax stabilized in Milestone 4.1;
> consume-exactly-once enforcement in Milestone 4.2; borrowing in Milestone 4.5;
> parameter ownership moved into the function type in Milestone 4.6; one-shot
> (`once`) closure parameters in Milestone 4.4a.
> Design rationale and the deferred items live in
> `docs/linear-types-m4-scoping-2026-08-01.md`,
> `docs/linear-types-m4.2-enforcement-2026-08-06.md`,
> `docs/linear-borrowing-v0.md` and `docs/one-shot-closures-v0.md`.

A type declaration may carry the contextual modifier `linear`, written between
`type` and the type name (mirroring `type alias`). It applies to ADT and record
declarations:

```
type linear File = File Int
type linear Pos = (x: Int, y: Int)
```

`linear` is recognized only in that one position; it remains an ordinary
identifier everywhere else (no reserved word).

**Semantics — consume exactly once.** A value of a linear type must be used
*exactly once* in a function body: not zero times (a leaked resource), and not
more than once (use-after-consume). "Use" means any reference to the binding,
including as the base of a field access (`p.x` uses `p`) — except a bare reference
in non-final `do`-statement position, which does nothing with the value and is
rejected as a discard (see **Discarded result** below). Enforcement covers every
binder that can hold a linear value — **function parameters**, **do-block `let`**,
**match-arm pattern variables** (a variable pattern aliases the whole linear
scrutinee; a constructor/tuple sub-pattern binds a linear field), and **`<-`
do-bind** variables — in `fn`, top-level `let`, and instance-method bodies, and is
checked on every control-flow path:

- **Reuse** — a linear binding referenced more than once along a path is rejected.
- **Leak** — a linear binding referenced zero times in its scope is rejected.
- **Branch convergence** — in an `if`/`match`, a linear binding defined outside it
  must be used either zero times in the whole construct, or exactly once in *every*
  branch; using it in some branches but not others is rejected.
- **Discarded result** — a `do`-block statement in non-final position is rejected
  when its value is linear, *contains* a linear value as a type argument or tuple
  component (`Maybe File`), or has a bare type-variable type. So is a wildcard bind
  (`_ <- e`) of such a value. The three rules above are keyed on binders, so a
  linear value that is never bound carries no obligation for them to find
  unfulfilled: `do { task_fork(s, w); 7 }` would otherwise drop the handle in
  silence. The **final** statement is exempt — it is the block's result, so the
  obligation passes to the caller.

  Containment is checked because in a `Maybe`/`Result` block *every* statement has
  type `Maybe X`/`Result E X`; a rule reading only the type's head could never fire
  in a short-circuiting block. A bare type variable is rejected conservatively —
  the body is checked with the variable rigid, and a caller may instantiate it at
  a linear type — which can refuse a program that only ever instantiates it at a
  non-linear type. Sprout has no linearity bound on a type parameter with which to
  state the difference; a type variable *nested* in a container (`Maybe a`) is
  therefore not checked. Two shapes remain accepted and are **not** guaranteed
  leak-free: a wildcard `match` arm over a linear value (`match f with | _ -> 0`,
  which does reference `f` exactly once) and a constructor pattern that drops a
  linear field (`Wrap _`).

A linear ADT is consumed once by matching it (`match f with | File n -> …`) or by
passing it to a function. A linear record is read via field access, which consumes
it — so an *owned* linear record supports reading a single field or being passed
once; reading two fields (`p.x + p.y`) is a reuse (records have no destructuring
pattern). A `borrowing` parameter lifts that restriction (see below). For a value
meant to be read freely, do not declare it `linear`.

#### Borrowing (Milestone 4.5)

A resource is often **acquire → use N times → release once**; a socket is read and
written repeatedly, then closed. Strict use-exactly-once cannot express that at
all — the first use would consume it. A **borrow** is a use that does *not* claim
the value's one consuming use.

A parameter whose type is linear may carry an ownership modifier between the `:`
and the type:

```
export fn write(conn: borrowing TcpConnection, b: Bytes) -> Result … !{IO} = …
export fn close(conn: consuming TcpConnection)          -> Unit       !{IO} = …

conn <- connect(host, port)
_    <- write(conn, request)   # borrows; conn stays live and still owes its use
line <- read(conn, 128)        # borrows again
close(conn)                    # the single consuming use
```

- `borrowing` — a non-consuming use. The consume obligation stays with the
  **caller**, so the parameter may be used zero or many times inside the function
  and must **not** be consumed there.
- `consuming` — the explicit spelling of the default. An unmodified linear
  parameter is already consuming, so this is redundant, but it documents the
  contract at an API boundary. No existing program changes meaning.

- `once` — see **One-shot closure parameters** below. It sits in the same slot but
  constrains a different thing: not how the parameter is taken, but how often the
  callee may invoke it.

All three are **contextual keywords**: `borrowing`, `consuming` and `once` remain
ordinary identifiers everywhere else, including as type names (`x: borrowing` is
the type named `borrowing`, since no type follows the word).

**Where a use is a borrow.** A reference to a linear binding is a borrow when it
sits at a `borrowing` parameter position, or when the binding it names is itself a
`borrowing` parameter and the reference merely *reads through* it — as the base of
a field access or as a `match` scrutinee. Every other reference consumes. So
`p.x + p.y` is legal for `p: borrowing Pos` and remains a reuse for an owned `p`.

**Rules.**

- **Leak is unchanged and strict** — a borrow never discharges the once-only
  obligation, so an omitted `close` is still rejected. That is the point of the
  feature.
- **Use after consume** — a borrow may not follow the consume along a path
  (`close(conn); write(conn, …)` is rejected). Borrow-then-consume is the normal
  order and is fine. Evaluation order is the one fixed in §6.
- **Branch convergence** applies to consumes only; arms need not agree on borrows.
- **Borrowed contents** — destructuring a borrowed value binds its linear fields
  as borrowed too, so they cannot be consumed out from under the owner. For the
  same reason a **linear field may not be read out of a borrowed value**
  (`w.inner` where `w: borrowing Wrap` and `inner` is linear): the result would be
  an owned value its real owner will still release. A non-linear field reads
  freely.
- **A consume may not follow a fallible bind.** A `<-` bind whose right-hand side is
  `Maybe` or `Result` short-circuits (§5.9), so the steps after it are conditional.
  Consuming a binding from outside the block there would be skipped on the failure
  path and the value leaked, so it is rejected; consume before the first fallible
  `<-`, or branch explicitly with `match`. The condition is the **bind's** type, not
  the block's, and `!{IO}` does not exempt it: an effect row and a short-circuit are
  orthogonal, so an `!{IO}` block containing a fallible bind does *not* run every
  step. A block whose own type is `Maybe`/`Result` but whose binds are all
  non-fallible is likewise unaffected — nothing in it can return early.
- A `borrowing` parameter may not be consumed or returned.
- An argument at a `borrowing` position must be a **variable reference**; a
  freshly-built linear value there would never be consumed.
- The modifiers are **erased**: they reach no IR pass and emitted code is
  byte-identical with and without them.

**Ownership is part of the function type (Milestone 4.6).** `(borrowing T) -> U`
and `T -> U` are **different types**, and the ownership is carried on the arrow
alongside the effect row. A `borrowing` function may therefore be bound, passed
and called as a value, and class and instance methods may carry modifiers — the
mode is readable wherever the type is.

The two conventions are **invariant**: neither substitutes for the other, in
either direction. Supplying a consuming function where a borrowing one is expected
means the value is released by a call whose caller still owns it (double consume);
supplying a borrowing one where consuming is expected discharges the caller's
obligation through a call that releases nothing (leak). A mismatch is a type
error where the mismatch occurs.

Two consequences follow:

- **An instance method's modifiers must match its class declaration's.** A call
  dispatches through the class signature, so an instance that borrows where the
  class consumes would leak. Compared as *ownership*, not as written text:
  `consuming` on the instance against an unmodified class parameter agrees, since
  both consume.
- **An arrow type written in an annotation means consuming.** Arrow-type syntax
  cannot yet spell `borrowing`, so passing a borrowing function to a parameter
  annotated `(T) -> U` is an ownership mismatch. This is a syntax gap, not a
  semantic one, and the diagnostic says so.

**A modifier on a non-linear parameter is an error**, as is one on a
type-variable parameter (reported distinctly). The type-variable case is not a
representation limit — ownership sits in the type and would survive
instantiation — but a *universe* one: admitting `borrowing a` without a linearity
bound on `a` would make `borrowing Int` an error while `borrowing a` instantiated
at `Int` silently was not. Lifting it therefore requires polymorphism over linear
types, which is a non-goal here. Both state-of-the-art designs bound the parameter
first: Swift SE-0427 makes generic parameters `Copyable` by default and requires
`<T: ~Copyable>` to opt out, and Austral annotates every type parameter with a
universe (`Free`, `Linear`, or `Type`).
`extern fn` signatures are checked the same way even though they have no body. This diverges deliberately from Swift, whose
`borrowing`/`consuming` apply to any parameter because under ARC they still change
retain/release traffic. Sprout has a tracing GC and erases the modifiers, so on a
non-linear parameter they would carry no meaning at all; rejecting them keeps them
from reading as enforcement that is not there.

**One-shot closure parameters (`once`, Milestone 4.4a).** A parameter may be
declared `once`:

```
export fn task_spawn(scope: Scope, work: once Unit -> Unit !{IO}) -> Unit !{IO} = …
```

This is a promise by the **callee**: it invokes `work` **at most once**, and does
not store or return it. The promise is **checked, not assumed** — a function
declaring `once p` may use `p` at most once along any path, counted per
control-flow path, so `if c then p(x) else p(x)` is one invocation and is legal
while `p(x) + p(x)` is not. Zero uses are legal: the bound is from above.

`once` is only meaningful on a **function-typed** parameter; elsewhere it is an
error. In particular it is rejected on a **type-variable** parameter, and for a
reason of its own — not the universe argument that rejects `borrowing a`. The
licence `once` grants rests on the callee being able to *invoke* the parameter,
which it cannot do when the type is not known to be a function, while the value
would remain freely returnable and duplicable. `once a` is therefore strictly
worse than a concrete parameter whose promise is merely wrong.

The promise licenses one thing at the **caller**: a lambda passed at a `once`
position may **move** linear values into itself. A moved capture is consumed *at
the call*, so it is discharged there and any later use is a reuse:

```
conn <- accept(listener)
task_spawn(scope, \_ -> handle(conn))   # conn is MOVED into the closure
                                         # any use of conn after this is rejected
```

- Each moved value must be consumed **exactly once inside the closure body**, on
  every path. The body is checked by the ordinary rules, so branch convergence and
  reuse detection apply unchanged within it. Reading the moved value before
  consuming it inside the same closure is a normal borrow-then-consume and is
  allowed — `\_ -> do { write(c, x); close(c) }` is the intended shape.
- A value the closure only **borrows** — reads without consuming — may still not
  be captured, at a `once` parameter or anywhere else. The closure may run after
  the owner has consumed the value, and Sprout has no escaping/non-escaping or
  lifetime distinction to rule that out.
- A **linear lambda parameter** is still rejected: `once` bounds how often the
  closure runs, not what may be handed to it on each run.
- `once` is **erased**, like the ownership modifiers, and is part of the function
  type, so it is compared invariantly at unification.

**At most once, not exactly once.** The type system bounds invocations from above
only — matching Rust's `FnOnce` and OxCaml's `once`, both of which are also
at-most-once. Leak-freedom for a moved value therefore also depends on the
callee's runtime contract that the closure *does* run: for `stdlib.task` that is
`with_scope`'s unconditional join.

**Leak-freedom for moved values holds absent scope cancellation.** Cancelling a
scope force-drops tasks that have not started, and every `with_timeout` expiry
reaches the same path, so a value moved into such a task's closure is never
released — while the program still type-checks as consume-exactly-once clean.
Sprout has no destructors, so closing this requires cancellation-time resource
release in the scheduler rather than a typing rule. Until it exists, do not move a
linear resource into a task in a scope that may be cancelled or timed out: acquire
it inside the task instead.

**Deferred (rejected with a diagnostic, never silently accepted):**

- Higher-order linearity beyond the `once` case — a linear binding captured by a
  lambda at an **unannotated** parameter, and linear lambda parameters, are not yet
  supported, nor is capturing a **borrowed** value anywhere. This is why the
  combinator form (`list_each(xs, \x -> write(conn, x))`) is still out of reach.
  The spawn-a-handler server shape is no longer: it is a move into a one-shot
  closure, and `stdlib.http_server` now runs on the linear socket API throughout.
- Containment virality — linearity is *per-declaration*: a record that merely
  contains a linear field is not itself linear (contrast Austral).
- `borrowing` inside an **arrow type**, and a modifier on a **type-variable**
  parameter. Both are described above; both need work this milestone deliberately
  did not take on (a parser change, and a linearity bound on type parameters).

### 5.9 The fallible `<-` bind

Inside a `do` block, `<pat> <- <e>` is an **effectful bind** when `e`'s type is
anything other than `Maybe`/`Result`, and a **fallible bind** when it is one of those
two. `Maybe` and `Result` are the only short-circuiting families; the behaviour is
not user-extensible.

A fallible bind `x <- e` where `e : Result E A` binds `x : A` on success. On failure
it **returns from the enclosing function**, carrying the failure — it does not merely
end the block. `Maybe` behaves the same way with `Nothing` in place of `Err`.

Because the failure leaves through the function's return, it must be a value that
function can return. A fallible bind is therefore well-typed only where the block's
type can carry the failure:

| Right-hand side | Block's type must be | On failure |
| --- | --- | --- |
| `Result E A` | `Result E B` — same error type `E` | returns `Err e` unchanged |
| `Maybe A` | `Maybe B` | returns `Nothing` |

Anything else is a **type error**, including a non-fallible block type (`Int`,
`String`, `Unit`, …), the other family, and the same family with a different error
type. Convert at the bind with `map_error` to change `E`, or handle the failure in
place — see the discard form below — rather than propagating it.

The binder does not affect propagation: `_ <- e` short-circuits exactly as `x <- e`
does, and is subject to the same rule. To run a fallible expression and **continue**
regardless, use it as a bare statement, which discards the whole `Maybe`/`Result`:

```sprout
do
  x <- may_fail(a)       # want the value; failure propagates (block must be fallible)
  _ <- may_fail(b)       # ignore the value; failure still propagates
  may_fail(c)            # run it, discard the Result, CONTINUE
  done(x)
```

Only one family may short-circuit within a single block; mixing `Maybe` and `Result`
binds is rejected. The block's own type is the type of its **last** step and is not
implicitly wrapped: a `Result`-returning function whose `do` block ends in a bare `A`
is a type error, not an implicit `Ok`.

Effects are orthogonal. `!{IO}` describes *what a step may do*, not whether the block
can exit early, so an `!{IO}` block containing a fallible bind still short-circuits
and its later steps are still conditional (see §5.8's consume rule).

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
  functions; their declared function type is the language contract. A builtin has
  no Sprout body to infer from, so its declaration is taken on trust and is the
  one place the effect discipline rests on an assertion rather than a check —
  which is why the rule below is stated normatively and `runtime/APPROVED_BUILTINS`
  requires a justification per entry.
- A builtin uses `!{IO}` when evaluating the call may interact with runtime or
  external state such as terminal IO, files, environment, network, randomness,
  or host-backed analysis services **in a way the rest of the program can
  observe**. The qualifier is normative: the test is whether a *continuation* can
  tell the call happened, not whether the implementation touches a descriptor.
  The two readings differ only on builtins that abort, covered below.
- A builtin stays pure when it only computes or transforms values, even if its
  result type is `Maybe ...` or `Result ...`.
- **Aborting the program is not an effect.** A builtin that may terminate the
  process stays pure. An abort has no continuation, so no Sprout expression can
  observe it, and no caller behaves differently for having called a function that
  might abort — if it aborts, the caller does not run.

  This is not a new exemption; it is what the prelude has always done. Every
  runtime abort goes through `tcp_fail`, which writes `runtime error: <msg>` to
  stderr and calls `exit(1)`, and there are ~187 such call sites behind builtins
  that are overwhelmingly declared **pure**: `vector_length : Vector a -> Int`
  aborts on a null vector, as do `vector_get`, `str_len` and most of the rest.
  Under the descriptor-touching reading every one of them would be `!{IO}`.

  `panic : String -> a` is the only builtin whose *sole purpose* is to abort, and
  until 2026-08-16 it was the only one annotated `!{IO}` for it. That made it the
  odd one out rather than the exception, so it is now declared pure like the rest.
  Its bottom-shaped return type `a` already carries the only information a caller
  can act on: this does not come back.

  The cost was measured in both directions: as `!{IO}` it made nine functions in
  `stdlib/compiler/` — pure in every case where they return, ending in
  `| _ -> panic("… (internal error)")` — report as effect gaps. It also matches
  every language surveyed; Java, Swift and Zig each track recoverable failure in a
  signature and each deliberately exempt the abort, and Koka, the one language
  that tracks it, tracks it as `exn`, a separate weaker effect inside its own
  `pure` alias rather than folded into `io`. Rationale and primary sources:
  `docs/effect-enforcement-v0.md` §6.

  Note this does **not** generalise to "diverging calls are pure". An infinite
  loop is not an abort; a Sprout function that never returns but keeps running
  can still perform observable I/O, and its effects are tracked as usual. The
  exemption is for termination of the whole program, which is why it covers
  exactly one builtin.
- v0 provides only restricted effect polymorphism via singleton effect variables
  such as `!{e}`.
- v0 does not provide delayed execution, mixed/open effect rows, or handlers.
- Because top-level `let` bindings must be pure, imported modules do not perform
  effectful initialization merely by being loaded.

> **Not yet enforced (2026-08-16).** The top-level-`let` purity rule above is normative
> but unchecked: `let boom = print("at load time")` type-checks, binding
> `main.boom : Unit`. The §7 enforcement note below covers `fn` and instance-method
> bodies; a `let` initializer's inferred effect is still discarded. Tracked in
> `BACKLOG.md`. Do not read this bullet as a guarantee the compiler currently makes —
> it is what v0 intends, and until the check lands an effectful top-level `let` is
> accepted.

## 7. Typing Rules (High Level)

1. Every expression has exactly one type after inference/checking.
2. `if` condition must be `Bool`.
3. Function application requires argument types to unify with parameter types.
   Arguments are checked in **two passes**: the non-lambda arguments are inferred
   and unified against their parameter slots first, then each lambda argument is
   inferred with its parameter types taken from the (now-resolved) slot. Argument
   *evaluation* order is unaffected; only the order in which the checker visits
   them differs. See §5.3.
4. Lambda expressions introduce parameter bindings for their body and infer a
   function type from parameters to body result. In argument position the
   parameter types come from the callee's parameter slot (rule 3).
5. `match` branches must have a unified result type.
6. Pattern-bound variables are scoped to their branch.
7. ADT constructors produce values of their declared type.
8. Effect annotations are checked on function types; omitted annotations mean purity.
9. Function types may quantify a singleton effect variable `!{e}`; use sites
   instantiate it with either purity or a concrete closed effect supported in v0.
   **Singleton is a limit, not a description**: a signature may name at most one
   effect variable. `f: a -> Unit !{e}` beside `g: b -> Unit !{d}` is rejected
   whether or not the body ever combines them — the limit is on the signature, so
   an unused second variable violates it just as a used one does. Write the same
   variable on every effect-polymorphic parameter instead.

   An annotation is a single concrete effect (`!{IO}`), a single effect variable
   (`!{e}`), or omitted for purity. A multi-label row — `!{IO, e}`, `!{a, b}` —
   is not a form this section defines and is rejected wherever it is written,
   including on a parameter's arrow. Note `!{IO, e}` names only one variable and
   so satisfies the singleton limit above; it is rejected under this sentence
   rather than that one.
10. `main` must use a concrete effect annotation when effectful; it may not be
    effect-polymorphic.
11. A pure function body may not call `!{IO}` functions unless it is allowed by
    the surrounding singleton effect variable instantiation.
12. Tuple expressions and tuple patterns use structural, exact-arity typing.
13. In a function-local `where` block, each binding is checked in order using the
    function parameters and earlier local bindings; later bindings are not in scope.
    Each binding's type comes from its right-hand side and is then available to the
    body — information flows right-hand-side to binding, never body to binding
    (§5.1). `where` and `let … in` are required to type identically.
14. Function-local `where` bindings may destructure tuples, but constructor, literal,
    and general pattern bindings are not part of v0. A tuple pattern binds the
    right-hand side's element types positionally; the elements are not unified with
    one another, so `(a, n) = (x * 1.5, 4)` binds `a : Double` and `n : Int`.
15. **Signature rigidity.** A type variable *written* in a function's signature is
    universally quantified: the caller, not the body, chooses what it stands for.
    The body must therefore leave it abstract, and a body that constrains it is a
    compile error — "Signature too general for its body". A written variable is
    constrained when it resolves to a type that is not a type variable
    (`fn f(x: a) -> Int = x + 1` forces `a` to `Int`; `fn f(x: a, g: b -> c) -> a = g`
    forces `a` to `b -> c`), or when two written variables resolve to the *same*
    variable (`fn f(x: a, y: b) -> a = y` forces `a = b`) — an equation the signature
    did not state. Both are rejected: a caller may still instantiate the variable at
    anything, so accepting either would let the caller and the body disagree about a
    value's representation. The rule applies only to variables the programmer wrote;
    an **omitted** parameter or return type is an inference request, not a promise,
    and inference is free to specialize it.

> **Enforcement of the effect rules.** Rules 8, 9, 10 and 11 are all **enforced** as of
> 2026-08-16. `fn shout(s: String) -> Unit = print(s)` is a compile error; an effect
> annotation is a checked contract, and a missing `!{IO}` now means the compiler has
> verified the function performs no IO. This replaces a note that stood for the whole of v0
> saying the opposite.
>
> Which check covers which rule:
>
> - **8** and **11** are one check. Rule 11 ("a pure function body may not call `!{IO}`
>   functions…") is rule 8 stated operationally — a body calling an `!{IO}` function infers
>   `!{IO}`, which a pure signature does not admit. Its escape clause is honoured: where the
>   body's effect resolves to a variable rather than to `!{IO}`, the declaration is accepted.
> - **9** is the singleton rule: a signature may name **at most one** effect variable, and
>   may not write a multi-label row. It is checked against the **declared signature**, never
>   against what the body infers. That distinction is normative, because the two questions
>   have different answers in both directions: a signature naming two variables whose body
>   never combines them infers no row and would pass an inference-side check, while two
>   fresh instantiations of a *single* variable — `fn f(n: Int) -> Unit !{e}` called twice —
>   do infer a row and would fail one, despite naming exactly one variable and being
>   conformant. Rule 9 constrains what the author wrote, so that is what is read.
> - **10** (`main` may not be effect-polymorphic) was already enforced, syntactically.
>
> Three properties of the check are normative, not implementation detail:
>
> 1. **The rule is subsumption, inferred ⊑ declared — not equality.** Over-declaring is
>    accepted: a pure body under an `!{IO}` signature is legal and is how a function
>    states that its result is not a function of its arguments alone. Only
>    *under*-declaration is rejected.
> 2. **Unification of an arrow's effect is total.** It binds effect variables and never
>    fails, so two arrows whose effects differ are not thereby a type error and a
>    program's acceptance never depends on effect inference reaching a particular answer
>    mid-way. Rejection happens at the declaration boundary and nowhere else.
> 3. **An unresolved effect variable is accepted.** `!{e}` is neither satisfied nor
>    violated until instantiation; where the checker does not know, it accepts. Every
>    imprecision in effect inference must therefore fail towards accepting a program, not
>    rejecting one.
>
> Enforcement runs as a pass over the whole program rather than aborting at the first
> offending declaration, so a codebase adopting it sees every gap in one compile.
> `compile_driver --phase effects` prints the same declared-vs-inferred census **without**
> rejecting, and shares the checker's gap predicate, so the report is an exact preview of
> what will be rejected.
>
> Note this note earlier said effects were "carried on `TFunc`" and "never unified"; both
> were misleading. They were carried on `Scheme` with the arrows hardcoded pure, and the
> problem was never a missing call to `unify_effects` — that is the wrong operation at a
> declaration boundary, since it accepts both directions and the rule is one-directional.
> See `docs/effect-enforcement-v0.md`.

## 8. Standard Library Math Modules

Sprout splits its math surface by numeric type, one module each, so that both can
use plain unprefixed names:

- **`stdlib.math.int`** — the normative integer math module. Its exports and
  semantics are specified below.
- **`stdlib.math`** — the `Double` (real-valued) layer. `Double` itself is an
  experimental extension rather than part of the normative v0 core (see §8.6), so
  that module's surface is documented in `docs/builtins-reference.md` and
  `docs/math-transcendental-v0.md` rather than fixed here.

Sprout has no overloading, so a single name cannot serve both types. The split is
what lets `abs`, `clamp` and `pow` mean the obvious thing in each module instead of
one of them carrying a C-style marker prefix (`fabs`, `fclamp`). Import qualified
when a module needs both:

```sprout
import stdlib.math as math
import stdlib.math.int as imath
```

### 8.1 `stdlib.math.int`

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

- `stdlib.math.int` does not introduce additional numeric types.
- `mod(x, n)` uses Euclidean modulo.
- If `n > 0`, `mod(x, n)` returns `Just r` where `0 <= r < n`.
- If `n <= 0`, `mod(x, n)` returns `Nothing`.
- `pow(base, exp)` returns `Nothing` when `exp < 0`; otherwise it returns
  `Just` of the integer power.
- `Int` is *specified* as a mathematical (arbitrary-precision) integer. No
  implementation realizes that today: the only backend lowers `Int` to machine
  `i64`, so arithmetic wraps.
- Wraparound is **defined** two's-complement behavior, not undefined behavior:
  codegen emits plain `add`/`sub`/`mul` with no `nsw`/`nuw` flags. Overflow-sensitive
  results for `abs`, `pow`, `gcd`, and `lcm` are therefore silently wrong, not
  memory-unsafe, once computation leaves the representable range. Whether `+`/`-`/`*`
  should instead trap is an open policy question
  (`docs/int-overflow-policy-decision.md`).
- This backend range limitation is a temporary implementation constraint in v0,
  not the intended long-term meaning of `Int`.
- The presence of `pow` and `mod` in `stdlib.math.int` does not imply implicit
  numeric coercions or fractional arithmetic for `Int`. A separate `Double`
  type with floating-point arithmetic has since landed as an experimental
  extension; `Int` and `Double` never implicitly coerce — conversion is
  explicit (`to_double`).

### 8.1.1 Double bit access (Experimental)

Two functions in `stdlib.math` expose the raw IEEE 754 binary64 encoding of a
`Double`. They are not in the prelude: reinterpreting a `Double`'s bits is a
deliberate reach for the math module, and `to_double` remains the globally
available numeric bridge. Import `stdlib.math` to call them.

```sprout
double_to_bits(x: Double) -> Int
double_from_bits(bits: Int) -> Double
```

Both are **total** and are **reinterpretations, not conversions**:
`double_to_bits(1.0)` is `4607182418800017408`, not `1`. `to_double` remains the
numeric bridge. The round trip is exact in both directions for every bit pattern,
including both zeros — bit access is the only way to distinguish `-0.0` from `0.0`,
since IEEE compares them equal — and every NaN payload, which is never
canonicalised.

Like `print` and `to_double`, both are compiler intrinsics rather than runtime
functions and therefore **cannot be used as first-class values**; passing one to a
higher-order function (`map(double_to_bits, xs)`) fails at link time.

Status: experimental. Rationale, the prior-art survey, and the rejected
alternatives are in `docs/double-bit-access-v0.md`.

### 8.1.2 Bitwise integer operations (Experimental)

`stdlib.bits` provides bitwise operations on `Int`. They are **functions, not
operators**: `>>` and `<<` are function composition (§5), and `&`, `^` and `~` are
not tokens of the language.

```sprout
bit_and(a: Int, b: Int) -> Int
bit_or(a: Int, b: Int) -> Int
bit_xor(a: Int, b: Int) -> Int
bit_not(a: Int) -> Int
bit_shl(x: Int, n: Int) -> Int      # left shift
bit_shr(x: Int, n: Int) -> Int      # arithmetic right shift (sign-filling)
bit_shr_zf(x: Int, n: Int) -> Int   # logical right shift (zero-fill)
```

Like `print`, `to_double` and the two functions above, these are compiler
intrinsics: each lowers to a single machine instruction, and none **can be used as
a first-class value** (`list_fold(bit_xor, 0, xs)` fails at link time). Being
`extern` declarations they are also **never module-qualified** (§"Externs are
outside the module system") — `import stdlib.bits` puts them in the build and they
are then called by bare name.

`bit_not(x)` is `-x - 1`: it flips every bit, so `bit_not(0)` is `-1`. It is the
integer complement, not the boolean `!`.

**Shift count.** A count of `0..63` shifts as expected. A count `>= 64` saturates to
the limit of the mathematical definition — `0` for `bit_shl` and `bit_shr_zf`, and
all sign bits (`0` or `-1`) for `bit_shr`. A **negative** count is an error: it
panics at run time, and a negative *literal* count is rejected at compile time.

**Shifted-out bits are discarded, and that is not an error.** `bit_shl` is `x * 2^n`
within a 64-bit window, so `bit_shl(1, 63)` is `-9223372036854775808` and
`bit_shl(2, 63)` is `0`, both total. This is deliberately independent of any
overflow policy for `*` (§6.5, §8.4).

**Width.** `bit_and`, `bit_or`, `bit_xor`, `bit_not` and `bit_shr` are defined
without reference to a width and are unaffected should `Int` become
arbitrary-precision as §8.4 intends. `bit_shl` and `bit_shr_zf` are defined on the
64-bit two's-complement representation and would have to be respecified.

`rotate`, `popcount` and similar are **not** provided: they are ordinary functions
composed from the above.

Status: experimental. Rationale, the prior-art survey and the rejected
operator spellings are in `docs/bitwise-int-ops-v0.md`.

### 8.2 Partiality

`stdlib.math.int` follows Rule 1 of the partiality convention: an out-of-domain
`Int` argument returns `Maybe` (`mod`, `pow`), because `Int` has no spare bottom
value. `stdlib.math` follows Rule 2: an out-of-domain `Double` argument returns an
IEEE `NaN` or `±inf`, detected with `math.is_nan`. The rules, the rationale, and
the prior-art survey behind them are in `docs/math-partiality-v0.md`; Rule 1's
`Maybe`-returning signatures are a standing commitment and will not be narrowed to
total functions.

### 8.3 Integer ranges (Experimental)

`IntRange` denotes an **inclusive interval walked in a fixed direction**. The direction
is part of the value, not inferred from which bound is larger. Design rationale and prior
art: `docs/ranges-v0.md`.

It is an ordinary algebraic data type declared in the prelude, **not** a built-in opaque
type (§5):

```sprout
export type IntRange =
  | IntRange Int Int Int   # start, end, step
```

Three consequences follow from it being an ordinary type, and all three are normative:

1. It may be **destructured**, like any ADT: `match r with | IntRange s e st -> …`.
2. The constructor is **applicable**, and therefore applicable with a step outside
   `{+1, -1}`. `range_step` is defined as the **sign** of the stored field — `-1` when it
   is negative and `+1` otherwise — so every operation sees a legal direction regardless
   of what was stored. An out-of-range step is normalized, never honoured and never an
   error.
3. It **prints like an ADT**: `print(1..4)` writes `IntRange(1, 4, 1)`. It formerly wrote
   `1..4`, which was a special case in the runtime's printer for the opaque
   representation.

Programs SHOULD read the fields through `range_start` / `range_end` / `range_step` rather
than by pattern match, because only `range_step` applies the normalization in (2).

**Construction.** Two peer constructors, neither of which is the default spelling:

- `range_up(lo, hi)` — step `+1`, enumerating `lo, lo+1, …, hi`.
- `range_down(hi, lo)` — step `-1`, enumerating `hi, hi-1, …, lo`. Arguments read in
  iteration order, so the first argument is where enumeration begins.

`lo..hi` is sugar for `range_up(lo, hi)` — a call to that prelude function, so the syntax
requires the prelude to be in scope. **There is no descending literal**: `a..b` always
builds an ascending range. Both operands are evaluated left-to-right, as for any binary
operator.

**Emptiness.** A range is empty when its end lies past its start *in its direction of
travel*:

| step | non-empty when | empty when |
|---|---|---|
| `+1` | `start <= end` | `end < start` |
| `-1` | `start >= end` | `end > start` |

Emptiness is therefore direction-relative: `range_up(5, 1)` and `range_down(1, 5)` are
both empty, while `range_up(1, 5)` and `range_down(5, 1)` both have five elements. This
is the total answer in the sense of §8.2 — an empty interval is well defined, so no
partiality arises and nothing panics.

**Contracts on an empty range.** All of the following hold, for either direction:

- `range_count` is `0`
- `range_is_empty` is `true`
- `range_contains` is `false` for every target
- `range_to_list` is `Nil`; `range_to_vec` has no elements
- `range_fold(f, init, r)` is `init`, with `f` **not applied even once**
- `range_each(f, r)` applies `f` zero times

**Bounds are stored as written.** No normalization occurs, so `range_start` and
`range_end` return the operands as given — for a descending range, `range_start` is the
larger. `to_string` reflects those bounds and does not show the direction, so a
descending range and its reversed ascending twin render alike.

**Diagnostics.** A range whose bounds are both integer *literals* and which is empty for
its direction is **rejected at compile time**, naming the fix. This mirrors the rule for
shift counts (§8.1.2): the computed case gets the total runtime answer, while a
statically-known-empty range is a program that cannot be right. A range with computed
bounds is never diagnosed — `range_up(0, n - 1)` at `n == 0` is legal and empty, and is
the ordinary spelling of a half-open `[0, n)` walk.

**Step.** `range_step` reports `+1` or `-1`. No other step is constructible in this
version; arbitrary steps are deferred (`docs/ranges-v0.md` §4).

## 8.5 Standard Prelude Typeclass Instances (Experimental)

The following typeclass instances are provided by `stdlib/prelude.sprout` and are
automatically available without an explicit import.  They are experimental in v0
(consistent with the module/typeclass extension status noted in §1).

**Declaration syntax.**  A `class` or `instance` declaration is a head followed
by a body of `fn` members.  The body takes one of two forms.

*Layout form* (idiomatic) — the members are the run of `fn` declarations
indented past the `class`/`instance` keyword and aligned on a single column.
The body ends at the first token that is not a `fn` on that column, normally the
next top-level declaration:

```sprout
class Codec a
  fn from_int(n: Int) -> a
  fn to_str(x: a) -> String

instance Codec Int
  fn from_int(n: Int) -> Int = n
  fn to_str(x: Int) -> String = int_to_string(x)
```

*Brace form* — the members are enclosed in `{ … }` and their indentation is not
significant:

```sprout
class Codec a {
  fn from_int(n: Int) -> a
  fn to_str(x: a) -> String
}
```

Both forms are accepted and denote the same declaration.  The layout form is
idiomatic; the brace form is **deprecated**.  It still parses — no source is
broken by the deprecation — but the linter reports every occurrence as
`deprecated-brace-body`, anchored on the opening `{`.  A `{` in an effect
annotation within the declaration head (`instance Boxer (a !{IO})`) is not a
body brace and is not reported.

The layout rule is the one already used by `do`, `let … in` and `match`.  The
first member fixes the **block column**, and exactly one thing ends the body: a
`fn` at or left of the `class`/`instance` keyword's own column.  A member
indented past the keyword but *not* on the block column — in either direction —
is rejected with **`Unexpected indentation in class body`** (resp. **`… in
instance body`**).  A member's body may wrap onto further-indented lines without
ending the block.  An empty body is legal — the class declares no methods.

Under-indentation is an error rather than an end-of-body because an instance
member `fn f(x) = e` is syntactically identical to a top-level `fn`.  Were a
misaligned member handed back to the top-level parser it would be silently
reinterpreted as an ordinary function, leaving the instance short a method and
the program compiling with a different meaning and no diagnostic.  This is where
the layout form is deliberately stricter than `do`, which ends its block on any
dedent: a `do` block's enclosing context is an expression, so a dedented token
there is a genuine continuation of an outer block, whereas nothing at all may
appear between a body's members.

**Constraint syntax.**  A `where` clause names the **class first, then the
constrained type variable(s)**: `where ToString a`, `where Applicative f`,
`where Eq a, Ord b`.  Two well-formedness rules are enforced at check time, both
preventing a silently-dropped constraint from lowering to an undefined class-method
call that would otherwise only fail at link time:

- A variable-first ordering (`where a ToString`) is rejected — the head token is
  read as the class name, so `a` is diagnosed as "not a class", with a hint
  suggesting the corrected order.
- The constrained type variable must **appear in the function's signature**
  (a parameter annotation or the return type).  A constraint on a variable that
  does not — e.g. `where ToString x` where `x` is an *unannotated value
  parameter*, or `where Eq b` where only `a` is annotated — is rejected as
  ambiguous, since no call site could ever determine the type to dispatch on.

**Ambiguous class-method dispatch.**  The two rules above are about a *declared*
`where` clause.  An expression can also be ambiguous with no `where` clause
anywhere, when a class method's dispatch type occurs only in an intermediate
value.  The canonical shape is a producer and a consumer of the same class
composed directly:

```sprout
class Codec a
  fn from_int(n: Int) -> a
  fn to_str(x: a) -> String

# rejected: nothing determines which Codec instance `from_int` should produce
fn main() -> Unit !{IO} = print(to_str(from_int(7)))
```

`a` appears nowhere but between the two calls, so every `Codec` instance
satisfies the constraint and different ones give different answers.  Sprout
rejects this with a located **`ambiguous type variable in `<method>`: nothing
determines which `<Class>` instance to use`**.  Both the type and the value must
be pinned down by something the call can see; annotate the intermediate
(`to_str(from_int(7) : Int)`) or an enclosing binding to resolve it.

The rule is *not* "a class-method call must dispatch on a concrete type" —
forwarding is still fine.  Inside `fn describe(x: b) -> String where Codec b`, a
call `to_str(x)` dispatches on a type variable and is accepted, because the
caller supplies the instance.  What is rejected is a class-method call whose
dispatch type is determined by **neither** a concrete type **nor** any
constraint in scope.  In particular a variable that merely *coexists* with an
unrelated `where` clause does not count: in

```sprout
# also rejected: `to_str(from_int(7))` is closed — it never mentions `b`
fn describe(witness: b) -> String where Codec b = to_str(from_int(7))
```

the `Codec b` witness must not be borrowed to resolve the closed subexpression.
Doing so would make one expression denote different values in different calls,
which is an incoherence rather than a missing diagnostic.

**Dispatch identity under several constraints of the same class.**  When more
than one constraint of the same class is in scope, an **operator** dispatches on
its own operand's type, never on whichever witness the compiler reaches first.
This holds even when the operand's type is tied to the constraint variable only
indirectly — in particular for a lambda parameter, whose type is fixed only once
the lambda is matched against the higher-order function's parameter:

```sprout
fn fold_both(xs: List a, ys: List b, sa: a, sb: b, …) -> String
  where Semigroup a, Semigroup b =
  showa(list_fold(\ (acc, x) -> acc ++ x, sa, xs))   # uses Semigroup a
  ++ "|"
  ++ showb(list_fold(\ (acc, y) -> acc ++ y, sb, ys))  # uses Semigroup b
```

The two `++` operators denote different `append` implementations, chosen by
their own operands.  Declaration order of the `where` constraints has no effect.

The same holds for a class-method call whose dispatching argument the compiler
can identify from the method's declared parameter types.  It is **not yet
guaranteed** when it cannot — when the constraint variable appears only nested
inside a parameter's type rather than as that parameter's own type.  With
several constraints of one class in scope, such a call may still select the
wrong one; this is a known gap tracked in `BACKLOG.md`, not intended behaviour.

### `ToString` instances

`to_string` is defined for the following types:

| Type | Result |
|---|---|
| `Int` | decimal string, e.g. `"42"` |
| `Double` | decimal string at the **shortest precision that reads back bit-identically** — the first of `%.15g` / `%.16g` / `%.17g` whose text parses back to the same `Double`. 17 significant digits always suffice for an IEEE-754 double, so the rendering is never lossy; starting at 15 keeps tidy values tidy. A `.0` is appended when the value is integral so it never reads as an `Int`. E.g. `"3.14"`, `"1.0"`, `"0.1"`, and `to_string(0.1 + 0.2) == "0.30000000000000004"` |
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

**Function types have no instances.**  No class instance is provided — or
representable — for a function type `a -> b`: instance heads dispatch on a type
*constructor*, and there is no `instance C (a -> b)` form.  Requiring `C` on a
function value is therefore unsatisfiable and is rejected at the call site with
`No instance of C for a function type`.  This covers both a direct class-method
call on a function (`to_string(f)`) and a constrained regular function whose
argument forces a function-typed head — e.g. `describe(describe)` where
`describe : a -> String where ToString a` demands `ToString (b -> String)`.  The
rejection is a type error, consistent with the constraint well-formedness rules
above: it prevents the obligation from being silently dropped (a codegen
under-application) or resolved to a wrong default dictionary (a silent
miscompile that reads a closure through an unrelated instance).

**An instance head must be a type constructor applied to distinct type
variables.**  The head of an `instance` declaration — the type immediately after
the class name — must be headed by a type *constructor* (`Int`, `List a`,
`Result e a`, a tuple), and each argument of that constructor must be a type
variable, with no variable repeated.  These are all rejected at the instance
declaration:

| head | rejected because |
|---|---|
| `instance C a` | a bare type variable |
| `instance C (a b)` | an applied variable head |
| `instance C (a !{IO})` | an effect annotation is dropped, leaving a variable head |
| `instance C (a -> b)` | a function type has no constructor head |
| `instance C (List Int)` | a concrete type argument |
| `instance C (Pair a a)` | the same variable used twice |
| `instance C (List (Maybe a))` | a nested type argument |

```
Instance head for C must be a concrete type, not the type variable `a`
Instance head for C must be a concrete type, not a function type
Instance head for C must be a type constructor applied to distinct type variables, but `Int` is a concrete type
Instance head for C must be a type constructor applied to distinct type variables, but `a` appears more than once
Instance head for C must be a type constructor applied to distinct type variables, but one argument is not a type variable
```

A tuple head (`instance C (a, b)`) is *accepted*: tuples have a constructor head
(`Tuple2`) applied to distinct variables — the prelude's `Eq`/`ToString` tuple
instances rely on this.  `instance C (a, a)` is not.

Both halves of the rule exist for one reason: **Sprout selects an instance by the
head constructor of the dispatch type alone, discarding its arguments.**  A
variable head names no dispatchable type, so it registers an instance no call site
can select — before this rule, `instance C a` was accepted and every use failed
with `No instance of C for T`, blaming the caller for a defect in the instance.  A
concrete argument is the mirror image, and worse: `instance Describe (List Int)`
registers `Describe`-at-`List`, which equally answers for `List String`, so a
`List String` reaches the `List Int` body and its `Int` arithmetic runs on `String`
payloads — accepted by the checker with no diagnostic at any phase.  Restricting
the head to what the key can represent is what makes selection-by-head-constructor
sound.

To write an instance at one specific argument type, give that instantiation a type
of its own, so it has a head constructor of its own:

```
type IntVec =
  | MkIntVec (Vec Int)

instance Summable IntVec
  …
```

**Two instances may not share a head constructor.**  Instance selection keys on
the head constructor, so `instance C (List a)` and `instance C (List b)` both name
`C`-at-`List` and the second would silently shadow the first.  Sprout has no
overlapping-instance resolution and no instance-specificity ordering, so this is
always an error:

```
Overlapping instances for C
```

Together these rules are the Haskell 2010 Report §4.3.2 restriction that an
instance head be "a type constructor `T` applied to simple type variables … [which]
must all be distinct".  Because a legal head is a constructor applied to distinct
variables, two instances sharing a head constructor are alpha-equivalent, so the
overlap rule above is the Report's own prohibition on duplicate instances rather
than an extra Sprout restriction.

Admitting concrete type arguments (`instance C (List Int)`) is GHC's
`FlexibleInstances` extension, which pairs the relaxation with *full-head
matching* — selecting an instance by unifying the whole head rather than its
constructor.  The two arrive together by necessity; Sprout has the
head-constructor key only, and so takes the Haskell 2010 position.  Lifting the
restriction requires widening the key first (see `BACKLOG.md`).

### `Applicative` class and `mapN` helpers

```
class Applicative f where Functor f
  fn pure(x: a) -> f a
  fn map2(g: a -> b -> c, xs: f a, ys: f b) -> f c
```

`Applicative` lifts an n-ary function over values in a context `f`.  Its primitive
is `map2` (apply a two-argument function to two wrapped values), **not** the
classic `ap`/`<*>` (`f (a -> b) -> f a -> f b`).  `ap` feeds one wrapped argument
at a time, which requires curried partial application; Sprout is n-ary with
explicit `_`-placeholder partials (§5.3), so the `map2` presentation is the
idiomatic one.  `map2` composes with fully-saturated calls only.

`pure` lifts a plain value.  Its class variable `f` appears only in the return
type, so — exactly like `Enum.from_ordinal` (§8.6) — instance selection requires
the target type to be determinable at the call site (an annotation, a typed
return, or unifying context).  A fully polymorphic `pure(x)` cannot be dispatched.

| Type | `pure` | `map2` semantics |
|---|---|---|
| `Maybe` | `Just(x)` | fail-fast — any `Nothing` collapses the result |
| `Result e` | `Ok(x)` | fail-fast — the first `Err` short-circuits (left-biased) |
| `List` | `[x]` (singleton) | cartesian product — for each `x` in `xs`, combine with every `y` in `ys` (the list-monad-consistent instance) |

Error-*accumulating* semantics (collect all failures rather than short-circuit)
need a distinct `Validation` type, since a type admits only one `Applicative`
instance; it is deferred (tracked in `BACKLOG.md`).  The pairwise "zip" behaviour
for `List` is likewise a separate future `ZipList` newtype.

`map3`, `map4`, and `map5` are free functions (`where Applicative f`) that lift 3-,
4-, and 5-argument functions:

```
map3(add3, Just(1), Just(2), Just(3))            # Just(6)
map4(mk4, Ok(1), Err("x"), Ok(3), Ok(4))         # Err("x")
```

They derive from `map2` by threading arguments through the context as tuples and
destructuring them in a final combiner (no currying is used or required).

### `Monad` class and generic `and_then`

```
class Monad m where Applicative m
  fn flat_map(f: a -> m b, xs: m a) -> m b

and_then(f: a -> m b, xs: m a) -> m b where Monad m   # = flat_map(f, xs)
```

`Monad` completes the `Functor → Applicative → Monad` tower.  Its sole method
`flat_map` is monadic bind: thread the payload of `xs : m a` into a continuation
`f : a -> m b`, short-circuiting on the empty/error case.  `pure` is inherited
from the `Applicative` superclass, so `Monad` adds only bind.  Dispatch keys on
`xs` (an ordinary argument), so — unlike `pure` — a `flat_map`/`and_then` call
needs no return-type annotation.

The exported combinator is the free function **`and_then`**, delegating to
`flat_map`; the method-vs-free-fn split mirrors `Functor.fmap`/`map` and
`Foldable.fold_values`/`fold`, keeping the class-method name out of the global
namespace.

| Type | `flat_map` semantics |
|---|---|
| `Maybe` | `Nothing` short-circuits; `Just x` feeds `x` onward |
| `Result e` | left-biased — the first `Err` short-circuits; `Ok x` feeds `x` onward |
| `List` | flatten-map — `f` is applied to every element and the results concatenated (consistent with the cartesian-product `Applicative`) |

The three Monad laws (left identity `and_then(f, pure(x)) == f(x)`, right identity
`and_then(pure, m) == m`, associativity) hold for all three instances and are
checked in `tests/stdlib/test_typeclass_laws.spr`.

`do`/`<-` already performs the same bind for `Maybe`/`Result` structurally in the
desugarer; the `Monad` class does not currently wire into `do` (a monad-generic
`do` and a built-in `?` propagation form are future work, see
`docs/let-else-and-monadic-binding-plan.md`).

An `Alternative` class (a generic `<|>`/`or_else`) is **not** provided: the only
lawful `List` instance duplicates `Semigroup (List a)`'s `++`, so with `List`
excluded the class would have a single `Maybe` instance — ceremony with no
dispatch payoff.  The `Maybe` fallback is therefore a free function.

### `Maybe`/`Result` combinator free functions

Beyond the generic `map` (`Functor`) and `and_then` (`Monad`), the prelude
provides per-type combinators covering the axes no class does:

| Function | Meaning |
|---|---|
| `maybe_with_default(fallback, m)` | `m`'s value, or `fallback` when `Nothing` |
| `maybe_or_else(primary, fallback)` | `primary` when `Just`, else `fallback` (left-biased choice) |
| `result_map`, `result_map_error`, `result_and_then`, `result_with_default`, `result_pipe*` | the `Result` family (see prelude) |

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
| `Enum` | **nullary-only ADTs** | `ordinal(v)` — `match v with` mapping each constructor to its 0-based declaration index; `from_ordinal(n)` — `if n == 0 then Just(Ctor0) else ... else Nothing`, the total-with-`Nothing` inverse |

`Enum` is restricted to types whose every constructor is nullary: `from_ordinal`
reconstructs a constructor from an integer, which is undefined for a variant that
carries fields.  Deriving `Enum` on a type with any field-bearing constructor is
an eager error at the deriving site (see Error conditions).  The ordinal is the
constructor's 0-based declaration-order index; reordering constructors renumbers
them, which is a breaking change for any persisted ordinal.

`from_ordinal : Int -> Maybe a` carries its class variable `a` only in the return
type.  Instance selection therefore requires the target type to be concrete at
the call site — through a type annotation, a typed function return, or unifying
usage context.  A fully polymorphic call (no determinable return type) cannot be
dispatched and is rejected at compile time.  In practice `from_ordinal` is
consumed through a concrete-typed wrapper (e.g. `fn tile_of(n) -> Maybe Tile =
from_ordinal(n)`), which satisfies this requirement.

For parametric types (e.g. `type Box a = | Hold a`), the synthesized instance
carries one instance constraint per type parameter, e.g. `instance Eq (Box a)
where Eq a { ... }`.  This is conservative — phantom type parameters get a
constraint they don't need; refining this is a future improvement.

**Records** support `deriving (Eq, Ord, ToString)`.  The clause is **trailing**,
after the field list (a record's `= (fields)` right-hand side is a self-contained
parenthesised expression, unlike an ADT where `deriving` precedes `=`):

```sprout
type Point = (x: Int, y: Int) deriving (Eq, Ord, ToString)
type Box a = (val: a, tag: String) deriving (Eq, Ord, ToString)
```

A record is a single product (no sum, no tag), so the synthesized bodies are the
ADT same-constructor logic accessing fields by name:

- `Eq` — conjunction of per-field `eq` in declaration order (`true` for a
  field-less record).
- `Ord` — lexicographic per-field `compare` in declaration order (`0` for a
  field-less record).
- `ToString` — `"Name(f0 = v0, f1 = v1, ...)"`, mirroring record construction
  syntax so the output is (modulo unquoted `String` fields) valid source.

> **Unsettled:** `Name` is the *declaring module's qualified* name when the type
> is imported (`app.models.Point(x = 1, y = 2)`) and the bare name when it is
> declared in the entry file. Both forms are valid source, so both satisfy the
> round-trip goal above, but the split is an artifact of deriving expansion
> running after name qualification rather than a decision — and it means moving a
> declaration into a module changes program output. Which convention wins is
> tracked in `BACKLOG.md`; until it is settled, do not depend on the prefix.
> The same split applies to ADT constructor names.

`Enum` cannot be derived for a record — it requires a nullary-constructor ADT
(the ordinal↔constructor bijection), and a record is a single field-bearing
product.  `deriving (Enum)` on a record is an eager error at the deriving site.
Parametric records gain the same per-type-parameter constraints as parametric
ADTs (`type Box a = (val: a, ...) deriving (Eq)` → `instance Eq (Box a) where Eq
a`).

Serialization (`Serialize`/`Deserialize`) and hashing (`Hash`) are intentionally
**not** in v1.  Both require design decisions the language hasn't made yet —
serialization needs a format-agnostic visitor abstraction (serde-style) rather
than baking S-expressions into a class name, and `Hash` waits on polymorphic-keyed
dicts.  Both are tracked in `BACKLOG.md`.

### Limitations (this version)

- Multiple `deriving (...)` clauses on the same type declaration are not
  supported (use one clause with all classes: `deriving (Eq, Ord, ToString)`).
- Records support `deriving (Eq, Ord, ToString)` but **not** `Enum` (a record is
  a single product, not an enumeration — see Records below).

### Error conditions

- Unknown class in deriving clause: eager error at the deriving site:
  `unknown class in deriving clause for 'Foo': 'Bar' (v1 supports Eq, Ord,
  ToString, Enum)`.
- `deriving (Enum)` on a type with a field-bearing constructor: eager error at
  the deriving site: `cannot derive 'Enum' for 'Foo': constructor 'Bar' has
  fields; Enum requires all constructors to be nullary`.
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

`main` is the conventional program entrypoint in v0. An executable entrypoint must
be a zero-argument `fn main() -> Unit !{IO}` or `fn main() -> Int !{IO}`, after
module qualification is resolved. Pure `main` definitions and effect-polymorphic
`main` definitions are rejected at the executable boundary. Helper functions
may still use shapes such as `Maybe a !{IO}` or `Result e a !{IO}` and be
handled explicitly from `main`.

These rules are enforced at type-check for any **defined** `main` (a function
named `main`, or the entry module's qualified `<mod>.main`), as a final
well-formedness gate that runs only once the body itself typechecks — so a
broken `main` body reports its own error first. The diagnostics are:
`Executable entrypoint \`main\` must take zero arguments`,
`… must return Unit or Int`, `… must declare the {IO} effect` (a pure `main`),
and `… must not be effect-polymorphic`. A **missing** `main` is not yet
diagnosed: at type-check the compiler cannot distinguish a library check from an
executable build (a library module legitimately defines no `main`), so requiring
one awaits an explicit executable-vs-library compile mode.

A `Unit`-returning `main` always exits the process with code `0`. An
`Int`-returning `main`'s return value becomes the process exit code
(truncated to the platform's native exit-code width, mirroring C's `int
main(void)` convention):

```sprout
fn main() -> Int !{IO} =
  do
    ok <- run_checks()
    if ok then 0 else 1
```

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

### 10.13 Using the math modules

Integer math comes from `stdlib.math.int`:

```sprout
import stdlib.math.int as imath

fn wrap_index(idx: Int, size: Int) -> Maybe Int =
  imath.mod(idx, size)
```

(The function is named `wrap_index`, not `wrap`: `wrap` is a reserved keyword — see §5.)

`Double` math comes from `stdlib.math`, which uses the same plain names for the
`Double` versions. A module needing both imports both under distinct aliases:

```sprout
import stdlib.math as math
import stdlib.math.int as imath

# Stefan-Boltzmann radiant emittance, j = sigma * T^4. `pow` with an integer
# exponent multiplies rather than going through exp/ln, so it avoids that path's
# truncation error (it is exact when every intermediate product is exactly
# representable, and within about an ulp otherwise).
fn emittance(kelvin: Double) -> Double =
  0.00000005670374419 * math.pow(kelvin, 4.0)

fn even_bucket(idx: Int) -> Bool =
  imath.is_even(idx)
```
