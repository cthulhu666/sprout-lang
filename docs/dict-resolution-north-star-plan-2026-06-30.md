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
     Consequence: an imported *constrained* instance whose decls aren't bundled is
     accepted without deep-checking its context — a safe miss (never a false positive).
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

**M3b — Evidence representation + mechanical lowering (the real north-star core; LAST).**
Feasible via Option A: resolve emits `EvInstance` for the concrete subtree + an opaque
`EvForward` marker; lowering fills the forward slot from `ctx_fwd` mechanically (a lookup,
no *decision*) — without moving per-function hidden-param assignment into resolve. Add
`Evidence`, have resolve rewrite each TDict into a resolved tree (or diagnostic), and have
lowering consume it while emitting **byte-identical** witness exprs (preserving the reroute
semantics above). Gate: golden IR / `just verify-bootstrap-fixed-point` unchanged. This is
what structurally eliminates the `__unresolved_`/null-fill escape hatch.

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

- M0 negative + positive fixtures (above).
- Broaden coverage for the blast radius: `Maybe`, `Vec`, tuples, and *nested* missing
  instances (`to_string([[()]])`, `to_string(Just(()))`).
- Eq/Ord/Serialize parallels (same constrained-instance machinery) get at least one
  missing-instance negative test each.
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
