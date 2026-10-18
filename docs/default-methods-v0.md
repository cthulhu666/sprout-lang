# Default method bodies (v0)

Status: **proposed**, not implemented. Spec §8.5 says classes have no default method bodies; that
stays normative until this lands. On landing, §8.5 carries the rules and this file keeps the
rationale and the rejected alternatives.

## 1. Problem

A class method is a signature only (`parse_class_body` collects `ClassMethodSig`s), so every
instance defines every method (spec §8.5). Two costs follow.

- **Adding a method breaks every user instance.** `Monoid.concat_all` (PR #664) was safe only
  because no user `Monoid` existed in the four repos.
- **A derivable method is still mandatory.** `Foldable.fold_values` is
  `fold_while_values(step, \_ -> true, init, xs)`; `Filterable.filter_values` is
  `filter_map_values` with a `Just`/`Nothing` predicate. Each instance writes them out anyway.

Two designs were declined over this cost: a sort method on `Ord` (`sorting-v0.md`) and
`length`/`is_empty` over `Foldable` (`BACKLOG.md`, collection-combinators non-goals).
`fold-while-v0.md` §5 paid it instead, keeping `fold_values` mandatory because deriving it costs
an indirect call per element. A default makes that derivation opt-in per instance.

## 2. Goals and non-goals

**Goals.** A class method may carry a body. An instance that omits the method gets it; one that
defines the method overrides it. The default is type-checked once, where it is written. Existing
programs keep their meaning.

**Non-goals.** Default signatures (a default with a stronger constraint than the method, GHC's
`DefaultSignatures`). Defaults supplied by a superclass. A `minimal` pragma. Specialising the
default per instance (§6).

## 3. Prior art

| language | default bodies | an instance that takes two defaults calling each other |
|---|---|---|
| Rust | a trait fn body "acts as a default for any implementation which does not override it" | not checked |
| Haskell | yes; `mconcat` is a `Monoid` method "so that an optimized version can be provided for specific types", defaulting to `foldr` | warned only under `{-# MINIMAL (==) \| (/=) #-}`; without it "no warning would be generated" |
| Scala cats | `Monoid.combineAll` has a body, `foldLeft(empty)(combine)` | not checked |
| PureScript | not used by `Monoid`, whose only member is `mempty` | — |

Sources: the Rust Reference (Traits), `base` `Data.Monoid`, the GHC User's Guide (pragmas,
`MINIMAL`), `cats/kernel/Monoid.scala`, `purescript-prelude` `Data/Monoid.purs`.

## 4. Design

```sprout
export class Monoid t where Semigroup t
  fn empty() -> t
  fn concat_all(xs: List t) -> t = list_fold(\ (acc, x) -> acc ++ x, empty(), xs)
```

1. **Syntax.** `= <expr>` after a method signature, with an optional trailing `where` of value
   bindings, as for an instance method. Constraints still bind before `=`.
2. **Scope.** The body is resolved in the class's module. It sees what that module sees, the class
   variable's own dictionary (so it may call the class's methods, and its superclasses', at `t`),
   and the method's parameters.
3. **Override.** An instance that defines the method uses its own body; the default is not called.
4. **Missing methods.** An instance must define every method *without* a default.
5. **Default cycles.** Reject an instance whose taken defaults call each other in a cycle of two or
   more methods, at the instance (a Haskell `Eq` with neither `==` nor `/=`). The class itself may
   pair two defaults; an instance must break the pair. A default calling itself is ordinary
   recursion and is allowed. Only direct calls are seen: a cycle routed through a free
   `where C t` function is not caught.

## 5. Implementation

Lift each default into one generic function and make an omitted method forward to it — the shape
of the `concat_by_append` helper PR #664 had to drop.

- **Lift.** `fn concat_all(xs: List t) -> t = <body>` in class `Monoid` becomes a top-level
  `fn __dm_Monoid_concat_all(xs: List t) -> t where Monoid t = <body>` in the class's module.
