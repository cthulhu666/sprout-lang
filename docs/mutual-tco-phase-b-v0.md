# Mutual tail-call optimization — Phase B (signature unification) — v0 design

**Status:** IN PROGRESS (build started 2026-07-20, branch `feat/mutual-tco-phase-b`). Kuba chose
"prove the risk first" (§2a, confirmed) then "build Phase B now". Non-normative
(`docs/spec-v0.md` promises no mutual-TCO guarantee — this is an optimization).

**Mechanism decision (2026-07-20): SIGNATURE UNIFICATION, not contification.** A prototype
confirmed the simpler path: **pad each heterogeneous SCC member to the max arity so they share
one LLVM prototype `i64(i64,…,i64)`, then Phase A's existing `musttail` fires on them** — with
the original arities kept as thin trampolines. This reuses Phase A's proven, already-in-the-seed
detection + rewrite; no new IR op, no dispatch tag, no whole-function merge, no whole-SCC pipeline
seam. The uniform-`i64` ABI is the enabler: "same prototype" reduces to "same arity", which
padding gives trivially (Sprout-level param types may differ; LLVM sees `i64` throughout). The
tag-dispatch/contification design (§5-ALT) is retained as the heavier alternative that was
considered and rejected. See §5 for the mechanism, §5a for the integration seam.

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

**Goals.** Make **heterogeneous-arity tail-call SCCs** run in O(1) native stack and O(1) GC roots
(like self-TCO and Phase A), via signature unification (§5) that reuses Phase A's `musttail`
detection + rewrite. No new IR op, no new rooting rules.

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

## 5. Mechanism — signature unification (chosen)

A **typed-program-level pre-pass** `phase_b_unify(decls) -> decls`, run in
`compile_program_streaming` **before** `build_ret_i64`/`build_alloc_summary` (so the generated
decls are visible to Phase A's existing detection — see §5a), does:

**Detection.**
1. Build the call graph from the typed `decls`, plus **tail-position** info: a call is a tail
   call iff it is the value of the function body / an `if` branch / a `match` arm / the last
   `do` step / a `let`-body (a standard typed-AST tail analysis — the call-graph superset in §2
   is tail-AGNOSTIC and must NOT be used directly, or it generates dead `_unified` variants).
2. Find **heterogeneous-arity tail-call SCCs**: cyclic over *tail* edges, members differ in
   arity, all `i64`-returning. Same-arity SCCs are Phase A's; single self-edges are self-TCO's.

**Transform (per heterogeneous SCC, max arity `M`).**
- For each member `f/k`, generate `f_unified/M` — `f`'s body with `M−k` extra **ignored** `Int`
  params appended, and every **internal tail call to an SCC member** `g` retargeted to
  `g_unified` with its args **padded to `M`** (pad value `0`; Sprout-level param types may differ,
  LLVM sees `i64`). Non-tail / external calls are left alone.
- Turn each original `f/k` into a **trampoline**: `f(a…) = f_unified(a…, 0…)`. External callers
  are untouched (no interprocedural rewrite).
- Now `{f_unified}` is a **same-prototype** (`i64(i64×M)`) tail-call SCC → **Phase A's existing
  `mutual_build_eligible` + `mutual_tco_rewrite_fn` emit `musttail`** with zero new machinery.
  (Empirically confirmed on a hand-written unified pair: both cycle edges emit
  `musttail call i64 @…`, and the green-task repro of §2a completes.)

**Rooting.** Unchanged — Phase A's `IRTailCall` is already a non-GC-trigger that exposes no
operands; the `_unified` bodies root exactly as any function does. No new rooting rules.

**No new IR op, no dispatch tag, no merged function.** The whole transform is per-function decl
generation informed by the SCC analysis — it fits the streaming pipeline.

## 5a. Integration seam (the load-bearing new question)

The prototype proved the *back* half (Phase A `musttail`s same-prototype decls). The *front* half
— getting generated decls in front of detection — is the real work:

- **`phase_b_unify` must run at the typed-program level, before `build_alloc_summary`.** That
  pre-pass (`finalize_summaries`) builds `mutual_build_eligible` over the `decls` list; if the
  `_unified`/trampoline decls are generated later (e.g. during IR translation, like
  `synthesize_eta_wrapper`), the pre-pass never sees them and no `musttail` fires. Injection point:
  `decls = phase_b_unify(decls)` at the top of `compile_program_streaming`.

**Status: BUILT + working (2026-07-20).** `phase_b_unify` runs at the top of
`compile_program_streaming` (via `phase_b_program`), before `build_alloc_summary`. Confirmed
end-to-end: the §2a green-task repro (`ping/2`↔`pong/3`) auto-generates `ping$u`/`pong$u`, Phase A
`musttail`s both edges, and it completes at N=40000 (was aborting at N=5500). Also verified on
`if`-position, `match`-arm-position, and boxed-heap (`List`) return shapes.

**v0 gate: SCALAR returns only (`Int`/`Bool`/`Char`/`Float`).** `phase_b_unify` runs
*post-lowering*, so a generated `$u` variant is **not** registered in the CPR/worker machinery
that lowering set up for the originals. When a member returns an ADT that routes to a Tier-2 CPR
worker (a `match f(args)` over a width-2-ADT-returning `f`, e.g. `Result`/`Maybe`), emitting the
worker for `f$u` crashes: `result type absent from adt_index (empty repack)` — a
bootstrap-breaker. So v0 gates detection to **scalar returns**, which never enter the worker path.
Consequences:
- Phase B is a **no-op on the compiler's own code** (all its fully-tail heterogeneous cycles
  return ADTs — `types.Type`, `Result`, `Effect`), so the self-compiled seed is byte-identical
  apart from the new `pb_*` definitions (fixed point holds immediately — safest bootstrap outcome).
- `unifier.apply_subst/2` ↔ `apply_subst_lookup/3` (returns `types.Type`) is therefore **not**
  contified in v0.
- **Phase B.1 (the real generality):** run `phase_b_unify` on the *pre-lowering* typed AST (tail
  calls intact, `$u` decls then lowered normally with proper worker registration). That lifts the
  scalar gate to all `i64`-returning cycles (ADTs included) and is the documented follow-up.
- **Tail-self-recursive AND mutually-tail-recursive member.** `mutual_tco_rewrite_fn` skips any
  function carrying an `IRTcoEntry` (self-TCO'd), so such a member's *mutual* edge stays a plain
  call — unfixed in v0 (no miscompile; the self edge still self-TCO's).
- **Mixed call sites.** Only internal *tail-cycle* calls are retargeted to `$u`; non-tail and
  external calls keep hitting the trampoline (scoped to the detected tail edges).
- **Non-`i64` returns.** Out of scope, as Phase A.

## 5-ALT. Contification / tag-dispatch (considered, rejected as heavier)

The original design merged each SCC into one `__sprout_scc_<n>(i64 tag, i64 p0…p_{M-1})` with a
`switch` on the tag to member loop-head blocks, internal edges as `br` (a new inter-member
terminator op), and trampolines. It is the GHC-join-point / MLton-contification shape and would be
needed if the ABI were not uniform-`i64` (real signature reconciliation) or to merge non-tail
members. Rejected for v0: it adds a new IR op + dispatch + a whole-SCC rewrite on the
bootstrap-critical path, where signature unification reuses Phase A entirely. Kept here as the
fallback if a future need (non-uniform ABI, tuple/`Bool` returns) outgrows unification.

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
