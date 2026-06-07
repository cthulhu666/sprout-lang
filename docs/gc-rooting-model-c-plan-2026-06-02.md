# GC Rooting — Model C Plan (typed Sprout-IR + linear types)

**Date:** 2026-06-02
**Status:** Proposal — to be executed incrementally; supersedes the linter+defer path discussed in [`gc-rooting-fix-plan-2026-06-01.md`](gc-rooting-fix-plan-2026-06-01.md) once Milestone 2 lands.
**Companion:** [`gc-rooting-fix-plan-2026-06-01.md`](gc-rooting-fix-plan-2026-06-01.md) — original Problem A / Problem B framing and stress-mode verification context.

---

## What this plan commits to

**Model C:** introduce a typed Sprout-IR layer between AST and LLVM, *and* extend the source language with linear types, *and* make the IR's heap-value types linear. The result: GC rooting correctness becomes a structural theorem about IR well-formedness rather than a discipline codegen authors must follow.

The end state replaces today's manual `push_temp_root_typed` / `pop_temp_roots` pairs scattered across `codegen.sprout` with three guarantees:

1. The IR type system makes "live heap value across a GC trigger" *unrepresentable*.
2. The lowering pass from Sprout-IR to LLVM IR has no rooting decisions to make — it mechanically translates ops.
3. Linear types become a user-facing language feature, reusable for file handles, channels, and other paired-resource patterns.

This plan deliberately **skips** the linter + `defer` mid-term solution.

---

## Guiding principles

1. **Two codegen paths coexist** until parity is proven. A `--use-ir-codegen` flag gates the new path; the existing direct AST→LLVM codegen remains the default until Milestone 3.
2. **Linearity is opt-in at the source level** before it's structural for Sprout-IR. This lets type-system work land without forcing a flip.
3. **Every PR is reversible.** Each milestone leaves the tree in a coherent state. If a milestone reveals a fatal design flaw, the previous milestone is the rollback target.
4. **Stress-mode (`SPROUT_GC_THRESHOLD=1`) is the safety net** between milestones. The moment IR codegen handles heap, run it under stress in CI.

---

## Milestone 1 — IR scaffolding + scalar programs

Establishes the IR module, lowering pass, and a working end-to-end path for non-heap programs.

| # | PR | What | Risk |
|---|---|---|---|
| 1.1 | IR module skeleton | `stdlib/compiler/sprout_ir.sprout` with `IRProgram`, `IRFunction`, `IRBlock`, empty `IROp`, printer. `--use-ir-codegen` flag added but fails fast on any input. | Nil |
| 1.2 | Trivial programs | `IRConst Int`, `IRRet`. AST→IR for `IntLit` and `fn id(x) = x`. First `tests/stdlib/test_ir_codegen_basic.spr`. | Low |
| 1.3 | Scalar arithmetic | `IRIAdd`, `IRISub`, `IRIMul`, `IRICmp`. AST→IR for `+`, `-`, `*`, comparison. | Low |
| 1.4 | Control flow | `IRBr`, `IRCondBr`, `IRPhi`. AST→IR for `if-then-else`. | Medium — phi placement is the first non-trivial design |
| 1.5 | Calls + recursion | `IRCall` for scalar-returning functions. Tests: `fib`, `factorial`. | Medium |

**End-of-milestone state:** `--use-ir-codegen` compiles all scalar-only programs to working LLVM. Heap programs fail fast with a clear error.

**Acceptance:** all scalar examples in `examples/` compile under both flags and produce identical output.

---

## Milestone 2 — Heap support + dataflow-based rooting (Model A's IR)

Add heap ops with rooting handled by dataflow analysis in the AST→IR translator. **No linear types yet** — IR architecture first, linearity later.

