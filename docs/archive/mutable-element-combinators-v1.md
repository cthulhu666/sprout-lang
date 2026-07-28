# Proposal: Element-level combinators for `MutVec` / `MutMatrix`

- **Status:** Largely implemented in `92db2f0` (9 of the proposed combinators landed; see §4.2 note)
- **Date:** 2026-07-10
- **Scope:** `stdlib.mutable` (pure stdlib addition — **no language change, no new builtins**)
- **Author:** design discussion, digit-recognizer readability thread

---

## 1. Problem statement

Numeric code over mutable containers is the most verbose code in the codebase. The
digit-recognizer example (`examples/digit_recognizer/recognizer.sprout`) is ~2× the line
count of the equivalent Scala/Haskell/Python ports, and after every other readability
improvement (local closures, dropped `_ <-`, interpolation) the dominant remaining driver is
a single construct: **19 standalone lines of the form `wi <- mutvec_get(...)` / `<- mutmatrix_get(...)`.**

The root cause is an API asymmetry, not a language limitation:

| Container | Element-level combinators | Read shape in user code |
|---|---|---|
| immutable `Vec` | `vec_map`, `vec_fold`, `vec_filter`, `vec_filter_map`, `vec_sum`, … | element handed to you; **no `Maybe`** |
| `MutVec` / `MutMatrix` | **none** — only `get`/`set`/`rows`/`cols` | must index via `mutvec_get`, which returns `Maybe a !{IO}` |

Because the mutable API offers *only* indexed access, and indexed access is the one
operation that must return `Maybe a` (bounds safety) **and** `!{IO}` (mutation), every read
in a hot loop becomes its own statement-level `<-` bind. The immutable `Vec` avoids this
entirely by letting you iterate elements instead of indices — the bounds check happens once,
inside the combinator, and the `Maybe` never reaches user code.

This is also relevant to performance: every user-level `mutvec_get` manufactures a `Maybe`
box (the ~71%-of-read-cost the CPR/unboxing work targets). Combinators that own the iteration
keep the number of user-visible `Maybe`s at zero and give the optimizer a single, closed loop
to work on.

## 2. Goals and non-goals

**Goals**
- Make bounds-**safe** iteration over `MutVec`/`MutMatrix` ergonomic — no user-level indexing,
  no user-visible `Maybe`, no per-read bind — while keeping in-place mutation (so performance
  characteristics are unchanged).
- Bring the mutable API to parity with the immutable `Vec` combinator family.
- Pure stdlib: **no new builtins, no syntax change, no type-system change.**
- Preserve the existing safe-by-default policy: OOB never reads garbage.

**Non-goals**
- No unsafe/unchecked (UB-on-OOB) accessor *exposed*. Out of scope by policy. (Combinators use
  the *bounds-checked, trapping* `vector_get_direct` internally — §5.5 — which is memory-safe,
  not the UB-unsafe kind.)
- No *user-facing* trapping index accessor (`v.at(i) -> a`) in this proposal. Orthogonal; can be
  revisited.
- No list-comprehension syntax. Comprehensions are a *separate* layer that would desugar to
  these combinators; this proposal is the foundation they'd sit on.
- No change to `mutvec_get`/`mutvec_set`/`mutmatrix_get`/`mutmatrix_set`. They stay for the
  cases where indexed or fallible access is genuinely what you want.

## 3. Prior-art survey

Element-level iteration is the idiomatic interface for mutable arrays across the ML family and
modern languages; primary-read-returns-`Option`-and-you-index is the exception, not the norm.

| Language | Mutable-array element iteration | Indexed/fallible read |
|---|---|---|
| **Rust** | `slice::iter()`/`iter_mut()`, `.fold()`, `.for_each()`, `.zip()` — idiomatic code iterates, doesn't index | `v[i]` (traps), `v.get(i) -> Option` (secondary) |
| **OCaml** | `Array.iter`, `Array.map`, `Array.fold_left`, `Array.iter2`/`map2` (zip), `Array.iteri` | `a.(i)` (raises), no `option` form in stdlib |
| **Haskell** | immutable `Data.Vector`: `map`/`foldl'`/`zipWith`; mutable `MVector`: `read`/`write` + `freeze`/`thaw` | `!` (partial), `!?` (`Maybe`) |
| **Scala** | `Array`/`ArrayBuffer`: `.map`/`.foldLeft`/`.zip`/`.foreach` | `a(i)` (throws), `.lift(i) -> Option` |

