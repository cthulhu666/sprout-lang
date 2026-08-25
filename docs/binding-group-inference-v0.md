# Binding-group inference — completing Hindley–Milner at the top level (v0)

> **Status: complete.** All four parts of §6 are implemented and gated.
> `docs/spec-v0.md` §7 rule 16 carries the normative rule, and no longer carries
> an order-dependence carve-out. **Polymorphic recursion is supported as of
> 2026-08-25 for a complete, constraint-free signature (§8)** — §8 also records
> the two earlier, wrong claims this document made about it.

## 1. Problem statement

`README.md:43` promises:

> **Full type inference** — Hindley–Milner; most code needs no annotations.

The top level did not implement Hindley–Milner, and the missing part was not a
corner case — it was the step HM performs on every group of declarations.

A call to a function declared *later* in the file, whose return type was omitted,
type-checked against whatever the caller wanted, and then miscompiled:

```sprout
module main

fn report(xs: List Int) -> String = "count=" ++ summarize(xs)

fn summarize(xs: List Int) = list_length(xs)

fn main() -> Unit !{IO} = print(report(Cons(1, Cons(2, Nil))))
```

Measured on the pre-change compiler, 2026-08-24:

```
$ … --phase check …
main.report    : List Int -> String
main.summarize : List Int -> Int      <-- correct, and contradicts what it accepted

$ ./unann_repro
[sprout] SIGSEGV
2   unann_repro   str_concat + 60
3   unann_repro   main.report + 140      exit 139
```

### Why

`pre_scan_fn_decls` (`infer.sprout`) records a type for every function before any
body is checked. With the return type omitted there was nothing to record, so
`scheme_from_fn_parts` synthesised a placeholder — `"_unann"` for a missing
return, `"_unann_" ++ name` for a missing parameter — and the collectors put it
into the scheme's **quantified** variable list.

That last word was the entire defect. A quantified variable is refreshed at every
use, so each caller got its own copy to unify with whatever it liked, and nothing
ever reconciled those copies against the callee's real type. The real type landed
later, when `typecheck_decl` rebound the name after the body check.

## 2. The gap, stated exactly

Haskell 2010 §4.5.2, on typing a declaration group:

> *"a type with **no universal quantification** is inferred for each variable
> bound in the group. Then, all type variables that occur in these types are
> universally quantified unless they are associated with bound variables in the
> type environment; this is called generalization."*

Two steps, strictly ordered: infer the group **monomorphically**, then generalize
at the group boundary. Sprout inverted the first step — it published a
*universally quantified* type for a binding whose type had not been inferred yet.

The placeholder was therefore not wrong for being blank. It was wrong for being
**generalized**. A non-quantified blank shared across a group is exactly what HM
prescribes.

## 3. Evidence

Six probes, run before the change and again after it.

| # | Program | Before | After |
|---|---|---|---|
| 1 | `report` declared above `summarize` (above) | accepted → SIGSEGV, exit 139 | ``Return type mismatch in main.summarize: Type mismatch: Int vs String`` |
| 2 | same, `summarize` moved above `report` | rejected | rejected |
| 3 | `fwd()` used at `String` in one caller and `Int` in another, `fwd` returns `Bool` | all three accepted (`main.fwd : Bool`) | ``Return type mismatch in main.use_as_int: Type mismatch: String vs Int`` |
| 4 | `is_even`/`is_odd`, unannotated, consistent | accepted | **accepted, inferred, no annotations** |
| 5 | `a`/`b` cycle, first member commits second to the wrong type, second internally consistent | accepted → `runtime error: builtin str_concat: null input` | ``Return type mismatch in main.b: Type mismatch: Int vs String`` |
| 6 | unannotated **self**-recursion (`countdown`) | correct | unchanged |

Probe 3 shows the placeholder was genuinely quantified, not one shared unknown.
Probe 5 shows mutual recursion had the same hole — probe 4 only *looked* fine
because the types happened to agree.

All six are pinned as fixtures: probes 1, 3 and 5 in
`tests/conformance/type_error/unannotated_*`, probes 4 and 6 in
`tests/conformance/run/unannotated_*`.

### Probe 6 is the anchor for the design

`fn_body_env` (`infer.sprout`) binds a function's own name inside its own body as

```sprout
dict_set(name, types.scheme_with_constraints(types.mono(inst_type), constraints), env)
```

