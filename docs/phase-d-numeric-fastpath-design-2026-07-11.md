# Phase D — numeric fast-path design (proposal, needs approval)

Status: proposal / design-for-approval (do not implement before sign-off)
Date: 2026-07-11
Scope: `stdlib/compiler/` (codegen + rooting), `runtime/sprout_runtime.c` (Vector layout ABI),
`stdlib.mutable` Double kernels. Depends on the profiling evidence in
`docs/digit-recognizer-performance-plan-2026-07-10.md` (§"Phase D preparation").

This doc follows the AGENTS.md **Design Change Process**. It is a proposal: the high-level
implementation overview (§4) is the part requiring approval before any editing.

---

## 1. Problem statement

On a **fully-kerneled** digit recognizer (`64→256→10`, after the Phase B follow-up landed
2026-07-11), the training loop is compute-bound with a ~9× wall gap to Go, and the residual cost is
**scalar-access overhead, not allocation** (Phase C proved the hot loops allocation-flat with
problem size). Ground truth from the emitted IR and `otool` disassembly of `mutmatrix_row_dot_go`:
the inner loop is **22 machine instructions per element, of which exactly 2 are arithmetic**
(`fmul`, `fadd`). The other 20 are overhead, in three removable classes:

| per-element overhead | count | mechanism |
|---|---|---|
| `bl _vector_get_direct` | 2 | opaque C call; bounds-check hidden inside; un-inlinable (see §4 B1) |
| `bl _sprout_gc_push_i64_root` / `pop_roots` | 3 | root-stack + `sp` juggling in an **allocation-free** loop |
| `fmov` GPR↔FP | 4 | uniform-i64 ABI: every `Double` round-trips through an integer register |
| autovectorization | 0 | blocked entirely by the opaque calls above |

The post-kernel `sample` corroborates it: with closures gone, GC-root push/pop appears in the most
call-tree nodes (91+55), then un-inlined `vector_get_direct` (23). (Frame *occurrence* counts, not
self-time %; the asm above — 3 live GC-root `bl` + 2 `vector_get_direct` `bl` per element — is the
decisive backing.) Both are compiler/IR concerns. This is what Phase D must attack.

## 2. Goals and non-goals

**Framing.** The digit recognizer is a *proxy* for Sprout's general performance, not the target. So
the primary lever (B1) is scoped to the **primitive `Vector` layer**, keyed on the **element type**,
not to `MutVec`/`MutMatrix` as containers. **Important reach limit (verified 2026-07-11):** Sprout
has **no monomorphization/inliner**, so B1 fires only where the element type is concrete *at the
source call site*. `MutVec`/`MutMatrix`/`Vec` accessors (`mutvec_get`, `vec_get_or`, …) are
*polymorphic wrappers* (`Vector a`, erased) — B1 does **not** reach reads made through them. It
reaches hand-written concretely-typed sites (the Double kernels). Lighting up idiomatic
container access needs an inliner/monomorphizer first (§4 "Reachability", §Recommendation). The
recognizer kernels are the near-term validation case *and*, for now, close to the whole reachable
scope.

**Goals.** For any statically-monomorphic `Vector T` element access:
1. Element read/write is **not an opaque runtime call** — it lowers to inline IR
   (`getelementptr` + `load`/`store`) whose bounds check LLVM's `prove`/`indvars` passes can hoist
   or eliminate. Read generalizes to every `T`; write is scalar-`T` first (see §4 B1).
2. Pointers that are only **read** inside an **allocation-free leaf loop** are **not rooted
   per-iteration** (element-type-agnostic; the broadest lever — helps any allocation-free loop).
3. The two **independent-write** kernels (`row_sub_scaled_inplace`, `row_add_scaled_into`) become
   **autovectorizable** — subject to the loop-shape checkpoint in §4 B3 (removing the calls is
   necessary but may not be sufficient; the TCO loop shape may still block the vectorizer).
4. **Memory safety and bit-identical FP semantics are preserved by default.** Any change that would
   alter the golden `139/150` (§5) is opt-in and separately gated.

**Non-goals.**
- A user-facing SIMD intrinsic surface, or a general linear-algebra library.
- Inlining access for **polymorphic** `Vector a` (element type erased to a type variable) — those
  keep the runtime call. Scope is *monomorphic* sites only.