Consensus: mature languages give mutable arrays a full `iter`/`map`/`fold`/`zip` surface and
push the `Option`/`Maybe`-returning read to a secondary role. **Sprout's immutable `Vec` already
follows this consensus; the mutable API is the outlier.** This proposal closes that gap without
adopting the trapping/unchecked reads those languages also ship (which our policy declines).

## 4. Proposed API

All names live in `stdlib.mutable`. Ordering follows the existing `range_fold`/`list_fold`
convention: **callback first, seed next, container last.** Folds take a curried
`step: b -> a -> b` (same shape as `list_fold`/`range_fold`/`vec_fold`).

### 4.1 `MutVec`

```
# O(1). The fixed length of the vector. Pure: length is invariant after creation.
export fn mutvec_len(v: MutVec a) -> Int

# O(n). Visit each element in order for effect.
export fn mutvec_each(f: a -> Unit !{IO}, v: MutVec a) -> Unit !{IO}

# O(n), O(1) extra space. Left fold over elements.
export fn mutvec_fold(step: b -> a -> b !{IO}, init: b, v: MutVec a) -> b !{IO}

# O(min(len a, len c)). Fold over corresponding element pairs (stops at the shorter).
export fn mutvec_zip_fold(step: b -> a -> c -> b !{IO}, init: b,
                          va: MutVec a, vc: MutVec c) -> b !{IO}

# O(n). Replace each element in place with f applied to it.
export fn mutvec_map_inplace(f: a -> a !{IO}, v: MutVec a) -> Unit !{IO}
```

### 4.2 `MutMatrix`

**Whole-matrix ("all cells") — no index of any kind; iterate the flat backing store `0 .. rows*cols-1`.**
These are the "apply a transformation to every cell" family: because the caller supplies no
index, there is nothing to be out of bounds (see §5.5).
```
# O(rows*cols). Fold over every cell in row-major order.  [IMPLEMENTED — 92db2f0]
export fn mutmatrix_fold(step: b -> a -> b !{IO}, init: b, m: MutMatrix a) -> b !{IO}

# O(rows*cols). Set every cell (r, c) to f(r, c). Structured init.  [IMPLEMENTED — 92db2f0]
export fn mutmatrix_fill(f: Int -> Int -> a !{IO}, m: MutMatrix a) -> Unit !{IO}
```

> **Not implemented (future additions).** `mutmatrix_map_inplace(f)`, `mutmatrix_zip_inplace(f, other)`
> (equal-dims checked once), and `mutmatrix_row_fold(step, init, m, r)` were proposed but the
> recognizer does not exercise them, so `92db2f0` deliberately omitted them rather than ship
> speculative, untested API. Add them under the same `_go`-helper pattern when a caller needs the
> whole-matrix `map`/`zip` or a bare row fold. Their intended signatures:
> ```
> export fn mutmatrix_map_inplace(f: a -> a !{IO}, m: MutMatrix a) -> Unit !{IO}
> export fn mutmatrix_zip_inplace(f: a -> b -> a !{IO}, m: MutMatrix a, other: MutMatrix b) -> Unit !{IO}
> export fn mutmatrix_row_fold(step: b -> a -> b !{IO}, init: b, m: MutMatrix a, r: Int) -> b !{IO}
> ```

