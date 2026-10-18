# Sorting v0

Status: `mutvec_sort` / `mutvec_sort_by` landed (#629). Everything faster is deferred; the
options and what each needs are below, so the next round starts from numbers, not from scratch.

## Problem

`vec_sort` costs about 1 µs per element on small `Vec Int`s and 1.8 µs at 100k (#629). A game
scheduler sorting ~440 Ints a tick spent ~85% of its time there, and `MutVec` had no sort.

`vec_sort` is a bottom-up merge sort over immutable lists of `(key, index, value)` tuples
(`stdlib/prelude.sprout`, `vec_sort_by`). It allocates on the order of n·(log2 n + 3) objects.

## Measurements

Apple M3 Pro, `clang -O2`, random Ints in [0, 65536), ns per element.

| | n=440 | n=100k |
|---|---|---|
| `vec_sort` (includes ~35 ns building the input) | 970 | 1,850 |
| `mutvec_sort` (shipped) | 193 | 343 |
| the same sort through `ord_lt` instead of `compare` | 273 | 493 |
| the same sort hand-specialised to Int (`<`, no `Ord`) ¹ | ~100 | ~190 |
| the same merge sort in C | 17.5 | 33 |
| C `qsort` | 36 | 61 |
| C LSD radix sort, 8 bits a pass | 2.0 | 1.6 |

¹ A prototype, timed with a ~19 ns fill and a sortedness check included.

`mutvec_sort` at n=1M: 403.

## Where the time goes

1. **Generic `Ord` dispatch: about 2×.** A `where Ord a` function receives the `Ord` and `Eq`
   dictionaries, and every comparison is an indirect `compare` call. The compiler does not
   specialise a generic function for a concrete type, so `Int` pays the same as any type.
2. **A wrapper's GC roots survive inlining.** `ord_lt(x, y)` is just `compare(x, y) < 0`, and
   LLVM does inline it, yet calling it instead of `compare` costs ~40% of the sort. The rooting
   pass roots the heap operands of every call, because some runtime builtins allocate before they
   read their arguments (`op_exposes_operands` in `ir_rooting.sprout`). So `ord_lt` pushes its
   four arguments (two values, two dictionaries) around its call to `compare`, and LLVM cannot
   delete a call to `sprout_gc_push_i64_root`. `mutvec_sort` calls `compare` directly.
3. **Sprout vs C on the same algorithm: about 5×, now 1.2–1.8×.** Under the whole-program
   link the cost was two calls per access: `sprout_tag`, for a match on the one-constructor
   `MutVec`, and a `vector_mutset` too big to inline. Both are gone (2026-10-18); what is left is
   in [native-sprout-runtime-v0.md](native-sprout-runtime-v0.md) §Phase 2.

## Options for going faster

| option | gain | needs |
|---|---|---|
| A. Specialise `where C a` functions at concrete types | ~2× on every generic sort, and on generic code at large | compiler design |
| ~~B. Inline `MutVec` element access as loads/stores~~ | done 2026-10-18: item 3 | — |
| C. C sort builtins for primitive vectors (`Int` radix, `Double`) | 50–500× over `vec_sort` | approval for new builtins; a way to reach them (below) |
| D. Generic C sort calling a Sprout comparator | removes what is left of item 3 (1.2–1.8×), keeps item 1 | a builtin, and C→Sprout callbacks; not checked whether the runtime supports them safely |
| E. Local mutation in pure code (ST-style) | lets pure `vec_sort` use the in-place sort, ~5× | language design |
| F. Root call operands only for runtime builtins, not for calls to Sprout functions | item 2, program-wide | a GC-safety argument; compiler change |

Option C is safe for the collector: an `Int` element is a plain word in the vector, the collector
follows a slot only when it looks up as a heap object, and a C sort allocates nothing. It also
unblocks a fast pure `vec_sort`: copy, sort the copy, return it.

The catch with C is reaching it from generic code: `vec_sort` with `where Ord a` cannot tell that
`a` is `Int`. Three ways:

- **Separate functions** (`mutvec_sort_ints`, `vec_sort_ints`). Simple; callers must know.
- **Specialisation (A).** `vec_sort` at `Int` picks the builtin with no new name.
- **A sort method on `Ord`.** Classes have no default method bodies (spec §8.5), so every
  hand-written `Ord` instance would have to define it. A breaking change; not recommended.

## Decision

Ship the in-place sort in Sprout now; revisit the rest when a caller needs more.

- `mutvec_sort(v)` / `mutvec_sort_by(f, v)` in `stdlib.mutable`: stable, ascending, in place.
  Insertion sort on 16-element blocks, then bottom-up merge passes between the vector and one
  n-slot scratch buffer, allocated only when n > 16.
- `mutvec_sort_by` calls `f` twice per comparison, unlike `vec_sort_by`, which computes each key
  once. Computing keys once would allocate n pairs, the cost the in-place sort exists to avoid.
- Stability: an element moves ahead of another only when strictly less, in both the insertion
  and the merge, so equal elements keep their input order.
  `tests/stdlib/test_mutvec_sort.spr` checks it against `vec_sort_by` with repeated keys.

Deferred, each a `BACKLOG.md` entry: specialisation (A), profiling the Sprout-vs-C gap (B), the
primitive sort builtins (C), and reworking `vec_sort`'s list sort.