- Inlining **pointer-element writes** in the first slice (kept at the `vector_mutset` chokepoint for
  the Phase 2 GC write barrier — see §4 B1).
- Any GC algorithm change (non-moving generational per the GC end-state decision stands).
- `Float`/f32, string→float parse, `to_int`/`fptosi` — tracked elsewhere.
- Auto-vectorizing the **dot reduction** by default — see §5, this reorders FP adds.

## 3. Prior-art survey (verified against primary sources)

The question — *how does a statically-typed language make `f64`-array element access cheap in a
hot loop?* — has a clear cross-language consensus: **flat unboxed storage + an explicit way to drop
the per-element bounds check**, and (unlike Sprout today) **no per-element GC bookkeeping** on the
unboxed path.

| language | unboxed flat f64 storage | bounds-check opt-out | primary source |
|---|---|---|---|
| **Rust** | `[f64]`/`Vec<f64>` contiguous | indexing panics on OOB; `get_unchecked` is `unsafe` and skips it ("out-of-bounds index is undefined behavior") | std `slice::get_unchecked` docs |
| **Haskell** | `Data.Vector.Unboxed` "picks an efficient, specialised representation for every element type … backed by unboxed array(s)" | checked `(!)`/`(!?)` vs `unsafeIndex`/`unsafeRead` which "omit boundary validation" | Hackage `vector` `Data.Vector.Unboxed` |
| **OCaml** | `float array` uses a distinct representation (float arrays have half the max length of general arrays) | `-unsafe` "turn[s] bound checking off for array and string accesses … faster, but unsafe" | OCaml 5.2 `Array` module; `ocamlopt` options |

**What this tells the design.** (a) Sprout already has flat unboxed storage — `Vector Double` is
i64-encoded 8-byte slots, bit-identical to `f64` in memory; the gap is not *representation* but
*access*. (b) Every one of these languages lets the hot loop reach an **unchecked, inline** element
access (safe default + opt-out, or JIT-hoisted check) — Sprout's access is an un-inlinable *call*
with the check trapped inside it, the worst of both. (c) None of them pays a **GC root push/pop per
element**; they use precise stack maps and don't touch the collector inside a leaf numeric loop.
Levers B1 (inline access) and B2 (root elision) are exactly the two things this survey says every
fast numeric runtime already does and Sprout does not.

## 4. High-level implementation overview (APPROVAL GATE)

Ordered by evidence-weighted value (biggest lever first). **B before A.**

### B1 — inlinable monomorphic `Vector T` element access (generalized, not Double-only)

> **B1-Double LANDED — 2026-07-12** (branch `worktree-phase-d-b1-double`). Scoped to `Vector Double`
> sites (commit-1). Three new IR ops in `sprout_ir.sprout`: `IRVecLenD` (load `len@0`), `IRVecGetD`
> (load `data[i]`), `IRVecSetD` (store `data[i]`) — pure straight-line **unchecked** loads/stores.
> The null + bounds guard is emitted as **IRBlocks** in `ast_to_ir.sprout` (`vec_double_guard`,
> mirroring `finish_checked_div` — a checked op must split blocks at the IRBlock layer, not the text
> layer, or downstream phi predecessors name the wrong block). The recognizer (`translate_direct_call`)
> Double-gates on `arg0_elem_is_double`, which matches the **exact canonical** `type_id_name`
> (`Vector`/`Double`), NOT the last-segment display — a user `type Double = <heap ADT>` is
> canonically `main.Double` and must stay on the call path, or IRVecGetD would load an unrooted heap
> pointer (use-after-free; caught in code review). The gate fires **only when fully applied** (arity 2
> for get / 3 for set) so an under-applied `vector_get_direct` still reaches partial application.
> Every other element type / arity falls through to the plain `IRCall`. Rooting (`ir_rooting.sprout`):
> all three ops are non-triggers and produce **scalar** results (a Double bit-pattern is never rooted
> as a pointer) — validated under `SPROUT_GC_STRESS=1`. Bounds uses one **unsigned** compare
> (`idx uge len` also catches `idx<0`). Regression guard: `just b1-gate` (asserts B1 fires on real
> `Vector Double`, does NOT fire on a shadowed heap `Double`, allows partial application, and traps
> out-of-bounds — none observable from numeric-result tests alone).
>
> **Results (M1-class, same-machine A/B, `-O2`, best of 4):** recognizer **1.33s → 0.51s (~2.6×)** at
> unchanged accuracy **139/150** — substantially larger than the "modest win" originally predicted:
> the two `vector_get_direct` calls per element were pipeline barriers in the innermost training
> loop, not just extra instructions. The three `Vector Double` kernels (`row_dot_go`,
> `row_sub_scaled_go`, `row_add_scaled_into_go`) are now **call-free**; the `fmov` ABI shuffles are
> gone (the inline `load i64` folds to `ldr d`). **NOT byte-identity-preserving** (global op set):
> the seed reconverges to a new fixed point (iteration 2). The compiler makes **0** vector-accessor
> calls, so B1 never fires on compiler code — no miscompile risk, `just test` + all examples green.
> **B3 re-checkpoint (post-B1):** still **0 `.2d`** in the Double kernels; `-Rpass-analysis` now
> reports **"Incorrect number of successors from early exiting block"** (the bounds-check panic
> branch) rather than "call instruction" — so B3 must hoist/eliminate the bounds check **and** fix
> the tco/stackrestore loop shape. B1 is the precondition it needed; B3 remains distinct work.

