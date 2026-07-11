# Phase D handoff — B1, B2, inlining, monomorphization (2026-07-11)

Handoff for a fresh session. Captures the through-line, the strategic decisions, the verified
facts, and — most importantly — **where B2 was left mid-investigation** and what to do next.

Master is at the commits below (all pushed):
- PR #160 merged: `mutmatrix_row_add_scaled_into` kernel + recognizer wiring + Phase D profiling
  evidence + bench update.
- `fe56a46` generalize B1 doc; `ca2d7fa` correct B1 reach; `60b7439` A*/nqueens baseline;
  `efefde4` accessor-inliner design.

Design docs already on master (read these; this handoff does not duplicate them):
- `docs/digit-recognizer-performance-plan-2026-07-10.md` §"Phase D preparation" — the profiling evidence.
- `docs/phase-d-numeric-fastpath-design-2026-07-11.md` — B1/B2/B3/A design.
- `docs/accessor-inliner-design-2026-07-11.md` — the inliner design (postponed, see below).

---

## 0. The through-line (how we got here)

Recognizer perf → Phase D. The recognizer is a **proxy for general Sprout performance**, not the
goal. After full kerneling it is `~3.2s`, `139/150 (92.67%)`, ~6× Go. The residual cost, from IR +
`otool` disassembly, is that the dot kernel's inner loop is **22 machine instructions/element, only
2 arithmetic**. The overhead, per element: **2 un-inlined `vector_get_direct` calls + 3 GC-root
push/pop calls + `fmov` ABI shuffles + no SIMD**. Phase D removes these:

- **B1** — inline `Vector T` element access (kill the 2 read calls, expose the bounds check).
- **B2** — stop the per-element GC-root push/pop (the single largest profile category).
- **B3** — vectorize the row-update kernels (checkpoint: TCO loop shape may block LLVM).
- **A** — Double-specialized non-scanned storage (GC scan precision; materiality-gated).

## 1. The big strategic finding — B1's reach is gated by monomorphization

**Sprout has NO monomorphizer or inliner** (verified: none in `stdlib/compiler/`; only
`is_monomorphic_*` type *predicates*). With the uniform-i64 compile-once ABI, "monomorphic
`Vector T`" means **concrete in the SOURCE TEXT**, not compiler-inferred. Consequence:

- **B1 reaches only hand-written concrete `Vector Double` sites** — the 3 kernels
  `mutmatrix_row_dot_go` / `row_sub_scaled_go` / `row_add_scaled_into_go` (params typed
  `Vector Double`).
- **B1 does NOT reach idiomatic access** — `mutvec_get(v: MutVec a, …)`, `vec_get_or`,
  `mutmatrix_get` are polymorphic wrappers; `Vector a` is erased inside them before the inner
  builtin. A\* (`MutVec Int`), nqueens (`Vec Bool`), and the recognizer's own scalar reads all go
  through these wrappers.
- The blocker is **type erasure inside the wrapper**, NOT `vector_get` vs `vector_get_direct`.
  (An earlier claim "A\* is the generalized-B1 witness" was WRONG and has been corrected in the docs.)

### The GHC-vs-Rust decision (DECIDED by Kuba)

To make concrete types reach wrapper call sites, two routes:
- **GHC route** — selective inlining (`{-# INLINE #-}` analogue): expand trivial accessor wrappers
  at concretely-typed call sites; keep the single shared copy for polymorphic callers. Aligned with
  Sprout's uniform ABI; small, incremental, reversible. (`docs/accessor-inliner-design-2026-07-11.md`)
- **Rust route** — monomorphization: a concrete copy per type instantiation. This is the
  **zero-cost / general-performance ceiling** — concrete types everywhere, unlocking B1 + CPR +
  unboxing broadly without per-site opt-in. But it's a foundational change: the self-hosted compiler
  (heavily polymorphic) must monomorphize itself (bootstrap risk), code bloat, worse compile times.
  It also negates the uniform ABI's *purpose* (one copy for all types). NOTE: mono does **not** force
  a representation change — it's separable from unboxed reps; but adopting it naturally pulls toward
  the full Rust model.

**Kuba's decision (2026-07-11):** the **end goal is the zero-cost / monomorphization model** — that
is what he cares about — **but it is too big to take on now, so it is POSTPONED.** The
accessor-inliner (the GHC-route tactical bridge) is **also postponed** in favor of **doing B2
first**. When the monomorphization arc is picked up, it deserves its own strategic design doc
(uniform+inline vs monomorphize) before committing.

**⇒ Immediate next work: B2.** It needs neither the inliner nor monomorphization.

## 2. B2 — leaf-loop GC-root elision (NEXT; left mid-investigation)

### The corrected mechanism (this is the key insight — the design doc's "allocation-free region analysis" framing is heavier than needed)

The recognizer's 3 GC-root calls/element exist because the rooting pass
(`stdlib/compiler/ir_rooting.sprout`, `fn op_triggers_gc`, line ~30) **conservatively treats the
non-allocating `vector_get_direct` read as a GC trigger** and roots live pointers (`raw_m`,`raw_v`)
around it. `vector_get_direct` is `v->data[index]` + bounds check — it **cannot allocate**, so those
roots are pure waste. **B2 = reclassify provably-non-allocating read externs as non-triggers.**

