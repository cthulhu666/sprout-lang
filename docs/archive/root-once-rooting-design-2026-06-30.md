# Root-once GC rooting (stack-coalesced) — design

Status: draft for approval. Date: 2026-06-30. Owner: typed-codegen / `ir_rooting`.
Supersedes the BACKLOG:57 prescription ("rooting-pass reachability analysis"),
which the investigation below shows cannot apply.

## 1. Problem

The typed-codegen rooting pass (`stdlib/compiler/ir_rooting.sprout`) brackets
**every** GC-trigger op with push-all-live-across + pop-all. A heap value live
across K triggers is therefore pushed/popped **K times** — cost **O(uses)**.

Two costs fall out of this single strategy (spike `/tmp/spike/FINDINGS.md`,
2026-06-30):

1. **Compile-time blowup (dominant).** A right-associated list/ctor literal
   `[e1,…,eN]` evaluates left-to-right but consumes `e1` last, so all N elements
   are simultaneously live across the spine. The pass emits **O(N²)** `IRRoot`s.
   Measured on a pure `List String` literal: peak *compiler* RSS 83 MB → 747 MB →
   **2943 MB** for N = 40 → 80 → 120 (grows ~N³ — the self-hosted compiler's
   immutable Set/List churn over O(N²) ops). Real tables (`compiler_intrinsic_sigs`,
   45 `FnSig` entries) hit GBs; today they are hand-split via `append_sigs`.

2. **Runtime tax (the 2.76×).** Hot recursive loops execute O(uses) push/pop
   **per iteration** (`bench/results-2026-06-29.md`). Separate bill, same cause.

The same N=120 literal costs **3 GB to compile** but **4.2 MB / 0.37 s to run** —
~700×. So the literal pain is compile-time; the 2.76× is the runtime face of the
same root cause.

### Why BACKLOG:57's prescribed mechanism does not apply

