# Sprout Type System — Deep Design + Implementation Review

**Status: non-normative review record, 2026-08-13.** `docs/spec-v0.md` remains the normative
source of truth. Findings are tracked in `BACKLOG.md` §1 ("Type-system review findings") and
§"Dispatch Soundness & Diagnostics"; this document is the evidence behind those entries and is
not updated as they are fixed — consult the backlog for current status.

Method: multi-lens adversarial review (5 file-scoped finder lenses → adversarial per-finding
verification with mandatory empirical repro → 0-100 confidence rubric, filter <80).
14 agents, 1.43M tokens, 24 min. 21 raw findings → 9 verified → 8 scored ≥80.
Zero refutations, all 9 with `repro_ran=true`.

Independently re-verified by the main agent from scratch (not taken on the subagents' word):
**F1, F2, F3, F4, F5, F6**, plus the F2 and F6 order-dependence controls — all reproduce
exactly, including the garbage value `int(35184372088840)` in F4 and the `bool/bool` vs
`int:9/int:7` incoherence in F5. F7/F8/F9 rest on the verifier's runs only.

Commit reviewed: `cd07923e` (master, clean tree).

---

## Verdict

The classical HM core is genuinely well-built and survived heavy adversarial probing.
Confirmed-correct on probes: substitution is re-applied to sibling positions before recursing
(no stale-sibling unsoundness), the occurs check covers every `Type` constructor, `TTuple`
arity mismatch is caught, module-qualified nominal `TConst` identity is enforced, ownership
invariance is enforced on both sides and recurses through nested arrows, `ftv_env` is computed
once and threaded (not per-generalization), pattern-bound vars are uniformly monomorphic,
a syntactic value restriction exists and rejects `let cell = ref_new(Nil)`, overlapping
instances are rejected, a cyclic superclass graph terminates and dispatches correctly, and
`verify_dispatch` fires on real calls rather than skipping everything. No unreachable-branch
false positive could be constructed.

**Every confirmed defect is at a seam, not in the engine.** Six of nine trace to one root
pattern: *a place where "I don't know yet" is silently converted into "anything you like"*,
rather than into an obligation or an error.

**Order dependence recurs.** Three findings have a strict *identical-text-reordered* control
that flips accept to crash — F1 and F2 (declaration order), F6 (operand order within one
expression). F4 and F5(b) are positionally dependent in adjacent ways: F4 on parameter order
(a different signature, not identical text) and F5(b) on which caller supplies the dictionary.
F3 has no order dependence.

---

## Confirmed findings (≥80), ranked

### F1 — [100] Declared type variables are not rigid; forward callers miscompile
`stdlib/compiler/infer.sprout:5546` · soundness · NOT tracked

`instantiate_with_vars` mints signature tyvars as ordinary flexible metavariables, and the
post-hoc W3 guard `rigidity_violation` only rejects a declared var whose resolution contains
a `TConst` (`type_has_const`, :5559). Any **const-free** resolution passes — including two
declared vars merged with each other, and a declared var forced to a compound arrow.

```sprout
fn main() -> Unit !{IO} =
  do
    let n = snd_as_first("hello", 7)
    print(int_to_string(str_len(n)))

fn snd_as_first(x: a, y: b) -> a = y     # accepted; scheme silently narrows
```
`--phase check` → `OK`, prints `main.snd_as_first : forall a. a -> a -> a`
(declared `forall a b. a -> b -> a`). Runs → SIGSEGV, exit 139.
Also accepted: `fn weird(x: a, g: b -> c) -> a = g`.

Window: same-file, caller textually above callee (the `pre_scan` forward-reference path).
Callee-first and cross-module both reject cleanly. The IR pushes the bogus value as a **GC
root**, so the type lie reaches the collector.

Fix: in `rigidity_violation`, reject any resolution that is not a bare `TVar`, and reject
pairwise duplicates among the declared vars' resolutions.

---

### F2 — [100] `_unann` placeholders are quantified into the published forward-reference scheme
`stdlib/compiler/infer.sprout:306` (and `:193`, `:255`, `:301`) · soundness · **FIXED 2026-08-24**

> Fixed by per-declaration unquantified placeholders plus a substitution threaded across
> declarations — `docs/binding-group-inference-v0.md`, `spec-v0.md` §7 rule 16. Two amendments
> to what is written below. The rigidity skip **was** removed, and safely: with the placeholders
> out of the binder list the skip had nothing left to skip, so the warning against removing it
> was correct for the code it was written against and no longer applies. And the "located error
> at the forward call site" turned out to be unnecessary — a threaded substitution gives the
> conflict somewhere to collide by itself, and reports it at the declaration whose body
> contradicts the caller. SCC dependency order remains open and is tracked in `BACKLOG.md`.

`scheme_from_fn_parts` synthesizes `_unann` / `_unann_<param>` for omitted annotations and
`collect_ret_type_vars`/`collect_param_type_vars` put them in the scheme's **quantified**
list. `pre_scan_fn_decls` publishes that to the global env, so a forward caller instantiates
the placeholder freshly and unifies it with whatever it wants. Nothing reconciles.

```sprout
fn report(xs: List Int) -> String = "count=" ++ summarize(xs)
fn summarize(xs: List Int) = list_length(xs)
fn main() -> Unit !{IO} = print(report([1, 2, 3]))
```
`--phase check` → `OK`, and the listing prints the **correct** types it failed to enforce
(`main.summarize : List Int -> Int`). Runs → SIGSEGV in `str_concat` ← `main.report`.

Proof the placeholder is genuinely quantified, not just an unconstrained mono var: one caller
can instantiate the same forward callee's return at two different types in one expression.
Affects omitted **parameters** too — which matters because `BACKLOG.md:1592` records that
`compiler.sprout`'s own cache params are unannotated for bootstrap compatibility.

Correction the verifier made to the finder's claim: the rigidity skip at `:5540` is **not**
the failing guard and must not be removed — it is correct and necessary (removing it rejects
every unannotated-return function). The defect is upstream: treating a synthesized inference
placeholder as a user-promised polymorphic binder.

Fix: keep minting `_unann` TVars for the shape, leave them **out** of the binder list, and pair
that with a located error at the forward call site. Principled version: infer declarations in
SCC dependency order.

---

### F3 — [100] Existential skolem escapes into a top-level scheme via an unannotated return
`stdlib/compiler/unifier.sprout:403` · soundness · NOT tracked

A skolem is a `TConst`, so it has no free type variables; `generalize_resolved` computes
`list_diff(ftv(resolved), env_ftv)` and never inspects for skolems. `type_mentions_skolem`
has exactly **one** call site in the whole compiler (`unifier.sprout:217`, to reword a message).
Because a `TConst` is not quantified, instantiation does not refresh it — every call site of
the decl shares one rigid type.

```sprout
type Boxed = | exists a. Boxed a
fn unbox(b: Boxed) =
  match b with
  | Boxed x -> x
```
`--phase check` → `main.unbox : main.Boxed -> $sk2130`. Running prints `1`, then a raw heap
address for the String, then accepts a heterogeneous `[Int, String]` list as homogeneous.

**Defeats the project's own fixture.** `tests/conformance/type_error/existential_merge.spr`
exists to pin "two unpacks must not unify"; routing the identical shape through this one-line
helper is accepted.

Falsifies a load-bearing premise in `docs/gadts-v0.md:342-343`: *"A leaked skolem is an inert,
unusable type, which is why nominal rigidity is sound without levels."* It is not inert — in a
top-level scheme it becomes a globally shared type. Blast radius is bounded: class dispatch on
the skolem *is* guarded (`resolve_skolem_given`, `:1223`) and coercion to a concrete type is
rejected; the leak reaches runtime through fully-polymorphic externs like `print`.

Fix: one `types.type_mentions_skolem` call at the decl-generalization site (`infer.sprout:4962`),
reusing the existing located diagnostic.

---

### F4 — [100] Compound-head constraint `where C (T a)` grabs the first concrete argument's dict
`stdlib/compiler/infer.sprout:2033` · soundness · NOT tracked

`canonicalize_constrained_constraints_acc` (`:5773`) **discards** a type-application constraint
head, emitting `"#none"`. The `#none` arm then scans the call's arguments left to right for the
first concrete-typed one and commits to its dictionary — with no check that it relates to the
constraint's head, and no backtracking.

```sprout
fn describe(tag: Int, b: Box a) -> String where Sh (Box a) = sh(b)
fn describe_ok(b: Box a, tag: Int) -> String where Sh (Box a) = sh(b)
```
Both compile clean. Output: `int(35184372088840)` / `box(int(1))` — same constraint, different
dictionary, chosen only by **which parameter comes first**. With a record as the leading param
it SIGSEGVs inside `str_concat`. If the first concrete-headed arg has no instance, the user gets
a leaked internal string: *"internal error: under-application … reached codegen"*.

Distinct from the `first_concrete_arg(guess)` heuristic that `BACKLOG:2838` audited and
deliberately spared: that tag comes from `resolve_obligation` (`:1949`), which is also the only
path `SPROUT_TRACE_DISPATCH` instruments. **The `#none` arm emits zero dispatch events** — the
corpus sweep that licensed sparing the heuristic could never have observed this path.

Not exotic: `tests/stdlib/test_compound_constraint_inline_record.spr:16` uses this exact shape.
Existing coverage only ever passes the constrained value **first**, which is precisely where the
positional guess accidentally agrees with the truth.

Fix: don't guess — the head is known. `apply_subst(s3, fresh_t)` yields `Box Int`; resolve
`@inst:C:<head-ctor>` directly as the concrete-ctor path already does. Preserve the head in
`canonicalize_constrained_constraints_acc` instead of collapsing it to `"#none"`.

---

### F5 — [100] Ambiguous class type variable is never reported
`stdlib/compiler/infer.sprout:1193` · soundness · NOT tracked

The classic `show . read` ambiguity (`to_str(from_int(n))`) is never diagnosed. Two outcomes:

(a) No `@fwd` marker in scope → `check_instance_fwd`'s final fallback returns the call
unchanged, no dict, no error. `--phase check` and `--emit-ir` both exit 0; resolve and
verify_dispatch both pass clean (`verify_call` keys off the *callee's declared `where` clause*,
and a class method has none). Fails at **link**: `use of undefined value '@from_int'`, with no
Sprout source position. This is exactly what `docs/spec-v0.md:1224-1226` says the constraint
well-formedness rules exist to prevent — but those rules only inspect declared `where` vars.

(b) Any `@fwd:*:{class}` marker in scope → the first one in `dict_entries` order is adopted.
Verified: `to_str(from_int(7))` — a closed subexpression with no free variables — prints
`bool` in one call and `int:7` in another, decided by an unrelated argument. That is
**incoherence**, not just a missing diagnostic.

The code's own asserted invariant at `:1189-1190` ("only a rigid function head reaches here
unresolved") is falsified by case (a).

Fix: gate `scan_fwd_markers` on the dispatch tyvar actually corresponding to a declared
constraint var; otherwise emit a located "ambiguous type variable" error.

---

### F6 — [100] Field access on an unresolved receiver mints a fresh unconstrained tyvar
`stdlib/compiler/infer.sprout:4083` · soundness · NOT tracked

`get_field_from_resolved`'s `| _ ->` arm invents a fresh tyvar when the receiver hasn't yet
resolved to a `TConst`/`TApp`. No deferred obligation is recorded; nothing revisits the node.
`assert_resolved_typed_expr` (`:5461`) walks into the receiver but ignores the node's own type
slot. Meanwhile `ast_to_ir` lowers a real offset-resolved field load.

```sprout
type P = (x: Int, y: Int)
fn zero(q: P) -> Int = 0
fn bad(p) =
  let s = p.x
  in str_len(s) + zero(p)
```
Checks clean as `main.bad : main.P -> Int`; runs → SIGSEGV. `fn coerce(p) = p.x` gets the
scheme `forall a b. a -> b`.

**Order-dependent within a single expression**: `zero(p) + str_len(p.x)` is correctly
rejected; the semantically identical `str_len(p.x) + zero(p)` compiles and crashes. So this is
simultaneously a soundness hole and a demonstration of non-principal inference.

The maintainers have already patched one manifestation — the comment at `:2263-2265` names
this exact fallback as the cause of a lambda-argument bug, fixed with a two-pass
`infer_call_args`. The general unannotated-parameter case remains open. Distinct from the
tracked `BACKLOG:1924` (unknown field on a known record, caught later by `ast_to_ir`); that one
costs a bad error message, this one costs memory safety.

Fix: record a deferred field obligation and discharge it post-`s2`, mirroring the existing
constrained-marker fixup. Cheap backstop: have `assert_resolved_typed_expr` (`:5420`) inspect
the `TGetField` type slot it currently discards — but note that pass is **dead code today**
(its only call sites are its own recursive ones), so the backstop only materializes once the
tracked "wire in the dead `assert_resolved_typed_expr` pass" backlog item lands.

---

### F7 — [85] Effect rows are inert; `unify_effects` has zero call sites
`stdlib/compiler/unifier.sprout:242` · soundness · **RESOLVED 2026-08-16** (was TRACKED via
`docs/fundamentals-code-review-handoff-2026-07-03.md` D2/W6)

> **Finding resolved; the analysis below is the 2026-08-13 state, kept as the record of why.**
> Spec §7 rules 8 and 11 are enforced as real rejections since 2026-08-16 — a pure-annotated
> body that calls `print` is now rejected — so "`!{IO}` is documentation everywhere except
> `validate_entrypoint`" (below) no longer holds. The fix was **not** the missing
> `unify_effects` call this finding proposes: unification accepts both directions, and the
> rule at a declaration boundary is one-directional subsumption (inferred ⊑ declared). See
> `docs/effect-enforcement-v0.md` and spec §7's enforcement note. Two narrower gaps stay open
> in `BACKLOG.md` §1: top-level `let` purity, and unknown effect labels.

`unify_applied`'s TFunc arm wildcards both effect fields; `unify_effects`/`unify_effects_applied`
have zero call sites in `stdlib/` or `tests/`. So `eff_subst` — threaded through every `infer_*`
signature — is never written by unification, and effect variables can never be bound.
`EffectRow × EffectRow` falls to `_ -> Err` even for identical rows (`:295`).

Broader than higher-order unification: a pure-annotated body calling `print` typechecks with no
arrow unification involved. `!{IO}` is documentation everywhere except `validate_entrypoint`,
which syntactically requires `main` to declare it.

Verifier's correction to the finder: the claimed downstream unsoundness (purity-driven
reordering/elision) is **hypothetical** — `ir_lowering.sprout` contains no purity reference, and
the one candidate optimization fires identically for correctly-`!{IO}`-typed callees. No
miscompile today.

