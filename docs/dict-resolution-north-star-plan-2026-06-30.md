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

**M2 — Checker-side recursive discharge → diagnostics (closes the bug).**
In `check_instance_for_marker` (infer.sprout:748–789) and the sibling resolution sites
(1211, 1275, 1295, 1456, 3118), after matching the head, instantiate the stored context
constraints at the concrete args and recursively resolve; emit the existing "No instance
of X for Y" diagnostic on a miss. **M0 negative test now passes.** At this point the
runtime segfault class is closed even though lowering/codegen still do the mechanical
resolution. *(This subsumes the originally-scoped #1+#2 fix.)*

**M3 — Evidence representation + checker produces it (north-star core).**
Add `Evidence` + evidence-carrying node. The post-inference pass rewrites each TDict
constraint into a resolved `Evidence` tree. Lowering starts consuming `Evidence` instead
of re-resolving — but must emit **byte-identical** witness exprs. Gate: golden IR / `just
verify-bootstrap-fixed-point` unchanged.

**M4 — Make lowering mechanical.**
Strip the `resolve_tdict` family of all lookups (it becomes an `Evidence`-walk). Move
`inst`/`fwd`/`inst_meta`/`super_map` table-building to the resolution pass or a shared
builder. Delete the three `__unresolved_*` sentinel sites (now unreachable → convert to
`panic`/internal-error).

**M5 — Codegen null-fill → hard error (guarded).**
Convert ast_to_ir.sprout:776 and codegen.sprout:1900 dict null-fill to a loud compiler
error. **Caveat:** the *legitimate* phantom free-tyvar dict (provably never invoked;
ast_to_ir.sprout:764-775) must be preserved — but now distinguished by an **explicit
marker** (e.g. `EvForward` of a known-phantom slot) rather than by the `__unresolved_`
name prefix. M2's guarantee (every *concrete* constraint resolved or rejected) is what
makes any surviving unresolved dict provably phantom.

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