BACKLOG:57 proposed a rooting-pass *reachability* analysis ("a value stored into a
cell is reachable via that cell's root; don't re-root it"). Auditing the real IR
(`/tmp/listlit2.ll`): **there are zero reachability-redundant roots.** The pass
already drops stored-then-dead values via liveness; a redundant root needs a value
stored-into-a-cell *and still independently live* — which never happens for
literals (each element is consumed exactly once). The cost is genuine
simultaneous liveness, which no reachability analysis can shrink. The lever is the
**rooting strategy**, not reachability.

## 2. Goals / non-goals

Goals:
- Replace per-trigger re-rooting (O(uses)) with **root-once-while-live** so a value
  live across a contiguous run of triggers is pushed once, popped when dead.
- Fix the compile-time literal blowup **fully** (it is intra-block — see §4) and
  reduce the runtime per-iteration tax where triggers cluster in a block.
- Let the `append_sigs` 5-way split (`codegen.sprout`) and the chunking of
  `builtin_entries`/`analysis_entries` be deleted once the win is confirmed.

Non-goals (this change):
- No runtime ABI change, no new builtins, no GC-scan change.
- No user-facing `linear` types (Model C M4). This is the structural rooting win
  *without* the language-design bet; M4 remains the eventual North Star.
- No change to inter-block rooting: the root stack stays **empty at every block
  boundary** (§4), so cross-block correctness is byte-for-byte today's.

Observable behavior is **unchanged** — this is an optimization emitting fewer root
ops around the identical set of triggers. Runtime results, output, and GC safety
are identical; only the count of `IRRoot`/`IRUnroot` ops drops.

## 3. No language-surface impact

- Syntax / parser: none.
- Type system / inference: none.
- Error messages / diagnostics: none.
- Evaluation order / semantics: **none** — unlike the rejected lowering reorder,
  this does not touch `ast_to_ir`; element evaluation order is untouched, so spec
  §337 (constructors evaluate fields left-to-right) is preserved unconditionally.
- Spec (`docs/spec-v0.md`): no change (rooting is an internal codegen detail).

## 4. Approach — intra-block stack coalescing

The pass is already per-block (`rewrite_block_full`), seeding `in_scope_ord` at
block entry from `live_in ∩ heap_origin` and emitting balanced push/pop per
trigger so the **root stack is empty at every block boundary**. We keep that
invariant and coalesce *within* a block.

Replace per-trigger push-all/pop-all with a persistent root stack `R` threaded
through `rewrite_ops`:

```
R := []                          # rooted values, in push order (bottom→top)
ops := block ops; term := last op (the terminator), body := all but last
for each op in body, with live_after(op) precomputed:
  if op_triggers_gc(op):
    need := roots_across(op)     # live-after heap values ∪ exposed operands (today's set)
    # PUSH phase — only the delta, in definition order for determinism
    for v in need, v not in R:  emit IRRoot(v); push v onto R
    emit op                      # the trigger
    # POP phase — drain dead values from the TOP only
    k := count popped while  top(R) ∉ live_after(op)
    if k > 0: emit IRUnroot(k); pop k from R
  else:
    emit op
# block end: pop all remaining R *before* the terminator (restores empty-at-boundary)
if R non-empty: emit IRUnroot(|R|)
emit term                        # br / condbr / ret / tcoback / abort — always last
```

`IRRoot`/`IRUnroot` are unchanged: `IRUnroot(n)` already pops `n` from the top
(`sprout_gc_pop_roots(n)`), so a count-pop of the dead top-run needs no new op.

**The block-end pop MUST precede the terminator** — nothing executes after a
terminator, so emitting `IRUnroot` after it produces invalid IR (`opt
--passes=verify` rejects it). This only fires for `br`/`condbr`/`tcoback`: at a
block ending in `ret` the live-out set is empty so `R` is already drained, and
abort/`unreachable` blocks likewise leave `R` empty. The live-out values popped
here are re-seeded (re-rooted) in the successor from its `live_in`, with no
allocation across the branch edge — exactly today's behavior.

### Worked example — the list literal (single block, confirmed)

`build()` for `[e1,…,eN]` is one basic block (no `br`; terminates at `ret`).
Elements satisfy `def(e1)<…<def(eN)` and `lastuse(e1)>…>lastuse(eN)` (consumed
outermost-last) → intervals are **perfectly nested**. The push phase pushes
`e1..eN` once (at the first trigger, `make0`/first `make2`); each subsequent
`make2` pushes only its new cell + any unrooted operand and pops the just-consumed
element from the top. Total: **O(N) pushes/pops** vs today's O(N²).

## 5. Correctness — the UAF surface

Operational invariant:

> **Pop a value only from the top of `R`, and only when it is not in
> `live_after` of the current op.**

**Primary proof — superset of the proven-safe scheme.** At every trigger `T`, the
new rooted set ⊇ the old rooted set. After the push phase `R ⊇ need(T)` (we push
`need\R`; `need∩R` is already present), plus possibly some buried dead values, so
`R ⊇ need(T) =` exactly today's rooted set. The old per-trigger scheme is already
proven safe (it roots `need(T)` and `need(T)` covers everything live-across `T`);
rooting a **superset** is the documented-safe direction — extra entries are either
valid pointers retained slightly longer or non-pointer `i64`s skipped by
`find_managed_ptr`. Therefore the new scheme is safe **given** the old one is. The
*only* way to break safety is an implementation bug that makes `R ⊉ need(T)` — i.e.
popping too eagerly, or the block-end-placement bug (§4). Both are caught by the
per-trigger `new-roots ⊇ old-roots` differential assertion (§6), which is sharper
than `test-stress` because it fails deterministically rather than on GC timing.

**Load-bearing lemma — a popped value never reappears in a later `need`.** This is
why "push only when `not in R`" never needs a re-push. In SSA straight-line code,
once `v ∉ live_after(op_i)` it has no use at any `op_j` (j > i) — and it cannot be
an operand of a later op either, since an operand is a use and uses keep a value
live. So after `v` leaves `R` it is never needed again in the block. (Across
blocks this is moot: `R` is emptied at the boundary and successors re-seed.)

- *Safe degradation, no explicit fallback.* If lifetimes interleave (a dead value
  buried under a live one), the dead value stays rooted until the live one above
  it dies — over-rooting, safe by the superset argument. Under-rooting cannot
  occur because we never pop a non-top or live value.
- *Inter-block unchanged.* Stack empty at boundaries (§4) ⇒ today's cross-block
  reasoning is preserved verbatim. TCO back-edges create loops but `IRTco*` ops do
  not allocate, and the pop-before-`tcoback` + re-seed-in-header matches today.

The residual risk is strictly an *implementation* bug (mis-threading `R`, an
off-by-one in the dead-top count, a mis-placed block-end pop, or a `live_after`
read that under-approximates liveness), all caught by §6.

## 6. Tests

- **Per-trigger superset assertion (the sharpest check).** Run old and new rooting
  differentially and assert, at every trigger, `new-roots(T) ⊇ old-roots(T)`. This
  fails deterministically on the one failure mode that matters (`R ⊉ need`), unlike
  `test-stress` which depends on GC timing. Cheapest to wire as a debug check in the
  pass or a one-off harness over the smoke shapes.
- **TDD unit (`tests/stdlib/test_ir_rooting.spr`, T1–T14).** Counts **derived from
  first principles** (validated by reproducing the three documented *current*
  counts T13=16, T3=4, T5=2), rewritten red-first:
  - **T13: 16 → 5** push, pops collapse to one `pop_roots(i64 5)` (v0..v4 pushed
    once each, drained at `%r`). The headline O(N²)→O(N) unit proof.
  - **T3: 4 → 3** push; pops become a single `pop_roots(i64 3)` at `%r` (the
    `pop(2)`+`pop(1)`+`pop(1)` assertions must be replaced — coalescing defers pops
    to end-of-liveness, a larger batch; safe over-retention).
  - **T5: 2 → 1** push, one `pop_roots(i64 1)` (`%s` rooted once across the call
    *and* the make).
  - T1,T2,T4,T6–T12,T14: **unchanged** (single-trigger or non-overlapping
    lifetimes) — they pin that coalescing does not perturb the simple cases.
  Never read counts off the new implementation, else a shared mental-model bug
  passes both. Add: (a) a perfectly-nested 4-element heap-literal block asserting
  O(N) not O(N²) pushes; (b) an interleaved-lifetime block asserting the safe
  over-retention shape; (c) a **two-block function with a heap value rooted by a
  trigger then live across a `br`** (T4 does NOT cover this — its live-out arm is
  never rooted), asserting valid IR (block-end pop before the terminator) and
  re-rooting in the successor — guards the §4 placement bug directly; (d) a TCO
  case (`tokenize_from` exercises the back-edge).
- **`just test-stress` / `SPROUT_GC_STRESS=1`** — the UAF oracle; mandatory, since
  this is the "don't re-root what's protected" risk class. Plus `just test`.
- **Compile-memory acceptance gate (the headline proof).** Re-run the spike: the
  N=120 pure-heap literal must drop from ~3 GB peak compiler RSS to roughly *linear*
  RSS — that, not merely a lower op count, is what shows the compile-time blowup is
  actually gone. Fold a bounded version into a guard (push count O(N), e.g. < 3·N
  for the literal block).
- **Differential.** `scripts/ir_runtime_parity.sh` (typed vs direct) must stay at
  parity; `just compile-examples-stage1`; example canary.
- **Bootstrap.** `ir_rooting.sprout` is compiler source → `just refresh-seed`
  before `just test` (per the compiler-source DoD ordering), reseed committed.

## 7. Alternative considered — indexed root frame (deferred)

A per-function root array (`set_root(slot,ptr)` / `clear_root(slot)`, GC scans the
frame) gives full O(defs) rooting for **arbitrary** (non-LIFO, cross-block)
lifetimes — the complete fix for the runtime 2.76×. Rejected for *this* step: it is
a runtime ABI change (new builtins + GC-scan change + per-function prologue),
requires explicit approval, and flips runtime+seed in lockstep. Revisit only if
intra-block coalescing leaves a material runtime tax after measurement; the
compile-time blowup (the dominant cost) is fully addressed without it.

## 7a. Results (measured after implementation, reseeded binary)

Spike re-run (pure `List String` literal, fn `build()`):

| N   | pushes before → after | peak compiler RSS before → after |
|-----|-----------------------|----------------------------------|
| 40  | 1894 → 730            | 82.9 MB → 24.1 MB                |
| 80  | 4394 → 810            | 747.2 MB → 32.0 MB               |
| 120 | 8494 → **890**        | **2942.6 MB → 40.3 MB**          |

N=120: 9.5× fewer root ops, **73× less compile memory**. `build()`-specific pushes
drop from N²/2 to ~2N (O(N)); peak RSS goes from ~N³ to flat-linear. The
compile-time blowup is eliminated. Unit: T13 16→5, T3 4→3, T5 2→1; T15/T16 added.
refresh-seed converged at iteration 3 (peak 1205 MB, 158 s).

## 8. Open questions for approval

1. OK to proceed with intra-block coalescing (§4) as the first step, deferring the
   indexed-frame full solution (§7) pending a post-change runtime re-measurement?
2. Delete `append_sigs` + the `builtin_entries`/`analysis_entries` chunking in the
   **same** PR once green, or a follow-up PR? **Recommend follow-up PR** — keeps the
   rooting change bisectable against `test-stress`; the deletion is pure cleanup
   once the win is confirmed.
3. Keep the cheap interim lint (BACKLOG:57: flag large literals in
   `stdlib/compiler/`) regardless, or drop it once coalescing lands?
