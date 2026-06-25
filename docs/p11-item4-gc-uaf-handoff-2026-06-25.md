# PR 11 Item 4 — GC use-after-free class (self-hosting backend) — handoff

**Date:** 2026-06-25
**Status:** Root cause UNPINNED. Two hypotheses falsified (see §Falsified). Diagnosis,
tooling, and repro are banked here for a fresh, dedicated attempt.

---

## TL;DR

Item 4 is **not one bug** — it is a *class* of GC rooting use-after-frees in the
**typed-codegen-compiled self-hosting backend**, surfacing as
`runtime error: non-exhaustive match` (exit 1) or `EXC_BAD_ACCESS`/`SIGSEGV`
(exit 139) under GC pressure. Known instances:

| Test | default thr. | `SPROUT_GC_DISABLE=1` | `SPROUT_GC_STRESS=1` |
|---|---|---|---|
| `tests/stdlib/test_ir_codegen_ctors.spr` | passes (lucky) | passes | **crashes** (non-exhaustive match) |
| `tests/stdlib/test_ir_codegen_match.spr` | passes (lucky) | passes | **crashes** (non-exhaustive match) |
| `tests/stdlib/test_ir_codegen_closures.spr` | **flips to crash when seed changes** | passes | **crashes** (EXC_BAD_ACCESS) |

`ctors`/`match` are in `STRESS_XFAIL` (justfile `test-stress`). `closures` is currently
green at the default threshold on master **by GC-timing luck** — any reseed can flip it
(it did, locally, during this investigation). "Passes under `GC_DISABLE`, crashes under
`GC_STRESS`" is the definitive signature of this class.

---

## How to reproduce (fast loop)

```sh
# build a binary (if missing): rm -f build/compile_driver_bin_stage1; just bootstrap-from-seed
B=build/compile_driver_bin_stage1
clang -c runtime/sprout_runtime.c -O2 -o /tmp/rt.o
$B --use-ir-codegen stdlib tests/stdlib/test_ir_codegen_ctors.spr > /tmp/c.ll 2>/dev/null
clang /tmp/c.ll /tmp/rt.o -framework Security -framework CoreFoundation -o /tmp/c
SPROUT_GC_STRESS=1 /tmp/c    # last line is a PASS:, not SUITE PASSED → crashed mid-suite
SPROUT_GC_DISABLE=1 /tmp/c   # SUITE PASSED → confirms GC-caused
```
(`just test-stress` runs the curated set; `SPROUT_GC_DISABLE=1` is true GC-off — NOT
`SPROUT_GC_THRESHOLD=huge`, which still collects.)

---

## What is established (do not re-derive)

1. **Crash site (backtrace, all three tests share the shape):**
   `sprout_abort_match` ← `list_reverse_go` ← `list_reverse` ← nested lifted lambdas
   (`__sprout_ir_lambda_*`) ← `ast_to_ir.translate_user_fn` ← `translate_decl_with_idx`
   ← `translate_decls` ← … ← `translate_source`. So `list_reverse` is handed a **corrupt
   list** (a spine node with a garbage/wrong tag) while the backend compiles a user fn.

2. **`list_reverse` / `list_reverse_go` rooting is CORRECT** (verified in emitted IR):
   `list_reverse_go` roots `acc`, `h`, and the tail `t` across its `@sprout_make2` (Cons)
   allocation; `list_reverse` roots `%xs` across its `@sprout_make0` (Nil). The worker is
   not the bug — the list is already corrupt when passed in.

3. **GC traces `Ref` contents correctly** (`runtime/sprout_runtime.c`:
   `sprout_heap_child_count` REF→1, `sprout_heap_child_value` REF→`((RefVal*)ptr)->value`).
   A list held in a `Ref` is reachable; the Ref-tracing hypothesis is dead.

4. **Direct `--emit-ir` does NOT use `ir_rooting`** — `insert_roots` is called only from
   `ir_pipeline.sprout` (typed path). `codegen.sprout` has its own inline rooting. So the
   typed path's rooting (`ir_rooting.sprout`) is the suspect for typed-compiled code; the
   `test_ir_codegen_*` tests are compiled via `--emit-ir` for `just test` but **run the
   full backend at runtime**, so the freed value is in the backend's own execution.

5. **The corrupt value has a real (non-sentinel) tag** read at offset 0 of the SproutObj
   (e.g. tag 11). The scrutinee genuinely is not a `Cons`(5)/`Nil`(6).

---

## Falsified hypotheses (don't repeat)