### Exactly where I was reading when we stopped

`op_triggers_gc` matches on **op type**, and currently:
- line 44: `IRCall _ _ _ _ -> true` — conservative: "every user call may transitively allocate."
- line 49: `IRCallUnboxed2 _ _ _ _ _ -> true` — conservative; comment notes "Most (vector_get,
  map_get, …) do not allocate, but `regex_find_range_unboxed` / `term_read_line_unboxed` still
  allocate their payload — so conservatively a trigger."

So the `vector_get_direct` call (an `IRCall`) is a trigger purely by the blanket `IRCall -> true`.

### The B2 change (design it precisely — this is GC-CORRECTNESS-DELICATE)

`op_triggers_gc` must **peek the callee name (fname)** for `IRCall`/`IRCallUnboxed2` and return
`false` for a **verified allow-list of non-allocating reads**, `true` otherwise. Critical
per-callee facts (VERIFY each in `runtime/sprout_runtime.c` before trusting):
- `vector_get_direct` (line 6184) — `v->data[index]`, **non-allocating** → safe non-trigger.
- `vector_get` (line 6107) — **ALLOCATES** the `Just`/`Nothing` Maybe box → **MUST stay a trigger.**
- `vector_mutset` (line 6176) — plain store, non-allocating (today; Phase 2 GC adds a write barrier
  — revisit then).
- `*_unboxed` variants (declared `ir_lowering.sprout:431+`): `vector_get_unboxed`, `map_get_unboxed`
  etc. are non-allocating, BUT `regex_find_range_unboxed` / `term_read_line_unboxed` **DO allocate**
  → allow-list must be per-name, not "all `_unboxed`".
- `vector_length` / `mutvec_len` and similar pure reads — verify, likely non-triggers.

### Hard safety rules for B2 (from the file's own scars)

- `op_triggers_gc` is **EXHAUSTIVE, no `_` catch-all** (lines 80–82): "a new IROp is a COMPILE ERROR
  until its GC-trigger status is decided (P11-2e: `IRMakeTuple` silently defaulting to non-trigger
  caused a UAF)." Keep that discipline — only reclassify names you have **verified** non-allocating.
- **Validate under `SPROUT_GC_STRESS=1` (`just test-stress`), the rooting-bug oracle — default
  greens are FALSE** (`project_gc_stress_oracle`). A dropped root is a use-after-free that normal
  tests miss.
