# Binding-group inference — completing Hindley–Milner at the top level (v0)

> **Status: the soundness half is implemented and gated; the completeness half is
> not.** §6 describes the design; §7 records exactly which parts landed and what
> the remainder still costs, measured rather than estimated. `docs/spec-v0.md`
> §7 rule 16 carries the normative rule.

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
| 1. Dependency analysis | **Not implemented.** See §7.2 |
| 2. Monomorphic per-declaration assumptions | **Landed.** `unann_ret_var` / `unann_param_var` mint `_unann@<owner>` and `_unann@<owner>/<param>`; the collectors no longer put them in the binder list, so `instantiate` leaves them alone and every use shares one variable |
| 3. Shared substitution | **Landed.** `check_fn_body` returns its final substitution, `TypedDeclOk` gained a fourth field, and `typecheck_decls_inner` threads it |
| 4. Generalization boundary | **Landed.** `own_unann_vars` subtracts the declaration's own placeholders from `env_ftv` at its generalization point — see §7.1, the part that is easy to get wrong |
| Placeholder deletion | **Landed.** With the placeholders out of the binder list, `rigidity_violation`'s `_unann` skip had nothing to skip and was removed |

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

### 7.2 The residual gap, measured

Without dependency analysis, an unannotated function that is **forward**-
referenced is monomorphic at its uses, because those uses are inferred before its
own body pins it. Measured on the landed compiler — the same program, twice, with
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

Define-before-use is fully polymorphic; use-before-define is not. This is
precisely the behaviour HM has without §4.5.1, and it is what OCaml imposes
unconditionally. It is a *rejection*, never a miscompile, and part 1 removes it.

Declaration order also still decides **which** declaration reports a conflict —
probe 1 reports at `summarize`, probe 2 at `report`. Both reject; only the
location moves.

## 8. The one case HM cannot infer

**Polymorphic recursion** — a function calling itself at a *different* type than
it was called with — is not inferable in general. Haskell 2010 §4.5.2:

> *"Polymorphic recursion allows the user to supply the more general type
> signature… a type signature can be used to specify a type more general than the
> one that would be inferred."*

Once part 1 lands this is the *only* place a top-level annotation is required.

## 9. Risks and how they were checked

- **Emitted IR.** The change alters no codegen. Verified: `just ir-golden-diff`
  reports 0 differences across the golden corpus, run against a **reseeded**
  stage-1 (per `AGENTS.md`, the gate is vacuous otherwise).
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
§6 with parts 1, 3 and 4 missing. Adding parts 3 and 4 to it is what landed; part
1 is what remains.
