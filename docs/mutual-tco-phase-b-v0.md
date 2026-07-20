# Mutual tail-call optimization — Phase B (contification) — v0 design

**Status:** DESIGN PASS (2026-07-20). Implementation **DEFERRED** pending the proceed/defer
decision in §11. This document is the design so Phase B is ready to build when warranted; it
is non-normative (`docs/spec-v0.md` promises no mutual-TCO guarantee — this is an optimization).

**Relationship to Phase A.** Phase A (`docs/mutual-tco-v0.md`, LANDED) uses LLVM `musttail`
for **same-prototype** tail-call cycles (all-`i64` ABI ⇒ same arity + `i64` return). `musttail`
requires identical caller/callee prototypes (LLVM-verifier-enforced), so it structurally cannot
cover **heterogeneous-arity** cycles. Phase B is the contification path for exactly that
residual. **Phase B layers beside Phase A, it does not subsume it** — `musttail` stays the
mechanism for same-prototype cycles (a true tail call, no dispatch branch, no param-union waste).

---

## 1. Problem statement

A tail-call cycle whose members differ in arity — e.g. `f(a,b)` tail-calls `g(a,b,c)` which
tail-calls back to `f` — cannot be `musttail`'d (prototype mismatch → illegal IR). Today such a
cycle compiles as genuine native calls: each frame roots its live values across the call (a GC
trigger), so a deep cycle accumulates one frame's worth of GC roots per iteration. This is the
**same failure mode Phase A fixed for scram** (`hi_loop`↔`hi_step`, which happened to be
same-arity): at high iteration counts in a constrained root pool (a green task's 16384-slot
pool vs. the main task's 131072), the pool exhausts and the program aborts with
`GC root pool exhausted`.

## 2. Empirical findings (2026-07-20 probe)

A throwaway diagnostic (the arity-gate-only failures of Phase A's `mutual_filter_targets`:
cyclic + both-`i64` + arity-mismatch) was run over the self-hosted compiler's closure.

- **The superset is large: 186 heterogeneous-arity cyclic-`i64` edge candidates** in the
  compiler (`ast_to_ir`, `infer`, `iface_codec`, `unifier`, `parser`, `lowering`, `checker`,
  `lexer`, `types`, `module_loader`, `resolve`). This is the natural shape of a recursive-descent
  compiler: `translate_expr`↔`translate_call`↔`translate_match`, all different arities.
- **The superset is tail-AGNOSTIC** (built from the call graph, which does not record tail
  position). Most of these edges are **non-tail** — recursive descent combines sub-results
  (`TApp(apply_subst(base), apply_subst(arg))`), and a non-tail call is **not** contifiable
  (its result is needed; it genuinely needs a frame). Contification only turns *tail* calls into
  jumps.
- **Genuine fully-tail heterogeneous cycles DO exist**, confirmed by source inspection:
  `unifier.apply_subst`(arity 2) ↔ `apply_subst_lookup`(arity 3) — `apply_subst`'s `TVar` arm
  tail-calls `apply_subst_lookup`, whose `Just` arm tail-calls back to `apply_subst`. Both edges
  are in tail position. (Note `apply_subst` is *also* non-tail self-recursive on `TApp`/`TFunc`
  subterms — so it would be **partially** contified: the tail cycle edges become jumps, the
  non-tail self-calls stay ordinary calls into the merged entry.)
- **None is a known active bug.** These run on the **main task** (131072-slot pool) with depth
  bounded by AST/type-structure or substitution-chain length — the scram trigger (thousands of
  iterations in a 16384-slot green pool) does not currently occur for any of them.

**Conclusion:** Phase B has genuine targets. No *shipping* code path currently triggers it (the
compiler's cycles run on the main task with bounded depth), but the failure mode itself is
**confirmed real** — see §2a.

## 2a. Risk confirmed (2026-07-20 reproduction)

A synthetic heterogeneous-arity, fully-tail, mutual cycle — `ping/2` ↔ `pong/3`, neither
self-recursive (self-TCO N/A), arities differ (Phase A `musttail` N/A) — driven inside a green
task reproduces the exact scram failure:

```sprout
fn ping(n: Int, acc: List Int) -> Int =
  if n <= 0 then list_length(acc)
  else pong(n, acc, Cons(n, acc))       # tail -> pong/3
fn pong(n: Int, acc: List Int, acc2: List Int) -> Int =
  ping(n - 1, acc2)                       # tail -> ping/2
# driven as: task_fork(s, \_ -> ping(N, Nil) >= 0)  inside with_scope
```

Measured (green task, 16384-slot pool):

| N (iterations) | outcome |
|---|---|
| ≤ 5000 | completes |
| ≥ 5500 | `GC root pool exhausted` (process aborts) |

- **Threshold ≈ 5250 iterations** for this 2-`List` example (~3 roots/iteration) — the same order
  as scram's 4096. Heavier per-frame heap state lowers it further.
- **Not masked by `-O2`.** Exhausts identically under production-like optimization: LLVM cannot
  eliminate the opaque `sprout_gc_push_root` calls, so it does not loop-convert the accumulation
  away. The risk exists in both test (`-O0`) and production (`-O2`) builds.
- **Main task tolerates ~8×** (131072-slot pool → ~43000 iterations) — why the compiler's own
  heterogeneous cycles, all on the main task, don't hit it.

**Takeaway:** any user-written mutually-recursive algorithm with heterogeneous arities (parsers,
interpreters, tree walks — extremely common) run inside a green task past ~5000 deep crashes with
a cryptic `GC root pool exhausted`. That is the concrete hazard Phase B removes.

## 3. Goals and non-goals

**Goals.** Contify **heterogeneous-arity tail-call SCCs** so the cycle runs in O(1) native stack
and O(1) GC roots (like self-TCO and Phase A). Reuse the self-TCO loop machinery
(`IRTcoEntry`/`IRTcoLoad` slots + per-iteration rooting).

**Non-goals.**
- **Non-tail recursion.** A non-tail cycle genuinely needs a stack; the correct transform there
  is defunctionalization to an explicit work-stack, *not* contification. `bundler.sprout`'s
  `process_work` (a hand-defunctionalized `WIVisit`/`WILeave`/`WIFinalize` continuation stack)
  is exactly this — its reified continuations prove the original 4-way recursion was non-tail, so
  **Phase B does not simplify the bundler** (a claim in the Phase A doc, now corrected).
- **Same-arity cycles.** Phase A (`musttail`) owns them — tighter codegen, no dispatch.
- **Non-`i64` returns.** Out of scope (same as Phase A / self-TCO v0).

## 4. Prior-art survey (verified against primary sources)

| System | Mechanism | Condition | Source |
|---|---|---|---|
| **MLton** | Contification | A function that **always returns to the same place** becomes a continuation (local jump/loop). Dominator-tree algorithm on the call graph, proven optimal. | Fluet & Weeks, *Contification Using Dominators*, ICFP 2001 |
| **GHC** | Join points | A binder is a join point iff **all occurrences are tail calls** with a fixed **join arity**; compiled to a jump. Mutually-tail-recursive join points of **different arities** are labeled blocks that jump to each other. | Maurer, Downen, Ariola, Peyton Jones, *Compiling without Continuations*, PLDI 2017 |
| **CPS backends** (SML/NJ) | CPS | Mutual tail recursion becomes gotos after CPS conversion (no return continuation to preserve). | classic |
| **LLVM** | `musttail` | No automatic contification; a guaranteed tail call requires **matching prototypes**. | LLVM LangRef (this is Phase A's constraint) |

**Consensus:** state-of-the-art functional compilers turn *tail-called-only* functions into
local jumps; heterogeneous arities are handled by giving each function its own labeled entry
(its own arity), with jumps between them. GHC's join points are the closest match to Phase B's
target — a group of mutually-tail-recursive, differently-typed/arity'd join points compiled to
jumps within one enclosing function. Sources:
[MLton Contify (Fluet & Weeks, ICFP01)](https://www.cs.cornell.edu/people/fluet/research/contification/ICFP01/icfp01.pdf) ·
[Compiling without Continuations (PLDI17)](https://dl.acm.org/doi/pdf/10.1145/3062341.3062380).

## 5. High-level implementation overview (for approval before any edit)

**Detection.**
1. From the alloc-summary call graph, find **tail-call SCCs** with heterogeneous arity — the
   Phase A detector minus the `arity(g)==arity(f)` gate, but **gated on actual tail position**
   at the IR level (Phase A's `mutual_collect_hits` already finds tail-position calls; the
   superset in §2 is call-graph-level and must be refined to true tail edges per fn).
2. Group the tail edges into SCCs. An SCC of size 1 with only self-edges is self-TCO's job; an
   SCC with same-arity members only is Phase A's; the residual (heterogeneous, size ≥ 2) is
   Phase B's.

**Transform (per heterogeneous SCC).** Merge the SCC into one synthetic function
`__sprout_scc_<n>`:
- **Signature:** `i64 __sprout_scc_n(i64 tag, i64 p0, … i64 p_{max-1})` — one dispatch tag plus
  the **union of parameters** (max arity across the SCC). The all-`i64` ABI makes the union
  trivial: no type reconciliation, unused trailing params are poison/0 on a given tag's path.
- **Entry block:** `switch` on `tag` → `br` to the origin member's loop-head block.
- **Bodies:** each original function's body becomes a block group inside the merged function,
  reading its params from `IRTcoEntry` slots (self-TCO machinery).
- **Internal tail edge f→g:** store g's args into g's slots, `br %g_loophead`. **No runtime tag
  needed** — the target block is statically known at the call site (this is the GHC-join-point
  insight; the tag is only for *external* entry). A new terminator op, symmetric to `IRTcoBack`
  but targeting another member's loop-head, carries `(stores, sp_save, target_label)`.
- **External call sites stay unchanged:** each original `f` becomes a thin **entry trampoline**
  `f(args) = __sprout_scc_n(TAG_f, args, padding)`. This is one non-recursive frame (needn't be
  `musttail` despite the prototype mismatch), so **no interprocedural call-site rewrite** — which
  is what would otherwise fight the one-function-at-a-time streaming pipeline (the seam Phase A
  hit). Partial contification (e.g. `apply_subst`'s non-tail self-calls) also just call the
  trampoline.

**Rooting.** Identical to self-TCO: per-iteration slot liveness. Slots not live on a given tag's
path are simply not rooted that iteration. `IRTcoBack`/the new inter-member terminator are
non-GC-triggers and expose no operands (args passed by value, callee roots on entry) — the
property that removes the per-frame accumulating root.

**Pipeline integration.** Detection piggybacks the existing alloc-summary pre-pass (as Phase A's
did). The merge is a whole-SCC rewrite, so it needs the SCC's member bodies together — this is
the one place Phase B is heavier than Phase A's per-fn rewrite, and the streaming pipeline seam
must be handled (buffer the SCC's members, or run the merge in a pre-pass that emits the merged
fn + trampolines).

## 6. Syntax & semantics impact

None. Pure codegen transform; no source syntax, evaluation order, or observable semantics change
(a contified cycle computes the identical result, just without stack/root growth).

## 7. Type-system impact

None. The merged function and trampolines are synthetic IR, below the type system.

## 8. Error-message impact

None. No new diagnostics. (A future `@tailrec`-style *checked* guarantee would be a separate,
spec-affecting feature.)

## 9. Compatibility / migration notes

- **Seed-affecting:** compiler-source change (`stdlib/compiler/`) → `refresh-seed` + fixed point.
  Because it changes codegen the compiler applies to itself, expect the self-compiled seed to
  need a fixed-point iteration; verify byte-identical.
- **No source migration.** No user or stdlib code changes.
- **`bundler.sprout` is NOT simplified by Phase B** (§3 non-goals — it is non-tail).

## 10. Tests (when built)

- **RED regression:** a synthetic heterogeneous-arity tail cycle (e.g. `f(a,b)`↔`g(a,b,c)`,
  both `i64`) iterated thousands of times inside a `task_fork`'d green task; asserts completion.
  RED = `GC root pool exhausted` on master; GREEN after. (This is the scram shape generalized to
  heterogeneous arity — the missing active-bug reproduction that would justify building now.)
- **Unit:** detection (heterogeneous tail-SCC grouping; same-arity excluded → Phase A; non-tail
  excluded); transform (merged-fn signature, tag dispatch, internal `br` edges, trampolines).
- **Self-host:** byte-identical fixed point; the compiler's own heterogeneous tail cycles
  (e.g. `apply_subst`↔`apply_subst_lookup`) newly contify.
- **Gates:** full suite + `test-stress` (GC-adjacent) + smoke-shapes + example canary.

## 11. Recommendation — the proceed/defer call is the user's

The risk is **confirmed real and reproducible** (§2a), not hypothetical. The remaining judgment
is a product call: how likely is user code to hit it, weighed against the build cost.

**For deferring:**
1. **No shipping trigger today.** The scram motivator is closed by Phase A + arc (b). Every
   heterogeneous tail cycle *found in-tree* (§2) runs on the main task with bounded depth — none
   exhausts roots today.
2. **Non-trivial machinery with bootstrap risk.** SCC merge + tag dispatch + trampolines + a new
   inter-member terminator + the streaming-pipeline whole-SCC seam — materially more than Phase
   A's per-fn `musttail` rewrite, all on the bootstrap-critical codegen path.

**For building now:**
3. **Confirmed footgun for user code.** A mutually-recursive algorithm with heterogeneous arities
   (parsers, interpreters, tree walks) run in a green task past ~5000 deep crashes with a cryptic
   `GC root pool exhausted` (§2a) — in both `-O0` and `-O2`. As the concurrency/green-thread
   surface sees more use, the odds of a real user hitting this rise. Phase A already fixed the
   same failure for the same-arity slice; leaving the heterogeneous slice is an asymmetry.

**Reproduction available.** The §2a repro is the RED regression to land *first* if building — it
converts the confirmed risk into a failing test the transform must flip to GREEN. It is NOT yet
in the gating suite (it would stay RED until Phase B lands); hold it until the transform is built,
or commit it to a known-broken/xfail set.
