## 8.5 Standard Prelude Typeclass Instances

The following typeclass instances are provided by `stdlib/prelude.sprout` and are
automatically available without an explicit import.

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

The layout rule is the one `do`, `let … in` and `match` use (§2.1).  The
first member fixes the **block column**, and the body ends at a line at or left of
the declaration's own column (its `export`, if it has one).  A member
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

**Instance method signatures.**  An instance defines only methods its class
declares, and each one's signature is the class's with the class variable
replaced by the instance head: the same number of parameters, and each
parameter and the return type matching.  A call dispatches through the class
signature, so a method that took other types would run on values it does not
expect.  The head's variables keep the head's spelling, because an instance's
`where` reaches its methods by those names: for `instance Same (Pair a b)`, a
method taking `Pair b a` is rejected.  The method's own variables may be renamed,
and a method may be more general than its class (a variable where the class
has `Int`), as long as each method-level `where` below still constrains what the
class's does.  An arrow inside a parameter or the return type is judged by
§7's arrow rule in the direction values cross: a caller's argument flows from
the class's parameter into the method's, so a method may not declare pure a
callback the class passes as `!{IO}`; the method's result flows out to the
class's, so it may not return an `!{IO}` function where the class promises a
pure one.  An arrow whose effect is a variable, on either side, passes this
check however the variable is spelled and wherever else it appears: rule 8's
variable exemption, judged one arrow at a time.  An unannotated parameter or
return type takes the class's type at the head, and the body is checked against
it.  A class variable takes what the written slots put in its place, aliases
expanded: with the class's `g: a -> b, x: a`, a method writing `g: q -> r` and
leaving `x` bare gets `x: q`.  The renaming is simultaneous, so a method
writing `g: b -> a` gets `x: b`.  A method variable written over the class's
structure (`r` for `List a`) fixes nothing under it, and the rest of that slot
still fixes what it matches.  When written types put two different types in one
class variable's place, that variable is unsettled, and a bare slot that needs
it is an error: annotate it.  One no written slot fixes is renamed apart when
the head or a written type spells it, and otherwise keeps the class's name, so
a restated `where` reaches it.  A method-level `where` that names a class
variable the method writes under one or more other names is an error: with
`g: q -> String` and `z` bare, `z` is `q` too, so write `where ToString q`, not
`where ToString a`.  A method whose written types
do not match the class is reported as written, with `_` for each bare slot.  A
seeded arrow takes the method's effect variable when it writes one, since rule 9
allows one per signature.  The method's own effect (rule 8) and ownership modes
have their own rules.

**The dispatch type.**  A class-method call selects its instance by what the class
variable binds to at the call, wherever the variable sits in the method's
parameters — not by the head of the argument that holds it.  Given `fn total(xs:
List t) -> t`, the call `total([1, 2])` dispatches on `Int`, and `fn mk(f: Int ->
b) -> String` called with `\x -> x + 1` dispatches on `Int` too.  The first
parameter whose binding has a concrete head decides.  When none does, the first
binding is a type variable and the caller's constraint on that variable is
forwarded, so `where Summ a, Summ b` keeps `total(xs: List a)` and `total(ys:
List b)` apart.  A class variable that occurs only in the return type is dispatched
by the expected result type.

**Method-level constraints.**  A class method may end with its own `where`
clause, constraining a type variable the *method* quantifies rather than the
class variable:

```sprout
class Traversable t
  fn traverse_values(g: a -> f b, xs: t a) -> f (t b) where Applicative f
```

`t` selects the instance by dispatch; `f` is chosen per call site and its
dictionary is passed as a hidden argument, exactly as for a constrained `fn`.
The clause is positioned after the return type and any effect annotation.

An implementing method **restates** the constraint, over its own variable names,
as it already restates parameters and return type:

```sprout
instance Traversable Maybe
  fn traverse_values(g: a -> f b, xs: Maybe a) -> f (Maybe b) where Applicative f =
    …
```

On an instance member the clause sits before the `=`, which is what separates it
from the value-binding `where` that may follow the body.

