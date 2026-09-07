# Effect subsumption at arrow positions (v0)

Status: **DESIGN, awaiting approval.** Revised after review (2026-09-07, round 2), which
found two blockers in the previous revision; both are independently reproduced and both
now have a mechanism.

The fix has **four parts**, at four different boundaries. No one subsumes another. Each
review round has found one more, so treat this list as *known incomplete*: revision 1 had
only part 1, revision 2 added parts 2 and 3, revision 3 added part 4.

| # | boundary | how the effect escapes | mechanism | § |
|---|---|---|---|---|
| 1 | a function value entering a slot | compared, wrong direction allowed | directional comparison, polarity-annotated | §6.3 |
| 2 | a peer join (`if`/`match`/elements/operands) | unified, difference swallowed | effect LUB **and GLB by depth parity** | §6.5 |
| 3 | an instance method vs its class signature | never compared — scheme level | declared-vs-declared comparison | §6.1a |
| 4 | a zero-arg call on a local/expression callee | **dropped before any comparison** | read the arrow's effect at `argc <= 0` | §6.6 |

Part 4 is different in kind from the other three, which all compare two effects
somewhere. Here nothing is compared, which is why an instrumented compiler that rejects
every concrete pure/IO arrow meet reports **zero errors** on it.

**Migration cost, now measured for three of four parts:**

| part | in-tree | downstream | note |
|---|---|---|---|
| 1 | 0 | 0 | 2 flagged sites, both the safe direction (§5) |
| 2 | 0 | 0 | concrete joins only; variable-effect joins unmeasured |
| 3 | 0 | 0 | **no method-level effect annotation exists anywhere** — 259 in-tree class/instance method signatures and 3 downstream, all pure, so no instance can differ from its class |
| 4 | 0 | unmeasured | `BACKLOG.md` census; but see §6.6 |

Part 2's zero is for the corpus as it stands; the mechanism must still newly reject
§6.5's currently-legal example, which is a correctness requirement rather than a
migration cost.

Corrected from the previous revision: §6.5's "no GLB is needed" was **wrong** and its
six-site inventory was the **wrong ontology** (§6.5); §6.1a's bare-name lookup is
**unsound under name shadowing and duplicate method names** (§6.1a); §6.1's universality
claim is false; §6.4's invariance recommendation is withdrawn; §7 promised a diagnostic
the check cannot produce; the audit is 36 sites, not 35.

Requires amending `docs/spec-v0.md` §7 note property 2, which states the behaviour this
document removes. Supersedes the withdrawn `effect-var-rigidity-v0.md`, whose scope
(effect *variables* only) was a corner of the hole described here.

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

Implemented at `unifier.unify_arrow_effects` (`unifier.sprout:425`), which swallows the
`Err`. "Rejection happens at the declaration boundary and nowhere else" is exactly the
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
a separate and much smaller change that composes with this one.

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

### 6.1 `unify_tfunc` covers every arrow COMPARISON — which is not every boundary

The detector lives in `unify_tfunc` and fires at every position where two arrows are
unified. Confirmed across eight: call argument, inline lambda argument, function return,
record field, ADT constructor payload, tuple component, list element, and `wrap` payload.
`unify_tfunc_owned` delegates to it, so one check there covers all of them and no
per-site enumeration is needed for coverage *of arrow comparisons*.

### 6.1a But an effect can cross without any arrow comparison — BLOCKER

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

An instance may therefore *strengthen* the effect its class declares, and every caller
dispatching through the class scheme inherits the class's weaker claim.

**Mechanism.** A third check, at instance-method checking, comparing two *declared*
effects — no inference and no unification involved:

- The class method's declared effect is already in scope. `register_class_method`
  (`infer.sprout:9170`) builds the class method's scheme from its `effects_maybe` and
  registers it under the bare method name, and that registration happens at `:7504`,
  before `check_instance_methods` at `:7510`. So inside `check_instance_method` the class
  effect is `types.scheme_effects(dict_get(name, env))`.