Replace the `call @vector_get_direct(vec, i)` / `call @vector_mutset(vec, i, x)` emitted at a
**statically-monomorphic `Vector T`** site with **inline IR**: load the data pointer + length from
the Vector header, emit an inline bounds `icmp`+branch (identical trap behavior), then
`getelementptr` + `load`/`store` at index `i`. Because the check is now *in the IR*, LLVM can
prove-and-hoist or eliminate it and (for independent writes) vectorize.

**Why this is not Double-specific.** Every `Vector T` stores uniform 64-bit slots
(`v->data[index]`), so the *read* is the same slot load for every `T`; the value returns exactly as
`vector_get_direct` yields it today. Double is not load-bearing for the read. The real axis is
**scalar vs pointer element**, and it splits read from write:

- **Read — generalizes to every monomorphic `T`, one slice.** The result's rooting is preserved by
  the existing rooting pass, which is **value-kind-driven, not call-site-driven**: `ir_rooting`
  seeds its root scope from whatever op produces a heap-typed value, keyed by kind (`IRTScalar` →
  never rooted; `IRTHeap`/unknown → rooted across later triggers), and *already* classifies
  `vector_get_direct`'s result by element-type kind (a `Vector String` read is rooted today; a
  `Vector Double` read is not). So the new inlined-read op must be added to that **exhaustive** (no
  `_` catch-all → fail-loud) classifier, reporting its result kind by element type: scalar → no root
  (a win), pointer → rooted exactly as before. No rooting-model rewrite needed.
- **Write — inline for scalar `T` now; keep pointer-element writes as `vector_mutset`.** Today
  `vector_mutset` has no write barrier, so a scalar store inlines to a plain `store` safely. Pointer
  stores stay at the `vector_mutset` call: the Phase 2 generational GC will add a write barrier
  there, and one chokepoint is where that work should land. Revisit inlining pointer writes when the
  Phase 2 barrier exists.

**Reachability — "monomorphic" means CONCRETE-IN-SOURCE, and that sharply limits reach (verified
2026-07-11).** Sprout compiles once with a uniform-i64 ABI and has **no monomorphization/inliner
pass** (verified: no such pass in `stdlib/compiler/`; only `is_monomorphic_*` type *predicates*).
So B1 can fire only where the element type is `Vector Double` **at the source call site** — i.e.
the hand-written kernels (`mutmatrix_row_dot_go(raw_m: Vector Double, …)` etc.). It does **not**
reach reads that go through a **polymorphic wrapper**: `mutvec_get(v: MutVec a, …)` /
`vec_get_or` call `vector_get` on `Vector a` — *erased* — so the concrete `Double`/`Int` never
reaches that site. Consequences:
- The recognizer's Double kernels are reachable (concrete in source) — the valid slice-1 witness.
- `examples/astar.sprout` is **NOT** reachable: its hot loop is `mutvec_get`/`mutvec_set` over the
  polymorphic `MutVec a` wrapper. The blocker is **type erasure in the wrapper**, not
  `vector_get` vs `vector_get_direct` — inlining `vector_get` would not help, because the element
  type is already gone by the time control reaches it. A* becomes a *motivating case for a future
  inliner/monomorphizer*, not a B1 witness.
