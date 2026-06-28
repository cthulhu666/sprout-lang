# P11 follow-up — typed-codegen over-rooting (precision pass) — handoff 2026-06-28

**Goal:** close the remaining typed-vs-direct memory/time gap on the self-hosted
compiler by making the typed GC-rooting pass as *precise* as direct codegen.
This is a **P2 optimization, not a correctness fix** — typed is already
behaviorally correct (`ir_runtime_parity` 98/98 OK, 0 TYPED-*). Do not regress
correctness chasing precision.

## Where we are (after the flip landed, branch `fix/typed-codegen-tco`)

The flip is done: `--emit-ir` routes to typed codegen, seed bootstraps via typed,
all DoD gates green. But typed codegen is heavier than direct on the compiler:

| | typed (`--emit-ir`) | direct (`--use-direct-codegen`) | ratio |
|---|---|---|---|
| whole-compiler RSS | **1.4 GB** | 305 MB | 4.6× |
| whole-compiler time | 35 s | 39 s | ~par |
| roots in `compiler_intrinsic_sigs`* | **11 202** | 4 062 | **2.76×** |

\* measured *before* the literal split; the split (8.8 GB → 875 MB for codegen.sprout)
removed that one function's O(N²) spike, but the per-function over-rooting RATIO is
general — it just no longer concentrates in one mega-function.

Reproduce the root-count gap on any rooting-heavy function:
```sh
ROOT="$PWD/stdlib"; BIN=build/compile_driver_bin_stage1
$BIN --emit-ir            "$ROOT" stdlib/compiler/codegen.sprout > /tmp/typed.ll
$BIN --use-direct-codegen "$ROOT" stdlib/compiler/codegen.sprout > /tmp/direct.ll
# total roots, whole module:
grep -c 'call i64 @sprout_gc_push' /tmp/typed.ll   # typed
grep -c 'call i64 @sprout_gc_push' /tmp/direct.ll  # direct
# whole-compiler peak RSS:
/usr/bin/time -l $BIN --emit-ir "$ROOT" stdlib/compiler/compile_driver.sprout >/dev/null
```

## Root-cause hypothesis (the thing to confirm first)

**Direct keeps a persistent root frame; typed re-roots per trigger.**

- **Direct** (`codegen.sprout`) roots *inline while emitting*, threading a `rooted: Int`
  count through `emit_*` (e.g. `push_temp_root` :861, `push_roots_list` :907,
  `pop_temp_roots` :899). A value is pushed **once** and stays rooted across the
  following operations until popped — a running root frame. The emitter knows
  exactly what is live in scope, so it pushes the minimum.
- **Typed** (`ir_rooting.sprout`) is a *post-translation dataflow pass*: for EACH
  GC-trigger op, `maybe_wrap` (the `op_triggers_gc` path) brackets that single op
  with push-all-live-across + `IRUnroot(n)` pop-all (`roots_across` :614 returns the
  set, `maybe_wrap_trigger`/`emit_roots_loop` emit one `IRRoot` per value). So K
  values live across N sequential triggers get pushed/popped **K×N** times instead
  of once — that is the 2.76× AND the O(N²) we saw in the giant literal (each list
  element re-rooted across every later allocation).

So the over-rooting is **structural** (per-trigger bracketing), not a stray bug.
Confirm by eyeballing two adjacent triggers in `/tmp/typed.ll` for a function with
several live heap values: you'll see the same value pushed+popped around each.

## The fix (in rough order of payoff / risk)

### A. Persistent root frame across consecutive triggers (the big one)
Instead of push-all/pop-all around *each* trigger, maintain a running rooted set as
the rewrite walks a block: push a value when it ENTERS scope-and-is-live-onward,
pop when it dies. A value live across triggers t1..tk is rooted **once**, not k times.
This matches direct's `rooted`-count model and removes the O(N²). It is the largest
change to `ir_rooting.rewrite_ops` / `update_scope` (:665) / the `in_scope_ord`
threading — design it carefully (see guardrails). The per-op `live_after` is already
precomputed (`block_live_afters` :464), so "does v die after op i" is O(1) to answer.

