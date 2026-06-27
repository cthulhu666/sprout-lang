# Typed-codegen TCO — implementation handoff (2026-06-27)

**Goal:** make the typed codegen path (`--use-ir-codegen`) emit tail-call-
optimization loops for self-tail-recursive functions, so the typed-built
compiler stops overflowing the stack self-compiling (flip blocker #2). Direct
codegen already does this; typed codegen emits **0** TCO loops (`just tco-diff`
→ `direct=55 typed=0`).

Branch: `fix/typed-codegen-tco`. Design approved. TDD test RED and committed.

## Confirmed root cause

`ast_to_ir.sprout` never checks tail position or the enclosing function name —
every call becomes `IRCall` (`finish_direct_call`). `sprout_ir.sprout` has no
tail-call/loop-header op. `ir_lowering.sprout` emits every `IRCall` as a plain
`call`. So a self-recursive function (e.g. `lexer.tokenize_from`, one call per
token) builds one native frame per iteration → overflow at scale. The parity
corpus (small files) never reaches the depth.

## Approved approach

- **Scope v1: self-tail-recursion only.** Mutual TCO (direct's `musttail` path)
  is a follow-up. Self-recursion covers the blocker (tokenize_from, scan_lines,
  strip_headers_b are all self-recursive).
- **Alloca-slot loop**, mirroring direct codegen (`emit_fn_tco`), NOT PHI. The
  runtime roots via a *stable* slot (root once at entry; back-edges store into
  the slot), which PHI can't give without per-iteration root churn.
- **i64-return only** (match direct's `body_ret_is_i64`; Bool/tuple is the
  BACKLOG P2 follow-up).
- **Option E: encapsulated TCO IR ops** (not general alloca/store/load). Chosen
  on long-term + policy grounds: Sprout's mutability is heap-`Ref`-based so
  general memory ops have no other consumer; the planned IR-rooting linter
  (BACKLOG 5/6) wants ops with declarable rooting semantics. See the session
  discussion / `[[project_blocker2_tokenize_from_tco]]`.

## The LLVM shape to produce (per self-tail-recursive `f(p1, p2)`)

```llvm
define i64 @f(i64 %p1$in, i64 %p2$in) {
entry:
  %p1.slot = alloca i64
  store i64 %p1$in, ptr %p1.slot
  %p2.slot = alloca i64
  store i64 %p2$in, ptr %p2.slot
  ; heap slots only: %r = call i64 @sprout_gc_push_i64_root(ptr %pN.slot)
  %sp = call ptr @llvm.stacksave()
  br label %tco_loop
tco_loop:
  %p1 = load i64, ptr %p1.slot          ; body references %p1/%p2 unchanged
  %p2 = load i64, ptr %p2.slot
  ... body ...
  ; tail self-call f(a1, a2):
  ;   <ir_rooting inserts: pop roots pushed since tco_loop>
  store i64 %a1, ptr %p1.slot
  store i64 %a2, ptr %p2.slot
  call void @llvm.stackrestore(ptr %sp)
  br label %tco_loop
  ; normal return:
  ret i64 %result
}
```

## The param-rename trick (keeps the body translation untouched)

Rename the `IRFunction` params to `%p$in` so `%p` is free for the loaded value.
The body keeps referencing `%p` (via `param_known` → `"%"++name`), and the
`tco_loop` load binds `%p`. So **only the function signature changes; the body
translation is unchanged**. `%p` is loaded at the top of `tco_loop`, which
dominates the whole body (and re-runs each iteration — a load, not a phi).

## NEW IR ops (sprout_ir.sprout)  [CORRECTED — supersedes the first draft]

Append (keeps existing constructor tags). Note `IRTcoLoad` is **per-param**
(single result) so it fits `op_produces_simple_heap`'s `Maybe String` model.

```
| IRTcoEntry (List (String, String, String)) String
    # per param: (slot_name, lltype, in_name=%<p>$in); + sp_save_name.
    # Lowers to: for each `<slot> = alloca <ty>` + `store <ty> <in>, ptr <slot>`;
    #   then `<sp> = call ptr @llvm.stacksave()`.  (Caller emits IRBr "tco_loop".)
    #   NO GC root push — see rooting below.  Slot allocas MUST precede stacksave.
| IRTcoLoad String String String IRType
    # (loaded_name, lltype, slot_name, kind) -> `<loaded> = load <ty>, ptr <slot>`
    # One op PER param, emitted at the top of the tco_loop block.
| IRTcoBack (List (String, String, String)) String String
    # per param: (slot_name, lltype, new_value); + sp_save_name + loop_label.
    # Lowers to: `store <ty> <new>, ptr <slot>` (each)
    #   + `call void @llvm.stackrestore(ptr <sp>)` + `br label %<loop_label>`.
    # Terminator. NO explicit root pop — see rooting below.
```

Slot/temp names follow the existing `%t<N>` (`fresh_name`) convention.

## CRITICAL: rooting is LIVENESS-based, not depth-based (first draft was WRONG)

`ir_rooting` is NOT a depth counter. `maybe_wrap` (ir_rooting.sprout:599)
brackets **each GC-trigger op** with `IRRoot…<op>…IRUnroot(n)` for the heap
values live-across that op (and its exposed operands). Roots are pushed
just-before and popped just-after each trigger — they do NOT accumulate down the
function. So there is **no loop-header depth to pop back to**; `IRTcoBack` needs
**no explicit root pop**.

Why the non-TCO version exhausts the root pool: the *non-tail* recursive
`IRCall`'s heap **argument** is rooted across the call (operand exposure), and
the call doesn't return until the whole recursion unwinds → one root per frame →
131072 exhausted. Turning the tail call into `IRTcoBack` (not an `IRCall`)
removes that accumulating root. The per-trigger bracketing then self-balances
each iteration. **That alone fixes the test.**

### Single-pass liveness is sound here — because state flows through slots

`compute_liveness` (ir_rooting.sprout:399) is a SINGLE backward pass assuming
**no back-edges** (today the typed IR has none). TCO adds the first back-edge.
It stays sound ONLY because `IRTcoLoad` **re-loads each param from its slot at
the loop header every iteration**, so the loop-carried values flow through
*memory* (the slots), and **no SSA value is live across the back-edge**:
- loop-header `live_in` = the slots (used by `IRTcoLoad`), defined once in the
  entry block by `IRTcoEntry` — always live, captured locally.
- a param passed unchanged (`f(n-1, s)`) makes `IRTcoBack` *use* `%s` (the store)
  — that use is in the back-edge block, captured by its own `block_use_def`; the
  successor (loop header) re-loads, so it does not reference `%s`.
- a heap value sitting in a slot is unprotected across a trigger only when it is
  DEAD (not used-after, not passed on) — in which case freeing it is correct.

If a later feature ever makes an SSA value span the back-edge, `compute_liveness`
must be upgraded to fixpoint iteration first. v1 must not.

### Why stacksave/stackrestore IS still needed

The rooting pass emits an `alloca` per `IRRoot` (per trigger) INSIDE the loop
body. Allocas are not freed until function return, so per-iteration root allocas
accumulate the LLVM stack → overflow over many iterations. `IRTcoEntry` takes
`llvm.stacksave` (AFTER the slot allocas, so slots survive); `IRTcoBack` issues
`llvm.stackrestore` before the back-edge, freeing the iteration's root allocas.
This matches direct codegen (BACKLOG:306).

### Rooting-pass op classification for the 3 new ops

- `op_triggers_gc`: all **false** (alloca/store/load/stacksave/stackrestore/br
  never collect).
- `op_produces_simple_heap`: `IRTcoLoad` → `Just(loaded)` iff kind is
  `IRTHeap`/`IRTUnknown` (so the loaded value enters `heap_origin` and gets
  rooted where live-across-triggers); `IRTcoEntry`/`IRTcoBack` → `Nothing`.
- `op_exposes_operands`: all **false** (none trigger GC).
- `op_successors`: `IRTcoBack` → `[loop_label]`; others → `[]`.
- `op_uses`: `IRTcoEntry` → the in_names; `IRTcoLoad` → `[slot]`;
  `IRTcoBack` → new_values ++ slots.
- `op_def`: `IRTcoLoad` → `Just(loaded)`; others `Nothing`.
- `last_op`/terminator handling: `IRTcoBack` is a terminator.
- The slots are `ptr` (alloca results), NOT i64 heap handles — keep them OUT of
  `heap_origin` (don't report them from `op_produces_simple_heap`).

## Exhaustive IROp matches to extend (grep `| sprout_ir.IR` / `| IR`)

At least: `ir_lowering.lower_op`, `ir_rooting` (op_triggers_gc /
op_produces_simple_heap / op_exposes_operands and any depth walker),
`sprout_ir.print_ir_program`. Search for every `match ... with` over `IROp` —
the exhaustive-match guard turns a missed one into a compile error (good).

## Detection in ast_to_ir

Port `has_self_tail_calls` / `has_tail_call_to_set` from `codegen.sprout`
(:1626–1655): a call is a tail self-call if it is the callee in tail position
(if-branches, match-branches, do-last step) targeting the enclosing fn at full
arity. Thread a `Maybe TcoMode` (enclosing name, slots, loop label, ret ll)
through `translate_expr`, mirroring direct's `Maybe TcoCtx`. At a tail self-call,
emit `IRTcoBack` instead of `IRCall`+the value that flows to `IRRet`. Only enable
for i64-returning functions.

`translate_user_fn` / `translate_body` wrap: translate body with first label
`tco_loop` (not `entry`), prepend `IRTcoLoad`, add a new `entry` block with
`IRTcoEntry` + `IRBr "tco_loop"`, rename params to `<p>$in`.

## Staged plan (verify at each step)

1. **[DONE] IR ops + lowering + rooting + print** (no producer yet). The 3 ops
   (`IRTcoEntry`/`IRTcoLoad`/`IRTcoBack`) are in `sprout_ir.sprout`; lowered in
   `ir_lowering.sprout` (+ `lower_tco_entry_slots`/`lower_tco_back_stores` +
   `@llvm.stacksave`/`@llvm.stackrestore` declares); classified in all six
   `ir_rooting` exhaustive matches (+ `tco_entry_uses`/`tco_back_uses`); printed
   in `sprout_ir.print_op`. Unit test: `test_ir_lowering_exports.spr` (10 asserts,
   GREEN) + the hand-checked shape `opt --passes=verify`s clean. Seed refreshed.
2. **[TODO] ast_to_ir detection + scaffold** (turns the producer on). The hard
   part — see below.
3. Verify: `just tco-diff` climbs 0→N; `just tco-runtime-smoke` GREEN;
   `just test`; `just test-stress` (GC!); smoke-shapes; bundle-smoke;
   compile-examples-stage1; run-example-canary.
4. `just flip-readiness` should now pass step [4] further (still needs #95 argv
   in the seed to fully go green).

## Increment 2 producer — concrete notes (ast_to_ir)

Entry points read: `translate_user_fn` (:3953) → `translate_body` (:3894) →
`translate_expr` (the 14-arg workhorse). `translate_body` translates the body
starting at label `"entry"` and appends a single `IRRet(result)` to the final
block. `translate_user_fn` then `build_fn(name, params_with_kinds, ret_ty, blocks)`.

Plan:
- **Detect** (only when `ret_ty == "i64"`): port `has_self_tail_calls` /
  `has_tail_call_to_set` from codegen.sprout:1626 (tail position = if-branches,
  match-branches, do-last; target = enclosing fn at full arity).
- **Rename** params to `<p>$in` in the `IRFunction` param list ONLY; the body
  keeps referencing `%<p>` (so `param_known` is untouched). `IRTcoLoad` binds
  `%<p>` at the loop header.
- **Thread** a `Maybe TcoMode` (enclosing name, `[(slot, ty, <p>)]`, loop label,
  ret_ty) through `translate_expr`. Recommended over a post-translation
  phi-rewrite: tail position is known structurally at emit time, so no phi
  surgery (removing a self-call's incoming from a join phi) is needed.
- **Emit** at a tail self-call: instead of the normal `IRCall` whose result
  flows to `IRRet`, terminate the current block with
  `IRTcoBack([(slot, ty, new_arg)…], sp, "tco_loop")`. NOTE the return-shape
  subtlety: a tail self-call yields NO result value (it's a terminator like
  `IRCondBr`/`IRAbortMatch`), so the translate_expr arm must return a TERMINATED
  block with no out-flowing result — check how the existing terminating ops are
  represented in the `(blocks, cur_lbl, cur_ops, result, idx)` tuple (look for an
  "already terminated" sentinel / how IRAbortMatch arms return).
- **Wrap** in `translate_user_fn` (or a TCO variant of `translate_body`):
  translate the body with first label `"tco_loop"`; prepend one `IRTcoLoad` per
  param; add a new `"entry"` block = `IRTcoEntry([(slot,ty,<p>$in)…], %sp)` +
  `IRBr("tco_loop")`. Compute slot names via `fresh_name`/the `%t` counter.

Rooting needs NOTHING further (increment 1 covers it): the loaded `%<p>` enters
heap_origin via `IRTcoLoad`'s kind; the per-trigger bracketing self-balances;
`stackrestore` (in `IRTcoBack`) frees per-iteration root allocas. Just confirm
under `just test-stress`.

## DoD reminders (compiler-source change)

- **Refresh seed BEFORE `just test`** (stage-1 uses the committed seed; new
  behavior won't show until refreshed) — see `[[feedback_refresh_seed_before_test_for_compiler_changes]]`.
- Delete `build/compile_driver_bin_stage1` before refresh-seed (stale-binary
  guard) — `[[feedback_refresh_seed_stale_binary]]`.
- Wrap self-compile/bootstrap with `scripts/memwatch.sh` (OOM guard).
- `just test-stress` is mandatory — this is GC-rooting-adjacent codegen; the
  default suite can false-green — `[[project_gc_stress_oracle]]`.
- Smoke-shapes + bundle-smoke (compiler-source DoD #7/#8).

## Gates (all currently RED, become GREEN as this lands)

- `just tco-diff` — structural meter (0 → matches direct).
- `just tco-runtime-smoke` — the TDD runtime regression.
- `just flip-readiness` — end-to-end (also needs #95).