`types.mono` (`types.sprout:342`) builds `Scheme(Nil, …)` — an **empty binder
list**, i.e. no universal quantification. That is precisely the §4.5.2 rule,
correctly implemented, for a binding group of size one. It is why unannotated
self-recursion both infers correctly and rejects the inconsistent case.

**So the work was not to invent binding-group inference. It was to generalize an
existing, correct, one-member implementation to N members.**

## 4. Goals and non-goals

**Goals**

- No program that type-checks may miscompile through this path.
- Unannotated mutual recursion is **inferred**, not rejected — the README promise
  applies to it too.
- The global `_unann` placeholder is deleted, not guarded.

**Non-goals**

- Requiring annotations anywhere except where HM genuinely cannot infer (§8).
- Changing how a single declaration's body is inferred. `check_fn_body` keeps its
  behaviour; only what surrounds it changes.
- The unrelated lowercase-annotation defect (`BACKLOG.md`), which shares the
  "wrong error, wrong place" symptom but not this mechanism.

## 5. Prior art

Read from each language's own reference on 2026-08-24; none from memory.

| Language | Order-independent top level? | Top-level inference? | Rule |
|---|---|---|---|
| **Haskell 2010** | Yes | Yes | §4.5.1: *"A declaration group is a minimal set of mutually dependent bindings. Hindley-Milner type inference is applied to each declaration group in dependency order."* §4.5.2 as quoted in §2. |
| **OCaml 5.2** | **No** | Yes | *"The scopes of the bindings performed by the definitions extend to the end of the structure."* Forward references are unbound; cycles need explicit `let rec … and …`. |
| **Rust** | Yes | **No** | Parameter types required by the grammar; an omitted return type *is* `()`, never inferred. |
| **Scala 2.13** | Yes | Partial | §4.6: *"If the function definition is not recursive, the result type may be omitted…"* — a recursive function must state its result type. |

No surveyed language combines order-independent top-level definitions with
unrestricted top-level inference *without* dependency-ordered group inference.
Haskell is the only one that keeps both, and it keeps them by doing exactly what
§6 describes. Rust, OCaml and Scala each buy order-independence or inference by
giving the other up — which is what the stopgaps in Appendix A amount to, and why
none of them satisfies `README.md:43`.

## 6. Design

Four parts, all standard HM.

1. **Dependency analysis.** Group top-level declarations into binding groups —
   each group a minimal set of mutually dependent declarations — and order the
   groups so a group's dependencies are checked before it.
2. **Monomorphic assumptions within a group.** Give every member a fresh,
   *non-quantified* type, so every use of a member sees the same variable.
3. **One substitution spanning the group.** Infer each member's body threading a
   shared substitution, so what one member learns about another is recorded
   rather than discarded.
4. **Generalize at the group boundary.** Once every body in the group is typed,
   quantify the remaining free type variables — releasing the group's own
   assumptions and nothing else.

### The substitution needs only GROUP scope

`typecheck_decls_inner` used to pass `subst` through **unchanged** at every step,
so each declaration was inferred against a fresh empty substitution and only its
final answer survived. Group inference needs those threaded — but only *between
members of one group*. Across groups the callee is already generalized, which is
exactly what a generalized scheme is for.

The one signature change that required: `check_fn_body` computed its final
substitution internally and dropped it. It now returns it, and `TypedDeclOk`
carries it to the next declaration.

## 7. What landed, and what did not

Parts 2, 3 and 4 are implemented. Part 1 — dependency analysis — is not, so the
module is treated as a single group for part 3 while each declaration still
generalizes at its own boundary in **source** order.

| Part | Status |
|---|---|
| 1. Dependency analysis | **Landed.** `referenced_names` builds the edge set, `sccs_in_dependency_order` partitions it (iterative Kosaraju), `group_plan` returns the components dependencies-first; see §7.3 |
| 2. Monomorphic per-declaration assumptions | **Landed.** `unann_ret_var` / `unann_param_var` mint `_unann@<owner>` and `_unann@<owner>/<param>`; the collectors no longer put them in the binder list, so `instantiate` leaves them alone and every use shares one variable |
| 3. Shared substitution | **Landed.** `check_fn_body` returns its final substitution, `TypedDeclOk` gained a fourth field, and `typecheck_decls_inner` threads it |
| 4. Generalization boundary | **Landed.** `own_unann_vars` subtracts the declaration's own placeholders from `env_ftv` at its generalization point — see §7.1, the part that is easy to get wrong |
| Placeholder deletion | **Landed.** With the placeholders out of the binder list, `rigidity_violation`'s `_unann` skip had nothing to skip and was removed |
| Group walk | **Landed.** `typecheck_groups` / `typecheck_fn_group` / `commit_group_members` hold the env fixed across a group, thread the substitution, and generalize at the boundary; `typed_decls_in_source_order` re-keys the output so no plan can move emitted code |

