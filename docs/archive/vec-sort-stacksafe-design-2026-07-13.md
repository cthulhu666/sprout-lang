# Design: stack-safe `vec_sort` (fix the ~50–60k crash)

Status: **proposal** — awaiting approval before implementation. Non-normative.

## 1. Problem statement

`vec_sort` / `vec_sort_by` (`stdlib/prelude.sprout`) crash on inputs larger than
~50–60k elements:

```
runtime error: builtin `sprout_gc_push_root`: GC root pool exhausted
```

Measured on a descending `Vec Int` built in O(n) (so the input construction is not
the cost):

| n | result |
|---|--------|
| 20,000 | ✓ (~20–40 ms) |
| 40,000 | ✓ (~68 ms) |
| 60,000 | **crash** |
| 100,000 | **crash** |

This is a hard correctness ceiling, not a slowdown: a program that sorts a
moderately large `Vec` aborts. It affects **every** key type (the crash is in the
generic `Ord k` path, not anything Int-specific).

## 2. Root cause

The merge sort is top-down (`decorated_sort` → `decorated_sort_split` →
`decorated_merge_sorted`). Three of its helpers are **non-tail-recursive**, so their
call depth is O(n), and each stack frame pins GC roots for its live heap locals. The
GC temp-root pool is a **fixed 131,072-slot array** with no runtime override
(`SPROUT_ROOT_POOL_SIZE`, `runtime/sprout_runtime.c:1030`). At ~50–60k elements the
accumulated live frames exhaust it. (40k × ~3 roots/frame ≈ 120k < 131072; 60k
overflows — matching the observed threshold.)

The O(n)-depth culprits, verified by reading the source:

| helper | shape | depth |
|--------|-------|-------|
| `list_take` (`prelude:426`) | `Cons(item, list_take(rest, k-1))` | O(k), up to n/2 |
| `decorated_merge_sorted` (`prelude:448`) | `Cons(x, decorated_merge_sorted(…))` | O(run len), up to n |
| `decorated_values_to_list` (`prelude:474`) | `Cons(value, decorated_values_to_list(rest))` | O(n) |

Non-culprits (already safe): `decorated_sort`’s split recursion is O(log n) depth
(~17 for 100k); `list_drop`, `list_reverse_go`, `list_length_go` are already
tail-recursive.

A secondary inefficiency in the same code: `decorated_sort` recomputes
`list_length(xs)` (O(n)) at every recursion node and re-traverses with
`list_take`/`list_drop`, adding an extra O(n log n) of pure bookkeeping and
allocation on top of the sort.

## 3. Goals / non-goals

**Goals**
- `vec_sort` / `vec_sort_by` sort arbitrarily large `Vec`s (bounded only by heap),
  with no recursion-depth ceiling.
- Preserve current semantics exactly: stable, `Ord k`-generic, same result order.
- Stay pure Sprout — no new builtin. (A C `qsort` builtin cannot call back into
  Sprout `Ord`, and the removed `vector_sort_by_int` was an Int-only temporary
  helper, already retired; reviving it would not fix the general case.)

**Non-goals**
- Changing the `vec_sort_by` signature or stability contract.
- Fixing `range_to_vec`’s O(n²) (`vec_append` in a fold) — real but separate;
  tracked below.
- Beating a native sort on constant factors; O(n log n) pure Sprout is the target.

## 4. Prior art

Stack-safe list sorting in functional stdlibs is uniformly **merge sort with bounded
stack** — GHC’s `Data.List.sort` and OCaml’s `List.sort`/`Stdlib.List.sort` are both
merge sorts that do not recurse to O(n) depth. The failure here is not the algorithm
choice (merge sort is correct) but that three helpers were written in a
non-tail-recursive style. Two established ways to make it stack-safe:

- **Bottom-up (iterative) merge sort**: seed one run per element, repeatedly merge
  adjacent runs until one remains. Outer passes and the merge are loops/tail-recursion.
- **Top-down with a tail-recursive merge**: keep the O(log n) split recursion but
  make the merge (and the final flatten) tail-recursive with an accumulator + one
  reverse.

## 5. Proposed options

### Option A — targeted: make the three culprits tail-recursive (minimal diff)

Keep the top-down structure (its split recursion is only O(log n) deep, which is
safe). Rewrite exactly the three O(n)-depth helpers:

- `decorated_merge_sorted` → a tail-recursive `merge_go(left, right, acc)` that pushes
  the chosen element onto `acc` (built in reverse), then finishes with
  `list_reverse_go(acc, remaining)` (= `reverse(acc) ++ remaining`, itself tail). Tie
  rule unchanged (`key_lt(right, left)` strict ⇒ take right, else left) so it stays
  stable; the decoration’s original-index tiebreak also guarantees a total order.
