# PR-A root cause — typed rooting pass omits IRMakeCtor operand roots

**Date:** 2026-06-23
**Status:** SOLVED. Two entangled root causes (operand-exposure + tuple
heap-classification) both fixed; `test_ir_rooting` is GREEN under
`SPROUT_GC_STRESS=1` AND default on the typed path (20/20). Seed refreshed
(fingerprint 055eaac9…). Full `mise exec -- just test` GREEN (all suites passed)
after correcting `test_ir_codegen_{ctors,match}` expectations that encoded the
pre-fix under-rooting.

### Exposed regression (fixed): init-globals root-slot name collision
First parity run after the rooting fix showed +2 new TYPED-COMPILE reds
(`test_toplevel_let_closure`, `test_ir_partial_application`), both
`opt --passes=verify` "multiple definition of local value named 't0'" in
`@__sprout_init_globals`. CAUSE (pre-existing, exposed not caused by the rooting
fix): the synthetic `__sprout_init_globals` and the lambdas it lifts (e.g.
`@__sprout_partial_N`) were never registered in `idx_map`, so the rooting pass
defaulted their fresh root-slot idx to 0 → `%t0` collided with body temps. The
new operand roots are the FIRST roots emitted there, so the hole only now
surfaces. FIX: `synthesize_init_globals_fn` now returns its `next_idx`; both the
streaming path (`ir_pipeline.compile_program_streaming`, which `--use-ir-codegen`
uses) and the batch path (`ast_to_ir.translate_program`) register
`__sprout_init_globals → next_idx` and read `lifted_idx_ref` AFTER init synthesis
so init-lifted wrappers are included. Both regressed files now opt-verify clean;
`test_ir_rooting` still stress-green.

ALL GATES GREEN (2026-06-24):
- `mise exec -- just test`: all suites PASSED.
- parity (`scripts/ir_runtime_parity.sh`): 106 runnable, **100 OK** (was 89 at
  P11-2d baseline, +11), **2 TYPED-RUNTIME** (was 9, −7), **4 TYPED-COMPILE**
  (was 8, −4), 0 TYPED-LINK. The 6 remaining non-OK are all PRE-EXISTING
  (tuple-value rendering ×2: tuples.sprout/06_tuple_param.spr; deriving/eq/astar
  compile class ×4). Zero new reds.
- smoke-shapes ✓, bundle-smoke ✓, compile-examples-stage1 ✓ (3 xfail expected),
  run-example-canary ✓.
- seed refreshed (fingerprint 8a0e2081…); fmt clean.
Not yet committed (on master — awaiting branch + commit go-ahead).
**Repro (deterministic oracle):** typed build of `tests/stdlib/test_ir_rooting.spr`
under `SPROUT_GC_STRESS=1` → `runtime error: non-exhaustive match` in
`ir_rooting.rewrite_fns`. Direct build passes default **and** stress. So it is
typed-specific and in scope.

## Build recipe used
```
build/compile_driver_bin_stage1 --use-ir-codegen stdlib tests/stdlib/test_ir_rooting.spr > /tmp/rooting_typed.ll
mise exec -- clang -g /tmp/rooting_typed.ll runtime/sprout_runtime.c -O0 \
  -framework Security -framework CoreFoundation -o /tmp/rooting_typed_bin
SPROUT_GC_STRESS=1 /tmp/rooting_typed_bin        # crashes
```
(Direct: same with `--emit-ir`; passes under stress. Note parity's `link_ir`
links at `-O0` too — no `-O` flag.)

## What lldb proved (not theory)
Stateful lldb script (`/tmp/uaf.py`) recorded every `register_managed_ptr` and
`sprout_gc_free_payload`, and stopped when `rewrite_fns` was entered with an
`fns` pointer that had already been freed. Victim address history (one run):

- **alloc** `sprout_make0(6)` in `test1_program` → the shared **`Nil`** node (`%t2`).
- **FREED** during a `sprout_make4`-triggered GC in `test1_program`
  (`make4 → sprout_alloc_obj_raw → sprout_gc_maybe_collect_threshold → sweep →
  free_payload`). It was *swept* (unmarked ⇒ unreachable from any root).
- reused as `IRProgram` (tag 59) → `rewrite_fns` reads tag 59, expects List
  (Nil=6/Cons=5) → non-exhaustive match.

## The exact miscompile (typed IR, `test1_program`)
```
%t39 = call i64 @sprout_make4(i64 54, i64 %t1, i64 %t2, i64 %t4, i64 %t38)   ; IRFunction(name, params=Nil, rettype, blocks)
```
The `pop_roots(3)` is emitted *before* this `make4`, and **no `push_i64_root`
wraps it**. `%t2` (Nil) is an operand consumed by the make and dead afterward,
so the pass's "live-after-trigger" rule emits no root. `sprout_make4` allocates
(GC point) before storing its argument registers, so `%t2` is swept.

## Why direct survives
Direct (reference codegen) **roots make4's operands**: 4 `push_i64_root` before
each `make4`, `pop_roots(4)` after (e.g. `/tmp/rooting_direct.ll` test1_program
make4 sites, tags 54 and 59).

