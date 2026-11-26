### 8.5.1 Kinds

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

