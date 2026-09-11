# Is there enough CSE to be worth a CSE pass? (2026-09-11)

Measured before building M1 of `docs/opt-passes-v0.md`, because M0 had just shown that a
correct, cheap pass (DLE) can find literally nothing in real code
(`bench/results-2026-09-11-opt.md`).

Instrument: `stdlib/compiler/cse_census.sprout`, via
`compile_driver --phase cse-census|cse-keys`. It counts repeated pure calls and rewrites
nothing.

**Machine:** Apple M3 Pro, macOS 15 (Darwin 24.6.0), Apple clang 17, `-O2`.

## The static opportunity is real

| program | bodies | pure calls | candidate keys | dup (upper bound) | **region dups** |
|---|---:|---:|---:|---:|---:|
| `astar` | 355 | 574 | 20 | 34 | **4** |
| `nqueens` | 283 | 500 | 22 | 37 | **7** |
| `math_transcendental` | 395 | 666 | 25 | 44 | **5** |
| `digit_recognizer` | 483 | 883 | 33 | 50 | **15** |
| `compile_driver` | 3 635 | 17 492 | 829 | 2 114 | **673** |

`region_dups` is the honest figure: repeats within one straight-line region, where an `if`
or `match` arm starts a new region because only one arm runs. The upper bound counts arms
that never both execute. Everything is *within one body*, since CSE is a within-body
rewrite.

Non-zero, unlike DLE — and the entries are not junk. The compiler's top repeats:

```
118  parser.tok_at(tokens, i)
 63  ast_to_ir.fresh_name(name_prefix, idx)      (summed over idx, idx1, idx+1, …)
 41  dict_empty()
 36  checker.bt_str()
 21  parser.cur_pos(tokens, i)
```

`looks_like_do_step_start` calls `tok_at(tokens, i)` **18 times in one `||` chain**.

## LLVM will not take it

The design doc claimed LLVM cannot dedupe Sprout-level calls because the shadow stack makes
every allocating function look `memory(readwrite)`. Confirmed directly on a minimal repro
(8 identical calls to a `vec_get` wrapper in one `||` chain): after `opt -O2` the wrapper is
fully inlined, but **6 of the 8 `vector_get_unboxed` calls survive**. The repeated work is
still there.

## …and taking it buys nothing

Two hand-applied CSEs, each verified to emit **byte-identical IR** to the unmodified
compiler — which is also the evidence that this class of rewrite is semantics-preserving.

**1. The largest cluster.** `looks_like_do_step_start`, `parse_pattern_atom` and
`parse_pattern_non_paren` rewritten to bind `tok_at`/`cur_pos` once — 49 of the 673 sites,
including the single densest one.

| | before | after |
|---|---:|---:|
| `--emit-ir infer.sprout` (min of 6) | 4.19 s | 4.19 s |
| `--phase bundle infer.sprout` (min of 8) | 0.56 s | 0.56 s |
| allocations, bundle phase | 13 771 079 | 13 771 **081** |

Zero time, and two allocations *more*. The reason is visible in the IR above: `tok_at`
unboxes to a load and a switch — `vec_get`'s `Maybe` never reaches the heap — so the
repeated work is a handful of instructions with nothing to reclaim.

**2. An allocating site**, to be fair to the class that should benefit.
`bind_tuple_items`' `VarPattern` arm calls `fresh_name` twice (a string concat: real
allocation), bound once instead.

| | before | after |
|---|---:|---:|
| allocations, `--emit-ir infer.sprout` | 50 833 740 | 50 833 **739** |

**One** allocation out of 50.8 million.

## Verdict: do not build M1

That last number is the whole finding. The census counts **static** sites; what pays is
**dynamic** frequency, and these sites fire a handful of times each across an entire
compile. 673 static opportunities against 50.8M allocations is noise, and the one cluster
dense enough to look promising turned out to be the cheapest kind of call there is.

The general lesson, which outlives CSE: **a static count of optimisation sites is not an
estimate of the win.** M0 asked "does the pass fire?" and DLE answered no. M1 asked "is
there something to fire on?" and the answer was yes — but the question that decides it is
"how often does that code actually run?", and only an A/B answers it. Both hand-CSEs here
took under an hour and settled what the pass would have taken a milestone to learn.

**M2 (LICM) is not refuted by this.** Its target is recursion, where the body runs many
times — precisely the multiplier CSE turned out to lack. It should still be gated on a
dynamic measurement rather than a count of invariant parameters.

The census stays as a diagnostic phase: it is cheap, it is the analysis half of any future
CSE, and it is what makes this answer re-checkable when someone proposes the pass again.