- `decorated_values_to_list` → tail-recursive with an accumulator + one final reverse.
- `list_take` → tail-recursive (`list_take_go` with accumulator + reverse), or replace
  the split with a single O(n) `split_at` pass returning `(front, back)` to avoid the
  separate `list_length`/`take`/`drop` re-traversals.

Depth after the fix: O(log n) from the split recursion + O(1) per merge/flatten. No
root-pool pressure at any n.

- Pros: small, low-risk, obviously preserves behavior.
- Cons: leaves the O(n log n) `list_length`-per-node bookkeeping unless `split_at` is
  also adopted; still three separate touch points.

### Option B — root-and-branch: bottom-up iterative merge sort (recommended)

Replace `decorated_sort` / `decorated_sort_split` / `list_take` / `list_drop`
/ per-node `list_length` with a bottom-up merge:

1. Decorate + collect once (existing `vec_sort_by_collect`, already tail) → `List (k,Int,a)`.
2. Turn it into a list of singleton runs.
3. Tail-recursive pass: merge runs pairwise (each merge is the tail-recursive stable
   merge from Option A) → half as many runs, each twice as long. Repeat until one run.
4. `vec_from_list` of the final run’s values (tail-recursive flatten).

- Pros: O(1) split/pass depth by construction; also removes the redundant O(n log n)
  `list_length`/`take`/`drop` bookkeeping and its allocation — a real constant-factor
  win on top of the crash fix. One clear structure.
- Cons: larger diff; more to review.

**Recommendation: Option B.** It fixes the root cause *and* the associated
inefficiency, and matches how mature stdlibs implement this. Option A is the fallback
if a minimal, obviously-behavior-preserving patch is preferred.

## 6. Type-system / syntax / error-message impact

None. Same `vec_sort_by(f: a -> k, vec: Vec a) -> Vec a where Ord k` signature, same
stability, same diagnostics. Purely an internal algorithm change.

## 7. Compatibility / migration

None. Observable behavior is identical for all currently-working inputs; previously
crashing inputs now succeed.

## 8. Tests (TDD)

Add before implementing; the first must fail (crash) on today’s code
(`tests/stdlib/test_vec_sort_stacksafe.spr`):

1. **Regression / crash gate** — sort a `Vec Int` of ~100k built in O(n)
   (`vec_from_list` of a tail-built list; **not** `range_to_vec`), assert the result
   is fully sorted (length, first/last, tail-recursive monotonicity scan). Aborts
   with root-pool exhaustion on the old code; passes after. **Landed.**
2. **Small cases** — empty, singleton, already-sorted, reverse-sorted, all-equal.
   **Landed.**
3. **Stability** — LANDED 2026-07-13 with the dispatch fix. A stability test needs
   `(key, tag)` pairs sorted by `key`, i.e. `vec_sort_by` with **key type ≠ element
   type**. That path hit a *pre-existing* `Ord`-dict dispatch bug (crashed on both
   the old and new sort; the uncovered sibling of the #141 `scan_fwd_markers` fix),
   now fixed via `canonicalize_constrained_markers` in `infer.sprout` (see
   BACKLOG.md). Stability is preserved by construction (`decorated_key_lt`’s
   (key, index) total order is byte-for-byte unchanged); the coverage now lives in
   `tests/stdlib/test_vec_sort_projection.spr` (element ordering + stability).

Run under `SPROUT_GC_STRESS=1` as well, since the change alters allocation/rooting
shape in a hot recursive path.

## 8a. Implementation note (bottom-up TCO verified)

The three merge helpers are `where Ord k` and self-recursive in tail position; the
compiler’s TCO must apply or they’d recurse O(n) deep and defeat the whole fix. IR
inspection confirms all three are rewritten to loops (`tco_loop` back-edges, params
renamed `%…$in`), and the 100k gate passes at both `-O0` and `-O2`, GC on and off —
so stack-safety comes from Sprout’s own TCO, not clang’s.

## 9. Docs

- Update the `vec_sort_by` complexity comment (`prelude:481`) to note stack-safety.
- Close the follow-up note added to PR #171 / `BACKLOG.md` once landed.

## 10. Related, separate follow-up

`range_to_vec` (`prelude:196`) is O(n²): `range_fold(range_vec_step=vec_append, …)`
and `vec_append` copies the whole array each call. One-line fix:
`vec_from_list(range_to_list(value))` (both O(n)). Independent of the sort; worth its
own tiny change + regression test.