## Root cause
The rooting model assumes "an op's operands are covered by the runtime
`SPROUT_HANDLE` during the call window" (see `test_ir_rooting.spr` header lines
14-17). True for handle-wrapped builtins (`str_concat`/`str_slice`/`str_from_int`
call `SPROUT_HANDLE(...)` before `maybe_collect`). **False for `sprout_makeN` /
`IRMakeCtor`**, which allocate then store unprotected operand registers. The
typed pass applies the assumption uniformly, so it never roots `IRMakeCtor`
operands. Direct does.

## Fix direction (pass-side, matches reference; not yet implemented)
In `stdlib/compiler/ir_rooting.sprout`: for `IRMakeCtor` triggers, root the
op's own **heap operands** across the make (in addition to values live after).
Open questions to settle before coding:
- Does the same operand-exposure affect `IRMkClosure` / `IRApplyClosure` /
  user `IRCall`? (Check whether those runtime paths handle-protect args; user
  calls have the callee root its params, but there may be a pre-root window.)
- Scope the change to the confirmed `IRMakeCtor` case first (TDD), with a
  deterministic IR-text test asserting roots wrap the make, plus the
  `SPROUT_GC_STRESS` behavioral pass as the causation oracle.

## Acceptance
- `test_ir_rooting` green under `SPROUT_GC_STRESS=1` and default (typed).
- direct still passes; `range_and_intrinsics` unchanged.
- full `scripts/ir_runtime_parity.sh` net-positive, no new reds.

## Fix IMPLEMENTED (operand-exposure class) — landed in working tree
`stdlib/compiler/ir_rooting.sprout`: added `op_exposes_operands`
(IRMakeCtor / IRMkClosure), `op_operand_set`, `roots_across_loop`,
`roots_across`; `maybe_wrap` now uses `roots_across` (= live-after ∪ heap
operands, for the exposing subclass only; identical to `live_across` for all
other triggers). Tests T5 (updated 1→2 roots), T6 (IRMakeCtor operand), T7
(IRMkClosure capture) added to `tests/stdlib/test_ir_rooting.spr`. RED→GREEN
verified at the library level (17/17). Seed refreshed.

IR confirms the fix: typed `test1_program` `make4`s now wrapped with 4 roots +
`pop_roots(4)`, matching direct. The `test1_program`→`rewrite_fns` victim is
GONE (lldb re-trace finds no premature free there).

## RELOCATION — a SECOND, DIFFERENT-CLASS victim under stress (still RED)
After the fix, the typed stress oracle still crashes — relocated to:
`list_reverse_go ← list_reverse ← rewrite_ops` (line 500, `Nil -> list_reverse(acc)`).
lldb history of the new victim (`0x600001eec3c0`):
- alloc `make2` in `set_to_list` (`bst_to_list_acc ← native_set_to_list`) — a `Cons`.
- FREED during GC triggered by `make0` (Nil, no operands) **inside `op_uses`**
  (`op_uses ← op_live_before ← live_before_first ← live_after_head`).

This is NOT operand-exposure (the make0 has no operands). It is a
**live-across-call** miss: a `List` value held live across a call to `op_uses`
(which allocates internally) is not rooted by its caller in the typed-compiled
rooting pass. Different class; the operand fix does not address it.

## Class #2 ROOT CAUSE found (IR-level, confirmed) — tuple heap-classification gap
The relocated victim is a tuple-rooting gap, NOT a new operand or liveness bug:

`op_produces_simple_heap` in `ir_rooting.sprout` has **no `IRMakeTuple` and no
`IRGetTupleField` case** → both fall through to `Nothing` → tuple values never
enter `heap_origin` → never rooted. (`op_uses`/`op_def` DO handle them, so they
are tracked for liveness/def but not heap-origin.)

IR proof in typed `rewrite_ops`: `maybe_wrap` returns a `(List IROp, Int)`
tuple `%t12`; `%t15` (the new `acc`) is extracted via IRGetTupleField and is
LIVE across the later `update_scope` call (an allocating user call), used in
the recursive `rewrite_ops`. The roots around `update_scope` cover
`%t10/%prefix/%heap_origin/%block_live_out` but NOT `%t15` — because `%t15`
(IRGetTupleField result) is not heap-classified. So `acc` is swept and
`list_reverse(acc)` later reads a corrupted node.

This is a SINGLE systemic cause (every tuple-returning rooting-pass function is
affected) and it is **PR-B's territory**: PR-B was exactly
`IRMakeTuple r _ -> Just(r)` in `op_produces_simple_heap`. The full fix also
needs `IRGetTupleField r _ _ -> Just(r)` (conservative; the op carries no kind).

### Conclusion: PR-A and PR-B are ENTANGLED, not sequential
`test_ir_rooting` stress-green requires BOTH:
1. operand-exposure fix (IRMakeCtor/IRMkClosure operands) — DONE this session.
2. tuple heap-classification (IRMakeTuple + IRGetTupleField results) — PR-B + ext.

The handoff's claim that PR-A must land before PR-B (because PR-B exposes PR-A)
is half right: they expose each other; the corpus file exercises both ctors and
tuples pervasively, so neither fix alone makes it stress-green. Scope decision
for Kuba: fold both into one landing, or keep the operand fix as its own PR and
track tuples as PR-B next.