Instance methods are deliberately excluded from the thread: two instances of one
class implement the same method *name*, so a shared placeholder would force their
types equal. `scheme_from_fn_parts` gives them a `"#inst/"` owner prefix and the
`InstanceDecl` arm passes its incoming substitution through unchanged. The two
guards are a pair; removing either alone reintroduces the collision.

### 7.1 The subtlety: `env_ftv` and the declaration's own assumption

Unquantifying the placeholder makes it a **free** variable of the scheme
`pre_scan` publishes. `env_ftv` is built from `ftv_env` over that pre-scanned
environment, so every unannotated declaration's placeholder now appears in it —
and `generalize` computes `ftv(type) \ env_ftv`. Left alone, that means a
declaration can never quantify its own placeholder: `fn id(x) = x` infers the
monomorphic `_unann@id/x -> _unann@id/x` instead of `forall a. a -> a`.

The fix is *not* to strip all placeholders from `env_ftv`. Another declaration's
placeholder appearing in your type is a genuine commitment — quantifying over it
would reopen the same hole from the other side, since that declaration's type is
not yet fixed. Only the declaration's **own** placeholders come out, at exactly
the point §4.5.2 says they should: the generalization boundary.

The same reduced `env_ftv` must reach `check_fn_body`, because
`canonicalize_constrained_constraints` computes a constraint's binder *position*
with the identical ftv-difference. The two disagreeing would misindex every
hidden dictionary.

Regression: `tests/stdlib/compiler/test_compiler.spr`, which already asserted
`fn id(x) = x` is `forall a. a -> a` and `fn double(x) = x * 2` is `Int -> Int`.
Those two assertions caught this on the first full run.

### 7.2 The gap that dependency ordering closed

Making the placeholder monomorphic fixed the soundness hole but left a
completeness one: an unannotated function that was **forward**-referenced stayed
monomorphic at its uses, because those uses were inferred before its own body
pinned it. Measured on the intermediate compiler — the same program, twice, with
only the declaration order changed:

```sprout
# use before define -> REJECTED          # define before use -> inferred
fn use_two() -> Unit !{IO} =             fn fwd(x) = x
  do
    print(fwd(1))                        fn use_two() -> Unit !{IO} =
    print(fwd("s"))                        do
                                             print(fwd(1))
fn fwd(x) = x                                print(fwd("s"))
```
```
6:14: ERROR: check: Call type mismatch:   main.fwd : forall a. a -> a
  Type mismatch: Int vs String            main.use_two : Unit !{IO}
  in function main.use_two                main.main : Unit !{IO}
```

Define-before-use was fully polymorphic; use-before-define was not — precisely
the behaviour HM has without §4.5.1, and what OCaml imposes unconditionally.

Both columns now read `main.fwd : forall a. a -> a`. Two further consequences,
both pinned as fixtures:

- **Diagnostics stopped moving.** Probe 1 used to report at `summarize` and its
  reordered twin at `report`. Both now report at `report` — the code that is
  actually wrong — with byte-identical text.
  `type_error/unannotated_forward_return` and `unannotated_backward_return` are
  the same program in both orders and share one `.err`; they only test anything
  as a pair, so keep the two files identical.
- **Mutually recursive unannotated functions generalize symmetrically.** Under
  per-declaration generalization the first member of a cycle could only quantify
  its *own* placeholder — a sibling's is a live commitment — so it came out
  monomorphic, with the internal placeholder name visible in a user-facing type:

  ```
  main.ping : Int -> _unann@main.pong/x -> _unann@main.pong/x
  main.pong : forall a. Int -> a -> a
  ```

  Both are now `forall a. Int -> a -> a`.
  `run/unannotated_mutual_polymorphic` pins it, and it is also the fixture that
  exercises the N>=2 group fold.

### 7.3 How the partition is built