Real present defect: `docs/spec-v0.md` §7 rules 8 and 11 assert effect checking normatively.
The gap is tracked; the **spec divergence is not**.

Cheap fix now: mark §7 rules 8/11 specified-but-not-enforced, pointing at D2/W6.

---

### F8 — [80] Single-constructor types cannot be destructured in a `do` bind — in any form
`stdlib/compiler/parser.sprout:497` · incompleteness · NOT tracked

The parser decides do-bind refutability **syntactically** (`:497-503`: only var/wildcard/unit/
tuple-of-those; every `ConstructorPattern` → `false`), while spec §5.2.1 (`docs/spec-v0.md:203-210`)
defines refutability as a property of the pattern *versus its type*, which is what W5 applies.
A `wrap` or 1-ctor ADT pattern is irrefutable by the spec and refutable by the parser, so it is
rejected **both** ways:

- without `else` → parse error *"refutable `<-` binding in a do block requires an `else`"*
- with `else` → W5 *"Unreachable match branch"* (correctly — the spec says an `else` on an
  irrefutable pattern is an error)

The two diagnostics point in opposite directions, so no error message leads to the workaround.
Affects `<-` and do-`let`, constant-else and binding-else.

The verifier explicitly exonerated W5: it does exactly what the spec asks. The single defect is
the parser's over-approximation. `BACKLOG.md:1786-1796` documents the change that *introduced*
this gate and calls it complete and correct.

