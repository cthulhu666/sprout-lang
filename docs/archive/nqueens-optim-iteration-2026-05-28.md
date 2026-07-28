# N-queens Optimization — Bottleneck Analysis & Next Iteration

**Date:** 2026-05-28
**Target:** Match Haskell UArray (~108 ms N=12, ~559 ms N=13)
**Method:** Profile first, then targeted fixes. No guessing.

---

## Status Summary (handoff for next session)

| Step | Status | Measured impact |
|---|---|---|
| **P0** Type-aware GC rooting | ✅ **Done 2026-05-28** | **1.5–2.7× speedup** (N=12: ~1.5 s → 928 ms) |
| **P1** Inline push/pop into IR (or `-flto`) | ⏳ **Next** | Targets remaining ~44% push/pop CPU share |
| **P2** Skip re-push of already-rooted params | ⏳ Open | 20–40% on top of P0/P1 |
| **P3** True/False/Nil singletons | ⏳ Open | 10–15% |
| **P4** Bump-allocated nursery (real gen GC) | ⏳ Open | Large, only after P0–P2 |
| **P5** HAMT vec_set | Deferred | Not the bottleneck at N≤14 |
| **P6** MutArray Bool builtin (API change) | Deferred | Needs user approval first |

**Current gap to Haskell UArray after P0:** ~9× (N=12: 928 ms vs 108 ms; N=13: 5,700 ms vs 559 ms). The 44% CPU still in push/pop is now genuine heap-pointer rooting that can't be filtered by type — needs inlining.

**Side discovery (file separately):** `tests/stdlib/test_json.spr` fails with `runtime error: non-exhaustive match` at startup. **Verified pre-existing** — reproduces identically with P0 stashed. Not a regression. Likely an unrelated json-runtime / json-test bug worth investigating; does not block P0.

**Environment note for next session:** `just bootstrap-from-seed` and `just _test-stdlib` invoke `opt` (LLVM IR verifier). On macOS it's at `/opt/homebrew/opt/llvm/bin/opt` and is NOT on PATH by default — prepend `PATH="/opt/homebrew/opt/llvm/bin:$PATH"` to the command, or use `mise` to manage llvm. The `mise.toml` entry was reverted in commit `49e9dd2` because `asdf-llvm` builds from source; clarify the install story before next session.

---

## Fresh Baseline (Apple M1, freshly rebuilt nqueens_sprout)

| N | Sprout | Go pure | Haskell UArray | Gap to target (HsUArray) |
|---|---:|---:|---:|---:|
| 8  | 2.7 ms | 0.15 ms | 0.2 ms | 14× |
| 10 | 58 ms | 3.5 ms | 4.6 ms | 13× |
| 12 | **1,372–2,588 ms** (noisy) | 64 ms | 108 ms | **13–24×** |
| 13 | **8,234–8,880 ms** | 377 ms | 559 ms | **15×** |

The README's ~1,620 ms post-CPR figure is roughly the low end of current variance. Run-to-run noise of 80% is itself a signal — confirms heap/GC-driven memory pressure.

Full run: 11.7 s wall, **159 billion instructions retired**, 5 MB peak RSS.

## Allocation Profile (`SPROUT_DEBUG_ALLOC=1` full run)

```
sprout_obj=33,412,698   (Vec ctor wrappers + True/False/etc literals)
vector=33,412,602       (counter incremented by both VectorVal + data array)
closure=0  map=0  bytes=0  builder=0
gc_swept=50,119,044     (~75% of allocations were freed)
```

≈ **66.8 M allocations** across the full run. 159B instructions / 66.8M allocations = **2,400 instructions per allocation** — confirms per-allocation overhead is the dominant cost class. 75% sweep rate means generational GC has real headroom.

## CPU Sample Profile (macOS `sample`, 1 ms interval, 8 s window)

Leaf-of-stack distribution (the "Sort by top of stack" section of the sample report):

| Function | Samples | % of CPU | Notes |
|---|---:|---:|---|
| **`sprout_gc_push_i64_root`** | **2,825** | **~42%** | Per-temporary root push |
| **`sprout_gc_pop_roots`** | **1,697** | **~25%** | Per-temporary root pop |
| `queens` (user code) | 303 | 4.5% | The actual algorithm |
| `is_free` (user code) | 222 | 3.3% | Algorithm leaf |
| `madvise` (kernel) | 185 | 2.7% | malloc returning pages to OS |
| `_nanov2_free` | 183 | 2.7% | Free path |
| `vec_get_or` | 149 | 2.2% | Stdlib accessor |
| `sprout_gc_collect_with_reason` | 140 | 2.1% | GC cycles themselves |
| `vec_get_worker` | 92 | 1.4% | CPR worker |
| `vector_get_unboxed` | 90 | 1.3% | CPR extern |
| `_platform_memset` | 80 | 1.2% | malloc bookkeeping |
| `vector_set` | 72 | 1.1% | The O(n) Vec copy |
| `_free` | 70 | 1.0% | Free path |
| `sprout_make_registered_obj` | 68 | 1.0% | The ADT alloc itself |

