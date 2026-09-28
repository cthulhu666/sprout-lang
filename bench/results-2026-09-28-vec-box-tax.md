# The `Maybe` box on an indexed read: free, or 70x? (2026-09-28)

Two places in the tree said opposite things about the same allocation.

`BACKLOG.md`'s "indexed `Vec` is far slower than an unboxed constructor" entry records
an unboxed accessor that was built to remove `vec_get`'s `Just`, measured, and dropped:
*"no gain (5M reads on a 100k `Vec`, 2 ns/read either way — the allocator is a bump
pointer and the boxes die immediately)"*. `stdlib/mutable.sprout`'s comment on
`mutvec_at` says the reverse: *"it allocates no Maybe per read — that is the whole
point, and in a hot loop the per-read Just dominates via GC pressure."*

Both are right, about different programs. **Boxed is not a property of the accessor.**
It is a property of what the caller does with the result, and the earlier measurement
compared two shapes that were *both* unboxed before they ever reached the allocator.

## What the CPR peephole does, and where it stops

A `Maybe`-returning call that a `match` consumes **in place** is rewritten to a
two-word worker return — no heap value is ever built. The emitted IR names the arm:

| source shape | IR |
|---|---|
| `match vec_get(i, v) with …` | `call { i64, i64 } @vec_get_worker` |
| `maybe_with_default(0, vec_get(i, v))` | `call @vec_get` then `call @maybe_with_default` |

The peephole does not reach through a call, so handing the `Maybe` to any combinator
materialises it. (Do not grep the IR for `sprout_gc_alloc` to tell these apart — the
`Just` is built inside the C builtin and never appears in emitted IR. The two symbols
above are the discriminator.)

## Results

`bench/vec_box/bench.sh`. Four read shapes, one binary, 12M reads per cell, held
constant across sizes so a column compares down it as well as across. ns/read:

| size | `escaped` | `in_place` | `mut_get` | `mut_at` | cost of one `Just` |
|---:|---:|---:|---:|---:|---:|
| 3 | 19.3 | 1.8 | 2.5 | 1.3 | **+17.5** |
| 10 | 19.0 | 1.5 | 2.4 | 1.2 | **+17.5** |
| 100 | 18.8 | 1.5 | 2.3 | 1.3 | **+17.4** |
| 1000 | 20.0 | 1.4 | 2.3 | 1.2 | **+18.6** |
| 100000 | 110.7 | 1.5 | 2.3 | 1.2 | **+109.3** |

- `escaped` — `maybe_with_default(0, vec_get(i, vec))`
- `in_place` — `match vec_get(i, vec) with …`, same accessor
- `mut_get` — `match mutvec_get(v, i) with …`, `MutVec`
- `mut_at` — `mutvec_at(v, i)`, no `Maybe` at all

`escaped` and `in_place` differ only in whether the `Maybe` crosses a call, so their gap
is the price of one `Just` and nothing else.

**Machine:** Apple M3 Pro, macOS 15.7.5 (Darwin 24.6.0), `clang -O2`, load avg ~5.6. The
table is one representative quiet run.

**Spread, over seven runs at two load levels.** The unboxed columns are steady:
`in_place` 1.4–1.8, `mut_get` 2.2–2.7, `mut_at` 1.2–1.3 ns, with one loaded outlier
(`mut_get` 3.8 at `size = 100`). `escaped` is steady at small sizes, 19–25 ns. The 100k
`escaped` cell is **not** tight: 110.7 / 110.7 / 110.8 ns on a quiet machine, 120.8 /
126.9 / 135.0 under load. Quote it as **110–135 ns**. What is robust is the ratio — this
cell is 70–90x the `in_place` column in every run, and never within an order of magnitude
of it — not the third significant figure.

## What this settles

**The earlier "2 ns/read either way" was measuring two unboxed arms.** Compare it to the
`in_place` and `mut_get` columns — 1.5 and 2.3 ns — and it reproduces exactly. The
experiment was sound; it just never had a boxed arm, so it could not have found a box
tax. That is why removing the box showed no gain: there was no box to remove.

**The `mutvec_at` comment is right for escaping callers, and understated for them** — an
escaping `Just` costs 12x at small sizes and **70–90x** at 100k, and the size dependence is
exactly the GC pressure it names: per-read allocation is flat, but collection cost scales
with the live set, so the same `Just` is 6x dearer with 100k live than with 1000.

It does **not** describe a caller who matches in place, and as written it reads as though
it does. There, `mutvec_get` costs 2.3 ns against `mutvec_at`'s 1.2 — a 1.1 ns gap that is
the worker's tag branch and two-word return, not a `Just`, because no `Just` is built.
"The per-read Just dominates" is true only where a per-read `Just` exists.

**It does not indict `Vec`'s representation.** `BACKLOG.md` entry says reopen only with a
measurement that indicts something specific; this indicts the peephole's *reach*, not the
data layout. `in_place` on an immutable `Vec` (1.4–1.8 ns) is already faster than
`mutvec_get` (2.3 ns) and within ~0.3 ns of the no-`Maybe` `mutvec_at`. There is nothing
left for an unboxed `Vec` accessor to win, which is the second reason that one was
correctly dropped.

## What to do

For callers, today: in a hot loop, match the producing call in place, or use an accessor
that returns no `Maybe` — `mutvec_at`, or `vec_get_or`, which despite being an ordinary
call matches `vec_get` in place internally and so lowers to `@vec_get_worker` with no box
(checked). This is already what
`docs/idiomatic-sprout.md` §"Reach for a combinator on a single `Maybe`/`Result`" says,
citing `stdlib.bytes.byte_at` going 15x faster off `maybe_with_default`. That 15x now has
a mechanism and a table behind it rather than one anecdote.

For the compiler: extending the peephole through a *known, non-escaping* combinator —
`maybe_with_default` is the whole population that matters — would remove the gap without
the caller having to know any of this. Filed in `BACKLOG.md` under Codegen: TCO, CPR and
scalar replacement.