- Correctness argument: since a non-allocating call cannot trigger GC *at that point*, live pointers
  don't need rooting *across it*. If a pointer is live across a *real* trigger elsewhere in scope,
  root-once coalescing still roots it (per-def, PR #108). So reclassification is safe and precise.

### Reach / expected payoff

- Recognizer kernels: removes the 3 GC-root calls/element (the largest profile category). Expect a
  real wall drop. Accuracy must stay `139/150` (bit-identical; `assert_true`+`==`, never
  `assert_eq` on Double).
- A\*: benefits **iff** its reads reach a non-allocating extern. A\* uses `mutvec_get` → `vector_get`
  (allocates Maybe) UNLESS the Tier-1 CPR peephole rewrites a do-bound/matched `vector_get` to
  `vector_get_unboxed` (non-allocating). **VERIFY** whether A*'s `mutvec_get` sites are CPR-unboxed;
  if so, reclassifying `vector_get_unboxed` removes A*'s roots too (B2 reaches idiomatic code where
  B1 can't). If not, A\* still routes through allocating `vector_get` and won't gain from B2.

### B2 next steps (TDD, compiler-source DoD)

1. Confirm the per-callee allocation facts in the runtime (list above).
2. TDD: an IR-shape test that a `vector_get_direct` in an allocation-free loop emits **no**
   `sprout_gc_push_i64_root`/`pop_roots` around it (RED on current code).
3. Implement the fname allow-list in `op_triggers_gc` (peek fname for `IRCall`/`IRCallUnboxed2`).
4. **Compiler-source DoD** (`AGENTS.md`): `refresh-seed` BEFORE `just test`
   (`feedback_refresh_seed_before_test_for_compiler_changes`; delete
   `build/compile_driver_bin_stage1` first per `feedback_refresh_seed_stale_binary`);
   `just test-stress`; `just compile-examples-stage1`; `just verify-bootstrap-fixed-point`; smoke
   shapes + bundle smoke; seed gate on commit.
5. Re-measure recognizer + A\* + nqueens vs `bench/results-2026-07-11.md`.

## 3. B1 — implementation facts (for when it's picked up, post-inliner/mono)

- Extends the **Tier-1 CPR peephole** path (`ir_lowering.sprout:172`, `IRCallUnboxed2`).
- `IRCallUnboxed2._vk` is **hardcoded `IRTUnknown`** at emission (`ast_to_ir.sprout:1251, 1420`) —
  the element kind is erased there; threading it is a prerequisite for the Maybe/`vector_get` path.
- `vector_get_direct` is a plain `IRCall`; its arg **type IS available** in `translate_call`
  (`ast_to_ir.sprout:3969`, typed args + `call_ty`); gate on `is_monomorphic_double`
  (`ast_to_ir.sprout:568`).
- `VectorVal` layout: `{ len@0, cap@8, data@16 }`, `data` is `long long*`
  (`runtime/sprout_runtime.c:73`). Inline read = inttoptr → load len → bounds-check → load data ptr
  → GEP → load.
- Rooting for inlined reads: add the new op to the **exhaustive** `ir_rooting` classifiers
  (`op_triggers_gc` AND the heap-result classifier ~line 113), reporting result kind by element
  type (scalar → no root; pointer → root).
- **Scope B1 commit-1 to Double-only** so the compiler's own `Vector Int`/`Vector Token` reads stay
  byte-identical → `verify-bootstrap-fixed-point` holds trivially. Widening to other scalar types
  changes the compiler's IR (seed regen, fixed-point load-bearing).
- Perf expectation: B1-Double removes the 2 calls + `fmov`s (~22→~14 insns) but NOT the GC-roots
  (that's B2) nor SIMD (B3). Modest win alone.

## 4. B3 / A (later)

- **B3 checkpoint:** after B1+B2, disassemble a row-update kernel; if no vector (`.2d`) ops, the
  TCO `tco_loop`/`stacksave` shape blocked LLVM → emit a real counted loop (canonical IV, no
  `stacksave`, SSA/phi loop state). Only the **row-update** kernels are safe to vectorize;
  **`row_dot` is a reduction** — SIMD reassociates FP adds → breaks `139/150`. Dot-SIMD is a
  separately-gated decision.
- **A:** Double-specialized non-scanned store. Rationale is **GC scan precision** (the collector
  scans every `Vector Double` element as a candidate pointer — `sprout_runtime.c`
  `sprout_heap_child_value_payload`, `case SPROUT_HEAP_VECTOR`), NOT the `fmov` (B1's typed load
  handles that). Materiality-gated (GC ~17% of wall).

## 5. Consolidated verified facts & gotchas

- **No inliner/monomorphizer exists.** The only "specialization" is typeclass-dict lowering
  (BACKLOG:238), not reusable for plain functions.
- **Duplicate-`entry:` trivial-accessor codegen bug is LEGACY-`codegen.sprout`-only** — does NOT
  reproduce on the active `--emit-ir` path (verified: `opt --passes=verify` clean, one entry/define).
  Not a risk for typed-AST passes.
- **`-flto` proven ineffective** — kernel loop byte-identical, no speedup; the LTO inliner declines
  `vector_get_direct` and GC-root calls are optimization barriers. Inlining must be at the IR layer.
- **`sample` can't split the levers** (one inlined symbol); the static **IR + asm** is the gate.
  Frame-occurrence counts ≠ self-time %.
- **Baselines (M1-class, warm):** recognizer ~3.2s / `139/150`; A\* ~305 µs/run (flat vs 2026-06-30);
  nqueens N=12 ~505 ms (~1.5× faster than 2026-06-30's 797 ms, from the CPR/codegen arc).
- **Bit-identical Double testing:** `assert_eq` is a SILENT no-op on Double; use
  `assert_true(state, …, x == y)`.
- Active codegen path: `ast_to_ir` → `sprout_ir` → `ir_lowering` (NOT `codegen.sprout`, retiring).
- Rooting pass is **value-kind-driven** (roots by produced-value kind), exhaustive classifiers.

## 6. Key files

| file | what |
|---|---|
| `stdlib/compiler/ir_rooting.sprout` | **B2 target** — `op_triggers_gc` (~L30), heap-result classifier (~L113) |
| `stdlib/compiler/ir_lowering.sprout` | IR→LLVM text; `IRCallUnboxed2` lowering (L172); `_unboxed` declares (L431+) |
| `stdlib/compiler/ast_to_ir.sprout` | `IRCallUnboxed2` emit (L1251,1420, `_vk`=IRTUnknown); `translate_call` (L3969); `is_monomorphic_double` (L568); `unboxed_maybe_extern_name` (L2887) |
| `runtime/sprout_runtime.c` | `vector_get` L6107 (allocates), `vector_mutset` L6176, `vector_get_direct` L6184; `VectorVal` L73; vector element scan L1311 |
| `stdlib/mutable.sprout` | the 3 Double kernels + the polymorphic accessors (`mutvec_get` etc.) |
| `examples/{digit_recognizer/recognizer,astar,nqueens}.sprout` | witnesses; benches in `bench/*/bench.sh` |

## 7. Relevant memories
`project_phase_d_prep_and_recognizer_kerneling` (updated with all of the above),
`project_gc_stress_oracle`, `project_root_once_coalescing_landed`,
`project_tier2_cpr_active_path_and_adt_index`, `project_cpr_active_path_gap_and_ml_perf`,
`project_trivial_accessor_codegen_bug`, `feedback_refresh_seed_before_test_for_compiler_changes`.