The restatement must name **the same classes in the same order** as the class
declaration, and each must constrain what the class's constraint in that
position does, under the method's renaming; only the variable names are free.
Each constraint is one positional dictionary slot, so naming a different class,
omitting or adding one, listing the same classes in another order, or putting a
slot on another variable (a method that swaps `a` and `b` but keeps `where
ToString a, ToString b`) would dispatch through the wrong slot — each is
rejected, naming the class and the position.  Rationale
and the hidden-argument layout: `docs/method-level-constraints-v0.md`.

**Constraint syntax.**  A `where` clause names the **class first, then the
constrained type**: `where ToString a`, `where Applicative f`, `where Eq a, Ord b`
(*A class takes one type parameter*, below, says what the type may be).  Two
well-formedness rules are enforced at check time, both
preventing a silently-dropped constraint from lowering to an undefined class-method
call that would otherwise only fail at link time:

- A variable-first ordering (`where a ToString`) is rejected — the head token is
  read as the class name, so `a` is diagnosed as "not a class", with a hint
  suggesting the corrected order.  A superclass written so gets the same hint.
- The constrained type variable must **appear in the function's signature**
  (a parameter annotation or the return type).  A constraint on a variable that
  does not — e.g. `where ToString x` where `x` is an *unannotated value
  parameter*, or `where Eq b` where only `a` is annotated — is rejected as
  ambiguous, since no call site could ever determine the type to dispatch on.

**A class takes one type parameter, and a constraint applies its class to one
type.**  An instance is selected by one dispatch type and a dictionary is keyed on
it alone, so Sprout has no multi-parameter classes.  Four rules are checked at the
declaration, before any use:

1. A class declares exactly one type parameter.
2. Every constraint applies its class to exactly one type, wherever it is written: a
   function's `where`, a class method's `where`, an instance method's `where`, an
   instance head, an instance's context, and a constructor's `where` (§5.6).  A
   superclass is held to more: its one type must be the class's own variable, bare.
3. An instance's context names only variables of its head.  A variable a method's
   signature writes that the head does not is the method's own, which a method-level
   `where` constrains.
4. An instance's context is smaller than its head.  For each context constraint
   `C' t'` under the head `C t`, `t'` is smaller than `t`, and no type variable occurs
   more often in `t'` than in `t`.  The size of a type counts a type name or variable
   as 1, an application as the sum of its parts, a tuple as 1 plus its items, an arrow
   or thunk as 1 plus its type parts, and an effect wrapper as its inner type;
   repetitions count (after alias expansion).  So a head with no type variables
   (`instance Foo Unit`) cannot have a context.  Each resolution step then replaces a
   constraint by strictly smaller ones, which guarantees instance resolution
   terminates.  Rationale: `docs/instance-termination-v0.md`.

| declaration | rejected because |
|---|---|
| `class Foo` | it declares no type parameter |
| `class Conv a b` | it declares two |
| `fn f(x: a, y: b) -> Bool where Eq a b` | `Eq` is applied to two types |
| `fn f(x: a) -> Bool where Eq` | `Eq` is applied to none |
| `instance Sh Int Bool`, `instance Sh` | the head applies `Sh` to two types, or none |
| `instance ToString (Box a) where Eq a a` | the context applies `Eq` to two types |
| `class Sh t where Eq u`, `where Eq Int`, `where Eq (List t)` | a superclass is not the class variable, bare |
| `class Sh t where Eq t t` | the superclass applies `Eq` to two types |
| `class Sh t where t Eq` | the superclass names its class second |
| `class Sh t where u Eq` | the superclass names its class second and constrains another variable |
| `instance Foo Bar where Baz k` | `k` is not in the head `Foo Bar` |
| `instance Mk Box where Eq b`, `b` a method's own variable | the context does not reach it |
| `instance Foo (List a) where Foo (List (List a))` | the context is larger than the head |
| `instance Foo (List a) where Foo (List a)` | the context is as large as the head: it resolves to itself |
| `instance Same (Box a) where Eq (Maybe a)` | the context is as large as the head; give the head a second variable |
| `instance Eq (T a b c) where Eq (Pair a a)` | the context names `a` twice, the head once |
| `instance Foo Unit where Bar Int` | the head has no type variables, so it takes no context |

