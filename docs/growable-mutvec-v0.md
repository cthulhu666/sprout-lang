# Growable `MutVec` — v0

**Status:** growth shipped 2026-08-15, shrinking 2026-09-09. Behaviour is documented in
[builtins-reference.md](./builtins-reference.md#growing-a-mutvec); this document records the problem,
the decisions, and what was deliberately left out.

`MutVec` is a stdlib type, not part of the language core, so `spec-v0.md` does not describe it and
this change did not add it there — the spec has never mentioned `MutVec`. Nothing here alters syntax,
typing rules, or evaluation order.

## Problem

`MutVec` was fixed-length: `mutvec_new(n, val)` allocated `n` cells and the API
(`new / len / get / at / set / each / fold / zip_fold / map_inplace`) offered no way to add one. So
every store whose size is discovered at runtime had to pick a capacity up front and then decide, by
hand, what to do when it filled — a decision made by each call site rather than by the data
structure.

The requirements came from a real blocked case in a downstream consumer (Uncharted Suns,
2026-08-15), where one codebase had made that decision three incompatible ways:

| site | capacity | on overflow |
|---|---|---|
| belt survey log | 4096 | **silently drops**, permanently |
| audio cue log | 6 | hand-rolled ring, evicts oldest |
| ECS component columns | caller's `cap` | undefined — "the caller must not exceed capacity" |

The survey log is the case that broke. Its population is data-driven — one asteroid belt can hold
24,317 bodies above the game's own size floor — so 4096 filled in about four seconds of play, after
which the instrument recorded nothing ever again. No constant is the right number there; raising it
just moves the wall.

## Goals and non-goals

**Goals.** Append in amortised O(1); `len` keeps meaning live length; growth is visible through every
copy of the handle; bounds behaviour unchanged.

**Non-goals.** No change to the immutable `Vec`. No iterator-invalidation rules — `mutvec_each` /
`mutvec_fold` read the length once on entry and a push from inside one is the caller's problem,
which is enough to say. No shrink-on-pop. No generic collection hierarchy.

## Why this was a small change

The backing representation already modelled growth. `VectorVal` has had distinct `len` and `cap`
fields since it was introduced, `vector_length` already returned `len`, and the runtime already did
doubling growth against them in internal builders (`read_int_lines`, the analysis-service JSON array
reader). `vec_make_filled(n, val)` sets `len = cap = n`, so every vector in existence had `len == cap`
and `mutvec_len` keeps meaning exactly what its callers already assumed. The missing piece was one
builtin and one wrapper, not a data structure.

## What shipped

One new builtin, `vector_push`, and two wrappers in `stdlib/mutable.sprout`:

```sprout
export fn mutvec_empty() -> MutVec a !{IO}
export fn mutvec_push(v: MutVec a, val: a) -> Unit !{IO}
```

`mutvec_empty` needed **no** new builtin: `vector_empty` has been a prelude extern all along
(`prelude.sprout`), backing `Vec`. It is declared *pure* there and cannot be changed — prelude `Vec`
code calls it in pure contexts — so the wrapper carries the `!{IO}` instead. A pure `mutvec_empty`
would both license sharing one buffer between two calls and let a `MutVec` be conjured in a pure
context. `tests/stdlib/test_mutvec_push.spr` pins the non-sharing empirically rather than trusting
the absence of a CSE pass.

### `mutvec_len` became effectful

Growable `MutVec` also changed an existing signature. `mutvec_len` was pure, and while `MutVec` was
fixed-length that was *true* — the length was a function of the handle alone. `mutvec_push` makes it
mutable state, so two calls on the same handle can now disagree, and it is declared `!{IO}`.

The underlying `vector_length` stays pure, and must: it also backs the immutable `Vec`, where the
length genuinely is fixed. **One C symbol, two truths** — and since an extern may be declared exactly
once (`scripts/check_extern_signatures.sh` enforces this), the distinction cannot live in the
declaration. It has to live in the wrapper, which is why `stdlib.mutable` re-states the effect rather
than the prelude.

Nothing in the repo broke: every one of the 13 external call sites was already inside an `!{IO}`
function, as were all 9 internal ones. Note this is *not* a change any test can catch — effects are
parsed but never checked (`spec-v0.md` §7), so both signatures typecheck. The test added alongside
pins the semantics instead: the same call, on the same handle, returning different answers either
side of a push.

### The requirement that can fail silently

Growth is **in place**: `vector_push` reallocates `v->data` inside the *existing* `VectorVal` rather
than producing a new one. Handles are copied freely into records that outlive the call that made
them, so a push that allocated a fresh `VectorVal` would leave every other holder reading the stale
buffer — and would still pass every "push, then read back" test written against a single binding.
The acceptance test therefore copies the handle *before* the growth and reads through the copy.

### GC interaction

Two facts make the C safe, and both are load-bearing rather than incidental:

- The collector takes a vector's child count from `->len`
  (`sprout_heap_child_count_payload`), so it scans `data[0..len)` and never touches spare capacity.
  The store order follows from this: write `data[len]`, *then* bump `len`. Reversed, a collection
  between the two would read an uninitialised slot as a heap pointer.
- `sprout_realloc_vector_data` is a plain `realloc` and never calls
  `sprout_gc_maybe_collect_threshold`, so no collection can run mid-call and neither argument needs
  rooting.

The second fact has a consequence worth stating: pushes alone never trip the collection threshold,
because the backing array is plain `malloc` and invisible to an object-count-based trigger (the
byte-blind-GC-trigger entry in `BACKLOG.md`). For a growing buffer that is the right answer anyway —
it is live, not garbage — but a workload that only pushes will not collect on its own account.

The compiler does not know any of this, so the emitted `mutvec_push` roots both operands around the
`vector_push` call (visible in `tests/golden/ir/examples__astar.sprout.ll`). That is correct and
costs two root pushes per append; it is also a concrete instance of what the interprocedural
non-allocation inference in `BACKLOG.md` would remove, since `vector_push` provably cannot collect.

`vector_push` is the **third** write-barrier site, joining `ref_write` and `vector_mutset`, and
carries the same ageprof hook. The generational-GC backlog entry previously justified its closed
barrier surface with "`stdlib/mutable.sprout` declares no writing externs, so no bypass"; this change
falsified that in one commit, which is precisely why that entry asks for the coverage to be a
checkable invariant rather than a claim.

## Prior art for the two open questions

Both questions the requirements raised are settled by the same observation: the established APIs
guarantee the *cost*, not the *strategy*, and keep failure out of the return type.

| language | is the growth factor specified? | allocation failure |
|---|---|---|
| Rust `Vec::push` | **No.** "Vec does not guarantee any particular growth strategy when reallocating when full… Whatever strategy is used will of course guarantee *O*(1) amortized `push`." | Diverges, does not return an error. `handle_alloc_error` for `std` binaries "prints a message to standard error" and "aborts the process". The fallible path is a *separate* API (`try_reserve`), not a `Result` on `push`. |
| Java `ArrayList.add` | **No.** "The details of the growth policy are not specified beyond the fact that adding an element has constant amortized time cost." | — (not surveyed) |

**Decision — growth factor: doubling from 8, documented as behaviour, not as contract.** The
contract is amortised O(1), matching both rows above. Doubling is nonetheless stated in
`builtins-reference.md` because a caller sizing a 24k-entry log needs to know the peak can reach 2×
the final length; the escape hatch for those callers is `mutvec_new(n, fill)` plus indexed writes,
which allocates exactly once.

**Decision — allocation failure aborts via `tcp_fail`.** This matches Rust's default and keeps
`push` from being the one operation in the `MutVec` API with a different failure discipline. The
requirements doc noted the inconsistency honestly — `push` is the first operation that can fail for
a reason the caller could have avoided — but Rust's answer to exactly that is a separate fallible
API rather than a `Result`-returning `push`, and Sprout has no caller asking for one yet.

## Deliberately not shipped

Kuba's instruction was a minimal implementation, adding functions when a caller needs one. Of the
four builtins originally scoped, only `vector_push` ships: `vec_make_empty` proved unnecessary
(`vector_empty` already existed), and `vector_reserve` / `vector_truncate` are deferred. The
following are known wants with known consumers, none blocking:

- `mutvec_with_capacity(n)` / `mutvec_reserve(v, n)` — lets a caller that knows the size skip the
  regrowth entirely. Matters for per-frame stores, where the doubling reallocations are the whole
  cost.
- ~~`mutvec_pop(v) -> Maybe a`, `mutvec_truncate(v, n)`, `mutvec_clear(v)`~~ — shipped 2026-09-09,
  along with `mutvec_remove` and `mutvec_insert`. See "Shrinking" below.

Tracked in `BACKLOG.md`.

## Shrinking (2026-09-09)

`text_area` and the IDE's line buffer need to remove a line, and `BACKLOG.md` recorded that as
needing a `vector_remove` builtin. It does not. A removal is two things — slide the tail down, and
shorten the length — and only the second has no Sprout spelling, because `->len` lives inside the
`VectorVal` whose backing array has no Sprout-visible handle. The shift is `vector_get_direct` +
`vector_mutset` in a loop.

**Decision — one builtin, `vector_truncate(v, n)`, scoped to the length.** `mutvec_remove` and
`mutvec_insert` are ordinary stdlib code on top of it and of `vector_push`; `mutvec_pop`,
`mutvec_truncate` and `mutvec_clear` fall out of the same call. `insert` needs nothing new at all —
pushing the current last element is what lengthens the vector.

The general test this applies: not *"is this operation primitive?"* but *"which byte of state does it
change that Sprout cannot address?"* A `remove` looks primitive because it is one call in every
other language's API; decomposed it is a loop Sprout can already write plus one field write it
cannot.

**Decision — the O(n) shift stays in Sprout.** A `memmove` builtin would be faster, and performance
alone does not justify a builtin ("Builtin vs Stdlib" 6). At IDE scale a few thousand extern calls
to delete a line is not a bottleneck; if a profile ever says otherwise, `vector_remove` can be added
then with the measurement to justify it.

**Decision — all four are total.** `remove` and `pop` return `Maybe a`; `insert` returns `Bool`. The
first draft had `insert` panic, on the reasoning that it has no return value to report a miss in and
that `mutvec_set` panics too. Both halves are wrong under guidelines.md §2, which is a *hard mandate*
for `[Library]`: `panic` is for a violated internal invariant, explicitly "not for input the caller
could plausibly supply", and an index is exactly that. That `mutvec_set` reaches a `tcp_fail` through
`vector_mutset` is a pre-existing deviation, not a licence. `Bool` rather than `Maybe Unit` because a
`<-` bind on a `Maybe` is a *fallible* bind — it would short-circuit the caller's block rather than
discard, which is the opposite of what a caller ignoring the result wants.

**Deviation, stated — argument order.** guidelines.md §6 wants the receiver last (`vec_get(index,
vec)`). These take it first, matching the `mutvec_get`/`mutvec_set`/`mutvec_push` group they sit
beside; the combinators in the same module (`mutvec_each`, `mutvec_fold`) are data-last as the rule
asks. Splitting the difference *within* the indexed group would be worse than either convention
applied consistently. Whether that group should move is a separate change to the whole API.

**Decision — the vacated slots are zeroed.** This is insurance, not a fix, and the distinction is
worth recording because the first draft of this change asserted it was a leak fix. It is not: the
collector sizes a vector's children by `->len`, so a shortened vector already drops its tail, and
nothing reads `data[len..cap)` — `vector_push` overwrites `data[len]` before bumping `->len`. What
zeroing buys is that a future scanner sizing by `->cap`, or a heap-walking tool, cannot dereference
a swept pointer. Go's `slices.Delete` zeroes for a stronger reason: Go's collector scans the backing
array, so there it *is* a leak fix.

Zero before lowering `->len`, so "every slot in `[0, len)` is a valid handle" holds at every instant
rather than only at the ends. A zero is legal mid-loop: `sprout_gc_drain_marks` skips a child whose
`sprout_heap_lookup` misses.

**The price is that shrinking is O(len − n) rather than O(1)**, which matters most for `clear`: a
caller emptying a large vector each iteration pays a full pass over the live region where a bare
length store would be free. That is disclosed in `builtins-reference.md` rather than hidden, and it
is the argument for revisiting the zeroing if a real caller ever measures it — the benefit is
hypothetical and the cost is not.

The safety of this builtin is a property of the *collector*, not of the operation. Under a moving
collector, a stale handle left in spare capacity would be a pointer never forwarded — the same bits,
silently wrong after the next collection. Non-moving is already load-bearing elsewhere
(`sprout_runtime.c` says so at the sticky-mark-bit comment); this is one more place it is.

### Prior art

| language | is remove-by-index primitive? | returns | out of range |
|---|---|---|---|
| Rust `Vec` | `remove` and `truncate` are both `std` methods; the shift is `ptr::copy` inside | `remove` → `T`; `pop` → `Option<T>`; `truncate` → `()` | `remove` panics; `truncate` has no effect when `len >=` the current length |
| Go `slices` | **No.** The language gives `append`/`copy`/`len`; `Delete[S ~[]E, E any](s S, i, j int) S` is a library function | the modified slice | — |
| Java `ArrayList` | library method over `System.arraycopy` | `remove(int)` → the removed element | `IndexOutOfBoundsException` |

Go is the closest analogue: the language exposes grow, copy and a length change, and removal is
library code composed from them. That is the split adopted here. Rust settles `truncate`'s clamping
behaviour, and Rust and Java agree that removal returns what it removed — free here, since the
element is read to shift it anyway.

## Open question this does not answer

Whether an ECS built on `MutVec` follows. `world_new(cap)` fixes capacity for every component column
at once, so a growable `MutVec` only helps if `world_spawn` grows its columns too — which it cannot,
since it does not own them. That is an engine-side design question, and it decides whether this
fixes one store or all of them.

## Tests

`tests/stdlib/test_mutvec_push.spr` — 30 assertions covering: length tracking across three
regrowths; element and order preservation; the pre-growth handle copy (the acceptance criterion);
bounds against `len` rather than `cap`, including an index inside spare capacity; push onto a
`len == cap` vector from `mutvec_new`; two `mutvec_empty()` calls not sharing a buffer; and pushed
`String` elements surviving regrowth, which exercises the collector scanning `data[0..len)`. Also
run green under `SPROUT_GC_STRESS=1`.

`tests/stdlib/test_mutvec_shrink.spr` — 58 assertions for the shrinking half. What they mostly pin
is the *shift*, since that is the part written in Sprout: removal at the front, the middle and the
last index (where the loop must run zero times rather than read past the end); insertion at `0`, in
the middle, at `len`, into an empty vector, and across a regrowth, where the push inside `insert`
reallocates mid-operation and the shift must run against the new buffer; insert-then-remove at the
same index as an identity, which catches an off-by-one in either shift; an out-of-range remove or
insert reporting the miss and leaving both length and contents alone, including that `len` is in
range for an insert and `len + 1` is not; `truncate` clamping at both ends; a push after `clear`
reading back rather than resurrecting; and removal seen through a handle copied beforehand, the
same acceptance criterion growth has. Also run green under `SPROUT_GC_STRESS=1`.

One group is there for the *lowering* rather than the arithmetic. `Maybe a` is CPR-able, so
matching `mutvec_remove(v, i)` directly — the shape an author writes — routes to
`mutvec_remove_worker` returning `{ tag, value }` unboxed, where the payload's rooting is governed
by `IRCallUnboxed2` rather than by the heap `Just` the other assertions build. That is the path this
change makes interesting, since the element leaves as a raw word at the moment the shift and
truncate have erased its only other reference. `matched_cases` takes it with `String` elements and
an allocation between the removal and the read.