| # | PR | What | Risk |
|---|---|---|---|
| 2.1 | Heap value type + simple allocators | `IRHeap τ` value category. `IRStrSlice`, `IRStrConcat`, `IRStrFromInt`. Lowering emits underlying runtime calls. | Medium |
| 2.2 | Rooting ops, dataflow analysis | `IRRoot`, `IRUnroot` ops. AST→IR includes a dataflow pass: for each heap value, determine if it's live across a GC-trigger op and insert root/unroot. Tests run under `SPROUT_GC_THRESHOLD=1`. | **High — rooting correctness is established here** |
| 2.3 | Constructor & ADT allocation | `IRMakeCtor`. AST→IR for ADT construction. Dataflow from 2.2 extends to ctor args. | Medium |
| 2.4 | Closures | `IRAllocEnv`, `IRMkClosure`, `IRApplyClosure`. AST→IR for lambdas and applications. Verify under stress mode. | **High — closures interact with capture and TCO** |
| 2.5 | Pattern matching ✅ | IR-level lowering of `match` to conditional branches with phi at the join. Linear-value-in-arms checking deferred to Milestone 4. **Landed** (Wildcard/Var/Int/Bool/Constructor patterns; design doc `pr-2.5-pattern-matching-plan.md`). | High — match lowering is intrinsically complex |

**End-of-milestone state:** `--use-ir-codegen` compiles everything the old codegen handles. Stress-mode is clean.

**Acceptance:** `just test` passes under both flags; stage-1 self-compile works under `SPROUT_GC_THRESHOLD=1` with `--use-ir-codegen`.

---

## Milestone 3 — Flip and retire old codegen

| # | PR | What | Risk |
|---|---|---|---|
| 3.1 | Flip default | `--use-ir-codegen` becomes default. Old path accessible via `--use-direct-codegen`. CI runs both for one release cycle. | Medium — flag flip |
| 3.2 | Remove old codegen | After one release of stable IR-codegen-default, delete `codegen.sprout`'s direct LLVM emission paths. Keep helpers (templates, fresh-tmp, value model) reused by lowering. | Low (if 3.1 went well) |

**End-of-milestone state:** single codegen path through Sprout-IR. Significant simplification in `codegen.sprout`.

**Acceptance:** zero behavior change visible to users; codebase has only one codegen path.

---

## Milestone 4 — Linear types in Sprout (user-facing first)

Add linear types as a *user-facing* language feature, validated on small synthetic programs before they touch Sprout-IR. Keeps the language-feature work isolated from IR work and parallelizable with Milestones 2–3.

| # | PR | What | Risk |
|---|---|---|---|
| 4.1 | Syntax + AST | Parser accepts `linear τ` (notation TBD). AST and `types.Type` carry a `linear: Bool`. Pretty-printer handles it. Type checker accepts but does not enforce. | Low |
| 4.2 | Use-count tracking | Type checker tracks per-binding use count for linear-typed bindings. Error if `count != 1` in a function body. Synthetic test programs exercise the new error paths. | **High — touches inference; expect iteration** |
| 4.3 | Match-arm convergence | All match arms must consume the same set of linear bindings. Branch-mismatch error reporting. | High |
| 4.4 | Function-call propagation | Linear arguments flow through to consumers. Higher-order linearity deferred unless trivial. | Medium |

**End-of-milestone state:** Sprout users can write `linear τ` and the type checker enforces consume-exactly-once. Not yet used internally.

**Acceptance:** new test suite for linear types passes; existing tests unchanged.

> **Note on standalone value:** Milestone 4 ships a user-facing language feature even if Milestone 5 never lands. Linear types are useful for files, channels, and capability tokens independent of IR-internal use.

---

## Milestone 5 — Apply linearity to Sprout-IR (the Model C switch)

The payoff. Sprout-IR's `Heap τ` and `Rooted τ` become linear; the AST→IR translator is type-checked against the linear discipline; the dataflow analysis becomes redundant and is removed.

| # | PR | What | Risk |
|---|---|---|---|
| 5.1 | Declare IR types linear | `Heap τ` and `Rooted τ` annotated linear. Type checker now validates the translator. Dataflow analysis still runs in parallel as a sanity check; emit a warning on disagreement. | **High — expect a wave of type errors to fix** |
| 5.2 | Translator hardening | Fix every translator path that doesn't type-check under linearity. Each fix is mechanical: insert `root`/`unroot` where the checker demands it. Investigate every translator/dataflow disagreement. | Medium — well-scoped but many small fixes |
| 5.3 | Remove dataflow analysis | After N CI runs with zero disagreements, delete the dataflow rooting pass. Linearity is the sole source of truth. | Low |
| 5.4 | Documentation | Update `AGENTS.md`; retire `gc-rooting-fix-plan-2026-06-01.md` and this doc into a single `sprout-ir-model.md`. | Nil |

