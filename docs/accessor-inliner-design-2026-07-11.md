# Accessor-inliner design (proposal, needs approval)

Status: proposal / design-for-approval (do not implement before sign-off)
Date: 2026-07-11
Scope: `stdlib/compiler/` (a new typed-AST → typed-AST pass), an optional `@inline` marker.
Prerequisite for the broad reach of Phase D B1
(`docs/phase-d-numeric-fastpath-design-2026-07-11.md`).

Follows the AGENTS.md **Design Change Process**. §4 is the approval gate.

---

## 1. Problem statement

Sprout compiles **once** with a uniform-i64 ABI and has **no monomorphization or inliner**
(verified 2026-07-11). Consequently, optimizations that need the *concrete element type at a call
site* — Phase D **B1** (inline `Vector T` access) and the existing Tier-1 **CPR peephole**
(`vector_get` → unboxed) — cannot fire through Sprout's small polymorphic accessor wrappers:

```
mutvec_get(v: MutVec a, i: Int) -> Maybe a = vector_get(mutvec_raw(v), i)   # a is erased here
```

A concretely-typed caller — `examples/astar.sprout` holds `g_score: MutVec Int`, the recognizer
holds `MutVec Double` — passes a concrete type *at the call site*, but the polymorphism inside
`mutvec_get`'s **definition** erases it before the inner `vector_get`/`vector_get_direct` is
reached. So idiomatic indexed access (`mutvec_get`, `mutmatrix_get`, `vec_get_or`, `mutvec_set`)
gets neither CPR unboxing nor B1 — only hand-written kernels that call the builtins directly do.

**Goal:** a minimal pass that expands these trivial accessor wrappers at their call sites *before*
type erasure, so the concrete type flows into the wrapped builtin — unlocking B1 and CPR for
ordinary code, and delivering a standalone win (fewer call frames per access) on its own.

## 2. Goals and non-goals

**Goals.**
1. Expand a **small, explicit set** of trivial accessor wrappers — single-expression,
   non-recursive — at each call site, at the **typed-AST level** (types still present).
2. Preserve the uniform-i64 ABI: the original function still exists for polymorphic-caller and
   non-inlined calls; only *this* call site is rewritten. No representation change, no code
   duplication per type.
3. Deliver value **before B1**: collapsing `mutvec_get`/`mutvec_raw` frames is a win by itself and
   feeds the existing `vector_get` CPR peephole (which cannot fire through the wrapper today).

**Non-goals (hard fence — do not let scope creep past this).**
- **No general size-heuristic inliner.** This is macro-expansion of a *named allow-list*, not a
  cost-model optimizer with fixpoint/recursion/capture machinery.
- **No monomorphization / per-type specialization** (the Rust/MLton model). It is philosophically
  against Sprout's compile-once uniform ABI and is a separate, larger decision. Deferred, explicitly.
- **No recursive-function inlining**, no inlining of multi-statement bodies (first cut).
- No `stdlib.mutable` API change; no runtime/builtin change.

## 3. Prior-art survey (verified against primary sources)

Two poles for "make polymorphic-function calls cheap," and a uniform-representation language must
pick one:

| language | mechanism | keeps one compiled copy? | primary source |
|---|---|---|---|
| **GHC (Haskell)** | `{-# INLINE f #-}` — per-function directive that "declare[s] a function's cost to be very low" so GHC inlines it at call sites; `{-# SPECIALIZE #-}` makes a concrete copy of an overloaded fn | yes (selective) | GHC users guide, *Pragmas* |
| **Rust** | monomorphization: "turning generic code into specific code by filling in the concrete types … generates code for the concrete types the generic code is called with" — a copy per type | no (a copy per type) | *The Rust Book* §10.1, "Performance of Code Using Generics" |

Sprout's uniform-i64 ABI is the compile-once, one-copy design — so it belongs on the **GHC pole**:
selective, per-function inlining that leaves the shared copy intact for everyone else. Rust-style
monomorphization is the opposite trade (no runtime cost, but code-per-type and no compile-once) and
is out of scope. This proposal is the `INLINE`-pragma analogue, restricted to trivial accessors.

## 4. High-level implementation overview (APPROVAL GATE)

A new typed-AST pass between inference and `ast_to_ir` (types must still be present):

1. **Trigger — one decision to make (see below):** either a hardcoded **allow-list** of accessor
   names, or an **`@inline` marker** on the definitions. Only functions that are (a) marked/listed,
   (b) a single expression body, (c) non-recursive are eligible.
2. **Expansion:** rewrite a call `f(a0, a1, …)` to `f`'s body with parameters substituted by the
   argument expressions (capture-avoiding β-reduction on the typed AST). The substituted body keeps
   its types, so a caller's `MutVec Int` flows into the inlined `vector_get(mutvec_raw(v), i)` as
   `Vector Int`.