```
class `Conv` declares 2 type parameters; a class takes exactly one
`Eq a b` applies `Eq` to 2 types; a constraint applies its class to exactly one type
the instance head `Sh Int Bool` applies `Sh` to 2 types; an instance head applies its class to exactly one type
the superclass `Eq u` of `Sh` must constrain the class variable `t`
the superclass `t Eq` of `Sh` names its class second; write the class first: `where Eq t`
the superclass `u Eq` of `Sh` names its class second and must constrain the class variable `t`: `where Eq t`
the instance context `Baz k` names `k`, which does not appear in the instance head `Foo Bar`
the instance context `Eq b` names `b`, which the instance head `Mk Box` does not bind; constrain a method's own variable with a method-level `where`
the instance context `Foo (List (List a))` is not smaller than the instance head `Foo (List a)`, so instance resolution might never end; constrain the head's variables instead, as in `where Foo a`
the instance context `Eq (Pair a a)` names `a` twice and the instance head `Eq (T a b c)` names it once, so instance resolution might never end
the instance `Foo Unit` has no type variables, so it cannot have a context
```

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
determines which `<Class>` instance to use`**.  Something the call can see must
pin the type down: a declared return type, a typed parameter the value passes
through, a typed top-level `let`, or a `where Codec a` clause so the caller
decides.  Sprout has no expression type ascription and no typed local `let`.

The rule is not scoped to `fn` bodies: a top-level `let` initializer is checked
the same way, so `let s = to_str(from_int(7))` is rejected identically.

A class method **used as a value** is checked too, at the mention: `apply_int(from_int, 1)`
into a parameter nothing constrains, `let f = from_int`, or `fn pick() = from_int` used.
Such a mention is accepted when use fixes its class variable, or when the enclosing
declaration forwards that variable's instance: a `where` constraint (or a superclass of
one), the context of the instance whose method it is in, or the dictionary a `match` arm
unpacks from an existential.  A `where` clause on another variable or another class does
not count.  A method whose own type never mentions the class variable (`fn tag(n: Int) ->
String`) has no use that fixes it: its mention is accepted only inside a declaration that
forwards exactly one constraint of its class, and is rejected as ambiguous otherwise.

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

**Constrained functions as values.**  A `where`-constrained function may be
passed to another function without being applied, and its type there is the one
its signature declares — the dictionaries it receives are not part of it:

```sprout
fn label(x: a) -> String where ToString a = to_string(x)
fn apply(f: Int -> String, n: Int) -> String = f(n)

apply(label, 3)     # `label` has type `Int -> String` here
```

The dictionary must be determined by something the compiler can read: a
receiving parameter whose declared type fixes it, as above; a `where` clause on
the enclosing function, which supplies one to forward; a type annotation on the
binding; the declared return type of the function the mention is returned from;
or, when none of those applies, how the value is eventually **used** — the
choice is made after unification, not at the mention, so a slot that fixes
nothing does not prevent it.  The mention may appear anywhere a value may —
including bound with `let`:

```sprout
fn describe(x: b) -> String where ToString b =
  let f = label                    # forwards `describe`'s own dictionary
  in f(x)

let labeller: Int -> String = label   # the annotation fixes `ToString Int`
```

A top-level `let` is **checked against its annotation**: the declared type is
known before the right-hand side is inferred, which is what lets it determine
the instance.  Without an annotation there is nothing to determine it, and
because a top-level binding is a single value — one slot holding one closure,
where a `fn` would take its dictionary as a parameter — the choice cannot be
deferred to each use.  Such a binding is rejected:

```sprout
let labeller = label
# ambiguous typeclass binding: `labeller` needs a ToString instance, but its
# type a -> String leaves that choice open ...
```

The rule is about **generalization**, not about the receiving slot.  A slot that
fixes nothing — a record field, a list or tuple element, or a constructor
payload such as `Just(label)` — is not itself a problem, because the eventual
use still determines the instance.  What cannot work is a declaration that
*generalizes* the variable the dictionary is for: each caller may then need a
different instance, and the declaration has only the one choice to give them
all.  That is what the top-level `let` above runs into, and it is why the same
mention inside a `fn` is fine — a `fn` takes its dictionary per call.

The rule is not confined to *mentions*.  **Applying** a constrained function
inside a function that generalizes the constrained variable is rejected on the
same grounds: with no `where` clause there is no dictionary parameter to forward,
so the instance would be chosen once at definition and every caller would receive
that choice.  Adding the clause is what makes it legal.  The diagnostic names the
type the instance is missing for, which need not be a bare variable: a constraint
on a partially applied constructor is reported and suggested as one, since `where
Applicative e` over an error type `e` would not compile.  A tuple or a fully applied
type, such as `(a, b)` or `List a`, is built from its variables' instances, so the
advice names a variable: `where ToString a`.  When the variable appears
in no parameter, a `where` clause has nothing to *attach* to until the signature
names the variable somewhere, so the diagnostic asks for a declared return type
carrying the constraint, or a concrete annotation:

```sprout
fn relabel(x: a) = label(x)
# `relabel` needs a ToString instance for type variable `a`, but declares no
# `where` constraint for it ... add `where ToString a` to its signature

