# Sprout-level optimization passes (CSE, LICM) and the A/B harness — v0

**Status — arc parked 2026-09-11.** No syntax, typing-rule, evaluation-order or diagnostic change
happened; `spec-v0.md` is untouched, as planned.

| milestone | outcome |
|---|---|
| **M0** — A/B harness | **landed.** `SPROUT_OPT_OFF`/`SPROUT_OPT_STATS`, `just bench-opt`, `just opt-harness-check`. Baseline: `bench/results-2026-09-11-opt.md` |
| **M1** — CSE | **measured and declined.** Opportunity real, payoff nil: `bench/results-2026-09-11-cse-census.md` |
| **M2** — LICM | **open, not refuted by M1.** `BACKLOG.md` P3, gated on a hand-applied-hoist A/B |
| **M3** — LLVM `declare` attributes / LTO | **open**, untouched by this arc. `BACKLOG.md` |

**What this arc established, in one line:** the two passes it set out to add turned out not to be
worth adding, and the instrument that proved it is the durable result. DLE removes zero nodes from
real code; CSE has 673 real sites in the compiler that LLVM genuinely will not take, and taking 49
of them by hand changed nothing measurable. Whoever proposes either pass again should read the two
results files first — both are re-runnable.

One open gap in the harness itself: `SPROUT_OPT_OFF` cannot reach `dce.elim_unreachable`, the pass
that actually does large work, because it runs below the seam. `BACKLOG.md` P2.

Scope correction up front: **DCE already exists.** `stdlib/compiler/dce.sprout` implements dead-let
elimination and declaration reachability. Dead-let runs through `compiler.run_opt_passes`
(`compiler.sprout:457`, called from `:579` and `:651`); reachability runs separately inside
`ir_pipeline.sprout:338`. The purity oracle (`is_pure_callee_type`, exposed as `dce.callee_is_pure`)
reads the effect row. The proposed passes were CSE and LICM; DCE is the shape they copy.

## Problem

`-O2` already runs on every Sprout binary (`justfile:133` and ~20 sibling sites), so LLVM's
`EarlyCSE`, `GVN`, `LICM` and `ADCE` are present. They fire on scalar operations and never on
Sprout-level calls, for two reasons visible in the emitted IR:

1. **The shadow stack makes every function look like it clobbers memory.** Every rooted value gets
   an `alloca` whose address is passed to an opaque external call
   (`tests/golden/ir/examples__aoc_2025_day_1.sprout.ll:682-685`):

   ```llvm
   %t$6 = alloca i64
   store i64 %t$1, ptr %t$6
   %t$7 = call i64 @sprout_gc_push_i64_root(ptr %t$6)   ; alloca escapes
   %t$2 = call i64 @print_value(i64 %t$1)
   ```

   LLVM's `FunctionAttrs` therefore infers `memory(readwrite)` on essentially every
   allocating Sprout function. Neither GVN nor LICM will dedupe or hoist a call it believes
   writes arbitrary memory.

2. **No LTO.** `clang a.ll b.c c.c -O2` compiles each translation unit separately, so `runtime/*.c`
   bodies are invisible to the Sprout IR unit and every runtime import is a bare `declare` with no
   attributes. Already filed in `BACKLOG.md` as "The emitted LLVM `declare`s carry no
   attributes".

The fact that licenses CSE and LICM — purity, from the effect row — is erased during lowering
and never reaches LLVM in any form. The compiler knows it; the backend cannot.

## Goals

- CSE and LICM over Sprout-level calls, at the typed-AST tier where the effect row is still present.
- An A/B harness that measures a pass ON vs OFF from **one** compiler build.
- Static per-pass counters usable as a CI regression detector.

## Non-goals

- Re-implementing scalar CSE/LICM. LLVM does that well and already runs.
- Any change to `-O2`, the rooting model, or the shadow stack.
- An optimization that is not measurable with the harness in M0. If it cannot be measured, it does
  not land.

## Prior art

The decision is where these passes live when a language has both its own typed IR and an LLVM
backend. Every row verified against a primary source.

