# Mutual tail-call optimization (`musttail`) — v0 design

**Scope:** fully-general mutual tail-call optimization, delivered in **two phases**:
- **Phase A — `musttail` (this doc).** Same-prototype cycles (all functions on an optimized
  edge share one prototype — in Sprout's all-`i64` ABI: same arity, all-`i64` params +
  return). Fixes the motivating bug and the common accumulator-loop shape. `musttail`
  *requires* matching prototypes (LLVM verifier-enforced), so it cannot cover
  heterogeneous-arity cycles — those fall to Phase B.
- **Phase B — contification (designed separately, layered on A).** Heterogeneous-arity
  cycles via signature unification (merge an SCC into one dispatch-tag loop). Completes
  "general mutual TCO" for any-arity cycles. **Design: `docs/mutual-tco-phase-b-v0.md`**
  (design pass done 2026-07-20; implementation DEFERRED — genuine targets exist in the
  compiler, e.g. `unifier.apply_subst`↔`apply_subst_lookup`, but none is an active bug, so
  it is latent hardening rather than a fix). A ships and is verified first, then B builds on it.

**Decision (2026-07-20, Kuba):** "option 2" — build both, incrementally: A now (unblocks the
Postgres demo early), B after. `musttail` chosen over a contification-only path for better
common-case codegen (measured ~3× tighter loop on trivial bodies; negligible on real ones),
incremental delivery, in-repo precedent, and reusability toward general proper-tail-calls.

**Status:** Phase A **LANDED** (2026-07-20). `IRTailCall` + tail-call SCC detection +
per-fn `musttail` rewrite, wired into the streaming pipeline; unit-tested
(`test_mutual_tco.spr`) and validated end-to-end (`test_scram_pbkdf2_green.spr` RED→GREEN;
both `hi_loop↔hi_step` edges emit `musttail`; seed refreshed to a fixed point; full gates +
`test-stress` green).

**Arc (b) LANDED (2026-07-20).** Immediately-applied lambdas (`TCall(TLambda([p…], body), [a…])`
at full arity — the desugaring of `where` / `let..in`) are now **beta-reduced inline** in
`translate_call`'s `TLambda` arm instead of lifted to a heap closure + indirect apply. This
retires the per-call closure allocation for *every* `where`/`let..in` and, in particular,
lets mutual-TCO fire through a `where` — so `stdlib/scram.sprout`'s `hi_step` reverts from the
Phase-A do-`let` workaround to idiomatic `where` and stays GREEN. Unit-tested
(`test_iife_inline.spr`: no lifted fn, no `sprout_alloc_closure_env`, incl. a nested IIFE that
references an outer IIFE-bound var); the scram-`where` RED→GREEN experiment isolates the inline
as the cause (identical source, pre-arc-(b) compiler exhausts roots, arc-(b) compiler passes);
seed re-refreshed to a fixed point.

> **Superseded 2026-08-06 — `where`/`let..in` no longer desugar to a lambda at all.**
> Both now lower to a **single-arm `MatchExpr` on the bound value**
> (`parser.wrap_where_binding` / `build_let_binding_match`), so the tail position in
> `hi_step` is structural — a match arm — rather than something arc (b)'s
> beta-reduction has to recover. `musttail` count on
> `test_scram_pbkdf2_green.spr` is unchanged (2), and the test still passes.
>
> The change was driven by typing, not TCO: an applied lambda with an *unannotated*
> parameter had its body inferred before its argument, so `check_arith` defaulted
> `where`-bound `Double`s to `Int` and even `where a = 2.5` failed to compile.
> `let … in` was already a match and never had the bug. Arc (b) remains live and
> still matters for user-written higher-order code; it is simply no longer what keeps
> `where` closure-free. The section below is retained as the historical record of why
> the lambda desugaring was a problem — read it in past tense.

### Key discovery during implementation — `where`/`let..in` allocate a closure

*(Historical: accurate for the lambda desugaring described above, which both forms
have since replaced with a single-arm match.)*