fn relabel(x: a) where ToString a = label(x)   # accepted: the dictionary forwards

fn try_map(f: a -> Result e b, xs: List a) -> Result e (List b) = traverse(f, xs)
# `try_map` needs an Applicative instance for `Result e`, but declares no `where`
# constraint for it ... add `where Applicative (Result e)` to its signature

fn try_map(f: a -> Result e b, xs: List a) -> Result e (List b)
    where Applicative (Result e) = traverse(f, xs)   # accepted: names the constructor

fn pick() = label
# `pick` needs a ToString instance for a type variable it generalizes, and that
# variable appears in no parameter ... declare a return type naming the variable
# and constrain it with a `where` clause, or annotate with the concrete type

fn pick() -> a -> String where ToString a = label   # accepted
fn pick() -> Int -> String = label                  # accepted, fixed at Int
```

The first accepted form is dispatched **per call**: the constraint variable is
determined by how the result is used, so `pick()(5)` and `pick()(true)` select
different instances from one declaration.  This is resolved after the call's type
is known rather than while its arguments are being inferred, since there are no
arguments to read it from.

The variable must be one the declaration **generalizes**.  A free variable it does
not is unaffected: `list_map(label, [])` inside a monomorphic function leaves an
element type undetermined, but the enclosing signature is not polymorphic and the
dictionary is never invoked, so the dead dictionary stays inert rather than
becoming an error.  A bare class method is not such a case: its mention is the
instance's implementation, so there is nothing to leave inert and an unfixed
instance is an error (*Ambiguous class-method dispatch*, above).  Coverage is
judged per variable, so a variable a `where` clause constrains is exempt here
even when the body needs a *different* class of it; that case is rejected by
dispatch verification instead.

**An instance method is held to the same rule, with two remedies.**  A method
that generalizes a type variable of its own and needs a dictionary for it is
rejected, exactly as the equivalent `fn` would be.  The remedies: constrain the
variable in the **instance head**, or give the method its own method-level
`where` clause (§8.5 *Method-level constraints*).  The `where` clause must also
appear on the class declaration — an instance method restates a class-level
constraint, it does not introduce one:

```sprout
class Renderer f
  fn render(r: f, x: a) -> String

instance Renderer Box
  fn render(r: Box, x: a) -> String = label(x)
# `render` needs a ToString instance for type variable `a`, which this method
# generalizes and nothing supplies; add `where ToString a` to the method (the
# class declaration must declare it too), constrain the variable in the
# instance head, or give the method a concrete type

instance Renderer (Pair a) where ToString a          # accepted: forwarded
  fn render(r: Pair a) -> String = label(r.l)
```

A class method whose *own* signature needs a constraint separate from the class
variable is expressible with a method-level `where` clause — see §8.5
*Method-level constraints*.

**An instance's context is needed too.**  A class method on a type that wraps a
type variable needs the instance for the wrapper *and* whatever that instance's
context needs at the variable.  `y == Just(x)` with `y: Maybe a` uses
`instance Eq (Maybe a) where Eq a`, so it needs `Eq a`, and the enclosing
declaration must supply it: a `where` clause naming it (or a class that has it as
a superclass, such as `Ord a`), the instance head, or a method-level `where`.  A
constraint of a different class on the same variable does not supply it.
Without one, the call is rejected where it is written:

```sprout
fn same(y: Maybe a, x: a) -> Bool = y == Just(x)
# the `Eq` instance for `Maybe a` needs `Eq a`, which nothing in scope supplies
# — add `where Eq a`

