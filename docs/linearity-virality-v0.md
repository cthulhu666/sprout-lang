# Containment virality for linear types — design note (v0)

Status: **design note, pre-approval.** Written 2026-08-26. Asks for a call on whether a type
that *contains* a linear value is itself linear ("virality"), which is the root cause behind a
`P1` soundness item and a `P3` design item in `BACKLOG.md` that are, on inspection, the same
question. No implementation has landed; the one-line probe described in §10 was built, measured,
and reverted.

Companion to the M4 linearity docs — `docs/linear-types-m4-scoping-2026-08-01.md`,
`docs/linear-types-m4.2-enforcement-2026-08-06.md`, `docs/linear-borrowing-v0.md`,
`docs/one-shot-closures-v0.md`. The normative description of what ships today is
`docs/spec-v0.md` §5.8.

## 1. Problem statement

A linear value wrapped in *anything* is silently dropped. Measured 2026-08-26 against
`build/compile_driver_bin_stage1` at `f8556ab0`, with `type linear Res = Res Int`:

| shape | result |
|---|---|
| `let r = Res(1) in 7` | **rejected** — `linear value 'r' is never used` |
| `let xs = [Res(1)] in 7` | accepted, full IR emitted |
| `let p = (Res(1), 2) in 7` | accepted, full IR emitted |
| `let m = Just(Res(1)) in 7` | accepted, full IR emitted |
| `let b = Box(Res(1)) in 7` (user ADT) | accepted, full IR emitted |

The first row is the control: it proves the pass runs on these programs and that the other four
are genuine misses, not an unrelated early exit.

### 1.1 The rule is already inconsistent with itself

This is not a missing feature. Sprout **already** decides containment — in one code path and not
the other:

| path | predicate | verified behaviour |
|---|---|---|
| discarded `do`-step (`linear_check.sprout:1034`) | `type_mentions_linear` — descends `TApp`/`TTuple` | `_ <- mk()` at `Maybe Res` is **rejected** |
| pattern/binder tracking (`linear_check.sprout:190`) | `type_is_linear` — head only | `let m = Just(Res(1))` is **accepted** |

Both were measured. So `Maybe Res` is a leak in statement position and not a leak in binder
position, in the same language, for the same reason (`Res` is inside a non-linear head).

`type_is_linear` is a single `dict_get("@linear:" ++ head_type_name(t), env)` — it asks only
whether the *outermost* type constructor was declared `type linear`. Every row 2–5 has a
non-linear head, so no obligation is ever created and none of the three binder rules (reuse,
leak, branch convergence) has anything to look for.

`spec-v0.md` §5.8 already documents the containment check as deliberate for the do-step case,
with the reason: in a `Maybe`/`Result` block every statement has type `Maybe X`, so "a rule
reading only the type's head could never fire in a short-circuiting block". That argument is
about where the rule *fires*, not about what a leak *is* — and it applies verbatim to a binder.

## 2. Goals and non-goals

**Goals.**
- Decide whether linearity is closed under containment, and record the decision normatively.
- If yes: close rows 2–5 uniformly, including the user-ADT row, rather than special-casing the
  three structural containers and leaving `Box` open.
- Keep the two predicates from disagreeing again — one notion of "this type carries a linear
  obligation", used everywhere.

**Non-goals.**
- Automatic destructors. Sprout has no `Drop`; a leak is a compile error, never a cleanup hook.
- A linearity *bound* on a type parameter (`Chan a where a: Unrestricted`). §7 explains why this
  is the thing that would make virality cheap, and why it is out of scope for v0.
