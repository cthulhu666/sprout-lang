# Lowering the GC root stack in `ir_lowering` (v0, built and rejected, 2026-09-20)

**Status: implemented and measured — and it does not pay off. See 10 before reading 1.**
The design below is what was built; 10 records what it actually delivered and why the estimate
in 1 was wrong.

## 1. Problem

Every rooted local costs a call into the C runtime. `docs/cross-tu-inlining-v0.md` shows that call
can never be inlined — `ir_lowering` emits functions with no `target-cpu`/`target-features` while
clang gives the runtime's the host's full set, and LLVM only inlines when the callee's features are
a subset of the caller's. No optimisation level, LTO flag or `always_inline` attribute gets past it.

The cost is large and concentrated. Making just the four root-stack entry points inlinable, with
the rest of the runtime left opaque, is **−27.5%** on `bench/gc_roots`, which is 71% of everything
whole-runtime inlining would buy. The emitted IR for that benchmark calls the push 156 times and
the pop 59 times, against roughly 110 calls to all other runtime functions combined.

## 2. Goals and non-goals

**Goals**

1. Remove the call boundary for `IRRoot` and `IRUnroot` on the fast path.
2. Keep emitted IR target-neutral. The seed is committed and must build on macOS arm64, Linux
   x86_64, Linux aarch64 and Windows.
3. No change to the build pipeline, to any justfile recipe, or to the release workflow.
4. Keep the layout knowledge in exactly one place in the emitted module, not at every root site.

**Non-goals**

1. Inlining any other runtime function. This is the root stack only.
2. The route-A build-pipeline change (`llvm-link` + whole-module `-O2`). It buys the remaining
   ~11 points at the cost of ~700 ms of link per binary; `docs/cross-tu-inlining-v0.md` §5.1.
3. Changing the rooting *model* — what gets rooted, when, or the type-aware rules in
   `docs/compiler-internals.md`. Only the lowering of an existing IR node changes.
4. 32-bit support. The offsets below assume 8-byte pointers and `size_t`, as the runtime already
   does throughout.

## 3. Prior art

Surveyed in `docs/cross-tu-inlining-v0.md` §8, verified against primary sources. GHC persists Core
unfoldings in `.hi`, Swift serializes SIL into the `.swiftmodule`, and Rust stores MIR that is
codegened into the caller's codegen unit. All three export the body **at their own IR level, above
LLVM**; none merges LLVM modules at link time. This proposal is Sprout's version of that choice.

## 4. Implementation overview

### 4.1 Shape: one emitted helper, not 156 open-coded sequences

`ir_lowering` emits, once per module, an `alwaysinline` definition with **no attribute group**, and
leaves each root site as a plain call to it. Both caller and callee are Sprout-emitted and carry no
target features, so the subset rule is satisfied and the inliner runs.

Open-coding the sequence at all 156 sites was the obvious alternative and is worse: it multiplies
emitted IR, grows the committed seed (already 390k lines), and spreads the struct layout across
every root site instead of one function.

```llvm
@sprout_current_roots = external global ptr

define internal i64 @sprout_inl_push_i64_root(ptr %slot) alwaysinline {
entry:
  %rc    = load ptr, ptr @sprout_current_roots
  %topp  = getelementptr inbounds i8, ptr %rc, i64 16
  %top   = load i64, ptr %topp
  %sizep = getelementptr inbounds i8, ptr %rc, i64 8
  %size  = load i64, ptr %sizep
  %ok    = icmp ult i64 %top, %size
  br i1 %ok, label %fast, label %slow
fast:
  %pool  = load ptr, ptr %rc
  %off   = mul i64 %top, 24
  %node  = getelementptr inbounds i8, ptr %pool, i64 %off
  store ptr %slot, ptr %node
  %kindp = getelementptr inbounds i8, ptr %node, i64 8
  store i64 1, ptr %kindp                      ; SPROUT_ROOT_I64
  %auxp  = getelementptr inbounds i8, ptr %node, i64 16
  store i64 0, ptr %auxp
  %top1  = add i64 %top, 1
  store i64 %top1, ptr %topp
  ret i64 0
slow:
  %r = call i64 @sprout_gc_push_i64_root(ptr %slot)
  ret i64 %r
}
```