fn same(y: Maybe a, x: a) -> Bool where Eq a = y == Just(x)   # accepted
```

This holds however the variable reaches the call — through a lambda parameter
applied later, through a function whose own constraint is compound (`where
Eq (Maybe t)`), or through a method used as a value (`let e = eq`) — and even
when the inner comparison can never run, as in `m == Nothing`: the instance is
chosen when the program is checked, not when the comparison runs.  A callee
constraint nested past one constructor or over a tuple (`where ToString (Box (List
b))`, `where ToString (b, Int)`) is built the same way.

A forwarded compound constraint arrives whole: `where Eq (Maybe a)` supplies
`Eq (Maybe a)`, context included — and only that.  It does not supply
`Eq (Maybe b)`, `Eq (Maybe Int)`, or `Eq (Maybe t)` for an existential `t`
unpacked in the body; those take the instance and need their own context.  A
type alias in the `where` is compared expanded, so `where Eq (List (Opt a))` with
`type alias Opt a = Maybe a` supplies `Eq (List (Maybe a))`.  A superclass may
bring a second constraint of one class at the same head (`where Ord (Maybe a), Eq
(Maybe b)`): each has its own dictionary. Two such constraints written directly are
rejected ("Two constraints of one class", below).

In an instance method the context comes from the instance head, under the head's
own variable names, and reaches only the variables the head binds; a variable of
the method's own needs a method-level `where`.  A method that writes `Box2 t u` for the
head's `Box2 a b` has variables of its own, so `where Eq (Maybe a)` on the head does
not reach `Maybe t`.  An unannotated parameter — or a
type variable inside one's inferred type — has no variable to constrain until it
is annotated, and a context at an applied variable (`Eq (f a)`) cannot be supplied
by a `where` clause today; each is rejected, and the message says what to do
instead.  As above, a variable the declaration does not generalize is unaffected.

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
| `Maybe a` | `"Nothing"` or `"Just(s)"` — requires `ToString a` |
| `Char` | the character as a one-character `String` |
| `Result e a` | `"Ok(s)"` or `"Err(s)"` — requires `ToString e`, `ToString a` |
| `Vec a` | `"[s_0, s_1, …]"` — requires `ToString a` |
| `Dict v` | `"{k_0: s_0, k_1: s_1, …}"` — requires `ToString v` |
| `Set` | `"{e_0, e_1, …}"` over the set's `String` elements, unquoted |
| `IntRange` | `"IntRange(start, end, step)"` |

Tuple instances format nested tuples recursively.  A 6-tuple or larger has no
`ToString` instance in the current prelude; adding one requires an explicit
instance declaration.  `Unit` has none either.

`print : a -> Unit !{IO} where linear a, ToString a` is an ordinary prelude function: it
writes `to_string(x)` and a newline.  A user type prints only through a `ToString` instance,
written or `deriving (ToString)` (§8.6).  A function value, `Unit`, and a 6-tuple or larger
have no instance, so printing one is a compile error.  The missing-instance error for a
program-declared type with no function-typed field names the fix.  In a headerless file it
reads `No instance of ToString for Answers in function main`, with the hint
``add `deriving (ToString)` to `Answers` ``; under `module main` the type is qualified
(`main.Answers`) and the function still reads `main`.

**A constructor renders under its source-form name, never a module-qualified
one.**  A ctor declared in `module main` renders `Apple(3)`, not `main.Apple(3)`,
and this holds for every module — a named one, a headerless entry file (§3.1), and
the prelude alike.  The one surface that renders a constructor is the `ToString`
instance, written or derived (§8.6); `print` writes its text.  Nesting is uniform, so an
inner value renders bare too: `Crate(Apple(1))`.

The consequence to be aware of is that two modules' same-named constructors
render identically.  That is deliberate — the qualified name exists to
disambiguate *symbols*, and a value's rendering is not one.  **Diagnostics are the
opposite case and keep the qualification**, because there the module prefix
answers a question the reader has: a type error says `Return type mismatch in
demo.f: Type mismatch: demo.Fruit vs Int`.

**Function types have no instances.**  No class instance is provided — or
representable — for a function type `a -> b`: instance heads dispatch on a type
*constructor*, and there is no `instance C (a -> b)` form.  Requiring `C` on a
function value is therefore unsatisfiable and is rejected at the call site with
`No instance of C for a function type`.  This covers both a direct class-method
call on a function (`to_string(f)`) and a constrained regular function whose
argument forces a function-typed head — e.g. `describe(describe)` where
`describe : a -> String where ToString a` demands `ToString (b -> String)`.  A
bare method mention whose class type is a function type is covered too, at
the mention.  A
function passed beside the value dispatched on — the callback in `app(x, \n -> n * 2)`
— demands nothing of the class and is not rejected.  The
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

**A class variable and every type it is applied to have a kind, and every constraint
and instance head must respect it.**  A kind is `Type` (a type that has values) or
`k1 -> k2` (a type constructor that takes a type of kind `k1` and yields one of kind
`k2`); `Maybe` is `Type -> Type`, `Result` is `Type -> Type -> Type`.  Kinds are
inferred, never written, and there are no kind variables in programs.  A class
variable used bare (`fn describe(x: t)`) has kind `Type`, so its instance heads must be
saturated; used applied (`fn label(xs: t Int)`) it has kind `Type -> Type`, so its heads
must leave one argument off.  With one class parameter (*A class takes one type
parameter*, above), the kind of that variable is the kind of the class.

Kinds are inferred over dependency groups of `type`, `record`, `wrap`, `type alias` and
`class` declarations (Haskell 2010 §4.6).  Each type parameter, alias parameter and class
variable starts as an unknown kind.  Every constructor field, `wrap` body and method
signature has kind `Type`; an existential constraint `C t`, a method-level `where C' u`
and a superclass `S t` each require the variable's kind to equal the class's.  The
constraints of one group are unified together, and only then is each kind still unknown
defaulted to `Type`; a later group reads the defaulted kind and cannot change it.  Two
consequences:

