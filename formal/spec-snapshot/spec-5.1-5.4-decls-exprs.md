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

Constructors count. A sum constructor, a `wrap`'s constructor and a record's
constructor (named after its type, §5.6.4) share the one namespace, so a name
declared by two of them, or twice by one, is rejected at the second declaration:

```
`Circle` is defined more than once in this module: a constructor of `Shape` and a record constructor
```

A top-level `fn` or `let` named like a constructor of the same module is the same
clash and is rejected the same way (`Sq` is defined more than once in this module:
a constructor of `Shape` and a function).

Rename one side. A sum *type's* own name is not a constructor, so it may match one
(§5.6).

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
- Multiple bindings are allowed and are evaluated in source order, one per line
  (§2.1): `where a = 1 b = 2` is a parse error.
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
At top level, `let` initializers must be pure, and this is enforced: `let boom = print("x")`
is rejected with ``top-level `let boom` performs IO when its module loads, but top-level
bindings must be pure``. Binding a function that performs IO is pure — the effect stays on
its arrow and fires when it is called (`let shout = \s -> print(s)`).

**Type annotation.** A top-level `let` binding may carry an optional type annotation
between the name and `=`:

```sprout
let answer : Int = 42
```

The initializer is checked against the written type: its inferred type must
unify with the annotation, or it is a compile error
(`type annotation mismatch for \`<name>\`: …`).
It resolves the same type names a `fn` signature in that module does.
The annotation is applied before
generalization, so it narrows the binding's type — and, like a function
parameter/return annotation, it drives the same expected-type-directed literal
lowering, so `let xs : Vec Int = [1, 2, 3]` yields a `Vec` (§5.5.1) at the
binding site. Free lowercase names in the annotation are ordinary type variables
(`let id_pair : (a, a) = …`).

**The annotation is evidence, not just a check.** When the initializer is a
function literal, its parameters take their types from the annotation *before*
the body is inferred — the same direction §5.1 makes normative for `where`
bindings, and for the same reason: information flows from the declaration into
the body, never backwards. This is observable wherever the body needs a type to
be known rather than merely consistent, typeclass dispatch being the case that
motivated it (§8): in `let labeller : Int -> String = label`, the annotation is
what makes the instance `ToString Int`.

A function's **declared return type** is evidence in the same way and for the
same reason.  Where the body is a function literal, its parameters take their
types from the declared return type before the body is inferred, so in
`fn pick() -> Int -> String = label` the return type is what makes the instance
`ToString Int`.  These two — an annotated `let` and a declared return type — are
the positions that carry a written type; elsewhere a function literal is typed
against the parameter it is passed to.

**A position that fixes nothing still determines the instance.** Expected-type
propagation is how a function *literal* learns its parameter types, and it
reaches only the positions above. Instance selection for a `where`-constrained
function used as a **value** does not depend on it: the dictionary is chosen
after unification, by locating the constraint's own variable in the callee's
declared type walked alongside the type the call instantiated it to. Whatever
the value was sitting in when it was written is irrelevant, so a constructor
payload, a list or tuple element, a record field of polymorphic type, and a
local `let … in` all dispatch to the instance the eventual use requires. The
choice is per call, so the same constrained function reached through the same
position from two callers yields two different dictionaries:

```sprout
fn label(x: a) -> String where ToString a = to_string(x)

fn describe(n: Int) -> String =
  match Just(label) with          # `Just`'s payload type is `a`; it fixes nothing
  | Just(f) -> f(n)               # ... and this still selects `ToString Int`
  | Nothing -> "?"
```

**An undetermined dictionary in a top-level binding is an error.** A top-level
`let` is a single value, so a typeclass dictionary its initializer needs is
chosen once, at the binding, and every use gets that choice — unlike a `fn`,
which receives its dictionary per call. If the binding's type still quantifies
over a type variable that a dictionary depends on, nothing determines the
choice and the binding is rejected (`ambiguous typeclass binding: …`) rather
than resolved by guesswork. Adding an annotation that fixes the variable is the
remedy. A dictionary the binding's type does not quantify over is unaffected:
`let empty = list_map(label, [])` has type `List String`, quantifies nothing,
and its never-invoked dictionary stays inert.

This version annotates **top-level** `let` bindings only; annotations on
`let … in` (§5.2.1), function-local `where` bindings, and do-block `let` steps
are not yet part of the language. See `docs/binding-annotations-v0.md`.

### 5.2.1 `let … in` binding block

In pure expression position, a `let … in` block introduces one or more local
bindings before a body expression. Bindings are layout-aligned under `let` (§2.1);
`in` — on the same line, or starting any later line — closes the block:

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
  is disambiguated on the `->` after the else. (Propagation without an `else` is
  `try`, §5.9.1.)
- Every `else` and the body must unify to the block's result type; a mismatch is
  reported at the `else` value and names both types, since either side may be the
  wrong one. At least one binding is required.
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
`let` statement per binding, and the split between bindings uses the same layout
rule the expression form uses (§2.1), so a right-hand side may span lines.

Each binding may be any binding `let … in` accepts: a pattern, with or without an
`else` (§5.2.2). A binding that fails ends the block with its `else` value, so the
bindings below it and the rest of the block do not run:

```sprout
do
  let base = 100
      Just a = lookup(k) else base     # the block's value; sees `base`
      (b, c) = split(a)
  a + b + c
```

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