- The instance method's declared effect is `eff_maybe`, already threaded through
  `instance_method_checked` (`:9358`).
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
earlier draft described. `class_method_mode_error` (`:9409`) shows the lookup pattern but
inherits the same bare-name weakness.

Note what this does **not** duplicate: rule 8 already checks an instance method's *body*
against its *own* declaration (`effect_pure_instance_method_does_io.spr`, cited at
`:9339`). The missing edge is own-declaration against class-declaration. Both are needed;
neither implies the other.

An effect variable on the class method (`class ... fn calc(x: a) -> Int !{e}`) is the
same check with the same relation, since a variable is not above a concrete effect —
consistent with §8's first arm.

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
convention. Full classification of the 35 sites:

| convention | count | examples |
|---|---|---|
| (actual, expected) | ~22 | 8929 return, 5095 record field, 397, 1256, 4096, 4510, 4957, 5337, 5729, 9376 |
| (expected, actual) | ~7 | 1712 call argument, 3223, pattern sites 4142/4170/4236, 3705, 1064 |
| **peer — neither side expected** | ~6 | 1283 if-join, binop operands 3357/3414/3440/3533, 1680 |

So there *is* a dominant convention and an earlier draft of this section overstated the
chaos: it claimed "only the call site follows" the left=expected convention documented at
`unifier.sprout:540`, which is wrong — the pattern-checking sites follow it too. The
conclusion survives regardless: two conventions plus a peer category mean argument order
cannot carry direction. `unifier.list_vec_hint` already works around the same flip for
types by writing a direction-agnostic diagnostic.

**So the design passes polarity explicitly** rather than inferring it from position. The
peer category is the one that breaks a two-valued flag — see §6.5.

**Audit rule: annotate polarity, never reorder arguments.** `unifier.sprout:540` binds
right→left so the left (expected/older) `TVar` stays canonical, which `@fwd`/`@eta_fwd`
marker reachability depends on. Adding a flag preserves that bit-for-bit; "normalising" a
site by swapping its arguments would silently change which tyvar survives as canonical.
That failure would surface only in dictionary forwarding through `where C m` functions —
concrete instances devirtualize the dict away — so it would not show up in a corpus sweep.

**One caller lives outside `infer`**, making the audit 36 sites:
`analysis_service_driver.unify_ok` (:394), the type-search matcher. A directional rule
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

At `unify_tfunc`, with polarity resolving which side is expected:

```
expected = Pure, actual = IO   -> reject
expected = IO,   actual = Pure -> accept        (§5)
either side a variable         -> bind as today (keeps every !{e} combinator working)
```

Then audit the ~35 call sites to pass the correct initial polarity. That audit is the
bulk of the work and cannot be skipped: a site left on the wrong polarity is a silent
hole or a false rejection, and neither shows up in a corpus that has zero violations.

### 6.5 Peer-join sites need an effect LUB, not a polarity — BLOCKER

Six of the 36 sites join two *peers*: `if`/`match` branch results, binary operands. At
`infer.sprout:1283` the call is `unify_types(then_type, else_type)` — neither side is
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

`infer.merge_effects` (`:766`) is already exactly that least upper bound:

```sprout
| (types.EffectIO, _)          -> types.EffectIO      # IO absorbs
| (_, types.EffectIO)          -> types.EffectIO
| (types.EffectPure, other)    -> other               # pure is the identity
| (other, types.EffectPure)    -> other
| _                            -> merge_effect_labels(a, b)
```

It is **already called at the if-join** (`:1290-1291`) — but only for the *expression's*
own effect, never for the effects *inside* the branch types. The join unifies the two
arrow types and swallows the difference. So the change is to apply the function already
sitting at that call site one level deeper.

Walking the blocker through it: `if b then tame else shout` joins `Int -> Int` with
`Int -> Int !{IO}`; the LUB gives `Int -> Int !{IO}`; that meets the declared pure return
and is rejected there — the right place, with the return-position message. The uniform
cases are untouched: two pure branches join to pure, two IO branches to IO.

