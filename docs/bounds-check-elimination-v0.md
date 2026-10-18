# Bounds-check elimination in counted loops, v0

Status: design, not started. Supporting doc; changes no syntax or semantics, so nothing here is
normative. Measured 2026-10-18 on macOS arm64, Homebrew LLVM 23.1.1, whole-program link
(`just compile-native`).

## 1. Problem

Every vector and byte access is bounds-checked: `vector_get_direct` and `vector_mutset` in
`runtime/sprout_runtime.c` call `sprout_fail` on a bad index. In a counted loop the check runs on
every iteration although the loop's own exit test already keeps the index in range. Two
workloads pay for it:

- **Int merge sort** (`bench/native_runtime/`), the Phase 2 exit metric of
  [native-sprout-runtime-v0.md](native-sprout-runtime-v0.md): 1.28× and 1.19× C. With the two
  checks deleted by hand: 1.06× and 1.06×.
- **base64 encode** ([byte-buffer-v0.md](byte-buffer-v0.md) §3.1): 1.25× with constant tables and
  shift packing; 0.99× with the loop's checks deleted as well.

The check also costs more than its compare. Its fail path inflates the inline cost of a small
loop function: the sort's `insert` (cost 325, threshold 225) inlines into `insertion` only once
its checks are gone. The overflow checks alone change nothing.

## 2. Goals and non-goals

Goals:

- Remove per-iteration checks from counted loops that cannot go out of range, and only those.
- Keep every out-of-range program's behaviour exactly: the same panic message, after the same
  output.
- No new syntax, no `unsafe` surface in user code.

Non-goals:

- Generic code. Generic `mutvec_sort` stays at ~6.4× and ~5.7× with or without checks; its cost
  is dictionary dispatch and rooting ([specialisation-v0.md](specialisation-v0.md)).
- Overflow checks. They cost under 5% here.
- Loops that are not self-recursive functions (folds over closures with unknown effects).

## 3. Measurements

Medians of 15 interleaved rounds; every checksum equals C's. Ratios against `sort_c.c`.

| variant | n=440 | n=100k |
|---|---:|---:|
| shipped (`MutVec`, checked) | 1.28× | 1.19× |
| raw `Vector`, checked | 1.26× | 1.17× |
| raw `Vector`, **versioned** (§5 B, by hand) | **1.09×** | **1.07×** |
| no bounds checks at all | 1.06× | 1.06× |
| no bounds or overflow checks | 1.01× | 1.00× |

The versioned row is the sort rewritten so each loop function checks once at entry that every
access it can make is in range, then runs a clone whose accesses call unchecked twins of the two
accessors. In the clones, `insert` inlines into `insertion`. The gap to the no-checks row was
not measured apart; the candidate is the entry test in `insert`, run once per element, which
§6 stage 2 removes. The last row is from an earlier run of the same protocol, which read
1.27×/1.17× shipped and 1.06×/1.05× with no bounds checks.

**base64**, the [byte-buffer-v0.md](byte-buffer-v0.md) bench: 64 KiB input, hand-edited IR,
medians of 15 interleaved rounds, ratios against `crypto_base64_encode`; every run's output
equals C's. `push4` packs a group into one 4-byte push, `push_byte` pushes each byte.

| variant | push4 | push_byte |
|---|---:|---:|
| shipped accessors, checked | 2.10× | 2.19× |
| constant table, no overflow checks, checked | 1.24× | 1.58× |
| the same, **versioned** | **0.98×** | **1.07×** |
| the same, every check removed | 0.98× | 1.07× |

The loop has three checks. Input reads at `3g + c` and output pushes (`n` grows by 4 per group)
are affine, so the entry test covers them: `3 * groups_n ≤ len(b)` and
`n + 4 * (groups_n - g) ≤ cap`. The table lookup's index is bounded by `& 63` and shifts, not by
the loop; with a constant table, LLVM folds that check itself, in both clones.

**LLVM does not do this itself.** Run on the whole-program module of the shipped sort:

- IRCE and loop predication are not in the `-O2` pipeline (`opt -O2 -print-pipeline-passes`);
  ConstraintElimination is.
