# Instance head kinds v0

Status: proposed, revision 2.

Revision 1 proposed a declaration-site arity check plus a per-class constant peel
in `unify_type_expr`. The placement survived review; the peel did not. It was
refuted by building a compiler with it implemented literally: the branch's own
`tests/stdlib/test_method_constraint_dispatch.spr` then fails to compile, where
the unpatched branch passes 13/13. §5 explains why, and is the part of this
document that carries the actual fix.

## 1. Problem

`class Boxed t` whose method writes `fn label(xs: t Int)` uses `t` applied to one
argument, so `t` has kind `* -> *`. Nothing checks that an `instance Boxed H`
supplies an `H` of that kind. Three observed consequences:

- `instance Boxed (Tagged k v)` saturates `Tagged`, leaving the class variable no
  argument. The head match binds nothing, lowering null-fills the dictionary slot,
  and the program aborts at run time with no diagnostic.
- `instance Boxed (Tri a b c) where ToString b` records a dispatch type shallower
  than the head. The two spines pair outside-in, `b` binds to the wrong argument,
  and the wrong witness reaches `str_concat` as a segfault.
- The consumer side compensates, and cannot. `te_app_depth`/`drop_surplus_args`
  peel a surplus computed per call as a depth difference, correct only when the
  concrete type is the deeper of the two; `resolve.check_context_subs` then guards
  the result by substitution membership, which misses a variable bound to `_`,
  never fires when the head is the deeper side, and rejects
  `instance Foo Bar where Baz k`, which compiled before.

Two distinct defects hide in that third bullet, and revision 1 conflated them.
The kind of the instance head is never checked — that is §4. And the three places
that record a dispatch type do not record the same thing — that is §5. Fixing
only the first leaves the second, which is what the refuted peel was
unsuccessfully papering over.

## 2. Goals

- Reject an ill-kinded instance head, and an ill-kinded constructor-headed
  constraint, at its own declaration, with that line's position.
- Give the three recorders one invariant, so that spine matching needs no peel at
  all and a depth mismatch becomes a located failure rather than a silent
  mis-binding.
- Delete `check_context_subs` without losing a program it correctly rejects.
- No new syntax, no new annotations.

### Non-goals

- Kind polymorphism, kind variables, kind signatures, or any kind but `*` and
  arrows over `*`.
- Multi-parameter type classes — Sprout has none, and the check keys on one class
  parameter exactly as `check_missing_superclass_instances` already does.
- Kind checking of type expressions generally (`fn f(x: Int Bool)`).
- Type-alias instance heads. They do not dispatch today (an instance is registered
  under the alias name, `register_instance_marker`), so their arity is moot until
  that changes.

## 3. Prior art

| Language | Kind structure | Where the instance head is checked |
|---|---|---|
| Haskell 2010 | Full inference, kinds default to `*` | Static restriction on the declaration: "The class and type must have the same kind; this can be determined using kind inference as described in Section 4.6." (§4.3.2) |
| PureScript | Richest of the four: `Type`, `Row`, `Symbol`, kind polymorphism via top-level kind signatures (`data Proxy :: forall k. k -> Type`) | Kind-checked at the declaration, with signatures optional |
| Scala 3 | Higher-kinded throughout; the expected arity is written at the abstraction site (`F[_]`), while a concrete type constructor's parameter clause is computed from its definition | At the declaration, by the same type checker |
| Rust | None — every type parameter is a proper type; associated types cover the use cases | Not applicable |

