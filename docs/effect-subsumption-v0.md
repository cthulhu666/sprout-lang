# Effect subsumption at arrow positions (v0)

Status: **ALL FOUR PARTS LANDED** — part 0 §6.0, part 1 §6.3 + §6.4b, part 2 §6.5b,
part 3 §6.1a. **Per-constructor variance (§6.4) is unimplemented**; no program reaching it
is known any more (§6.5a). Part 2 did not land as §6.5 designed it: see §6.5b.
Part 1 landed 2026-09-10 as two commits: polarity threaded through `unify_types` with the
symmetric spelling replaced by `unify_expect_actual` / `unify_actual_expected` /
`unify_join` across 36 sites, then **bounded effect variables** (§6.4b) fixing the five
defects a code review found in the first implementation (§6.4a).
Revision 8 (2026-09-10). The nullary type collapse
landed on master (`docs/nullary-type-collapse-v0.md`, Option A′): the zero-arg boundary
revision 4 excluded is now **closed** (§6.6), and the new `TThunk` arrow is a second
comparison site part 1 must cover (§6.1). Revision 5 added part 0 after a review found
part 1 has a one-token bypass without it. Revision 4 narrowed the scope: what was part 4
is a separate bug, moved to `docs/nullary-type-collapse-v0.md` — and fixed there.

The fix has **four parts**. No one subsumes another. Each review round found one more
boundary, so treat this list as *known incomplete*: revision 1 had only part 1, revision 2
added parts 2 and 3, revision 3 added a fourth that revision 4 removed as
not-an-effect-problem, and revision 5 added part 0 at the front.

| # | boundary | how the effect escapes | mechanism | § |
|---|---|---|---|---|
| 0 | an unknown effect label | parsed as an effect **variable**, so every rule that exempts variables exempts it | reject a label that is not `IO` | §6.0 |
| 1 | a function value entering a slot | compared, wrong direction allowed | directional comparison, polarity-annotated | §6.3 |
| 2 | a peer join (`if`/`match`/elements/operands) | unified, difference swallowed | effect LUB **and GLB by depth parity** | §6.5 |
| 3 ✅ | an instance method vs its class signature | was never compared — scheme level | declared-vs-declared comparison | §6.1a |

Parts 0 and 3 have landed. Neither was a prerequisite of the other; part 3 went second
because it needs no part of part 1 — it compares two declarations and touches no
unification site, so the 36-site polarity audit is not on its path.

Parts 1–3 compare two effects somewhere, which is what makes them one design. **Part 0 was
a prerequisite, not a peer:** part 1 is unsound without it (§6.0). It landed first and
alone, which satisfies that constraint — the requirement was only that part 1 must not
land *without* it.

**Migration cost, measured for all four:**

| part | in-tree | downstream | note |
|---|---|---|---|
| 0 | 0 | 0 | every effect annotation in `stdlib/`, `examples/`, `tests/`, `bench/` re-enumerated 2026-09-09: `IO` (2596) and lowercase variables (81); every other spelling sits in a deliberately-ill-formed `type_error` fixture or a compiler comment. Confirmed zero on landing: the whole compiler source passes the new check (stage-3 builds). `!{}` was admitted rather than rejected, so its three signatures are untouched |
| 1 | **1** | 0 | the pre-implementation estimate said 0/0 from 2 flagged sites, and was wrong in-tree: `test_unboxed_maybe_shadow.spr` declares a parameter as a pure arrow and passes an `!{IO}` closure into it — a true positive needing an annotation. The estimate counted only the shapes it went looking for. It is also not the real cost until §6.4a's false rejections are fixed, since those reject legal code the corpus happens not to contain |
| 2 | 0 | 0 | concrete joins only; variable-effect joins unmeasured |
| 3 | 0 | 0 | **confirmed on landing.** One method-level effect annotation exists: `test_effect_polymorphic_class_method.spr` declares `!{e}` on a class method and its instances alike, which the relation accepts. Every other class/instance method signature (259 in-tree, 3 downstream, measured 2026-09-08) is pure. The census is no longer the evidence: stage 3 builds, which runs the check over the whole compiler source |

Part 2's zero is for the corpus as it stands; the mechanism must still newly reject
§6.5's currently-legal example, which is a correctness requirement rather than a
migration cost.

Corrected in revision 6: the zero-arg escape is closed on master, so the boundary
inventory (§8) and the property-2 replacement (§10) no longer list it; part 1 gains the
`unify_tthunk` site (§6.1); §6.1a's declared-vs-declared comparison must be total over
declared effects, with its variable/variable cell now pinned by an in-tree test (§6.1a);
and the line-number citations revision 4 corrected had rotted again after one rebase, so
they are gone rather than re-corrected (§6.1a, §6.2).

Corrected in revision 4, retained: former part 4 was not an effect bug and its stated
mechanism was not implementable (§6.6); §6.4's covariance recommendation is **unsound for
`Ref`** and is replaced by a per-constructor rule (§6.4); §7's "no IR change" promise is
now true *because* the scope narrowed, and says what it excludes.

Corrected in revision 3, retained: §6.5's "no GLB is needed" was **wrong** and its
six-site inventory was the **wrong ontology** (§6.5); §6.1a's bare-name lookup is
**unsound under name shadowing and duplicate method names** (§6.1a); §6.1's universality
claim is false; §7 promised a diagnostic the check cannot produce; the audit is 36 sites,
not 35.

Requires amending `docs/spec-v0.md` §7 note properties 2 **and 3**, which state the
behaviour this document removes (§10). Supersedes the withdrawn
`effect-var-rigidity-v0.md`, whose scope (effect *variables* only) was a corner of the
hole described here.

## 1. Problem statement

*Written 2026-09-07, describing the state before this document's parts landed. The shape
below is a compile error on master since 2026-09-10; the quoted property 2 is the text
part 1 replaced. Kept in the present tense as the record of what was being fixed.*

A function's declared purity is not enforced once a function is passed as a **value**.
An `!{IO}` function reaching a pure arrow — in an argument, a return, or a record field
— is accepted, and the IO runs under a pure signature. Verified by compiling *and
running* each shape on 2026-09-07:

```sprout
fn shout(n: Int) -> Int !{IO} = do
  print("io")
  n + 1

fn pure_map(xs: List Int) -> List Int = list_map(shout, xs)   # prints 3x. no !{e} anywhere.
```

`list_map`'s callback is a plain arrow, so this is one line of ordinary Sprout. Since
Sprout is functional-first, higher-order code is most of the interesting code, and the
purity guarantee does not hold across any of it.

The cause is normative. `docs/spec-v0.md` §7, note property 2:

> **Unification of an arrow's effect is total.** It binds effect variables and never
> fails, so two arrows whose effects differ are not thereby a type error and a program's
> acceptance never depends on effect inference reaching a particular answer mid-way.
> Rejection happens at the declaration boundary and nowhere else.

Implemented at `unifier.unify_arrow_effects`, which swallows the `Err`. "Rejection happens at the declaration boundary and nowhere else" is exactly the
sentence that permits the laundering: a function value crosses boundaries that are never
declaration boundaries.

Consequence for tooling: every declaration in such a file reports `(pure, pure)`, so
`--phase effects` and anything reading `effect_report_is_gap` are structurally blind to
it. The blindness is not an oversight in the report — the information never reaches it.

## 2. Goals and non-goals

**Goals.** Reject an `!{IO}` arrow supplied where a pure arrow is required, at every
position a function value crosses. Keep the reverse — a pure arrow supplied where an
effectful one is required — legal, since it is idiomatic and load-bearing (§5). Keep
`!{e}` combinators working unchanged.

**Non-goals.** Open effect rows (`!{IO|e}`); effect parameters on type constructors; new
effect labels; changing what `!{e}` means; the declaration-boundary gaps of §8, which are
a separate and much smaller change that composes with this one. Also not *where* `!{e}`
should be written in a public signature — that is `docs/effect-polymorphism-policy-v0.md`,
whose `Foldable` application waits on this change landing first.

## 3. Prior art

Verified against primary sources.

**Swift.** The closest analogue, and it states the rule as *subtyping on function types* —
the framing this design adopts. From *The Swift Programming Language*, Reference Manual,
"Function Type":

> The type of error that a function throws is part of that function's type, and a subtype
> relationship between error types means the corresponding function types are also
> subtypes. […] the relationship between some function types is as follows, from
> supertype to subtype:
>
> 1. Functions that throw any error, marked `throws(any Error)`
> 2. Functions that throw a specific error, marked `throws(MyError)`
> 3. Functions that don't throw, marked `throws(Never)`
>
> As a result of these subtype relationships:
> - You can use a nonthrowing function in the same places as a throwing function.

One-way, and exactly `pure ⊑ IO`: the non-effectful function is the *subtype*, usable
wherever the effectful one is, never the reverse. This is §5's rule, independently
arrived at.

Swift's `rethrows` additionally covers the declaration-boundary cases of §8: a rethrowing
function "throws an error only if one of its function parameters throws an error", "must
have at least one throwing function parameter", and "can contain a `throw` statement only
inside a `catch` clause".

**Koka.** Leijen, *Koka: Programming with Row-polymorphic Effect Types* (MSFP 2014) §2.3.
For `foo(f,g) { f(); g(); error("hi") }` the inferred type is
`∀μ. (() → ⟨exn|μ⟩ (), () → ⟨exn|μ⟩ ()) → ⟨exn|μ⟩ ()`: a concrete effect stays visible in
the row at every position rather than being absorbed. Koka has no bare-variable form that
could hide `io`, so the laundering shape cannot be written.

**OCaml 5.** Declined to track: "Unlike languages such as Eff and Koka, effect handlers
in OCaml do not provide effect safety; the compiler does not statically ensure that all
the effects performed by the program are handled." A real divergence, but not available
to Sprout, which already enforces purity at declarations and would otherwise be
half-enforcing.

