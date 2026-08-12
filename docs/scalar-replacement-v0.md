# Scalar replacement / tuple-CPR — eliminating short-lived allocations (proposal)

Status: LANDED (Stage 1 width-2/3 CPR + Stage 3 do-block SRA). See **Appendix B** for what shipped.
§4 was the approval gate; Stage 2 (arg-position) remains a separately-approved follow-up.
Date: 2026-07-25.
Scope: `stdlib/compiler/` (extend the existing CPR unboxing in `ast_to_ir.sprout` + `ir_lowering.sprout`); no runtime/builtin change in the first cut.
Follows the AGENTS.md **Design Change Process**.

Continues the CPR thread (`docs/unboxed-adt-returns-v1-draft.md`, shipped v1/v2). Sibling of — not
overlapping with — the root-elision proposal (`docs/archive/effects-and-nonalloc-analysis-2026-07-11.md`,
which removes GC *roots* around allocating calls) and the accessor inliner
(`docs/accessor-inliner-design-2026-07-11.md`, which exposes more call sites). This proposal removes
the *allocations themselves*.

---

## 1. Problem statement

A value that is constructed and immediately consumed — never observed as a heap pointer — is still
heap-allocated. Two ops dominate in hot per-element loops:

- **Tuples** — `(a, b, c)` lowers to `sprout_alloc_tuple_blob(N*8)` + N stores
  (`ir_lowering.sprout:166-170`); a `match … with (a,b,c) ->` reads the fields straight back with a
  GEP+load (`ir_lowering.sprout:171-174`). The blob is pure garbage.
- **`Maybe`/1-field ctors** — `Just(x)`, and derived `from_ordinal`'s `Just`, lower to
  `sprout_make1(tag, x)` (`ir_lowering.sprout:235-242`), matched and discarded.

**This is a general language gap, not a demo problem.** `match f(args) with (a,b,…) ->` — call a
tuple-returning function and immediately destructure — is pervasive: **14** tuple-returning functions
in `stdlib`+`loam`, **123** in the self-hosted compiler (the `translate_*` passes thread
`(blocks, lbl, ops, val, idx)` tuples and match them everywhere), so this optimization speeds up
Sprout compiling *anything*, bootstrap included. The clinching evidence it is not demo-specific is in
`loam/hydrology.sprout` (the rivers/loam engine has since been extracted to the uncharted-suns
project — the benchmark lives there now): `dir_delta(d) -> (Int, Int)` exists but the drainage
solve **cannot use it in its hot loop** — its own comment records that returning "a fresh `(Int, Int)` tuple there allocated
~tens of millions of pairs per run (a GC firestorm)," so it was **hand-split into scalar `dir_dr` /
`dir_dc`**, duplicating the whole 8-direction table. That hand-unboxing is exactly the workaround this
optimization removes the *need* for — the demo is the measurement, not the scope.

**Measured cost.** The `terrain_rivers_demo` bake and tree-scatter passes each walk 1024² tiles;
each `bake_tile` allocates an rgb `(Int,Int,Int)` tuple + the `from_ordinal` typeclass-dispatch
**dictionary** (two `sprout_alloc_closure_env` envs — the second per-tile allocation; the
`tile_kind_of`→`from_ordinal` `Maybe` itself is already CPR-unboxed to `{i64,i64}`, so it is *not* the
box), each `place_tile` a `Maybe` (`tree_for_biome`) + a tuple (`variant_range`). Because the collector is
non-generational (full-heap mark each cycle, over the resident 1024² grids), this young garbage is
superlinear: measured **bake 9.4s → 0.19s (48×)** and **place 11.6s → 0.14s (86×)** with GC
suppressed (`SPROUT_GC_THRESHOLD=1e8`). ~59s of the demo's ~80s startup was collecting these boxes.
Fixing the demo by hand (packed-Int / raw-tag) produced ugly magic-number code and was rejected —
the right fix is for the compiler not to allocate them.