### B. Tighten `op_exposes_operands` (:543) — cheaper, independent
Today it roots the operands of **ALL** `IRCall`s (comment :«We can't cheaply know
which callees alloc-before-store, so root the operands of ALL calls»). Direct only
roots call operands that are actually heap AND live. Two sub-levers:
  1. Don't expose operands that are non-heap scalars (the `IRCall` already carries an
     `IRType kind`; operand kinds are knowable via `heap_origin`).
  2. If a known-non-allocating intrinsic set can be identified, skip operand exposure
     for those calls (mirror what direct does — check `codegen.sprout`'s call paths).
Purely additive today (safe over-rooting), so trimming it is lower-risk than A but
must be validated the same way.

### C. Liveness precision audit
Compare, on one small Ref/ctor-heavy function, exactly which values typed roots vs
direct, and classify each *extra* typed root as: (a) per-trigger re-root → fixed by A;
(b) operand over-exposure → fixed by B; (c) genuinely-dead value kept live by
`in_scope_ord` / `compute_heap_origin` (:504) imprecision → a liveness fix. Do this
diff FIRST — it tells you how much of the 2.76× is A vs B vs C before you build A.

## Guardrails (read before touching ir_rooting)

- **Under-rooting = use-after-free, not a verify error.** `opt --passes=verify` will
  NOT catch a dropped root. The oracle is `just test-stress` (SPROUT_GC_STRESS=1,
  collect on every alloc) — see `[[project_gc_stress_oracle]]`. Run it after every
  change. A passing default `just test` is NOT sufficient (timing luck hides UAF).
- **Keep `ir_runtime_parity` 98/98 OK** (`bash scripts/ir_runtime_parity.sh`) — it is
  the behavioral direct-vs-typed differential; any drop is a real miscompile.
- The current over-rooting is **SAFE** (over-retention, not UAF): an `IRRoot` of a
  non-pointer i64 is scanned via `find_managed_ptr` and skipped. So you are trading
  safety-margin for memory — measure, don't guess.
- Output need not stay byte-identical to today's typed IR (fewer roots = different IR),
  but it must stay **parity-equal to direct's runtime behavior** and **verify-clean**.
- `ir_rooting.sprout` is compiler-source: refresh the seed (`just refresh-seed`,
  delete `build/compile_driver_bin_stage1` first) BEFORE `just test`; TDD-guard
  requires touching a `tests/` file before editing it.

## Method / DoD

1. Do the **C diff** first (classify the extra roots) → decide A vs B vs both.
2. Add a unit test in `tests/stdlib/test_ir_rooting.spr` that locks the new (lower)
   root count for a representative multi-trigger block (the existing T1–T13 are the
   model; T13 is the long-block one).
3. Implement; `just test-stress` after every iteration.
4. Measure: root count on codegen.sprout + whole-compiler RSS (target: well under
   1 GB; direct is 305 MB).
5. Full DoD: refresh-seed → `just test`, `just test-stress`, `scripts/ir_runtime_parity.sh`
   (98/98, 0 TYPED-*), `just flip-readiness`, smoke-shapes, bundle-smoke,
   compile-examples-stage1, run-example-canary.

## Key code map

- Typed rooting: `stdlib/compiler/ir_rooting.sprout`
  — `op_triggers_gc` :30, `op_produces_simple_heap` :96, `block_live_afters` :464,
    `live_across` :526, `op_exposes_operands` :543, `roots_across` :614,
    `update_scope` :665, the per-op rewrite loop (`rewrite_ops` / `maybe_wrap`).
- Direct rooting (precision reference): `stdlib/compiler/codegen.sprout`
  — `push_temp_root` :861, `push_temp_root_typed` :895, `pop_temp_roots` :899,
    `push_roots_list` :907; rooting is threaded as a `rooted: Int` count through the
    `emit_*` family.
- Background: `docs/p11-flip-handoff-2026-06-27.md` (the flip + memory saga),
  `[[project_gc_stress_oracle]]`, `[[project_pr11_typed_codegen_campaign]]`.

## Open questions to resolve early

- How much of the 4.6× RSS gap is over-rooting vs other typed overhead? (The C diff +
  an RSS measurement with B alone applied will tell you.) If A+B don't close most of
  the gap, profile where the rest goes before deeper work.
- Does A interact with the TCO ops (`IRTcoEntry`/`IRTcoLoad`/`IRTcoBack`)? The TCO
  back-edge relies on the per-trigger bracketing self-balancing each iteration
  (see the TCO handoff's "rooting is liveness-based" section) — a persistent frame
  must still pop everything pushed since `tco_loop` before the back-edge, or roots
  leak across loop iterations. This is the subtle correctness risk in A.