Fix: route a no-`else` constructor-pattern do-bind through the existing `build_do_refutable`
staircase with one success arm; W5 then decides refutability type-relatively for free, and
genuinely-refutable patterns get a better diagnostic than today's parse error.

---

### F9 — [75, below threshold] Exhaustiveness is per-column, not Maranget
`stdlib/compiler/infer.sprout:2833` · **TRACKED** (`BACKLOG.md:243`, `docs/spec-v0.md:363-366`)

Reported for completeness; scored below the ≥80 bar and correctly so. The verifier downgraded
the finder's "soundness" framing: no type lies and codegen always emits the abort call, so this
is a spec-acknowledged incompleteness, not unsoundness.

Worth noting anyway: the reach is wider than the backlog's single `(Bool, Bool)` example — it
fires on `(Nothing, _) | (_, Nothing)` over `(Maybe Int, Maybe Int)` and on plain multi-field
constructors with no tuple. The catch-all-per-column idiom is materially more likely to be
written than explicit product enumeration, so practical exposure exceeds what the entry implies.

---

## Reported unverified (below the top-9 cap — plausible, NOT confirmed)

| Sev | Where | Claim |
|---|---|---|
| med | `infer.sprout:162` | Any unknown effect label silently read as an effect variable |
| med | `resolve.sprout:159` | Mutually-referential instance contexts → infinite recursion in resolve |
| med | `verify_dispatch.sprout:195` | Structurally skips every non-var-headed constraint — the exact shape F4 exploits |
| med | `infer.sprout:2916` | Three silent accept-everything fallbacks in the coverage walk |
| high | `infer.sprout:135` | Effect rows erased on nested arrows, contra spec §7.8/§7.11 (F7 sibling) |
| med | `infer.sprout:123` | `type_from_ast` validates nothing — unknown names and bad arity pass |
| med | `unifier.sprout:216` | Skolem `$sk<n>` leaks into published schemes and `.iface` artifacts |
| low | `unifier.sprout:125` | `occurs()` re-applies the subst at every node — quadratic on curried spines |
| low | `infer.sprout:5554`, `types.sprout:443` | Internal names `$sk2130` / `$t2135` leak into user errors |
| low | `infer.sprout:3312` | Constructor-pattern arity error reported as "Tuple pattern arity mismatch" |