- A phantom parameter is `Type`.  After `wrap Tagged u = Int`, `Tagged Maybe` is a kind
  error, because `Tagged` takes a type.
- A class whose variable no method mentions has kind `Type`, so only saturated heads
  are instances of it.

An alias has a kind of its own: its parameters' kinds, then its body's, which need not be
`Type`, so `type alias E = Result String` has kind `Type -> Type` and `type alias AtInt f
= f Int` has kind `(Type -> Type) -> Type`.  An alias parameter its body never mentions is
a phantom: after `type alias Const a = Int`, `Const Maybe` is a kind error.  A written type
is kinded before alias expansion, so a use of an alias is checked against the alias's kind
and a message names the alias as written.  An alias that declares parameters must be given
all of them wherever it is written: in a signature, a `let` annotation, a lambda parameter
annotation and a `where` constraint (a zero-parameter alias is exempt, and may be applied
further).  An alias in a `where` constraint or an instance head is expanded before the kind
check, so a message there names the expansion.

Each `fn`, `extern fn`, `instance` and top-level `let` is then checked alone: its type
variables get fresh kinds from its signature, and each constraint `C t`, including one whose
`t` is a variable, requires kind(`t`) = kind(`C`).  The constraints checked are those of a
`fn`, a class method, an instance method, an instance context, a superclass, an existential
(`exists … where`, `any C`) and a derived instance; an instance head `C H` requires
kind(`H`) = kind(`C`).  The signature is read first, and a clash among its parameters and
return type is reported as the signature's own; then the `where`, which may fix the kind of
a variable the signature leaves open, is checked.  The built-in
kinds are `Type` for `Int`, `Double`, `Bool`, `String`, `Char`, `Unit`, `Bytes` and
`Builder`, `Type -> Type` for `Vector`, `Map` and `Ref`, and `Type` for the function,
thunk and tuple types and their arguments.

A `deriving` clause forwards the derived class over each type parameter a field stores
(§5.8), so a stored parameter of kind other than `Type` cannot be derived over (`type Wrap f a deriving
(Eq) = Wrap(f a)` would need `Eq f`); the declaration is rejected at the type.