Landed in two steps on purpose. The first put the group machinery in with
`group_plan` returning one group per declaration in source order — a change that
was *supposed to change nothing*, and could therefore be proved by identity
(byte-identical IR from the old and new compilers on all 60 golden files), which
is a far stronger check than "the tests still pass". Dispatch in
`typecheck_group` is by declaration *kind* rather than group *size* so that proof
covered the machinery instead of stepping around it: every `FnDecl` runs the group
path, at size one. The second step replaced `group_plan`'s body, leaving a small
diff whose entire blast radius is the ordering.

**Barriers.** Only `FnDecl`s are reordered, and only among themselves; every other
declaration is its own group, in place, and no `FnDecl` crosses it. This is what
the environments allow rather than caution:

- `RecordDecl`, `ClassDecl`, `InstanceDecl` are registered by `typecheck_decl` as
  it walks, not by `pre_scan_fn_decls`.
- A top-level `let` is not pre-scanned either — forward-referencing one is
  `Unknown variable` today (measured), and this must not change that.
- `AliasDecl` is a barrier *despite* being pre-scanned. `pre_scan`'s `alias_env`
  is used only to build schemes inside `pre_scan`; `typecheck_decls` starts the
  body walk from `qual_env` and re-registers aliases as it goes, so a `FnDecl`
  checked above an `AliasDecl` would rebuild its own `decl_scheme` against an
  `alias_env` that has not seen it. An earlier note in `group_plan` claimed
  aliases could be crossed; reading the two `alias_env` paths says otherwise.

Edges are restricted to the segment between two barriers, which is exact, not an
approximation: a reference to an earlier segment names something already checked,
and one to a later segment names something the barrier makes unreachable anyway.

**The edge set.** `referenced_names` walks `ast.Expr` tracking shadowed names
(lambda parameters, pattern binders, `do let`). `where` and `let … in` need no case
— the parser desugars both to a single-arm `MatchExpr`. The walk lists every
`ast.Expr` constructor with **no** `| _ ->` catch-all, because the two ways to be
wrong are not symmetric: over-approximating merges groups (less general, never
unsound, and free for the annotated declarations that dominate any program), while
under-approximating puts a declaration before its dependency, which is the hole
this all exists to close. A catch-all would fail in the dangerous direction,
silently, the next time an expression form is added.

**The algorithm.** Iterative Kosaraju, `sccs_in_dependency_order`. Two things ruled
out the SCC walk that already existed. `pb_scc_of` (`ast_to_ir.sprout`) computes a
component by calling `mutual_reaches` **pairwise** — 2n graph searches per node —
which is fine for the mutual-TCO pre-pass and not for this: the largest reorderable
run in this repo is **551** functions (`infer.sprout` itself, measured
2026-08-25), where pairwise reachability is around a billion operations on the
compiler's own hottest file, every build. And it must be iterative: 551 recursive
DFS frames is the stack the `stack-overflow-smoke` gate exists to police, at a
depth set by user code rather than by us.

**The invariant.** Groups must partition `0..n-1` exactly once — a missing index
silently drops a declaration from the emitted program, a duplicate emits it twice.
It holds by construction (each declaration is a barrier singleton or in exactly one
segment; an SCC partition covers its nodes) given unique names, which
`check_duplicate_fn_decls` has already guaranteed. `checked_plan` verifies it
anyway and falls back to source order if it fails: reordering improves
*completeness*, so losing it degrades diagnostics, whereas dropping a declaration
corrupts output — worth trading the first for certainty about the second.

## 8. Polymorphic recursion — supported for a complete, constraint-free signature

**History, because both earlier versions of this section were wrong and the
corrections are the useful part.** The first draft said polymorphic recursion
"requires a signature" and was the last place a top-level annotation was needed —
carried from Haskell's rule without being run. The second draft corrected that to
"unsupported, and an annotation does not help", which was true of the compiler as
it then stood. This third version records the implementation. It also fixes a
citation both earlier drafts carried: the Haskell text is §4.4.1 (*Type
Signatures*), not §4.5.2 (*Generalization*) — §4.5.2 is the declaration-group rule
this document is otherwise about, and polymorphic recursion is a *signature*
feature.

**Polymorphic recursion** — a function calling itself at a *different* type than
it was called with — is not inferable. Not "hard": inference for it is equivalent
to **semi-unification**, which is undecidable (Henglein, *Type inference with
polymorphic recursion*, TOPLAS 15(2) 253–289, 1993; Kfoury, Tiuryn & Urzyczyn,
ibid. 290–311). Every language that permits it therefore demands an annotation.