- Revisiting the Position A/B wildcard question (`BACKLOG`, "wildcard pattern over a linear
  value"). Adjacent, separately decidable, and unchanged by every option here.
- Changing borrowing, `once` closures, or parameter ownership modes.

## 3. Prior-art survey

The question is where linearity lives, and *that* determines whether containment is even a
meaningful question. Every row below is verified against the language's own reference.

| language | linearity lives on | viral by containment? | primary source |
|---|---|---|---|
| **Austral** | the **type** (universes: `Free` / `Linear`) | **Yes** — "linear types can be thought of as being *viral*. If a type contains a value of a linear type, it automatically becomes linear"; "You can't sneak a linear type into a free type" | [Austral spec](https://austral-lang.org/spec/spec.html) |
| **Linear Haskell (GHC)** | the **arrow** (multiplicity, `%1 ->`) | **No** — types are not linear at all; constructors are multiplicity-polymorphic (`MkT1 :: forall {m} a. a %m -> T1 a`), so `MkT1` is usable in unrestricted contexts like `map` | [GHC users guide, `LinearTypes`](https://ghc.gitlab.haskell.org/ghc/doc/users_guide/exts/linear_types.html) |
| **Idris 2** | the **binder** (quantities `0` / `1` / unrestricted, written `(1 x : a) -> …`) | **No** — a constructor field at quantity 1 does not make the type linear; the guarantee is *preservation through application*: if `f x` is used once then `x` is used once | [Idris 2 docs, multiplicities](https://idris2.readthedocs.io/en/latest/tutorial/multiplicities.html) |
| **Rust** | **affine**, not linear — dropping is always allowed and runs `Drop` | n/a for linearity, but the dual property **is** viral: `Copy` "can only be implemented for types which do not implement `Drop`, and whose fields are all `Copy`. For enums, this means all fields of all variants have to be `Copy`" | [Rust reference, special types and traits](https://doc.rust-lang.org/reference/special-types-and-traits.html) |

**What the survey settles.** There is no consensus on virality, because the three linear systems
disagree about *where linearity lives* — and that placement decides the answer:

- Put it on the **type** and you must close it under containment, or the property is not a
  property of the type at all. Austral is the only surveyed language that does this, and it is
  viral. Rust confirms the pattern from the dual side: `Copy` is a type property, so `Copy` is
  computed from the fields.
- Put it on the **arrow** (GHC) or the **binder** (Idris 2) and containment is a non-question —
  the same type is used linearly in one place and freely in another, so nothing needs to be
  viral.

**Sprout placed linearity on the type** (`type linear File = File Int`, §5.8) and then did *not*
close it under containment. That combination appears in none of the surveyed languages, and §1.1
is what it costs: the property "is linear" is not preserved by the language's own constructors.

## 4. Options

### Option 1 — S1, binder-only containment (the probe)

A *binder* whose type mentions a linear type carries the use-exactly-once obligation. One
predicate swap at `linear_check.sprout:190`, `type_is_linear` → `type_mentions_linear`. The
meaning of `type_is_linear` is untouched, so parameters, returns and fields are unaffected.

Closes all four open rows, `Box` included, and makes the binder path agree with the do-step path
that already ships.

### Option 2 — S2, full virality (Austral)

`type_is_linear` itself becomes containment-computed: a type whose components mention a linear
type **is** a linear type, everywhere. Beyond binders this reaches parameter-mode checking
(`:1219` `BadNonLinear`, `:1259`/`:1271` borrowing filters), field-read consumption (`:505`) and
the `program_has_linear` gate.

Strictly stronger and the only option that makes "is linear" a real type property. Also the only
option whose cost includes rejecting programs that work today — see §7.

### Option 3 — status quo, documented as intentional

Keep the head-only binder rule and state in §5.8 that containment is checked in statement
position only. Costs nothing to implement and requires no migration, but leaves a soundness hole
whose practical consequence is stated in the `P1`: a resource pool protects its contents while
dropping the pool with resources inside is silent.

## 5. Syntax and semantics impact

No syntax change under any option. `type linear` stays the only way to *declare* linearity; the
question is purely what the checker derives from it.

Semantics, under 1 or 2: a binding whose type mentions a linear type must be used exactly once,
on every control-flow path, under the existing reuse / leak / branch-convergence rules. Nothing
new is added to the rule set — the same three rules see more bindings.

`type_mentions_linear` deliberately does **not** descend a function type, with a reason worth
preserving in the spec: `Unit -> File` is a recipe for a resource, not a resource, and discarding
one leaks nothing.

## 6. Type-system impact

Option 1 is not a type-system change at all: it changes which bindings the linear pass tracks,
not what any type *is*. Inference, unification and generalization are untouched, and `@linear:`
markers stay exactly as `infer.sprout:1359` writes them.

Option 2 *is* a type-system change: linearity becomes a computed property of a type expression
rather than a lookup on its head. Two consequences to settle before choosing it:

1. **A type variable's universe is unknown.** `type Box a = Box a` is linear at `Box File` and
   free at `Box Int`. Austral answers this with explicit universe parameters; Sprout is rank-1 HM
   with no linearity bound, so the honest options are to compute virality *after* instantiation
   (per use site, which is what a binder's concrete type already gives us) or to be conservative
   at the declaration (which would make every polymorphic container linear and is a non-starter).
2. **Recursive types must terminate.** `type List a = Nil | Cons a (List a)` — a containment walk
   over declarations needs a visited set. The existing `type_mentions_linear` walks a `Type`, not
   a declaration graph, so it is already safe; a declaration-level computation is not, for free.

This asymmetry is the main argument for taking Option 1 first: it buys the soundness fix without
opening the universe-polymorphism question.

## 7. Compatibility and the measured cost

The `P2` item "the over-strict effect-bind fallback now has a concrete consumer" is this same
question from the opposite side. It records, verified 2026-08-10, that `Chan Res` used twice **as
a parameter** typechecks, and that `List Res` used twice typechecks — and it wants that to keep
working, because `bench/http_worker_pool/pool_server.sprout` relies on it (`ch <- chan_new(s,
cap)` already has to be a threaded parameter instead, precisely because the do-step containment
rule fires).

So containment already costs real stdlib ergonomics in statement position, and extending it makes
that cost bigger before a linearity bound makes it smaller. **The two items must be decided
together**: adopting virality without a way to say "`Chan` is non-linear in its argument" means
some correct concurrent code becomes unwritable in the natural shape.

Under Option 1 the exposure is bounded to **pattern-bound names** — `let` binders, match-arm
variables and `<-` binds. Parameters are decided by separate `type_is_linear` call sites
(`:254`, `:1219`, `:1259`, `:1271`) that the `:190` swap does not touch, verified by reading all
seven call sites. So the `Chan Res`/`List Res` *parameter* uses the `P2` wants to protect are
untouched by Option 1, and are exactly what Option 2 would break.

**But Option 1 is not leak-catching only, and this is the part to weigh.** The three existing
rules all start firing on containers, not just the leak rule — measured on the probe:

```
let xs = [Res(1)]
in consume_list(xs) + consume_list(xs)
→ 10:38: ERROR: linear value 'xs' is used more than once
```

That is correct under linear semantics (a list of resources cannot be consumed twice) and it is
a new restriction on code that compiles today. The positive control passes — the same binding
consumed exactly once still compiles — so the rule discriminates rather than rejecting all
containers. Reviewers should read this as the intended consequence of the option, not a surprise
in it.

## 8. Error-message impact

Option 1 reuses the existing wording unchanged — measured on the probe binary:

```
7:6: ERROR: check: linear value 'xs' is never used (linear values must be used exactly once)
9:6: ERROR: check: linear value 'b' is never used (linear values must be used exactly once)
```

That is accurate but thin: `xs : List Res` is not itself a linear value, and a reader who has
only read §5.8's `type linear` rule will not see why `xs` is one. The message should name the
contained type and why the obligation exists, in the style the do-step diagnostic already uses
(`discarded_step_msg`, `:1025`, which distinguishes a bare reference from a wildcard bind because
the generic wording "describes a rule the program does not break"). Proposed:

```
linear value 'xs' is never used: its type `List Res` contains the linear type `Res`
(linear values must be used exactly once)
```

This is a required part of either option, not a polish item — the whole difficulty of a viral rule
is that the value the author wrote is not the value the rule is about.

## 9. Tests

Definition of Ready #2/#3 artifacts, all RED-verified before any implementation:

- `tests/conformance/type_error/linear_drop_in_list.{spr,err}`, `…_in_tuple`, `…_in_maybe`,
  `…_in_user_adt` — rows 2–5 of §1, one file each so a partial fix cannot look complete.
- A **positive control** in the same batch: the four containers each used exactly once must still
  compile. A rule that rejects every container of a linear is not the fix.
- `tests/stdlib/test_linear_binders.spr` — extend with the branch-convergence case over a
  container (used in one `match` arm, not the other), which the three existing rules should now
  reach.
- A **negative control** pinning §5.8's function-type carve-out: `let f = \_ -> Res(1) in 7` must
  keep compiling, since a recipe for a resource is not a resource.
- Regression guard for §1.1: a test asserting the do-step and binder paths agree on `Maybe Res`,
  so the two predicates cannot drift apart again.

Compiler-source change, so the full chain applies: `just test`, `just ci-fast-gates`,
`just refresh-seed` **before** `just ir-golden-diff` (per AGENTS.md DoD #12 — the gate runs the
stage-1 binary and reseeding is what makes its answer non-vacuous), and staged
`bootstrap/compile_driver.ll`.

## 10. Measurement

The Option 1 probe was built and run rather than reasoned about. Procedure: swap the predicate at
`linear_check.sprout:190`, `just build-stage2` (stage-2 compiles the modified compiler source with
the unmodified stage-1, so no seed refresh is involved), then run the corpus against
`build/compile_driver_bin_stage2` via `just test-stdlib-stage2` and `just compile-examples-stage2`.
Source reverted afterwards.

Confirmed effective on all four rows, with the user-ADT row included:

```
7:6: ERROR: check: linear value 'xs' is never used …    # let xs = [Res(1)]
9:6: ERROR: check: linear value 'b' is never used …     # let b  = Box(Res(1))
```

Both controls behave, so the rule discriminates rather than blanket-rejecting containers:

| probe | probe binary | reading |
|---|---|---|
| `let xs = [Res(1)] in 7` | rejected, *never used* | leak caught |
| `let xs = [Res(1)] in consume_list(xs)` | **compiles**, 266 `define` blocks | single use still legal |
| `let xs = [Res(1)] in consume_list(xs) + consume_list(xs)` | rejected, *used more than once* | reuse caught |

### 10.1 Corpus results

- `just compile-examples-stage2` — **all 52 examples compiled OK**, exit 0.
- `just test-stdlib-stage2` — **`==> All suites PASSED`**, exit 0, over the 338 `.spr` files in
  `tests/stdlib/` and `tests/stdlib/compiler/`.

**Zero corpus impact.** Nothing in the repository binds a container of a linear value and then
drops or reuses it, so Option 1 rejects no code that exists today. Combined with the four probe
rows it also rejects the four shapes it is meant to, and accepts the single-use control — so the
green is not the vacuous kind.

Guarding against exactly that: the suite ran against the binary *after* it was independently
confirmed to have the new behaviour (it rejects `lin_list.spr` and `lin_adt.spr`, which stage-1
accepts). `test-stdlib-stage2` takes the binary as a path with no rebuild dependency, so there is
no way the run silently used an unmodified compiler — the failure mode AGENTS.md DoD #12 warns
about for `ir-golden-diff`.

One limitation of this measurement, stated because it bounds the conclusion: the corpus contains
no *deliberate* container-of-linear code to regress. `stdlib/` declares only three linear types
(`TcpConnection`, `TcpListener` in `net.sprout`, `Task a` in `task.sprout`) and the shapes the
effect-bind `P2` cares about live in `bench/http_worker_pool/`, which neither recipe compiles. A
green corpus therefore says "no existing code breaks", not "no realistic code breaks".

## 11. Recommendation

**Option 1 (S1, binder-only containment), then decide Option 2 jointly with the effect-bind
`P2`.**

Reasons, in order:

1. It is the fix for an inconsistency the language already has (§1.1), not a new rule. The
   containment predicate ships today and is already the answer in statement position.
2. It closes the `P1` uniformly — all four rows including the user ADT — which the
   "special-case `List`/tuple/`Maybe`" alternative does not, and which is the arbitrary half.
3. It avoids the two open questions in §6 (a type variable's universe, and declaration-level
   recursion) because a binder's type is already concrete at the point the rule fires.
4. Its blast radius is bounded to binders, so it does not touch the parameter positions the `P2`
   is protecting.

Option 2 is the coherent end state — Sprout put linearity on the type, and Austral is the one
surveyed precedent for that placement, viral. But it should follow a linearity bound on type
parameters, not precede it, or it makes correct concurrent code unwritable in its natural shape.

## 12. Spec/docs plan

- `docs/spec-v0.md` §5.8 — under Option 1, replace the sentence listing the two accepted-and-not-
  leak-free shapes so it no longer implies containers are among them, and state the binder
  containment rule alongside the existing statement-position one. §5.8 is **experimental**, not
  normative v0, so this is not a normative change.
- `BACKLOG.md` — the `P1` container item and the `P3` "containment virality" item are one
  question; merge them, and cross-reference the effect-bind `P2` as jointly decidable per §7.
- This note gets a status header recording the decision, per the convention in
  `docs/linear-borrowing-v0.md`.
