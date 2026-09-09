# Effect subsumption at arrow positions (v0)

Status: **Parts 0 and 3 LANDED 2026-09-09 (§6.0, §6.1a). Parts 1–2 DESIGN, awaiting
approval.**
Revision 6 (2026-09-09). The nullary type collapse
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
| 1 | 0 | 0 | 2 flagged sites, both the safe direction (§5) |
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
unchanged. Spec §7 rule 9 and property 3 carry the rule; parts 1–3 below are unaffected
and still awaiting approval.

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

- **bind** is today's total unification, and is what keeps every `!{e}` combinator working.
  It is the one arm an attacker can aim at, which is why part 0 must make an unknown label
  ill-formed rather than a variable.
- **unreachable** is a claim, not a shrug: rule 9 admits only a single concrete effect, a
  single variable, or nothing, and part 0 enforces it — so no *conformant* signature builds
  an `EffectRow`. Inference still builds rows internally (`merge_effects`), so the arm must
  exist and must **hard-error** rather than fall through to `bind`. A row reaching this
  comparison means part 0 has a hole, and the loud failure is how that gets found.

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

### 6.5 Peer-join sites need an effect LUB, not a polarity — BLOCKER

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
`test-type-errors`' xfail list so the gate is green while the check is unimplemented and
goes red with `UNEXPECTED MATCH` when it lands. That makes the wording load-bearing: change
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

Accept (`tests/conformance/run/`) — the over-correction guards:
- `effect_pure_arrow_into_io_slot_ok` — §5's direction, both witnesses.
- `effect_polymorphic_combinator_ok` — `list_each(print, xs)` and a pinned `!{e}` used at
  both IO and pure, pinning that variables still bind freely.
- `effect_mixed_handler_list_ok` — §6.4's `run_all([shout, tame], 1)`, pinning that
  polarity propagates into an *immutable* type-constructor argument.
- `effect_join_uniform_ok` — a join of two arrows with the *same* effect, pinning that
  §6.5's LUB does not reject the ordinary case.

And one more reject fixture for §6.4, which the accept list above cannot cover:
- `effect_io_arrow_through_ref` — the `Ref` aliasing shape. It must be rejected at
  `sink(pure_cell)`. Pair it with `effect_mixed_handler_list_ok` deliberately: the two
  differ only in the constructor, so together they pin that variance is per-constructor
  and not a single global switch. A build that made both pass or both fail would satisfy
  neither.

Gates: full `just test`, `compile-examples-stage1` (the two HTTP examples are the §5
witnesses and must stay green), `effect-report-smoke`, `ir-golden-diff` (expect 0), plus
a downstream run against `uncharted-suns`.

**A migration gap the zero-cost measurement cannot see.** Inference never produces an
effect-polymorphic HOF: `fn helper(f, n) = f(n)` generalises to
`forall a b. (a -> b) -> a -> b` with *concrete pure* arrows, so `helper(shout, n)`
launders today and part 1 will correctly reject it. The only remedy is a hand-written
`!{e}`, and the prelude does not offer one for the shape people reach for — `list_map`'s
callback is a plain pure arrow, and only `list_each` takes `!{e}` (returning `Unit`). So
after this lands, **"map an IO function over a list" has no stdlib spelling.** The corpus
measures zero because nothing in it does that yet, which is exactly why the number is
silent here. Annotating `list_map`/`list_fold` with `!{e}` is the obvious follow-up and
should be costed with this change rather than discovered by the first user.

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