### 8.1 Prior art

| Language | Rule | Source |
|---|---|---|
| **Standard ML** | Forbidden. *"each use of a recursive function in its own body must be assigned the same type"* | [Definition of SML (Revised)](https://smlfamily.github.io/sml97-defn.pdf), rule 26, Comment (26) |
| **Haskell 2010** | Permitted with a type signature: *"Type signatures can also be used to support polymorphic recursion"* | [Report §4.4.1](https://www.haskell.org/onlinereport/haskell2010/haskellch4.html) |
| **OCaml** | Permitted with an *explicitly quantified* annotation: `let rec depth : 'a. 'a nested -> int` | [Manual, Polymorphism](https://ocaml.org/manual/5.2/polymorphism.html) |
| **Sprout before this** | Forbidden — behaviourally identical to SML | — |
| **Sprout now** | Permitted for a complete, constraint-free signature | §8.2 |

OCaml needs the explicit `'a.` because a bare OCaml annotation is
unification-variable-typed. Sprout's annotated variables are already rigid, so a
complete Sprout signature *is* the OCaml explicit form; no new syntax is needed.

### 8.2 The rule

Inside its own body, a declaration's own name is bound to its **declared,
quantified scheme** when both hold:

1. **The signature is complete** — every parameter annotated *and* the return
   annotated.
2. **The declaration is constraint-free** — no `where` clause.

Otherwise it keeps the monomorphic binding, exactly as before. Both conditions
are forced, not cautious.

**Why (1).** An omitted slot is a `_unann@` placeholder: one variable shared by
every use of the declaration, deliberately left out of the binder list so that
pinning it is a commitment rather than a per-call guess (§2). Quantifying a scheme
that still contains one hands each use its own copy again and re-opens the
forward-reference soundness hole the placeholder exists to close. The predicate is
`own_unann_vars(...) == Nil` — the same one the generalization boundary uses.

**Why (2).** A `#pos:<k>` constraint token means "generalized at `type_vars`
position k". Under the monomorphic binding `type_vars` is `Nil`, `#pos:k` decodes
to nothing, and the self-call falls through to the enclosing `@fwd` markers — it
**forwards the caller's dictionaries**, which is right for a same-type recursive
call. That fallback is load-bearing. Bind the declared scheme and `#pos:0` decodes
successfully, to a *freshly instantiated* variable, which resolves against the
wrong instance and returns a **wrong answer rather than an error**. Caught by
`tests/stdlib/compiler/test_typeclass_recursive_forwarding.spr` during
implementation, not by inspection.

Lifting (2) is a constraint-solver feature, not a different binding: a self-call
at `Nest (a, a)` under `where Eq a` needs `Eq (a, a)`, a dictionary that does not
exist at the call site and has to be deduced against the instance environment —
what GHC does, and what it reports as *"Could not deduce"* when it cannot.
Tracked in `BACKLOG.md`.

### 8.3 What works

```sprout
type Nest a =
  | NestNil
  | NestCons a (Nest (a, a))

fn nest_size(n: Nest a) -> Int =
  match n with
  | NestNil -> 0
  | NestCons _ rest -> 1 + 2 * nest_size(rest)
```
```
main.nest_size : forall a. main.Nest a -> Int
```

**Mutual** polymorphic recursion works too, and the reason is worth recording
because the obvious guess is wrong. Two mutually recursive functions sit in ONE
binding group, and a group's members share monomorphic assumptions while it is
checked (§4.5.2) — which suggests a peer should look monomorphic from inside.
It does not: `pre_scan_fn_decls` publishes each declaration's *declared* scheme
before the group is walked, so a fully annotated group member is visible to its
peers already quantified. The shared-assumption rule bites only through `_unann@`
placeholders, and a complete signature leaves none. An annotation is a promise
available to the whole group, not merely to callers outside it.

Fixtures: `tests/conformance/run/polymorphic_recursion{,_mutual}.spr`, and the two
boundaries at `tests/conformance/type_error/polymorphic_recursion_{partial,constrained}.spr`.

### 8.4 A latent effect bug this fixed

`types.mono` hardcodes `EffectPure`, and `call_effect_of` reads a scheme's effect
field only when `argc <= 0`. So a **nullary** self-call took its effect from a
scheme that claimed purity regardless of the declaration:

```
fn spin() -> Unit !{IO} = spin()

before:  effect ok main.spin: declared !{IO}, inferred pure
after:   effect ok main.spin: declared !{IO}, inferred !{IO}
```

The direction is safe by construction: a "gap" is *declared pure, inferred
`!{IO}`*, and for this mechanism to create one a declaration would have to be both
impure and pure at once.

## 9. Risks and how they were checked

- **Emitted IR.** No codegen moves, in either step, including after 3,050
  declarations are reordered by SCC — because groups are *checked* in dependency
  order and *emitted* in source order (`typed_decls_in_source_order`). Verified:
  `just ir-golden-diff` reports 0 differences across the golden corpus, run
  against a **reseeded** stage-1 (per `AGENTS.md`, the gate is vacuous otherwise).
- **`env_ftv` drift.** §7.1. Guarded by the existential/rigidity suite and by
  `test_compiler.spr`'s generalization assertions.
- **Dictionary markers.** `@fwd:` / `@eta_fwd:` keys are globally unique against
  one `InferState`, so threading cannot make them collide; the binder-position
  encoding is kept consistent by using one `env_ftv` value throughout (§7.1).
- **Bootstrap.** This changes the compiler that compiles the compiler. The seed
  reached a fixed point at iteration 2 and `just verify-bootstrap-fixed-point`
  passes. No 2-step bootstrap was needed — the parser is untouched.
- **Substitution growth.** The thread now spans a module rather than a
  declaration. `BACKLOG.md` records a latent non-termination in
  `unifier.apply_full_subst` (no visited set); a larger shared substitution
  raises, but does not create, that exposure. Unchanged by this work and still
  open.
- **Effects.** `check_fn_body` drops its effect substitution exactly as it used
  to drop its type substitution. Whether omitted effect annotations have the same
  shape of hole is not answered here; it is filed in `BACKLOG.md`.

## 10. Tests

- `tests/conformance/type_error/unannotated_forward_return` — probe 1.
- `tests/conformance/type_error/unannotated_forward_two_types` — probe 3.
- `tests/conformance/type_error/unannotated_mutual_inconsistent` — probe 5.
- `tests/conformance/run/unannotated_mutual_recursion` — probe 4, the case both
  stopgaps in Appendix A would have rejected.
- `tests/conformance/run/unannotated_self_recursion` — probe 6, guarding the
  size-one case that already worked.
- `tests/conformance/type_error/unannotated_backward_return` — probe 1's twin in
  the opposite declaration order, sharing one `.err`: the pair is the
  order-independence test.
- `tests/conformance/run/unannotated_forward_polymorphic` — an unannotated
  function used at two types above its own declaration (§7.2).
- `tests/conformance/run/unannotated_mutual_polymorphic` — symmetric
  generalization of a mutually recursive unannotated pair, and the fixture that
  exercises the N>=2 group fold.
- `tests/stdlib/compiler/test_compiler.spr` — pre-existing, and the guard that
  caught §7.1.

## 11. Spec and docs

- `docs/spec-v0.md` §7 rule 16 carries the normative rule: an omitted parameter
  or return type is one monomorphic variable shared by every use of that
  declaration, generalized at the declaration boundary. Its closing paragraph
  states the order dependence in §7.2 as a v0 limitation. §1's inference bullet
  now points at that rule instead of saying "where needed".
- `README.md:43` needs no change — this is what makes it true.

---

## Appendix A — stopgaps considered and rejected

Recorded because both were seriously proposed, and the reasons they fail are the
reasons §6 is the right shape.

**A1. Reject the forward reference.** Mark placeholder-bearing schemes; reject a
call resolving to a still-unchecked marked declaration ("add a return type, or
move it above"). Smallest sound change, zero measured in-tree breakage — across
756 files only 2 production functions omit a return type (`prelude.sprout`
`rcompose`, `lcompose`) and 4 omit a parameter type (`compiler.sprout`, the
`cache` param). **Rejected because it also rejects unannotated mutual
recursion**, which is ordinary code HM infers without help, and because requiring
annotations to avoid a miscompile is precisely what `README.md:43` says Sprout
does not do.

**A2. Per-declaration placeholders, unquantified.** Rename `_unann` to
`_unann@<qualified fn name>` and leave it out of the binder list. The intuition
is right — the placeholder *should* be monomorphic — but a monomorphic variable
is only meaningful if a substitution spans the declarations that share it. A2 is
§6 with parts 1, 3 and 4 missing; it is where this work started, and the three
missing parts are what the rest of it added.