**Row-structured — still no *user* index (the library iterates rows/cols internally).** Matrix–
vector work (the recognizer's forward/backward pass) is row-shaped, not "all cells", but the
same safety argument holds: every index is library-generated.
```
# O(min(cols, len v)). Fold over row r zipped with vector v.  [IMPLEMENTED — 92db2f0]
export fn mutmatrix_row_zip_fold(step: b -> a -> c -> b !{IO}, init: b,
                                 m: MutMatrix a, r: Int, v: MutVec c) -> b !{IO}

# O(min(cols, len v)). Update each cell of row r in place from (cell, v-element).  [IMPLEMENTED — 92db2f0]
export fn mutmatrix_row_zip_update(f: a -> c -> a !{IO},
                                   m: MutMatrix a, r: Int, v: MutVec c) -> Unit !{IO}
```

The whole-matrix family serves general elementwise matrix work (scale, add, hadamard, sum,
norm, activation). The recognizer specifically drives the row-structured family (dot products →
`row_zip_fold`, SGD updates → `row_zip_update`) plus `mutmatrix_fill` for LCG init and
`mutvec_map_inplace` for activations. The set is deliberately small; more (`mutmatrix_col_fold`,
`mutvec_zip_each`, …) can follow the same pattern as needs arise.

Note the row index `r` in the row-structured functions is validated once (`0 <= r < rows`)
before iterating that row — a single check, not one per cell.

### 4.3 Semantics

- **Bounds:** every combinator iterates a statically valid index range (`0 .. len-1`), so no
  read is ever out of range. Internally reads use `vector_get_direct` (§5.5), which returns the
  element directly (no `Maybe`) and traps on OOB — a trap that, by construction, never fires.
  Safety is preserved; the `Maybe` never exists.
- **Zip length:** `MutVec` `zip`-family combinators stop at the shorter operand (Rust/OCaml
  `iter2` precedent, except OCaml which requires equal lengths — we choose the more permissive
  stop-short). `mutmatrix_zip_inplace`, however, **requires equal dimensions** and verifies them
  once before iterating: a shape mismatch would otherwise let iteration over `m`'s extent read
  past `other`'s. Mismatch is rejected (a no-op / diagnostic — decided at implementation), not
  silently truncated.
- **Order:** strictly left-to-right / ascending index, matching `mutvec_get` iteration order, so
  fold results are bit-identical to the current hand-written loops (important: the recognizer
  must still reach 89.33%).

## 5. Key design decisions

1. **Effect typing — `!{IO}` concrete, not row-polymorphic.** The combinator body does IO
   (`vector_get_direct`), and Sprout has no precedent for a fixed-label-plus-poly-tail row (`!{IO | e}`
   appears nowhere in stdlib). **Verified:** a *pure* step (`\(acc, x) -> acc + x`) unifies
   cleanly into an `!{IO}` step parameter, so the common case (pure arithmetic callbacks) works
   with a concrete `!{IO}` signature. Cost: a callback that wants to carry a *non-IO* effect
   can't be expressed. That's acceptable for v1 (numeric callbacks are pure or IO); see §11.
2. **Fold arg convention — curried `b -> a -> b`.** Matches `range_fold`/`list_fold`/`vec_fold`.
   Callers write `\(acc, x) -> …` (which is two curried params, the established multi-arg lambda
   form).
3. **Data-last ordering.** Callback, then seed, then container — consistent with the existing
   iteration family and pipe-friendly.
4. **Add `mutvec_len`.** The combinators need the length; `MutVec` currently exposes none
   (only `MutMatrix` has `rows`/`cols`). It's independently useful and pure.
5. **Internal read primitive — `vector_get_direct`, not `vector_get`.** The combinators read
   with `vector_get_direct(raw, i) -> a !{IO}` (an existing extern; runtime `sprout_runtime.c`),
   which returns the element **directly with no `Maybe` box** and traps (`tcp_fail`) on OOB —
   *not* `vector_get`, which allocates a `Maybe`. This eliminates the per-read box (the
   ~71%-of-read-cost) inside the safe API, **without a new builtin**. **Verified:** a fold over a
   `Vector` via `vector_get_direct` compiles and runs (`fold (+) 0 [7,7,7]` → `21`); the read
   binds `val : a` directly (no `Maybe`, so no do-bind strip and no occurs-check pitfall).

### 5.5 Safety model — unchecked-level speed without `unsafe`

The design goal was to get the performance of out-of-bounds-*unsafe* access without exposing
unsafety. The key property that makes this sound:

> **No combinator accepts an index from the caller.** Every index is *library-generated* over
> the container's true extent (`0 .. len-1`, `0 .. rows*cols-1`, a validated row). It is
> therefore provably in-bounds by construction — the caller has no way to supply a bad index,
> hence no way to trigger a fault.

This is the standard *safe-abstraction-over-unsafe-core* pattern (Rust's `slice::iter`/`map`/
`fold` build on `get_unchecked`; Haskell's `Data.Vector` fused combinators use `unsafeIndex`).
The unsafety is *encapsulated*: it lives only where the library controls and can prove the index.

**Two tiers, and why we stop at the first:**

- **Checked-but-trapping (`vector_get_direct`) — chosen.** Returns `a`, no box, traps on OOB.
  The bounds check is one comparison + a branch that, in a `0 .. len-1` loop, is *perfectly
  predicted and effectively free*. The expensive thing was the `Maybe` **allocation**, not the
  **branch** — and this removes the allocation. Memory-safe: a library off-by-one is a clean
  trap, never UB.
- **Truly unchecked (a hypothetical `vector_get_unchecked`) — deferred.** Removing even the
  predicted branch is *sound* under the no-index invariant above, but it would (a) require a new
  genuinely-unsafe builtin (approval + scrutiny) and (b) turn any library off-by-one from a
  clean trap into undefined behaviour. The marginal gain is one predicted branch per access.
  **Not worth it absent a profile showing the branch matters** — see §11.

Net: the no-index API + `vector_get_direct` captures essentially all of the "unsafe" performance
(no box) while remaining fully memory-safe and needing no new builtin. The trap is a safety net
that, given the invariant, never fires.

## 6. Implementation notes (verified constraints)

Two prototypes were compiled and **run**: a `mutvec_fold` over `mutvec_get` (`fold (+) 0
[4,4,4]` → `12`) and a fold over `vector_get_direct` (`fold (+) 0 [7,7,7]` → `21`). Three
constraints were confirmed empirically and must be honoured:

- **Invoke the step n-ary, never curried.** `step(acc, x)` works; `step(acc)(x)` SIGSEGVs at
  runtime (the open currying/partial-application defect #2). The existing `range_fold_go`/
  `list_fold_go` already call `step(acc, current)` — follow that exactly.
- **Read with `vector_get_direct(raw, i)`** (§5.5). It returns `a !{IO}` directly, so
  `val <- vector_get_direct(raw, i)` binds `val : a` with **no `Maybe`** involved — cleaner than
  the `vector_get` path (no Maybe-strip, no `Just/Nothing` match, no occurs-check pitfall).
  Obtain `raw` via `mutvec_raw` (private to `stdlib.mutable`, so the combinators must live in
  that module).
- **No new builtins.** Implementations use only existing primitives — `vector_get_direct`,
  `vector_mutset`, `vector_length`, `vec_make_filled` (all shared with the runtime) — plus the
  private `mutvec_raw`. This holds for both the checked-Maybe path and the chosen direct path.

Each combinator is a tail-recursive `_go` helper (index-threaded) wrapped by the exported
data-last entry point — structurally identical to `vec_fold_indexed` / `range_fold_go`.

## 7. Worked example — the recognizer's inner dot product

**Now** (indexed; two manufactured `Maybe`s, a bind per read, 4 lines):
```
let dot = \(acc, i) -> do
      wi <- mutmatrix_get(w1, h, i)
      xi <- mutvec_get(x, i)
      acc + wi * xi
z <- range_fold(dot, b, range(0, n_in - 1))
```
**With this proposal** (element-level; no index, no `Maybe`, no per-read bind, 1 line):
```
z <- mutmatrix_row_zip_fold(\(acc, wi, xi) -> acc + wi * xi, b, w1, h, x)
```
The SGD weight update collapses the same way to `mutmatrix_row_zip_update`, activation to
`mutvec_map_inplace`, and LCG init to `mutmatrix_fill` — eliminating **every** `mutvec_get` /
`mutmatrix_get` in the file while keeping all mutation in place.

## 8. Impact

- **Syntax:** none.
- **Semantics:** none (additive library functions).
- **Type system:** none new; uses existing generics + effect-polymorphism-into-`!{IO}`.
- **Error messages:** strictly fewer failure modes for users — combinators cannot produce an
  OOB `Nothing`, so the "handle the `Maybe`" obligation disappears at call sites.
- **Performance:** positive. Same operation count as the hand-written loops, but **zero `Maybe`
  allocations on the read path** — the internal `vector_get_direct` returns the element directly
  (§5.5), so the per-read box that dominates current `mutvec_get` cost is gone. Each combinator is
  also a single closed loop the optimizer can see end-to-end. This is independent of, and
  complementary to, the CPR/unboxing effort: it removes the box at the source rather than
  unboxing it downstream.

## 9. Compatibility & migration

Purely additive. `mutvec_get`/`set` and `mutmatrix_get`/`set` are unchanged and remain the
right tool for genuinely fallible or random access. Migration of existing call sites (e.g. the
recognizer) is optional and mechanical; it must preserve fold order so numeric results are
bit-identical (recognizer stays at 89.33%, 134/150).

## 10. Tests

Per `guidelines.md` / DoD, add `tests/stdlib/` coverage before implementing (TDD):
- Per combinator: empty container, single element, multi-element, and result-order check.
- `zip`-family: equal lengths, and unequal lengths (confirm stop-at-shorter).
- `map_inplace` / `row_zip_update`: confirm the container is mutated and the return is `Unit`.
- Whole-matrix family: `mutmatrix_map_inplace` / `mutmatrix_fold` over a known matrix;
  `mutmatrix_zip_inplace` with equal shapes (correct result) **and** mismatched shapes
  (confirm the rejection path, not a silent OOB).
- `mutmatrix_fill`: confirm each cell equals `f(r, c)`.
- **Integration:** rewrite the recognizer against the new API and assert final accuracy is
  unchanged (89.33%) — the regression guard for fold-order fidelity.
- Complexity annotations on every new export (per prelude-perf-docs guideline).

## 11. Risks & open questions

- **Effect polymorphism (deferred).** v1 fixes callbacks to `!{IO}`. If we later want callbacks
  carrying additional effects, we need fixed-label-plus-poly-tail rows (`!{IO | e}`) — an
  effect-system feature that doesn't exist today. Out of scope here; flagged for the effects work.
- **Currying defect coupling.** Correctness depends on invoking the step n-ary. This is already
  how the shipped combinators work, so the risk is "follow the existing pattern," not new
  exposure — but it's a reminder that the open currying decision touches this area.
- **Truly-unchecked reads (deferred, see §5.5).** A `vector_get_unchecked` builtin used only
  inside no-index combinators would be *sound* (the no-index invariant proves every access valid)
  and would shave the predicted bounds branch. Deferred because it needs a new genuinely-unsafe
  builtin (approval) and converts library off-by-ones from clean traps into UB, for a gain that
  is one predicted branch per access. Revisit only if a profile shows the branch is material.
- **API surface creep.** Keep v1 to the whole-matrix + recognizer-driven row set; grow only on
  demonstrated need (`mutmatrix_col_fold`, `mutvec_zip_each`, …).

## 12. Docs/spec

`stdlib.mutable` is library, not normative `spec-v0` core, so no spec change. Update:
- Module-level doc comment in `stdlib/mutable.sprout` describing the combinator family and the
  "iterate, don't index" idiom.
- If a stdlib reference / examples note exists, add the recognizer before/after as the motivating
  example.

## 13. Rollout

1. Land `mutvec_len` + tests.
2. Land the `MutVec` combinators (`each`/`fold`/`zip_fold`/`map_inplace`) + tests.
3. Land the `MutMatrix` combinators — whole-matrix (`map_inplace`/`zip_inplace`/`fold`/`fill`)
   and row-structured (`row_fold`/`row_zip_fold`/`row_zip_update`) + tests.
4. Migrate `recognizer.sprout` to the combinators as the integration test; confirm 89.33%.
5. (Later, separate proposals) comprehension sugar over these; revisit trapping `at` accessor if
   ever wanted.