Every type the program writes is judged.  A constructor field, a record field, a `wrap`
body, a class method signature, each parameter and the return type of a `fn`, `extern fn`
or instance method, a top-level `let` annotation and a lambda parameter annotation must
have kind `Type`, and a variable used at two kinds in one of them is an error, whatever
order the parameters come in.  An alias body must be well-kinded and need not have kind
`Type`.  Rationale and the compatibility census: `docs/kind-inference-v0.md`.

| declaration | rejected because |
|---|---|
| `class Boxed t` with `fn label(xs: t Int)`, then `instance Boxed (Tagged k v)` | the head has kind `Type`, the class wants `Type -> Type` |
| `class Plain t` with `fn describe(x: t)`, then `instance Plain (Tagged k)` | the head has kind `Type -> Type`, the class wants `Type` |
| `fn f(…) where Pretty Tagged`, `Pretty`'s variable used bare | `Tagged` has kind `Type -> Type -> Type` |
| `fn g(x: f a) -> Int where Foo f`, `Foo`'s variable used bare | `f` has kind `Type -> Type` |
| `class Tagless t` with no method mentioning `t`, then `instance Tagless Box` | `Tagless` has kind `Type` |
| `class Mixed t` with `fn one(x: t)` and `fn two(x: t Int)` | its own methods disagree |
| `class Wrapper t where Functor t` with `fn unwrap(x: t)` | it disagrees with its superclass |
| `wrap Tagged u = Int`, then `Foo (Tagged Maybe)` | `Tagged` takes a type, `Maybe` is a type constructor |
| `type T = T(Maybe)` | a field must have kind `Type`; `Maybe` takes a type |
| `fn g(…) where ToString (Box, Int)` | a tuple part must have kind `Type`; `Box` takes a type |
| `type Wrap f a deriving (Eq) = Wrap(f a)` | a derived instance constrains only parameters of kind `Type` |
| `fn f(x: Int Bool)` | `Int` takes no argument |
| `fn f(x: t Int, y: t) where Functor t` | `t` is used at two kinds |
| `type alias Const a = Int`, then `fn f(x: Const Maybe)` | `Const` takes a type, `Maybe` is a type constructor |
| `type alias Bad = Int Bool` | an alias body must be well-kinded |
| `type alias Pair a b = (a, b)`, then `let p: Pair Int = …` | an alias must be given all its arguments |

```
class `Boxed` takes a type of kind `Type -> Type` (a type constructor that takes one type), but the instance head `Tagged k v` has kind `Type`
class `Foo` takes a type of kind `Type`, but in the constraint `Foo f` the variable `f` has kind `Type -> Type` (a type constructor that takes one type)
class `Pretty` takes a type of kind `Type`, but in the constraint `Pretty Tagged` the type `Tagged` has kind `Type -> Type -> Type` (a type constructor that takes two types)
class `Mixed` uses its variable at kind `Type` in one place and `Type -> Type` (a type constructor that takes one type) in another; one class variable cannot be both
class `Wrapper` uses its variable at kind `Type`, but its superclass `Functor` takes `Type -> Type`
in the constraint `Foo (Tagged Maybe)` the type `Maybe` has kind `Type -> Type` (a type constructor that takes one type), but `Tagged` takes a type of kind `Type`
in constructor `T` the type `Maybe` has kind `Type -> Type` (a type constructor that takes one type), but it is used where a type of kind `Type` is needed
in the signature of `g` the type `f` is applied to `f`, so its kind would have to contain itself
cannot derive `Eq` for `Wrap`: its parameter `f` has kind `Type -> Type` (a type constructor that takes one type), and a derived instance can constrain only parameters of kind `Type`
in the signature of `f` the type `Int` has kind `Type` and takes no argument, but it is applied to `Bool`
in the signature of `f` the type `Maybe` has kind `Type -> Type` (a type constructor that takes one type), but `Const` takes a type of kind `Type`
in the alias `Bad` the type `Int` has kind `Type` and takes no argument, but it is applied to `Bool`
in the alias `A` the type `Box T` has kind `Type`, but the alias is used at kind `Type -> Type` (a type constructor that takes one type)
type alias `Pair` takes 2 type arguments, given 1
```

