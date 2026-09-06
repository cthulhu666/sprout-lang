# Lambda parameter annotations — design v0

**Status:** proposal, awaiting approval. Revision 2 — revised after an independent
review found four defects in revision 1, each reproduced here (§11 records what
changed and why). Supersedes the follow-up in
[`lambda-argument-inference-v0.md`](./lambda-argument-inference-v0.md) §7, whose
motivating example and error text are both stale (§1.3).

## 1. Problem statement

A lambda parameter may be annotated — `\(s: String) -> …` — and the annotation is
parsed into `ast.Param String (Maybe TypeExpr) ParamMode` and then **discarded**.
`make_fresh_param_types` (`infer.sprout:4740`) allocates a fresh variable per
parameter without consulting it, and `extend_env_with_params` (`:4749`) matches it
away as `ast.Param name _ _`.

### 1.1 What that costs

The annotation is decoration that is permitted to lie. This type-checks today:

```sprout
fn shout(n: Int) -> String =
  wrap_it(imath.abs(n))                  # applied to an Int
where
  wrap_it = \(s: String) -> `<${s}>`     # annotation says String
```

Inference solves `s` from the use site and never compares it against what was
written. No diagnostic at any phase.

### 1.2 Where the gap actually bites

Every row was compiled and run against `build/compile_driver_bin_stage1`.

| Form | Annotation honoured | Compiles today |
|---|---|---|
| lambda in argument position | n/a — the callee's slot supplies the type | yes |
| `where f = \(x: T) -> …` | no | yes — the use site solves it |
| `let f = \(x: T) -> …` inside a `fn` | no | yes — same reason |
| do-block `let` step, ADT constructor argument, instance-method body | no | yes |
| **top-level** `let f = \(x: T) -> …`, body carrying an unresolved class constraint | no | **no** |
| **top-level** `let f = \(x: T) -> …`, body without one | no | yes — **at a type more general than written** |
| annotation contradicts the use, any position above | no | yes, wrongly |

Rows 5 and 6 are the subtle pair, and revision 1 collapsed them wrongly. Row 5:

```sprout
let wrap_it = \(s: String) -> `<${s}>`
# ERROR: check: dispatch-verify: ambiguous type variable in `to_string`:
# nothing determines which `ToString` instance to use.
```

Row 6, same shape, no class constraint in the body:

```sprout
let first = \(s: String, n: Int) -> s
…
first(1, 2)     # compiles: `first` generalized to forall a b. a -> b -> a
```

So a top-level annotated lambda does not simply "fail". It fails only when a
class constraint has nothing to resolve against; otherwise it silently generalizes
to something **more general than the annotation states**, and unrelated call sites
type-check that should not.

A `where` binding lives inside a function, so its use sites are in the same
inference scope and unify the parameter for free. A top-level `let` is generalized
alone, so the annotation is the only evidence available — and it is dropped.

### 1.3 Correction to the prior write-up

`lambda-argument-inference-v0.md` §7 offers

```sprout
let wrap_it = \(s: String) -> `<${s}>`   # error: use of undefined value '@to_string'
```

as evidence. The failure is now a located Sprout diagnostic, not a `clang` link
error; and the same shape *inside a function* does not fail at all. The gap is
narrower than that section implies and differently shaped: primarily a **checking**
gap, not an inference gap.

### 1.4 Why this matters now

[`style-guide-v0.md`](./style-guide-v0.md) §10 and
[`idiomatic-sprout.md`](./idiomatic-sprout.md) now require that a lambda needing
`match`/`if`/`let`/`do` be lifted into a named function, on the stated grounds that
naming it *puts the signature back*. A `where`-bound local helper is the lighter
form of that lift, but its signature is currently unchecked — so localizing a
lifted helper today trades an enforced signature for one that can silently drift.
This proposal is what makes the local form carry equal weight.

## 2. Goals and non-goals

**Goals**

- An annotated lambda parameter's annotation is resolved and **unified** with the
  parameter's type.
- A conflict between the annotation and the type the parameter must have is a
  located error, in argument position and everywhere else.
- A top-level `let` bound to an annotated lambda type-checks on the strength of
  its annotations alone, and generalizes no further than they permit.

**Non-goals**

- **Type variables in lambda annotations are REJECTED in v0**, with a diagnostic
  naming the restriction. `\(x: a) -> …` is a compile error, not a vacuous
  acceptance. Rationale in §2.1 — this is the load-bearing change from revision 1,
  which proposed accepting them.
- Annotating a lambda's **return** type. No surface syntax is proposed.
- Local annotations on `where`/`let` *bindings* (`where f: T = …`), excluded from
  v0 by spec §5.1. This proposal annotates lambda *parameters*, already-parsed
  syntax.