**End-of-milestone state:** GC rooting correctness is a *theorem* about IR well-formedness, enforced by the type checker. The bug class is structurally impossible.

**Acceptance:** stress-mode passes, dataflow analysis is gone, documentation captures the model.

---

## Critical dependency graph

```
M1 (scalars) ────┐
                 ├─→ M2 (heap + dataflow rooting) ──┐
                 │                                  │
                 │                                  ├─→ M3 (flip, retire old) ─┐
                 │                                  │                          │
                 │                                  │                          ├─→ M5 (apply linearity to IR)
                 │                                  │                          │
                 └─→ M4 (linear types in Sprout) ───┴──────────────────────────┘
```

**Parallelism opportunities:** M4 can be developed alongside M2/M3 since they touch different modules (type checker vs. codegen). The hard sequencing is M2 before M3 (parity before flip) and M3+M4 both before M5.

**With one work stream:** ~3 months sequential. **With M4 parallelized:** ~2 months wall-clock.

---

## What's intentionally NOT in this plan

- **Linter + `defer`.** Per the project decision to pursue Model C directly, these pragmatic mid-term solutions are skipped.
- **Optimizations in Sprout-IR.** Peephole, inlining, constant-folding are tempting because the typed IR invites them. Out of scope until post-M5.
- **Higher-order linearity.** PR 4.4 punts on linear function arguments past trivial cases. Full higher-order linearity is known-hard (Linear Haskell shipped it incomplete). First-order linearity is enough for Sprout-IR's needs.
- **Moving GC / generational GC interaction.** Touched by [`generational-gc-v1-draft.md`](generational-gc-v1-draft.md). The Model C plan assumes non-moving GC throughout; generational write barriers introduce new GC-trigger sites that the dataflow pass (M2) and the linear types (M5) must learn about. Sequence: generational GC lands after Model C, not before.

---

## Honest assessment

**Biggest risk:** Milestone 4 (linear-type inference). Hindley-Milner doesn't infer linearity; you're adding a new dimension to the inference algorithm. Expect this milestone to take longer than estimated, possibly significantly. De-risk early by prototyping PR 4.2 (use-count tracking on annotated bindings) in a throwaway branch.

**Biggest payoff:** after Milestone 5, GC rooting is no longer a class of bugs that can be introduced by codegen authors. It becomes structurally impossible. The compiler grows a new architectural pattern (linearly-typed internal IR) that is reusable for future cross-cutting concerns.

**Smallest viable subset:** if budget gets cut, the natural stopping point is **end of Milestone 3** — typed IR with dataflow rooting. That alone eliminates manual push/pop discipline at the codegen-author level and sets up Model A's end state. Milestones 4–5 then become a future upgrade path rather than a commitment.

**What this plan does NOT prove:** that linearly-typed IR is *easier* to author than the dataflow-rooting IR. M2 establishes a working baseline (Model A's IR); M5 upgrades it. If the M5 upgrade reveals that linearity adds more friction than it removes, the plan can stop after M3+M4 with both the typed IR *and* user-facing linear types but without the IR-internal coupling.

---

## Glossary

- **Shadow stack** — the existing GC root mechanism. Codegen emits `sprout_gc_push_i64_root` / `sprout_gc_pop_roots` calls; the runtime walks pushed slots during collection.
- **Heap-typed value** — an SSA value whose source is `str_*`, `sprout_make*`, `sprout_alloc_*`, or an `inttoptr` from one of those. Lives on the GC heap.
- **GC trigger** — any runtime function that may call `sprout_gc_maybe_collect_threshold`. See [`gc-rooting-fix-plan-2026-06-01.md`](gc-rooting-fix-plan-2026-06-01.md) for the full list.
- **`Heap τ` (IR type)** — pointer to heap-allocated value of type `τ`, NOT registered on the shadow stack. Linear.
- **`Rooted τ` (IR type)** — pointer to heap-allocated value of type `τ`, currently registered on the shadow stack. Linear.
- **Dataflow rooting** — automatic insertion of `IRRoot`/`IRUnroot` ops by analyzing IR liveness across GC-trigger ops. M2's approach.
- **Linearly-typed rooting** — the same correctness property enforced via linear types instead of dataflow analysis. M5's approach.