---

## Structural themes

Items 1–4 generalize the verified findings above. Items 5–6 come from the design lens and did
**not** go through a verifier; the two load-bearing claims in item 5 were spot-checked by the
main agent (noted inline), the rest is that lens's assessment.

1. **Silent fallbacks are the dominant bug generator.** F4, F5, F6, and three unverified items
   are all the same move: convert an unresolved state into a guess instead of an obligation or
   an error. The surrounding machinery is otherwise careful, which makes these stand out.

2. **Three of the five modalities `TFunc` carries are second-class.** Effects are written then
   discarded (F7); ownership is checked but is parsed only at *parameter* position
   (`parser.sprout:1168-1181`, `conn: borrowing TcpConnection`), so a function-typed
   parameter's own arrow cannot carry one; constraints reached first-class `Scheme` status
   only after a multi-PR campaign, while
   `@fwd`/`@eta_fwd`/`@super`/`@rec`/`@inst`/`@class` metadata still rides in the same
   `Dict types.Scheme` as real bindings, keyed by a string prefix the lexer happens to forbid.

3. **The safety nets skip exactly the shapes that need checking.** Both resolve's
   satisfiability check and `verify_dispatch` key on a constraint head being a bare variable or
   a concrete constructor — so a compound head (F4) passes through unexamined by both, and is
   invisible to `SPROUT_TRACE_DISPATCH` corpus sweeps.