**Why the shipped CPR does not catch these** (verified, `ast_to_ir.sprout:3113-3209`, 6461-6470):
the Tier-2 worker/wrapper fires only when the match scrutinee is a **direct call to a bare top-level
fn** whose result is a **width-2 ADT** (every ctor ≤1 field), with **simple ctor-pattern arms**. It
therefore misses:
1. **Tuples entirely** — arms must be `ConstructorPattern` over the ctor table; tuple patterns never
   route, and `IRMakeTuple` is never unboxed.
2. **Width > 2 results** — `max_ar <= 1` disqualifies any 2+-field ctor (and the `{i64,i64}` ABI
   carries one payload).
3. **Arg-position results** — `g(f(x))` (e.g. `biome_rgb(tile_kind_of(tag))`) only ever routes when
   `f(x)` is a *match scrutinee*, so the nested `f(x)` boxes.

## 2. Goals and non-goals

**Goals.**
1. Stop heap-allocating a **tuple** that is constructed and immediately destructured across a single
   function-return boundary — the scalar-replacement analogue for tuples, reusing the CPR mechanism.
2. Preserve the uniform-i64 ABI and every existing call site: like CPR v2, emit a `{i64,…}` *worker*
   and keep the boxed *wrapper* for callers that don't match immediately. No ABI break, no per-type
   duplication.
3. Deliver a measured win on idiomatic code (the rivers-demo bake), not just a synthetic bench.

**Non-goals (hard fence).**
- **No generational GC / runtime GC change** in this proposal — that is the other lever
  (`docs/archive/generational-gc-v1-draft.md`), deliberately not chosen here. This is a compiler-only fix.
- **No new user syntax.** Purely an internal optimization; source is unchanged and behavior identical.
- **No general monomorphization / size-heuristic inliner** (same fence as the accessor-inliner doc).
- **No change to nullary-ctor representation.** Nullary ctors are already singleton-cached in
  `sprout_make0` (verified) — they do *not* allocate per call, so OCaml-style immediates are
  unnecessary here and out of scope.

## 3. Prior-art survey (verified against primary sources)