3. **Argument-duplication guard:** if the body uses a parameter more than once and the argument is
   non-trivial (not a variable/literal), bind it to a fresh `let` first. Accessor bodies typically
   use each param once (`match v with …`, `vector_get(raw, i)`), so this is rarely triggered — but
   name it, don't assume.
4. **Effect/purity:** inlining is semantics-preserving for these pure/`!{IO}` single-call bodies;
   the pass must not reorder or duplicate effects (the duplication guard covers the reorder case).

**Trigger sub-decision (for approval):**
- **(a) Allow-list** — smallest change, no parser/syntax change; but hardcodes stdlib names into the
  compiler (mild coupling). Good for the first cut.
- **(b) `@inline` marker** — principled and user-controllable (the GHC `INLINE` analogue), matches
  Sprout's explicit-over-implicit value; costs a small parser + AST + decl-field change.
- **Recommendation:** ship **(a)** as the first cut to prove the pass on the five accessors, then
  add **(b)** as the durable interface. (a)→(b) is additive.

**Initial allow-list:** `mutvec_get`, `mutvec_raw`, `mutvec_set`, `mutmatrix_get`, `vec_get_or`
(+ `mutmatrix_raw`-shaped helpers if present). All are single-expression, non-recursive.

**De-risked by verification (2026-07-11):**
- The duplicate-`entry:` trivial-accessor codegen bug (`project_trivial_accessor_codegen_bug`,
  BACKLOG:628) is **legacy-`codegen.sprout`-only** — confirmed it does *not* reproduce on the active
  `--emit-ir` path (`opt --passes=verify` clean, one `entry:` per define). Inlining *removes* the
  standalone accessor, so it further sidesteps the bug.
- No reusable general-function specialization infra exists (the BACKLOG:238 "specialized wrappers"
  machinery is typeclass-dictionary-specific); this pass is new, but the `is_monomorphic_*` type
  predicates (`ast_to_ir.sprout:562+`) are reusable.

## 5. Syntax and semantics impact
Option (a): none. Option (b): a `@inline` marker on `fn` declarations (attribute syntax) — purely
advisory, no semantic change (an inlined call computes exactly what the call computed). Evaluation
order preserved by the duplication guard.

## 6. Type-system impact
None. The pass runs on the **typed** AST and substitutes typed sub-trees; the caller's concrete
types are what make it useful. No inference change.

## 7. Error-message impact
None. Inlining is invisible to diagnostics (it runs after type checking). A recursion/multi-statement
body that is marked `@inline` but ineligible is simply *not* inlined (a lint could warn later).

## 8. Compatibility / migration
Internal optimization; no observable behavior change. **Bootstrap:** if the pass fires inside
`stdlib/compiler/` (any compiler code that calls a listed accessor), the compiler's own emitted IR
changes → the seed must be refreshed and `verify-bootstrap-fixed-point` re-run. The allow-list is
stdlib-mutable accessors, which the compiler may or may not use — check before landing; scope the
first cut to keep the seed change reviewable (or start with accessors the compiler does not call).

## 9. Tests added / updated
- **IR-shape:** a concretely-typed caller of `mutvec_get`/`mutmatrix_get` lowers with **no**
  `call @…mutvec_get` and a direct `vector_get`/`vector_get_direct` (or, post-B1, an inline load).
- **Standalone-win witness (pre-B1):** `examples/astar.sprout` — after the inliner alone, the
  `mutvec_get` wrapper frames are gone; measure against `bench/results-2026-07-11.md` (~305 µs/run).
  This is the concrete proof the pass works, independent of B1.
- **CPR-peephole reach:** confirm the `vector_get` CPR unboxing now fires at an inlined idiomatic
  site (it can't through the wrapper today).
- **Semantics/guards:** a non-variable argument used twice in a body is `let`-bound once (no double
  evaluation); a recursive or multi-statement `@inline` fn is left un-inlined.
- **No-regression:** full suite + `SPROUT_GC_STRESS=1` (inlined bodies must root identically).
- **Bootstrap:** `verify-bootstrap-fixed-point` after refreshing the seed.

## 10. Spec / docs status
Experimental compiler optimization, not a normative language change — no `docs/spec-v0.md` edit
(unless (b) adds `@inline`, which would get a short spec note as an advisory attribute). Document the
pass in `docs/compiler-internals.md`. Update the Phase D doc to note B1's broad reach is now unlocked.

---

## Recommendation

Approve a **minimal allow-list accessor-inliner** (option 4a) over the five trivial `stdlib.mutable`
/ `Vec` accessors, as a typed-AST pass. It (1) delivers a standalone win on idiomatic code (A\*,
recognizer scalar reads) *before* B1, (2) unlocks the existing CPR peephole and future B1 for
wrapper-mediated access — the "general Sprout performance" goal the recognizer was a proxy for — and
(3) is de-risked (the one known codegen fragility is confirmed legacy-only). Hold `@inline` syntax
(4b) as the immediate follow-up, and Rust-style monomorphization firmly out of scope.
