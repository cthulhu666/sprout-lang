# Method-Level Constraints (v0)

**Status:** landed. `Traversable` (`stdlib/prelude.sprout`) is the first
consumer. Normative syntax is `docs/spec-v0.md` §8.5; this doc carries the
rationale and the hidden-argument ABI.

## 1. Problem

A class method cannot constrain a type variable of its own:

```sprout
class Traversable t
  fn traverse_values(g: a -> f b, xs: t a) -> f (t b) where Applicative f
```

`t` is the class variable; `f` belongs to the method. Before this change the
trailing `where` was a parse error, so `Traversable` could not be declared at
all and `BACKLOG.md` §7.5 listed the capability as blocked at the syntax layer.

The blocker was syntax only. Sprout already quantifies a method's own variables:
`infer.register_class_method` scans the signature for lowercase names not among
the class parameters, and `collect_te_vars` descends both halves of a
`TypeApply`, so the `f` in `f b` is bound like any other. What was missing was a
place to attach a constraint to it — `ClassMethodSig` had no field, and
`method_scheme` passed `Nil` into a `Scheme` whose constraint slot already
existed.

## 2. Goals and non-goals

**Goals.** Declare a constraint on a method-quantified variable; resolve its
dictionary per call site; keep the hidden-argument layout of every existing
class byte-identical.

**Non-goals.** A `forall` surface — deferred, with its own trigger, in
`docs/scoped-type-variables-analysis-2026-07-26.md`; it solves a different
problem (rigid variables, rank-2 arguments) and nothing here needs it.
Inferring the constraint on the instance side (§4). Fixing the eta'd
method-value gap (§7).

## 3. Prior art

| Language | Method-level constraint | Instance restates it? |
|---|---|---|
| Haskell (2010 §4.3.1) | yes, in the class method's context | no — the class fixes the signature |
| Rust (Reference, *Traits*) | yes, `where` on the method | no — `impl` writes the method signature without the bound |
| Swift | yes, `where` on a protocol method | no |
| OCaml | n/a — modules, not constrained methods | n/a |

Every surveyed language spells it as a trailing clause on the method and lets
the instance inherit it. Sprout matches the syntax and diverges on inheritance,
for the reason in §4.

## 4. Decision: the instance restates the constraint

Sprout instance methods already restate what the class declared — parameters and
return type are written out at every instance:

```sprout
instance Container List
  fn show_all(xs: List a) -> String where ToString a =
    ...

instance Container Maybe
  fn show_all(xs: Maybe z) -> String where ToString z =
    ...
```

The constraint follows that existing convention rather than Haskell's. Two
consequences that motivated the choice:

- The dictionary's binding site is local. An instance may name its variables
  differently from the class (`z` above), so an inherited constraint would have
  to be mapped onto the instance's variables positionally — machinery that does
  not exist and that would be invisible at the point a reader needs it.
- It matches how the instance already reads. Nothing else about an instance
  method's signature is inherited.

The cost is duplication, and a restated constraint that disagrees with the class
declaration is **not yet rejected** (§7).

## 5. Hidden-argument ABI

One order, three sites:

```
<declared params> ++ <instance/class dictionaries> ++ <method-level dictionaries>
```

Method-level dictionaries come **last** so a method without a `where` keeps the
layout it had before this change — which is what keeps the committed bootstrap
seed and the golden IR snapshots stable for every existing class.

The three sites are `lowering.generate_one_class_wrapper` (the `__cm_` dispatch
wrapper, which forwards the method slots), `lowering.lower_instance_method` (the
`__tc_` instance function), and the call site. A mismatch between the first two
is not a compile error: it surfaces as an arity error at codegen, or — if the
counts happen to agree — as a dictionary dispatched through the wrong slot. The
invariant is recorded at `generate_one_class_wrapper`.

A forwarded dictionary slot is a heap value. Its `TVar` carries the method's
scheme type, never a scalar: type-aware rooting reads that field, and naming a
non-heap type there drops the root (`docs/compiler-internals.md` §Type-aware GC
rooting).

## 6. Where the constraint is recorded

| Stage | Carrier |
|---|---|
| Parse | `parse_optional_constraints`, before `=` on an instance method — position separates it from the value-binding `where` after the body |
| AST | `ClassMethodSig … (List TypeConstraint)`, `InstanceMethodImpl … (List TypeConstraint)` |
| Scheme | `Scheme`'s existing constraint slot; positional `#pos:k` tokens in `infer`, name-keyed in `iface_codec` (whose method schemes quantify class params only) |
| Typed AST | `TypedInstanceMethod … (List TypeConstraint)` |
| Lowering | `build_hidden_for_constraints`, the same helper a constrained `fn` uses |

`.iface` encoding gains a field on both nodes, so the format changes and cached
interfaces are invalidated.

## 7. Call-site injection

`infer.sprout`'s call path splits on the `@class:` marker: a class method goes to
`check_instance_for_marker` (dispatch), a constrained `fn` to
`inject_constrained_fn_dicts_via_field` (hidden dicts). A method with its own
`where` needs **both** — the method's dicts are injected first, then the class
dict is prepended ahead of them, which is the §5 order once lowering appends
them after the user arguments.

One trap, found by `Traversable` and invisible to a single-method test class: a
constraint expands to one hidden slot **per method of the class and its
transitive superclasses**, not one per constraint. `where Applicative f` is
three slots (`pure`, `map2`, and `Functor`'s `fmap`). A class with no
superclass and one method hides the bug entirely.

## 8. Open

- **No conformance check.** An instance whose restated constraint disagrees with
  the class declaration is accepted.
- **Eta'd method values.** A class method in value position under a method-level
  constraint reaches codegen as `__eta_unresolved_<Class>_<method>`; the lambda
  spelling works. Same root as the ambiguous-method-reference entry in
  `BACKLOG.md` §7.5.
- **Instances beyond `List`/`Maybe`.** `Vec` and `Result e` are unimplemented.
- **Self-dispatch.** An instance method cannot call its own class method; the
  recursion goes in a free helper, as `Eq (List a)` does with `list_eq`.

## 8. References

- `BACKLOG.md` §7.5 (the P0 this serves, and the eta'd-value entry).
- `docs/scoped-type-variables-analysis-2026-07-26.md` (why no `forall`).
- `docs/compiler-internals.md` §Type-aware GC rooting (the dictionary-slot rule).
- `tests/stdlib/test_method_level_constraint.spr`.
