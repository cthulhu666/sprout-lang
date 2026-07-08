# Dictionary-Resolution North Star — Implementation Plan (2026-06-30)

Branch: `feat/dict-resolution-north-star` (off `origin/master`).

## 1. Problem statement

Typeclass-dictionary resolution is **split across three compiler passes**, each with
its own silent-failure exit. A constraint that no instance can satisfy is not
rejected: it leaks through inference, is silently turned into an `__unresolved_*`
sentinel by lowering, then null-filled to `0` by codegen, and finally **segfaults at
runtime** when the (non-empty) dictionary is invoked.

Reproducer (verified, exit 139 / SIGSEGV):

```
module main
fn main() -> Unit !{IO} = print(to_string([()]))   # no ToString () instance
```

`to_string(())` is correctly rejected ("No instance of ToString for Unit"), but
`to_string([()])` typechecks **OK** because the checker only verifies the *outer*
instance head (`@inst:ToString:List`) and never discharges the instance's context
constraint (`where ToString a`) at `a := ()`.

Root cause across the pipeline:
- **infer.sprout** injects `TDict(unresolved_constraint, …)` and only resolves the
  outer head. `register_instance_marker` (infer.sprout:3550) stores `@inst:` markers
  with a throwaway `mono(TConst("Unit"))` payload — the instance context constraints
  are dropped.
- **lowering.sprout** independently re-derives the full resolution (`resolve_tdict`
  family, ~1208–1443) against its own tables and emits `__unresolved_*` sentinels at
  three sites (1255, 1302, 1320) on a miss — silently.
- **codegen.sprout:1900 / ast_to_ir.sprout:776** null-fill `__unresolved_*` to `0`.

## 2. Goals and non-goals

**Goals**
1. Single owner of resolution: the **checker** resolves every constraint into a
   *complete evidence tree* or emits a *diagnostic with a source position*.
2. Lowering and codegen become **mechanical translation** — no instance lookups, no
   silent `__unresolved_*` fallback, no dict null-fill escape hatch.
3. The segfault class becomes a compile-time error: `to_string([()])` must produce the
   **same** diagnostic as `to_string(())`.

**Non-goals**
- No change to the **dict ABI** (witness layout/order, hidden-param order, lambda
  wrapping shape). The bootstrap fixed-point must keep passing at every milestone
  except the final intended-behavior changes.
- No new language surface syntax. No change to which programs are *accepted* other
  than rejecting the currently-miscompiled ones.
- `instance ToString ()` ergonomics is **out of scope** here (tracked separately; it
  papers over one element type and is orthogonal to the structural fix).

## 3. High-level implementation overview

Introduce a **resolved-evidence representation** and make the checker produce it.

```
# new, in typed_ast.sprout (or a dedicated evidence.sprout)
type Evidence =
  | EvInstance String (List Evidence)   # instance method-map key + resolved inner dicts
  | EvForward  String                   # forwarded hidden-param slot (polymorphic site)
  | EvMissing  ast.TypeConstraint       # unsatisfiable → becomes a diagnostic
```

The checker resolves each `TDict` constraint into an `Evidence` tree (recursively
discharging instance context constraints), and either:
- attaches the resolved `Evidence` to the evidence node (new node `TDictE Evidence …`
  or an extra field on `TDict`), or
- emits `"No instance of <Class> for <Type>"` when any node is `EvMissing`.