- Run by hand on top of `-O2`, both remove no checks.
- IRCE recognises no range check in any sort loop. A vector's length and data pointer are
  reloaded every iteration: they sit after an exit to `sprout_fail`, and the vector pointer is an
  `inttoptr` of an `i64`, so LLVM cannot prove the loads safe to hoist. A length that is not
  loop-invariant is not a range check to IRCE.
- With the loads in `copy` hoisted by hand, IRCE recognises both checks but still rewrites
  nothing.
- ConstraintElimination needs `stop ≤ len(src)`, and no function in the sort knows it. `copy`
  knows `i < stop`; the link from `stop` to the length is made by the caller.

## 4. Prior art

Read on each project's main branch, 2026-10-18.

| system | mechanism | needs a static proof? | panics at the same iteration? |
|---|---|---|---|
| Go | `prove` (`ssacompile/prove.go`) removes a check implied by dominating branches; `findIndVar` (`ssacompile/loopbce.go`) gives induction ranges. No loop versioning. | yes | yes: only provably redundant checks go |
| HotSpot C2 | range check elimination (`loopTransform.cpp`, `do_range_check`, `insert_pre_post_loops`, `add_constraint`): pre, main and post loops; the main loop runs with no checks. Needs a constant stride (`stride_is_con()`). | no: limits computed at run time | yes: pre and post loops keep their checks |
| .NET RyuJIT | loop cloning (`loopcloning.cpp`, `optCloneLoops`): conditions such as `n <= a.Length` tested once; fast clone without checks, original as the slow path. Needs `i += 1`, `i < x`. | no: one run-time test | yes: the slow loop runs "the normal computation (esp., side effects) before the exception occurs" (`loopcloning.h`) |
| LLVM | IRCE (`InductiveRangeCheckElimination.cpp`, `LoopConstrainer.cpp`): splits the loop into three ranges. Needs an affine index, constant step and loop-invariant length. Not in the default pipeline (`PassBuilderPipelines.cpp`); ConstraintElimination is. | no | yes: outer ranges keep their checks |
| Swift | `BoundsCheckOpts.cpp`, `hoistArrayBoundsChecksInLoop`: copies the check to the preheader for the first and last index. | needs the array unchanged in the loop and a linear index | no: the hoisted check traps before the loop |
| Rust | `get_unchecked` (`core/src/slice/mod.rs`) is `unsafe`: an out-of-bounds index is undefined behaviour "even if the resulting reference is not used". | the programmer's | n/a |

Two families: remove what is provably redundant (Go, LLVM ConstraintElimination), or test once
and run a clone (HotSpot, RyuJIT, IRCE). Only the second helps here, because the proof needs a
fact that is only true at run time (`stop ≤ len`).

## 5. Options