4. **Order dependence is systemic, not incidental.** F1, F2, F5(b), F6 and the F4 argument-scan
   all decide meaning by textual or positional order. F6's control pair is the clearest
   statement of the problem: `zero(p) + str_len(p.x)` errors, `str_len(p.x) + zero(p)` crashes.

5. **Architecture.** `infer.sprout` is 6009 lines / 402 functions / 8 exports, with internal
   seams that are calling conventions rather than module boundaries. `iface_codec.sprout`'s
   2297 lines of Scheme serialization has all its call sites confined to the `--emit-iface`
   and `--check-iface` driver modes (`compile_driver.sprout:180` `run_file_emit_iface`, `:234`)
   — *spot-checked by grep and confirmed* — so no ordinary compile path exercises the
   `Scheme` round trip. A field written but not read, or read with a default, would silently
   weaken types across a module boundary and nothing today would notice.

6. **`docs/hm-typechecker.md:121-129` is badly stale** — still lists "No typeclasses/traits"
   and "No effect system beyond the simple `IO a` surface annotation" as intentional v0 limits,
   against an implementation with dictionary-passing classes, a four-constructor `Effect` type,
   ownership modes, linear types and existentials. Ironically the effect claim is now *more*
   accurate than the code's design intent (F7).

---

## Suggested sequencing

Cheapest-first, by (impact / effort):

1. **F3** — one `type_mentions_skolem` call at one site; closes a memory-safety hole and
   restores a fixture the codebase already maintains.
2. **F1** — tighten `rigidity_violation` to reject non-bare-TVar resolutions + pairwise dups.
3. **F7 doc half** — mark spec §7 rules 8/11 as not-yet-enforced. Minutes.
4. **F6 backstop** — extend `assert_resolved_typed_expr` to the `TGetField` type slot; turns a
   SIGSEGV into a compile error while the real deferred-obligation fix is designed.
5. **F4** — preserve the compound head instead of `"#none"`; route through `trace_dispatch`.
6. **F5** — gate `scan_fwd_markers`; add the ambiguity error.
7. **F2** — needs a design call (mono placeholder + located error vs. SCC-ordered inference).
8. **F8** — parser routes constructor patterns through `build_do_refutable`.

Every code fix here lands in `stdlib/compiler/` (`infer`, `unifier`, `parser`), so all of them
carry the reseed + full-gate cost — F1/F3 are not exempt, they are just small enough that
batching them into one reseed *cycle* is cheap. F8 touches the parser, so it needs the 2-step
bootstrap protocol. Item 3 (the spec wording) is docs-only and can go alone.