**Consensus.** Among languages that track effects statically, an effectful function is
never silently usable as a pure one, and the check is at the *arrow*, not only at
declarations. Sprout is the outlier.

## 4. The measurement (done, 2026-09-07)

An instrumented compiler (branch `effect-arrow-subsumption-detector`) made
`unify_tfunc` reject a concrete pure/IO arrow mismatch instead of swallowing it.
Control-tested first: fires on 3 known-positive laundering probes, silent on 4
known-negative legitimate ones.

| corpus | files | flagged | real violations |
|---|---|---|---|
| `stdlib/` + `stdlib/compiler/` + `examples/` | 127 | 2 | **0** |
| `uncharted-suns` real source | 199 | 0 | **0** |

Downstream ran as an exact A/B — both compilers over the identical 199 files — yielding 2
pre-existing errors under each and **0 files failing under the instrumented build only**,
so the zero is not instrumentation-caused failures masking sites.

**Migration cost is zero on both repos** — for the rule of §6.3, at the comparisons the
detector instruments. Caveats, stated rather than buried:

- The detector is direction-agnostic, so it measures what an *equality* rule would
  reject. Directional rejections are a strict subset of those (every directional
  rejection is a concrete pure/IO meet, and every such meet is flagged), so a flagged
  count of 2 with both hand-classified as the accept direction gives a directional cost
  of 0. That inference is valid *only* for unify_tfunc-mediated comparisons.
- It therefore says nothing about §6.1a (class/instance) or §6.5 (joins). Those are
  unmeasured, and §6.5's example is a currently-legal program that a correct
  implementation must newly reject — so the true migration cost is **not yet zero-proven**.
- The downstream sweep covered 199 `.sprout` files. It did **not** cover the ~190 `.spr`
  files (186 of them `test_*.spr`), which are code that must also compile after migration.
- An earlier 1814-file downstream figure was wrong: that sweep walked
  `uncharted-suns/.claude/worktrees/`, i.e. other checked-out branches.

## 5. The rule must be subsumption, not equality

Both in-tree flags are the safe direction, a pure handler entering an effectful slot:

- `examples/http_echo_server.sprout:28` — `serve(8081, handle)`, `handle` pure.
- `examples/http_web_server.sprout:204` — `Route("GET", "/", \req -> see_other("/users"))`,
  whose own comment reads "a pure handler subsumes into the effectful route slot
  unchanged."

So the relation is `pure ⊑ IO`: a pure function may stand in for an effectful one (it
promises less), never the reverse. An equality rule breaks both examples on day one. The
corpus supplies its own counterexample, and these two lines become the regression tests
for the permissive half.

## 6. Implementation overview (for approval)

### 6.0 An unknown effect label is a variable, which bypasses part 1 — LANDED 2026-09-09

**Status: implemented.** `!{}` is admitted as an explicit spelling of purity — the form
`docs/effect-system-v0-plan.md` §15 already made canonical, with omission as its sugar —
so rule 9 now lists four annotation forms and the three in-tree `!{}` signatures stand
unchanged. Spec §7 rule 9 and property 3 carry the rule; parts 1 and 3 below have since
landed, and part 2 is unaffected by this one.

**The rejection belongs in the PARSER, and two attempts at a declaration walk are why.**
`parser.parse_effect_annotation` is the one place a written `!{...}` is read, so the
covered set is closed by construction. A walk over `ast.Decl` was tried first and missed
two positions in succession — a `where` constraint, found in review, and then a **lambda
parameter**, found by a code review after the first version was pushed. The lambda case
compiled and printed `io` three times under a pure signature: the very laundering this
part claims to close, still open. Enumerating every `Decl` *variant* is not the same as
reading every *field*, and the exhaustiveness checker cannot see a field discarded with `_`.

Section §6.0's stated mechanism was also stale in two ways:

- **"Two functions build the `Effect`, and the rejection goes in both."** They now both
  delegate to one `types.effect_from_labels`, which has no error channel and no position —
  so the check could not live there either way.
- **The interface half is real, and its first version was wrong.**
  `iface_codec.decode_effect_var_at` must admit a *well-formed variable name*, not a
  lowercase one: a generalized scheme carries `$eN` from `unifier.fresh_effect`, and
  `--emit-iface` writes it verbatim, so a lowercase-only guard made the compiler's own
  output fail `--check-iface`. It was green in-tree only because no stdlib module exports
  an effect-polymorphic function — of 99 interfaces the encoded effects are 18257
  `EffectPure` and 1435 `EffectIO`, with zero `EffectVar`. That is corpus evidence, and
  corpus evidence is exactly what this document warns against elsewhere.

---


Found in review round 5. `!{NOPE}` type-checks, and it does not become an inert label — it
becomes an effect **variable**:

```sprout
fn sneak(n: Int) -> Int !{NOPE} = do { print("io"); n + 1 }
fn pure_map(xs: List Int) -> List Int = list_map(sneak, xs)
```

```
main.sneak    : Int -> Int !{$e30}        <- the written NOPE
main.pure_map : List Int -> List Int
```

Verified by compiling and running: prints `io` per element under a fully pure signature.

**Why this is part of this design and not an unrelated wart.** §6.3's rule table exempts
variables — "either side a variable → bind as today" — and that arm is load-bearing, since
it is what keeps every `!{e}` combinator working. An unknown label is a variable, so the
exemption covers it. After parts 1–3 land, **replacing `!{IO}` with `!{NOPE}` re-opens §1's
hole in one token**, and the resulting program is conformant against the amended spec
(§10). A guarantee with a one-token opt-out is not a guarantee.

**Mechanism.** Reject any label that is neither `IO` nor a lowercase effect variable, where
the `Effect` is built. Two functions do that — `infer.effect_from_maybe_labels` for source
annotations and `iface_codec`'s private copy for interface decode — and the rejection goes
in both, or a hand-edited interface file smuggles what source cannot spell. Migration cost
is zero, already measured (header table). `docs/spec-v0.md` §7 rule 9 already admits only
three annotation forms, so this rejects nothing rule 9 ever permitted — it closes the gap
between the rule and its enforcement rather than adding a rule.

**Decide `!{}` in the same change.** The empty row is written in three tests
(`test_eta_forwarding.spr` ×2, `test_devirt_classmethods.spr`) and evidently means "pure",
and §7 does not define it. A validator written from rule 9 as it stands rejects all three,
so either rule 9 gains it as a spelling of purity or those signatures drop it.

**The deeper requirement this exposes: the comparison must be TOTAL over `Effect`.**
`types.Effect` has four constructors — `EffectPure`, `EffectIO`, `EffectRow (List String)`,
`EffectVar String`. Revision 4's §6.3 table specified two cells and gestured at a third,
and this bypass is what an unspecified cell looks like from the outside. §6.3 and §6.5 now
specify every cell. Treat a non-total match on `Effect` as the defect class, not `NOPE` as
the defect.

### 6.1 `unify_tfunc` covers every arrow COMPARISON — which is not every boundary

The detector lives in `unify_tfunc` and fires at every position where two arrows are
unified. Confirmed across eight: call argument, inline lambda argument, function return,
record field, ADT constructor payload, tuple component, list element, and `wrap` payload.
`unify_tfunc_owned` delegates to it, so one check there covers all of them and no
per-site enumeration is needed for coverage *of arrow comparisons*.

**Revision 6: there are now two arrow constructors, so two comparison sites.** The
nullary landing added `types.TThunk ret eff` — a zero-parameter arrow with its own effect
slot — unified in `unifier.unify_tthunk`, which `unify_tfunc` never sees. Part 1's
directional check therefore goes in both: at a thunk the effect sits at result depth, so
it is compared at the *current* polarity with no flip (there is no parameter to flip on),
and §6.5's join takes the LUB at a joined thunk's effect slot the same way. Without the
thunk arm, an `!{IO}` thunk value entering a pure `() -> T` slot re-opens §1's hole at
arity 0 — undoing exactly what the nullary fix bought. The measurement in §4 predates
`TThunk` and does not cover it; thunk-typed slots did not exist to be measured, so the
zero there is inherited, not observed.

### 6.1a But an effect can cross without any arrow comparison — LANDED 2026-09-09

The original claim ("one check covers every position") is false. A class method's
declared effect and its instance's declared effect are compared at the scheme level, not
by unifying two arrows:

```sprout
class Quiet a
  fn calc(x: a) -> Int              # PURE

instance Quiet Int
  fn calc(x: Int) -> Int !{IO} = do { print("io"); x + 1 }

fn pure_user(n: Int) -> Int = calc(n)      # runs the IO
```

Verified: **0 errors under stage-1 and 0 under stage-2**, and the linked binary prints.
The instrumented compiler rejects every concrete pure/IO arrow meet anywhere unification
happens, so its silence is positive evidence that this boundary is not an arrow
comparison at all. No effect variable and no higher-order type is involved — this is
plain typeclass code.

An instance could therefore *strengthen* the effect its class declares, and every caller
dispatching through the class scheme inherited the class's weaker claim. The rest of this
section is the design as written before it landed; **As landed** below records what
differs.

**Mechanism.** A third check, at instance-method checking, comparing two *declared*
effects — no inference and no unification involved:

- The class method's declared effect is already in scope. `register_class_method`
  (`infer.sprout`) builds the class method's scheme from its `effects_maybe` and
  registers it under the bare method name when the `ClassDecl` is processed — before any
  of that class's instances are checked. So inside `check_instance_method` the class
  effect is `types.scheme_effects(dict_get(name, env))`.
- The instance method's declared effect is `eff_maybe`, already threaded through
  `instance_method_checked`.
- Reject unless `instance ⊑ class` — the §5 relation, with the class signature as
  expected. `pure` instance under an `!{IO}` class stays legal (an instance may promise
  *less*); an `!{IO}` instance under a pure class is the rejection.