- Changing evaluation order, effects, or `ParamMode`/linearity behaviour.
- Fixing the pre-existing top-level-`let` type-variable collision found during
  this review (§11.1). It is filed separately; this proposal must merely avoid
  inheriting it.

### 2.1 Why type variables are rejected rather than accepted

Revision 1 proposed resolving annotations with `let_annotation_type`
(`infer.sprout:397`) "verbatim", describing its type variables as "seeded as its
own fresh variable". **That description is false**, and the mechanism carries a
bug. `build_type_var_dict` (`:376–379`) seeds each lowercase name as
`TVar(tyvar_id(name))` — one variable **per name**, and the substitution threads
across declarations. Reproduced:

```sprout
let p1: Maybe a = Just(1)
let p2: Maybe a = Just("x")
# ERROR: type annotation mismatch for `p2`: Type mismatch: String vs Int
```

Two unrelated declarations' `a` are the same variable. Silently, in the worse case:

```sprout
let bump:  a -> a = \x -> x + 1     # narrows `a` to Int …
let ident: a -> a = \x -> x         # … so `ident` is Int -> Int, not polymorphic
…
ident("hello")   # ERROR: Call type mismatch: Int vs String in function main
```

`ident` is checked clean at a type nobody wrote, and the error surfaces at an
innocent call site.

Lambda annotations would be far denser than top-level `let`s, so wiring them into
this pool unmodified multiplies the bug. Per-lambda freshening would fix *that*,
but not the deeper problem: a reader writing

```sprout
fn f(xs: List a) -> … = list_map(\(x: a) -> …, xs)
```

