# Growable `MutVec` — v0

**Status:** shipped 2026-08-15, minimal surface. Behaviour is documented in
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
- `mutvec_pop(v) -> Maybe a`, `mutvec_truncate(v, n)`, `mutvec_clear(v)`. The downstream
  `world_reset` is `truncate 0` written by hand, and individual removal is an open item in that
  consumer's ECS docs for want of exactly this.

Tracked in `BACKLOG.md`.

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