**But the bare-name lookup is unsound, and this is the part that needs work.** Two
conditions that compile today break it:

- **Two classes declaring the same method name.** Both compile; the name resolves to the
  last registered. So `dict_get(name, env)` at instance-check time can return the *other*
  class's scheme, and with differing effects that is a false rejection of a legal
  instance — or a false acceptance of an illegal one.
- **A top-level function shadowing a method name.** `fn calc(s: String) -> String`
  alongside a class method `calc` compiles clean, and the bare name maps to the unrelated
  function — so the check would compare the instance's `!{IO}` against that function's
  `Pure` and reject a program that is legal today. This is the hazard
  `test_local_shadows_class_method.spr` already guards elsewhere.

The `@class:` marker cannot stand in: `register_class_method_markers` stores
`Scheme(type_params, Nil, TConst(class_name), EffectPure, Nil)` — the effect slot is
always `Pure`. And `check_instance_method` does not currently receive the class name at
all, only `inst_constraints` (the where-clause).

So part 3 needs a **class-qualified registration key** for method signatures plus the
class identity threaded into instance checking. Small, but not the one-line lookup an
earlier draft described. `class_method_mode_error` shows the lookup pattern but
inherits the same bare-name weakness.

Note what this does **not** duplicate: rule 8 already checks an instance method's *body*
against its *own* declaration (`effect_pure_instance_method_does_io.spr`). The missing
edge is own-declaration against class-declaration. Both are needed; neither implies the
other.

An effect variable on the class method (`class ... fn calc(x: a) -> Int !{e}`) is the
same check with the same relation, since a variable is not above a concrete effect —
consistent with §8's first arm.

**And this comparison must be total over declared effects, by §6.0's own principle.** Each
side is one of `Pure`, `IO`, or a variable (a `Row` on either side is part 0's hard-error,
as in §6.3), which is nine cells, and the sentence above decides only some of them. Two
are worth pinning now:

- **variable / variable must accept** — a class declaring `!{e}` with instances declaring
  `!{e}` is exactly `test_effect_polymorphic_class_method.spr`, added alongside
  `docs/effect-polymorphism-policy-v0.md`; a check that rejects that cell breaks an
  in-tree test on landing day.
- **`Pure` class / variable instance is §8's second shape reached from the class side** —
  an instance whose effect depends on its own arrow parameters, dispatched through a class
  that promises purity. It needs the same quantified-vs-leaked guard §8 describes, and
  deciding it belongs to the implementing PR, stated in the fixture set rather than
  defaulted into.

The remaining cells follow from `instance ⊑ class` with `pure ⊑ e ⊑ IO` read pointwise;
write the table out in the implementation and keep it total, because §6.0 is the record of
what an unspecified cell costs.

**As landed.** `types.effect_declared_at_most(inner, outer)` is the relation, and it is
exactly `pure ⊑ e ⊑ IO`:

| class ↓ / instance → | pure | `!{e}` | `!{IO}` |
|---|---|---|---|
| **pure** | accept | reject | reject |
| **`!{e}`** | accept | accept | reject |
| **`!{IO}`** | accept | accept | accept |

**The accepting `!{e}`/`!{e}` cell guarantees nothing about what runs**, and that is not
this rule's doing. An instance declaring `!{e}` whose body performs IO is accepted, and a
pure caller runs it — but so is a plain `fn f(x: Int) -> Int !{e}` with the same body and
no class anywhere. The channel is rule 8's standing variable exemption (spec §7 property 3),
which part 1 closes. Pinned with its no-typeclass control by
`tests/conformance/run/effect_tied_var_body_io_known_escape.spr`, because it otherwise sits
one keystroke from `effect_instance_weakens_class_ok.spr`'s `Zeroed` class with nothing
marking where the guarantee stops.

All nine cells plus the `EffectRow` arms are pinned by
`tests/stdlib/compiler/test_declared_effect_subsumption.spr`.

**A row is outside the order, and it is reachable.** `parser.checked_effect_names`
validates labels, not arity, so `!{IO, e}` parses and reaches every pass that runs before
rule 9 — the earlier claim that "a conformant signature cannot build one" was true of
conformant signatures and irrelevant, since this pass also sees non-conformant ones. The
relation answers false for a row on either side (a row is within nothing, including
itself), and the *caller* decides what to do — and **the two sides need opposite
answers**:

- **instance-side row → decline.** The instance method has a body, so it records an
  `EffectReport` and `checker.enforce_effects` names the row properly. Ranking it here
  produced `declared !{IO, e}, but the class declares !{IO, e}` — a contradiction on its
  face — and masked that message. Regression:
  `tests/conformance/type_error/effect_row_on_class_method.spr`, which writes both sides
  identically so any mismatch complaint is wrong by construction.
- **class-side row → reject, from a scan of its own.** A class method signature has no body
  and records no report at all (`docs/effect-enforcement-v0.md` §13.7), so nothing else
  reaches it. Declining both sides — the first fix — meant a class declaring `!{IO, e}` over
  an instance declaring `!{IO}` was accepted, and `pure_user` ran the IO. Regression:
  `tests/conformance/type_error/effect_row_on_class_signature.spr`.

The pair is the point: "a row is someone else's problem" is true on one side and false on
the other, and the first fix applied it to both.

**Rejecting the class-side row from inside the instance walk was still wrong, twice over.**
A third code review found both, verified by running. It fired only when a matching instance
existed, so a class declaring `!{IO, e}` with **no** instance stayed accepted — the escape
the section above claims is closed was only conditionally closed. And it reported at the
*instance's* position while naming the class's method, a caret pointing at correct code
(`docs/guidelines.md` §5). Both dissolve in a standalone `ClassDecl` scan running ahead of
the instance walk, which also restores the symmetry the instance walk wanted all along: it
now declines a row on either side, because by then both are owned elsewhere. Regressions:
`effect_row_on_class_method_no_instance.spr` (no instance; the `.err` pins the class's
line) and `effect_row_on_instance_method_only.spr` (a well-formed class signature, so rule
9 still names the instance).

The **two-variable** half of rule 9's singleton clause has no such scan and is still
unreachable on a class method signature — that escape is real and remains open, recorded in
`docs/effect-enforcement-v0.md` §13.7. Only the row half was closed, because only the row
half can let an effect cross.

**The cell this section left to the implementing PR was decided by execution, not by
judgement.** Pure class / `!{e}` instance is not a variable being conservatively refused —
the instance's variable is unconstrained by a class signature that has no `e` to
instantiate, so rule 8 checks the body against it and an **IO body satisfies it**. The
probe compiles, links, and prints under a pure caller, exactly as the concrete arm does;
it is `tests/conformance/type_error/effect_instance_var_under_pure_class.spr`. The `!{e}`
class / `!{IO}` instance cell was verified the same way. Neither is an over-correction, and
neither cost a design round.

**Both bare-name hazards above were confirmed to compile on master before the fix**, so
the class-qualified key is load-bearing rather than defensive:
`tests/conformance/run/effect_class_method_name_not_unique_ok.spr` is both shapes in one
program, and it was A/B'd — with the key degraded to the bare method name, that fixture is
rejected with `class main.Loud declares pure`, which is `Quiet`'s signature leaking through
the shared name.

The key is `@classmethod:{class}:{method}`, and it names an entry in a table this check
builds from the decl list — **not** an env marker. An earlier version did register it in
`env` so an imported class could be looked up there; that fallback is gone (see below), and
with it the registration, so `register_class_method` is unchanged from `master`.

**Placement, and the bug that decided it.** The check is a whole-program pass in
`typecheck_decls_resolved`'s validator chain, beside `check_missing_superclass_instances`
— it reads the decl list, so it sees every class before it judges any instance. It is two
scans over that list: the class-row scan (below) first, then the instance walk.

The first implementation ran at the `ast.InstanceDecl` arm instead, where the class effect
is whatever the env holds *so far*. An instance declared **above** its class therefore
found no class effect and was skipped in silence: the program compiled, and
`pure_user() -> Int` printed `io`. Verified by running before the move, and pinned by
`tests/conformance/type_error/effect_instance_strengthens_class_before_decl.spr`. The spec
note says "Declaration order is not significant", so a rejection that depends on source
order is not the rule it states.

This is §6.0's lesson on a second axis. There the covered set was left open along the
*field* axis — a walk enumerated every `Decl` variant and still missed a field discarded
with `_`. Here it was open along the *ordering* axis, and no amount of care about fields
would have closed it. Reading the whole decl list closes both at once: every instance
method's declared effect is reachable from an `InstanceDecl` node, and every class is in
the list regardless of where.

`deriving.sprout` synthesizes eight `InstanceMethodImpl`s and passes `Nothing` for the
effect slot in all eight, so a derived instance can never trip it.

The class side is read from the `ClassDecl`s in the decl list, keyed
`@classmethod:{class as the decl spells it}:{method}`, **and from nothing else**.

**The key is the class name verbatim, and there is no `env` fallback.** Both halves of that
were arrived at by being wrong first.

A **short-name** key came first, on the strength of compiler-internals.md §"Env-path type
names are SHORT" — a real rule, applied without checking what it cost. Two modules each
declaring `class Enc a`, one `!{IO}` and one pure, collapse to one entry; the `!{IO}`
class's instance is then judged against the *pure* class's effect, so a **legal program is
rejected** and the diagnostic names a class declaring the opposite of what it reports. It
was import-order dependent too — the same order-sensitivity this part had just fixed on the
declaration axis. A code review disproved it by running. This document had claimed the
failure mode was "a skipped check, never a false rejection"; it was not. Regression:
`tests/conformance/package_resolution/app_class_name_collision.spr`, which lives there
because the shape needs two sibling modules and only a package root supplies one.