**A. Fact-based removal in `ast_to_ir` (Go's way).** Track branch facts inside a function and
drop a check they imply. Cheap to build, but removes nothing in the sort or base64: each loop is
its own function, and the fact linking its bound to a length is in the caller. The one local
fact base64 has, a table index under `& 63`, LLVM already uses.

**B. Loop versioning on Sprout IR (RyuJIT's way).** For a self-recursive function that qualifies
(§6), emit an entry test that every access the loop can make is in range; on success tail-call a
clone whose accesses are unchecked, else run the original. Measured above: 1.09× and 1.07× on
the sort, and on base64 the same as removing every check.
Behaviour is unchanged by construction: the clone runs only when no access can fail, and
otherwise the original code runs, panicking where it always did.

**C. Make LLVM do it.** Tell LLVM the vector pointer is dereferenceable (an `llvm.assume` bundle
on each entry), so the length load hoists, then add IRCE to the pipeline. Unproven: with the
loads hoisted by hand, IRCE still rewrote nothing, and no debug build was at hand to see why.
IRCE also needs constant-step induction, which the merge's `i` and `j` (step 0 or 1) are not.

**D. Unchecked accessors for the stdlib, checked once by hand (Rust's way).** `mutvec_sort`,
the encoders and the `_go` loops in `stdlib.mutable` test their range once and call unchecked
twins. Same machine code as B, written by hand. A wrong test is memory corruption, not a panic,
and the Phase 2 exit keeps element access checked. Needs the module-private extern rule from
[byte-buffer-v0.md](byte-buffer-v0.md) §5.3 so user code cannot reach the twins.

## 6. Recommendation: B

B is the only option that removes the checks and keeps them as semantics: the proof obligation
moves from a person (D) or an optimiser that does not fire (C) into one entry test the compiler
derives and the slow path backs. It applies to user loops, not only stdlib ones.

What qualifies, all on Sprout IR after type inference:

1. A function whose self-tail-calls `tco_rewrite` (`ast_to_ir.sprout`) turns into a loop.
2. Each vector index is affine in the parameters: `c * p + d`, `c` and `d` constants. Each
   parameter used in an index is invariant, or steps by a constant (`i + 1`, `j - 1`), or by 0 or
   1 with a sum that steps by exactly 1 (the merge's `i`, `j`, `k`; HotSpot and RyuJIT stop at
   constant strides, so this case is later).
3. Each access is dominated by the exit test that bounds its index (`i >= stop` returns before
   reading `i`).
4. Nothing in the body can change an accessed vector's length. Only `vector_push` and
   `vector_truncate` do, and both are `!{IO}`; the pure `vector_set` and `vector_append` copy.
   So the body may make pure calls, accessor calls, and calls to functions that are
   length-preserving by the same rule. A pure comparator qualifies: an effect type is the proof.
   `Bytes` never changes length, so byte reads need only 2 and 3.

A byte buffer ([byte-buffer-v0.md](byte-buffer-v0.md) §5) changes rule 4 by how it is kept pure:

| buffer | what can change it | rule 4 needs |
|---|---|---|
| `MutVec` today | `vector_push`, `vector_truncate`: `!{IO}` | the effect type |
| 5.2 `Local` | every `ByteBuf` op: `!{Local}` | the effect type, `Local` in place of `IO` |
| 5.4 Erlang-style | `push_byte`, which is pure | the body calls only known functions, none of which reaches a `ByteBuf` |
| 5.1 linear | only the owner, which is the loop | nothing |

Under 5.4 a pure callee can push onto the same `Storage` and move `fill`, so the effect type
proves nothing. Nor do the callee's parameter types: an `Int -> Int` closure can capture the
buffer, push and drop the result. 5.4 also has a second per-push check, `len == fill`, which only
that alias rule lets the entry test cover; the base64 rows above have the room check only, and
the version check costs 3–12% (byte-buffer-v0.md §3). Running out of room is not an error, so
for pushes the entry could grow the buffer once instead of testing it; unmeasured.

The entry test uses wrapping arithmetic and falls back on overflow; a test that panics where the
original did not would change behaviour. Each qualifying function is emitted twice.

Staging:

1. Constant-step loops: `copy`, `insertion`, `insert`, the `stdlib.mutable` `_go` loops, base64.
2. A fast clone calling a callee whose test its own entry test implies goes straight to the
   callee's clone (`insertion_fast` to `insert_fast`, saving the per-element test).
3. Step-0-or-1 induction variables (the merge).

## 7. Impact

- **Syntax, typing, diagnostics:** none.
- **Semantics:** none. `sprout_fail` prints its message and calls `exit(1)`, and the original
  loop is what runs whenever an access would fail.
- **Runtime:** unchecked twins of `vector_get_direct` and `vector_mutset`, the second still
  calling `sprout_ap_on_store`, and of the `Bytes` reads. New runtime functions need approval and
  `runtime/APPROVED_BUILTINS` entries; `ir_rooting.sprout` must list them as non-allocating, as
  it does the checked ones. Emitting the access inline instead, as B1 does for `Vector Double`,
  avoids the runtime change but has to reproduce the store hook in IR.
- **Compile cost:** a qualifying function is emitted twice; `just rooting-cost-gate` and
  `just scc-cost-gate` price it.
- **Seed:** a compiler change, so a reseed.

## 8. Tests

- Differential: a loop whose entry test fails (an index one past the end, an overflowing bound)
  prints the same output and the same panic as today.
- Golden IR: a qualifying loop has the entry test and two bodies; a loop with an `!{IO}` callback
  (`mutvec_each_go`) has one.
- The sort bench: the Int row at or under 1.1× at both sizes.

## 9. Open questions

1. Priority. The Phase 2 exit (1.3×) is met without this; B buys the 1.2× aim and most of the
   way to parity, here and in base64.
2. Runtime twins or inline IR (§7)?
3. Code size: how much does emitting every qualifying function twice cost the compiler binary?
   Unmeasured.