| language | CSE | loop-invariant motion | where |
|---|---|---|---|
| **GHC** | `-fcse`, "off but enabled by `-O`" | `-ffull-laziness`, "floats let-bindings outside enclosing lambdas, in the hope they will be thereby computed less often"; same default | Core, its own IR — unchanged whether the backend is NCG or `-fllvm` |
| **Swift** | no CSE pass in the current `Passes.def`; `redundant-load-elimination` instead | `PASS(LoopInvariantCodeMotion, "loop-invariant-code-motion", ...)`, `include/swift/SILOptimizer/PassManager/Passes.def:159` | SIL, above LLVM |
| **Rust** | leans on LLVM | leans on LLVM | MIR passes exist, but the stated aim is "it means that LLVM has less work to do"; the distinctive win is that MIR "is generic (not monomorphized yet) [...] so all of the monomorphizations are cheaper" |
| **OCaml (Flambda)** | not named | not named as LICM, but specialises on **invariant parameters**: arguments that "during the execution of the recursive function(s) themselves [...] never change" | its own IR |

**Where they diverge, and why it matters here.** Rust delegates because its IR lowers cleanly to
LLVM's memory model and it has no GC shadow stack obscuring effects. GHC and Swift keep the passes
at their own IR because the licensing fact — purity for GHC, ownership/ARC for Swift — lives in
their IR and dies below it. Sprout is in the second group for exactly GHC's reason. This is the same
argument that put `dce.sprout` at the typed-AST tier rather than asking LLVM to do it.

Flambda is the most directly useful row: its *invariant parameter* analysis over recursive functions
is precisely the loop notion M2 proposes below, arrived at independently by a language with the same
"loops are recursion" shape.

## Implementation overview

### M0 — harness first, no new optimization — **landed 2026-09-11**

What shipped, and where:

| piece | file |
|---|---|
| `SPROUT_OPT_OFF` / `SPROUT_OPT_STATS` switchboard | `stdlib/compiler/opt_config.sprout` |
| node census (`node_count`) | `stdlib/compiler/typed_ast.sprout` |
| pipeline seam (`run_opt_passes`) | `stdlib/compiler/compiler.sprout` |
| self-check gate | `just opt-harness-check`, in `just ci-fast-gates` |
| A/B bench | `bench/optpasses/bench.sh`, `just bench-opt [pass]` |
| first baseline | `bench/results-2026-09-11-opt.md` |

**The prediction below was wrong, and the measurement is the point of having built this.** DLE
removes **zero** nodes from every program in the bench corpus, the 110 096-node compiler included,
so `SPROUT_OPT_OFF=dle` does *not* change a real binary. Self-validation therefore comes from
`tests/opt_harness/dead_let.spr` — a deliberately wasteful shape where the pass removes 6 nodes and
the switch demonstrably reaches emitted IR — asserted by `just opt-harness-check` on every CI run.
The consolation was a clean baseline — whatever M1 removed would have been the first node this
pipeline ever removed from real code. M1 was then measured and declined, so that baseline still
stands unbroken.

`dce.elim_unreachable`, the half of `dce.sprout` that *does* do large work, is not under the switch
— it runs inside `ir_pipeline`, not at the `compiler.sprout` seam. Filed in `BACKLOG.md`.

`Pass` names `Cse` and `Licm` before they exist, so the switch's vocabulary is the roadmap. That
only works because `opt_config.implemented` marks them pending and `SPROUT_OPT_OFF=cse` warns
"nothing was disabled" — without it the knob quietly acquits a pass that never ran. **Flip the arm
in the change that lands the pass**; `just opt-harness-check` asserts the warning, so a forgotten
flip shows up as a red gate rather than a bad bisection.

`Cse` deliberately **stays** in that vocabulary even though M1 was declined. Anyone following an
older note and typing `SPROUT_OPT_OFF=cse` gets "not implemented yet — nothing was disabled",
which is exactly what they need to know; dropping the name would answer "no such pass" and read
like a typo. The status that matters lives in §M1, not the enum.

The rest of this section is the design as approved, kept because it is still the rationale.