An **`env` fallback** came next, to satisfy compiler-internals.md §"Whole-program passes:
scan `decls` AND read `env`". A second review found it carries the same collision, because
on the env path a class is named bare on *both* sides — so the verbatim key and the short
key are the same string there and the exact-first ordering buys nothing. Reading `env`
adds no coverage on that path; it manufactures a wrong answer.

So the check now reads decls only, and **deliberately does not follow that rule.** The
rule exists so a pass does not silently see an empty vocabulary and reject valid code —
here, seeing nothing means *skipping*, which is safe, while reading `env` produces the
false rejection the rule is meant to prevent. Every path a user compiles through bundles
(`--phase check`, `--emit-ir`, `compile_source_with_cache` — the last is why
`test_repl_instance_class_effect.spr` still passes 4/4), so the only path that loses the
check is `module_loader.load_module`'s isolated per-module typecheck, which had no witness
and no corpus. Removing the fallback also made the `@classmethod:` env markers dead, and
`register_class_method` is back to its `master` shape.

Front-end verdicts are pinned by `tests/stdlib/compiler/test_repl_instance_class_effect.spr`
against `compile_source_with_cache`, as that section requires — rejects and accepts alike,
since an over-correction there is invisible to fixtures that all run `--phase check`.

`docs/effect-enforcement-v0.md` §13.7 already records that a class method signature has
no body and so records no `EffectReport`; this is the same blind spot reached from the
instance side, and it is why neither this design's §6 nor the withdrawn design's arms
can see it.

### 6.2 Argument order cannot carry the direction — polarity must be explicit

The obvious cheap fix, flipping the `(EffectPure, EffectIO)` arm in
`unify_effects_applied`, is **provably wrong**: the same pair means opposite things at
different positions. All four cells verified by probe:

| position | case | reported as | wanted |
|---|---|---|---|
| argument | IO fn → pure param | `pure vs !{IO}` | REJECT |
| argument | pure fn → IO param | `!{IO} vs pure` | ACCEPT |
| return | IO fn as pure result | `!{IO} vs pure` | REJECT |
| return | pure fn as IO result | `pure vs !{IO}` | ACCEPT |

The cause is that `infer`'s `unify_types` call sites do not share an (expected, actual)
convention. Classification of the 36 sites (35 in `infer.sprout`, one in
`analysis_service_driver`), by what the site does:

| convention | count | where |
|---|---|---|
| (actual, expected) | ~22 | declaration return, record field, and most inference sites |
| (expected, actual) | ~7 | call argument; the pattern-checking sites |
| **peer — neither side expected** | ~6 | `if`-join, binary operands, match-arm accumulation |

**Regenerated at implementation time (part 1): 26 / 3 / 6 — and the peer bucket had the
right SIZE with the wrong MEMBERS.** Match-arm accumulation, which this table lists as a
peer, was classified `unify_actual_expected`; `ctor_field_types` took the sixth peer slot
instead. "The peer count matched exactly" was therefore evidence of nothing, and it read as
confirmation. A count is not a checklist — compare the *members* against the rows above. The (expected, actual) count did not — the **call sites are (actual, expected)**,
not (expected, actual) as predicted. The prediction reads the call's top-level comparison,
but the only cell that decides anything sits one *flip* below it: at the parameter. Writing
`unify_expect_actual(callee_type, call_shape)` — the spelling that reads correctly — makes
the parameter comparison treat the argument as the slot, and `list_map(shout, xs)` is
**accepted**. It type-checks, and the whole suite passes. Only running the fixture shows it.

That is the concrete reason this table says to regenerate rather than inherit: the wrong
entry here is not a miscount, it is the headline bug surviving the fix that was supposed to
close it.

> **The concrete site list must be REGENERATED at implementation time, and this table is
> deliberately not keyed on line numbers.** Revision 4 carried a line-numbered version whose
> citations were wrong on the tree it shipped against; revision 5 corrected them; one master
> rebase later the corrected numbers had rotted too, so revision 6 removed them — this
> document now names compiler functions, never compiler lines. Since the audit *executes
> from* this
> classification, a stale table is worse than none: it reads as a completed survey.
> Regenerate by grepping `unify_types(` and classifying each hit, and record the result in
> the implementing PR rather than here, where it rots. The counts above are the shape to
> expect, not a checklist.

So there *is* a dominant convention and an earlier draft of this section overstated the
chaos: it claimed "only the call site follows" the left=expected convention documented in `unifier.sprout`, which is wrong — the pattern-checking sites follow it too. The
conclusion survives regardless: two conventions plus a peer category mean argument order
cannot carry direction. `unifier.list_vec_hint` already works around the same flip for
types by writing a direction-agnostic diagnostic.

**So the design passes polarity explicitly** rather than inferring it from position. The
peer category is the one that breaks a two-valued flag — see §6.5.

**Audit rule: annotate polarity, never reorder arguments.** `unify_types` binds
right→left so the left (expected/older) `TVar` stays canonical, which `@fwd`/`@eta_fwd`
marker reachability depends on. Adding a flag preserves that bit-for-bit; "normalising" a
site by swapping its arguments would silently change which tyvar survives as canonical.
That failure would surface only in dictionary forwarding through `where C m` functions —
concrete instances devirtualize the dict away — so it would not show up in a corpus sweep.

**One caller lives outside `infer`**, making the audit 36 sites:
`analysis_service_driver.unify_ok`, the type-search matcher. A directional rule
changes tooling behaviour there — a search for `Int -> Int` would stop matching
`Int -> Int !{IO}` — and arguably search should match across the subtype relation rather
than adopt the checker's direction. Decide it explicitly rather than inherit it.

### 6.3 The change

Thread a polarity argument through `unify_types` → `unify_applied` → `unify_tfunc`:

- `Covariant` — left is expected, right is actual (the call-argument convention).
- `Contravariant` — the reverse.
- Flip polarity when descending into an arrow's **parameter**; keep it for the result.
  This is ordinary function subtyping, and it is what makes a second-order case
  (`((A -> B) -> C) -> D`) come out right rather than backwards.

At `unify_tfunc`, with polarity resolving which side is expected. **The table is total over
`Effect`'s four constructors, and must stay that way** — revision 4 specified three lines
and the unspecified cell was the §6.0 bypass:

| expected \ actual | `EffectPure` | `EffectIO` | `EffectVar` | `EffectRow` |
|---|---|---|---|---|
| **`EffectPure`** | accept | **reject** | bind | unreachable |
| **`EffectIO`** | accept (§5) | accept | bind | unreachable |
| **`EffectVar`** | bind | bind | bind | unreachable |
| **`EffectRow`** | unreachable | unreachable | unreachable | unreachable |

- **bind** is today's total unification. It was claimed to keep every `!{e}` combinator
  working; it does not, because the table says nothing about *when* the variable is read.
  A variable inference has already bound to `!{IO}` still enters this table as `EffectVar`
  and binds, and one not yet bound can be pinned by an earlier argument and then act as a
  concrete contract for a later one — §6.4a's soundness hole and its false rejections,
  respectively. Both cells are "bind" in this table and neither behaves like it.
  It is also the one arm an attacker can aim at, which is why part 0 must make an unknown
  label ill-formed rather than a variable.
- **unreachable was WRONG, and implementing it as a hard error is how that was found.**
  The claim was that no conformant signature builds an `EffectRow`, so a row here means
  part 0 has a hole. It does not: a *non*-conformant signature reaches this comparison
  first. `fn writes(s: String) -> Unit !{IO, e}` is rejected by rule 9, but the call
  `writes("hi")` unifies the arrow **before** rule 9 reports, so the hard error fired on
  `effect_mixed_row.spr` and masked rule 9's diagnostic with an internal-error string.
  The arm now **declines** — same resolution, and the same underlying mistake, as the
  instance-side row in §6.1a: the pass that sees a row first is not the pass that should
  name it. Both were "this cannot be reached" arguments that a fixture refuted in one run.

Then audit the 36 call sites (35 in `infer.sprout`, one in `analysis_service_driver`) to
pass the correct initial polarity.

**Make the audit self-checking rather than trusting it.** A site left on the wrong polarity
is a silent hole or a false rejection, and neither shows up in a corpus with zero
violations — so a green suite is not evidence the audit was done right. Two mechanisms,
both cheap:

1. **Remove the symmetric spelling.** Make the bare `unify_types` private and expose
   `unify_expect_actual`, `unify_actual_expected`, `unify_join`. An unconverted site then
   fails to compile instead of silently inheriting a default, and each call site states its
   convention where a reviewer reads it. Precedent: `unify_tfunc_owned` already hard-rejects
   ownership mismatches at the same layer.
2. **Flip-test the minority buckets.** The `(expected, actual)` and peer buckets are ~7 and
   ~6 sites. One fixture each that goes red when that site's annotation is flipped — the
   generalisation of §9's branch-swap pairs. The dominant bucket stays unpinned per-site,
   but every site is at least *forced to declare*.

### 6.4a What a code review found in part 1's first implementation

Four defects, every one reproduced by running. Two are false rejections of legal code, two
are holes in the guarantee the change advertises. They share one shape: **the check fires on
whatever the comparison happens to be holding, and cannot tell a declared contract from
something inference pinned a moment earlier.**

- **`!{e}` pass-through launders IO into a pure slot (soundness).** The polarity check reads
  the raw effect field; nothing applies `eff_subst` first, so a variable already bound to
  `!{IO}` still reads as `EffectVar` and takes the bind arm.
  `fn id_fn(f: Int -> Int !{e}) -> (Int -> Int !{e}) = f` then
  `fn get_pure() -> (Int -> Int) = id_fn(shout)` compiles and prints `io` — the exact shape
  `type_error/effect_io_arrow_into_pure_return.spr` exists to reject.