Order-independence comes free, because `merge_effects` is commutative on these cases —
which is precisely what a fixed polarity could not deliver.

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
rejected at `:8929`.

So part 2 is "join by depth parity", not "apply merge_effects". The alternative — reject
outright any join whose parameter arrows differ concretely — is simpler and adequate for
a corpus with zero such joins; the doc must pick one, and the simpler one should be
chosen only with the restriction stated.

**The six-site inventory is the wrong ontology.** A unification is a *join* whenever its
"expected" side is a fresh or accumulating variable, which is a dynamic property of the
unification, not a static property of the site. Demonstrated:

- **Match arms** join at `:4096` (`infer_branch_unify`, each arm unified against a
  progressively-bound fresh `ret_type`) — a site §6.2's table files under
  *(actual, expected)*, i.e. as directional. Annotate it by its bucket and mixed matches
  reject in one arm order and not the other.
- **List/constructor elements** join through the call site: `run_all([tame, shout], 1)`
  reports `pure vs !{IO}` while `run_all([shout, tame], 1)` reports `!{IO} vs pure` —
  the fold's winner flips with element order.
- **`++` operands** likewise, surfacing as "needs matching Semigroup operands".

So LUB-at-`:1283` fixes `if` alone. The audit needs a rule keyed on *accumulation* —
widen wherever a result variable is folded over peers — rather than a list of six sites.
§9 needs match-order and element-order fixture pairs, not just the `if` pair.

### 6.6 The effect is DROPPED at a zero-arg call on a local callee — BLOCKER

The other three parts all compare two effects. This one has nothing to compare:

```sprout
fn io_thunk() -> Int !{IO} = do { print("io"); 7 }

fn launder() -> Int =
  let t = io_thunk
  in t()                       # compiles, links, PRINTS
```

Verified: runs and prints; `--phase effects` reports `main.launder: declared pure,
inferred pure`; and the instrumented compiler — which rejects every concrete pure/IO
arrow meet anywhere — gives **0 errors**. That silence is the evidence: no comparison
takes place, so parts 1–3 are all structurally incapable of catching it.

Control: the direct spelling `fn launder() -> Int = io_thunk()` **is** rejected by rule 8.
The hole is precisely the local or expression callee.

**Mechanism.** `infer.call_effect_of` with `argc <= 0` returns `scheme_effects(scheme)`
raw, and `infer_call_general`'s `arrows_effect(t, argc)` returns `Pure` for `argc <= 0`
with no scheme fallback. A `let`-bound local's `mono()` scheme carries `EffectPure` at
scheme level while the real effect sits on the arrow, so the arrow effect is discarded.
The fix is to read the arrow's effect at `argc <= 0` rather than returning `Pure`.

`BACKLOG.md` recorded both twins but classified them as "conservative (accept, do not
reject)" with "nothing in-tree hits either" — corrected 2026-09-07. Accepting a program
that runs IO under a pure signature is not conservatism, and the shape needs no record
field or `if`; a plain `let` reaches it.

A second spelling, `(if b then t1 else t2)()`, also typechecks `(pure, pure)` but fails
at emit with "ast_to_ir: indirect call not yet supported". It becomes live the day
indirect calls land, so fixing this is also a prerequisite for that work.

### 6.4 Variance inside type constructors — recommendation WITHDRAWN

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

So polarity should propagate into type-constructor arguments. If invariance is chosen
anyway for implementation reasons, this example belongs in the doc as an acknowledged
regression with a named workaround, not as a cost of zero.

## 7. Syntax, types, and errors

No syntax change. No IR change — check-only, so `ir-golden-diff` should report 0.

Type-system impact: effect comparison becomes directional at arrow positions. Effect
*variables* are unaffected — they still bind to anything, which is what keeps
`list_each(print, xs)` legal.

Diagnostic — and the wording above what `unify_tfunc` can actually produce was too
ambitious. That function receives two types and two substitutions: no names, no
positions, no AST. An earlier draft promised

```
… the parameter `f` of `list_map` is declared `Int -> Int` …
```