- **H1: "Ref `%t$11` (ops accumulator) unrooted across a `make1` inside a match arm."**
  Looked right in one IR snapshot, but fixing the liveness (H2) didn't fix the crash, and
  re-reading showed the Ref *is* rooted where it matters. Wrong/incomplete.
- **H2: "`ir_rooting.compute_liveness` is single-pass; iterate to a fixpoint."**
  `compute_liveness` (ir_rooting.sprout ~line 399) IS a single backward pass over
  `list_reverse(blocks)` — and that IS a real latent bug (a value live *through* a match
  whose using-block is listed before the arm is under-rooted). A deterministic regression
  was written (`test_ir_rooting.spr` T11: value live through an arm, block listed before
  successor → RED 0 roots, GREEN 1 root with fixpoint). **BUT:** making it a fixpoint
  - did **not** fix ctors/match (still crash under stress), and
  - its reseed **exposed `closures`** at the default threshold (DoD #5 `just test` failed),
  so it is **not landable as-is** and is **not the item-4 cause**. Reverted.
  *(The fixpoint-liveness fix is a genuine separate correctness improvement worth
  revisiting on its own — but only with a plan for the closures fallout, which proves
  there are MORE latent UAFs that more-rooting exposes rather than fixes.)*

---

## Tooling (built this session)

`scripts/gc_free_trace.py` — added a **`gctracetag <fn> <legal-tags>`** lldb command
(alongside the existing `gctrace`). It records every alloc/free and **stops when the
watched fn is entered with a scrutinee whose tag (offset 0) is not legal** — robust to
freed-then-reallocated addresses (the old `gctrace` free-history check false-positives on
hot functions like `list_reverse_go`). Usage:
```sh
lldb -b -o "settings set target.env-vars SPROUT_GC_STRESS=1" \
        -o "command script import scripts/gc_free_trace.py" \
        -o "gctracetag list_reverse_go 5,6" -o run -o quit /tmp/c_dbg   # built with -g -O0
```
**Known limitation to fix first:** the `_hist` alloc/free **history dump is unreliable** —
known-good (tag 5/6) entries showed `history_events=0`, i.e. the lookup key (`x0`) doesn't
match the recorded `register_managed_ptr` ptr for OBJ allocs in practice, even though
`box_ptr`/`unbox_ptr` are identity and `register_obj` registers the same `obj`. The
**tag-catch firing is reliable**; the lineage dump is not. Fixing the keying is the
prerequisite for using the tool to name the freed value's alloc+free backtraces.

---

## Recommended next steps (for the focused session)

1. **Fix `gctracetag` history keying** (so alloc/free lineage works). Re-check what address
   `register_managed_ptr`'s `ptr` arg holds at the breakpoint vs `x0` at `list_reverse_go`
   — instrument both and diff one concrete entry. Once history works, the tool names the
   victim's alloc site (what it is) and free site (the unrooted-live-across-alloc frame =
   the bug).
2. **Pin the actual freed value** with the fixed tool: catch the corrupt `list_reverse_go`
   entry (tag ∉ {5,6}) and dump its lineage. The free backtrace's frame is the rooting gap.
3. **Then** target the specific gap. Candidates NOT yet ruled out: rooting of values across
   `IRCall` results in match arms; `IRPhi` liveness interaction; closure-env capture rooting
   (`sprout_heap_child_value` CLOSURE slot+1 offset, runtime line ~908). Note the cross-block
   liveness gap (H2) is real but separate — decide whether to land it independently *with* a
   plan for the closures it exposes.
4. **DoD reminder:** any `ir_rooting.sprout` change → `rm build/compile_driver_bin_stage1` →
   memwatch `just refresh-seed` → `just test` (DoD #5 will catch newly-exposed latent UAFs
   like closures — that is a feature, not noise). Golden IR diff was 0/43 for the fixpoint
   change (corpus doesn't exercise these patterns; the compiler's own code does).

## Key references
- `stdlib/compiler/ir_rooting.sprout`: classifiers `op_triggers_gc` (~30),
  `op_produces_simple_heap` (~92), `op_exposes_operands` (~496); liveness
  `compute_liveness` (~399, single-pass), `block_live_in`/`compute_live_out` (~378-388);
  rewrite `rewrite_block_full` (~654, seeds in-scope from `live_in ∩ heap_origin`).
- `runtime/sprout_runtime.c`: `sprout_abort_match` (~3337), `sprout_tag` (~3265, tag@0),
  `register_managed_ptr` (~524), `sprout_heap_child_value` (~892).
- Memory: `project_gc_stress_oracle`, `project_pr11_typed_codegen_campaign`.
