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

## NEW IR ops (sprout_ir.sprout)

Append (keeps existing constructor tags):

```
| IRTcoEntry (List (String, String, Bool)) String
    # per param: (slot_name, lltype, is_heap); + sp_save_name.
    # Lowers to: for each, `<slot> = alloca <ty>` + `store <ty> %<name>$in, ptr <slot>`
    #   + if is_heap `<t> = call i64 @sprout_gc_push_i64_root(ptr <slot>)`;
    #   then `<sp> = call ptr @llvm.stacksave()`. (Caller emits IRBr "tco_loop".)
| IRTcoLoad (List (String, String, String))
    # per param: (loaded_name, lltype, slot_name) -> `<loaded> = load <ty>, ptr <slot>`
| IRTcoBack (List (String, String, String)) String String
    # per param: (slot_name, lltype, new_value); + sp_save_name + loop_label.
    # Lowers to: `store <ty> <new>, ptr <slot>` (each)
    #   + `call void @llvm.stackrestore(ptr <sp>)` + `br label %<loop_label>`.
    # ROOT POP is inserted by ir_rooting BEFORE this op (see below), NOT here.
```

Slot names need the `%` convention used elsewhere (temps carry `%`); check how
`alloca` temps are named in the existing IR (`fresh_name`, "%t" prefix).

## CRITICAL: where root-depth accounting lives — ir_rooting, not ast_to_ir

The typed path runs `ast_to_ir` (translate) THEN `ir_rooting` (insert
IRRoot/IRUnroot). So at translate time, ast_to_ir does NOT know how many roots
the body pushes — only `ir_rooting` does. Therefore:

- `ast_to_ir` emits `IRTcoBack` as **store-args + stackrestore + br** only (no
  GC pop count).
- `ir_rooting` must treat `IRTcoBack` like a **looping return**: it already
  pops roots back to the caller depth at `IRRet`; at `IRTcoBack` it must pop back
  to the **loop-header depth** (the root level right after `IRTcoEntry`'s slot
  roots + `IRTcoLoad`), keeping the slot roots alive. It must insert an
  `IRUnroot(current_depth - loop_header_depth)` (or equivalent) before the
  `IRTcoBack`.
- Heap slot roots: either ast_to_ir marks them in `IRTcoEntry(is_heap)` and
  ir_lowering emits the push (current sketch), OR ir_rooting owns it. Pick one;
  keeping the push in ir_lowering (driven by `is_heap`) keeps the count stable
  and known to ir_rooting as the loop-header floor.

Rooting-pass op classification for the 3 new ops:
- `op_triggers_gc`: all **false** (stacksave/store/load/push_root don't collect).
- `op_produces_simple_heap`: all **Nothing** (IRTcoLoad's loaded value is
  already covered by the slot root; do NOT double-root it).
- `op_exposes_operands`: all **false**.
- Plus the IRTcoBack back-edge depth logic above.

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

1. **IR ops + lowering + rooting + print** (no producer yet). Refresh seed,
   `just test` — all green, behavior unchanged. Safe foundation commit.
2. **ast_to_ir detection + scaffold** (turns the producer on). The hard part.
3. Verify: `just tco-diff` climbs 0→N; `just tco-runtime-smoke` GREEN;
   `just test`; `just test-stress` (GC!); smoke-shapes; bundle-smoke;
   compile-examples-stage1; run-example-canary.
4. `just flip-readiness` should now pass step [4] further (still needs #95 argv
   in the seed to fully go green).

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
