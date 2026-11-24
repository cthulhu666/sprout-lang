# Property-based testing and mutation testing — v0

**Status — proposal.** Nothing here is implemented. §4–§6 are the property-testing design, staged
for approval. §7 is mutation testing, a later and narrower stage. No language change, no new
builtin: everything is a stdlib module over `MutVec`, `Ref`, `stdlib.rng`, `stdlib.args` and
`stdlib.process`.

## 1. Problem

Tests in `tests/stdlib/` check hand-picked points. Three gaps follow.

**Inputs nobody picked.** Since bigint-v0 Stage 1, `Int` overflow traps. `rng_hash2` aborted the
process on a seed within `rng_hash_px` of INT_MAX and on a coordinate past ~1.25e11
(`stdlib/rng.sprout`, the comment above `rng_hash2`). The tests used small coordinates, so they
could not see it. Any stdlib function that does arithmetic on caller input has the same exposure.

**Hand-rolled harnesses.** Two tests already generate inputs:
`tests/stdlib/test_mutvec_sort.spr` (via `rng_hash2`) and
`tests/stdlib/test_parse_double_differential.spr` (via `rng_next`, with `--seed`/`--runs` knobs
and `just parse-double-sweep`). Each writes its own generators. Neither reduces a failing input,
so a failure reports whatever large random value tripped it.

**Laws at points.** `tests/stdlib/test_typeclass_laws.spr` checks Functor, Semigroup, Monoid,
Applicative and Monad laws on a few fixed values. A law is a statement over all values; a handful
of points is the weakest form of it.

Separately: nothing says how strong the existing ~400 test files are. The one recorded check was
done by hand — `docs/bigint-v0.md` deleted a length check in `public_key_spki` and watched all 41
cases stay green, which proved the check redundant. §7 makes that a tool.

## 2. Goals

1. A stdlib module, `stdlib.prop`, for writing properties: draw random inputs, check a predicate,
   and on failure report a **reduced** counterexample with a seed that replays it.
2. Generators compose with ordinary `do` and shrink without user-written shrinkers.
3. Fits `stdlib.test`: a property is a `TestCase`, so `run_suite` and the `SUITE FAILED` grep in
   the `_test-stdlib` recipe need no change.
4. Deterministic by default. Same seed, same cases, every target, so `just test` stays stable.
5. A failure that **aborts the process** (trap, `panic`, stack overflow) can still be reduced
   (stage 2).

## 3. Non-goals

- **A CI gate on random search.** `just test` runs a fixed seed. Wider search is an opt-in sweep
  recipe, as `just parse-double-sweep` is today.
- **A failure database.** Hypothesis and proptest save failures to disk (§5). Here a found failure
  becomes an ordinary regression test with the reduced input written as a literal, as AGENTS.md
  already requires for every bug.