The pop is the same shape: load the context, subtract, store. Its `count < 0` check is dead in
emitted IR — the count is always a literal — and its underflow check becomes the cold branch.

The `store i64 1` into `kind` **presumes the field-type change in 4.3**. Against today's 4-byte
enum plus 4 bytes of padding that store happens to read back correctly on a little-endian target
and not on a big-endian one, which is a bug waiting rather than a portability footnote. The two
changes ship together or neither does.

**The overflow path needs no new runtime symbol.** It calls the existing
`sprout_gc_push_i64_root`, which re-checks and reports through `tcp_fail` exactly as today. The
declaration is already emitted (`ir_lowering.sprout:512`).

### 4.2 The one runtime surface change — needs approval

`g_current_roots` is `static` (`runtime/sprout_runtime.c:1614`), so emitted IR cannot name it. It
must lose `static` and be renamed `sprout_current_roots`.

This is exported runtime surface, so it is a decision, not an implementation detail. Two things
make it smaller than it looks: the pointer is already exposed read-only through
`sprout_roots_current()`, and already mutated from outside the file's logic by
`sprout_roots_switch()`, which the scheduler calls on every task switch. No new function is added.

### 4.3 Layout coupling, and the precedent for it

The helper hard-codes `struct SproutRoots` as `{ ptr pool@0, i64 pool_size@8, i64 pool_top@16 }`
and `RootNode` as 24 bytes with `slot@0`, `kind@8`, `aux_words@16`.

This is not a new kind of coupling in this file. `ir_lowering` already open-codes a C struct
layout for vector access, with the layout documented inline (`ir_lowering.sprout:339-341`):
`VectorVal { i64 len@0, i64 cap@8, i64* data@16 }`. The same convention applies here.

One tidy-up belongs with it: `RootNode.kind` is `SproutRootKind`, an enum, so its width is
implementation-defined and the 4 bytes of padding after it are implicit. Changing the field to
`long long` makes the layout explicit without changing `sizeof(RootNode)`, which stays 24.

### 4.4 Correctness note: the context pointer must not be hoisted across a yield

The scheduler reassigns `sprout_current_roots` at every task switch, so the load of the global must
not be hoisted across anything that can yield. An external global is already clobberable by any
call as far as LLVM is concerned, so the default behaviour is correct — but this must not be
"optimised" later by marking the global constant, `unnamed_addr`, or otherwise promising it does
not change. A test that roots across a yield is listed in 7.

## 5. Impact on the language

None. Syntax, semantics, typing rules, evaluation order, visibility and diagnostics are all
unchanged — this replaces the lowering of two existing IR nodes and adds no surface a Sprout
program can observe. `docs/spec-v0.md` needs no edit.

The one observable difference is a GC root pool exhaustion message arriving from the same
`tcp_fail` call as today, via the cold path, so even the error text is unchanged.

## 6. Compatibility and migration