A constraint in a class method's `where` is reported with the prefix ``in method `m` of
class `C`:``, and one in an instance method's with ``in method `m`:``.  A clash in a
class method's signature is reported as ``in the signature of method `m` of class `C` …``,
in a `fn` or `extern fn` signature as ``in the signature of `f` …``, in an instance
method's as ``in the signature of method `m` …``, one in a record field as ``in the field
`f` of `R` …``, one in a `wrap` body as ``in the body of `W` …``, one in an alias body as
``in the alias `A` …``, one in a `let` annotation as ``in the annotation of `x` …`` and one
in a lambda parameter annotation as ``in a lambda parameter annotation …``.  The error is at the
declaration, since type expressions carry no position.

Type application is curried, so a head whose kind differs from the class's would leave
a different number of arguments than the dispatch type and their spines would misalign
*silently*: `instance Boxed (Tri a b c) where ToString b` bound `b` to the wrong
argument and printed a raw pointer.  Equal kinds leave equal numbers unapplied, which is
what dispatch relies on.  Rationale, prior art and the compatibility census:
`docs/kind-inference-v0.md`.

**An instance defines every method its class declares.**  Classes have no default
method bodies, so an instance that leaves one out is rejected at the instance:

```
instance Two Box does not define method `two`, declared by class Two. Classes have no default method bodies, so an instance defines every method (spec-v0.md §8.5).
```

**A class method is a value of its class's module.**  It is qualified as a function
is, exported exactly when its class is, and reached through imports like any value
(§3): a method of a module the file does not import is unknown.  One module may not
declare a method name twice, nor a method and a top-level value of one name; the
second declaration is rejected:

```
`blank` is declared by class main.Blank and by class main.Sizer in this module
```

An instance names its methods through its class, so it need not import them.
Rationale: `docs/qualified-class-methods-v0.md`.

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

**An instance head is its type-alias expansion.**  A `type alias` is transparent
(§5.6.2), so `instance C Name` where `type alias Name = String` *is* the instance
`instance C String`: it dispatches at `String`, and declaring both is an overlap.
The head is expanded before selection, kind checking and the overlap check all
read it, so a local alias of a foreign type is not a distinct head.  `wrap` is
**not** an alias — it declares a distinct nominal type, and `instance C Tag` on
`wrap Tag = String` stays separate from `instance C String`.  An alias applied to
fewer arguments than it declares is left unexpanded and is rejected where it is written:
after `type alias Half a = Tagged a`, `instance Boxed Half` fails with ``type alias `Half`
takes 1 type argument, given 0``.
Rationale: `docs/alias-instance-heads-v0.md`.

Admitting concrete type arguments (`instance C (List Int)`) is GHC's
`FlexibleInstances` extension, which pairs the relaxation with *full-head
matching* — selecting an instance by unifying the whole head rather than its
constructor.  The two arrive together by necessity; Sprout has the
head-constructor key only, and so takes the Haskell 2010 position.  Lifting the
restriction requires widening the key first (see `BACKLOG.md`).

**A constructor-headed constraint dispatches on the parameter it names.**  Given

```
fn second_only(x: Tagged k Int, y: Tagged j Int) -> String where Boxed (Tagged j) =
  label(y)
```

the hidden dictionary is `Boxed` at `Tagged <whatever j is>`, chosen because the
constraint writes `j` — not because some parameter happens to be headed by `Tagged`.
Where the two readings differ, only this one is correct: the other supplies a witness
for a type the constraint does not mention, and the method then interprets one type's
value through another's dictionary.  An argument the constraint fixes names its
parameter just as a variable does: `where Sh (Box String)` dispatches on the `Box
String` parameter even where a `Box Bool` one precedes it.  When a constraint's
arguments are not all fixed at a call site, the caller's own identical constraint is
forwarded; failing that, the dictionary is built from the instance for the head, using the
caller's dictionaries for its context.  A context constraint on one of the caller's type
variables that the caller does not declare is a compile-time error, even when the instance
never reads that dictionary: the check goes by the declared context.

**Two constraints of one class must not differ only in their arguments.**  A `where`
clause may not carry both `Boxed (Tagged k)` and `Boxed (Tagged j)`: each gets its own
hidden dictionary, but a method used as a value in the body cannot yet tell the two
apart, so it could take either one.  This is rejected at the declaration.  Two constraints with
the same subject are not affected, and neither are two over different head
constructors or different classes.  Lifting the restriction is tracked in `BACKLOG.md`.

