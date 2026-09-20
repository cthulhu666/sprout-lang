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
Inferring the constraint on the instance side (§4). Fixing the constrained
method-value gap (§8).

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

The cost is duplication, so the restatement is checked: it must name the same
classes as the class declaration, in the same order. The dictionaries are
positional slots, so a different class, a longer or shorter list, and the same
classes reordered each dispatch through the wrong slot rather than failing —
all three are rejected with the class and position named. Variable names are
free, which is what lets `Maybe`'s instance say `z`.

## 5. Hidden-argument ABI

One order, four sites:

```
<declared params> ++ <instance/class dictionaries> ++ <method-level dictionaries>
```

Method-level dictionaries come **last** so a method without a `where` keeps the
layout it had before this change — which is what keeps the committed bootstrap
seed and the golden IR snapshots stable for every existing class.

Four sites lay this out: `lowering.generate_one_class_wrapper` (the `__cm_`
dispatch wrapper, which forwards the method slots),
`lowering.generate_one_instance_fn` (the `__tc_` instance function), the call
site, and `lowering.build_lambda_with_inner_dicts`. The fourth is the awkward
one. It builds the witness for an instance whose HEAD is also constrained, so it
closes over the head dictionaries while staying open in the method's — the
captured group sits *between* the two open ones, and no prefix application can
express that. `collect_method_arities` therefore records the declared parameter
count and the method-level slot count separately, taking the latter from
`build_hidden_for_constraints` so it cannot drift from the instance function it
must match.

A mismatch between any two sites is not a compile error: it surfaces as an arity
error at codegen, or — if the counts happen to agree — as a dictionary
dispatched through the wrong slot. The invariant is recorded at
`generate_one_class_wrapper`.

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

`.iface` encoding gains a field on both nodes. The format version is 8; an
`.iface` written by an earlier compiler is rejected rather than misread.

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

- **A constrained class method in value position.** Passing the method itself
  bare — e.g. `list_map(show_all, xss)` where `show_all` carries a method-level
  `where` — fails at codegen: `internal error: under-application of
  '__tc_Container_List_show_all' reached codegen (arity 2, got 1)`. Calling it
  applied works, and an ordinary method used in value position *inside* a
  constrained instance method (bare `to_string` under `where ToString a`) is
  unaffected — only the constrained method itself, unapplied, hits this. See
  `tests/stdlib/test_method_level_constraint.spr`.
- **Instances beyond `List`/`Maybe`.** `Vec` and `Result e` are unimplemented.
- **Self-dispatch.** An instance method cannot call its own class method; the
  recursion goes in a free helper, as `Eq (List a)` does with `list_eq`.

## 9. References

- `BACKLOG.md` §7.5 (Type Classes) tracks the surrounding gaps: the missing
  conformance check above, and the unrelated ambiguous-forwarded-reference entry
  that also emits an `__eta_unresolved_*` sentinel.
- `docs/scoped-type-variables-analysis-2026-07-26.md` (why no `forall`).
- `docs/compiler-internals.md` §Type-aware GC rooting (the dictionary-slot rule).
- `tests/stdlib/test_method_level_constraint.spr`.