- **Forward.** An instance omitting `concat_all` gains
  `fn concat_all(xs: List Sum) -> Sum = __dm_Monoid_concat_all(xs)`, the class variable
  substituted by the instance head.
- **When.** After bundling, before checking. The bundler already qualifies names per module, so it
  qualifies a default body in the class's module (rule 2), and the forwarder names the lifted
  function by its canonical name, so import visibility does not apply to it.
- **Order.** Each module is checked whole, classes first (d1f533e0). The lifted function is an
  ordinary declaration of the class's module, and a forwarder an ordinary instance method, so the
  existing segment graph orders them. Nothing after the checker changes.
- **AST.** `ClassMethodSig` gains a `Maybe Expr` body. 68 uses outside `ast.sprout` across
  `parser`, `bundler`, `infer`, `lowering`, `typed_ast`, `verify_dispatch`, `iface_codec` and
  three test files. `iface_codec` records only whether a method has a default; the missing-method
  check needs that, the body does not.
- **Missing-method check.** `own_class_methods` (infer.sprout) keeps the methods without a
  default; the cycle check runs beside it, over the methods each instance takes.

The forwarding call works today when written by hand: an instance method calling a
`where Monoid t` function at its own type, the case B2 used to block, compiles and runs on
master (6ede8783).

## 6. Cost

The forwarder is a direct call, but it builds the instance's dictionary on each call: the
hand-written probe allocates three closures (`empty`, `concat_all`, `append`) per call to `Sum`'s
`concat_all`. Inside the default every class-method call (`++`, `empty()`) is indirect.

That is per call of the defaulted method, not per element. It matters for a method called in an
inner loop, such as a defaulted `eq`. An instance that cares overrides the method. Removing the
cost — specialising a default per instance, checked once and lowered per head — is a later
optimisation, not part of v0.

## 7. Rejected alternatives

- **Copy the body into each instance.** Concrete dispatch and no dictionary, but the body would be
  resolved in the instance's module (rule 2 broken: a local or a same-named method there captures
  it, the `BACKLOG.md` capture family), and a bad default is reported once per instance.
- **A separate class per bulk method** (`class Concat t where Monoid t`). Sprout has no fallback
  instances, so a generic caller cannot use the fast path when it exists and fold otherwise: every
  type needs both instances, the same tax plus a second constraint.
- **A `minimal` pragma now.** Rule 5 derives what `MINIMAL` states by hand, for direct calls,
  without new syntax. A pragma can come later if a class needs a requirement rule 5 cannot see.

## 8. Impact

- **Syntax.** One optional `= <expr>` per class method signature. The parser comment that a
  signature's `where` cannot be a value binding no longer holds; the instance-method rule
  replaces it.
- **Type system.** None new: the lifted function is an ordinary constrained function.
- **Errors.** The missing-method message narrows to methods without a default. New, at the
  instance: ``instance Eq Box takes the defaults of `eq` and `ne`, which call each other; define
  one of them``.
- **Compatibility.** Additive. Every current program keeps its meaning; no instance needs editing.
  Prelude classes may then gain defaults (`Monoid.concat_all`, `Foldable.fold_values`,
  `Filterable.filter_values`); removing a now-redundant instance method is optional.

## 9. Tests

- Conformance run: an instance taking a default; one overriding it; a default calling another
  method of its class and a superclass method; a class and instance in different modules; an
  instance in a module that cannot see a private helper the default uses.
- Conformance type errors: a missing method without a default; a default cycle taken by an
  instance; a default body that does not type-check, reported once at the class.
- Parser: both body forms, a trailing `where`, and a signature with constraints and a body.
- Regression: devirtualization still fires on an overriding instance (golden IR unchanged).

## 10. Spec and docs on landing

Spec §8.5: replace "Classes have no default method bodies" and its error text with rules 1-5.
Then revisit `sorting-v0.md` (sort on `Ord`) and the collection-combinators non-goals entry.