- **Match arms falsely rejected.** Each arm unifies against an accumulating fresh `ret_type`,
  so the FIRST arm becomes the contract. `match b with | true -> quiet | false -> shout` in
  an `!{IO}` slot is rejected while the `if` spelling — correctly a join — compiles.
- **Argument order decides acceptance.** For a polymorphic callee a tyvar pinned by an
  earlier argument is the expected side for later ones: `[quiet, shout]` is rejected,
  `[shout, quiet]` is not. This also refutes "every `!{e}` combinator keeps working" —
  `list_fold`'s step slot is `b -> a -> b !{e}` and it rejects previously-valid code.
- **A mutable container defeats it, and this one was not a discovery.** Type arguments keep
  the enclosing polarity, so `Ref (Int -> Int)` accepts an `!{IO}` write and reads back
  pure; `call_pure : (Int -> Int) -> Int` then prints `io`. Revision 4 of this document
  already says covariance is "**unsound for `Ref`**" and replaces it with the
  per-constructor rule in §6.4. The implementation used blanket covariance regardless, with
  a source comment rationalising it as "its own change, which nothing in the corpus needs
  today" — a decision the design had already made, reversed silently at implementation
  time because the corpus did not object. **Not reproducible on master since bounded
  effect variables landed** — four shapes tried, all rejected (§6.5a). The conclusion
  stands anyway: the rejection comes from a bound travelling through the shared type
  variable, not from the descent being variance-correct.

- **The pin bites from the CONTRACT side too**, which the four above missed. A lambda-bound
  parameter is pinned by whichever declared slot it meets first:
  `(\f -> takes_io(f) + takes_pure(f))(quiet)` is rejected while the same expression with
  the two calls swapped compiles. `quiet` is pure and legal in both slots; `takes_io`'s
  *written* `!{IO}` becomes an upper bound that the later comparison reads as if it were
  the value's actual effect.

**None of these has a local fix, and that is the finding.** "Apply the effect substitution"
alone — the obvious patch for the first — *widens* the false-rejection class, because more
pinned variables then read as concrete. The defects are coupled: they are one defect, which
is that an equality unifier can say "these are the same" but not "this may flow into that",
so every pin becomes a fake contract. Polarity supplies which *side* is expected; it cannot
supply whether that side was ever written down. The missing information is **provenance**,
and by the time `unify_tfunc` sees two effects it has been erased — which is why §6.2's
"both sides are as written: no inference, no unification" was never implementable there.

### 6.4b Bounded effect variables — the fix for §6.4a, LANDED 2026-09-10

**Approved and landed 2026-09-10**, closing all five of §6.4a's defects — the `Ref` one
included, but by bound propagation rather than by the per-constructor variance §6.4
prescribes, which remains unimplemented (§6.5a). Stop letting a tyvar pin turn an effect
into a contract. At the
moment a bind or effect meet happens, record *which role* the effect came from — a written
contract or an observed value — as a bound on a freshened effect variable, and reject only
when the bounds contradict: `lower = IO` while `upper = Pure`.

This is MLsub's insight narrowed to Sprout's two-point lattice. Dolan states the root cause
exactly: "the unification engine at the core of classical type inference accepts only
equations, not subtyping constraints" (*Algebraic Subtyping*, §1). Scala 3's capture
checking ships the directional-propagation form; Koka refused sub-effect constraints as
undecidable **for full rows** and instead keeps inferred effects open. Sprout has no rows
here — the lattice is `Pure ⊑ IO` — so the cost that made Koka refuse does not apply: two
monotone sets and an eager contradiction check, no constraint language, no solver.

- `bind_var`, which already has `polarity` in scope, freshens each concrete arrow effect
  when binding a tyvar to an arrow-carrying type and records the concrete as a bound.
  Actual-side origin ⇒ lower bound at covariant depth, upper at contravariant; expected-side
  origin ⇒ the mirror.
- Effect meets take a polarity: expected-concrete vs var ⇒ upper; actual-concrete vs var ⇒
  lower; var–var ⇒ alias, merging both sets; concrete vs concrete ⇒ today's check, now
  genuinely written-against-written. Every insert eagerly checks `IO ∈ lower ∧ Pure ∈ upper`.
- Readout: `must_io` ⇒ `IO`, `must_pure` ⇒ `Pure`, unconstrained ⇒ generalize as today.
- `infer.template_eff` stops returning `EffectPure` for a `TVar` template and manufactures a
  fresh unconstrained variable — a hardcoded `Pure` there is an assertion the call site is
  in no position to make, as its own comment already says.

**Why no read-time policy can work, which is what rules out the cheaper options.**
`[quiet, shout]` must be *accepted* as `List (Int -> Int !{IO})` and *rejected* as
`List (Int -> Int)`. It is the same expression; the two differ only in a return annotation
that has not been consulted when the `Cons` meet fires. So no verdict taken at that meet is
right for both — the meet must defer *and remember that `shout` was IO*, which is a lower
bound. Dually, the contract-pin pair differs only in a value arriving later, so a pin must
be remembered as an upper bound rather than treated as the value's effect.

Rejected alternatives: a post-pass over resolved types (the pin is still whichever side
bound first, so it is order-dependent at readout); rejecting only on annotation-origin
expected sides (regresses the pure-list case to accepted); "inference-origin variable ⇒
accept" (at the failing comparison the expected effect is already a concrete `EffectPure`
*inside* a pinned arrow, not a variable). A Koka-style open-instantiation fallback fixes
four of the five but leaves the contract pin order-dependent, and was not taken.

**As landed: the floor must RE-BIND, which the first implementation missed.** Recording a
bound is not enough, because `apply_effect_subst` reads the *binding*, and the first
implementation bound the freshened variable to whichever effect arrived first. `[shout,
quiet]` under a pure element type therefore laundered: `quiet`'s pure overwrote `shout`'s
IO and the annotation saw pure. Raising the floor now re-binds, since IO absorbs. Caught by
the mirrored fixture and by nothing else — the other element order rejected correctly, so a
single-order test would have shown green on exactly the bug this design exists to kill.
**Every fixture with an order gets its mirror**; that is the discipline, not a nicety.

### 6.4 Variance inside type constructors — per-constructor, not global

`List (Int -> Int)` versus `List (Int -> Int !{IO})`: invariant, or propagate polarity?

An earlier draft recommended **invariance** on the grounds that the corpus contains zero
occurrences, so it "costs nothing". That reasoning was wrong — zero occurrences measures
the corpus, not the shape's plausibility, and a one-line counterexample is legal today:

```sprout
fn run_all(fs: List (Int -> Int !{IO}), n: Int) -> Int !{IO} = ...
run_all([shout, tame], 1)        # mixed handler list; `tame` is pure
```

Verified legal under stage-1, and the elements meet the slot *through* the `TApp` arm.
Under invariance the pure element is rejected — a natural "list of handlers, some of them
pure" turned into an error. That is the same pattern as the in-tree route table, which
survives today only because its pure lambdas meet `Route`'s payload at the constructor
argument rather than through the `Vec`.

**But blanket covariance is unsound, and `Ref` is the counterexample** (found 2026-09-07,
after the draft that recommended it). `ref_write : forall a. Ref a -> a -> Unit !{IO}`
puts `a` in a parameter position, so `Ref` cannot be covariant in it. The classic aliasing
shape is writable in Sprout today:

```sprout
fn sink(cell: Ref (Int -> Int !{IO})) -> Unit !{IO} = ref_write(cell, shout)

fn main() -> Unit !{IO} = do
  pure_cell <- ref_new(tame)     # Ref (Int -> Int)
  sink(pure_cell)                # covariance would allow this
  f <- ref_read(pure_cell)       # f : Int -> Int, believed pure
  print(int_to_string(f(1)))     # prints "io"
```

Verified: type-checks, links, and prints `io`. Under covariance a correct implementation
would still accept it, because every step is individually legal — so covariance does not
merely miss this, it *blesses* it.

**So variance is per-constructor, not global.** A constructor whose parameter appears only
in result positions of its operations may be covariant; one whose parameter appears in an
argument position must be invariant. `List` and `Vec` are immutable and covariant; `Ref`
is invariant. That is the standard rule (Java's arrays are the famous counterexample to
getting it wrong, and are checked at runtime for exactly this reason).

The v0 surface is small enough to enumerate rather than infer, but **the enumeration is
stdlib-wide, not prelude-wide** — a first draft of this section said "`Ref` is the only
mutable container in the prelude", which is true and misleading. `stdlib/mutable.sprout`
exports two more: `MutVec a` (`mutvec_set(v, i, val)`, `mutvec_push`) and `MutMatrix a`
(`mutmatrix_set`). Both take the parameter as an argument, so all three are invariant, and
`MutVec` is load-bearing downstream — `stdlib/linalg.sprout` builds every `Vec3` through
it.

**Decide by enumeration, and make an unlisted constructor invariant by default** — a wrong
invariance is a rejected legal program, a wrong covariance is this hole, so the default
must fail towards rejecting. If a general rule is wanted later it is inferable from each
constructor's declared field positions, which is a separate change.

### 6.5 Peer-join sites need an effect LUB, not a polarity — SUPERSEDED, see §6.5b

*Kept as the reasoning that identified the blocker. Its diagnosis is right and its
prescription — a LUB/GLB by depth parity — was not what landed; §6.5b explains why the
cheaper fix is also the more principled one.*

Six of the 36 sites join two *peers*: `if`/`match` branch results, binary operands. At
the `if`-join the call is `unify_types(then_type, else_type)` — neither side is
expected, so no polarity value is correct:

```sprout
fn pick(b: Bool) -> (Int -> Int) = if b then tame else shout   # declared PURE result
```

Verified: compiles under stage-1 and **prints**. The join binds and swallows, one
branch's effect survives, and the return comparison sees whatever won.

Both available answers are wrong:

