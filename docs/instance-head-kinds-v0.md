# Instance head kinds v0

Status: implemented, revision 4.

Revision 1 proposed a declaration-site arity check plus a per-class constant peel
in `unify_type_expr`. The placement survived review; the peel did not. It was
refuted by building a compiler with it implemented literally: the branch's own
`tests/stdlib/test_method_constraint_dispatch.spr` then fails to compile, where
the unpatched branch passes 13/13.

Revision 2 diagnosed why — three recorders at three depths, §5 — but recorded the
repair as unavailable, because the constraint token kept a compound head's
constructor and dropped its arguments. Revision 3 widens that token and is what
landed. §5 carries the fix; §6 lists the eight steps as built.

Revision 4 is five fixes from an ensemble review of revision 3, each with a
regression test that was confirmed red first. Two were defects revision 3
introduced, three were holes it left. §12 lists them.

## 1. Problem

`class Boxed t` whose method writes `fn label(xs: t Int)` uses `t` applied to one
argument, so `t` has kind `* -> *`. Nothing checks that an `instance Boxed H`
supplies an `H` of that kind. Three observed consequences:

- `instance Boxed (Tagged k v)` saturates `Tagged`, leaving the class variable no
  argument. The head match binds nothing, lowering null-fills the dictionary slot,
  and the program aborts at run time with no diagnostic.
- `instance Boxed (Tri a b c) where ToString b` records a dispatch type shallower
  than the head. The two spines pair outside-in, `b` binds to the wrong argument,
  and the wrong witness reaches `str_concat` — which prints a raw pointer rather
  than crashing, so nothing marks it as wrong.
- The consumer side compensated, and could not. `te_app_depth`/`drop_surplus_args`
  peeled a surplus computed per call as a depth difference, correct only when the
  concrete type is the deeper of the two; `resolve.check_context_subs` then guarded
  the result by substitution membership, which missed a variable bound to `_`,
  never fired when the head was the deeper side, and rejected
  `instance Foo Bar where Baz k`, which compiled before. Both are gone: the guard
  outright, the peel to the recorder that knows the depth (§5).

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
- Delete `check_context_subs`'s unbound-variable test without losing a program it
  correctly rejects.
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
   where Pretty Tagged` is a wrong-kind head in a `where` clause. An instance-only
   check would regress it to master's runtime abort — and `check_context_subs` did
   not in fact reject it either, as the fixture written for it showed: it compiled
   clean before this change. §6 step 4 covers fn, method-level and instance-context
   constraints alongside instance heads.

Two shapes reach residual 0 by different routes, and conflating them would get a
bare `Tagged` wrong: a tuple (`instance Eq (a, b)`) has no constructor to count,
while a primitive (`instance Semigroup String`) is a constructor whose declared
arity is 0. A bare `Tagged` is residual 2, which is the point. Every other
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
simply was not carried to the other two. The arg-scanned recorder also has a
selection bug of its own: with
`fn helper(x: Tagged k Int, y: Tagged j Int) where Boxed (Tagged j)`, the scan
takes the first argument whose head matches, which is `x`.

**The fix is to substitute the callee's own constraint, and it needed a wider
token first.** Substituting `where Boxed (Tagged j)` into the call's instantiation
gives the binding directly, at the right depth — but the constraint the recorder
could see was `#app:Tagged`, because the token kept the head and dropped `j`. The
token now carries both:

```
#app:<Head>                      the head alone (a pre-v9 interface only)
#app:<Head>:<t1>,<t2>,...        one constraint-var token per WRITTEN argument
```

Each `<ti>` is an ordinary constraint-var token — `#pos:<k>` against the callee's
generalized binder list, a source name where there is no index, or `#any` for an
argument that is no variable at all. Two things follow, and the second is what
makes the peel removable:

- **Which** argument the constraint meant is now stated, not guessed. The head NAME
  still comes from a matching argument, because the constraint spells it as written
  (`Tagged`) while `@inst:` keys carry the qualified one (`main.Tagged`); which
  argument supplies that spelling no longer matters.
- **How deep** the head is written is now known even when the arguments are not —
  `#any` occupies a position, so the COUNT survives. A recorder that starts from a
  whole argument type takes the surplus off itself (`resolve_arg_scanned_tdict`'s
  `want_arity`), which is the peel moved from the consumer, which could not know the
  depth, to the recorder, which does.