**Key finding: ~67% of CPU is in `sprout_gc_push_i64_root` + `sprout_gc_pop_roots`** — the per-temporary GC rooting that wraps every heap-valued expression. This is the single biggest bottleneck, by a huge margin. It is NOT what the docs previously identified (which said `register_managed_ptr` was the bottleneck — wrong, only ~1%).

## Why root push/pop dominates

Codegen emits, for every heap-valued temporary in a call expression
(`stdlib/compiler/codegen.sprout:873-908`):

```llvm
%slot = alloca i64
store i64 %val, ptr %slot
%reg = call i64 @sprout_gc_push_i64_root(ptr %slot)      ; ← function call
...                                                       ; the actual work
%reg2 = call i64 @sprout_gc_pop_roots(i64 N)             ; ← function call
```

Each push/pop is an opaque **external call** into the C runtime. LLVM cannot inline across the boundary without LTO, so each call costs:

- Caller-save register clobber
- Branch to runtime (instruction-cache miss probability)
- 3–5 memory writes inside the runtime body
- Return

Estimate: ~30 ns per push (≈100 cycles on M1). With ~134 M push/pop pairs over the full run (2× per allocation × 67M allocs), this is ~4 seconds — matching the 67% observation.

The static root pool at `runtime/sprout_runtime.c:617` was added to skip *malloc* in the lexer hot path, but the **call boundary itself** was left unaddressed.

## Next Iteration Plan (ranked by leverage)

### ✅ P0 — Type-aware rooting: skip push/pop for non-heap-typed values [DONE 2026-05-28]

**Status: shipped (uncommitted in working tree). Measured 1.5–2.7× speedup on N-queens. See below for results.**

This was the highest-leverage fix and the cheapest to implement. Verified by reading the emitted IR for `queens` (`/tmp/nqueens.ll:9005-9133`).

The `push_temp_root` check at `stdlib/compiler/codegen.sprout:874` uses `val_is_i64(v)` to decide whether to push. But Sprout's `Int` is also represented as `i64` in LLVM — so the heuristic conflates Int (never a heap pointer) with boxed ADT (always a heap pointer). The codegen unconditionally pushes roots for Int parameters and Int call arguments.

Concrete evidence from `@examples.nqueens.queens`:

- **Function entry** pushes 6 roots — 3 are Int (`n`, `row`, `col`), 3 are Vec (`cols`, `pos_diag`, `neg_diag`). **50% wasted.**
- **Recursive skip-branch call** pushes 6 args — 3 are Int, 3 are Vec. **50% wasted.**
- **`is_free` call** pushes 6 args — 3 are Int, 3 are Vec. **50% wasted.**
- **Before `vec_set`** pushes 5 args of which 4 are Int. **80% wasted in this site.**
- IR line 9091 even pushes a root for the literal constant `0` — `store i64 0` then `push_i64_root`. Constants can never need rooting.

Overall ~40–50% of push/pop pairs in `queens`/`is_free` are pure waste.

**Fix:**
1. Thread the typed AST's source-level type through `push_temp_root` (callers already have it from `typed_expr_type`).
2. Push only for types that are heap-allocated: ADT constructors (`Maybe`, `Result`, `List`, `Vec`, `Dict`, user data types), closures, strings, bytes, builders. Skip for `Int`, `Bool`, `Char`-as-codepoint, and any constant value (no allocation = no rooting).
3. The pop side already takes a count; just emit a smaller count.

