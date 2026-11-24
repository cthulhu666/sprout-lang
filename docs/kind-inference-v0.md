# Kind inference v0

Status: stage 1 implemented. Stage 2 is in `BACKLOG.md`.
Normative text: `spec-v0.md` §8.5. This document is the rationale.
Replaces `instance-head-kinds-v0.md`.

## 1. Problem

The instance-head check modelled a kind as one integer: the number of arguments a class
variable is applied to. That is not the kind once a type takes a higher-kinded parameter
(`type Wrap f a = Wrap(f a)`), which Sprout allows. Four bugs, each reproduced before the
change:

- **False accept, variable head.** A type-variable-headed constraint was never checked.
  `fn g(x: f a) -> Int where Foo f` and `instance Foo (Wrap f a) where Foo f` compiled; a use
  failed with `No instance of Foo for List`.
- **False reject.** `class C t` with `fn m(x: Wrap t Int)`, then `instance C Maybe`, failed with
  "applies its variable to 0 arguments … this head leaves 1".
- **Internal error, unmentioned variable.** `class Tagless t` (variable in no method) admitted
  heads of any arity. `instance Tagless (Box a) where Eq a`, `fn h(x: f Int) where Tagless f`
  and `h(Box(1))` failed with "internal: instance Tagless_Box is written at depth 1 but
  dispatches on a type at depth 0".
- **Internal error, deriving.** `type Wrap f a deriving (Eq) = Wrap(f a)` emits `Eq f`; a use
  failed with "internal: instance Eq_List is written at depth 1 but dispatches on a type at
  depth 0".

## 2. Goals and non-goals

Stage 1 (landed): every type constructor and class has an inferred kind. Every instance head
and every constraint is kind-checked at its declaration: fn, class method, instance method,
instance context, superclass, existential (`exists … where`, `any C`) and derived.

Stage 1 also judges the types that declare a kind: every constructor field, record field and
`wrap` body, and every class method signature, must have kind `Type`. A clash there (`type T =
T(Maybe)`, `type W f = W(f Int, f)`) is reported at the declaration.

Stage 2 (`BACKLOG.md`): kind-check the types written in `fn` and instance-method signatures
(`fn f(x: Int Bool)`, `x: f Int, y: f`), alias declarations including unused parameters, and
expression annotations. Stage 1 reads those signatures to learn the kinds of their variables and
does not judge them. Stage 2 is separate because it is the only part that can reject code that
compiles today. Until then, a fn constraint's verdict can depend on parameter order: the first
unification wins, so `fn f1(x: t Int, y: t) -> Int where Functor t` is accepted while
`fn f2(y: t, x: t Int) -> Int where Functor t` is rejected.

Non-goals: kind polymorphism, kind syntax, multi-parameter classes.

## 3. Prior art

- **Haskell 2010 §4.6** (kind inference): "arrange the set of datatype, synonym, and class
  definitions into dependency groups"; "standard techniques of type inference and
  kind-preserving unification"; "a default of ∗ is assumed", per group "without consideration of
  the ways in which particular type constructor constants or classes are used in later
  dependency groups". `data App f a = A (f a)` gets `(∗→∗) →∗→∗`.
- **Haskell 2010 §4.3.2** (instance declarations): "The class and type must have the same kind".
- **GHC without PolyKinds** (users guide §6.4.14.19): "defaults kind variables to `Type` when
  possible; when this is not possible, an error is issued". With PolyKinds it generalises
  instead.

Sprout follows Haskell 2010 and GHC without PolyKinds, defaults included.

## 4. The rule

Kinds are `Type` and `k1 -> k2`. Kind variables exist only during inference.

**Group pass.** Type, record, wrap and class declarations are grouped by dependency (fields,
method signatures, method-level `where`, superclasses, existential constraints). Each strongly
connected group is inferred, then defaulted to `Type`.

- Type parameters and existential variables get fresh kind variables. The result kind is `Type`.
  Every field and wrap body has kind `Type`. An existential constraint `C t` requires
  kind(`t`) = kind(`C`).
- A class variable gets a fresh kind variable. Every method signature type has kind `Type`.
  A method-level `where C' u` and a superclass `S t` unify as constraints, before defaulting.
- A group is read in two passes. The first reads every member's fields and method signatures
  and judges them. The second checks every constraint (existential, method-level `where`,
  superclass) in member order. A constraint is therefore blamed only for a clash with what
  the signatures say, never for a kind that a later member's signature would have fixed, and
  a method's `where` is not read as the class variable's own use.

Per-group defaulting has two consequences. A phantom parameter is `Type`: after
`wrap Tagged u = Int`, `Tagged Maybe` is a kind error. A class whose variable nothing mentions
has kind `Type`.

**Declaration pass.** Each fn and instance (head, context, method `where`s) is
checked alone. Its type variables get fresh kind variables from its signature, which the pass
reads only when there is a `where` to judge. Each constraint `C t` requires kind(`t`) =
kind(`C`), variable heads included. An instance head `C H` requires kind(`H`) = kind(`C`).
Variables still open default to `Type`.

**Built-ins.** `Int Double Bool String Char Unit Bytes Builder` are `Type`. `Vector Map Ref` are
`Type -> Type`. Function, thunk and tuple types take and give `Type`. Kinds are read from
`types.Type` as `type_from_ast` builds it, so an alias counts as its expansion, including an
unsaturated body (`type alias E = Result String`).

**Deriving.** A derived class's constraint on a parameter of kind other than `Type` is rejected
at the type declaration. An applied-variable context (`Eq (f a)`) cannot be forwarded
(`resolve.check_context_subs`).

## 5. Messages

A kind is glossed once, where it first appears.