- **A fixed polarity** makes acceptance depend on branch order. The instrumented compiler
  rejects both orders with mirrored pairs (`!{IO} vs pure` and `pure vs !{IO}`), so
  whichever polarity is chosen accepts one spelling and rejects the other — same program,
  branches swapped.
- **Keeping today's swallow** preserves the launder above.

**Mechanism — and the LUB already exists.** At a join the two arrows' effects should be
*combined*, not unified: the joined arrow carries `merge_effects(e1, e2)`. Then the
ordinary directional comparison downstream does the rejecting.

`infer.merge_effects` is already exactly that least upper bound:

```sprout
| (types.EffectIO, _)          -> types.EffectIO      # IO absorbs
| (_, types.EffectIO)          -> types.EffectIO
| (types.EffectPure, other)    -> other               # pure is the identity
| (other, types.EffectPure)    -> other
| _                            -> merge_effect_labels(a, b)
```

It is **already called at the if-join** — but only for the *expression's*
own effect, never for the effects *inside* the branch types. The join unifies the two
arrow types and swallows the difference. So the change is to apply the function already
sitting at that call site one level deeper.

Walking the blocker through it: `if b then tame else shout` joins `Int -> Int` with
`Int -> Int !{IO}`; the LUB gives `Int -> Int !{IO}`; that meets the declared pure return
and is rejected there — the right place, with the return-position message. The uniform
cases are untouched: two pure branches join to pure, two IO branches to IO.

Order-independence comes free, because `merge_effects` is commutative on these cases —
which is precisely what a fixed polarity could not deliver.

**The GLB must be specified over all four constructors too** (§6.0's requirement, applied
here). `merge_effects` is the LUB and is already total; its dual is not written yet:

| GLB | `EffectPure` | `EffectIO` | `EffectVar` | `EffectRow` |
|---|---|---|---|---|
| **`EffectPure`** | `Pure` | `Pure` | `Pure` | hard-error |
| **`EffectIO`** | `Pure` | `IO` | bind, as LUB does | hard-error |
| **`EffectVar`** | `Pure` | bind | bind | hard-error |
| **`EffectRow`** | hard-error | hard-error | hard-error | hard-error |

`Pure` is the GLB's absorbing element exactly as `IO` is the LUB's, which is what makes the
parameter-position join demand the *weaker* obligation of the two branches. The row arms
hard-error for §6.3's reason: part 0 means no conformant signature builds one, so reaching
here is evidence of a hole, and silence would hide it.

**A greatest lower bound IS needed — an earlier draft of this section was wrong.** That
draft argued a join always produces a value, values sit in covariant position, so a GLB
never arises. That answers the wrong question: the GLB is needed *inside the join's own
recursion*, not because of where the joined value lands. Counterexample, legal today and
it runs:

```sprout
fn f1(cb: Int -> Int !{IO}, n: Int) -> Int !{IO} = cb(n)
fn f2(cb: Int -> Int,       n: Int) -> Int       = cb(n)
fn pick(b: Bool) -> ((Int -> Int !{IO}) -> Int -> Int !{IO}) = if b then f1 else f2
pick(false)(shout, 1)        # f2's PURE cb slot receives an IO function
```

Applying `merge_effects` at *every* arrow of the joined type yields exactly `pick`'s
declared return, so nothing downstream rejects and the launder survives the fix. The
correct join of two function types takes the **LUB at even depth and the GLB at odd
(parameter) depth** — standard function subtyping. Here that gives
`(Int -> Int) -> Int -> Int !{IO}`, against which `pick`'s declared return is correctly
rejected at the return comparison.

So part 2 is "join by depth parity", not "apply merge_effects". The alternative — reject
outright any join whose parameter arrows differ concretely — is simpler and adequate for
a corpus with zero such joins.

**Recommendation: depth parity.** The simpler rule rejects programs that are legal and
correct under the relation §5 establishes, and it does so at a *join*, which is the one
place a user cannot annotate their way out. Its only advantage is implementation effort,
and the GLB it avoids is the same `merge_effects` dual applied at odd depth — small next
to the 36-site polarity audit part 1 needs anyway.

**The six-site inventory is the wrong ontology.** A unification is a *join* whenever its
"expected" side is a fresh or accumulating variable, which is a dynamic property of the
unification, not a static property of the site. Demonstrated:

- **Match arms** join in `infer_branch_unify` (each arm unified against a
  progressively-bound fresh `ret_type`) — a site §6.2's table files under
  *(actual, expected)*, i.e. as directional. Annotate it by its bucket and mixed matches
  reject in one arm order and not the other.
- **List/constructor elements** join through the call site: `run_all([tame, shout], 1)`
  reports `pure vs !{IO}` while `run_all([shout, tame], 1)` reports `!{IO} vs pure` —
  the fold's winner flips with element order.
- **`++` operands** likewise, surfacing as "needs matching Semigroup operands".

So LUB at the `if`-join fixes `if` alone. The audit needs a rule keyed on *accumulation* —
widen wherever a result variable is folded over peers — rather than a list of six sites.
§9 needs match-order and element-order fixture pairs, not just the `if` pair.

### 6.5a Bounded effect variables already fixed most of §6.5 — measured, revision 7

**§6.5 above was written before part 1 landed and now overstates part 2's scope.** Every
row below was run against master `48419f7b`:

| shape | now |
|---|---|
| mixed `match` arms into a **pure** slot | **rejected** |
| mixed `match` arms into an `!{IO}` slot, both arm orders | accepted |
| mixed list elements, both element orders | correct in both (§9 fixtures) |
| `if c then pure else io` into a **pure** slot | **accepted — laundered, prints** |
| the same with the branches **swapped** | **rejected** — so branch order decides |
| the second-order `pick`/`f1`/`f2` case above | **accepted — laundered, prints** |
| `Ref` covariance, four shapes (§6.4a) | all rejected — see below |

The accumulation ontology is therefore no longer the problem. An accumulating site unifies
against a progressively-bound variable, so it goes through `unify_actual_expected`, and an
arm's observed `!{IO}` is recorded as a **floor** — the declared slot's ceiling then
contradicts it and the rejection lands at the declaration. Order-independence comes from
the bounds being monotone, not from commutativity of a LUB.

What still swallows is exactly `NoExpectation`: `unify_join` calls `unify_arrow_effects`,
which binds and records no bound at all. Six sites (`infer.sprout` 1572, 3713, 3770, 3796,
3889, 4061, plus `analysis_service_driver:390`).

**And the swallow is not symmetric**, which §6.5 assumed it was. `if c then io else pure`
is rejected today; only `if c then pure else io` launders. So "keep today's behaviour" is
not the neutral option §6.5 lists — it is already an order-dependent rule. The mirrored
reject pair §6.5 asks for (`effect_io_arrow_join_then` / `_else`) split exactly this way
when written: `_then` passed on arrival and `_else` was red. Part 2 (§6.5b) closed it.

**This suggests a smaller part 2 than "join by depth parity".** A join could record a floor
from whichever side is concretely `!{IO}` — reusing `record_lower`, already written — and
let the downstream declaration comparison reject, which is exactly how the `match` spelling
of the same program is already caught (row 1). Depth parity remains necessary for the
second-order row, since a floor at even depth is a *ceiling* at odd depth; but that is the
existing `bound_role` flip, not a new GLB table. **Cost this against §6.5's design before
implementing — the two differ, and the measurement above is the reason.**

