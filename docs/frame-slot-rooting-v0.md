# Frame-slot GC rooting (v0, 2026-10-26)

Status: implemented 2026-10-26 (approved the same day, §8 recommendations taken). Follows up
`docs/archive/root-once-rooting-design-2026-06-30.md` (PR #108), whose §7 deferred this change.
§5.6 says what landed; §7 lists what is still to be measured.

## 1. Problem

`ir_rooting` roots a heap value once per block, not once per function. PR #108 coalesced the
pushes inside a block and kept one rule: the root stack is empty at every block boundary
(`finish_block` pops before each terminator, and `rewrite_block_full` re-seeds `pending` from the
block's `live_in ∩ heap_origin`). So every block holding a GC trigger pushes again every heap value
live in it. The emitted push count is about

    Σ over blocks with a trigger of |heap values live after that block's first trigger|

`uncharted-suns` `game.app.render_loop` (`game/app.sprout`, ~1,040 source lines, 22 parameters,
one self tail call) shows the worst case. Measured 2026-10-26 with the compiler at seed
`e69817f9` on `uncharted-suns` `df24d438`, over the function's body in `--emit-ir` output:

| | count | how |
|---|---:|---|
| emitted IR lines | 67,190 | between `define` and `}` |
| `sprout_gc_push_i64_root` calls (each with an `alloca` and a `store`) | 20,338 | `grep -c 'call i64 @sprout_gc_push_i64_root'` |
| distinct values pushed | 509 | distinct operands stored into a pushed alloca |
| `sprout_gc_pop_roots` calls | 437 | `grep -c '@sprout_gc_pop_roots'` |
| pushes of each of `aud`, `res`, `link`, `parsed`, `inhabited`, `dstate` | 251 | per operand, as above |
| everything else (the function's real work) | ~1,600 lines | the rest |

It is a TCO loop, so a value passed unchanged to the next iteration is a use of `IRTcoBack` and
is live in every block of the body. A long `do` of `if` expressions makes ~1,000 blocks.

What it costs:

- **Whole-program link.** Same day and commits: the game links in 84.4 s with
  `scripts/link_whole_program.sh` against 6.4 s with plain `clang -O2`, and `llc -O2
  -time-passes` puts the Register Coalescer at 39.9 s of a 67.5 s codegen. `render_loop` grows
  from 65,189 instructions to 319,771 after `-O2`. That the growth is mostly inlined pushes is
  inferred, not measured: 254,582 extra instructions over 20,338 pushes is ~12.5 each, and the
  572 `sprout_field`, 191 allocation and 437 pop calls also inline.
  `docs/cross-tu-inlining-v0.md` §5.4 recorded 7 s vs 148 s in September, on an older
  compiler, LLVM and game.
- **Run time.** Every push on the path taken runs, every frame. The per-frame count is
  unmeasured. Neutralising all rooting made whole-program-linked nqueens ~20% faster (208 →
  165 ms at N=12, GC off, 2026-09-25; added to BACKLOG in `3d4f5af5`, not re-measured), and the
  push was 31% of `bench/gc_roots` after the array rewrite
  (`bench/results-2026-09-20-gc-root-stack.md`). Neither isolates the cross-block share.
- **Compile time, probably.** BACKLOG "A long `match`, or a `do` block of many `if`s, compiles
  in superlinear time" has this shape. That it shares the cause is unproven.

## 2. Why §7 of the PR #108 design deferred this, and why that no longer holds

§7 deferred a per-function root array as "a runtime ABI change (new builtins + GC-scan change +
per-function prologue)", to revisit "only if intra-block coalescing leaves a material runtime
tax after measurement". Against the current code:

- **No new builtin, no scan change.** `sprout_gc_push_root` stores a slot *address* and
  `sprout_gc_mark_root` reads the slot when it marks; every `IRRoot` already works this way (an
  alloca's address pushed once, never reloaded). `sprout_gc_push_scan_root(slot, size_bytes)`
  roots a whole array of words with one pool entry; its marking goes through
  `sprout_gc_mark_ptr` → `sprout_heap_lookup`, the same lookup the `IRRoot` path uses. It is in
  `runtime/APPROVED_BUILTINS`. Emitted IR does not declare it yet (§5.6).
- **The trigger was met.** The nqueens and `bench/gc_roots` numbers above are that runtime tax.
  Gating only on run time was also the wrong test: compile cost was that design's own headline.

Only the prologue/epilogue part is true: a function with a frame pushes it on entry and pops it
on every exit.

## 3. Goals and non-goals

Goals:

- Push count O(rooted values), not O(blocks × live values), for values that cross a block.
- No runtime change.
- No change to what the program computes. Reachability changes only by over-retention (§6).

Non-goals:

- No change to intra-block coalescing.
- No slot sharing between values (coloring) in v0 (§8 Q3).
- No change to `op_exposes_operands` (BACKLOG "Calls to Sprout functions root their operands").
  It is a second multiplier on the same pushes, with its own safety argument.
- No language, type-system, diagnostics or spec change.

## 4. Prior art

| system | how roots are kept |
|---|---|
| LLVM `gcroot` / shadow stack | `llvm.gcroot` "must reside inside the first basic block" on an alloca; per the sample runtime, "Functions push and pop onto this in their prologue and epilogue", one `StackEntry` with a roots array per frame ([GarbageCollection](https://llvm.org/docs/GarbageCollection.html)) |
| LLVM statepoints | a stack map records where each live GC pointer is at each safepoint; the same page calls `gcroot` "mostly of historical interest" apart from its shadow stack ([Statepoints](https://llvm.org/docs/Statepoints.html)) |
| Sprout's former direct codegen | a running root count: a value is pushed once and stays rooted until popped (`docs/archive/p11-over-rooting-handoff-2026-06-28.md` §A) |

A stack map is read by the collector at each safepoint, and Sprout's runtime has no reader for
one. A frame per function is the shadow-stack model Sprout already runs, applied to more values.

## 5. Design

### 5.1 Which values get a slot

**F = cross ∩ T**, where cross = `heap_origin` ∩ (∪ over non-entry blocks of `live_in`) and T =
the heap values live across some trigger (`op_triggers_gc_summary`): for a trigger at index i of
block B, a value that exists at i (in B's `live_in`, or defined at an index before i; not the
trigger's own result) and is live after i (in B's `live_out`, or used at an index after i). F = ∅
when the function has no trigger. A function has a frame iff F ≠ ∅, or a heap `IRTcoLoad` result
is in T (then F may be empty and the frame holds only TCO slots). Both inputs exist before the
rewrite: `compute_liveness` and `compute_heap_origin` already run per function in `rewrite_fn`.

T is computed per block from the same index the rewrite uses (`block_live_of`: live-out plus last
use per name), in one forward pass that tests each candidate at the first trigger after it exists.
Liveness only shrinks along a block, so a value dead after that trigger is dead after every later
one. No set per op. A block with no trigger is skipped.

The entry block is left out because every used parameter is in its `live_in` (`block_live_in`)
and in `heap_origin` (`params_heap_origin`), so including it would give a frame to every
triggering function with a heap parameter, single-block ones included. A value whose rooted range
is inside the entry block, parameters included, stays block-local. In a TCO function this also
keeps out the `%p$in` names, which only `IRTcoEntry` uses.

Without the second factor, F gave a slot, a frame and a memset to values never live across a
trigger. `fn pick(xs, n) = if cheap(n) then xs else Cons(n, Nil)` paid a frame (memset, scan push,
store, pop) for `xs`, live into the `then` arm and dead at the only trigger; the old rooting
pushed nothing for it (+53% run time, measured). A deep non-tail recursion whose per-level
temporary dies before the recursive call kept it in a slot for the whole depth (+56% peak RSS).

Everything else is **block-local**: it is rooted by `pending` and the block's root stack as
before, in each block it is live in, so narrowing F cannot make rooting unsound. Such a value
live into a block is seeded into `pending` there; it is pushed only if a trigger of that block
finds it live after, or exposes it as an operand (`op_exposes_operands`). A cross-block value
that is only a trigger's operand and dead after it (the stage values of
`tests/cost/rooting_frame_chain.sprout`) is pushed once, in that block, instead of owning a slot.
Counting exposed operands in T was tried and rejected: the compiler's own IR had 3,270 fewer
static pushes but 468 more frames, and a slot keeps such a value alive until the function returns,
so the deep recursion above kept its temporary per level again (`str_len(s)` is a trigger).

### 5.2 Entry

The frame ops are the **first ops of the entry block, before `IRTcoEntry`**: a static
`alloca [|F| x i64]`, one `llvm.memset` of its 8·|F| bytes to zero, and one
`sprout_gc_push_scan_root(frame, 8·|F|)`. Each value of F gets a fixed index. The order matters:
`IRTcoEntry`'s `stacksave` must come after every alloca that outlives an iteration, or the
back-edge `stackrestore` frees a frame that is still registered (`sprout_ir` `IRTcoEntry`
comment).

`drop_unused_stack_resets` keys on `IRRoot`. The frame ops are not `IRRoot`, so a TCO loop whose
pushes all move to the frame loses its `stacksave`/`stackrestore` pair, which is wanted.

### 5.3 Definitions and blocks

- Store each value of F into its slot right after it is defined: parameters at entry after the
  frame push, an op result after the op (including a trigger's own result), phi results after
  the block's phi group, a TCO header's values after its `IRTcoLoad`s (but see §8 Q6).
- `rewrite_block_full` seeds `pending` from `live_in ∩ heap_origin` minus F, and
  `update_scope` does not add a value of F to `pending`. So `roots_across` never returns one,
  and no value of F is pushed block-locally.

### 5.4 Exits

In a function with a frame, every exit pops the frame together with the block's own roots, as
one `pop_roots(n + 1)`. The rule is structural, not a list: **a block whose last op has
`op_successors` = `Nil` and is not noreturn** (`op_successors` is also `Nil` for every
non-terminator, so the rule reads the last op only). Today that is `IRRet`, `IRRetVoid` (the end
of `__sprout_init_globals`), `IRRetUnboxed2/3` and `IRTailCall`. `finish_block` already places
pops before the terminator, which `musttail` requires. Noreturn paths (`panic`, `IRAbortMatch`)
need no pop: nothing resumes after a panic. A suspended task that is cancelled or times out
(`force_drop_task` in `runtime/sprout_scheduler.c`) has its roots and its green stack reclaimed
together, so a frame on that stack goes with them.

### 5.5 TCO back-edge

No pop and no re-push: the frame stays registered across iterations. The slots of F are cleared
at each `IRTcoBack` with one `llvm.memset` over the indices after the TCO slots (§8 Q6 (b): those
hold the next iteration's input, and a loop whose frame is only TCO slots clears nothing). That is
sound because no SSA value spans the back-edge, so every value of F is dead there, and the header
re-stores what it loads before any trigger. `render_loop` has one `IRTcoBack` site, so one memset
of up to 4 KB per iteration.

### 5.6 What landed

- **New IR ops** in `stdlib/compiler/sprout_ir.sprout`, none a trigger, none an operand exposer:
  `IRFrameEnter(frame, slots, discard)`, `IRFrameStore(frame, index, value)`,
  `IRFrameSlot(slot, frame, index, init)` and `IRFrameClear(scratch, frame, start, count)`. The
  frame's pop folds into `IRUnroot`'s count, so it has no op of its own.
- **Frame-backed TCO slots (§8 Q6 (b)).** `IRFrameSlot` defines the slot's *name* as a
  `getelementptr` into the frame and stores the incoming parameter into it. `IRTcoLoad` and
  `IRTcoBack` name the slot as before and lower to the same `load`/`store`; only the definition
  changed, from the `alloca` that `IRTcoEntry` emits to the op. `ir_rooting` lists the frame-backed
  slots out of `IRTcoEntry`, so the entry reads `IRFrameEnter`, one `IRFrameSlot` per heap
  parameter, the entry's `IRFrameStore`s, `IRTcoEntry` (scalar slots, `stacksave`). Smallest
  change that keeps `ast_to_ir` and the lowering of the two TCO ops untouched, and it is the one
  place a TCO slot's owner is stated, which the verifier reads. `IRFrameClear` takes a start index,
  so the back-edge zeroes only the indices after the TCO slots.
- **Exhaustive matches** extended: in `ir_rooting`, `op_triggers_gc` (`false`),
  `op_produces_simple_heap` (`Nothing`), `op_uses` (`IRFrameStore` uses its value and
  `IRFrameSlot` its `init`; never the frame) and `op_exposes_operands` (`false`); in `sprout_ir`,
  `op_direct_callee` (`Nothing`) and `print_op`; in `ir_lowering`, `lower_op`. `op_def` has a
  catch-all and needs no arm: the frame and the slot names are not liveness-tracked.
- **`ir_rooting`**: `plan_frame` in `rewrite_fn` computes F; `update_scope` and the block seed
  skip a frame value; `finish_block` pops the frame at an exit and clears before `IRTcoBack`;
  the stores are added after the rewrite (`add_frame_stores`), so the per-op index that
  `BlockLive` is keyed on is untouched.
- **`ir_lowering`**: lowers the four ops, and `runtime_declares` gained
  `decl("i64", "sprout_gc_push_scan_root", "ptr, i64")` and `llvm.memset.p0.i64`.
- **Verifier**: `ir_verify` checks the frame (§6 Checker; `docs/gates.md` §Root verifier).
- **Docs**: `docs/compiler-internals.md` §Type-aware GC rooting and its moving-GC bullet; the
  `sprout_ir` comments on `IRRoot`, `IRTcoEntry` and `IRTcoBack`; the `ir_rooting` header.

For `tests/cost/rooting_frame_chain.sprout` (24 `if`s in a TCO loop, 127 blocks) the function pushed
50 roots before and pushes one scan root and no `i64` root now. `render_loop` is not measured here.

## 6. Safety argument

Invariant: **at every trigger, each heap value live across it is either in its frame slot,
holding its current value, or on the block-local root stack.**

Premises, each needing a test:

1. **F is complete.** A value live across a trigger in a non-entry block it was not defined in is
   in that block's `live_in` and in T, hence in F. A value live only within the entry block is
   seeded and rooted block-locally, as today, and so is one that lives into a later block but is
   dead at every trigger: it is in cross but not in T, and block-local rooting pushes it where it
   is an operand of an exposing trigger. Test: a value defined in one block and live across a
   trigger in a successor gets a slot; so does one live across a trigger only through `live_out`;
   a heap parameter of a single-block function gets none; nor does a value live into a later
   block and dead at every trigger (`pick`), whether or not the function has a frame for others.
2. **A slot holds a value while the value is live.** The store follows the definition before
   any later op, and the slot is written again only after the value is dead: by the next
   execution of its own definition, or by the back-edge clear (§5.5). Test: a store follows each
   def in F, including a phi, an `IRTcoLoad` and a trigger's own result.
3. **The frame is registered from before the first trigger until every exit.** Test: no trigger
   precedes the frame push, and every function-leaving terminator, including `IRRetVoid` in a
   multi-block `__sprout_init_globals`, is preceded by a pop that counts the frame. A missed pop
   is not only a leak: a finished awaitable task keeps its root context while its green stack is
   destroyed (`sprout_ctx_destroy` in the pump, `runtime/sprout_scheduler.c`), so the next mark
   would read freed memory. Test: the same function run as an awaited task under
   `SPROUT_GC_STRESS=1`.
4. **Liveness stays sound with a back-edge.** Single-pass liveness is sound only because no SSA
   value spans the `IRTcoBack` edge. Frame slots are memory, so the design keeps this premise and
   must not make an SSA value live across the back-edge. No test covers a back-edge today
   (`tests/stdlib/test_ir_rooting.spr`). Test: a loop carrying a heap parameter.
5. **Block-local rooting is unchanged.** PR #108's argument covers values not in F. Its §5
   predates the pending-only scan; the current invariant is "rooted ∪ pending ⊇ live ∩ heap at
   every trigger". Test: keep T15b and add a pending-only case.
6. **The GC does not move objects.** A slot is written once per definition and never reloaded
   for the activation, which is sound only while the collector is non-moving mark-sweep
   (`docs/compiler-internals.md` §Non-moving GC). `IRRoot` rests on the same premise.
7. **Pushes and pops of one activation run on one root context.** The pump calls
   `sprout_roots_switch` before every `sprout_ctx_switch`, so a frame pushed before a task parks
   is popped from the same task's stack after it resumes. Test: a frame function that parks
   between push and pop, with another task allocating meanwhile.

**Over-retention** is the safe direction: the GC looks every word up with
`sprout_heap_lookup`, so a zero or stale word is ignored or kept alive, never misread. Its costs:

- *Within an activation:* a value stays reachable from its death until the function returns (or
  the next back-edge in a TCO loop). Today `pop_dead_top` or the block-end pop releases it.
- *Deep non-tail recursion:* each activation keeps its dead values until it returns. A function
  that builds a temporary, e.g. a string passed to a call, then branches and recurses keeps one
  dead temporary per level, where today each is released at the next trigger. This can raise
  peak memory on recursive code, the compiler included, and needs a measurement (§7).
- *TCO loops:* bounded to one iteration by the back-edge clear.

**Checker.** PR #108 promised a per-trigger "new roots ⊇ old roots" check and a push-count guard.
The push-count guard is the frame-chain arm of `scripts/rooting_cost_gate.sh` (§7). The check is
`ir_verify.verify_rooting` (`stdlib/compiler/ir_verify.sprout`, `just verify-roots`, in
`ci-fast-gates`; `docs/gates.md` §Root verifier). It recomputes liveness as a fixpoint over the
CFG, independently of `ir_rooting`'s single pass, and requires every heap value live across a
trigger to be rooted, and no SSA value to be live into a TCO loop header (premise 4): the
back-edge clear would turn such a value from over-retained into freed. Uses, defs and
classification come from `ir_rooting`, so a misclassification there passes. For the frame it
treats the frame as the bottom entry of the root stack: a value counts as rooted at a trigger when
the frame is registered and the value owns a slot whose store has run, or is the load of a
frame-backed TCO slot; the frame is registered by `IRFrameEnter`, popped by every exit, and is
the whole stack at a non-exit block's end. It also rejects a slot with two owners, a
back-edge clear that covers a TCO slot, and a store that does not follow its definition before the
next trigger.

## 7. Tests, gates and measurements

- ~~Unit tests~~ done: `tests/stdlib/test_ir_rooting.spr` (FR1–FR5 and T15/T15b, whose counts
  changed because `%p` now owns a slot), `tests/stdlib/test_ir_verify.spr` (each violation kind)
  and `tests/stdlib/test_frame_slot_rooting.spr` (an awaited task, a task parked with frame
  values while another allocates, deep recursion, a TCO loop; in `just test-stress`). The
  executable tests cannot see a missing frame pop (BACKLOG).
- ~~Cost fixture~~ done: `tests/cost/rooting_frame_chain.sprout` and the push-count arm of
  `scripts/rooting_cost_gate.sh`; `docs/gates.md` §Compiling one long block.
- ~~`just test-stress`, `just test`, `just compile-examples-stage1`, the example canary,
  `just rooting-cost-gate`, `just scc-cost-gate`, `just o2-codegen-smoke`, reseed~~ done.
- ~~**Golden IR**~~ done: `scripts/golden_ir_root_strip.sh` (`just golden-root-strip`; see
  `docs/gates.md` §Golden IR). The stripped residual is empty over all 75 goldens and over a
  512k-line self-compile of the compiler; the snapshots were regenerated after reading samples
  of the full diff (a TCO function, multi-block functions, single-block ones).
- **Seen in the goldens**: a small function whose only cross-block heap value is a parameter used
  in a `match` arm paid a frame (zeroing, scan push, store, pop) for a value that is dead at the
  arm's one trigger, e.g. `vec_get(index, vec)` in the `aoc_2025_day_3` golden. §5.1's F = cross ∩
  T removes it: `vec_get` is back to its one block-local root.

### 7.1 Measured (2026-10-26, F = cross ∩ T)

Old is the compiler at `3baa8cf9`, before frames; new is this design. The same source, emitted by
each and linked with `clang -O2` against the same runtime. Run times are medians of 5 interleaved
old/new pairs on a loaded M3 Pro; new was faster in every pair where a change is listed. Only the
ratios carry: the same nqueens binary ran 2.57 s and 1.73 s in two windows of the same day.

| workload | old | new | change |
|---|---:|---:|---:|
| `bench/gc_roots` | 1.09 s | 0.81 s | −26% |
| nqueens (`examples/nqueens.sprout`) | 1.73 s | 1.60 s | −8% |
| `bench/dispatch` | 0.16 s | 0.13 s | −19% |
| guard-first helper (`pick`, §5.1) | 0.18 s | 0.17 s | none |
| deep non-tail recursion, 40,000 levels: peak RSS | 7.0 MB | 7.1 MB | none |
| compiler self-compile (`--emit-ir` of `compile_driver.sprout`): peak RSS | 280 MB | 258 MB | −8% |

The self-compile's time was within noise (3.6–4.2 s both sides). The first version of F (§5.1,
without T) measured +53% on the guard helper and +56% peak RSS on the recursion (at 40,000 levels:
7.1 → 11.1 MB).

`uncharted-suns` `df24d438`, by the commands in §1; link times are one run each, old and new in
different windows:

| | old | new |
|---|---:|---:|
| `render_loop` IR lines | 67,190 | 7,285 |
| `sprout_gc_push_i64_root` calls in it | 20,338 | 340, plus 1 frame push (169 slots) |
| `sprout_gc_pop_roots` calls in it | 437 | 203 |
| game IR lines | 292,034 | 201,397 |
| whole-program link | 84.4 s | 8 s |
| normal link | 6.4 s | 4 s |

The compiler's own IR: 2,357 of 6,332 functions have a frame, holding 7,697 slots. Every slot's
value was pushed at least once by the old rooting, so no frame roots a value nothing needed rooted.
Static `i64` pushes: 58,970 → 33,973.

## 8. Alternatives and open questions

Fixes at other layers, and why they are not the main lever:

- **Skip re-pushing parameters only** (the BACKLOG entry this replaces): 2,420 of
  `render_loop`'s 20,338 pushes (12%). A subset of this design.
- **Fewer blocks**: the other factor in blocks × live values. A `do` of `if`s needs its blocks;
  cutting them changes lowering, not rooting, and helps only that shape.
- **A LIFO stack that stays non-empty along the dominator tree** (push in the dominating block,
  pop at exits): no frame and no zeroing, but every join must see the same stack depth on all
  incoming edges, which branch-local pushes break. The frame removes that constraint.
- **Inline push lowering** (`docs/cross-tu-inlining-v0.md` §7, Route B, built, −6.1%): makes
  each push cheaper, not fewer. Orthogonal.
- **Splitting `render_loop` in the game**: helps one function and leaves the cause.

Open questions:

1. **Frame layout: one scan root, or one `push_i64_root` per slot?** A scan root is one pool
   entry and one push per call. Recommend the scan root.
2. **Clearing beyond the back-edge.** (a) None (v0's default outside TCO loops). (b) Zero a slot
   where its value dies: precise, but a store after the value's last use in every block where it
   dies, per path. Recommend (a), with (b) if peak RSS moves.
3. **Coloring.** Values whose live ranges never overlap could share a slot, shrinking the frame
   and the clearing. It needs an interference graph over F. Recommend v0 without it, measuring
   frame sizes first (`render_loop`: up to 509 slots, 4 KB).
4. **Trigger-free paths.** A function with a heap value live into a non-entry block and across a
   trigger on some path pays the memset, the frame push, a store per value and the pop on every
   call, even on a path that pushes nothing today, e.g.
   `fn f(x) = if cheap(x) then x else g(alloc(), x)` (`x` is live into both arms and across the
   `alloc`). Single-block functions, functions whose heap values stay in the entry block, and
   functions whose cross-block values are dead at every trigger, get no frame. Placing the frame push at the nearest common dominator of the
   triggers would avoid it, at the cost of a dominator computation. Recommend entry placement in
   v0 and measuring a guard-first helper; move the push only if it regresses.
5. **First-PR scope.** The checker is in place (§6). Frame values only, or also hoisting
   block-local `IRRoot` allocas to the entry block, which would let more TCO loops drop
   `stacksave`? Recommend frame values only.
6. **Carried values: copy into the frame, or make the TCO slots frame slots?** `IRTcoEntry`'s
   per-parameter allocas already hold each carried value for the whole iteration: `IRTcoBack`
   writes them just before the `br`, with no trigger between. (a) As in §5.3: each `IRTcoLoad`
   result is stored again into a frame slot, one redundant store per carried heap value per
   iteration, and cleared at the back-edge. (b) Put the heap-typed TCO slots inside the frame
   array, at its first indices: `IRTcoEntry` and `IRTcoBack` store into them, their `IRTcoLoad`
   results leave F, and the back-edge memset covers only the indices after them. No copy and
   no second mechanism, but `IRTcoEntry`/`IRTcoBack` lowering changes and `ir_rooting` must know
   which loads are covered. Recommend (b).