which cannot be emitted: the callee's whole arrow is unified in ONE `unify_types` call
(`infer.sprout:1712`), so *which* parameter failed is not known without decomposing
argument unification per-parameter or threading an error context. `unifier.list_vec_hint`
exists for exactly this reason — the unifier can only speak name-agnostically.

What is deliverable: the unifier supplies the effect pair, and the wrapping site adds the
callee name it already holds (`callee_display`, `infer.sprout:1697`):

```
a function that performs IO cannot be used where a pure one is required
(`!{IO}` vs pure), in the call to `list_map` — spec-v0.md §7
```

Naming the offending parameter needs per-argument unification, which is a separate
change worth costing on its own merits.

The reverse direction produces no diagnostic. Pre-existing fixtures matching
`performs IO but is declared pure` via `grep -qF` are untouched — that wording stays on
the declaration-boundary rule. **But the fixture already written for this change is not:**
`tests/conformance/type_error/effect_io_arrow_into_pure_param.err` currently holds
`performs IO but is declared pure`, which the new arrow message does not contain, so it
would stay RED after a correct implementation. Its `.err` must carry the new wording, and
its comment still points at the deleted `effect-var-rigidity-v0.md`.

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

The lesson for §4's method: a corpus census counts what the instrument can *see*, and
this instrument sees arrow comparisons. Two boundaries that carry effects are not arrow
comparisons, and a zero says nothing about them.

## 9. Tests

TDD — the reject fixtures fail (compile clean) before the change and must pass after.

Reject (`tests/conformance/type_error/`):
- `effect_io_arrow_into_pure_param` — **already written**, currently RED: the `list_map`
  case from §1. Its `.err` needs the §7 wording, not the declaration-boundary wording it
  currently carries.
- `effect_io_arrow_as_pure_return` — `fn mk(n: Int) -> (Int -> Int) = shout`.
- `effect_io_arrow_into_pure_field` — the record-field case, `Box(f = shout(_))`.
- `effect_io_arrow_second_order` — a callback-of-a-callback, pinning that the polarity
  flip in §6.3 is applied rather than assumed.
- `effect_instance_strengthens_class` — §6.1a, concrete form. Plus an `!{e}`-class variant.
- `effect_io_arrow_join_then` and `effect_io_arrow_join_else` — §6.5, **the same program
  with the branches swapped**. Two fixtures, not one: a single one passes under a wrong
  fixed polarity, and the pair is what makes order-independence testable at all.

Accept (`tests/conformance/run/`) — the over-correction guards:
- `effect_pure_arrow_into_io_slot_ok` — §5's direction, both witnesses.
- `effect_polymorphic_combinator_ok` — `list_each(print, xs)` and a pinned `!{e}` used at
  both IO and pure, pinning that variables still bind freely.
- `effect_mixed_handler_list_ok` — §6.4's `run_all([shout, tame], 1)`, pinning that
  polarity propagates into type-constructor arguments.
- `effect_join_uniform_ok` — a join of two arrows with the *same* effect, pinning that
  §6.5's LUB does not reject the ordinary case.

Gates: full `just test`, `compile-examples-stage1` (the two HTTP examples are the §5
witnesses and must stay green), `effect-report-smoke`, `ir-golden-diff` (expect 0), plus
a downstream run against `uncharted-suns`.

## 10. Spec and docs

- `docs/spec-v0.md` §7 — **normative, and the blocking edit**: note property 2 must be
  replaced. "Rejection happens at the declaration boundary and nowhere else" becomes a
  statement that an arrow's effect is compared directionally wherever a function value
  crosses a boundary, with `pure ⊑ IO` one-way. Property 3 ("an unresolved effect variable
  is accepted") stays as written.
- `docs/effect-enforcement-v0.md` — add a section recording that §14's premise was
  narrower than stated, and cross-reference this document.
- `BACKLOG.md` — the deferred *rigid effect variables* item (skolemising a declared effect
  variable while checking its own body, mirroring `unifier.fresh_skolem` for types) stays
  deferred; it becomes worthwhile only alongside open rows.