means *the enclosing function's* `a`. No freshening scheme delivers that — it is
lexically scoped type variables (GHC's `ScopedTypeVariables`), a much larger
change. Accepting the annotation would make it appear meaningful while meaning
something else, which is strictly worse than today's honest inertness.

Rejecting costs nothing: the census (§8) finds **zero** annotated lambda
parameters in either repository, so no code is affected, and the restriction can
be lifted later without breaking anything written under it.

## 3. Prior-art survey

The decision is where an explicit lambda-parameter annotation sits relative to the
type the context expects. Every row is quoted from the language's own reference.

| Language | Rule | Primary source |
|---|---|---|
| **OCaml** | A type constraint "forces the type of *expr* to be **compatible with** *typexpr*" — unification, so a conflict is a type error. | [OCaml manual, Expressions](https://ocaml.org/manual/expr.html) |
| **Haskell (GHC)** | For a pattern type signature whose variables are already in scope, "the signature simply **constrains** the type of the pattern in the obvious way". Also: "pattern type signatures are not implicitly generalised." | [GHC User's Guide, Lexically scoped type variables](https://downloads.haskell.org/ghc/latest/docs/users_guide/exts/scoped_type_variables.html) |
| **Rust** | Closure parameters carry an optional annotation — "the optional type after each pattern is a type annotation for the pattern" — otherwise "inferred from context if not given". | [Rust Reference, Closure expressions](https://doc.rust-lang.org/reference/expressions/closure-expr.html) |
| **TypeScript** | **Divergent.** An explicit annotation "**override[s]** any contextual type" rather than unifying with it; the function is then checked for assignability as a whole. | [TypeScript Handbook, Type Inference](https://www.typescriptlang.org/docs/handbook/type-inference.html) |

**Consensus, and its limit.** The ML family — the relevant prior art for a
Hindley–Milner language — treats the annotation as a *constraint* on an
already-determined type, so a disagreement surfaces as ordinary unification
failure. TypeScript is the outlier, and belongs to a gradual, structurally-typed
setting where `any` is the intended escape hatch; Sprout has none.

The survey settles the **concrete-type** case, which is what §2 adopts. It does
**not** settle the type-variable case, because the languages differ on a dimension
the table does not capture: whether an annotation variable is *rigid* (skolemized,
as GHC's scoped signatures are) or merely another unification variable. Sprout has
no rigid-variable machinery, which is a second, independent reason §2.1 rejects
type variables rather than guessing which behaviour to imitate.

## 4. Implementation overview

`infer_lambda_expected` (`infer.sprout:4675`) runs three steps:

```sprout
fresh_params <- make_fresh_param_types(state, params)       # fresh var per parameter
let param_types = seed_param_types(fresh_params, expected)  # callee slot wins
let env2 = extend_env_with_params(params, param_types, env) # annotation dropped
```

Insert a fourth step between the last two.

1. **Reject** any annotation containing a lowercase type variable (§2.1),
   reusing the existing `collect_te_vars` (`:312`) to detect them.
2. **Resolve** the remaining, concrete annotation with `type_from_ast`.
3. **Unify** it against the parameter's seeded type. In argument position the seed
   is the callee's slot, so this is the check §3 calls for. Elsewhere the seed is a
   fresh variable, so unification *installs* the annotation — which is what makes
   rows 5 and 6 of §1.2 behave.
4. **Thread the resulting substitution into the body's inference.** `:4689`
   currently passes `subst` through unchanged; the unified substitution has to
   replace it, or the annotation will not be visible to the body.

**Plumbing — corrected from revision 1.** Resolution needs an alias environment:
`lookup_type_var` resolves qualified names through `@qualalias:` keys (`:212`), so
without one an annotation naming `mod.Type` or an alias misresolves silently.

Revision 1 proposed adding a `Ref` field to `InferState`. **Do not.** Two things
were wrong with it. First, the premise: `alias_env` is *not* module-constant —
aliases are registered *during* the declaration walk, not up front (`:6706–6711`,
re-registration at `:6961–6971`, with `AliasDecl` acting as a group barrier), so a
once-set `Ref` would either miss every `alias` declaration or break that ordering.
Second, it is unnecessary: `import_aliases_from_env` (`:5838`) already derives an
alias env from the `env` that `infer_lambda_expected` receives, and its own comment
says the markers thereby "reach every annotation position without threading a
parameter through inference". Only `AliasDecl` entries are missing from `env`;
mirror them as `@aliasty:<name>` markers at the two registration sites, following
the existing `@qualalias:`/`@type:`/`@rec:` marker convention.

That removes the `InferState` arity change and the `unifier.sprout` edit entirely.

Revision 1 also mis-costed the rejected option: there are **33 `InferState`
occurrences, all in `unifier.sprout`, and zero in `tests/`** — so the AGENTS.md
fixture-sweep hazard it warned about did not apply. The option is dropped on
correctness grounds, not cost.

**Cost the work lazily**: do all of the above only when a parameter carries
`Just te`. Annotations are absent from both corpora, so the common path pays
nothing.

## 5. Syntax and semantics impact

No syntax changes. `\(s: String) -> …` already parses; only its meaning changes.

Runtime semantics are unchanged: annotations affect checking only and none reaches
codegen. Emitted IR for any program that compiles before and after should be
byte-identical — `just ir-golden-diff` is the gate that checks it.

`ParamMode` note: lambdas share `collect_params` with `fn` declarations, so
`\(x: borrowing T)` already parses and modes already reach lambda types (`:4720`).
`type_from_ast` hardcodes `OwnConsume` on annotation arrows (`:168`) and drops a
`TypeEffect` on a non-arrow annotation (`:177`) — both are pre-existing limits
shared with declaration annotations, unchanged here and out of scope.

**Open, recommendation attached.** Spec §5.3 advertises an unparenthesized
`\s: String -> …` shorthand. It is a parse error today (`Expected -> at 6:15`).
**Recommend correcting the spec rather than the parser**: the annotation's type may
itself contain `->`, so `\s: Int -> String -> …` has no readable parse without
parentheses. OCaml and Haskell require them for the same reason; Rust escapes it
only because `|…|` self-delimits.

## 6. Type-system impact

- No new types, no new constraint forms, no change to generalization *machinery* —
  though see the caveat below on generalization *results*.
- The annotation must be applied **before** the body is inferred: a dictionary is
  chosen *during* body inference, so a check applied afterwards "arrived too late
  to be evidence" (`:405`, recording the same bug on the `let x: T = e` path).
  The §4 insertion point satisfies this.
- Two-pass argument checking limits the reach: an annotation joins in pass 2
  (`infer_lambda_slots`, `:3190`), after `push_down_arg_slots`, so it cannot inform
  pass-1 dictionary choices on *sibling* arguments. It determines instances within
  the lambda's own body only.
- **Amended from revision 1**, which claimed "no program changes meaning". More
  programs are rejected (conflicting annotations) and more are accepted
  (§1.2 row 5). But a third class exists: a program that compiles before and after
  while the *inferred scheme narrows* — §1.2 row 6's `first` goes from
  `forall a b. a -> b -> a` to `String -> Int -> String`. That is the intended
  correction, and it can reject previously-valid call sites elsewhere.

## 7. Error-message impact

Two new diagnostics, modelled on `apply_let_annotation` (`:392`):

```
type annotation mismatch for `s`: Type mismatch: String vs Int
type variable `a` is not allowed in a lambda parameter annotation — v0 requires a concrete type
```

**Located at the lambda, not the parameter.** Revision 1 said "located at the
parameter"; that is unimplementable as specced —
`Param String (Maybe TypeExpr) ParamMode` (`ast.sprout:60`) carries no
`SourcePos`, and no `TypeExpr` variant carries one either (`ast.sprout:9–13`). The
lambda's `lpos` is the only position in hand. Adding a position to `Param` would be
an AST arity sweep across parser, infer, lint, `ast_to_ir` *and* test fixtures —
larger than anything else here, and not worth it while the message names the
offending parameter in its text.

Second-order: §1.2 row 5's `ambiguous type variable in to_string` stops being
reachable for an annotated lambda, and that diagnostic's advice — "Annotate the
expression with the type you mean" — becomes actionable where it was not.

## 8. Compatibility and migration

**Nothing in either repository is affected.** Revision 1's census pattern was
insufficient — it required the annotated parameter to be first and adjacent to
`\(`, missing `\(a, b: Int) -> …` and multi-line parameter lists. Re-run with a
multi-line-tolerant pattern validated against a known-positive fixture, the result
is unchanged: **0 annotated lambda parameters in `sprout_lang`, 0 in
`uncharted-suns`**.

That is not coincidence — nobody writes them because they currently do nothing. So
both the strictening and the §2.1 rejection have zero migration cost.

## 9. Tests

TDD, per AGENTS.md: each written and confirmed failing first.

Rejection (new errors):
1. `where`-bound lambda whose annotation contradicts its use site.
2. Argument-position lambda whose annotation contradicts the callee's slot.
3. Multi-parameter lambda where only the second annotation conflicts.
4. Lambda annotation containing a type variable — §2.1's new diagnostic.

Acceptance (newly working / newly correct):
5. Top-level `let` + annotated lambda with a class-constrained body — §1.2 row 5.
6. §1.2 row 6's `first`: the annotation now narrows the scheme, so `first(1, 2)`
   becomes an error. This is the regression test for the §6 caveat.
7. Annotation agreeing with the callee's slot — still compiles, no diagnostic.
8. Annotation using a qualified type name and one using an `alias` — guards the
   §4 plumbing; without it these misresolve silently.

Non-regression:
9. Unannotated lambdas in every position of the §1.2 table, unchanged.
10. `just ir-golden-diff` clean — the change is check-only.

## 10. Spec and docs

- **`spec-v0.md` §5.3** — normative: an annotated lambda parameter's annotation is
  unified with the parameter's type; a mismatch is an error; type variables are
  rejected in v0. Remove the "*Not yet enforced…*" note at line 607. Correct or
  remove the unparenthesized `\s: String -> …` form per §5.
- **`spec-v0.md` §5.3:622–624 — DONE, landed with this document.** It stated that
  a lambda outside argument position has "its parameters inferred solely from its
  body". That is false today, independent of this proposal: a `let`-bound
  `\s -> \`<${s}>\`` resolves only because the **use** applies it to a `String`,
  the body alone leaving `ToString s` ambiguous. Corrected in place, with that
  example, rather than deferred — a normative line wrong about current behaviour
  is a defect now, not on approval.
- **`spec-v0.md` §5.1:342** — "A binding's type is determined by its right-hand
  side, not by how the body uses it" is in tension with the same measurement for a
  lambda right-hand side. Reconcile the wording.
- **`lambda-argument-inference-v0.md` §7** — replaced by this document (§1.3).
- **`idiomatic-sprout.md`** — the *Name the lambda that doesn't fit its line*
  section gains the local `where`-bound form as a lighter alternative to a
  top-level `fn`, honest only once this lands.
- **`BACKLOG.md`** — file §11.1, and record scoped type variables as the future
  work that would lift §2.1's restriction.

Status: **experimental** until implemented; `spec-v0.md` remains normative.

## 11. Review history

### 11.1 Defect found in existing code, not caused by this proposal

Top-level `let` annotations sharing a type-variable *name* share one variable
across the whole module, so an unrelated declaration can narrow another's scheme —
loudly (`type annotation mismatch for p2`) or silently (`ident: a -> a` checked as
`Int -> Int`). Both reproduced in §2.1. This predates the proposal and is filed
separately in `BACKLOG.md`; §2.1 exists so that lambda annotations do not inherit
it.

### 11.2 What revision 2 changed

| # | Revision 1 said | Correction |
|---|---|---|
| 1 | Reuse `let_annotation_type` verbatim; its tyvars are "seeded as its own fresh variable" | False — one variable per *name*, module-wide. §2.1 rejects type variables instead |
| 2 | Accept type-variable annotations as a documented non-goal | Inverted: reject them, since accepting is worse than today's inertness |
| 3 | Add a `Ref` to `InferState`; `alias_env` is module-constant; the sweep hits test fixtures | `alias_env` is registered during the walk, not constant; `import_aliases_from_env` already reaches `env`; and the sweep touches 33 sites in one file, 0 in tests |
| 4 | Top-level `let` + annotated lambda "fails" | Only with an unresolved class constraint; otherwise it compiles at a *more general* type (§1.2 row 6) |
| 5 | "No program changes meaning" | A third class exists: schemes of already-compiling bindings narrow (§6) |
| 6 | Error located at the parameter | Neither `Param` nor `TypeExpr` carries a `SourcePos`; locate at the lambda (§7) |
| 7 | Census pattern | Missed multi-parameter and multi-line forms; re-run, result still 0 (§8) |