Sources: [Haskell 2010 Report ch. 4](https://www.haskell.org/onlinereport/haskell2010/haskellch4.html),
[PureScript Types](https://github.com/purescript/documentation/blob/master/language/Types.md),
[Scala 3 spec §3 Types](https://www.scala-lang.org/files/archive/spec/3.4/03-types.html),
[Rust reference, generic parameters](https://doc.rust-lang.org/reference/items/generics.html).

They agree on the placement — the declaration, not the use — and diverge only on
how much kind structure exists. Sprout has one class parameter, no kind variables,
no kind annotations, and no kind but `*` and arrows over `*`. Every kind is
therefore `*^n -> *` and is fully described by the single number `n`. An arity
check here is not an approximation of kind checking; it is kind checking, with the
kind written as a number. Should Sprout later gain a second kind or a kind
variable, `n` becomes a `Kind` type and the check grows a unifier — its placement
does not move.

## 4. The rule

For a single-parameter `class C t`:

- `classvar_arity(C)`: every occurrence of `t` — across C's method signatures and
  across each transitive superclass's own arity — must apply it to the SAME number
  of arguments, and that number is the arity. Disagreement is an error reported at
  the CLASS declaration, not at any instance.
- `residual_arity(H)` = `declared_arity(ctor) - args applied in the head`.
- **Restriction:** every `instance C H`, and every constraint `C H` whose head is a
  constructor application, satisfies `residual_arity(H) == classvar_arity(C)`.

Four notes the first revision got wrong:

1. **Agreement, not maximum.** Revision 1 took the greatest depth. `class Twice t`
   with `fn one(x: t Int)` and `fn two(x: t)` then has arity 1, and
   `instance Twice (Box a)` is rejected with an arity message — blaming the
   instance for a defect in the class. That program compiles today, so this is
   also a compatibility break, and it belongs in §8.
2. **Superclasses carry arity.** A class whose methods never mention `t` is not
   arity 0 if a superclass constrains it: `class Wrapper t where Functor t` with
   `instance Wrapper Maybe` compiles and runs today, and a methods-only rule
   rejects it. Arity comes from methods unified with transitive superclasses via
   `build_class_super_map`. If neither constrains the variable, the class is
   unconstrained and every instance of it is accepted.
3. **Runtime types take parameters.** `c_runtime_type_names` is
   `["Vector", "Map", "NativeSet", "Ref"]` in `infer.sprout`, and
   `instance Peek (Vector a)` compiles and runs today. They need an explicit table
   — Vector 1, Map 1, Ref 1, NativeSet 0 — not the blanket 0 revision 1 gave every
   builtin. `primitive_type_names` are genuinely 0.
4. **Constraints, not only instances.** `fn wrap_pp(x: Tagged Bool Int) -> String
   where Pretty Tagged` is a wrong-kind head in a `where` clause. It is rejected
   today by `check_context_subs`; an instance-only check would regress it to
   master's runtime abort. §6 step 4 covers fn, method-level and instance-context
   constraints alongside instance heads.

A head that is not an application has residual arity 0: a tuple
(`instance Eq (a, b)`) and a primitive (`instance Semigroup String`). Every other
prelude head is a `TypeDecl`, so `List`, `Maybe`, `Result`, `Vec`, `Dict`, `Set`
and `IntRange` need no special case. `stdlib/prelude.sprout` has 64 top-level
`instance` declarations; the acceptance sweep in §9 must cover all of them
mechanically rather than by inspection.

## 5. The recorder invariant

This is the part revision 1 missed, and the reason its peel could not work.

Three places record the dispatch type a `TDict` carries, and today they record
three different things for the same class:

| Recorder | Records | Depth for `label(Tagged(true, 5))` |
|---|---|---|
| `class_var_dispatch_type` | the class variable's binding, `Tagged Bool` | `k` |
| `resolve_arg_scanned_tdict` via `headed_arg_typeexpr_or_name` | the whole argument type, `Tagged _ Int` | `k + classvar_arity` |
| `resolve_concrete_head_tdict` | a bare `TypeName(head)` | 0 |

`class_var_dispatch_type` also falls back to the whole argument type when its
projection misses, so even one recorder is not self-consistent.

The surplus a consumer must peel is therefore a property of the RECORDER, not of
the class. A per-class constant cannot serve three recorders at three depths,
which is why implementing revision 1's step 4 literally breaks the branch's own
test file: with `classvar_arity(Boxed) = 1`, `fwd_ts`'s call to `label(x)` — whose
constraint went through the arg-scanned recorder at depth 2 — peels one argument
too few and the head match binds nothing.

**Invariant: every recorder records the class variable's binding.** That is
already the branch's thesis for the primary path (`concrete_dispatch_call`); it
simply was not carried to the other two. For the arg-scanned recorder the binding
is obtainable without scanning arguments at all: the callee's own constraint
(`where Boxed (Tagged k)`) already has the right shape, with program-level names,
and needs only the call's instantiation substituted into it. That also removes a
selection bug the argument scan has independently — with
`fn helper(x: Tagged k Int, y: Tagged j Int) where Boxed (Tagged j)`, the scan
takes the first argument whose head matches, which is `x`.

Once the invariant holds and §4 is enforced, pattern depth equals concrete depth
by construction. `drop_surplus_args`, `te_app_depth` and the depth subtraction are
deleted from both copies of `unify_type_expr`, and the `| _ -> acc` fallthrough
in each becomes a located invariant
failure: reaching it means a recorder broke the invariant, and silently returning
the accumulator is what turned that into a segfault.

## 6. Implementation

1. **`@tyarity:<TypeName>`** in `infer.sprout` beside `mark_declared_types`: the
   declared parameter count of every `TypeDecl` / `RecordDecl` / `WrapDecl`, same
   sentinel idiom as `@arity:` / `@linear:` / `@type:`. Plus the fixed table for
   `c_runtime_type_names` and 0 for `primitive_type_names`.
2. **`classvar_arity`** derived from the method SCHEMES (`dict_get(method, env)`,
   walking the `types.Type` spine for the class parameter) rather than from
   `ClassMethodSig` TypeExprs, unified across methods and transitive superclasses.
   The class parameter name comes from the `@class:` marker, whose `Scheme`
   already carries `class_vars`. Schemes are the one source
   available on both the bundled and the env path; a decls-only table is inert in
   the REPL, where no prelude class has a decl. **Verify first:** that method
   schemes are in `scanned_env` at the point the check runs. If they are not, the
   check moves later in the chain rather than reverting to decls.
3. **Read arities verbatim on the bundled path.** The bundler qualifies both sides
   (`Tagged` becomes `main.Tagged`), so a lookup keyed on the head's spelling
   against short-keyed markers misses. Take decls verbatim here, exactly as
   `class_effect_table` does and for the reason its comment gives; use short-keyed
   `@tyarity:` with `after_last_dot` only as the env-path fallback, where the
   short-name collision it warns about is confined.
4. **`check_instance_head_kinds`** into the whole-program chain in
   `typecheck_program`, after `validate_all_decls` and after
   `check_tyvar_instance_heads` (which has by then established the head is
   `T v1..vk` with distinct variables). Covers instance heads and every
   constructor-headed constraint — fn `where`, method-level `where`, instance
   contexts — skipping type-variable heads. On the bundled path an unknown
   constructor arity is a compiler bug, because `validate_all_decls` has already
   rejected unknown type names: fail loudly, do not skip.
5. **Carry the recorder invariant** (§5): the arg-scanned recorder substitutes the
   callee's constraint head instead of scanning arguments;
   `class_var_dispatch_type`'s fallback and `resolve_concrete_head_tdict` record
   the class variable's binding.
6. **Delete the peel** — `drop_surplus_args`, `te_app_depth` and the subtraction —
   from both copies of `unify_type_expr`, and make the fallthrough a located
   failure.
7. **Delete** `check_context_subs`, `unbound_context_var`, `unbound_var_in_list`
   and `unbound_var_in_te`. Safe only once step 4 covers constraint heads.
8. **`@fwdvar:<tyvar>`** beside `@fwd:<tyvar>:<class>`, replacing
   `fwd_prog_var_any_class`'s full-env scan — 3.3x on a 2400-site A/B, 81% of
   samples, for byte-identical IR. It must be written by BOTH seeders:
   `seed_fwd_with_supers` and `seed_given_for_skolem`, which
   writes `@fwd:` directly. Missing the second regresses existential compound-head
   forwarding to `_`. The cross-module identity worry is moot: `fwd_env` is
   per-function and never returned, so a marker never leaves the function that
   seeded it.

## 7. Impact

- **Syntax:** none.
- **Semantics:** none for any program accepted both before and after.
- **Type system:** one new static restriction, on instance declarations and on
  constructor-headed constraints.
- **Errors:** two new messages — an ill-kinded head at its own position, naming the
  class, the head as written and both arities; and an inconsistent class at the
  class declaration. They replace a call-site message that hard-coded one cause for
  a condition with several and printed the internal key (`Boxed_main.Tagged`)
  rather than the instance as written.

## 8. Compatibility

Newly rejected, and each needs a conformance fixture:

- An instance or constraint whose head kind disagrees with its class variable's.
  Measured against a master-seed compiler, these do not uniformly misbehave today:
  `instance Boxed (Tri a b c)` with `fn label(xs: Tri a b Int)` prints the CORRECT
  `hi/5` on master and a raw pointer, `4364600296/5`, on this branch. So the rule
  rejects a program that works today, and its working is accidental — the head
  match happens to land on the right argument for this arity. That is the honest
  cost of the check, and the reason to take it is that the same shape one argument
  wider silently selects a different dictionary.
- A class whose occurrences of its variable disagree on arity (`class Twice t`
  above). This compiles today. It is a genuine break, accepted because the class
  is ill-kinded and every instance of it is a coin flip.

Returning to compiling: `instance Foo Bar where Baz k`, which revision 1's guard
rejected.

Evidence required before landing, none of it optional: `just test`,
`just compile-examples-stage1`, `just ir-golden-diff`, `just ci-fast-gates`, and
the downstream sweep against uncharted-suns (four instance heads, all arity 0).

## 9. Tests

- Conformance `type_error`: oversaturated head (`Tagged k v` at `* -> *`);
  undersaturated head; the `Tri a b c` shape, which today compiles and segfaults;
  a wrong-kind fn `where` head (`where Pretty Tagged`); a class whose methods
  disagree on arity.
- Conformance `run`: `instance Foo Bar where Baz k`; `class Wrapper t where
  Functor t` with no methods mentioning `t`; `instance Peek (Vector a)` and
  `instance Peek (Ref a)`.
- `tests/stdlib/test_method_constraint_dispatch.spr`: a polymorphic caller of the
  compound-head forwarding function, which currently aborts; and the two-candidate
  `helper(x: Tagged k Int, y: Tagged j Int) where Boxed (Tagged j)` shape that the
  argument scan resolves to the wrong parameter.
- A mechanical sweep asserting all 64 prelude instances are accepted, so the claim
  is checked rather than eyeballed.
- A REPL/env-path regression through `compile_source_with_cache`, since a
  decls-only table would leave the check silently inert there.
- `classvar_arity` units: class parameter only in a return type; only under a
  superclass; mentioned at two depths (must reject the class).

## 10. Risk

`just ir-golden-diff` reported 0/65 for the previous attempt in this area. That is
evidence about the corpus, not about the change: the corpus contains no program of
this shape. Blast radius must be measured with a shape-specific A/B against a
master-seed compiler, as in [docs/gates.md](gates.md), never by the gate's colour.

The recorder invariant is the risky half, not the arity rule. It changes what
every dispatch site records, so the golden IR corpus and the seed fixed point are
the load-bearing gates, and the A/B must cover a forwarded constraint, a compound
head, an existential and a devirtualized concrete call.

## 11. Deferred

- Multi-parameter type classes: `classvar_arity` becomes per-parameter and the
  head check runs per argument.
- Kind checking of arbitrary type expressions, which would catch `fn f(x: Int Bool)`.
- Type-alias instance heads, once they dispatch.