**On the `Ref` row: a failed repro is not a proof.** *(This paragraph is unaffected by
§6.5b — `Ref` is still §6.4's, still unimplemented.)* Four shapes were tried — write-then-
read in one body, a pure cell into a declared `Ref (Int -> Int)` slot, an IO write through
a declared `Ref (Int -> Int !{IO})` parameter, and the same via a top-level `let` cell to
break tyvar sharing. All four reject, because the floor a write records travels through the
type variable the container's argument is bound to, whichever way the descent judged
variance. That is a *different* mechanism from the one §6.4 specifies, so covariance for a
mutable container is still wrong in principle and §6.4 is still the fix; what changed is
that no known program reaches it. Do not close §6.4 on this evidence — it is evidence about
four shapes.

### 6.5b What part 2 actually was — LANDED 2026-09-10

**An `if` had no result type of its own.** `infer_if_merge` unified the two branch types
against each other with `unify_join`, then typed the node as
`apply_subst(s5, then_type)` — so the then-branch became the node's type and the else
branch's effect was discarded. That single line is the whole defect: it is why the launder
existed, and why it was asymmetric (`if c then io else pure` was already rejected — the IO
branch was first, so it won).

**`match` never had the bug, and it shows the fix.** `infer_match` allocates a fresh
variable and unifies *every* arm against it as an actual (`infer_branch_unify`), typing
the node as the variable. No arm is any other arm's contract. `if` now does exactly this:

```sprout
v <- unifier.fresh(state)                       # a result type no branch owns
unify_actual_expected(…, then_type, ret_type)   # both branches are ACTUALs
unify_actual_expected(…, else_type, ret_type)
TIf(…, apply_subst(s6, ret_type), pos)          # was apply_subst(s5, then_type)
```

**Why this is better than §6.5's LUB/GLB by depth parity, not merely cheaper.** The LUB
design asks the join to *compute* a combined effect, which needs a lattice operation, its
dual, and a rule for which depth uses which. Handing the join a fresh variable asks it to
*compare* instead, and the comparison machinery — polarity, bounds, the `bound_role` flip
under a parameter — already exists from part 1 and is already the thing that gets depth
parity right. The second-order case §6.5 raised as the reason a GLB was unavoidable is
rejected by the landed change with no GLB written: the flip at odd depth turns the floor
into a ceiling on its own.

**The other five `unify_join` sites keep the old swallow.** They join binary operands
(`++`, numeric, comparison, equality) and a constructor result.

Revision 8 first recorded that these were safe because what they join is a *container*, so
the arrow sits under a type argument where the element variable carries the bounds. **That
was wrong, and the code says so plainly.** `unify_join` passes `NoExpectation`;
`bound_role` answers `NoBound` for it unconditionally; `arrow_effect_meet`'s `NoExpectation`
arm returns `Ok(unify_arrow_effects(…))`, which has no `Err` path at all. `unify_tapp`
carries the enclosing polarity into a type argument, so this holds at *every* depth. A join
records no bound and cannot reject, container or not.

What actually rejects in the pinned fixtures is the **declared return type** — all three
report `Return type mismatch in main.handlers` / `main.pick`, closing the bound at the
declaration exactly as `if` does. `[shout] ++ [quiet]` survives its join because both
element effects are *concrete*: a join has nothing to swallow unless one side carries an
open effect variable the other can bind to pure. Whether that is reachable through `++` is
open — filed in `BACKLOG.md`, not answered here.

### 6.5c Part 2 was order-independent only at first order — the GLB was unavoidable after all

Part 2's claim was checked at the top level and asserted generally. One arrow deeper it was
false: `if b then f2 else f1`, where `f1` takes an `!{IO}` callback and `f2` a pure one,
**compiled and ran `shout` inside `f2`**, whose signature is `fn f2(cb: Int -> Int, n: Int)
-> Int`. The mirror rejected. `match` behaved identically, so this predates part 2 rather
than being caused by it.

A 2×2 probe (branch order × declared slot) showed the declared slot made no difference at
all: **only branch order decided the verdict**, and the IO-first order *falsely rejected* a
legal program. Two defects, opposite directions.

Reading the bounds off the substitution rather than guessing from verdicts gave the cause in
one step. For the joined result variable `r`:

```
pure_first: joined = (Int -> Int !{$br/0}) -> Int -> Int !{$br/1}
            hi($br/0) = -          ← f2's pure slot recorded NO ceiling
            $br/0     = !{IO}      ← f1 then bound the slot to IO
```

`bind_bounded` receives `AsFloor` for this side, `freshen_arrow_effects` applied that one
role at every depth, and `record_lower` ignores `Pure` — so the branch demanding a pure
callback was forgotten, and the next branch bound the slot to IO.

The fix is two halves, and **neither works alone**:

1. `freshen_arrow_effects` carries the PARITY of each position. A parameter is
   contravariant, so its bound flips to `AsCeiling`, which `record_upper` does record. This
   is the GLB at odd depth §6.5 said was unavoidable — obtained by flipping which bound is
   recorded, not by computing a second lattice operation.
2. `bind_open` no longer binds IO over a pure ceiling. The ceiling **is** the meet, so it
   stands and the declaration judges it.

The slot now settles at the meet — the pure callback both branches accept — in either order.
The two orders still *render* differently (resolved pure, versus a variable carrying a pure
ceiling), which is why `test_effect_join_bounds` asserts that neither is IO rather than that
the two strings match.

This also removed the false rejection: `-> ((Int -> Int) -> Int -> Int !{IO})` is legal and
now compiles whichever branch is written first.

That is the fifth mechanism in this document asserted from a passing fixture and then
retracted (§6.4a's `Ref`, property 2's branch order, the `unify_join` rationale twice, and
§6.5b's order-independence). The fixtures were green every time. **A green test reports the
outcome and says nothing about which code path produced it** — read the path.

Two habits ended the streak, and both belong to §6.5c rather than to any of the retractions.
Every claim about an *order* gets its mirrored twin written at the same time — the
second-order fixture was the one case left unmirrored, and it was the one that was wrong.
And a claim about a *mechanism* is read off the mechanism: `test_effect_join_bounds` calls
the unifier directly and asserts on the bounds it records, which found in one run what three
rounds of inference-from-verdicts had each got wrong.

Three consequences worth stating. The `if` node's type is now a *variable* resolved through
the substitution rather than the then-branch's type — equivalent where the branches agree,
which is every previously-accepted program. Every `if` allocates one type variable, which
shifts fresh-variable numbering; `ir-golden-diff` reports **62 files, 0 differences**, so
none of it reaches the IR.

**And the branch-mismatch diagnostic names its two types in the other order**, which the
suite caught: `if x > 0 then x else false` reported `Type mismatch: Int vs Bool` and now
reports `Bool vs Int`. The message is positional (`unifier` prints its two arguments in
call order), so the old order was an artifact of the symmetric `unify_join(then, else)`
call that no longer exists. The new order is the `(actual, expected)` convention every
other `unify_actual_expected` site already uses — "you supplied `Bool` where `Int` was
expected" — so this is a small improvement rather than a cost.
`tests/conformance/type_error/if_branch_mismatch` pins it and says why.

### 6.6 Zero-arg calls — removed in revision 4, CLOSED on master in revision 6's window

Revision 3 added a fourth part here: `fn launder() -> Int = let t = io_thunk in t()`
compiled, linked and printed, with `--phase effects` reporting `declared pure, inferred
pure`. Revision 4 removed it — its stated mechanism ("read the arrow's effect at
`argc <= 0`") read an arrow that did not exist, and the collapse it sat on was a type bug
producing two non-effect symptoms, one of them memory-unsafe. It moved to
`docs/nullary-type-collapse-v0.md` as a prerequisite this design explicitly did not close.

That split has since been vindicated: the collapse landed on master 2026-09-08 (Option
A′ — `types.TThunk ret eff`, a real zero-parameter arrow carrying its own effect), and
the zero-arg laundering is closed. Re-verified 2026-09-09 by re-running revision 3's
probe: `launder` is now rejected with `performs IO but is declared pure` under rule 8.

Two consequences flow back into this design, both incorporated above: the boundary
inventory no longer lists a zero-arg escape (§8; §10's property-2 text), and part 1 gains
the `unify_tthunk` comparison site (§6.1) — a thunk is an arrow now, so part 1 must check
it like one or re-open at arity 0 exactly the hole the nullary fix closed.

## 7. Syntax, types, and errors

No syntax change. **No IR change — check-only, so `ir-golden-diff` should report 0.** That
promise holds only because revision 4 narrowed the scope: it was false while §6.6 was part
of this design, since every resolution of the nullary collapse is ABI-visible. Parts 1–3
add comparisons and change no lowering.

Type-system impact: effect comparison becomes directional at arrow positions. Effect
*variables* are unaffected — they still bind to anything, which is what keeps
`list_each(print, xs)` legal.

Diagnostic — and the wording above what `unify_tfunc` can actually produce was too
ambitious. That function receives two types and two substitutions: no names, no
positions, no AST. An earlier draft promised

```
… the parameter `f` of `list_map` is declared `Int -> Int` …
```

which cannot be emitted: the callee's whole arrow is unified in ONE `unify_types` call,
so *which* parameter failed is not known without decomposing
argument unification per-parameter or threading an error context. `unifier.list_vec_hint`
exists for exactly this reason — the unifier can only speak name-agnostically.

What is deliverable: the unifier supplies the effect pair, and the wrapping site adds the
callee name it already holds (`callee_display`):

```
a function that performs IO cannot be used where a pure one is required
(`!{IO}` vs pure), in the call to `list_map` — spec-v0.md §7
```

Naming the offending parameter needs per-argument unification, which is a separate
change worth costing on its own merits.

The reverse direction produces no diagnostic. Pre-existing fixtures matching
`performs IO but is declared pure` via `grep -qF` are untouched — that wording stays on
the declaration-boundary rule.

**Fixed in revision 4:** `tests/conformance/type_error/effect_io_arrow_into_pure_param.err`
held `performs IO but is declared pure` — the declaration-boundary wording, which the arrow
message above does not contain — so the fixture would have stayed RED after a *correct*
implementation. It now carries the first line above, and is quarantined in
`test-type-errors`' xfail list so the gate was green while the check was unimplemented and
went red with `UNEXPECTED MATCH` when it landed — the xfail list is now empty. That makes the wording load-bearing: change
the message and the fixture stops self-healing, so change both together.

## 8. Composes with, does not replace, the declaration-boundary gaps

Two smaller holes live at declaration boundaries and are invisible to §6 because no arrow
comparison occurs:

- `fn sneak(n: Int) -> Int !{e} = shout(n)` — concrete-IO body under a declared effect
  variable. Pair `(!{e}, !{IO})`; `effect_report_is_gap` matches only `(pure, !{IO})`.
- `fn pure_apply(g: Int -> Int !{e}, n: Int) -> Int = g(n)` — declared pure, performs the
  effect of a caller-chosen parameter. Pair `(pure, !{$eN})`.

Both measured at **0 occurrences** in-tree and downstream. They are two extra arms on
`unifier.effect_report_is_gap`, worth landing in the same change but sequenced after
§6 — the second needs a guard distinguishing an effect variable the signature quantifies
from one that merely leaked in from a callee, which is subtle enough to deserve its own
fixtures. Detail is in this document's git history (the withdrawn
`effect-var-rigidity-v0.md`).

Shapes closed by neither §6 nor these arms — an earlier draft listed one and the review
found two more, so treat this inventory as *known incomplete* rather than exhaustive:

- `fn mk(n: Int) -> (Int -> Int !{e}) = shout` — an effect variable in a returned arrow.
  A control-tested grep finds **0** occurrences in either repo: a correctness footnote.
- **The class/instance boundary of §6.1a**, in both its concrete (`class` pure /
  `instance !{IO}`) and effect-variable (`class !{e}`) forms. Not a footnote — plain
  typeclass code, and the reason §6.1a adds a third check.
- **A top-level `let` initializer**, added revision 5. `let seeded = shout(41)` compiles,
  binds `seeded : Int`, and runs the IO at startup; `--phase effects` does not enumerate
  top-level `let`s at all, so the census cannot see it either (both verified 2026-09-08).
  Parts 0–3 miss it — there is no arrow comparison, no join and no class. Already tracked
  in `BACKLOG.md` and `docs/effect-enforcement-v0.md`, and spec §5.2 already prohibits it
  normatively; it is listed here because a design that omits a boundary the repo *already
  knows about* will mislead its own landing note. Not in scope: the fix couples to the
  value restriction, which is its own decision.

The lesson for §4's method: a corpus census counts what the instrument can *see*, and
this instrument sees arrow comparisons. Two boundaries that carry effects are not arrow
comparisons, and a zero says nothing about them.

## 9. Tests

TDD — the reject fixtures fail (compile clean) before the change and must pass after.

Reject (`tests/conformance/type_error/`):
- `effect_unknown_label` — §6.0, `fn f() -> Int !{NOPE}`. **Write this one first**: it is
  the cheapest of the set and every other reject fixture is defeatable while it fails.
- `effect_unknown_label_launders` — the full §6.0 program, `list_map(sneak, xs)` under a
  pure signature. Distinct from the above on purpose: rejecting the *annotation* and closing
  the *hole* are different claims, and a fix that only warned would pass the first.
- `effect_io_arrow_into_pure_param` — **already written**, currently RED: the `list_map`
  case from §1. Its `.err` needs the §7 wording, not the declaration-boundary wording it
  currently carries.
- `effect_io_arrow_as_pure_return` — `fn mk(n: Int) -> (Int -> Int) = shout`.
- `effect_io_arrow_into_pure_field` — the record-field case, `Box(f = shout(_))`.
- `effect_io_arrow_second_order` — a callback-of-a-callback, pinning that the polarity
  flip in §6.3 is applied rather than assumed.
- `effect_instance_strengthens_class` — §6.1a, concrete form. Plus an `!{e}`-class variant.
  **Landed**, plus two the list did not anticipate:
  `effect_instance_var_under_pure_class` (the cell §6.1a left open) and
  `effect_instance_strengthens_class_before_decl` (the ordering bypass, found in
  self-review). Accept side: `effect_instance_weakens_class_ok` (the five accepting cells
  in one program) and `effect_class_method_name_not_unique_ok` (both bare-name hazards).
  Unit: `test_declared_effect_subsumption.spr`, all nine cells plus both row arms.
- `effect_io_arrow_join_then` and `effect_io_arrow_join_else` — §6.5, **the same program
  with the branches swapped**. Two fixtures, not one: a single one passes under a wrong
  fixed polarity, and the pair is what makes order-independence testable at all.
  **Landed**, and the pair earned its keep: `_then` passed on arrival and only `_else` was
  red, which is the asymmetry §6.5 assumed away. Plus `effect_io_arrow_join_second_order` —
  §6.5's own `f1`/`f2` counterexample, the one it said needed a GLB; it is rejected by the
  parameter-position flip alone (§6.5b).

Accept (`tests/conformance/run/`) — the over-correction guards:
- `effect_pure_arrow_into_io_slot_ok` — §5's direction, both witnesses.
- `effect_polymorphic_combinator_ok` — `list_each(print, xs)` and a pinned `!{e}` used at
  both IO and pure, pinning that variables still bind freely.
- `effect_mixed_handler_list_ok` — §6.4's `run_all([shout, tame], 1)`, pinning that
  polarity propagates into an *immutable* type-constructor argument.
- `effect_join_arrow_order_ok` — **landed as one program instead of the planned
  `effect_join_uniform_ok`**: mixed branches into an `!{IO}` slot in *both* orders, plus
  both-pure and both-IO joins. The uniform cases alone would not have caught a join that
  rejects a legal mixed one, and the mirrors are what pin order-independence on the accept
  side as well as the reject side.

And one more reject fixture for §6.4, which the accept list above cannot cover:
- `effect_io_arrow_through_ref` — the `Ref` aliasing shape. It must be rejected at
  `sink(pure_cell)`. Pair it with `effect_mixed_handler_list_ok` deliberately: the two
  differ only in the constructor, so together they pin that variance is per-constructor
  and not a single global switch. A build that made both pass or both fail would satisfy
  neither.

Gates: full `just test`, `compile-examples-stage1` (the two HTTP examples are the §5
witnesses and must stay green), `effect-report-smoke`, `ir-golden-diff` (expect 0), plus
a downstream run against `uncharted-suns`.

**A migration gap the zero-cost measurement cannot see — REALIZED, then RESOLVED as
policy (2026-09-10).** Confirmed by running: `fn map_io(xs: List Int) -> List Int !{IO} =
list_map(shout, xs)` is rejected, an honest `!{IO}` caller and all. Inference never
produces an effect-polymorphic HOF: `fn helper(f, n) = f(n)` generalises to
`forall a b. (a -> b) -> a -> b` with *concrete pure* arrows, so `helper(shout, n)`
laundered before part 1 and is correctly rejected now. The only remedy is a hand-written
`!{e}`, and `list_map`'s callback is a plain pure arrow. The corpus measures zero because
nothing in it does that yet, which is exactly why the number is silent here.

**This paragraph used to end "annotating `list_map`/`list_fold` with `!{e}` is the obvious
follow-up." That is wrong, and `docs/effect-polymorphism-policy-v0.md` — written after it
— is why.** A callback slot may be effect-polymorphic exactly when the published contract
fixes both the *order* and the *multiplicity* of its invocation. `list_fold` documents
"left fold" and already carries `!{e}`; `list_map` documents neither, and §5 of that policy
grades `Functor.fmap` **pure** for the same reason — an `!{e}` map would be an accidental
promise that every future instance, `Dict` and `Set` included, iterates in a fixed order.

So the rejection is the policy working, not a regression, and the operation is expressible
today through the combinator whose contract does pin both — verified by running, printing
in source order:

```sprout
fn map_io(xs: List Int) -> List Int !{IO} =
  list_reverse(list_fold(\ (acc, x) -> Cons(shout(x), acc), Nil, xs))
```

What remains is ergonomics, not capability: a `list_traverse` that states its order in its
own contract. That is `BACKLOG.md`'s existing `traverse`/`sequence` entry, not a new one.

## 10. Spec and docs

- `docs/spec-v0.md` §7 — **normative, and the blocking edit.** Two of the three properties
  change. Exact replacement text below; it lands *with* the implementation, not before,
  since the spec is normative and must not describe a check that does not run.

  **Property 2** currently reads:

  > **Unification of an arrow's effect is total.** It binds effect variables and never
  > fails, so two arrows whose effects differ are not thereby a type error and a program's
  > acceptance never depends on effect inference reaching a particular answer mid-way.
  > Rejection happens at the declaration boundary and nowhere else.

  becomes:

  > **An arrow's effect is compared where a function value is supplied against an expected
  > type**, not only at a declaration. The relation is subsumption, `pure ⊑ IO`, and it is
  > one-way: a pure function may stand where an effectful one is required, never the
  > reverse. The comparison is directional — the position decides which side is expected —
  > and it descends into arrows with the polarity flipped at each parameter.
  >
  > Where two types are joined as **peers** — the branches of an `if` or `match`, the
  > elements of a list, the operands of a binary operator — neither side is expected. The
  > joined arrow takes the least upper bound of the two effects at result positions and the
  > greatest lower bound at parameter positions, so the join is independent of branch order
  > and the ordinary directional comparison downstream does the rejecting.
  >
  > Unification of an effect *variable* is still total: a variable binds to anything and
  > never fails, which is what keeps effect-polymorphic combinators usable at both pure and
  > effectful instantiations. Inside a type constructor the comparison propagates only where
  > that constructor's parameter is covariant; a constructor whose operations take the
  > parameter as an argument — `Ref`, `MutVec`, `MutMatrix` — is invariant in it.
  >
  > One position is **not** covered and is stated so the property is not read as total: a
  > top-level `let` initializer, which §5.2 prohibits from being effectful but which
  > nothing checks. A zero-parameter function carries its effect on its `() -> T` arrow
  > and is compared like any other arrow.

  **Property 3** currently ends "Every imprecision in effect inference must therefore fail
  towards accepting a program, not rejecting one." As written that forbids this design:
  the whole point is that a *known* concrete mismatch is now rejected. Narrow it to what
  it was actually protecting:

  > **At a comparison, an unresolved effect variable binds rather than fails.** `!{e}` is
  > neither satisfied nor violated until instantiation, so where a comparison does not know
  > which effect a variable will take, it binds and continues. This is a rule about
  > *unification*, and it licenses nothing elsewhere: two concrete effects that differ are
  > compared and may be rejected, and the rules governing what a **signature** may declare
  > (rules 8, 9 and 11) are unaffected by it.
  >
  > An effect *label* that is not `IO` is **ill-formed**, not a variable. Rule 9 admits a
  > single concrete effect, a single lowercase variable, or nothing; anything else is
  > rejected where the annotation is read. Without this the rule above is an opt-out — an
  > unrecognised label would parse as a variable, bind against everything, and carry IO
  > through a pure signature unchallenged.

  Property 1 (subsumption not equality, at declarations) stays as written and is the same
  relation this extends to arrows.

  **Landed already, ahead of the implementation:** the enforcement note's opening claim
  that "a missing `!{IO}` now means the compiler has verified the function performs no IO"
  was false and is corrected in place, because a false normative claim should not wait on
  a fix. It now scopes the guarantee to a declaration's own body and names the four
  escaping boundaries. That correction is independent of this design and stands whether or
  not it is approved.
- `docs/effect-enforcement-v0.md` — add a section recording that §14's premise was
  narrower than stated, and cross-reference this document.
- `BACKLOG.md` — the deferred *rigid effect variables* item (skolemising a declared effect
  variable while checking its own body, mirroring `unifier.fresh_skolem` for types) stays
  deferred; it becomes worthwhile only alongside open rows.