- **The seed.** Emitted IR changes, so `bootstrap/compile_driver.ll` must be refreshed in the same
  change (Definition of Done #9), and `just ir-golden-diff` will report a diff for every golden —
  expected, and each must be read before regenerating, not after.
- **ABI.** A committed seed that encodes the layout means a later change to `struct SproutRoots`
  breaks old binaries as memory corruption rather than a link error. This is the real cost of the
  proposal. Mitigation: the layout is asserted in C next to the struct with
  `_Static_assert(offsetof(...) == ..., ...)` for every offset the emitted IR uses, so a struct
  edit fails the build instead of miscompiling.
- **No migration for users.** Nothing in the source language changes.

## 7. Tests

1. **Regression oracle for 4.4**: root a heap value, yield to another task that allocates and
   drives a GC, resume, and read the value. `tests/stdlib/test_task_cooperative.spr` is the
   existing shape; this needs its rooting exercised specifically against a hoisted context load.
2. **Pool exhaustion still reports**: a program that overflows the root pool must fail with the
   existing message, exercising the cold branch.
3. **Stress**: the `test-stress` set already drives rooting under collect-on-every-allocation with
   `SPROUT_FL_VERIFY`; a rooting regression presents there as a collected-while-live abort.
4. **Golden IR**: the new helper and the changed `IRRoot`/`IRUnroot` lowering appear in every
   golden, which is the point — the diff is the review artifact.
5. **`_Static_assert` coverage** for each offset the emitted IR encodes.

## 8. What would make this the wrong call

- If the measured win on a real workload is much smaller than on `bench/gc_roots`. The benchmark's
  inner loop is almost entirely root pushes, so −27.5% is a ceiling, not an expectation. The
  compiler saw −11.8% from *whole-runtime* inlining, so its root-stack share is smaller again.
  **`uncharted-suns` is the workload that prompted this and it is still unmeasured.**
- If the emitted-IR growth slows the compiler or inflates the seed more than the runtime win
  repays. One helper per module is small, but it is unmeasured.
- If the ABI coupling is judged too high a price for a codegen win, given that a struct change
  would corrupt memory rather than fail to link. The `_Static_assert` mitigation reduces but does
  not remove this.

## 9. Open question for approval

Only one blocks a start: **may `g_current_roots` become exported runtime surface as
`sprout_current_roots`?** Everything else in this document is ordinary implementation work inside
`ir_lowering` and the runtime's own header.

## 10. Outcome: measured, and the estimate in 1 was wrong

Built as designed. Correct — `opt --passes=verify` clean, answers identical, the helper inlines
(0 surviving `sprout_inl_*` call sites), and `tests/stdlib/test_gc_root_cross_task.spr` passes.

`bench/gc_roots`, interleaved, min of 12, same source and runtime, compiler as the only variable:

| build | time | vs today |
|---|---:|---:|
| today (call into C) | 1052.6 ms | — |
| this design, per-TU link | 987.9 ms | **−6.1%** |
| this design + whole-module merge | 988.2 ms | −6.1% |
| this design + merge + `internalize` | **645.2 ms** | **−38.7%** |
| route A alone, unmodified runtime | 652.5 ms | −38.7% |

**The export in 4.2 is what costs the win.** `docs/cross-tu-inlining-v0.md` §3 measured the
root stack at 71% of the available win by stripping four functions inside a merged module — but
in that module the root-context pointer was `static`, so LLVM knew every writer and could prove
the root-pool stores do not alias Sprout heap objects. This design requires the pointer to be
exported, which destroys that analysis. The `internalize` row isolates it: identical code,
identical merge, symbol visibility the only variable, −6.1% → −38.7%.

So `static` on that global was load-bearing optimisation information, not an access-control
preference, and 3's decomposition was measuring a benefit this design structurally cannot have.

Two further hypotheses were tested and refuted before that one: an external global forcing a
reload per push (merging fixes visibility and changed nothing, −6.1%), and the cold branch
returning rather than aborting (rewriting it to `unreachable` gave −9.0%, not −27.5%).

**Recommendation: do not land this.** The last row is the finding — route A alone reaches the
same −38.7% with no lowering change, no exported global and no ABI coupling. Its cost is link
time (818 ms per binary, `docs/cross-tu-inlining-v0.md` §5.1), which is why it belongs on
long-lived binaries only and not on the 416-binary test path.

The one artifact worth keeping either way is `tests/stdlib/test_gc_root_cross_task.spr`: heap
values held across a task switch while another task allocates, an oracle the suite did not have.

As first written it was not one. It allocated 1245 objects against a 4096 threshold, so its only
collection was the one at exit, after both assertions had passed — break `sprout_gc_mark_roots`
to scan only the current root context and it still exited 0. The churn now allocates 5000 and
asserts that it did; the same break then crashes it with a use-after-free. The lesson generalises
past this file: a GC test that does not name the count it must exceed is passing on nothing.