This is a `.iface` wire-form change, so it is a version bump — v8→v9, and the second
bump to change this exact field (v3→v4 changed the constraint pair's shape). Old
interfaces are rejected loudly, as every earlier bump rejects its predecessor.

With the invariant holding and §4 enforced, pattern depth equals concrete depth by
construction, and `drop_surplus_args`, `te_app_depth` and the subtraction are gone
from both copies of `unify_type_expr`. The `| _ -> acc` fallthrough stays: of the
three `unify_type_args_list` callers only `resolve.check_context` has a position to
report with, and it now checks the depths and reports a mismatch as
`internal: instance <key> is written at depth N but dispatches on a type at depth
M`. The other two return an `Evidence` and a `Maybe` list with no position between
them, which is why the check lives at the caller rather than inside the unifier.

## 6. Implementation

All eight steps are implemented.

1. **`@tyarity:<TypeName>`** in `infer.sprout` beside `mark_declared_types`: the
   declared parameter count of every `TypeDecl` / `RecordDecl` / `WrapDecl`, same
   sentinel idiom as `@arity:` / `@linear:` / `@type:`. Plus the fixed table for
   `c_runtime_type_names` and 0 for `primitive_type_names`.
2. **`classvar_arity` from two sources**, unified across a class's methods and its
   transitive superclasses. The single-source plan this replaces rested on a
   precondition that is false: `pre_scan_fn_decls` has no `ClassDecl` case, so
   `register_class_methods` and `register_class_method_markers` run per-decl inside
   `typecheck_decls_inner`, after the whole chain. A module's own method schemes are
   therefore NOT in `scanned_env` where the check belongs. Moving the check later is
   the wrong repair — it would report a body's type error ahead of the unsatisfiable
   class declaration that caused it. Two sources is what the same function already
   does one line above, for the same reason: `declared_types = own_type_names ++
   type_names_from_env(...)`.
   - Own classes: the `ClassMethodSig` TypeExprs through `type_from_ast(te,
     qual_env)`. `qual_env` is the alias env `pre_scan_fn_decls` is already handed,
     so a method's signature resolves aliases exactly as `register_class_method`
     resolves them — both sources yield a `types.Type` spine and one walker serves
     both.
   - Imported classes: from env, with no new marker. `@class:<method>` keys the
     method name, its body is `TConst(class)` and its `scheme_vars` are the class
     parameters, so one pass over `dict_entries` builds class → (vars, methods) the
     way `class_names_from_env` already builds the class set, and `dict_get(method,
     env)` gives the spine. `encode_scheme` round-trips `scheme_vars`, so the marker
     survives the interface codec.
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
5. **Carry the recorder invariant** (§5) by widening the compound-head token to
   `#app:<Head>:<args>`, one entry per written argument, and bumping the `.iface`
   format v8→v9. The arg-scanned recorder then rebuilds the constraint's own head
   at this call (`resolve_compound_head_tdict`) and only falls back to scanning
   when an argument is `#any` or still polymorphic — in which case the token's
   argument COUNT truncates the scanned type to the head's depth.

   The three producers collapse to one. `constraint_source_tokens` existed only to
   key by NAME where the others key by POSITION, and `constraint_var_token` already
   falls back to the name when the variable has no index — so passing `Nil` binders
   IS the name-keyed behaviour, and it is now a one-line delegation. The generalized
   producer keeps its own writer (`canonical_compound_head_token`) because its
   arguments must be canonicalized through `prog_to_fresh`/`s2` exactly as the head
   variable beside them is; a source name would not survive the renaming.

   `resolve_concrete_head_tdict` needs no change, and §4 is why: a bare
   constructor-headed constraint reaches it only with residual arity equal to
   `classvar_arity`, so its depth-0 `TypeName(head)` already IS the class variable's
   binding. `class_var_dispatch_type`'s four fallbacks to the whole argument type
   would break the invariant, but are measured defensive — instrumented with a
   panic, none fired on 462 corpus files or on the compiler's own source.
6. **Delete the peel** — `drop_surplus_args`, `te_app_depth` and the subtraction —
   from both copies of `unify_type_expr`. Done, and the peel's job did not vanish:
   it moved to the recorder, which knows the constraint's written depth where the
   consumer only knew a per-call difference between two spines. The fallthrough
   stays a fallthrough; the located check lives in `resolve.check_context`, the one
   caller with a position — see §5 for why the other two have none.
7. **Delete the unbound-variable test** — `unbound_context_var`,
   `unbound_var_in_list`, `unbound_var_in_te` and the branch that reports them.
   Safe only once step 4 covers constraint heads. `check_context_subs` itself stays:
   substituting an instance context at its concrete arguments and recursing is a
   job that still needs doing, and its name is where the reason this test is gone
   belongs.
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
- **Semantics:** for a program accepted both before and after, one change, and it is
  a fix: a compound-head constraint now takes its dictionary from the parameter it
  names rather than the first argument sharing its head constructor. Where those
  differ the old choice was a wrong witness, not a second valid reading.
- **On-disk format:** `.iface` v8→v9. Old interfaces are rejected loudly, so a
  stale cache fails rather than mis-decoding.
- **Type system:** one new static restriction, on instance declarations and on
  constructor-headed constraints.
- **Errors:** four new messages, all at the offending declaration — a head at the
  wrong arity (instance and constraint wordings) and a class disagreeing with itself
  (between its own methods, or with a superclass). They are normative in spec §"An
  instance head must leave exactly as many arguments unapplied". They replace a
  call-site message that hard-coded one cause for a condition with several and
  printed the internal key (`Boxed_main.Tagged`) rather than the instance as
  written. The position is the declaration's: `ClassMethodSig` carries no
  `SourcePos`, so a method-level `where` reports at its enclosing class.

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

All of the fixtures below are in tree, and each was run against the pre-check
compiler to record what it does today — the five `type_error` ones are accepted
without complaint, and the `run` ones are stated per fixture.

- Conformance `type_error`, all five accepted today: `instance_head_oversaturated`
  (`Tagged k v` under a `t Int` method); `instance_head_oversaturated_deep` (the
  `Tri a b c` shape, which prints a raw pointer rather than segfaulting);
  `instance_head_undersaturated`; `constraint_head_bare_ctor_arity` (`where Pretty
  Tagged`); `class_var_arity_disagreement` and
  `class_superclass_var_arity_disagreement`, which separate the two ways a class can
  contradict itself — between its own methods, and against a superclass.
- Conformance `run`: `instance_context_var_absent_from_head`, rejected today and the
  regression this branch must undo; `class_superclass_var_arity_agrees` and
  `instance_head_c_runtime_arity` (heads over `Ref` and `Vector`, whose arity has no
  decl and no marker), both passing today and guarding against a check that rejects
  what it should accept.
- `tests/conformance/run/dispatch_compound_head_polymorphic_caller.spr` covers a
  polymorphic caller of the compound-head forwarding function. An earlier review
  claimed this aborts; it does not, in either of two variants, so it is a green
  guard rather than a reproduction.
- Three `run` fixtures pin what the widened token buys, each a golden stdout that
  says which parameter's dictionary was used. All three were confirmed red first,
  and the third was red only *after* the peel came out — it is the regression that
  found the argument COUNT to be load-bearing, not just the positions:
  - `dispatch_two_candidates_second_constrained` — `second_only(x: Tagged k Int, y:
    Tagged j Int) where Boxed (Tagged j)`; printed `false=7`, now `q=7`. Quarantined
    in `run/XFAIL` for one afternoon; the self-healing gate reported it fixed.
  - `dispatch_compound_head_two_args` — the same with TWO written arguments, so one
    token per argument is what rebuilds `Trip r s`; printed the raw pointer
    `4333309080=9`, now `ok=9`.
  - `dispatch_compound_head_concrete_arg` — `where Sh (Box String)`, whose argument
    is no variable, so the token has only its count. Printed `4362308680#3` with the
    peel removed and no count; now `hi#3`.
- `tests/stdlib/compiler/test_scheme_roundtrip.spr` pins the widened token through
  the wire codec — a comma and two colons inside one unquoted atom — and
  `test_iface_file_roundtrip.spr` pins v8 as rejected, alongside v1–v7.
- `tests/stdlib/test_instance_head_arity.spr` holds the shapes the rule must ACCEPT:
  the class variable only in a return type, only under a superclass, and constrained
  nowhere at all — the last one being why an unconstrained class admits any arity.
- The 64-instance prelude sweep is mechanical rather than eyeballed, and needs no
  fixture of its own: `--emit-ir` over `stdlib/compiler/compile_driver.sprout`
  bundles the prelude and the whole compiler through the check, and every
  conformance and stdlib test does the same for the prelude. An earlier eyeballed
  count of this said 58.
- `tests/stdlib/compiler/test_repl_instance_head_arity.spr` is the env-path
  regression through `compile_source_with_cache`: a session instance over an
  IMPORTED class (`Functor`) and over an IMPORTED type (`Result`, arity 2, rejected
  at 2-where-1-is-wanted). Both rejections were confirmed to carry the arity
  message rather than failing for an unrelated reason.
- `classvar_arity` units are the fixtures above: only in a return type and only
  under a superclass in `tests/stdlib/test_instance_head_arity.spr`, two depths in
  `type_error/class_var_arity_disagreement`.

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
- Two same-class constraints whose heads differ only in their arguments, which needs
  the hidden-dictionary key to carry those arguments — see §12.

## 12. Revision 4: what the review found

An ensemble review of revision 3 confirmed five defects. Each fix below has a
regression test that was run against a revision-3 compiler and seen to fail first.

**Two that revision 3 introduced.**

1. **Same-named classes in one bundle collided.** The class-depth table keyed on the
   SHORT name, so two modules each declaring `class Held` let the later declaration
   decide both arities — rejecting one module's well-kinded instances, and silently
   admitting the other's ill-kinded ones, which is the runtime abort this rule exists
   to prevent. The table now keys the name exactly as declared, as the sibling
   `type_arities` already did for types and for the same reason; lookups fall back to
   the short name for the env path, which has only that. The arity check also gets its
   own superclass map keyed and named verbatim, since a shortened superclass reference
   resolves to whichever class won the short key. Test:
   `tests/stdlib/test_class_name_collision.spr`, with `testsupport/held_class.sprout`.

2. **A fixed compound-head argument was still guessed.** `where Sh (Box String)` says
   which parameter to dispatch on exactly as `where Sh (Box a)` does, but the
   canonicalizer recorded a concrete argument as `#any` and the reader then abandoned
   the whole token, leaving the choice to the scan — which takes the first parameter
   the head constructor matches. With two such parameters the constraint read one's
   payload through the other's witness, contradicting the spec paragraph this doc's
   rule added. A constructor argument now records its own name. Test:
   `tests/conformance/run/dispatch_compound_head_concrete_arg_two_candidates.spr`.

**Three holes it left.**

3. **Two same-class constraints can share one dictionary slot.** Hidden dictionary
   parameters are keyed by class plus each argument's outermost constructor, so
   `where Boxed (Tagged k), Boxed (Tagged j)` is one key for two obligations: the
   caller passes two dictionaries and the body reads one twice. This miscompiled
   before the rule too, printing a wrong answer; once a compound head dispatches on
   the parameter it names the two dictionaries genuinely differ, and the wrong read
   became a SIGSEGV. `check_indistinct_constraints` now rejects the shape at its
   declaration. It is a rejection, not a repair: making it work means putting the
   arguments' identity into that key in all four places that build it — the
   dictionary-passing key format, deferred above and filed in `BACKLOG.md`. Test:
   `tests/conformance/type_error/same_class_heads_share_dict_slot.spr`.

4. **A type alias claimed an arity it does not have.** `type_arities` recorded an
   `AliasDecl`'s own parameter count, which is not its residual arity — `type alias
   Half a = Tagged a` takes one parameter and leaves one more unapplied — so a valid
   instance was rejected with a number nobody declared. Alias heads are a §2 non-goal,
   so the table now records nothing for them and the check skips. Test: the alias case
   in `tests/stdlib/test_instance_head_arity.spr`.

5. **An instance method's `where` was never checked.** `decl_arity_error` dropped the
   `List InstanceMethodImpl`, so a head rejected on the class signature compiled clean
   when restated on the implementation — a hole in exactly the surface §4 note 4 and
   §6 step 4 claim to cover. Test:
   `tests/conformance/type_error/instance_method_where_head_arity.spr`.

One further finding was reported and refuted: `merge_class_depths` appending imported
depths into the entry `class_own_part` reads. The merge is real, but no live entry
point feeds imported `@class:` markers in as env schemes — every one bundles imports
as decls, and the callers that would are marked RETIRED and unbuilt.