The instrument before the experiment, and the baseline M1/M2 are measured against. Today the A/B
ritual is manual; `bench/unboxed_read/bench.sh:5-8` documents it:

> This times a SINGLE compiler; the ON-vs-OFF A/B [...] is produced by building the compiler
> once with the extension and once without (stash the `ir_rooting` change, rebuild from seed).

Stash-and-rebootstrap per measurement is slow and cannot run in CI.

**Toggle and stats as environment variables**, following the established precedent —
`compiler.sprout:554` already has `SPROUT_VERIFY_DISPATCH_OFF` as a pass kill-switch and `:446`
`SPROUT_VERIFY_DISPATCH_STATS` as a stats reporter:

```
SPROUT_OPT_OFF=cse,licm,dle    # comma list; absent = all on
SPROUT_OPT_STATS=1             # eprint static counts per pass
```

Env vars rather than CLI flags: `compile_driver.sprout:446-482` dispatches on positional list
patterns, so an orthogonal flag would have to be threaded through every arm. `compiler.sprout`
already imports `stdlib.env`, so this adds no import and no bundle change, and stays inside the
existing seed closure.

`dce.elim_program` is pure and stays pure. Read the flag in the enclosing `!{IO}`
`compile_phase_lower_with_roots` and pass an `OptConfig` record down.

**Retrofit the toggle onto the existing DCE first.** This is what makes M0 self-validating:
`SPROUT_OPT_OFF=dle` must produce a measurably different binary on day one. A harness that cannot
detect a pass already known to do work will not detect CSE either.

**`bench/optpasses/` and `just bench-opt`.** One compiler build; each program compiled twice (OFF,
ON) and run warm; emits a table and writes `bench/results-<date>-opt.md` in the existing house
format. Corpus from what already exists: `astar`, `nqueens`, `math_transcendental`, `unboxed_read`,
`digit_recognizer` — plus **the compiler compiling itself**, the largest real Sprout program there
is and the one whose speed is felt daily.

Two correctness instruments come free: `just ir-golden-diff` over the 64 files in `tests/golden/ir/`
is the review artifact for what a pass actually did, and `SPROUT_OPT_STATS` counts in CI catch a
pass that silently falls to zero after an unrelated change.

### M1 — CSE — **measured and DECLINED, 2026-09-11**

**Not being built.** `bench/results-2026-09-11-cse-census.md` has the numbers; the short form:

- The opportunity is real — 673 shareable repeated pure calls in the compiler
  (`stdlib/compiler/cse_census.sprout`, `--phase cse-census`), 4–15 in user programs. Not the
  zero DLE reported.
- LLVM will not take it: in a minimal repro, 6 of 8 identical calls survive `opt -O2` inlined
  but un-deduped, exactly as this doc predicted.
- Taking it buys nothing. Hand-CSE of the 49 densest sites moved compile time 0.00% and
  allocations by −0; hand-CSE of an *allocating* site removed **1** allocation out of 50.8
  million. Both edits emitted byte-identical IR, which is also this rewrite's soundness evidence.

The census counts **static** sites; what pays is **dynamic** frequency, and these fire a handful
of times each per compile. That is the transferable lesson: a count of optimisation sites is not
an estimate of a win, and two hand-applied rewrites settled in an hour what the pass would have
taken a milestone to learn.

The design below is kept because it is what was measured, and because `cse_census` is its
analysis half — already written, if the question ever returns with a workload that has the
multiplier this one lacked.

Narrowest useful scope: within one function body, two calls with the same callee, syntactically
equal arguments, and no intervening effectful step become one binding and a reuse. Reuses
`dce.is_pure_callee_type` verbatim.

Runs **before** DLE. `dce.sprout:17-19` records that DLE precedes reachability because shrinking
bodies can only remove references; CSE sits one step earlier for the same reason — it creates
shared bindings, which can only make more things dead.

### M2 — LICM (not refuted by M1's result; gate it on a DYNAMIC measurement)

