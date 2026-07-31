# GC Rooting — Systemic Fix Plan

> **Status (2026-06-02): superseded.** The project has chosen the Model C path
> in [gc-rooting-model-c-plan-2026-06-02.md](../gc-rooting-model-c-plan-2026-06-02.md);
> the linter + Option-2/3 framing below is no longer the intended execution
> path. Milestones 1.1–1.5 (scalar IR) and 2.1–2.2 (heap ops + dataflow rooting)
> have already landed (see `git log` for branch `m2-pr-*`); M2 PR 2.3 (ctors) is
> in progress. Retained as the original Problem A / Problem B framing.

**Date:** 2026-06-01
**Context:** The `emit_binary` GC rooting hole (TokenizeError bootstrap bug) was fixed in commit `3729039`.
Stress-mode verification (`SPROUT_GC_THRESHOLD=1`) confirmed the fix is real (all 30 suites pass).
This doc captures the remaining structural work for a future PR.

---

## Two Distinct Problems

### Problem A — Unknown Latent Holes (existing codegen)

The `emit_binary` hole was found through a specific symptom (bootstrap crash). The current shadow-stack
model places the burden on each codegen site to root values across GC-triggering calls — and there is no
automated check that every site does so correctly.

**Fixed holes:**
- `emit_binary` heap-pointer left operand unrooted across `emit_expr(right)` — fixed in commit `3729039`.
- `emit_binary` ptr-dispatch branch: both `lv` and `rv` were unrooted when `coerce_value(lv, ll_ptr(), em)`
  triggered GC (via `string_concat_many` inside `emit_template`). Fixed by per-branch `pop_temp_roots`
  placement and adding `push_temp_root_typed(rv, right_ty, em)` before the coerce calls.

**Known open instance:** REPL `string.slice`-via-closure crash. A rooting hole somewhere in the
closure / eta-expansion call path causes a dangling pointer under GC pressure. Not fixed ad-hoc because
the exact site is not confirmed; the IR linter (see below) should surface it.

**How to fix:** Build an IR linter (post-process `.ll`) that:
1. Walks all SSA defs in every function.
2. Classifies each def as *heap-typed* if its source is `str_*`, `sprout_make*`, `sprout_alloc_*`, or
   an `inttoptr` from one of those.
3. At every call instruction that can trigger GC (calls to any function that calls
   `sprout_gc_maybe_collect_threshold`), asserts that every heap-typed value live at that call is
   currently held in a shadow-stack slot (i.e., passed to `sprout_gc_push_i64_root` without a matching
   `pop` since the push).
4. Reports all violations as errors with function name + IR line.

**Implementation estimate:** ~400–600 lines of Sprout. Runs as a post-compilation pass in CI.
Target: `just ir-lint` (lints the LLVM IR produced from `stdlib/compiler/`).

**Triage sequence after linter ships:**
1. Run linter on the current stage-1 IR.
2. Fix every reported hole (each is a 3-line push/pop pair around the second `emit_expr` call, same
   pattern as the `emit_binary` fix).
3. Re-run stress test to confirm clean.

### Problem B — Future Codegen Will Reintroduce the Bug

The shadow-stack model is manual: every codegen author must remember to root heap values across
GC-triggering calls. As codegen grows (new expression forms, optimizations, rewrites), new holes will be
introduced. This is a **structural** problem that the linter catches but does not prevent.

**Two candidate options (decision deferred):**

| | Option 2 — Codegen-automatic scoped rooting | Option 3 — Conservative GC |
|---|---|---|
| **Mechanism** | `emit_expr` returns a `ScopedValue` that auto-pushes on construction and auto-pops when its scope closes; no manual push/pop at call sites | Replace shadow stack with stack scanning; GC walks the C call stack and treats every pointer-aligned word as a potential root |
| **Correctness** | Guaranteed by type (can't forget if the API prevents it) | Guaranteed structurally (no shadow stack at all) |
| **Runtime cost** | Slightly higher push/pop count (roots scalars too unless `push_temp_root_typed` gating is preserved); still O(depth) | Stack scan is slower per-collection; cannot distinguish pointers from integers — may keep dead objects alive |
| **Codegen complexity** | Requires refactoring `emit_expr` return type; all call sites change | Requires changes to `sprout_gc_*` internals only; codegen untouched |
| **Sprout fit** | Fits the current typed IR; `push_temp_root_typed` type-gating carries over naturally | Does not use type information; simpler runtime but loses type-aware optimizations |
| **Risk** | Medium — large codegen refactor; must be done atomically | Low runtime-side risk, but conservative GC may resurface false-retention bugs |

**Recommended long-term answer:** Option 2. Automatic scoped rooting is the right abstraction: it makes
the correct behaviour the only behaviour, and it preserves type-aware scalar filtering. However it
requires a significant codegen refactor and should not block the linter (Problem A) work.

---

## Recommended Sequence for the Future PR

1. **Build IR linter** (`just ir-lint`) — Problem A detector.
2. **Run linter on current stage-1 IR** — triage all reported holes.
3. **Fix every reported hole** — confirm with stress test.
4. **Decide Option 2 vs 3** — based on the scope revealed by triage.
5. **Implement the chosen option** — if Option 2, refactor `emit_expr` return type and remove all manual
   push/pop sites; if Option 3, replace shadow-stack with stack scanner in `runtime/sprout_runtime.c`.
6. **Remove IR linter** (or keep as belt-and-suspenders CI gate) once Option 2 lands.

---

## Shadow-Stack Model Reference

For context, the current rooting API (in `codegen.sprout`):

```
push_temp_root(val, em)        -- roots any value unconditionally
push_temp_root_typed(val, ty, em)  -- skips root if ty is Int/Bool/Char (non-heap scalar)
pop_temp_roots(n, em)          -- pops n roots
```

GC-triggering call sites (any call to these builtins may collect):

```
str_slice, str_concat, str_from_int, str_from_char
sprout_alloc_closure_env
sprout_make1, sprout_make2, sprout_make3
```

The invariant: every heap-typed SSA value that is live at one of the above call sites must have been
pushed to the shadow stack before the call and not yet popped.

---

## Open Questions

- Should the IR linter be written in Sprout (eating our own cooking, runs slow on stage-0) or as a
  standalone Python/shell script (faster to build, orthogonal to the self-hosting bootstrap)?
- Long-term: once generational GC lands (`docs/archive/generational-gc-v1-draft.md`), does the nursery
  promotion model interact with the shadow-stack invariant? (Likely yes — young objects promoted to old
  generation must remain rooted across minor collections.)