There is no `TLet` expression node: `let x = e in body` and `where` desugared to
`TCall(TLambda([x], body), [e])`, which lifts to a **heap closure + an indirect apply**.
`musttail` cannot cross an indirect call, so a recursive call sitting under a `where` (e.g.
scram's original `hi_step`) is closure-mediated and breaks the same-prototype cycle — the
detector sees `eligible = 0`. A **do-block `let` (`TDoLetStep`) compiles inline**
(closure-free), so Phase A ships with scram's `hi_step where` rewritten as a do-`let`, which
makes the `hi_loop↔hi_step` cycle direct and lets the `musttail` rewrite fire.

This exposed a broader footgun (tracked as "arc (b)", **now landed** — see the Status block
above): **every `let..in`/`where` allocated a closure per call** — a real perf cost that also
defeated TCO wherever a recursive call sat under one. The fix routes immediately-applied
lambdas (`TCall(TLambda)`) through the do-`let` inline-bind path (bind param → arg SSA in
`captures`, translate the body in place); it is value-preserving (the IIFE result IS the
body's value) and, because an IIFE is applied exactly once, never duplicates the body. scram's
`hi_step` do-`let` workaround has reverted to `where`. This was separate from (and
higher-priority than) the heterogeneous-arity contification (Phase B).
**Motivating bug:** SCRAM PBKDF2 (`stdlib/scram.sprout` `hi_loop`↔`hi_step`) exhausts a
green task's GC-root pool on the first request of the Postgres-backed HTTP demo.
**Related:** `docs/archive/typed-tco-implementation-2026-06-27.md` (self-TCO, the v1 this generalizes).

---

## 1. Problem statement

The typed codegen (`--use-ir-codegen`, the only backend since `codegen.sprout` was retired
in `5f29b9d`) optimizes **self**-tail-recursion into an alloca-slot loop (`IRTcoBack`), but
does **not** optimize **mutual** tail recursion. A mutually-tail-recursive cycle
(`A` tail-calls `B` tail-calls `A`) compiles to ordinary `IRCall`s, so:

1. **Native stack growth** — one frame per iteration; deep cycles overflow the 8 MB stack.
2. **GC-root-pool exhaustion** — worse, and the reason this surfaced now. `ir_rooting`
   roots each heap **argument** across its (non-tail) `IRCall` via operand exposure. Because
   the call does not return until the whole cycle unwinds, those roots **accumulate one set
   per frame**. PBKDF2 at the SCRAM-default 4096 iterations holds ~9 live roots × 4096 ≈
   36 864 simultaneous roots. The main task's pool (131 072 slots) absorbs it; a green
   task's pool (16 384) exhausts partway (measured: dies at exactly 16 384 = 4096 × 4).

This makes every Postgres connection from a green thread (i.e. every HTTP request handler)
crash, while the same code on the main thread merely wastes stack/roots invisibly. More
broadly it is a latent correctness footgun: any mutually-tail-recursive helper in stdlib or
user code silently uses O(depth) stack and roots.

### Empirical confirmation

- Instrumented `sprout_gc_push_root` (`SPROUT_ROOT_TRACE`): monotonic root-count staircase
  to 16 384 on the green pool, no unwind — accumulation, not bounded depth.
- `musttail` viability on target (arm64-apple, `-O0`): a 200 M-deep `even`↔`odd` cycle with
  `musttail` completes with **zero** stack growth; the identical cycle with plain `call`
  SIGSEGVs. `musttail` is honored irrespective of optimization level (its contract).

---

## 2. Goals and non-goals

**Goals**
- Optimize mutual tail calls within a **tail-call cycle** so they neither grow the native
  stack nor accumulate GC roots — O(1) in both, matching self-TCO.
- Reuse the existing typed-codegen architecture and its rooting-correctness argument.
- Fix the motivating bug (SCRAM in a green thread) as the acceptance test.

**Non-goals (v0)**
- **Non-`i64` returns.** Same scope limit as self-TCO. `musttail` requires identical
  caller/callee return types; Sprout returns are uniformly `i64` *except* CPR width-3 (sret)
  returns and any future struct returns. Functions whose return is not plain `i64` are
  excluded (they stay ordinary calls — correct, just unoptimized). `Result`/heap ADTs are
  `i64`, so SCRAM is in scope.
- **Heterogeneous-prototype cycles** (→ **Phase B**, not v0/Phase A). `musttail` requires the
  caller and callee prototypes to match exactly (return type, arity, param types, calling
  convention, ABI attrs). Edges whose two functions differ in prototype (e.g. a cycle mixing
  `i64(i64)` and `i64(i64,i64)`) are **not** `musttail`-eligible and stay ordinary calls in
  Phase A. Phase B (contification / signature unification) covers them.
- **Cross-module cycles, higher-order/indirect tail calls.** Only statically-resolved
  direct calls to named functions in the same compilation unit are eligible. (OCaml has the
  same practical limitation across module boundaries.)
- **Proper tail calls in general** (every tail call, recursive or not, as in Scheme). We
  optimize only calls that close a cycle, because that is where unbounded growth lives and
  where the cost/benefit is unambiguous.
- **A user-facing annotation** (`@tailrec`-style opt-in or diagnostic). Deferred; see §7.

---

## 3. Prior-art survey (primary sources)

| Language | Mutual tail calls | Mechanism / notes |
|---|---|---|
| **Scheme (R7RS)** | **Guaranteed** | Spec *mandates* proper tail recursion for all tail positions; unbounded active tail calls in O(1) space; "turns mutual recursion into a co-routine." [r5rs](https://people.csail.mit.edu/jaffer/r5rs/Proper-tail-recursion.html), [Clinger 1998](https://www.cs.tufts.edu/~nr/cs257/archive/will-clinger/proper-tail-space.pdf) |
| **OCaml** | **Optimized** | Native/bytecode optimize *all* tail calls, "direct calls to arbitrary other functions, not just tail self-recursion"; mutual recursion supported. Limited across module boundaries / higher-order / indirect. [manual](https://ocaml.org/manual/5.1/tail_mod_cons.html) |
| **Scala (`@tailrec`)** | **Not optimized** | `@tailrec` optimizes **only direct self-recursion**; mutual recursion errors ("recursive call not in tail position"). JVM lacks tail-call support. [scala/bug#9647](https://github.com/scala/bug/issues/9647) |
| **Rust** | **Opt-in, experimental** | `become` reserved keyword for *guaranteed* explicit tail calls (compile error if unhonorable); no implicit TCO otherwise. Not yet stable. [become](https://doc.rust-lang.org/std/keyword.become.html), [RFC 3407](https://github.com/rust-lang/rfcs/pull/3407) |
| **LLVM (our backend)** | **`musttail`** | *"Guarantees that the call will not cause unbounded stack growth if it is part of a recursive cycle."* Requires: call immediately precedes `ret` returning its value; caller/callee prototypes + calling conventions + ABI attrs match. [LangRef](https://llvm.org/docs/LangRef.html) |

**Reading.** The mature functional tier (Scheme, OCaml) guarantees mutual TCO via a
jump/`musttail`-style mechanism; the JVM tier (Scala) is limited to self-recursion; Rust
makes it explicit-and-checked. Sprout currently sits at the Scala tier (self-only). Because
our backend is LLVM, we can reach the Scheme/OCaml tier for the bounded i64-cycle subset at
low cost using `musttail` — the same mechanism the retired direct backend already shipped
(commit `5f29b9d^:codegen.sprout`, `build_mutual_tco_fns` + `musttail call i64`).

**Prior-art gap we must close.** The retired `build_mutual_tco_fns` *seeded from
self-recursive functions* and grew by "tail-calls-into-the-set." A pure 2-cycle with **no**
self-recursive member (exactly `hi_loop`↔`hi_step`) has an empty seed → it would have been
**missed**. v0 therefore uses proper tail-call **SCC (cycle) detection**, which strictly
subsumes the old heuristic.

---

## 4. High-level implementation overview

Two parts, both in the typed IR pipeline; no change to parsing, typing, or the runtime.

### 4a. Detection — tail-call SCC over the compilation unit

At module scope (where all function bodies are available, before per-function lowering):

1. Build a **tail-call graph**: an edge `f → g` iff `f`'s body contains a call to `g` in
   **tail position** (if/match arms, `do` last step) at **full arity**. Reuse the tail-position
   logic already in the self-TCO detector (`has_tail_call_to_set` port).
2. Compute **strongly-connected components**. A tail edge `f → g` is **musttail-eligible**
   iff: (a) `f` and `g` are in the same SCC that contains a cycle (size > 1, or a self-loop),
   (b) both return plain `i64` (`body_ret_is_i64`), **and (c) `f` and `g` have identical
   prototypes** — in the all-`i64` ABI: same arity and all-`i64` params (`params_match`,
   ported from the retired backend's `params_match_slots`). This per-edge prototype gate is
   mandatory: `musttail` on a prototype-mismatched edge is illegal IR (verifier-rejected).
3. Produce the set of eligible **edges** (not just functions). Thread it into
   `translate_user_fn`/`tco_rewrite`. Ineligible edges (mismatched prototype, non-`i64`, or
   acyclic) stay ordinary `IRCall`s — correct, merely unoptimized. Partial optimization of a
   cycle (some edges musttail, some not) is legal; only fully-musttail cycles achieve O(1).

Self-TCO is the degenerate case (a 1-node SCC with a self-edge) and keeps its existing
alloca-slot path; `musttail` is used only for **inter-function** tail edges inside an SCC.

### 4b. Emission — `IRTailCall`

New IR terminator op (symmetric to `IRTcoBack`):

```
| IRTailCall String String (List (String, String)) String
    # (result_tmp, callee_ll_name, args=[(lltype, ir_value)], sp_save)
    # Lowers to:  [ call void @llvm.stackrestore(ptr <sp>)   -- only if sp_save != "" ]
    #             <result_tmp> = musttail call i64 @<callee>(<args>)
    #             ret i64 <result_tmp>
    # Terminator. NON-GC-trigger, does NOT expose operands (no root emitted) — this is
    # what removes the per-frame accumulating root that exhausts the pool.
```

In `tco_rewrite` (which already walks tail calls per function): a tail call on a
**musttail-eligible edge** (§4a — callee is a different SCC member, full arity, i64 return,
**identical prototype**) → rewrite the `IRCall`+`IRRet` (or `IRCall`+phi-to-`IRRet`) into an
`IRTailCall`, reusing the same phi-tracing/tail-safety machinery the self case uses. `sp_save` is set only when the enclosing function is *also*
self-TCO'd (has a `tco_loop` with a live `stacksave`), so its per-iteration root allocas are
freed before the jump — mirroring the retired backend.

### 4c. Rooting classification (`ir_rooting`) — parallels `IRTcoBack`

- `op_triggers_gc`: **false**.
- `op_produces_simple_heap`: **Nothing** (the result flows straight to `ret`; nothing outlives it).
- `op_exposes_operands`: **false** — args are **not** rooted across the tail call. Safe: args
  are i64 handles passed by value; the callee roots its params on entry; no GC runs between
  the `musttail` jump and callee entry. (Same argument that makes `IRTcoBack` stores safe.)
- `op_successors`: `[]` (control leaves the function).
- `op_uses`: the arg values (keep them live to the call) ++ `sp_save` if present.
- `op_def`: Nothing. Terminator.

Single-pass liveness stays valid: an `IRTailCall` is `call; ret`, **not** a CFG back-edge, so
it introduces no loop-carried SSA value (unlike `IRTcoBack`, which the self-TCO design
already justified). It integrates *more* simply than the self case.

**Load-bearing rooting invariant (must be asserted, not assumed):** no GC-triggering op may
sit between the final materialization/coercion of the tail call's arguments and the
`musttail` jump — otherwise an unrooted arg (we deliberately root none across `IRTailCall`)
could be swept. In the all-`i64` ABI arg coercion is a no-op (no allocation), so the invariant
holds by construction; the implementation must confirm coercion emits no allocating op and
place `IRTailCall` immediately after the last arg SSA def. This is GC-timing-dependent, so
`test-stress` may not catch a violation deterministically — verify it structurally in the IR,
not only by testing.

### 4d. Lowering (`ir_lowering`) + printing (`sprout_ir`)

Add the `IRTailCall` arm to `lower_op` (emit the 2–3 lines above; ensure `@llvm.stacksave`/
`@llvm.stackrestore` declares are present — already added for self-TCO), and to
`print_op`. The exhaustive `match … | IR…` guard turns any missed site into a compile error.

---

## 5. Syntax and semantics impact

**None at the source level.** No new syntax, no new keywords, no evaluation-order change. A
mutually-tail-recursive program that previously overflowed/exhausted now runs in O(1)
stack/roots and returns the same value. Observable behavior changes only from "crash" to
"correct result." Programs already within stack/root limits are unaffected (same result;
lower resource use).

## 6. Type-system impact

**None.** Detection reads already-inferred types (to gate on `i64` return) but adds no
inference, no new type, no new constraint. Eligibility is a codegen property, not a typing one.

## 7. Error-message impact

v0 is silent (an optimization, not a checked contract). Two follow-ups are noted, not adopted:
- A **`@tailrec`-style diagnostic** ("this call is in a tail-recursive cycle but not
  optimized because it returns a non-`i64` type / crosses a module / is indirect"), so the
  optimization is predictable — Rust's `become` and Scala's `@tailrec` both argue for making
  the guarantee explicit. Deferred to a v1 with the annotation design.
- The retired backend emitted an advisory warning for cross-function tail calls it *couldn't*
  optimize; we may resurrect that once the eligible set is trustworthy.

## 8. Compatibility / migration notes

- **Bootstrap:** compiler-source change (`stdlib/compiler/`), so seed-affecting. Requires
  `just refresh-seed` and, because it changes codegen the compiler applies to *itself*, the
  **2-step bootstrap** to reach a fixed point. The compiler contains its own mutual-tail
  cycles (e.g. the parser's expression/statement descent), which will newly optimize — the
  self-compiled seed must be verified byte-identical.
- **No source migration.** No user or stdlib code must change. Post-arc-(b),
  `stdlib/scram.sprout` is idiomatic mutual recursion with a `where` — it simply stops leaking
  (no hand-contification). The `bundler.sprout` iterative-DFS workaround (`visit_by_name` /
  `visit_module`, hand-rewritten because codegen couldn't TCO mutual recursion, ~line 325)
  could be revisited now that mutual-TCO exists — but those functions differ in arity, so a
  natural mutual-recursive rewrite would need **Phase B** (heterogeneous-arity contification),
  not Phase A/arc (b). Left as-is for now.
- **GC-safety linter (BACKLOG 5/6):** `IRTailCall` gets declared rooting semantics
  (non-trigger, non-exposing), so it is as analyzable as `IRTcoBack`.

## 9. Tests added / updated

- **RED regression (motivating bug):** `tests/stdlib/test_scram_pbkdf2_green.spr` — build a
  `ScramServerFirst` with `i=4096` (`parse_server_first`), call `scram.client_proof` inside a
  `task_spawn`'d green task; assert `Ok`. Fails ("GC root pool exhausted") on master; passes
  after. Doubles as the **first** native SCRAM coverage (currently zero).
- **Unit (pure IR transform):** extend `test_tco_rewrite.spr` — a 2-function i64 cycle
  rewrites both inter-function tail calls to `IRTailCall`; a non-`i64` cycle is left as
  `IRCall`; a 3-cycle; a function both self- and mutually-recursive gets `IRTcoBack` for the
  self edge and `IRTailCall` (with `sp_save`) for the mutual edge.
- **Lowering/rooting classification:** extend `test_ir_lowering_exports.spr` and the
  `ir_rooting` op-classification tests for the six exhaustive matches.
- **End-to-end acceptance:** the rebuilt `sprout-postgres` `http_pg_server` serves `/users`
  across many requests without exhaustion.
- **GC stress:** `just test-stress` (mandatory — GC-rooting-adjacent codegen false-greens on
  the default suite).
- **Arc (b) — IIFE inline (pure IR transform):** `tests/stdlib/compiler/test_iife_inline.spr`
  — an immediately-applied lambda (incl. a nested IIFE referencing an outer IIFE-bound var)
  translates with an empty `lifted` list and no `sprout_alloc_closure_env`. RED on the pre-arc
  compiler (lifts a closure), GREEN after. Plus the scram-`where` RED→GREEN experiment above.

## 10. Spec / docs status

This document is **non-normative** (a design draft). `docs/spec-v0.md` states no guarantee
about mutual-TCO, and v0 adds none — it is an optimization, not a promised contract, so the
normative spec is unchanged. If a future v1 adds a checked `@tailrec`-style guarantee, *that*
becomes a spec change. `docs/archive/typed-tco-implementation-2026-06-27.md` is updated to point here
for the mutual case; `BACKLOG.md` tracks the deferred annotation/diagnostic and the
`bundler.sprout` de-contification cleanup.

---

## Staged plan (verify at each step; mirrors the self-TCO staging)

1. **IR op + lowering + rooting + print** (no producer). Add `IRTailCall`; lower it; classify
   in all six `ir_rooting` matches; print it. Unit test the lowering shape; `opt
   --passes=verify` a hand-built sample clean. Refresh seed.
2. **Detection + producer.** Port `has_tail_call_to_set`; add tail-call SCC detection; thread
   the eligible set into `tco_rewrite`; emit `IRTailCall` at eligible inter-function tail
   edges. Unit-test `tco_rewrite` (fast loop, no seed churn). Then the RED green-task test
   goes GREEN.
3. **Fixed-point seed via 2-step bootstrap.** `verify-bootstrap-fixed-point` — the mutual-TCO'd
   compiler self-compiles byte-identically.
4. **Acceptance + full gates.** Rebuild `http_pg_server` (serves); `just test`,
   `just test-stress`, smoke-shapes, bundle-smoke, compile-examples-stage1, example canary.
   Revert/gate the temporary `SPROUT_ROOT_TRACE` instrumentation in `sprout_runtime.c`.
