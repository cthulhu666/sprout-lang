# Typeclass Policy (v0)

Status: **proposed — non-normative, pending approval.** Nothing here is implemented.
`docs/spec-v0.md` is the normative source of truth; this doc proposes three changes to it and one
written-down freeze. Companion to `docs/haskell-lessons-learned.md` (traps #4, #10, #12, #13) and
`docs/cross-language-design-lessons.md` §B (coherence & dispatch prior art, not duplicated here).

---

## 1. Problem statement

Sprout's typeclasses sit at roughly the Haskell 98 position: one type parameter per class, instance
heads keyed on the head *constructor*, overlap an unconditional error. That position is cheap to
hold and expensive to leave. Nothing currently records that it *is* held, and three things are open
at once:

1. **There is no orphan rule.** `spec-v0.md` §5.6.4 states it plainly: closing the record-opacity
   bypass "needs an orphan-instance rule, which v0 does not have." Coherence today rests on
   whole-program compilation from source plus a single prelude — a property of the build model, not
   a guarantee of the language.
2. **The first rung of the relaxation ladder is already filed as work.** `BACKLOG.md` P2 "Widen the
   `@inst:` key to full-head matching (GHC `FlexibleInstances` position)" is queued as an
   improvement, and correctly notes that the relaxation and full-head matching "arrive together."
3. **A type that wants two instances of one class has no answer but a newtype.** `Sum`/`Product`
   over `Int` is the canonical shape. Sprout has `wrap`, so the workaround exists; it is the only
   route.

Why the sequence matters more than any single rung: Haskell's extension set is the observable
consequence of leaving this position without a policy. Multi-parameter classes introduce ambiguity
at the use site, which needs functional dependencies or explicit type application; relaxing instance
heads needs full-head matching, which admits overlap, which needs specificity ordering, which ends
at an escape hatch that picks an instance arbitrarily. Each rung is individually reasonable. The
sequence is not, and no rung is where anyone intended to stop.

Sprout is on rung zero with rung one already in the backlog. The gap this doc closes is a *stated
policy*, plus the two additive features that make the policy liveable rather than merely
restrictive.

---

## 2. Goals and non-goals

**Goals.**

- **G1.** Make the coherence guarantee **total and stated**, not an artifact of the build model.
- **G2.** Give "one type, several instances of one class" a **first-class answer**, so that
  newtype-plus-relaxation is never the only available route.
- **G3.** **Freeze the relaxation ladder in writing**, with exactly one named conditional door.

**Non-goals (hard fence).**

- **NG1. Replacing or removing typeclasses.** Not on the table, and the reason is mechanical, not
  sentimental: the prelude exports twelve classes (`prelude.sprout:506-840`), `deriving` is built on
  them (spec §8.6), the self-hosted compiler uses them, and the dictionary ABI is frozen into
  `bootstrap/compile_driver.ll` and `tests/golden/ir`. `spec-v0.md:7` marks typeclasses *wholesale*
  as an experimental extension, so the label does not narrow the blast radius — the mechanical facts
  above are the reason, not the status line.
- **NG2. Module functors / first-class modules.** Open audit item #13. Complementary to classes, not
  a substitute — see `haskell-lessons-learned.md` #13 and
  `module-qualified-type-identity-design-2026-07-10.md`.
- **NG3. First-class constraints at the type level.** Passing or storing a constraint as a value
  stays out. The existential form (`exists a. T a where C a`, spec §5.6) already covers the case
  that motivates it — packing a witness into a heap value — and is the intended answer.
- **NG4. Any change to the dictionary ABI, lowering, or devirtualization.** All three proposals are
  front-end only.

---

## 3. Where Sprout actually stands (measured, 2026-09-29)

| Property | State | Evidence |
|---|---|---|
| Class arity | Single parameter only | `infer.sprout:8270-8274` — "When multi-parameter classes land, revisit here" |
| Instance head | Constructor applied to distinct variables (Haskell 2010 §4.3.2) | `spec-v0.md:3028-3040` |
| Instance key | Head **constructor** only, not full head | `BACKLOG.md` P2 `@inst:` widening |
| Overlap | Unconditional error, no pragma escape | `infer.sprout:8242` `check_overlapping_instances`; `spec-v0.md:3016` |
| Overlap on REPL path | **Not enforced** against env-supplied instances | `BACKLOG.md` P2 (probed there, not re-probed here) |
| Orphan rule | **None** | `spec-v0.md:1273` |
| Ambiguity | Rejected, never defaulted — no numeric defaulting exists | `spec-v0.md:2795` |
| Dictionary cost at concrete instances | Devirtualized to a direct call | `docs/devirtualization-v0.md` (LANDED) |
| Fundeps / overlapping / quantified constraints / undecidable superclasses | Absent | — |

**The orphan-rule blast radius, measured.** Across `stdlib/`, `ide/`, `examples/` and `tests/`:
**299** instance declarations and **157** class declarations (86 *distinct* class names). Under a
module-local rule (§5.1 below), **exactly one** would be rejected:

- `stdlib/bytes.sprout:201` — `instance Eq Bytes`. `Eq` is the prelude's; `Bytes` is a compiler
  primitive (`infer.sprout:6879` `primitive_type_names`) and so is declared by no module at all. Its
  own comment states the reason it lives there: "the prelude deliberately declares no bytes
  externs."

That single case is not noise — it is the design question the rule must answer, and §5.1's open
question #1 is exactly it. A primitive type has no declaring module, so "the type is local" can
never be satisfied for one, and every `instance PreludeClass Primitive` outside the prelude is an
orphan by construction. The same holds for two further ownerless groups the census turned up:
**tuple heads** (16 instances, 12 of them in the prelude) and the `c_runtime_type_names` set
(`Vector`, `Map`, `NativeSet`, `Ref` — `infer.sprout` `c_runtime_type_names`).

**A hole in the rule as stated, found while reviewing this doc.** An alias-headed instance is
accepted today and silently never selected: `type alias Name = String` with `instance Label Name`
declares cleanly, and a call then fails with "No instance of `Label` for `String`". Worse, that
instance does **not** overlap with `instance Label String` — both are accepted together, while two
literal `instance Label String` are correctly rejected. `instance_key_and_class` keys on the
*unexpanded* head. The rule in §5.1 must therefore be stated over the **expanded** head, or a local
alias of a foreign type launders it.

**Severity, measured rather than assumed.** With both instances present, the aliased-type instance
is the one that dispatches, so nothing is *mis*-selected; the alias-headed one is simply
unreachable. This is a silently-dead declaration, not a soundness hole. The user-visible cost is the
diagnostic: the declaration is accepted, and the error surfaces at the call site naming a type the
author never wrote ("No instance of `Label` for `String`").

**The repo has already taken a position here, against banning it.**
`tests/stdlib/test_instance_head_arity.spr` — "the shapes the instance-head arity rule must ACCEPT"
— deliberately accepts `instance Boxed (Half k)` on a `type alias`, because an alias head "is
outside the rule's scope … and must not be judged by a number the writer never declared".
`instance-head-kinds-v0.md` §2 lists alias heads as a non-goal *of the arity check*, notes they "do
not dispatch today", and §11 defers their arity check "once they dispatch" — anticipating that they
will. So the recorded direction is to make alias heads *work*, not to reject them, which makes the
type-synonym item in §5.3 an open question rather than a freeze (open question #4).

---

## 4. Prior art

Full treatment with primary sources is in `cross-language-design-lessons.md` §B1–B4; this is the
decision-relevant subset, re-verified.

| Language | Position | Primary source |
|---|---|---|
| **Rust** | Orphan rule: an impl needs the trait or a type to be local. Documented cost is newtype wrapping. | [Reference, Implementations](https://doc.rust-lang.org/reference/items/implementations.html); [RFC 1023](https://rust-lang.github.io/rfcs/1023-rebalancing-coherence.html) |
| **Rust** | Trait type parameters are **input** types (they select the impl); associated types are **output** types. "Associated types do not increase the expressiveness of traits per se… However, associated types provide several engineering benefits." | [RFC 0195](https://rust-lang.github.io/rfcs/0195-associated-items.html) |
| **PureScript** | Orphans are a **type error**, not a warning. Rationale given: "Without global uniqueness, you risk operating on data with incompatible instances… keys disappear from your map." | [Type-Classes.md](https://github.com/purescript/documentation/blob/master/language/Type-Classes.md) |
| **Swift** | Declined coherence; pays with indeterminate resolution — "if multiple modules declare the same conformance … it is indeterminate which definition … will 'win'". Patched years later with a warning and `@retroactive`. | [SE-0364](https://github.com/swiftlang/swift-evolution/blob/main/proposals/0364-retroactive-conformance-warning.md) |
| **Scala 3** | A context parameter can be supplied **explicitly at the call site**: `max(2, 3)(using intOrd)` — "passes `intOrd` as an argument for the `ord` parameter". `intOrd` is an ordinary named definition, which is the shape P2 needs. | [Using clauses](https://docs.scala-lang.org/scala3/reference/contextual/using-clauses.html) |
| **Idris 2** | **Named implementations**: `[myord] Ord Nat where …`, selected at the call site as `sort @{myord} testList`. Coherent *and* admits several implementations per type. | [Interfaces tutorial](https://idris2.readthedocs.io/en/latest/tutorial/interfaces.html) |

Two honest notes on the above:

- **RFC 0195 does not mention functional dependencies or multi-parameter type classes.** It argues
  the input/output split on its own terms. The correspondence to fundeps is this doc's reading, not
  the RFC's claim, and §5.3 states it as such.
- **The Idris tutorial does not say whether a named implementation is excluded from automatic
  resolution.** §5.2 proposes exclusion as Sprout's rule; the Idris behaviour must be confirmed
  against a second primary source before it is cited as precedent for that specific choice.
- **The Rust row is a simplification.** "Trait or a type is local" is the Reference's own summary
  sentence, but the normative rule turns on covered/uncovered type parameters and fundamental types
  (`Box<Local>` counts as local, `Vec<Local>` does not). The summary is adequate for the decision
  here; it is not the rule to implement from.

**Correction — Scala 3 was wrongly excluded from an earlier draft of this table.** The stated reason
was that the Scala 3 reference frames givens as values synthesized for context parameters and
retrieved via `summon`, and so does not establish explicit passing. That was an artifact of reading
only `contextual/givens.html`: `contextual/using-clauses.html` shows `max(2, 3)(using intOrd)`
directly. The row is now in the table, and it is the closest mainstream precedent for P2 — worth
weighing against Idris before the selection syntax in §5.2 is chosen.

---

## 5. The three proposals

### 5.1 P1 — The orphan rule (recommended; cheapest now, impossible later)

**Rule.** An `instance C T …` declaration is admitted only if the declaring module also declares
`C`, or declares the head constructor of `T` **after type-alias expansion**. Otherwise it is a type
error at the instance's position.

The expansion clause is load-bearing, not a detail: without it a module declares `type alias Name =
String` and instances the prelude's `Eq` on it, satisfying "declares the head constructor" with an
alias of a type it does not own. Sprout's aliases are transparent by specification (`spec-v0.md`
§5.6.2), so the rule must see through them.

A `deriving` clause synthesizes an instance at the type's own declaration site, so derived instances
satisfy the rule by construction and need no carve-out (spec §8.6).

**Why now.** Swift is the controlled experiment: it deferred coherence and the unsoundness became
very hard to close (§4). Today the rule costs one instance (§3). After any third-party package
exists it costs an ecosystem. `docs/packaging-v0.md` §5.1 already commits to "the strict orphan rule
(an `instance (C, T)` is legal only in the package defining `C` or the package defining `T`),
extended to hold **across** packages" — this is the v0, module-level precursor that work presumes.

**What it does *not* close: the record-opacity bypass.** An earlier draft of this doc claimed it
did, following `spec-v0.md` §5.6.4 and the `BACKLOG.md` entry, which both name an orphan rule as the
fix. Neither survives checking. The bypass is "declare a class, instance it for the foreign record,
read the fields" — and because that class is *local*, the instance satisfies this rule and its
method still reads the fields: `infer.group_module` returns `unattributed_module` for every
`InstanceDecl` (`infer.sprout` `group_module`), and the opacity gate requires a module that is not
that sentinel.

What closes the bypass is **attributing an instance to its writing module and gating the body**,
which is the `BACKLOG.md` entry's *second* alternative, not its first. P1's implementation needs
that attribution anyway (below), so the two are naturally done together — but they are separate
changes, and the closure must be claimed and tested for on its own. The spec and BACKLOG wording
should be corrected when either lands.

**Implementation dependency.** The rule needs the writing module on the instance declaration — the
`BACKLOG.md` route is the writing module on `ast.InstanceDecl` (that entry counts 74 construction
sites across 18 files; not re-verified here, and a plain grep gives a different figure on a
different measure). That mechanical sweep is the bulk of the work; the check itself is small. It is
also what would close the record-opacity bypass above.

**Open question #1 — who owns an ownerless type?** Three groups have no declaring module, so the
"type is local" clause is unsatisfiable for them: primitives (`Bytes`, `Int`, `String`, …), tuple
heads, and the `c_runtime_type_names` set (`Vector`, `Map`, `NativeSet`, `Ref`). Three options:

- **(a) Primitives are owned by the prelude.** Clean rule, no carve-out. Forces `instance Eq Bytes`
  into `prelude.sprout`, which contradicts the stated reason it is not there (no bytes externs in
  the prelude). Would need that constraint revisited first.
- **(b) `stdlib/` is one coherence unit.** Costs nothing today, and weakens the rule exactly where a
  third-party package will later want the same exemption. Not recommended.
- **(c) Primitives have no owner; instances on them require the class to be local.** Strictest, and
  identical in effect to (a) for the one real case: `instance Eq Bytes` must move.

Recommendation: **(a)**, with the bytes-externs question settled as its own step. It is the only
option that leaves one rule with no exceptions.

### 5.2 P2 — Named instances (proposed; needs a syntax decision)

**What.** A second, explicitly-named instance for a (class, type) pair that is never selected
implicitly. This is the direct answer to G2 and to open audit item #12 (invisible dispatch), and it
is what keeps the freeze in §5.3 from being merely a restriction: "this type needs two instances"
stops being a reason to relax coherence.

**Declaration form.**

```sprout
instance rev_ord Ord Int
  fn compare(left: Int, right: Int) -> Int = compare(right, left)
```

**This is *not* unambiguous against the current grammar**, contrary to an earlier draft of this doc.
`parse_type_constraint` (`parser.sprout` `parse_type_constraint`) accepts any identifier as the
class name — nothing requires an initial capital — so `class label a` and `instance label Int`
type-check today. Under the present grammar `instance rev_ord Ord Int` already parses as the class
`rev_ord` applied to `Ord` and `Int`.

The named form therefore requires **a new rule that a class name begins with an uppercase letter**.
That is a restriction on existing legal programs, small but real, and it belongs in the spec and in
this proposal rather than being assumed away. §6 counts it as a semantics change, not purely
additive syntax.

**Selection form — open question #2.** Sprout has no type-application syntax to extend, and `with`
is taken (record update, `match … with`). Candidates, no recommendation yet because this is a taste
call:

1. `sort@rev_ord(xs)` — closest to Idris's `@{}`. `@` is currently a lex error, so the character is
   free. Unstated cost: `rev_ord(xs)` reads as a call, so the form needs a precedence rule.
2. `sort(xs) using rev_ord` — `using` is not a keyword today (`lexer.sprout`), so this **reserves a
   new keyword**. No in-repo identifier uses it. Reads left-to-right; sits badly inside a pipe
   chain. It is also Scala 3's spelling for exactly this (§4), which is an argument for it.
3. `rev_ord::sort(xs)` — instance-qualified, mirrors Rust's `<T as Trait>::method` intent. `::` is
   not in `try_multi_char_symbol`, so it lexes as two colons today and needs a lexer entry. The
   leading qualifier also resembles a module path and may misread as one.

**Semantics (proposed).**

- A named instance is **excluded from automatic resolution**. It is reachable only through an
  explicit selection. This is what preserves coherence: the implicit instance set stays exactly one
  per (class, head constructor).
- It therefore **does not participate in the overlap check** against the unnamed instance. Two named
  instances sharing a name for the same (class, head) do overlap and are an error.
- **Existential construction** packs the instance in scope at the construction site (spec §5.6), so
  an explicit selection at construction packs the named witness. Worth a fixture either way.
- **Devirtualization is *not* free in general**, contrary to an earlier draft. It applies only to a
  **direct method call** at the selection site (`compare@rev_ord(a, b)`), which names a concrete
  instance and lowers through the existing concrete path. Selecting into a *polymorphic* callee
  (`sort@rev_ord(xs)`) passes a witness that `sort` forwards, and `docs/devirtualization-v0.md` §2
  excludes polymorphic dispatch — so every `compare` inside `sort` stays a `__cm_` indirect call.

### 5.3 P3 — The freeze (recommended)

Written into the spec as policy, each item with the ambiguity that admitting it would introduce.

**Permanently out.**

- **Multi-parameter classes.** A method that does not mention every class parameter leaves one
  unconstrained at the use site, which needs a covering condition to make instance selection unique.
  Sprout already carries the latent shape of this: `validate_ctor_where` checks only the *first*
  constraint argument, and the `BACKLOG.md` entry records that the bug is "latent, since
  multi-parameter classes are unsupported." Landing MPTC activates it.
- **Functional dependencies.** Out even if the conditional door below is ever opened.
- **Overlapping and incoherent instances.** Never. Overlap stays an unconditional error.
- **Quantified constraints; undecidable superclasses.**

**Not frozen on soundness grounds — deferred, with a condition.** An earlier draft froze
`FlexibleInstances` / full-head matching alongside the above, arguing that full-head matching "is
the rung that admits overlap." **That is false about GHC and should not be repeated.** The GHC user
guide: `FlexibleInstances` only "permits definition of type class instances with arbitrary nested
types in the instance head", while "GHC's default behaviour is that *exactly one instance must match
the constraint it is trying to resolve*" — overlap requires an
`OVERLAPPING`/`OVERLAPPABLE`/`INCOHERENT` pragma. Full-head matching with overlap-as-an-error is
coherent, and is GHC's default position.

So the honest status of the `BACKLOG.md` P2 `@inst:` widening is **deferred on cost, not forbidden
on soundness** — its blast radius is the key writers in `infer.sprout` and `resolve.sprout`,
lowering's parallel instance table, plus the seed and golden IR. The condition if it is ever taken:
**full-head unification with overlap remaining an unconditional error, never a most-specific-wins
specificity ordering.** That entry's own "most-specific-wins" phrasing is what smuggles the overlap
rung in, and is the wording to fix. §8 no longer deletes the entry.

- **Type-synonym instances — NOT frozen; this is open question #4.** An earlier draft listed them as
  "out". That is wrong: `tests/stdlib/test_instance_head_arity.spr` deliberately *accepts* an
  alias-headed instance, and `instance-head-kinds-v0.md` §11 anticipates making them dispatch (§3).
  Freezing them would break that test and reverse a recorded decision. The live problem is not that
  they are admitted — it is that they are admitted and unreachable with no diagnostic.

**The one conditional door, aimed at the case the repo actually has.** An earlier draft opened it
only for "generic containers", which is the wrong domain: the recorded in-repo demand is **numeric**
and sits in *output* position. `docs/numeric-types-v1-draft.md:27` and
`docs/math-transcendental-v0.md:230` both state that `Integer.pow -> Maybe a` and `Real.pow -> a`
"cannot both be one class method without associated types, which Sprout lacks."

So: where one class method's *result* type must vary per instance, take **associated types**, never
functional dependencies. RFC 0195's split — all class parameters are *inputs* that select the
instance, associated types are *outputs* determined by it — keeps selection keyed on one thing and
so keeps the overlap check meaningful. (RFC 0195 does not itself mention fundeps; the correspondence
is this doc's reading, per §4.) Nothing in the repo needs an *input* second parameter, which is why
the MPTC and fundep freezes cost nothing today. Opening this door is a Design Change Process change
with its own doc, not an incremental relaxation.

**What the freeze does not restrict.** Superclasses (already used: `Ord where Eq`, `Monad where
Applicative`), method-level `where` constraints (landed), instance contexts (`instance Eq (Maybe a)
where Eq a`), and `deriving`. None of these widen the instance key.

---

## 6. Impact

**Syntax.** P1: none. P3: none, except the type-synonym item, which turns a silently-accepted form
into an error (§5.3). P2: two new forms (a named declaration, a selection expression), plus a new
uppercase-initial rule on class names that the named declaration form requires (§5.2).

**Semantics.** P1 rejects one in-repo instance (§3) and closes the alias-laundering route its
expansion clause names. It does **not** reach the record-opacity bypass — see §5.1, where an earlier
draft claimed it did. P2 is **not** purely additive: the class-name casing rule rejects programs
that are legal today (`class label a`), though none exist in-repo or downstream. P3 changes nothing
except the type-synonym item.

**Type system.** P1 adds a module-locality check over instance declarations, over the alias-expanded
head, and needs the writing module recorded on `ast.InstanceDecl`. P2 adds a second instance
namespace, keyed (class, head constructor, name); the constraint solver never consults it. Neither
touches unification, generalization, or the value restriction.

**Error messages.** Two new diagnostics for P1, one for P2. Drafts, in the style of the existing
typeclass errors (`spec-v0.md:2795-2864`, which name the fix rather than dumping a constraint):

```
orphan instance: `instance Eq Bytes` declares neither the class `Eq` nor the type
  `Bytes`, so it belongs to no module ... declare it in the module that declares
  `Eq`, or in the module that declares `Bytes`
```

```
no instance named `rev_ord` for `Ord Int` ... the instances named for `Ord Int`
  are: `reverse`, `by_magnitude`
```

**Compatibility and migration.** P1: one instance to move, or one carve-out, settled by open
question #1; plus the `BACKLOG.md` REPL-path gap, which must be closed in the same change or the
rule holds on `--phase check` and not in the REPL. P2: no existing program migrates except one that
gives a class a lowercase name (none in-repo, none downstream). P3: the type-synonym item may reject
an alias-headed instance that compiles today — in-repo there are none, and any that exist are
already silently dead (§3).

---

## 7. Tests required (per stage, TDD — failing first)

**P1.** Conformance `type_error` fixtures: an instance local to neither class nor type; one local to
the class only (accepted); one local to the type only (accepted); a derived instance (accepted,
proving no carve-out is needed). A fixture for whichever primitive-ownership option is chosen.
Parity fixture on the REPL/analysis path, so the rule cannot hold on one path only.

**P2.** Parse tests for both new forms and for the ambiguity they must not create (a named instance
vs a plain one). Run tests proving the named instance is *not* selected implicitly and *is* selected
explicitly, at the same call site. A test that exercises selection through a **polymorphic**
function with a `where C a` constraint, not only a concrete call — a concrete instance devirtualizes
the dictionary away, so a concrete-only test does not exercise dispatch. An existential-construction
fixture. An overlap fixture: two same-named instances for one (class, head).

**P3.** Mostly a policy, whose artifact is the spec text. One exception: the **type-synonym item
needs a rejection test**, because alias-headed instances are accepted today (§3). A `type_error`
fixture for an alias-headed instance, and a fixture proving an alias-headed and a head-type-headed
instance are no longer both accepted.

---

## 8. Spec and docs updates

- `spec-v0.md` §8.5: the orphan rule becomes **normative** when P1 lands. Named instances land
  **experimental**, consistent with §8.5's current status for the instance surface.
- `spec-v0.md` §8.5: the freeze as a short "what is deliberately absent" subsection, so the position
  is recorded where instance rules are specified rather than only in this doc.
- `spec-v0.md` §5.6.4: the paragraph ending "needs an orphan-instance rule, which v0 does not have"
  is rewritten — to state the rule, **and to stop naming an orphan rule as what closes the
  record-opacity bypass**, which it does not (§5.1).
- `haskell-lessons-learned.md` #4: the Sprout implication becomes the decided rule.
- `cross-language-design-lessons.md` §0: audit row #12 (invisible dispatch) updated if P2 lands.
- `instance-head-kinds-v0.md`: its Deferred list carries MPTC, which P3 forecloses — reconcile when
  P3 lands. Its alias-head entry is *not* in conflict with P3 as now written, because P3 no longer
  freezes type-synonym instances (open question #4). Two earlier drafts of this doc got this wrong
  in opposite directions — first claiming a conflict, then claiming agreement on a ban.
- `BACKLOG.md`: the `@inst:` widening entry is **kept**, with its "most-specific-wins" wording
  corrected to the condition in §5.3 — an earlier draft of this doc deleted it on a false premise.
  The record-opacity entry keeps its second alternative (writing module) as *the* fix and drops the
  first. The REPL-overlap entry points at this doc. A new entry per approved stage that is not
  implemented in the same change.

**Done in this change**, because each was a claim this doc's research disproved rather than a
consequence of a decision, and leaving a known-wrong line in a doc to be fixed later is how the next
reader inherits it:

- `packaging-v0.md` §5.1 said "**Keep** and formalize the strict orphan rule", presuming a rule that
  does not exist at package *or* module level. Now "Adopt", with a note naming what actually exists
  (the unrelated overlap check) and pointing at §5.1 here as the precursor it presumes.
- `cross-language-design-lessons.md` §0 row #4 was ✅ on the evidence "overlaps unconditionally
  rejected", conflating two different checks. Now 🕗 — that doc's legend reads ⚠️ as "doc-vs-reality
  gap" and 🕗 as "genuinely open", and this change *removes* the gap while leaving the feature
  absent, matching row #12. The citation moved from `infer.sprout:3677-3684` — which has drifted
  onto unrelated code (`tdict_head_str`) — to the identifier `check_overlapping_instances`.
- `cross-language-design-lessons.md` §B2's implication rested on that same row, reading that Sprout
  was "ahead of the Haskell doc's pending" because it rejects overlaps. Rewritten to say Sprout does
  not yet take the stance. Changing the row without this prose would have left the audit and its own
  analysis section disagreeing.
- `haskell-lessons-learned.md` #4 now states that Sprout has no orphan rule and points here. The
  decision itself stays unapproved, so the wording records the proposal, not a verdict.

---

## 9. Open questions, for approval

1. **Primitive-type ownership** under the orphan rule — (a) prelude-owned, (b) `stdlib/` as one
   unit, or (c) class-local-only. Recommendation: (a).
2. **Named-instance selection syntax** — the three candidates in §5.2, or another. No
   recommendation; this is a taste call.
3. **Scope** — land all three, or land P1 + P3 now and hold P2 until something concrete needs a
   second instance. P1 + P3 is a coherent smaller change: the freeze is defensible without P2, it
   just leaves `wrap` as the only escape hatch.
4. **Alias-headed instances** (§3) — make them dispatch by expanding the alias in the instance key
   (the direction `instance-head-kinds-v0.md` §11 anticipates; breaks no existing test), or diagnose
   the dead declaration at its site (cheap and loud, but contradicts
   `tests/stdlib/test_instance_head_arity.spr`), or leave it and file it. Independent of P1–P3.