Lowering's `resolve_tdict` family is then replaced by a **mechanical** `Evidence →
TypedExpr` translation that reproduces today's witness exprs exactly (instance fn ref,
forwarded slot `TVar`, or lambda-wrapping with inner dicts). The `__unresolved_*`
sentinels and the codegen null-fill are deleted / converted to loud internal errors.

**Where the resolution runs.** DECIDED (2026-06-30): a dedicated **`resolve.sprout`
module** between infer and lowering. It *owns* the resolution tables
(`inst`/`fwd`/`inst_meta`/`super_map`/`class_order`/`class_params`) as the single source
of truth, including the hidden-param slot-name ABI currently in lowering. The module
rewrites each `TDict(unresolved)` into a resolved `Evidence` tree or emits a diagnostic;
lowering becomes a mechanical `Evidence`-walk with no lookups. This physically eliminates
the three-way split (vs. extending infer's post-pass, which would leave the tables in
lowering and force duplication or a backwards infer→lowering dependency). Cost: a
one-time new-module bootstrap-seed surface, landed via the 2-step bootstrap protocol.
M1–M2 (marker storage + checker diagnostics) stay in `infer.sprout` regardless.

**Table sharing.** The resolution pass needs the instance metadata lowering currently
owns (`inst_meta`: instance type args + context constraints + method arities; `inst`:
key→method→fn_name; `super_map`; `class_order`; `class_params`). Step M1 stores the
instance context in the checker marker; the remaining tables are built from the typed
program and moved/shared so the resolution pass and (mechanical) lowering read the same
source of truth.

## 4. Milestones (each keeps bootstrap green unless noted)

**M0 — Failing regression test + type-error runner. DONE (2026-06-30).**
- Positive guard `tests/stdlib/test_nested_tostring_dispatch.spr` (`[[1]]`,
  `[Just(1)]`, `[(1,2)]` render correctly) — PASS; gates via `test-stdlib-stage1`.
- Negative fixture `tests/conformance/type_error/missing_nested_instance.{spr,err}`
  (expects `No instance of ToString for Unit`) — currently `--phase check` prints OK
  (the bug), so it fails for the right reason. Tracked as **xfail** until M2.
- Built `just test-type-errors` (xfail-aware; matches `.err` substring since
  `--phase check` exits 0 on type errors) and wired it into `test` + CI. This
  **revived 6 orphaned `type_error/` fixtures**; survey found only `if_branch_mismatch`
  currently produces its expected diagnostic. The other 4 are pre-existing
  not-yet-implemented diagnostics, now tracked as xfail:
  `duplicate_instance`, `overlapping_instance` (overlapping-instance detection),
  `stdlib_mixed_do_bind_family_conflict`, `stdlib_mixed_do_wrong_final_family`
  (do-block family-conflict diagnostics). When M2 lands, `missing_nested_instance`
  will start matching and the runner forces its promotion off xfail.

**M1 — Instance context in the checker marker (shared infra, no behavior change).**
`register_instance_marker` (infer.sprout:3550) + its caller (infer.sprout:2894) store
`inst_constraints` (and instance type args / method arities as needed) instead of
`mono(Unit)`. Pure enrichment; verify suite + seed fixed-point unchanged.

**M1–M2 (infer-side) — DROPPED (2026-06-30).** Advisor review showed the infer prefix
was the wrong path: `state` is not threaded to the discharge sites, the homogeneous
`GlobalEnv` (`Dict Scheme`) can't carry `ast.TypeConstraint` context, and the discharge
logic already exists in lowering. Rebuilding it in infer would be throwaway churn and a
third copy of the diagnostic. Folded into M3a below.

**M3a — resolve.sprout discharge + diagnostic in the check phase. DONE (2026-06-30).**
New `stdlib/compiler/resolve.sprout` owns its instance tables (existence + context),
built from the typed decls (prelude is bundled in, so prelude instances are visible). A
`resolve_program` pass walks every `TDict`; a constraint whose head is *concrete* with no
instance → `"No instance of X for Y"`; a *variable*-headed (forwarded/polymorphic)
constraint → skipped (mirrors lowering's inst-vs-fwd split without the per-fn fwd table).
Wired into `compile_phase_check` (+ `_with_cache`, the REPL path). Validated:
- `missing_nested_instance{,_maybe}` now reject at `--phase check` with the exact
  `No instance of ToString for Unit`; promoted off xfail.
- positive guard (`[[1]]`/`[Just(1)]`/`[(1,2)]`) still compiles+runs.
- stage-2→stage-3 self-compile clean (no false positives on the compiler + prelude).
- Two false-positive classes found via the full suite and fixed (invariants for M3b):
  1. **Concrete-head test must be "uppercase head", not an `a–z` allowlist.** `_` and
     fresh metavars are non-uppercase → variable/forwarded. `head_is_concrete =
     starts_upper(type_expr_head_name(te))`.
  2. **Existence must consult the checker `@inst:` env markers, not just bundled
     decls.** Instances from imported modules / builtins register `@inst:Class:Head`
     markers but may have no bundled `TInstanceDecl`. resolve now checks env markers
     for *existence* (same source of truth as infer.sprout:769) and keeps the decl
     table only for *context recursion* (it alone carries the `where` constraints).
     Consequence: an instance whose decls aren't bundled is accepted without
     deep-checking its context — a safe miss (never a false positive).
     **Downgraded 2026-07-01 (repro attempt): not reachable via source imports.**
     The bundler bundles imported modules' decls (topologically), so an imported
     *constrained* instance's `where` context IS in the decl table and resolve
     recurses normally. Verified: a module `stdlib.reprobox` exporting
     `instance ToString (Box a) where ToString a`, imported and applied as
     `to_string(mk_box(()))`, is cleanly rejected with `No instance of ToString
     for Unit` — the context recursion fires across the module boundary. The
     env-marker-only set is therefore just: builtins (all *unconstrained* → no
     context to miss) and future `.iface` imports (markers without decls — not in
     the default source-compile path). The residual hole is theoretical, gated on
     the iface arc, and closable only when iface imports carry instance context —
     resolve has nothing to thread until then. No code fix warranted now.
- Prereqs discovered & fixed properly (not worked around): `stdlib.string`
  `rsplit_once`/`substring_after_last` (replacing a 7th `strip_module_prefix` copy) and
  prelude `Eq` instances for tuples (arities 2–5; tuples had `ToString` but no `Eq`, a
  latent segfault source).

**M3b — Evidence representation + checker produces it (north-star core).**
Add `Evidence` + evidence-carrying node. resolve.sprout rewrites each TDict constraint
into a resolved `Evidence` tree. Lowering starts consuming `Evidence` instead of
re-resolving — but must emit **byte-identical** witness exprs. Gate: golden IR / `just
verify-bootstrap-fixed-point` unchanged.

**M4/M5 — PARKED as follow-ups (2026-06-30).** After M3a, a standalone "turn the
`__unresolved_` sentinels / codegen null-fills into hard errors" pass is **not worth
doing on its own** and was rejected (advisor-reviewed):
- It is a *dead branch*: M3a already rejects every concrete-head constraint at check
  time, so a concrete-head sentinel surviving to codegen is unreachable for well-typed
  programs. The guard would never fire on valid input and has **no positive test**.
- A leaky (null-fill-on-ambiguity), untestable classifier added to bootstrap-critical
  files (`ast_to_ir`, `codegen`) reads as "the null-fill hole is handled" when it barely
  is — a half-measure.
- The value it chased ("a future resolve gap becomes loud") is already better served by
  the `test-type-errors` gate: add a negative fixture whenever `resolve` is touched.
- The *genuine* "no silent escape hatch" fix is M3b done properly — centralizing
  resolution so the null-fill becomes **structurally unreachable**, not a codegen
  string-parse. Folded into M3b's scope below.

**⚠️ CRITICAL CONSTRAINT for M3b — the `__unresolved_` sentinel is load-bearing.**
`has_unresolved_dict` (lowering.sprout:860, used at 1074) consumes the `__unresolved_`
sentinel as a *reroute signal*: in the eta/value-position path, an inner dict that comes
back unresolved (inner constraint not in scope) causes lowering to fall back to the
**forwarded slot** instead of emitting a bad node. So the sentinel has **dual
semantics**: (1) transient reroute signal (eta path, replaced upstream, never reaches
codegen) and (2) genuine null-fill marker (call path, survives to codegen). Consequences
for M3b:
- The `Evidence` rewrite must **preserve the reroute behavior**, not just the null-fill.
- The two paths mint sentinels at *different sites*; only line 1255 (`resolve_tdict_with_key`)
  was confirmed. **M3b's real first step is to enumerate every sentinel mint site and
  which path (call vs eta) consumes it** — this was never fully verified and is the
  prerequisite, not an afterthought.

**M3b — PARKED (2026-06-30, Kuba's call).** After mapping the machinery (appendix
below) two facts settled it:
1. **M3b closes nothing.** M3a already closed the escape hatch at *check* time. The
   null-fill that remains only handles *phantom* free-tyvar dicts — correct behavior that
   MUST survive in the end state (`test_unresolved_dict_nullfill` guards it). So M3b is
   pure architecture (single resolution path / dedup), **zero behavior change**, with
   byte-identical IR as the gate.
2. **It is all-or-nothing.** `resolve_tdict` is shared by the call *and* eta paths. A
   call-position-only migration leaves `resolve_tdict` alive (eta needs it) while resolve
   duplicates its witness-planning → *more* duplication, not less. The only coherent end
   state is FULL migration (both paths on Evidence, `resolve_tdict` deleted), which
   requires moving the eta reroute into the Evidence model.

M3a is the shipped fix; this map is the spec for whenever full M3b is undertaken.

**Evidence encoding — DECIDED key-based (advisor, 2026-07-04).** Evidence carries
resolution *keys* (`EvInstance "Eq_Int"`, `EvForward "Eq_a"`); lowering keeps its
mechanical `ctx_inst`/`ctx_fwd` tables and does the key→name mapping. The rejected
alternative (name-based: Evidence carries resolved `fn_name`s + slot names, lowering
emits `TVar`s with zero tables) is *more* plan-faithful ("no lookups") and makes the
null-fill structurally impossible, but forces resolve to own a **second copy** of the
hidden-param slot ABI (`tc_slot_name` per-function `idx`, super-within-idx,
duplicate-same-class disambiguation — the C.11 blocker). Decision rationale (Kuba's two
criteria): (1) long-term — key-based keeps a **single source of truth** for the slot ABI,
since `ctx_fwd` is *built by* `build_hidden_for_constraints`, the same function that
declares the callee's hidden params (`best-practices:19`, drift avoidance); (2) Sprout
principles — key-based keeps the checker speaking *semantics* and lowering owning its
*codegen ABI* (clean layer boundary), and sits inside `guidelines.md:31`'s explicit
carve-out for loud invariant-guarded lookups. `guidelines.md:37` ("encode invariants in
ADTs") does *not* favor name-based: its `EvInstance (List String)` still admits
`EvUnresolved`/`EvMissing` and unvalidated strings, so it changes the *failure mode*
(:31's domain), not the ADT's structural guarantee.
**Two conditions make key-based legitimate (not a shortcut) — both mandatory:**
1. The consumption lookup-miss (`ctx_inst`/`ctx_fwd`) becomes a **located panic**, not the
   current `emit_var` null-fill — per `guidelines.md:31` (loud, never silent garbage).
2. **Three invariant fixtures as tests:** a bundled `EvInstance` key ∈ `ctx_inst`; a forward
   key ∈ `ctx_fwd`; a surviving type-var-headed TDict is always a forwarded param (the
   dropped-`fwd_keys` premise — must be a test, not a comment).

**Super-expansion correction (advisor, 2026-07-04) — applies to BOTH encodings.** A head
TDict must carry its transitive-superclass blocks (`Ord (Maybe Int)` lowers to Ord's
methods *and* the `Eq (Maybe Int)` super-block). M3b-2's initial `produce_evidence`
captured only the head class, so #119's populated Evidence was **incomplete** (byte-identical
because unconsumed, but a later PR would have to correct it). Fix folded into #119 before
merge: add an `EvClasses (List Evidence)` wrapper (one block per effective class), port
`class_with_transitive_supers` (verbatim DFS order + diamond-dedup) + `collect_super_map`
into resolve, and super-expand at every constraint-resolution point (head and each context
child). Keeps every merged step internally honest.

**Full M3b — FUNDED (Kuba, 2026-07-03), eyes open on the eta cost below.** The machinery
map (see "eta blocker" below) showed M3b-full needs an *inference-level* change, not just
resolve+lowering work, so it is sequenced as four byte-identical increments:
- **M3b-1** — dormant `Evidence` field (DONE, below).
- **M3b-2** — `resolve.sprout` *populates* Evidence on every TDict; lowering ignores it.
- **M3b-3** — lowering *consumes* Evidence on the call-position path (`expand_dict_witness_args`),
  falling back to `resolve_tdict` on `EvUnresolved`.
- **M3b-4** — inference emits an evidence-carrying node for value-position class methods so
  the eta path can consume Evidence. Split: **4a** = dormant `TMethodRef` node (DONE, below);
  **4b** = resolve populates it + lowering consumes the CLEAN concrete case (DONE, below); the
  eta **reroute** is deferred to M3b-5 (handled by the `resolve_tdict` fallback, byte-identical).
- **M3b-5** — delete `resolve_tdict`; lowering becomes a pure Evidence→witness printer.

**The eta blocker (why M3b needs inference surgery).** `resolve_tdict` has three callers.
Two are coverable by Evidence-on-TDict: the call-position witness args and nested context
dicts (the latter recurse into `EvInstance`'s child list). The third — the value-position
**eta path** — is entered from a bare `TVar` (`lower_expr` TVar case → `try_eta_in_class`,
lowering.sprout:1119→1131) that inference never turned into a TDict, and it resolves its
inner context dicts by calling `resolve_tdict` (`resolve_eta_substituted_inner_dict`,
lowering.sprout:979-980). To delete `resolve_tdict` the eta path must also run on Evidence,
but it has no TDict to carry it — so inference must emit an evidence-carrying node for
value-position class methods (M3b-4). This is outside the plan's original "resolve produces /
lowering consumes" scope and is the real cost.

**M3b-2 — resolve.sprout populates Evidence. DONE (2026-07-03).**
`resolve_program` now runs two passes: the existing check pass (diagnostic unchanged), then a
pure `rewrite_decls` pass that fills each TDict's Evidence via `produce_evidence`. Decision
order mirrors lowering's `resolve_tdict` exactly: type-variable head → `EvForward key`;
concrete head with a *bundled* instance → `EvInstance key children` (children = one Evidence
per substituted instance context constraint, in declaration order); concrete head known only
via an `@inst:` env marker → `EvUnresolved` (lowering still resolves it, matching today);
concrete head with no instance → `EvMissing` (unreachable once check passes). **No new tables
needed** — the key insight is that a forwarded constraint's own `constraint_key` (e.g.
`Eq_a`) is already the exact super-expanded key lowering's `ctx_fwd` uses, and `EvInstance`
gates on `tables_exists` (bundled decls) so it stays in lockstep with lowering's `ctx_inst`
source. Byte-identical verified: IR for `typeclass_collections_demo`, `tuples`, `maybe_map`,
`fizzbuzz` is unchanged before/after (lowering ignores the field). New test:
`tests/stdlib/compiler/test_resolve_evidence.spr` runs `resolve_program` on a synthetic
program and asserts the populated `EvInstance`/nested/`EvForward` trees (9 assertions).

**M3b-3 — lowering consumes Evidence on the call-position path. DONE (2026-07-04).**
`expand_dict_witness_args` switches `resolve_tdict(c, pos, ctx)` → `evidence_to_witness(ev, c,
pos, ctx)`: a fully-resolved tree is consumed by `consume_block`, which REUSES the existing
`resolve_tdict_apply_inner_dicts`/`resolve_method_var` helpers (same witness shape, only the
decision comes from Evidence); any `EvUnresolved`/`EvMissing` in the tree falls back to
`resolve_tdict` for the whole constraint (env-marker-only imports — today's behavior).
Byte-identical verified comprehensively: the new compiler's `--emit-ir` matches the old
(master-seed) compiler's on all 40 examples and the stdlib test corpus.

Two design facts emerged, both correcting earlier assumptions:
1. **`fwd_keys` are required after all (the C.11 machinery, but cheap for key-based).**
   M3b-2's "no new tables" claim was wrong for consumption: a CONCRETE-headed constraint that
   the enclosing function *forwards* (e.g. `where Summable (Vec Int)`) has a bundled instance
   too, and lowering's `resolve_tdict_with_key` checks `ctx_fwd` BEFORE `ctx_inst`. resolve was
   picking `EvInstance` where lowering used the forwarded slot — a real divergence (caught by
   the byte diff on `typeclass_collections_demo`). Fix: `produce_one_class` consults a per-body
   `fwd_keys` SET (the where-clause constraints, super-expanded, keyed exactly as
   `build_hidden_for_constraints`) and emits `EvForward` for any key in it, concrete or not —
   mirroring the `ctx_fwd`-first priority. For key-based this is just a `Dict Bool`; name-based
   would have needed the full slot-name ABI here (why key-based was the right call).
2. **The loud-miss is `EvInstance`-only, not `EvForward`.** Advisor condition 1 said both
   `ctx_inst` and `ctx_fwd` misses should panic. But a metavar/phantom head (`Ord__` from an
   unresolved fresh tyvar) is legitimately `EvForward` and legitimately misses `ctx_fwd` — it
   must null-fill exactly as today (guarded by `test_unresolved_dict_nullfill`). So `EvForward`
   miss reproduces the sentinel (byte-identical); only `EvInstance` miss panics, where resolve
   genuinely proved a *bundled* instance exists so a `ctx_inst` miss is a real table
   disagreement. The "type-var head is always forwarded" premise is therefore FALSE — the
   phantom is the counterexample, found by the located panic firing during self-compile.
New test: `tests/stdlib/test_dict_evidence_consumption.spr` (forwarded + concrete-at-call +
super-having + nested-constrained shapes; compile-and-run regression).

**M3b-4a — inference emits a value-position method node (dormant). DONE (2026-07-04).**
Kuba chose the *localized new node* over eta-expanding in inference (Option A): a value-position
class method used as a value (e.g. `list_map(to_string, xs)`) is now carried by a dedicated
`typed_ast.TMethodRef method_name class_name types.Type source.SourcePos Evidence`, minted by a
new `infer_var` helper `value_var_node` when the reference has an `@class:{name}` marker.
Rationale over Option B: eta-expansion is a *desugaring*; doing it in inference would put
synthetic lambdas in the typed AST, degrading the source-faithful LSP/TASTy artifact and the
pass boundary (infer types / lower desugars). The extra node keeps value-position methods
observable — the price of the ~24-site exhaustive-match ripple, which is mechanical and gated.

4a is the M3b-1 analog: the node is emitted but **nothing consumes its Evidence yet**.
Lowering's `TVar` eta body is extracted into a shared `lower_value_var(name, t, pos, ctx)`;
both the `TVar` and new `TMethodRef` arms of `lower_expr` delegate to it, so whichever node
inference emits, lowering runs the *identical* eta reconstruction (bound-var gate included).
This makes 4a byte-identical **regardless** of `value_var_node`'s detection precision — the
`TVar`/`TMethodRef` split only starts to matter in 4b when `TMethodRef` alone carries Evidence.

Arm strategy for the 14 at-risk `TypedExpr` matches (census: 14 at-risk + 14 wildcard):
- **Pre-lowering** (typed_ast `typed_expr_type`/`typed_expr_pos`; infer `apply_subst_typed_expr`,
  `resolve_dispatch_typed_expr`, `assert_resolved_typed_expr`) — mirror `TVar`/`TDict`. `TMethodRef`
  genuinely flows through these; `apply_subst_typed_expr` is what makes its type concrete (the head
  lowering later reads).
- **Post-lowering** (dce ×3, ast_to_ir ×3, codegen ×2) — **loud panic**. Lowering eliminates every
  `TMethodRef` before dce/codegen run (`compile_phase_lower` = check→lower→dce→codegen), so these
  arms are unreachable; a panic turns any future leak into a located oracle instead of silent
  codegen corruption (the M3b-3 located-panic pattern).
- `resolve.rewrite_expr`/`check_expr` wildcards pass `TMethodRef` through unchanged in 4a (Evidence
  stays `EvUnresolved`); `rewrite_expr` gains a real arm in 4b.

Verified: (1) byte-identical `--emit-ir` stage1(old-seed) vs stage2(new source) on all 158
corpus files (0 diffs) via new `scripts/ir_byte_identical_check.sh`; (2) a temporary panic probe
in `value_var_node` confirmed the path *fires* on real code (`to_string` in
`tests/stdlib/test_to_string.spr:27`) — the byte-identity is not vacuous. New constructor test:
`tests/stdlib/compiler/test_method_ref.spr`.

**M3b-4b — resolve populates + lowering consumes the CLEAN eta case. DONE (2026-07-05).**
Scope was REFINED from the original "incl. the eta reroute" to "clean cases via Evidence, reroute
deferred to M3b-5" (advisor, on the finding below). A faithful M3b-3 analog for the value-position
path.

*Resolve side.* `rewrite_expr` gains a `TMethodRef` arm calling `method_ref_evidence`, which
derives the class-parameter head type from the method's (substituted) type and reuses
`produce_one_class` for that SINGLE class (not the super-expanding `produce_evidence` wrapper — a
method ref extracts one class's slot). The head derivation (`mr_eta_class_type` + `match_type_vars`
+ helpers) is ported ~verbatim from lowering; it needs the class→param map, so a 5th `Tables` field
`class_params: Dict String` was added (built by `collect_class_params` from `ClassDecl`
TPassThroughs, same scan shape as `collect_super_map`). Non-concrete / non-derivable head →
`EvUnresolved` (lowering falls back).

*Lowering side.* `lower_expr`'s `TMethodRef` arm: bound-var check first (a shadowed local is still
emitted as `TMethodRef`), then `eta_from_evidence`, else `lower_value_var` (today's reconstruction).
`eta_from_evidence` handles ONLY the clean case — a bundled `EvInstance` whose method slot is in
`ctx_inst` and whose inner context dicts (consumed via M3b-3's `consume_inner_dicts`) contain no
`__unresolved_` sentinel — building `make_eta_lambda_with_dicts(impl, t, inner_dicts, pos)`. An
empty-children `EvInstance` yields the same node as today's `make_eta_lambda(impl, t)` (outcome 3);
non-empty is outcome 4. Everything else returns `Nothing` → fallback: `EvForward` (forwarded or
phantom), `EvUnresolved`/`EvMissing`, non-concrete head, and the **reroute** (an inner forward-miss
surfaces as a `__unresolved_` sentinel in the consumed dicts → `has_unresolved_dict` → fall back,
where `lower_value_var` reroutes to the outer slot exactly as today). Reroute-in-Evidence is M3b-5.

*The finding that refined scope.* The eta path's ONLY `resolve_tdict` calls are for inner context
dicts; the impl-vs-forward decision and reroute trigger are plain `ctx` lookups. So only inner-dict
resolution needed Evidence-ifying; the reroute is reachable only through it and the existing
reconstruction handles it byte-identically — so deferring it (fallback) is a real M3b-3 analog, not
a shortcut. At **eta** position `EvInstance(impl, [EvForward(inner-miss)])` reroutes to the outer
slot; at **call** position the same shape null-fills — the asymmetry stays unencoded until M3b-5
forces it.

Verified: byte-identical `--emit-ir` stage1(old-seed) vs stage2(new source) on all 159 corpus files
(0 diffs) at BOTH checkpoints — populate-only (lowering ignores) AND full (lowering consumes). A
temporary panic probe in `eta_from_instance` confirmed the consumer FIRES on real code, both outcome
3 (`to_string@Int`, empty inner dicts) and outcome 4 (`my_eq@Box` in
`test_constrained_eta_codegen.spr`, with a `MyEq Int` inner dict) — so the consumer is non-vacuous
(the eta clean path genuinely consumes Evidence, groundwork for M3b-5). The **reroute** is covered by
the existing `test_constrained_eta_codegen.spr:box_eq_via_hof` (forwards `MyEq (Box a)`, uses `my_eq`
value-position → `EvForward` → fallback), byte-identical in the harness. New test:
`tests/stdlib/test_value_position_method_ref.spr`. Next: **M3b-5** — delete `resolve_tdict`,
Evidence-ify the reroute, make the eta path a pure Evidence→witness printer.

**M3b-5 (PR-A) — eta reroute consumed from Evidence. DONE (2026-07-07).**
Split from M3b-5 per the sequencing decision ("encode first, delete second") so byte-identity is
provable at each half; `resolve_tdict` stays as an unreached-but-present fallback until PR-B removes
it. `eta_from_evidence` gains an `EvForward class key` arm — the eta analog of `consume_block`'s
call-path `EvForward` arm — that looks up `ctx_fwd[key]`, then the method's slot within it, and emits
`make_eta_lambda(fwd_slot, t)`. A `ctx_fwd` miss (a phantom fresh tyvar resolve emitted `EvForward`
for optimistically) returns `Nothing`, so `lower_value_var` null-fills exactly as today. It reuses the
identical `dict_get(key, ctx_fwd(ctx))` lookup the call path already proved key-correct in M3b-3, so
the key↔`ctx_fwd` correspondence needs no new argument.

*Correction to the M3b-4b framing above.* The value-position reroute is driven by a **top-level
`EvForward`**, not `EvInstance(impl, [EvForward(inner-miss)])`. When the forwarded key is in
`fwd_keys`, `produce_one_class` short-circuits (line 237) to `EvForward` before ever reaching the
concrete/bundled branch — so a forwarded reroute never produces an `EvInstance` with an unresolved
child in the first place; that `has_unresolved_dict` path is a distinct, rarer sub-case. A temporary
panic probe confirmed the new arm FIRES on `box_eq_via_hof` (key `MyEq_Box`) and a new distinct-output
regression (key `Label_Wrap`) — non-vacuous. Verified byte-identical `--emit-ir` OLD(seed) vs
NEW(source) across the 181-file corpus (0 diffs). New test:
`tests/stdlib/test_value_position_reroute.spr` (distinct per-instance strings so a wrong-slot
dispatch is observable, unlike `box_eq_via_hof`'s unconditional `true`).

**M3b-5 (PR-B) — `resolve_tdict` DELETED. DONE (2026-07-08).**
`resolve_tdict` + its decision family (`resolve_tdict_for_classes` / `_with_key` / `_for_inst`,
`resolve_inner_constraint_dicts`) are gone.  The witness-BUILDING helpers
(`resolve_tdict_apply_inner_dicts` / `resolve_method_with_lambda` / `resolve_method_var`) stay — the
Evidence consumer (`consume_block`) reuses them so witnesses are unchanged.  Three fallback sites were
handled: the call-path `evidence_to_witness` `EvUnresolved`/`EvMissing` fallback now emits the
`__unresolved_` sentinel directly (`evidence_unresolved_witnesses`) — sound because
`EvUnresolved`/`EvMissing` ⟹ not forwarded (`EvForward` is fully-resolved) ⟹ both `ctx_fwd` and
`ctx_inst` necessarily miss, so `resolve_tdict` produced that same sentinel; and the two eta
inner-dict sites became located unreachable-guards (`eta_inner_dict_unreachable`) — census-verified
they never fire across the corpus + self-compile.

**Verification method correction (important for future M3b work).** The byte-identity harness's active
`--emit-ir` path *always* resolves, so it compared `consume_evidence` vs `consume_evidence` and NEVER
exercised `resolve_tdict`.  `resolve_tdict`'s only live consumers were the *non-resolving* paths:
`test_lowering`'s `run_lower`, and the `--use-direct-codegen` pipeline body
(`compile_phase_recheck_timed`, live via `cpr_differential_check` / `ir_runtime_parity` / `flip-smoke`).
Both were pre-M3b relics that skipped the resolve pass and relied on `resolve_tdict` self-resolving;
both now run `resolve.resolve_program` before `lower_program` (mirroring `compile_phase_check`).
`compile_full`/`full_driver` is dead (no recipe) and was left untouched.

**Behavior change (verified correct, NOT byte-identical).** For a value-position method inside a
function that forwards a *parameterized* constraint whose inner dict is ALSO forwarded
(`where MyEq (Box a), MyEq a`), `resolve` emits `EvForward` for the forwarded outer key, so lowering
uses the forwarded slot directly (2-arg eta lambda) instead of reconstructing from the concrete
instance + inner dict (the deleted `resolve_tdict` eta path's 3/4-arg output).  This unifies the eta
path with the call path (fwd-first) and is the more consistent behavior; the forwarded outer dict is
already complete.  Runtime-verified via `tests/stdlib/test_forwarded_inner_dict_dispatch.spr`
(observable inner dispatch: `Box`'s method calls the inner element's method, distinct per-instance
strings — `box(int)`/`box(bool)` correct on both codegen paths).  `test_lowering`'s 4 structural
arg-count assertions (p1-constrained, p1b-multi-var/mixed-inner/super-inner) were updated 3/4 → 2.

**Still deferred — the eta→single-authority collapse.** `try_eta_in_class` /
`try_eta_forwarded_without_class` remain a *second* resolution authority for ONE shape: a **polymorphic
(type-variable-head) forwarded** value-position method (`apply_any(x, to_string)` inside
`fn f(x: a) ... where ToString a`).  `resolve` emits `EvUnresolved` there (non-concrete head), so
lowering resolves it.  Making `resolve` emit `EvForward` instead produces a key
(`ToString_<generalized-tyvar>`) that misses `ctx_fwd`'s source-name key (`ToString_a`) — blocked on
**tyvar canonicalization** (see [[project_typevar_identity_generalization_gap]]).  Until then,
`prereq 1` (`TFunc` gate on `eta_from_evidence`) and `prereq 2` (marker-miss) stay moot: the nullary
value-position case is still caught by `try_eta_in_class`'s existing `TFunc` gate → clean sentinel.

**M3b-5 prerequisites surfaced by the M3b-4 code review (2026-07-05).** A recall-biased
multi-angle review found NO current correctness regression (the strongest candidate — a missing
`TFunc` gate — was empirically refuted: `fn get_empty() -> String = empty` compiles to the SAME
`__eta_unresolved_Monoid_empty` sentinel under old and new). But three items are *latent*, safe only
while the `resolve_tdict` fallback exists, and MUST be handled (with tests) when M3b-5 removes it:
1. **`TFunc` gate on `eta_from_evidence`.** `eta_from_instance` builds
   `make_eta_lambda_with_dicts` with no check that the use-site type is a function. Today a
   non-function value-position method (a nullary method inferred as its bare result type, e.g.
   `empty` at `String`) never reaches it — `try_eta_in_class`'s `TFunc` gate bails and both paths
   emit the sentinel, byte-identical. When the fallback is deleted, that case would reach
   `eta_from_instance` and build a malformed *nullary-applied* lambda (`\ -> impl()` typed as a
   non-function). M3b-5 must add `match t with types.TFunc _ _ _ -> … | _ -> <sentinel/diagnostic>`,
   with a regression test that fails without it (a nullary method in value position).
2. **`value_var_node` classification precision becomes load-bearing.** It emits `TMethodRef` iff an
   `@class:{name}` marker is present and its payload is a bare `types.TConst`. A miss (imported
   method whose marker isn't in env, or a non-`TConst` class type) silently yields `TVar`, which is
   harmless now (the `TVar` eta path reconstructs identically) but at M3b-5 leaves the reference
   with no Evidence AND no fallback. Add a test exercising a marker-miss before deleting the fallback.
3. **Two independent resolutions must collapse to one.** resolve's `method_ref_evidence` and
   lowering's `lower_value_var`/`try_eta_in_class` are two head-derivation + instance-lookup
   implementations kept in lockstep only by the byte-identity corpus (which cannot prove agreement on
   uncovered shapes: multi-param classes, deeply-nested `TApp` heads, the multi-same-class-constraint
   `@eta_fwd` disambiguation). M3b-5's deletion of `resolve_tdict` collapses them to one authority —
   that is the real fix; until then the `mr_*` helpers in resolve are verbatim copies of lowering's
   (`match_type_vars`, `type_is_unit`, `eta_class_type` family) that can silently drift.

**If full M3b is ever done — design notes.** Option A: resolve emits `EvInstance` for the
concrete subtree + an opaque `EvForward` marker; lowering fills the forward slot from
`ctx_fwd` mechanically (a lookup, no *decision*). Evidence threading: add an `Evidence`
field to `typed_ast.TDict` (ripples to 36 sites: 12 constructions in infer + 24 matches
across 7 files; travels with the node and
survives dce/apply_subst — a pos-keyed side table breaks on synthetic/duplicate-pos
TDicts). Sequence `resolve_tdict` deletion LAST, after both paths consume evidence and the
fixed-point still holds. Three known traps: the load-bearing sentinel (below), per-function
`ctx_fwd`, and the eta reroute strategy.

---

## Appendix — `__unresolved_` / `__eta_unresolved_` sentinel-flow map (M3b spec)

Verified 2026-06-30; line numbers re-verified 2026-07-02. All lines in `stdlib/compiler/`.

**Mint sites — Family A `__unresolved_*` (dict resolution), all in the `resolve_tdict`
family:**
- **A1** `lowering.sprout:1245` `resolve_tdict_with_key` — `"__unresolved_" ++ key` (per
  method). Condition: key absent from BOTH `ctx_fwd` (1235) AND `ctx_inst` (1240).
- **A2** `lowering.sprout:1292` `resolve_method_with_lambda` — `"__unresolved_" ++ key ++
  "_" ++ method`. Condition: concrete instance matched but a method slot missing from its
  `method_map`.
- **A3** `lowering.sprout:1310` `resolve_method_var` — `"__unresolved_" ++ fallback_key ++
  "_" ++ method`. Condition: method absent from the resolved `method_map`.

**Mint site — Family B `__eta_unresolved_*` (eta expansion):**
- **B1** `lowering.sprout:1136` `lower_expr` (TVar) — `"__eta_unresolved_" ++
  string.after_last_dot(class_name) ++ "_" ++ string.after_last_dot(name)`. Condition:
  bare TVar is a class method but all eta-expansion attempts failed. Carries the real type.
  NO dedicated consumer → hard error at `ast_to_ir.sprout:781`; silent zero at
  `codegen.sprout:1900`.

**Consumers of Family A:**
- **C1 (transient, reroute)** `has_unresolved_dict` `lowering.sprout:850`, used at `:1064`
  in `try_eta_in_class`: if an eta inner-dict list contains a sentinel, REROUTE to the
  forwarded slot (`make_eta_lambda(fwd_slot,…)` 1066) or return `Nothing` (1067). Sentinel
  is discarded — never reaches codegen.
- **C2 (terminal, explicit null-fill)** `ast_to_ir.sprout:776`: `str_starts_with(name,
  "__unresolved_")` → `IRConst 0`; other unknown → hard error (781).
- **C3 (terminal, implicit null-fill)** `codegen.sprout:1900` `emit_var`: any unknown name
  (incl. sentinels) falls through to `zero_val`. NOT sentinel-specific.

**The two paths:**
- **Call-position (TERMINAL):** `lower_expr`(TCall) → `expand_call_args`(1177) →
  `expand_dict_witness_args`(1186) → `resolve_tdict`(1198) → … → mint A1/A2/A3. The result
  is spliced straight into TCall witness args; `has_unresolved_dict` is NEVER called on it.
  → reaches codegen (C2/C3). **This is the only path to the null-fill, and the path the
  original bug took.**
- **Eta/value-position (TRANSIENT):** `lower_expr`(TVar) → `try_eta_in_class`(1048) →
  `lookup_eta_inner_dicts_general`(949) → `resolve_tdict` → mint A1/A2/A3, THEN inspected by
  `has_unresolved_dict`(1064) → reroute/discard. Family-A sentinels never escape here; a
  failed reroute instead mints a Family-B sentinel (B1) that does reach codegen.

**Refactor hazard:** `has_unresolved_dict` matches the bare prefix `"__unresolved_"` and
does NOT match `"__eta_unresolved_"`. Unifying or renaming the prefixes silently changes
which family the reroute predicate (1064) and the null-fill guard (`ast_to_ir:776`) catch.

## 5. Impact

- **Syntax/semantics:** none, except programs that currently segfault now fail to
  compile with a diagnostic.
- **Type system:** instance context constraints (`where C a`) are now discharged
  recursively at use sites — closing a real soundness hole.
- **Error messages:** missing nested instances produce the existing "No instance of X
  for Y" message (with a real source position via the carried `pos`).
- **Compatibility / ABI:** witness layout/order unchanged through M4; the dict ABI and
  bootstrap seed are preserved. Seed refresh + fixed-point verification at each
  compiler-source milestone (DoD #9).

## 6. Tests

- M0 negative + positive fixtures (above). DONE.
- Broaden coverage for the blast radius: `Maybe`, `Vec`, tuples, and *nested* missing
  instances (`to_string([[()]])`, `to_string(Just(()))`). **DONE (2026-07-02):**
  `missing_nested_instance_maybe` (`Just(())`) landed with M0;
  `missing_nested_instance_deep` (`[[()]]`) added — the doubly-nested case rejects
  correctly with no code change, confirming resolve's context discharge is fully
  recursive.
- Eq/Ord/Serialize parallels (same constrained-instance machinery) get at least one
  missing-instance negative test each. **DONE for Eq/Ord (2026-07-02):**
  `missing_nested_instance_eq` (`[()] == [()]` → `No instance of Eq for Unit`) and
  `missing_nested_instance_ord` (`compare([()], [()])` → `No instance of Ord for
  Unit`) both reject with no code change — the check-phase discharge generalizes
  across classes, not just ToString. **Serialize is N/A:** no `Serialize` class exists
  yet (deriving v1 still in design); when it lands, add its negative fixture then.
- Golden IR snapshots + `just verify-bootstrap-fixed-point` as the no-ABI-drift gate
  through M4.

## 7. Risks

- This is the most delicate subsystem (dict ABI, hidden params, TCO of constrained
  recursive calls, return-type dispatch, GC rooting of dict values). Memory notes:
  `project_recursive_constrained_fix`, `project_eta_fwd_namespace`,
  `project_return_type_typeclass_dispatch`, `project_typed_codegen_unresolved_dict_nullfill`.
- Mitigation: M0–M2 deliver the user-visible fix with minimal ABI surface; M3–M5 are
  gated on byte-identical IR until the final intended changes. Seed refresh + fixed-point
  at every step. Stop-and-review checkpoints between milestones.
```