Expected savings: 30–40% of total CPU time (since push/pop is 67% and ~50% of that is wasted).
Estimated work: 2–4 hours (single function in codegen + a few callers' type plumbing).

**Actual implementation (2026-05-28):**

Files changed (uncommitted, in working tree):
- `stdlib/compiler/codegen.sprout` (+74 / −22 lines): added `type_is_non_heap_scalar` and `type_expr_is_non_heap_scalar` predicates + `push_temp_root_typed` helper. Threaded source-level types through call-site rooting (`emit_args_with_roots`, `emit_args_with_roots_lls`, `emit_tco_args`, `emit_tuple_items`, `emit_do` TDoLetStep). Added `build_param_locals_and_push_roots` that consults `ast.Param` annotations for function-entry rooting. Updated `emit_pattern_bind`, `load_lambda_params`, `allocate_tco_slots_acc`, `emit_fn_worker`.
- `tests/stdlib/compiler/test_codegen.spr` (+18 lines): 3 regression tests pinning the invariant.

**Measured impact at N=12 in `@examples.nqueens.queens`:**
- Function entry: 6 pushes → **3 pushes** (skip `n`, `row`, `col`)
- Recursive skip-branch call: 6 → **3** (only Vec args)
- `is_free` call: 6 → **3**
- Before `vec_set(col, true, cols)`: 5 → **1** (only `cols`)

**Benchmark (Apple M1):**

| N | Before | **After** | Speedup |
|---|---:|---:|---:|
| 8  | 2.7 ms | **1.8 ms** | 1.5× |
| 10 | 58 ms | **35–45 ms** | 1.5× |
| 12 | 1,372–2,588 ms | **928 ms** | 1.5–2.8× |
| 13 | 8,234–8,880 ms | **5,700 ms** | 1.5× |

Total instructions: **159 B → 114 B (−28%)**. Wall time (full run): **11.7 s → 6.85 s**.

**Test status:** 21/21 codegen tests pass. Single failure (`test_json.spr` non-exhaustive match) verified pre-existing.

**Remaining work:** `compile-examples-stage1` not re-verified in this session — run before committing.

### P1 — Inline GC root push/pop (eliminates remaining push/pop overhead)

Two implementation paths, in order of preference:

**Option A (LLVM IR inlining): emit push/pop directly as IR instead of as runtime calls.**
A pure i64 root stack with no kind/aux fields:

```c
static void* g_i64_root_slots[131072];
static long long g_i64_root_top;
```

Codegen emits (3 IR instructions instead of a call):

```llvm
%top = load i64, ptr @g_i64_root_top
%addr = getelementptr ptr, ptr @g_i64_root_slots, i64 %top
store ptr %slot, ptr %addr
%new_top = add i64 %top, 1
store i64 %new_top, ptr @g_i64_root_top
```

Pop is even cheaper (just decrement `g_i64_root_top` by `N`).

Mark-roots walks `g_i64_root_slots[0..g_i64_root_top]`. SCAN roots (tuples) and PTR roots can keep the original `RootNode` machinery — they're cold.

Expected savings on N-queens: 5–15× depending on how aggressively the alloca/load/store reorder. Likely lands at **150–400 ms N=12** — close to Haskell UArray range.

**Option B (LTO): compile runtime.c with `-flto` and clang-link with `-O2 -flto`.**
Lets LLVM inline `sprout_gc_push_i64_root` directly. Cheaper to implement (just toolchain change) but won't fully match Option A because the runtime is more complex than the minimum-viable push (it has the kind/aux fields and bounds checks). Worth measuring first; could be a 2–3× win with no code change.

**Recommendation:** Try Option B first (an afternoon's measurement). If it lands within 30% of the Option A target, ship it. Otherwise do Option A.

### P2 — Eliminate redundant root push/pop for already-rooted values (multiplicative with P0/P1)

Currently `emit_args_with_roots` pushes a root for EVERY argument of a call, even when the argument is:
- An immediate (Int literal, register-only value)
- A parameter that's already rooted by the caller
- A pure expression that doesn't allocate

The simplest fix: skip pushing roots for already-rooted local variables. The codegen knows when a value is a `TVar` that resolves to a function parameter; those parameters are already roots in the caller's frame. The recursive `queens(n, row, col+1, cols, pos_diag, neg_diag)` call re-roots `cols`, `pos_diag`, `neg_diag` even though they're the same values the current frame already has rooted.

Expected savings: 20–40% on top of P0/P1 (the recursive case is the hot path).

### P3 — True / False / Nil singletons (parallel to existing Nothing singleton)

`runtime/sprout_runtime.c:3156` caches `Nothing` to a singleton; do the same for `True`, `False`, `Nil`. Every `vec_set(col, true, cols)` and every `false` literal in `is_free` will dedupe to a constant pointer. Eliminates ~16 M sprout_obj allocations.

Expected savings: 10–15% (allocation count drops; rooting count drops proportionally — multiplicative with P0+P1+P2).

### P4 — Generational GC with bump-allocated nursery (NOT the existing draft)

The existing `docs/archive/generational-gc-v1-draft.md` keeps per-object `ManagedNode` metadata (just splits the heap list) and explicitly notes its ceiling is 10–20%. That ceiling holds because per-alloc cost is unchanged.

A real generational GC needs a **bump-allocated nursery**: a fixed-size arena where allocations are `arena_top += size; *arena_top = obj;` with no `ManagedNode`, no hash insert, no GC threshold check. On minor GC, surviving objects are copied to the old gen and get full ManagedNode metadata there.

Expected savings: large for allocation-heavy workloads — bump alloc is ~5 cycles vs ~50 for malloc+register_managed_ptr. But ONLY meaningful AFTER P0+P1 have eliminated the push/pop overhead, since push/pop dominates today.

### P5 — Persistent vector (HAMT-style) for `vec_set`

At N=12, vectors are 12 and 23 elements — `vector_set`'s O(n) copy is only ~32 i64 memcpy per call. The CPU profile shows `vector_set` at 1.1% + `_platform_memmove` at 0.4% = ~1.5%. Path-copying HAMT is roughly the same work at this size (single leaf node). Don't bother at N≤14; revisit if benchmarking larger N.

### P6 — Mutable array builtin (`MutArray Bool`)

Adds an opt-in public API for in-place mutation. Matches Go-mutable / Haskell ST. Useful for closing the last 2–3× gap if P0–P3 leave residual gap. Requires an API design discussion before implementing (per `feedback_ask_before_acting`).

## What we explicitly are NOT doing

- **Not rewriting the algorithm to use bitmasks.** That's a 90× win but would not measure language performance — it would measure whether the user wrote a different program. The point of this benchmark is the SAME algorithm in different languages.
- **Not chasing `register_managed_ptr` overhead alone.** Profile says 1%, not 67%. The old docs were wrong about this.
- **Not implementing the existing generational-GC draft as-is.** Its ceiling (10–20%) leaves the actual bottleneck untouched.

## Suggested order of work (next session)

1. ✅ **P0 done.** Skip — already shipped, see above.
2. **Try P1 Option B (LTO) first.** One-line toolchain change: pass `-flto` to clang on both runtime.c and the emitted .ll. If LTO inlines `sprout_gc_push_i64_root` across the boundary, we may approach Haskell parity for free. Measure first; estimated 1–2 hours.
3. **If LTO doesn't suffice: P1 Option A.** Two files: runtime adds the slim i64 root stack (no kind/aux fields); codegen `push_temp_root` emits inline IR instead of a call. Estimated 1–2 days.
4. **P2 redundant-root elimination.** Pure codegen change. A few hours.
5. **P3 True/False/Nil singletons.** Mirror existing `Nothing` cache in `sprout_make0` (`runtime/sprout_runtime.c:3156`). A few hours.
6. **Re-measure after each step.** If we're within 1.5× of Haskell, ship. If still >3×, move to P4.

After each step, repeat the `sample` profile to confirm the bottleneck shifted and identify the next biggest contributor. Do **not** implement multiple steps before re-profiling — the leaderboard reshuffles as each fix lands.

## Post-P0 CPU profile (re-measured 2026-05-28)

`sample` profile, 6 s window, leaf-of-stack "Sort by top of stack" section:

| Function | Before P0 (%) | **After P0 (%)** |
|---|---:|---:|
| `sprout_gc_push_i64_root` + `sprout_gc_pop_roots` | 67% | **~44%** |
| `queens` + `is_free` (user code) | 7.8% | ~8% |
| malloc/free family (`_nanov2_free`, `madvise`, `_platform_memset`, `_free`, etc.) | 7.5% | **~10%** |
| Vec access (`vec_get_*`, `vector_get_unboxed`, `vector_set`) | 4.4% | ~6% |
| `sprout_gc_collect_with_reason` (GC cycles) | 2.1% | ~2.5% |
| `sprout_rebox2` + `sprout_make_registered_obj` | 2.0% | ~3% |

The remaining push/pop pairs are for genuine heap pointers (Vec args) — type filtering can't help further. Next attack target: the **function-call boundary** itself (P1 inlining or LTO), which is the per-push cost driver.

## Reproducing this analysis

```bash
# Rebuild the benchmark binary
just compile-native examples/nqueens.sprout bench/nqueens/bin/nqueens_sprout

# Allocation count
SPROUT_DEBUG_ALLOC=1 bench/nqueens/bin/nqueens_sprout

# CPU sample (background + sample by PID)
bench/nqueens/bin/nqueens_sprout > /tmp/nq_out.txt &
sample $! 8 1 -file /tmp/nqueens_profile.txt
# Read the "Sort by top of stack" section at the end of the file
```