| technique | what it does | primary source | how it maps here |
|---|---|---|---|
| **Constructed Product Result (CPR)** | return a product in registers instead of a heap tuple; worker/wrapper; unboxes *nestedly* | GHC — [CPR paper (MSR)](https://www.microsoft.com/en-us/research/publication/constructed-product-result-analysis-haskell/), [GHC user's guide §5.3](https://downloads.haskell.org/ghc/9.8.1/docs/users_guide/using-optimisation.html) | Sprout's CPR is *named after* this; we extend it from width-2 ADTs to tuples (Stage 1) and toward nested/arg-position (Stage 2) |
| **Scalar Replacement of Aggregates**, driven by escape analysis | decompose a non-escaping object into field-locals; emit no allocation (HotSpot does *not* stack-allocate — it does SRA) | HotSpot C2 — [OpenJDK EA](https://cr.openjdk.org/~cslucas/escape-analysis/EscapeAnalysis.html), [Shipilev Quark #18](https://shipilev.net/jvm/anatomy-quarks/18-scalar-replacement/) | the intra-function case (Stage 3): a tuple/ctor `let`-bound and matched in the same fn → fields stay in SSA |
| **Interprocedural escape analysis** (connection-graph per-method summaries) | reuse a callee's escape summary at call sites | Choi et al., OOPSLA'99 — [PDF](https://faculty.cc.gatech.edu/~harrold/6340/cs6340_fall2009/Readings/choi99escape.pdf) | the summary shape a cross-call version (Stage 2) would mirror; also cited by the nonalloc-roots proposal |

Sprout's uniform-i64, compile-once ABI puts it on the **GHC pole** (selective worker/wrapper, one
shared copy) rather than the Rust/MLton monomorphization pole — consistent with the accessor-inliner
doc's framing. Nullary immediates (OCaml) are already achieved via the `sprout_make0` singleton cache.

## 4. High-level implementation overview — APPROVAL GATE

Proposed as a **staged** effort; each stage is independently reviewable, measurable, and shippable.
**This gate asks approval for Stage 1 as the first cut**, with 2–3 sketched as direction (each will
get its own approval before code).

### Stage 1 — Tuple-return CPR (the first cut)

Extend the *existing* worker/wrapper + match-routing to a **tuple-returning** function whose result
is immediately destructured by a tuple pattern at a match/`let` scrutinee.

- **Recognize:** `match f(args) with | (x, y, …) -> …` (and the `let (x,y,…) = f(args)` /
  `where` desugaring) where `f` is a bare top-level fn returning a fixed-width tuple. This mirrors
  `unboxed_maybe_match_target` (`ast_to_ir.sprout:3182`) but for a tuple pattern instead of a
  `ConstructorPattern`.
- **Emit:** a `@f_worker` returning the fields by value. Width 2 reuses the existing `{i64,i64}`
  ABI; **width 3 uses a DIRECT `{i64,i64,i64}` return — NOT sret** (correction to the original
  sketch, verified 2026-07-25). The sret convention in `docs/compiler-internals.md:74-99` is
  required **only at the LLVM-to-C extern boundary** (a Sprout caller invoking a Clang-compiled
  runtime function, where the two frontends must agree on the 24-byte struct-return ABI). A
  tuple-CPR worker is **Sprout-defined and called by Sprout-emitted code** — an LLVM-to-LLVM path,
  which is internally self-consistent, so the direct multi-register/memory return is correct and
  needs no sret. Empirically confirmed: a standalone `define {i64,i64,i64}` + `call` + `extractvalue`
  compiles, `opt --passes=verify`-passes, and returns the right values on arm64 Darwin. Wider tuples
  (≥4) fall back to the boxed path (documented cap, not silent).
- **Wrapper:** `@f` still allocates the tuple for non-matching callers (`sprout_alloc_tuple_blob`),
  exactly as CPR v2 keeps the boxed wrapper. No call site outside the recognized pattern changes.
- **New IR:** a tuple-flavoured unboxed call/return. Either reuse `IRCallUnboxed2`/`IRRetUnboxed2`
  for width 2 and the sret path for width 3, or add `IRCallUnboxedN`/`IRRetUnboxedN`. **Any new op
  must be classified in `ir_rooting.op_triggers_gc` / `op_produces_simple_heap` / `op_uses` /
  `op_def`** — these matches are exhaustive with no `_` (verified `ir_rooting.sprout:135`), so a new
  op is a compile error until classified. Removing an allocation only *reduces* rooting (safe: "a
  spurious extra root is harmless; a missing root corrupts the heap").

**Field types — general, not scalar-only.** All-scalar tuples (rgb, `variant_range`, `dir_delta`)
carry no rooting obligation and are the **first correctness milestone** (simplest to GC-stress-verify).
But heap-field tuples (`(String, String)` from a `split_once`-style fn, `(a, List a)`) are **in scope
for Stage 1**: a returned slot whose `IRType` is heap and is live across a GC trigger must be rooted at
the call site, exactly as `IRCallUnboxed2`'s `val` slot already is for `Just <heap>`. The all-scalar
case is where the mechanism is proven; heap-field support is the immediately-following increment, not a
separate stage — otherwise this would only ever help the demo.

**Why Stage 1 first:** it is the smallest extension of a shipped mechanism, it removes the one
allocation class CPR categorically cannot touch today, and it lands broadly — the rivers-demo bake's
per-cell rgb tuple *and* `loam.hydrology`'s hand-split `dir_delta` *and* the compiler's own
tuple-threading passes. Expected: `bake_region` collapses toward its ~0.19s floor.

### Stage 2 — arg-position / nested results (direction, separate approval)

> **Correction (post-implementation).** This subsection's premise — that
> `biome_rgb(tile_kind_of(tag))` boxes an inner `Maybe` — was wrong: the `from_ordinal` `Maybe` is
> already CPR-unboxed. The actual demo residue was the `from_ordinal` dispatch **dictionary**, since
> removed by devirtualization (`docs/devirtualization-v0.md`). Arg-position/nested CPR below remains a
> valid *general* direction, but it was **not** the rivers-demo bottleneck.

`biome_rgb(tile_kind_of(tag))` boxes the inner `Maybe` because it is a call argument, not a
scrutinee. Two composable routes, both already proposed elsewhere:
- Land the **accessor inliner** (`docs/accessor-inliner-design-2026-07-11.md`) so a trivial wrapper
  like `tile_kind_of` inlines and its `from_ordinal` `Maybe` becomes intra-function (→ Stage 3), and
- extend routing to recognize a CPR-eligible call in argument position feeding a consumer that
  immediately projects it. Mirrors GHC's *nested* CPR.

### Stage 3 — intra-function scalar replacement (direction, separate approval)

The pure HotSpot-SRA case: a tuple/ctor `let`-bound and later matched **within one function**, with
no escape (not returned, not stored, not passed to an unknown call) → keep its fields in SSA locals,
emit neither `IRMakeTuple`/`IRMakeCtor` nor the field loads. Needs a conservative escape check
(default "escapes"). Broadest reach; lowest priority for the demo (its cases are cross-call), highest
long-term value.

## 5. Syntax and semantics impact
None. No source syntax changes; an optimized call computes exactly what it computed before
(same fields, same order). Evaluation order preserved (the worker evaluates args in the same order;
the wrapper is a pure repack).

## 6. Type-system impact
None. Recognition runs on the typed AST / IR using types already present (tuple width is known from
the inferred type). No inference change.

## 7. Error-message impact
None. Optimization is invisible to diagnostics. A tuple wider than the supported ABI is simply not
unboxed (boxed fallback), never an error.

## 8. Compatibility / migration
Internal optimization, no observable behavior change. **Bootstrap:** the optimization changes the
compiler's own emitted IR if any `stdlib/compiler/` code matches the pattern → refresh the seed and
re-run `verify-bootstrap-fixed-point`. Scope the first cut so the seed diff is reviewable. Since
compiler source uses tuples widely, expect a real (but mechanical) seed change; use a full
`refresh-seed`, and a 2-step bootstrap only if a parser change is involved (it is not).

## 9. Tests added / updated
- **IR-shape (the core assertion):** a concretely-typed `match f(args) with (a,b,c) -> …` over a
  tuple-returning `f` lowers with **no** `sprout_alloc_tuple_blob` and a `{…}` worker call +
  `extractvalue` — the tuple never touches the heap. A non-matching caller still boxes (wrapper
  intact).
- **Behavior (preservation):** existing suites unchanged — the optimization is semantics-neutral, so
  `just test` stays green with no expectation edits.
- **Demo witness:** `terrain_rivers_demo` bake time drops (re-measure with the phase timer;
  target ≈ the GC-suppressed ~0.19s floor for bake).
- **GC stress (non-negotiable):** full suite under `SPROUT_GC_STRESS=1` — an unboxed value that
  should have stayed rooted would surface here, per the CPR/#162/#163 discipline.
- **Bootstrap:** `verify-bootstrap-fixed-point` after `refresh-seed`; smoke-shapes + bundle smoke +
  example canary (compiler-source DoD, AGENTS.md items 7–9, 11).

## 10. Spec / docs status
Experimental compiler optimization, **non-normative** — no `docs/spec-v0.md` edit. Document the
extended CPR in `docs/compiler-internals.md` and update `docs/unboxed-adt-returns-v1-draft.md`'s
"remaining bottlenecks" (tuples were unlisted there; this closes them). Add a BACKLOG entry tracking
Stages 2–3.

---

## Recommendation

Approve **Stage 1 (tuple-return CPR)** as the first cut: it is the smallest extension of a shipped
mechanism, removes the one allocation class CPR cannot touch today, directly targets the measured
rivers-demo bake cost, and reuses the existing width-2/width-3 unboxed ABIs. Hold Stages 2–3 as
separately-approved follow-ups, and keep generational GC and monomorphization firmly out of scope.

---

## Appendix A — Stage 1 implementation checklist (turnkey; APPROVED 2026-07-25)

All anchors in `stdlib/compiler/ast_to_ir.sprout` unless noted. The width-2 all-scalar milestone
reuses the shipped `IRCallUnboxed2`/`IRRetUnboxed2` ABI (two i64 slots) → **no new IR op, no
`ir_rooting` change**. Typed-AST tuple node: `typed_ast.TTuple (List TypedExpr) types.Type
SourcePos`; tuple type: `types.TTuple (List Type)`.

**RED baseline (captured, `tests/smoke_shapes/07_tuple_cpr.spr`):** `main.swap_pair -> (Int,Int)`
emits `call @sprout_alloc_tuple_blob(i64 16)` + 2 stores; `main.use_pair` matches it via `inttoptr`
+ two `load i64`. **Target:** `use_pair` → `call { i64, i64 } @main.swap_pair_worker` + `extractvalue`,
no alloc/loads; boxed `@main.swap_pair` wrapper retained.

1. **Recognizer** — new `unboxed_tuple_match_target(scrut, arms, params, captures, let_names,
   top_level, …)` beside `unboxed_maybe_match_target` (:3182). Fire iff: `scrut = TCall (TVar f) args`;
   `f ∈ top_level` and not `callee_name_shadowed`; `arms = [ TypedMatchBranch (TuplePattern [p0,p1])
   body ]` (single irrefutable arm, each `pi` a `VarPattern`/`WildcardPattern`); `f`'s result type is
   `types.TTuple [t0,t1]`. Milestone-1 guard: `t0,t1` both `type_is_non_heap_scalar`. Returns
   `(f ++ "_worker", args, [p0,p1], body)`. Call it in the `TMatch` translator where the Maybe
   recognizer is consulted.
2. **Call-site emission** — `translate_unboxed_tuple_match`: `translate_args_scalar` args →
   `IRCallUnboxed2(slot0, slot1, IRTUnknown, worker, arg_names)`; bind `p0←slot0`, `p1←slot1` into
   captures (mirror `bind_just_arg` :3213); translate `body` in that scope. Single block — **no tag
   test, no phi, no abort arm** (simpler than `translate_unboxed_maybe_match` :3340).
3. **Worker set** — extend `collect_worker_callees` (:6554) / add a `tuple_worker_shape` sibling to
   `tier2_worker_shape` (:3176): a top-level fn whose result type is a 2-tuple and whose tail bottoms
   in tuple literals qualifies. Keeps the emission scan a shadow-free superset of the router.
4. **Worker body** — add a `typed_ast.TTuple [e0,e1] _ _` case to `translate_tail_unboxed` (beside the
   nullary-`TVar` case :6725): eval `e0,e1` scalar → `IRRetUnboxed2(base, e0_ssa, e1_ssa)` (already
   lowers to `insertvalue×2 + ret {i64,i64}`, `ir_lowering.sprout:193`).
5. **No rebox wrapper** *(correction, verified 2026-07-25).* There is **no** separate boxed wrapper to
   build: the normal `@f` is compiled as-is and already allocates the tuple (it *is* the wrapper for
   non-matching callers — confirmed in the RED baseline). `emit_worker_fn` only emits the *added*
   `@f_worker`. Appendix-A-original's "tuple-shaped rebox" step does not exist.
   **BUT — the shared catch-all is tuple-hostile.** `translate_tail_catchall:6965` errors when
   `result_adt_ctors` is `Nil`, and *every* tuple result yields `Nil` (`adt_ctors_of_type` of a
   `TTuple` is empty). So a routed tuple fn whose body tail is **not** a literal `(a,b)` — e.g.
   `fn f(x) = g(x)` forwarding another tuple call, or a tail `TVar` holding a tuple — hits the
   catch-all and **fails compilation** (a regression on code that compiles today). Required fix:
   thread "result-is-tuple" into `emit_worker_fn`/`translate_tail_unboxed` and give tuple workers a
   **tuple-aware repack** for non-literal tails: eval the tail to the boxed tuple, `IRGetTupleField 0`
   / `1` (both `IRTScalar` in the milestone), `IRRetUnboxed2(base, f0, f1)` — never the ADT
   `emit_repack_arms`. This is genuine extra scope beyond the four steps above.
6. **Rooting** — width-2 all-scalar: none (scalar slots unrooted; `IRCallUnboxed2`/`IRRetUnboxed2`
   already classified `ir_rooting.sprout:190,204`). Heap-field increment: root a returned slot whose
   `IRType` is heap and live across a trigger — confirm the `IRCallUnboxed2` rooting covers *both*
   slots for a heap tuple, extend if it only roots `val`.

**Verify, in order:** (a) `--emit-ir 07_tuple_cpr.spr` → `use_pair` has the worker call + `extractvalue`
and no `alloc_tuple_blob`; wrapper still boxes. (b) run → `7`. (c) `just test` green
(semantics-neutral, no expectation edits; the loam/rivers benchmark this targets now lives in
uncharted-suns — validate it via `just test-loam` there). (d) reseed: `rm build/compile_driver_bin_stage1 && just
refresh-seed` (compiler uses tuples → real seed change, **not** `seed-fp-ack`) + `verify-bootstrap-
fixed-point`. (e) `SPROUT_GC_STRESS=1 just test` (unboxing correctness gate). (f) compiler-source DoD:
smoke-shapes, bundle-smoke, example canary. Then the heap-field + width-3 increments per §4.

---

## Appendix B — Implementation status (LANDED)

All in `stdlib/compiler/`. Tests: `tests/stdlib/test_tuple_cpr.spr` (width-2/3 CPR),
`tests/stdlib/test_tuple_sra.spr` (SRA), `tests/smoke_shapes/07_tuple_cpr.spr` + `08_tuple_cpr_w3.spr`.

### Stage 1 — tuple-return CPR (width 2), then width-3 CPR (C1)

`match f(args) with (a,b[,c]) ->` over a top-level fn returning a scalar 2- or 3-tuple routes to
`@f_worker` returning the fields by value. **Width 3 uses a DIRECT `{i64,i64,i64}` return, NOT sret**
(§4, verified) — the worker is Sprout-defined and Sprout-called (LLVM-to-LLVM, self-consistent); sret
is only for the LLVM-to-C extern boundary. New IR ops `IRCallUnboxed3`/`IRRetUnboxed3` mirror the
width-2 pair (three insertvalues/extractvalues); classified in every exhaustive `ir_rooting` match.
`scalar_tuple_width(t) -> Maybe Int` is the **single width oracle** — the router (`tuple_worker_shape`),
the worker's declared return type (`worker_ret_ty`/`emit_worker_fn`), the repack tail
(`translate_tail_tuple_repack`), and the worker-chain (`translate_tail_chain`, now width-parameterised)
all read it on the same fn-result type, so call-site and `ret` widths cannot diverge (a divergence
mismatches under `opt --passes=verify`, never silent).

### Stage 3 — intra-function tuple SRA, do-block-localized (C2)

The `let x = <producer>; …; match x with (tuple-pat) -> …` shape (the rivers-demo `bake_tile` shape)
is scalar-replaced: no heap tuple, the fields flow as N SSA scalars, an `if`-producer merges them via
N per-field phis (`build_tuple_phis`), and fn producers (`river_rgb`/`biome_rgb`-style) become
width-w workers. Two consumers share one **shadow-free** eligibility oracle `sra_core_eligible`:
worker-collection (`collect_wc_sra_producers`, emits the producer workers) and `translate_do` (fires
the rewrite, adding only a shadow gate → translation ⊆ collection, so a called worker is always
emitted). Producer lowering is `translate_tuple_scalars` (tuple literal / width-w worker call /
`if`-with-N-phi). Soundness rests on a **default-deny** escape/linearity check (`sra_escape_ok`, using
the exhaustive `compute_free_vars`): `x` must escape nowhere but the single consuming scrutinee — any
rebind, second use, or use in another step denies. A `sra_rest_plain` guard requires no Maybe/Result
do-bind in the continuation (those route to `translate_do_bind_*`, which reset the SRA map to empty),
so no SRA binding is ever live across such a boundary — this keeps the change to `translate_do` only,
not all ~9 do-helpers.

**Measured (demo, via full compile path):** `bake_tile` now calls `river_rgb_worker`/`biome_rgb_worker`
and its rgb `(Int,Int,Int)` tuple alloc is gone (3-phi merge, zero `alloc_tuple_blob`). This removed the
**first** of the two per-tile allocations. The **second** was the `from_ordinal` typeclass-dispatch
dictionary (two closure envs) — **not** a `Maybe` box (that is already CPR-unboxed) — and it has since
been eliminated by **concrete-instance devirtualization** (`docs/devirtualization-v0.md`): `tile_kind_of`
now calls `@__tc_Enum_…_from_ordinal_worker` directly with no dictionary. Net: `bake_tile` is
**allocation-free** — the full GC-suppressed floor, not half.

### Precondition: the callee's result representation must be KNOWN (added 2026-08-12)

CPR chooses a *representation* for the returned value, so it may only fire where that
representation is known at compile time. Two conventions exist and they are not compatible:

| result | unboxed convention |
|---|---|
| width-2 ADT | `{ tag, field0 }` — tag from the header, payload from field 0 |
| scalar tuple | `{ f0, f1 }` — the fields themselves, no tag |

The call-site routers pick a convention from the *instantiated* type at the call site, while the
worker is emitted once per callee from its *declared* result type. For a monomorphic callee those
agree by construction. For a callee whose declared result is a **type variable** they need not: the
same `@f_worker` symbol would have to serve an ADT instantiation at one call site and a tuple
instantiation at another, and without monomorphization both can occur in one program.

So every router gates on `cpr_result_known` (`ast_to_ir.sprout`), which reads the `cpr_result` flag
that `build_fn_arities` computes per fn from the same oracle the emitter uses
(`adt_ctors_of_type` non-empty, or `scalar_tuple_width` present). There are four gated sites — the
Tier-2 ADT router, the tuple router, the do-bind router, and `collect_worker_callees` (which must
agree, because `translate_tail_chain` chains worker-to-worker through the collected set). The
`translate_tail_catchall` fallback now hard-errors instead of assuming "no ctor list ⇒ tuple", so a
future convention gap is a compile error rather than silent mis-lowering.

Before this gate, a polymorphic callee got the tuple convention while its ADT call site read slot 0
as a tag: silent wrong answers whenever a payload happened to equal a valid ctor tag, an abort
otherwise, and an out-of-bounds load in every case. Full analysis:
`docs/bug-adt-through-generic-param-2026-08-12.md`.

The gate costs less than it appears to, because it declines **bare type variables only**. A generic
callee declared `-> Maybe b` or `-> Box b` resolves its head through `type_head_name`, so it still
workerizes with the constructor fully fused — `fn known_head(x: b) -> Maybe b = Just(x)` emits a
worker of two `insertvalue`s and a `ret`, with no allocation. Only `-> a` is declined.

That residue cannot be recovered by a better ABI, and the gate is therefore permanent rather than
provisional. By parametricity a function declared `-> a` cannot construct its result, and Tier-2
CPR's allocation win comes exclusively from fusing a tail constructor into the unboxed return; the
pass-through path boxes the value and then reads its tag back. A per-instantiation worker symbol
would relocate two loads and remove no allocation. Analysis and the surviving alternatives (inlining
before the router; allocator attributes) are filed `P3` in BACKLOG §Sprout-IR / Model-C Codegen.

### Follow-ups (see BACKLOG)

- **Devirt beyond the clean case — DONE** (`docs/devirtualization-v0.md`, increment 2): superclass
  (`Ord`) and context-constrained instances (`Eq (Maybe a) where Eq a`) now devirtualize too, forwarding
  the resolved inner dicts. Only recursive inner-dict devirt (monomorphization) remains out of scope.
- **SRA in `let..in`** (pure, non-do-block) and across a Maybe/Result do-bind (thread the SRA map
  through the `translate_do_bind_*` helpers instead of resetting).
- **Heap-field tuples** (`(String, String)`): needs per-slot rooting in `IRCallUnboxed{2,3}` + a
  multi-slot `op_heap_def`.