**One step per line.** A `do` step must consume the whole of its line (and any
continuation lines indented under it). Trailing tokens are an error — they used
to be discarded in silence, so a second statement written on the same line as
the first simply never ran.

**Where a step ends.** By the layout rule (§2.1). A step runs up to the next line
that both starts at the block's indentation column and begins with a token that
can head an expression: an identifier, a literal of any kind, an opening
`(`/`[`/`{`, a `\` lambda, a prefix `-` or `!`, a backtick template, or one of
`let`/`if`/`match`/`do`/`true`/`false`. A line indented further — or one starting
at the block column with a token that cannot head an expression, such as an
operator or a `|` match arm — continues the step above it, as does any line after
a token that cannot end one (`->`, `=`, an operator). A step also ends at a token
that closes an enclosing bracket, so a `do` block may be a call argument.

**The last step is the block's value**, so it must be an expression. A `<-` or `let`
there, with any pattern and with or without an `else` (§5.2.2), binds names nothing
can read and leaves the block with no value; it is an error.

### 5.2.2 Refutable binds in `do` blocks

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
A refutable step with **no following step** has no success continuation; it is the
last-step error of §5.2.1a. RHS effect handling is inherited from `<-` and is
unchanged: inference applies the rewrite before typing the step
(`docs/effectful-let-else-v0.md`). Propagation without an
`else` is `try` (§5.9.1).

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
- Single-parameter shorthand: `\x -> expression`. An annotation requires the
  parenthesized form, `\(x: T) -> expression`, because the annotation's own type
  may contain `->` and `\x: A -> B -> e` has no readable parse without them.
- The parenthesized parameter list must contain at least one parameter.
- **A parameter annotation is unified with the type the parameter already has** —
  the callee's parameter slot in argument position, an unconstrained variable
  anywhere else. A disagreement is a **compile error** (`type annotation mismatch
  for 'x'`). Outside argument position, where nothing else determines the
  parameter, the annotation is what fixes it, so a `let`- or `where`-bound lambda
  can be given a type its body alone would leave ambiguous.

  Because the annotation is believed, a conflicting *use* is what gets reported
  outside argument position: the lambda is inferred first and the call is then
  checked against the signature written. In argument position the slot is known
  before the body is inferred, so the mismatch is reported at the annotation.
- **A type variable in a lambda parameter annotation is rejected in v0**
  (`type variable 'a' is not allowed in a lambda parameter annotation`). Sprout has
  no lexically scoped type variables, so the `a` in `\(x: a) -> …` could not mean
  the enclosing signature's `a`; it would be a separate variable that merely
  happens to unify. Rejecting keeps the spelling free for scoped type variables to
  later give it the meaning readers already assume. See
  `docs/lambda-parameter-annotations-v0.md`.
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

  A lambda in any other position (a `let` initializer, a `where` binding, a record
  field) has no such slot. Its parameters are then ordinary inference variables,
  solved by everything that constrains them — the body **and any use of the
  binding**:

  ```sprout
  # `s` is fixed by the USE, not the body: `${s}` alone leaves `ToString s`
  # ambiguous, and this resolves only because `f` is applied to a String.
  let f = \s -> `<${s}>`
  in f("hi")
  ```

  A top-level `let` is the case with no use in scope to help: there the binding
  generalizes on its own, so a body whose class constraint nothing resolves is
  rejected as ambiguous.
- Function application is **n-ary**: a call saturates its callee. **Under-application
  of a known function** — supplying fewer arguments than its declared arity — is a
  **compile error** (`'f' expects N arguments, got M`); use a `_`-placeholder partial
  (above) to partially apply. A function whose declared return type is itself a
  function (e.g. `-> Int -> Int`) is not under-application: it is saturated at its own
  parameter count and returns that function.
- A **data constructor is a function value when named without parentheses**, and
  under-applying one is the same compile error as under-applying a function. A
  constructor `C` of arity N>0 mentioned in value position denotes exactly
  `C(_, …, _)` — the N-parameter lambda that constructs — so `list_map(Just, xs)`
  is legal and means `list_map(\x -> Just(x), xs)`. This holds for every
  constructor form: an ADT constructor, a `wrap`'s constructor, and an
  existential one (whose hidden witnesses are resolved at the implied call, as at
  a written one). A **nullary** constructor is a plain value, not a function:
  `Nothing` is a `Maybe a`, never `\-> Nothing`. A record type name is not a
  constructor and is not bound as a value at all (§5.6). One restriction: a
  constructor with a **linear** field cannot be used as a function value in either
  spelling, because the implied lambda would take a linear parameter and
  higher-order linearity is deferred (§5.8); construct it with a written call.
  A **parameter** may shadow a constructor's name — a parameter is not a pattern, and
  a capitalized name is a binder there rather than a constructor pattern — and inside
  that scope the name denotes the local. The shadowing is total, which matters for
  `Cons` and `Nil` specifically: a list literal desugars to those constructors, so a
  parameter named `Cons` makes every `[…]` literal in its scope ill-typed. Shadowing a
  constructor is legal but rarely what you want; the desugared forms are why.
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

Both branches must type-check to the same type. That type is a common one neither
branch owns — as for a `match` result (§5.5) — so writing the branches in the other
order cannot change whether the expression type-checks. This is what makes the effect
rule of §7 rule 8 reach a join.