- Reaching idiomatic / generic-wrapper-mediated code (the "general Sprout performance" goal) needs
  an **inliner or monomorphization pass** — separate, larger work, and arguably the higher-leverage
  prerequisite if broad reach is the priority (see the strategic note in §Recommendation).

**Two prerequisites to verify at implementation time (assumed, not confirmed):**
1. The concrete element-type kind reaches the active typed-lowering site (`ast_to_ir`→`sprout_ir`→
   `ir_lowering`) so a `vector_get_direct`/`vector_mutset` call can be classified scalar/pointer
   (via the `field_kinds` IR-type machinery). If the type is erased by then, un-erasing it is a
   prerequisite task, not a detail.
2. A **pointer-element** read held live across an allocation is validated under
   `SPROUT_GC_STRESS=1` (`just test-stress`), the rooting-bug oracle — a dropped root is a
   false-green under default tests. Probe: `Vector String` (or `Vector` of an ADT) read, held across
   an allocating call, under stress.

**Prerequisite (shared):** a documented `Vector` layout ABI contract (header word(s), length, data
ptr) shared between `runtime/sprout_runtime.c` and codegen — today the layout is private to the
runtime. Review against the GC header plans (Phase 2 header rewrite).

**Rejected alternative (measured):** compiling the runtime with `-flto` so clang inlines
`vector_get_direct`. **Proven ineffective** — the kernel loop was byte-for-byte unchanged and wall
did not move (evidence in the perf plan). The inliner declines, and the GC-root calls are
optimization barriers regardless. Inlining must happen at *our* IR layer.

### B2 — leaf-loop GC-root elision — **LANDED 2026-07-11**
In a loop body with **no allocation and no call that can trigger GC** between a pointer's root and
its last use, a read-only pointer (`raw_m`, `raw_v`, `raw_dst`) does not need a per-iteration
`push_i64_root`/`pop_roots`.

