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
not to `MutVec`/`MutMatrix` as containers. `MutVec`/`MutMatrix`/`Vec` are all thin wrappers over
`Vector`; a fix at the `Vector` layer lights up every container built on it. The recognizer's
Double kernels are the validation case, not the scope.

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

### B2 — leaf-loop GC-root elision
In a loop body with **no allocation and no call that can trigger GC** between a pointer's root and
its last use, a read-only pointer (`raw_m`, `raw_v`, `raw_dst`) does not need a per-iteration
`push_i64_root`/`pop_roots`. The rooting pass must identify allocation-free regions and skip roots
for values not live across a GC-trigger. This is delicate — it touches the type-aware rooting rules
in `docs/compiler-internals.md` and must be validated against the `SPROUT_GC_STRESS=1` oracle, not
just default-green tests. Note that once B1 makes access inline (no call), the *only* calls left in
the loop are the GC-root ops themselves, so eliding them can make the body call-free — the
precondition for vectorization.

### B3 — loop-shaped codegen (contingency, gated on the B1+B2 empirical checkpoint)
"B1+B2 → LLVM vectorizes" is an **assumption, not a consequence**, and is the first thing to
measure once B1+B2 land. The kernels are tail-recursive `go` functions lowered to a `tco_loop`
with `llvm.stacksave`/`stackrestore` and alloca'd loop state; LLVM may not recognize that shape as
a vectorizable counted loop even after the calls and roots are gone. **Checkpoint:** disassemble a
row-update kernel; if there are no vector (`.2d`) ops, B3 is required — emit the monomorphic Double
kernels as a real counted loop (canonical induction variable, no `stacksave`/`stackrestore`, loop
state in SSA/phis) so LLVM's loop vectorizer engages. B3 is a distinct codegen sub-lever; do not
assume it comes for free.

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
- Existing `tests/stdlib/test_native_mutmatrix.spr` kernel semantics tests must stay green.

## 10. Spec / docs status
**Experimental compiler optimization, not a normative language change.** No `docs/spec-v0.md`
edit. Document the `Vector` layout ABI contract (B1) in-file and in `docs/compiler-internals.md`
alongside the GC ABI invariants; note the root-elision rule there too. Update the perf plan with
Phase D results when it lands.

---

## Recommendation

Approve **B1 + B2** as the Phase D first slice (biggest measured lever, safety-preserving,
bit-identical), scoped to **any monomorphic `Vector T`** — not just Double — because the recognizer
is a proxy and the fix lives at the primitive `Vector` layer, benefiting `Vec`/`MutVec`/`MutMatrix`
and every future `Vector`-backed container:
- **B1 read:** all monomorphic `T` in one slice (rooting is value-kind-driven; add the inlined-read
  op to the exhaustive classifier). **B1 write:** scalar `T` now; pointer-element writes stay at the
  `vector_mutset` chokepoint for the Phase 2 GC barrier.
- **B2:** element-type-agnostic — helps any allocation-free loop.
- Two implementation-time prechecks decide whether pointer-element *reads* also ship in slice 1:
  (1) the element-type kind reaches the typed-lowering site; (2) the `Vector String`-across-alloc
  case is green under `SPROUT_GC_STRESS=1`. If either fails, pointer-element reads become slice 2.

Then hit the **B3 checkpoint** (measure whether the row-update kernels vectorize; if not, add
loop-shaped codegen). Hold **A** (Double no-scan store — GC-precision rationale, materiality-gated)
and any **SIMD dot reduction** (changes FP order) as separately-gated follow-ons. The Double kernels
are the validation case; the `139/150` accuracy gate holds on every slice.