- Head: ``class `Boxed` takes a type of kind `Type -> Type` (a type constructor that takes one type), but the instance head `Tagged k v` has kind `Type` ``
- Variable head: ``class `Foo` takes a type of kind `Type`, but in the constraint `Foo f` the variable `f` has kind `Type -> Type` (a type constructor that takes one type)``
- Constructor head: ``class `Pretty` takes a type of kind `Type`, but in the constraint `Pretty Tagged` the type `Tagged` has kind `Type -> Type -> Type` (a type constructor that takes two types)``
- Class method `where`: prefixed ``in method `m` of class `C`: ``. Instance method `where`: prefixed ``in method `m`: ``.
- Class conflict: ``class `Mixed` uses its variable at kind `Type` in one place and `Type -> Type` (a type constructor that takes one type) in another; one class variable cannot be both``
- Superclass: ``class `Wrapper` uses its variable at kind `Type`, but its superclass `Functor` takes `Type -> Type` ``
- Ill-kinded argument: ``in the constraint `Foo (Tagged Maybe)` the type `Maybe` has kind `Type -> Type` (a type constructor that takes one type), but `Tagged` takes a type of kind `Type` ``
- Over-application: ``in the constraint `Pretty (Int Bool)` the type `Int` has kind `Type` and takes no argument, but it is applied to `Bool` ``
- `any C`: ``class `Container` takes a type of kind `Type -> Type` (a type constructor that takes one type), but `any Container` stands for a type of kind `Type` ``
- Not a type: ``in the constraint `Pretty (Box, Int)` the type `Box` has kind `Type -> Type` (a type constructor that takes one type), but it is used where a type of kind `Type` is needed``. The same form, with another scope, is used for a field: ``in constructor `T` …``, ``in the field `items` of `R` …``, ``in the body of `W` …``.
- Self-application: ``in the constraint `Pretty (f f)` the type `f` is applied to `f`, so its kind would have to contain itself``
- Class method signature: any clash that is not the class variable's own is prefixed ``in the signature of method `m` of class `C` ``.
- Deriving: ``cannot derive `Eq` for `Wrap`: its parameter `f` has kind `Type -> Type` (a type constructor that takes one type), and a derived instance can constrain only parameters of kind `Type` ``

Position is the declaration's: `TypeExpr`, `Param` and `ClassMethodSig` carry none.
The fixtures are `tests/conformance/type_error/kind_*.spr` with their `.err` files.

## 6. Implementation

A section of `stdlib/compiler/infer.sprout`, "Kind inference", all `kind_*`.

- `Kind` (`KType`, `KArrow`, `KVar`), `kind_unify` with an occurs check, `kind_default`,
  `kind_text` and `kind_gloss` for rendering. State is `KindSt`; declared kinds are `KindEnv`.
- `kind_of` reads the kind of a `types.Type`.
- `kind_groups` collects type and class nodes (`kind_node_key`, `kind_collect`), builds the
  dependency graph (`kind_graph`, `kind_decl_deps`) and runs the components in order, reusing
  `scc.sccs_in_source_order`. `kind_infer_group` infers one component and `kind_close` defaults it.
- `kind_decl_error` checks each fn and instance against the closed environment
  (`kind_check_fn`, `kind_check_instance`). `kind_derive_error` is the deriving rule.
- Entry: `check_instance_head_kinds`, in the same pipeline slot as before: after
  `check_tyvar_instance_heads`, before `check_instance_context_sizes`.

Equal kinds leave equal numbers of arguments unapplied, so the equal-spine premise in
`resolve.unify_type_expr` holds for every accepted program; the two internal errors in §1 are
now located kind errors. Removed: `var_app_depths`, `own_class_depths`, `env_class_depths`,
`class_var_arity`, `head_residual`, `constraint_arity_error`, `decl_arity_error`. The
environment-path halves were unreachable (the checker and the compiler both pass `extra = Nil`).

**The one integer table left.** `type_arities` and the `@tyarity:` markers stay. Only
`leaves_arguments` reads them, to word constraint advice. No check consults them.

## 7. Compatibility

Newly rejected: the variable-headed false accepts, a head not of kind `Type` for a class whose
variable nothing mentions, a derived constraint on a higher-kinded parameter, a field, `wrap`
body or class method signature that is not of kind `Type` (`type T = T(Maybe)`), a tuple or
function part in a constraint that is not of kind `Type` (`where C (Box, Int)`), and a
variable applied to itself (`where C (f f)`).
Newly accepted: the false reject in §1.

Census: higher-kinded type parameters occur only in test fixtures. Every class in `stdlib/`,
`examples/` and `ide/` mentions its variable. A grep of signatures for a bare `Maybe`, `Vec`,
`Map`, `Ref` and similar found nothing ill-kinded.

Fixtures changed: the `.err` text of `instance_head_oversaturated`,
`instance_head_oversaturated_deep`, `instance_head_undersaturated`,
`constraint_head_bare_ctor_arity`, `instance_method_where_head_arity`,
`class_var_arity_disagreement` and `class_superclass_var_arity_disagreement`;
`tests/stdlib/test_instance_head_arity.spr` now holds the accepted `instance Tagless (Box a)`;
`instance Tagless Box` is rejected by the fixture `kind_unconstrained_class_head`.

## 8. Tests

`tests/conformance/type_error/kind_*` (variable-headed fn constraint and instance context, `any`,
`exists … where`, method-level `where`, unconstrained-class head, deriving, phantom default,
over-application, tuple and function parts, self-application, a class variable through an
application, constructor, record and `wrap` fields) and `tests/conformance/run/kind_*`
(`instance C Maybe` under `Wrap t Int`, an App-style type with an instance, a method-level
constraint that fixes a higher-kinded method variable, a higher-kinded class parameter, a
higher-kinded record field and `wrap` body).