M1 failed for lack of a multiplier: its sites run a handful of times. LICM's target is recursion,
where the body runs many times — the multiplier M1 lacked is exactly LICM's premise, so M1's
verdict does not carry over. What does carry over is the method: gate it on an A/B of a
hand-applied hoist, not on a count of invariant parameters.

Sprout has no loops, only recursion that TCO lowers. So loop-invariant becomes: *in a self-recursive
function, argument `i` is passed unchanged at every recursive call site* — a syntactic check on
call sites, and genuinely easier here than after lowering, where it is dataflow through phis.
Flambda reached the same formulation.

Hoisting needs a worker/wrapper split: compute once in the wrapper, pass in as an extra parameter.
The CPR machinery already performs this shape, which is both the feasibility argument and the reason
this is a milestone rather than a follow-up commit.

### M3 — the LLVM-side lever (orthogonal)

The `BACKLOG.md` entry "The emitted LLVM `declare`s carry no attributes" — plus an `-flto`
experiment (zero `flto` hits in the justfile today), which would let LLVM see runtime bodies and
infer attributes on `vector_length` and `vector_get_direct` itself. That entry already records the
trap: stack promotion is entangled with precise-GC rooting, so the safe attribute subset must be
established first. Note that `readnone` is **false** for a pure Sprout function — it allocates,
therefore it writes memory.

## Preconditions and risks

**Allocation identity.** Sprout-pure functions allocate, so deduping two calls makes both sites
share one heap object. No `ptr_eq`/`physical_eq`/`same_object` exists in the prelude or
`runtime/APPROVED_BUILTINS`, so object identity is unobservable and the sharing is sound. This is a
**precondition of the pass, not a permanent property** — it belongs in the pass file header the
way `ir_rooting.sprout:30-46` documents its non-allocating allow-list, so that adding a
`ref`-identity builtin later trips over it.

**LICM can lose.** Hoisting extends a value's live range, which means more GC roots held across more
trigger points. In a precise-GC language that can cost more than the recomputation saved. Expect a
regression on at least one benchmark; the harness is what settles it rather than argument.

**Bootstrap.** These passes change the compiler's own emitted IR and the compiler compiles itself.
Every M1/M2 landing needs `just refresh-seed` (DoD #9), the 2-step protocol if the IR shifts far,
and a full 64-file golden regeneration (DoD #12) with the diff **read** before staging. Golden churn
is the point, not noise: a CSE that produces no golden diff has proven it does nothing.

**Convergence.** The bootstrap fixed point still holds — stage1 and stage2 implement the same
pass, so both apply it identically — but the first landing needs the 2-step protocol
(`docs/debugging.md` §2-Step Bootstrap Protocol).

## Compatibility and migration

None. The passes are semantics-preserving and default ON; `SPROUT_OPT_OFF` exists for measurement
and bisection, not as a supported user-facing knob.

## Tests

- M0 *(landed)*: `tests/stdlib/compiler/test_opt_config.spr` (29 cases) pins the pure switchboard —
  an unrecognised name disables nothing and is reported, a declared-but-unimplemented one says so,
  and `SPROUT_OPT_STATS` follows the runtime's truthiness rule rather than "the variable is set".
  `tests/stdlib/compiler/test_typed_ast_node_count.spr` (12) pins the census, the delta case
  included. `just opt-harness-check` is the end-to-end half: the stats line appears in both modes,
  the pass removes a non-zero count, the two IRs differ, both binaries print the same thing, and
  neither an unknown nor an unimplemented pass name changes the output.
- M1 *(landed as an analysis, not a pass)*: `tests/stdlib/compiler/test_cse_census.spr` (26 cases).
  The load-bearing ones are the negatives — an `if`'s two arms are not an opportunity, a repeated
  `!{IO}` call is not counted at all, and one call in each of two bodies is not a within-body
  duplicate. Each caught a real defect in the analysis while it was being written.
- M2: per-pass unit tests on the typed AST — fires where it should, and does **not** fire
  across an intervening effectful step. Effect-row negatives matter more than positives here.
- Conformance: existing suites must be byte-identical with the pass OFF, which is the regression
  net.