**Mechanism as implemented (simpler than the "allocation-free region analysis" originally
sketched):** the rooting pass (`stdlib/compiler/ir_rooting.sprout`, `op_triggers_gc`) conservatively
treated *every* `IRCall` as a GC trigger. B2 peeks the callee name (`is_nonallocating_read`) and
returns `false` for a **verified allow-list of non-allocating read/store externs** —
`vector_get_direct`, `vector_mutset`, `vector_length` (each read against `runtime/sprout_runtime.c`;
the allocating `vector_get`, which boxes a `Just`/`Nothing`, deliberately stays a trigger). A
non-trigger short-circuits in `process_op` before any rooting, so no second edit is needed.
**Correctness:** a non-allocating call cannot collect at that point, so no live pointer needs rooting
across it; a pointer live across a *real* trigger elsewhere is still rooted there by root-once
coalescing (PR #108). Validated under `SPROUT_GC_STRESS=1` (`just test-stress`), the rooting-bug
oracle, in addition to the full suite; IR-shape regression tests T17–T19 in `tests/stdlib/test_ir_rooting.spr`.

**Not byte-identity-preserving** (unlike B1-Double): `op_triggers_gc` is global, so the compiler's
own `vector_length`/`vector_get_direct`/`vector_mutset` calls also shed roots — the bootstrap seed
reconverges to a new fixed point (3 refresh-seed iterations).

**Results (M1-class, warm; see `bench/results-2026-07-11-b2.md`):** recognizer **3.03s → 1.67s
(~1.8×)** at unchanged accuracy `139/150` — the 3 GC-root calls/element were the dominant cost, as
the profile predicted. A* (~305 µs) and nqueens N=12 (~500 ms) flat = **no regression**. A* got no *win* not because its
wrapper allocates — CPR unboxing already sees through `mutvec_get` and emits the non-allocating
`vector_get_unboxed` (verified: `astar.sprout --emit-ir` has 4) — but because the B2 commit
reclassifies only plain `IRCall`, not the `IRCallUnboxed2` op unboxing produces. nqueens' cost is
persistent `vec_set` copies, outside B2 entirely.

**B2 reach extension — LANDED 2026-07-11** (`IRCallUnboxed2` allow-list). `op_triggers_gc` also peeks
the `IRCallUnboxed2` callee (`is_nonallocating_unboxed_read`): non-trigger for the 8 verified
non-allocating unboxed reads (`vector_get_unboxed`, `map_get_unboxed`, `map_nth_key/value_unboxed`,
`bytes_get_unboxed`, `str_char_at_unboxed`, `argv_get_unboxed`, `env_get_unboxed`), trigger for the 2
that allocate (`regex_find_range_unboxed` → `@sprout_alloc_range_val`; `term_read_line_unboxed` →
`register_cstr`). **Correction to the note above:** `env_get_unboxed` does **not** allocate (returns
`getenv()`'s pointer, runtime:4342) — the landmine set is only those 2. **Result** (bench
`results-2026-07-11-unboxed-reach.md`): a tight `MutVec Int` sum loop (root-bound on the unboxed read)
runs **~1.3× faster** (~8.3→6.3 ns/read); A*/nqueens/recognizer flat = no regression (not root-bound
on unboxed reads). The win removes the **worker-internal** root around `vector_get_unboxed`; the
larger per-read root — around the `mutvec_get_worker` *call* itself (a Sprout `IRCall`) — remains, and
needs interprocedural non-allocation inference (the Koka-style analysis in BACKLOG).

Note that once B1 makes access inline (no call), the *only* calls left in the loop are the GC-root
ops themselves, so eliding them can make the body call-free — the precondition for vectorization.

### B3 — loop-shaped codegen (contingency, gated on the B1+B2 empirical checkpoint)
"B1+B2 → LLVM vectorizes" is an **assumption, not a consequence**, and is the first thing to
measure once B1+B2 land. The kernels are tail-recursive `go` functions lowered to a `tco_loop`
with `llvm.stacksave`/`stackrestore` and alloca'd loop state; LLVM may not recognize that shape as
a vectorizable counted loop even after the calls and roots are gone. **Checkpoint:** disassemble a
row-update kernel; if there are no vector (`.2d`) ops, B3 is required — emit the monomorphic Double
kernels as a real counted loop (canonical induction variable, no `stacksave`/`stackrestore`, loop
state in SSA/phis) so LLVM's loop vectorizer engages. B3 is a distinct codegen sub-lever; do not
assume it comes for free.

**CHECKPOINT RAN — 2026-07-12 (B2-only; B1 not yet landed).** Disassembled all three row kernels
from `examples/digit_recognizer/recognizer.sprout` at `clang -O2` **and** `-O3`. Result: **zero
vector-lane ops** (`.2d`/`.4s`/…) in any kernel or anywhere in the linked binary. **But the negative
is not yet attributable to loop shape — it is gated entirely on B1.** The precondition this checkpoint
assumes ("after the calls and roots are gone") is **not met**: B1 has not landed, so the emitted IR
still contains the un-inlined `vector_get_direct`×2 + `vector_mutset` calls
(`recognizer.ll` `@stdlib.mutable.mutmatrix_row_sub_scaled_go`). LLVM's vectorizer bails at the first
opaque call and never evaluates the loop shape — confirmed from the compiler's own mouth via
`clang -O3 -Rpass-analysis=loop-vectorize`, whose sole reason for these loops is **"call instruction
cannot be vectorized"** (500× across the module; verified on the isolated kernel too).

**Evidence that B3's premise is currently *unsupported*:** the O2 disassembly shows LLVM promoted the
`alloca`'d TCO loop state to registers (SROA) and formed a **clean counted loop** anyway —
`add x20,#1` / `cmp x19,x20` / `b.ne` back-edge, with the only shape residue being a per-iteration
`mov sp, x25` (the `stackrestore`). So the worry that "the `tco_loop`/`stacksave` shape stops LLVM
recognizing a countable loop" is **not borne out** by current evidence; the induction variable and
trip count were identified. **Conclusion:** the checkpoint's blocker today is **B1, not B3.** A
*definitive* B3 verdict is unobtainable until B1 removes the calls. **Caveat (do not overstate):**
B1 does **not** leave the `stackrestore` as the *sole* barrier — inlining the reads replaces each
`call` with a null/**bounds-check early exit** (a `noreturn` `tcp_fail` branch inside the loop), and
the remarks already list "Cannot vectorize early exit loop" as a distinct refusal. So **post-B1 the
row loop still has two barriers: (a) the bounds-check early exit and (b) the `stackrestore` loop
shape.** B3 must handle **both** — hoisting/eliminating the bounds check *and* emitting a canonical
counted loop — it is larger than "just the tco/stacksave shape."

**UPDATE — B1-Double landed 2026-07-12** (§B1 note above). The post-B1 re-checkpoint was run and
**confirmed the two-barrier prediction**: the Double kernels are now call-free yet still emit **zero
`.2d`**, and `-Rpass-analysis` reports the blocker has **shifted from "call instruction" to
"Incorrect number of successors from early exiting block"** — the bounds-check panic branch. So B3
remains distinct work: it must hoist/eliminate the bounds check *and* emit a canonical counted loop.
The IR-surgery pre-simulation (inline the reads/writes as typed `load`/`store double`, delete the
`stacksave`/`stackrestore`) was not needed — the real B1 provided the measurement.

### A — Double-specialized non-scanned backing store (follow-on, materiality-gated)
**Corrected rationale.** A does *not* remove the `fmov` shuffles — B1 already does, because a typed
`load double` lands the value directly in an FP register with no GPR round-trip. A's only surviving
justification is **GC scan precision**: today the collector enumerates *every* element of a
`Vector Double` as a candidate heap value each mark cycle
(`runtime/sprout_runtime.c` `sprout_heap_child_value_payload`, `case SPROUT_HEAP_VECTOR:
return ...->data[index]`), so a 256-wide weight matrix costs O(256) scalar-scan work per cycle plus
a conservative false-retention hazard (a `double` bit-pattern that aliases a heap address is
mis-marked live). A `Double`-typed store the GC knows holds no pointers makes vector marking O(1)
and removes the hazard. **But** Phase C measured GC at only ~17% of wall and falling, so A's payoff
must be *measured* (mark-phase time on the recognizer) before it earns scope. Naturally-aligned
`double` storage is a secondary benefit for B3's SIMD loads. Deferred within Phase D, and may be
dropped if the mark-scan cost proves immaterial.

## 5. Syntax and semantics impact

No surface-syntax change. The kernels keep their signatures. **Critical semantics constraint:**
`row_dot` is a **reduction** (`acc + Σ m·v`); SIMD summation **reassociates** floating-point adds,
which changes the result and would move the golden `139/150`. Therefore:
- **Independent-write kernels** (`row_sub_scaled_inplace`, `row_add_scaled_into`): no cross-element
  dependency → vectorizable bit-identically (element writes don't reassociate), *if* the loop shape
  cooperates (§4 B3).
- **`row_dot` reduction:** B1+B2 give scalar speedup (fewer calls/roots) with **bit-identical**
  output. *True* SIMD reduction is **out of default scope** — it requires either an accepted new
  golden accuracy or an explicit ordered/pairwise-tree reduction, gated separately. Default builds
  must **not** enable fast-math reassociation.

## 6. Type-system impact
Minimal. The read fast path is guarded on the element type being statically **known** (any
monomorphic `T`), classified scalar/pointer via the existing `field_kinds` IR-type machinery; the
write fast path additionally requires scalar `T` (first slice). Polymorphic `Vector a` (erased
element type) is unchanged — it keeps the runtime call. No new surface types (A adds an internal
specialized store, not a user-visible type).

## 7. Error-message impact
None by default: the inlined bounds check preserves the exact same OOB trap/`Nothing` behavior as
`vector_get`/`vector_get_direct`. An `unsafe` opt-out (à la OCaml `-unsafe`) is explicitly **not**
proposed here — Sprout's safety-first intent argues for hoist-the-check, not drop-the-check.

## 8. Compatibility / migration
Internal optimization; no user-visible behavior change. Golden IR snapshots change (they are not a
CI gate). If codegen emits new IR shapes, the compiler seed (`bootstrap/compile_driver.ll`) changes
and must be refreshed per the seed gate — unlike the Phase B follow-up, which left the seed
untouched.

## 9. Tests added / updated
- **IR-shape test:** a monomorphic `Vector Double` read lowers to `getelementptr` + inline bounds
  `icmp` + `load`, and **no** `call @vector_get_direct`. Add a scalar non-Double case
  (`Vector Int`) to prove the generalization.
- **Pointer-element rooting (the generalization's real risk):** a `Vector String` (or `Vector` of
  an ADT) read held live across an allocating call, validated under `just test-stress`
  (`SPROUT_GC_STRESS=1`) — the rooting-bug oracle; a dropped root is a false-green under default
  tests. This gates whether pointer-element reads ship in the first slice.
- **Vectorization asm check:** `row_sub_scaled`/`row_add_scaled_into` emit vector (`.2d`) ops.
- **Bit-identical accuracy gate:** recognizer stays `139/150` (discriminating `assert_true`+`==`,
  never `assert_eq` on Double).
- **GC correctness (B2):** root-elision validated under `SPROUT_GC_STRESS=1` too.
- **Polymorphic fallback:** a `Vector a` access at a polymorphic site still emits the runtime call
  (no mis-inlining of an erased element type).
- **Recognizer kernels are the slice-1 witness** (concrete `Vector Double` in source): expect the
  per-element `vector_get_direct` calls gone from the kernel IR and a modest wall drop (~22→~14
  insns/element; the GC-root calls remain until B2, no SIMD until B3), accuracy still `139/150`.
- **A\* / N-Queens are NO-REGRESSION checks only, NOT win targets** (corrected): both go through
  polymorphic wrappers (`mutvec_get` on `MutVec a`; `Vec Bool`) that B1 cannot reach without an
  inliner. Baselines in `bench/results-2026-07-11.md`. They must not regress; a win there is not
  expected until the inliner/monomorphizer prerequisite exists.
- Existing `tests/stdlib/test_native_mutmatrix.spr` kernel semantics tests must stay green.

## 10. Spec / docs status
**Experimental compiler optimization, not a normative language change.** No `docs/spec-v0.md`
edit. Document the `Vector` layout ABI contract (B1) in-file and in `docs/compiler-internals.md`
alongside the GC ABI invariants; note the root-elision rule there too. Update the perf plan with
Phase D results when it lands.

---

## Recommendation

**Strategic reframing (verified 2026-07-11, corrects earlier turns).** B1's mechanism is sound, but
its *reach* is gated by the absence of an inliner/monomorphizer: B1 only fires on source-level
concrete `Vector Double`, i.e. the hand-written kernels. It does **not** reach idiomatic
wrapper-mediated access (`mutvec_get`, `vec_get_or`, A\*), because the element type is erased inside
the polymorphic wrapper. So there is a fork:

- **(1) Narrow B1-Double now** — inline `vector_get_direct` at concrete `Vector Double` sites. Scope
  it to **Double only** so the self-hosted compiler's own `Vector Int`/`Vector Token` reads are
  untouched → its emitted IR is byte-identical → `verify-bootstrap-fixed-point` holds trivially, and
  the only changed binaries are the recognizer + Double examples. This *proves the inline-load
  mechanism* and gives a modest recognizer win (calls gone; GC-roots remain until B2; no SIMD until
  B3). Low risk, narrow value.
- **(2) Inliner / monomorphizer first** — the higher-leverage prerequisite if the goal is *general*
  Sprout performance. It's what makes B1 (and much else) reach idiomatic code. Larger, separate
  design.

**Recommended:** do (1) as a small, mechanism-proving, fixed-point-safe first commit, *then* weigh
(2) as its own design — because generalizing B1 to `Vector Int`/etc. only pays off once wrapper
inlining exists, and (1) de-risks the codegen/rooting machinery (2) would also rely on.

B1 mechanism details (unchanged): read lowers to inline `load` (rooting is value-kind-driven — add
the new op to the exhaustive classifier, result kind by element type); scalar writes inline, pointer
writes stay at the `vector_mutset` chokepoint. Then the **B3 checkpoint** (do the row-update kernels
vectorize?); hold **A** (Double no-scan store, materiality-gated) and any **SIMD dot reduction**
(changes FP order). The `139/150` accuracy gate holds on every slice.
