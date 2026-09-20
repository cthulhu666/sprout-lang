# GC temp-root stack: array, not linked list (2026-09-20)

The shadow root stack was a bump-allocated array *and* a linked list over the same
nodes. Push wrote the node, then linked it; pop unlinked one node per iteration;
marking pointer-chased the chain. The runtime's own comment recorded the invariant
that made the chain redundant — a context's live roots are exactly `pool[0..pool_top)`.

Push is now three stores, pop is one bounds check plus a subtraction, and marking is a
linear array scan. `RootNode` loses its `next` and drops 32 → 24 bytes, so the static
task-0 pool goes 4 MiB → 3 MiB BSS and a scan touches a quarter fewer cache lines. The
permanent (never-popped) roots from `register_root_slot` are individually malloc'd and
still chain, in their own `PermRoot`.

**Machine:** Apple M3 Pro, macOS 15 (Darwin 24.6.0), `clang -O2`. The machine was
*loaded* throughout (unrelated suites pinning cores, load avg 12–47), so every pair
below is **interleaved** old/new in one window and only ratios are quoted across
windows. A before-window/after-window comparison here measures the load, not the
change: the first attempt at the compiler A/B that way returned 5.0 s, 8.7 s, 11.1 s
for the *same* binary.

Both sides link the *same* emitted IR against two runtime trees, so nothing but the
runtime differs.

## Results

| workload | old | new | change |
|---|---:|---:|---:|
| `bench/gc_roots` (game-tick shape, 6 pairs) | 2.19 s | 1.38 s | **−37%** |
| N-queens (`examples/nqueens.sprout`, 5 pairs) | 2.49 s | 2.16 s | **−13%** |
| stage-1 compiler, `--emit-ir stdlib/compiler/infer.sprout` (4 pairs) | 6.61 s | 5.92 s | **−10%** |

Every pair agreed with the median; no pair crossed over. The compiler row reproduced
in a second, much busier window at −8.7% (4.83 s → 4.41 s), so call it ~9–10%. Its
emitted IR is **byte-identical** old vs new (136 141 lines) — for a change to rooting,
the strongest cheap correctness signal there is.

The three workloads sit where their rooting density puts them. `bench/gc_roots` allocates
a record per fighter per tick and roots almost every local; N-queens roots list cells in a
backtracking loop; the compiler spends much of its time in string and I/O work that roots
nothing. The change cannot beat the fraction of a workload that is push/pop, which is the
same ceiling the profile below names.

## Where the time went

`sample(1)`, top of stack, both binaries in one window. Read the columns as *shares*:
each sample ran for one second while the run itself got shorter.

| frame | old | new |
|---|---:|---:|
| `sprout_gc_push_i64_root` | 232 (29%) | 235 (31%) |
| `sprout_gc_pop_roots` | 146 (18%) | 53 (7%) |
| `sprout_closure_arity_check` | 104 (13%) | 80 (11%) |
| `sprout_gc_collect_with_reason` | 73 (9%) | 116 (15%) |

Push holds its *share* while the run shrinks by 37%, so in absolute terms it fell from
~0.63 s to ~0.43 s: the two dropped stores were not free, but the bump was always the
bulk of it. Pop fell from ~0.40 s to ~0.10 s — that is the win, and it is the loop that
is now a subtraction.

Those two account for ~0.51 s of the 0.81 s saved. **The remaining ~0.30 s is not
attributed.** The tempting story — that a `RootNode` 32 → 24 bytes made the mark scan
cheaper — is refuted by this same table: marking happens inside
`sprout_gc_collect_with_reason`, which is flat in absolute terms (~0.20 s both sides)
and only *rises* as a share. The residual sits in frames the change does not touch
(`sprout_closure_arity_check` is ~0.28 s → ~0.15 s, which no part of this rewrite
explains), so the honest reading is second-order effects plus sampling error on
one-second windows, not a mechanism this profile can name.

## What this does not fix

Push is still the single largest frame at ~31%, one call per rooted local. That is a
codegen question (fewer roots, or a frame-at-a-time push) rather than a runtime one.
The two leads beside this one — cheaper collection via a nursery, and escape analysis
for non-escaping combinator closures — are untouched; `sprout_alloc_closure` and
`sprout_closure_arity_check` still sit near the top.