- **Type-directed generation** (`Arbitrary` class, `deriving`). Deferred to its own design (§6.4).
- **Function generators** (QuickCheck's `CoArbitrary`). No current target needs one.
- **Generating well-typed Sprout programs** (Csmith-style compiler fuzzing). Highest power,
  highest cost; out of scope for v0.
- **Mutation score as a CI gate** (§7).

## 4. Design

### 4.1 One primitive: draw from a choice sequence

A generator is a plain effectful function from a `Source` to a value:

```sprout
fn gen_point(s: Source) -> (Int, Int) !{IO} =
  do
    x <- draw_int(s, -100, 100)
    y <- draw_int(s, -100, 100)
    (x, y)
```

`Source` records every choice made through it. The only primitive is `draw_below(s, n) -> Int`,
a choice in `[0, n)`. `draw_int`, `draw_bool`, `draw_one_of`, `draw_list` and the rest are
ordinary Sprout over it.

On a first run the choices come from `stdlib.rng`. On a replay they come from a given sequence,
and once that sequence runs out every draw returns 0. So the generator is a decoder from a list
of `Int`s to a value, and **shrinking happens on that list, not on the value**.

**Why this shape and not a `Gen` monad.** The main reason is shrinking: a generator that only
draws through `Source` shrinks through `bind`, where rose trees do not (§5). A second reason is
syntax: `<-` binds no user monad — the spec's `Monad` section says "the `Monad` class does not
currently wire into `do`", and `docs/try-propagate-v0.md` makes monad-generic `do` a non-goal. A
`Gen a` would be written as `and_then` chains or `map2`/`map3`. An `!{IO}` function over a
mutable `Source` gets `do`, `let..else` and `list_each` for free. This is Hypothesis's
`@composite` / `draw` shape (§5).

### 4.2 Shrinking

A failing choice sequence is reduced by passes. Each pass proposes a candidate sequence:

1. delete a block of choices (shortens lists, drops branches);
2. set a block of choices to 0;
3. lower one choice, by binary search towards 0;
4. redistribute: lower one choice by `d` and raise a later one by `d` (without it, "list sum <
   10" stops at `[5, 5]`: no deletion keeps the sum, and lowering either element passes);
5. swap two adjacent choices.

**Replay rules.** A candidate is replayed, and what the generator *actually drew* is recorded. A
draw past the end of the candidate gets 0. A choice `c` where `draw_below(s, n)` needs one below
`n` is clamped to `n - 1`; clamping keeps "larger choice, larger value". The shrinker keeps the
**recorded** sequence, and only when the property still fails and the recording is strictly
smaller than the current one. "Smaller" is shortlex: shorter first, then lexicographically lower.
Every kept step strictly decreases in a well-founded order, so the loop ends. Comparing the
recording rather than the candidate matters: after a branch change the recording can be longer.

Generators decide what shrinks well by how they decode:

- `draw_int(s, lo, hi)` decodes so that choice 0 is the value closest to 0 within `[lo, hi]`
  (§4.3 has the layout).
- `draw_list(s, g)` draws a "continue" choice before each element, Hypothesis-style. Deleting a
  continue-plus-element pair removes one element, and setting a continue to 0 ends the list.
- `draw_one_of(s, gs)` puts the simplest alternative first, so lowering the choice moves towards it.

Because every generator only draws through `Source`, shrinking survives `map`, `and_then` and
data-dependent draws. The rose-tree designs lose this under `bind` (§5).

### 4.3 Integer distribution

Full-range `draw_int` must reach the values that trap. A uniform 64-bit draw almost never hits
`INT_MAX - 1`. So `draw_int` first draws a **shape** choice — small value, boundary (0, ±1,
`INT_MIN`, `INT_MAX`, a power of two ± 1), or uniform — and then the value. Shape 0 is "small",
so shrinking moves a boundary hit towards a small value whenever the failure allows it.

The layout, so the stage-1 self-tests (§6.1) can be written against it:

- **choice unit.** One recorded choice is 15 bits. `stdlib.rng` is a 31-bit LCG (linear
  congruential generator), and `rng_below` takes the high bits (`state / 65536`), which gives
  values below 2^15. A `draw_below(s, n)` with `n > 2^15` takes several choices, high chunk
  first, so lowering the first chunk lowers the value most. A test covers an `n` above 2^15.
- **small.** One choice `k`, decoded by zigzag from the point of `[lo, hi]` nearest 0: `k = 0`
  is that point, then `+1`, `-1`, `+2`, `-2`, …, clamped to the range. The small window is
  `|v| < 256`.
- **boundary.** One choice indexing the boundary list in the order given above, 0 first.
- **uniform.** Five chunks (75 bits ≥ 64), mapped monotonically onto `[lo, hi]`. The mapping must
  not itself trap: on the full range `hi - lo` overflows, so it works per chunk.

The LCG is enough as a choice source: its job is to vary the input, not to resist prediction.

### 4.4 API sketch

```sprout
import stdlib.prop (Source, draw_int, draw_list, prop_check)

fn sorted_is_idempotent(s: Source) -> PropResult !{IO} =
  do
    xs <- draw_list(s, \t -> draw_int(t, -1000, 1000))
    note(s, "xs", xs)
    let v = vec_from_list(xs)
    prop_eq(vec_sort(vec_sort(v)), vec_sort(v))

fn main() -> Unit !{IO} =
  do
    c1 <- prop_check("sort idempotent", sorted_is_idempotent)
    run_suite("prop", [c1])
```

- `prop_check(label, p) -> TestCase !{IO}` runs `p` over a fixed seed and a default number of
  cases. On failure it shrinks and puts the reduced `note`s, the seed and the choice sequence in
  the `TestCase` detail.
- `prop_eq` / `prop_true` mirror `check_eq` / `check_true` (`Eq` + `ToString`, as `check_eq`
  constrains them) and give a `PropResult`, so the failure detail says what differed.
- `note(s, label, v) where ToString a` records a drawn value for the report. Values are not
  printed unless noted, because a generator's value need not be `ToString`.
- Knobs read through `stdlib.args`, as the parse-double harness does: `--prop-runs=N`,
  `--prop-seed=N`, `--prop-replay=<choices>`.

Names are open. `check_*` is taken by `stdlib.test`, hence `prop_*`.

### 4.5 Crashes: shrinking in a child process (stage 2)

Overflow and division by zero "panic with a source-located message" (spec §8.1), and a panic
ends the process with status 1 (`sprout_fail` in `runtime/sprout_runtime.c`). Nothing catches
it, so in-process shrinking only covers properties that return false. Overflow traps are the
main target of §1, so stage 2 is not optional.

So a crashing case is run in a child process, and the test binary is its own driver.

- **Finding its own path.** `argv_get` skips `argv[0]` (`argv_get` in
  `runtime/sprout_runtime.c`), so the path comes from the environment: the runner sets
  `SPROUT_TEST_BIN`, read with `env.env_get`. The `_test-stdlib` recipe already passes
  `SPROUT_STDLIB_ROOT` to the binary the same way.
- **Ordinary runs stay in-process.** `just test` pays nothing: no child, no extra output. A crash
  shows up as the runner's `RUN FAILED: exit 1` with the panic message.
- **Crash mode is opt-in.** `just prop-shrink <file>` builds the file, sets `SPROUT_TEST_BIN` and
  passes `--prop-isolate`. Then each property runs its cases in one child. If the child dies
  without a verdict, the parent finds the crashing case by re-running with `--prop-trace`, which
  prints each case's index before running it. Runs are deterministic, so seed plus index gives
  the choices. Then it shrinks with one child per candidate, through `--prop-replay=<choices>`.
- **Verdict.** A candidate "still fails" if the child exits non-zero or prints `SUITE FAILED`.
  `proc_run` reports a child killed by a signal as exit 1 (`sprout_proc_run_impl`), so a stack
  overflow counts.
- **Timeout.** `proc_run` takes none, so the child's argv is wrapped in `perl -e 'alarm N; exec
  @ARGV'`, the portable timeout the justfile already uses (macOS has no `timeout`). A case that
  loops then fails like a crash and shrinks the same way.

## 5. Prior art

Each row is from the library's own docs, source or changelog.

| | Generator | Shrinking | Through `bind` | Crash in a case | Replay |
|---|---|---|---|---|---|
| QuickCheck (Haskell) | `Arbitrary` class and first-class `Gen` [Q1] | user `shrink :: a -> [a]`, default `[]` [Q1] | none: a built `Gen` has no shrinker | exception caught in-process, becomes a failure [Q3] | `replay` with `usedSeed`/`usedSize` [Q2] |
| Hedgehog (Haskell) | first-class `Gen` [H1] | integrated rose tree [H2] | weak (stated by [Y2], not by Hedgehog) | `catchAny` in the runner [H6] | prints `recheckAt` seed [H5] |
| Hypothesis (Python) | strategies; `@composite` + `draw` [Y1] | internal: reduces the recorded choice sequence [Y2] | works: "shrink by shrinking the output of each draw call" [Y1] | exception becomes a failure and is shrunk [Y3] | example database; `@reproduce_failure`; `@seed` [Y3] |
| proptest (Rust) | `Strategy` values and `Arbitrary` trait [P1] | `ValueTree` simplify/complicate [P2] | `prop_flat_map` shrinks, nesting "risk[s] exponential run time" [P3] | `catch_unwind`; opt-in `fork` runs cases in a subprocess so aborts and stack overflows minimise; `timeout` implies `fork` [P5][P6] | regression files store seeds, meant to be committed [P4] |
| elm-test 2.x | `Fuzzer` values [E1] | internal, "on the PRNG history", replacing rose trees in 2.0 [E2] | `Fuzz.andThen` reintroduced because of it [E2] | not checked | `--seed` printed by the CLI [E3] |
| fast-check (JS) | `Arbitrary<T>` values [F1] | value plus context [F1] | `chain` "might not be able to shrink as much as possible" [F2] | thrown error becomes a failure [F4] | prints `{ seed, path }` [F3] |

Where they diverge, and what Sprout takes:

- **Shrinking.** Type-directed shrinkers (QuickCheck) need one per type and are lost under `map`.
  Rose trees (Hedgehog, proptest, fast-check) shrink for free but degrade under `bind`:
  "in practice the rose tree approach produces poor reductions when bind is used" (MacIver &
  Donaldson [Y2]). Internal shrinking (Hypothesis, elm-test 2.x) works through `bind`. elm-test
  moved from rose trees to it in 2.0 for that reason [E2]. Sprout takes internal shrinking. It is
  also the only design whose generators are plain effectful functions, which §4.1 needs.
- **Crashes.** The in-process designs depend on exceptions or `catch_unwind`. Sprout has neither,
  so it takes proptest's `fork` idea (subprocess per case), as a separate shrink driver.
- **Replay.** Sprout takes the printed seed (all six) and not the on-disk database (Hypothesis,
  proptest). A reduced failure is written down as a regression test instead (§3).

Stateful (model-based) testing exists in all but elm-test: QuickCheck through
`quickcheck-state-machine` [Q4], Hedgehog built in [H5], Hypothesis `RuleBasedStateMachine`
[Y5], `proptest-state-machine` [P7], `fc.commands` [F5]. With internal shrinking it needs no
separate mechanism. A drawn list of operations, run against the real structure and a `List`
model, shrinks by the list rules of §4.2. That covers stage 3.

Recursive data: every library bounds depth (`sized`, `Gen.recursive`, `st.recursive`,
`prop_recursive`, `fc.letrec`) [Q1][H1][Y1][P3][F6]. Sprout generators take an explicit depth
argument, as elm-test's docs advise [E1]. Each recursive draw lowers the depth, and reaching 0
draws a leaf.

## 6. Stages

### 6.1 Stage 1 — `stdlib.prop`, in-process

- `Source`, `draw_below`, the `draw_*` family, `note`, `prop_check`, `prop_eq`/`prop_true`,
  shrink passes, `--prop-*` knobs.
- Self-tests (`tests/stdlib/test_prop.spr`), at a fixed seed, written against §4.2–§4.3:
  - known-false properties shrink to known minima: `x < 1000` over `draw_int(s, 0, 10^6)` gives
    `1000`; "list sum < 10" gives `[10]` (needs pass 4); "no element equals 7" gives `[7]`;
  - for each, the reported sequence is a fixed point: no pass improves it;
  - same seed, same cases; replaying a printed sequence reproduces the failure;
  - `draw_below` with `n > 2^15` reaches values above 2^15.
- First users: port `test_mutvec_sort.spr`'s generators; turn the laws in
  `test_typeclass_laws.spr` into properties beside the fixed points; full-range-`Int` properties
  for `rng_hash2`, `bits` and `math` (functions that must never trap). Pick the default case
  count by measuring the suite's time increase, not by guess.

### 6.2 Stage 2 — crash shrinking

§4.5: `SPROUT_TEST_BIN`, `--prop-isolate`, `--prop-trace`, `--prop-replay`, the timeout wrapper,
`just prop-shrink`. Tests: a property that traps on `x + 1` reduces to `x = INT_MAX`; one that
loops forever on some input is reported as a timeout and shrinks.

### 6.3 Stage 3 — targets

In order of stakes:

1. **Encode/decode round-trips:** `json` (`parse`/`stringify`), `hex` (`encode`/`decode`), `uuid`
   (`parse`/`to_string`), bigint against `Int` where both are defined. `url` exports decoders
   only, so its property is "`percent_decode` never traps", not a round-trip.
2. **Model-based:** `MutVec`, `linheap`, `linvec` against a `List` model, with GC stress on.
3. **Formatter:** `fmt` is idempotent, and `parse(fmt(src))` equals `parse(src)`. Needs an AST
   generator over `stdlib/compiler/ast.sprout` and an AST equality that ignores source positions.
   `fmt` has a record of bugs (see `BACKLOG.md` §7, "Formatter and linter"), which makes it the
   most useful target in the compiler.

### 6.4 Deferred — a `Draw` class and `deriving (Draw)`

A type-directed default generator would remove boilerplate for records and ADTs, like derived
`Eq`/`Ord`/`ToString` (spec, *Derivable classes*). It is a language change (a new derivable
class), so it needs its own design. Open point: recursive ADTs need the depth argument of §5,
which a class method signature must then carry.

## 7. Mutation testing

### 7.1 What it measures

A mutant is the program with one small change (`<` to `<=`, `True` to `False`, a match arm
deleted). A test suite **kills** a mutant if some test fails on it. A surviving mutant is either
an untested behaviour or an **equivalent** mutant (same behaviour, different code). Mutation
score correlates with real-fault detection independently of coverage: Just et al., "Are Mutants
a Valid Substitute for Real Faults in Software Testing?", FSE 2014, 357 real faults in 5 programs
[J1].

### 7.2 Prior art

| Tool | Mutates | Ill-typed mutants | Cost cut | Skip marker | Gate? |
|---|---|---|---|---|---|
| PIT (Java) | bytecode, in memory [M-P1] | "non viable", rejected by the JVM [M-P2] | per-test coverage [M-P3] | `excludedMethods` [M-P5] | optional `mutationThreshold` [M-P5] |
| cargo-mutants (Rust) | source text [M-C1] | "unviable", compiled and reported [M-C1] | `--in-diff`, `--shard`, `--jobs` [M-C1][M-C2] | `#[mutants::skip]` [M-C1] | example CI fails on any miss [M-C1] |
| Mull (C/C++) | LLVM bitcode; all mutants in one binary, one switched on per run [M-M1] | dropped by Clang-AST junk detection [M-M1] | skips uncovered lines; git-diff mode [M-M3][M-M4] | `// mull-off` [M-M5] | `--mutation-score-threshold` [M-M3] |
| StrykerJS | AST; "mutation switching", all mutants in place, one active [M-S1] | optional TS checker, not scored [M-S2] | per-test coverage, `--incremental` [M-S3] | `// Stryker disable` [M-S4] | `thresholds.break`, off by default [M-S3] |
| sydtest-mutation (Haskell) | GHC plugin on the type-checked AST [M-H1] | not checked | covering tests only; `diff` mode [M-H4][M-H5] | `DisableMutations` annotation [M-H7] | `assert-score` [M-H6] |

Two lessons. **Unviable mutants are cheap to report and need no triage** (cargo-mutants), so a
type checker is a free filter. **Compiling once** with every mutant behind a runtime switch (Mull,
Stryker, sydtest) is how the tools that compile avoid one build per mutant. For Sprout the build
is the expensive part: every test file is compiled to IR and linked before it runs.

### 7.3 Design

A driver, `just mutate <module>`:

1. Parse the module with the compiler's own parser. Enumerate sites: comparison and arithmetic
   operator swaps, integer literal ±1, `true`/`false` flip, `if` condition to `true`/`false`, match
   arm deletion, `Just(x)` to `Nothing`, `Ok(x)` to an `Err` of the same arm type where one exists.
2. For each site, print the mutant with the formatter, re-run the test files that import the
   module, and record killed / survived / unviable (fails to compile) / timed out.
3. Report survivors with file, line and the change. No score threshold.

Scope is one module at a time, chosen by whoever is hardening it. Mutation switching (one build,
a runtime mutant id) would cut the cost to one build per test file, but it changes codegen, so it
waits until per-mutant builds are measured and found too slow.

**Timeouts.** `proc_run` has no timeout and the `_test-stdlib` runner has none either. A mutant
that turns a loop infinite would hang the driver. Each run is wrapped in `perl -e 'alarm N; exec
@ARGV'` as in §4.5, with `N` a multiple of the baseline run time, as PIT, cargo-mutants, Mull,
mutmut and StrykerJS each derive theirs. A timeout counts as a kill.

### 7.4 Targets

1. **Small stdlib modules with exact specs:** `math.bigint`, `crypto`, `json`, `rng`, `scram`.
   Few importers, so few tests to re-run per mutant.
2. **The gates.** `docs/gates.md` records how gates have been defeated. A gate is checked by a
   hand-made mutant: inject an O(n²) accumulator and require `rooting-cost-gate` to fail; drop a
   root and require the GC linter to fail. These are a short list written by hand, not generated.
3. **One compiler pass at a time** (`dce`, `bce`, `ir_rooting`). Each mutant needs a new stage-1
   build before any test runs, so whole-compiler runs are out of reach. A survivor in a pass points
   at an untested optimisation branch.

### 7.5 Why not a gate

A score threshold rewards tests that pin incidental behaviour, and equivalent mutants make 100%
unreachable without skip markers. StrykerJS ships its break threshold off by default [M-S3]. Here
it stays a report, run when a module is being hardened.

## 8. Impact

- **Syntax, semantics, types, diagnostics:** none. Stage 1–3 are library code; §6.4 would add a
  derivable class and gets its own design.
- **Builtins:** none. Timeouts use the `perl alarm` wrapper (§4.5, §7.3).
- **Status and docs:** `stdlib.prop` is experimental and documented in
  `docs/stdlib-reference.md` only, not the normative spec, which has no `stdlib.test` section
  either. `docs/idiomatic-sprout.md` gains a short "write a property" section once stage 1 lands.
- **Compatibility:** additive. The two hand-rolled harnesses keep working until ported.

## 9. Open questions

1. Module and function names (`stdlib.prop`, `prop_check`, `draw_*`).
2. Does a property return `Bool`, a `PropResult`, or a `TestCase`? §4.4 assumes `PropResult`, so
   the failure detail can say what differed.
3. Default case count inside `just test`, to be set by measurement in stage 1.

## References

Property testing:
[Q1] https://hackage-content.haskell.org/package/QuickCheck-2.19.0.0/docs/Test-QuickCheck.html ·
[Q2] https://raw.githubusercontent.com/nick8325/quickcheck/master/src/Test/QuickCheck/Test.hs ·
[Q3] https://raw.githubusercontent.com/nick8325/quickcheck/master/src/Test/QuickCheck/Property.hs ·
[Q4] https://hackage.haskell.org/package/quickcheck-state-machine ·
[H1] https://hackage-content.haskell.org/package/hedgehog-1.7/docs/Hedgehog-Gen.html ·
[H2] https://github.com/hedgehogqa/haskell-hedgehog/blob/master/README.md ·
[H5] https://github.com/hedgehogqa/haskell-hedgehog/blob/master/CHANGELOG.md ·
[H6] https://github.com/hedgehogqa/haskell-hedgehog/blob/master/hedgehog/src/Hedgehog/Internal/Runner.hs ·
[Y1] https://hypothesis.readthedocs.io/en/latest/reference/strategies.html ·
[Y2] D. R. MacIver, A. F. Donaldson, "Test-Case Reduction via Test-Case Generation: Insights from
the Hypothesis Reducer", ECOOP 2020, LIPIcs 166, 13:1–13:27,
https://doi.org/10.4230/LIPIcs.ECOOP.2020.13 ·
[Y3] https://hypothesis.readthedocs.io/en/latest/reference/api.html ·
[Y5] https://hypothesis.readthedocs.io/en/latest/stateful.html ·
[P1] https://github.com/proptest-rs/proptest/blob/main/proptest/src/arbitrary/traits.rs ·
[P2] https://github.com/proptest-rs/proptest/blob/main/proptest/src/strategy/traits.rs ·
[P3] https://docs.rs/proptest/latest/proptest/strategy/trait.Strategy.html ·
[P4] https://proptest-rs.github.io/proptest/proptest/failure-persistence.html ·
[P5] https://github.com/proptest-rs/proptest/blob/main/proptest/src/test_runner/runner.rs ·
[P6] https://proptest-rs.github.io/proptest/proptest/forking.html ·
[P7] https://proptest-rs.github.io/proptest/proptest/state-machine.html ·
[E1] https://package.elm-lang.org/packages/elm-explorations/test/2.2.1/ ·
[E2] https://github.com/elm-explorations/test/blob/master/CHANGELOG.md ·
[E3] https://github.com/rtfeldman/node-test-runner/blob/master/README.md ·
[F1] https://github.com/dubzzz/fast-check/blob/main/packages/fast-check/src/check/arbitrary/definition/Arbitrary.ts ·
[F2] https://fast-check.dev/docs/core-blocks/arbitraries/combiners/any/ ·
[F3] https://fast-check.dev/docs/tutorials/quick-start/read-test-reports/ ·
[F4] https://github.com/dubzzz/fast-check/blob/main/packages/fast-check/src/check/property/_internals/PropertyImplem.ts ·
[F5] https://fast-check.dev/docs/advanced/model-based-testing/ ·
[F6] https://fast-check.dev/docs/core-blocks/arbitraries/combiners/recursive-structure/

Mutation testing:
[J1] R. Just, D. Jalali, L. Inozemtseva, M. D. Ernst, R. Holmes, G. Fraser, "Are Mutants a Valid
Substitute for Real Faults in Software Testing?", FSE 2014, 654–665,
https://homes.cs.washington.edu/~mernst/pubs/mutation-effectiveness-fse2014-abstract.html ·
[M-P1] https://pitest.org/quickstart/mutators/ ·
[M-P2] https://pitest.org/quickstart/basic_concepts/ ·
[M-P3] https://pitest.org/faq/ ·
[M-P5] https://pitest.org/quickstart/maven/ ·
[M-C1] https://mutants.rs/print.html ·
[M-C2] https://mutants.rs/pr-diff.html ·
[M-M1] https://mull.readthedocs.io/en/latest/HowMullWorks.html ·
[M-M3] https://mull.readthedocs.io/en/latest/command-line/mull-runner.html ·
[M-M4] https://mull.readthedocs.io/en/latest/IncrementalMutationTesting.html ·
[M-M5] https://mull.readthedocs.io/en/latest/tutorials/DisableMutantsWithAnnotations.html ·
[M-S1] https://stryker-mutator.io/blog/announcing-stryker-4-mutation-switching/ ·
[M-S2] https://stryker-mutator.io/docs/stryker-js/typescript-checker/ ·
[M-S3] https://stryker-mutator.io/docs/stryker-js/configuration/ ·
[M-S4] https://stryker-mutator.io/docs/stryker-js/disable-mutants/ ·
[M-H1] https://github.com/NorfairKing/sydtest/tree/master/sydtest-mutation-plugin ·
[M-H4] https://github.com/NorfairKing/sydtest/blob/master/sydtest-mutation-driver/src/Test/Syd/Mutation/Driver/Mutate.hs ·
[M-H5] https://github.com/NorfairKing/sydtest/blob/master/sydtest-mutation-driver/src/Test/Syd/Mutation/Driver/DiffRun.hs ·
[M-H6] https://github.com/NorfairKing/sydtest/blob/master/sydtest-mutation-driver/src/Test/Syd/Mutation/Driver/AssertScore.hs ·
[M-H7] https://cs-syd.eu/posts/2026-06-03-mutation-testing-in-haskell
