# Implementation plan: effectful iteration combinators (`range_each` / `list_each` / effectful `*_fold`)

Status: **design approved** (2026-07-09, brainstorm with Kuba). Normative status: experimental
stdlib addition; no spec change. Data-last convention gets documented in the style guide (§Docs).

## Goal

Remove the dominant source of verbosity in effectful, imperative-over-mutable-memory Sprout
code — the hand-rolled `if i >= n then base else do <reads/writes>; recurse(i + 1)` counter
loop — by adding a small, **generic** family of effect-polymorphic iteration combinators to the
prelude. Motivating consumer: `examples/digit_recognizer/recognizer.sprout` (≈18 such loops).
Second consumer already on record: the GC safety-net tests that hand-write `churn(n, acc)`
(BACKLOG §7-ish, `docs/gc-phase2-retro-handoff-2026-07-05.md` §3).

The combinators are **not** recognizer-specific: they iterate over the two general iteration
substrates (`IntRange`, `List a`), never over `MutVec`, `Double`, or any domain type.

## Scope decisions (all verified empirically 2026-07-09, probes in the session scratchpad)

1. **The effect system already supports this — it is a stdlib gap, not a language gap.**
   A recursive higher-order function whose function argument carries `!{IO}` compiles and runs
   (`for_range` probe → `2.0`), and the effect-**polymorphic** `!{e}` form does too (`→ 9.0`), as
   does an effectful `fold_range` with an accumulator (`→ 6.0`). The README's "Effectful list
   iteration: Not Yet Supported" note is therefore stale and gets removed by this work.

2. **Recursion must live in a plain `_go` helper, not a self-recursive class method.**
   Generalizing the existing `Foldable`/`Functor` classes to an effectful step is blocked: a class
   method that calls itself recursively fails instance resolution (`No instance … in instance
   method`) — and the pure control fails identically, so this is the pre-existing recursive
   instance-dispatch limitation (a facet of the tyvar-identity / dict-resolution gap), **not** an
   effect-system problem. Consequence: the combinators are standalone functions delegating to a
   private recursive `_go`, exactly like today's `range_fold` → `range_fold_go`. Generic-over-
   `Foldable` unification is deferred to a backlog item gated on that dispatch fix.

3. **Data-last argument order** (collection is the final argument), matching the entire existing
   HOF family — `map`, `fmap`, `fold`, `fold_indexed`, `filter`, `list_map`, `vec_map` are all
   data-last; `range_fold` was the *lone* data-first outlier. This is also the pipeline order
   (`data |> f(args)`). The "trailing lambda reads as the loop body" counter-argument is moot
   because inline multi-line `do`-block lambdas do not parse (Backlog B1), so every step is a
   *named* function regardless — there is no trailing-lambda ergonomic to protect.

4. **Range combinators take a single `IntRange`**, not a `(lo, hi)` pair. Data-last wants exactly
   one trailing data argument so the call pipes (`range(0, n) |> range_each(f)`); a `(lo, hi)`
   pair would put two data args at the end and break that. Coheres with `range` / `range_fold`.

5. **`range_fold` is reordered to data-last** (breaking change, option (a), approved). Blast
   radius: two real callers (`stdlib/prelude.sprout:165` in `range_to_vec`'s helper, and
   `examples/int_range_demo.sprout:10`); all other grep hits are `tests/golden/ir/*.ll`
   snapshots, which are **not** a CI gate (confirmed: full `just test` passes without touching
   them, and memory `project_golden_ir_not_a_gate` says do not regenerate in-PR). Left stale
   deliberately — regenerating would add a 40-file noise diff for no gate benefit.

## The combinators (the `{range,list} × {each,fold}` grid)

```sprout
# imperative loop, no accumulator
export fn range_each(f: Int -> Unit !{e}, r: IntRange) -> Unit !{e}
export fn list_each (f: a   -> Unit !{e}, xs: List a)  -> Unit !{e}

# fold with an accumulator; step's effect row is polymorphic, so pure callers get e = {}
export fn range_fold(step: b -> Int -> b !{e}, init: b, r: IntRange) -> b !{e}   # REORDERED
export fn list_fold (step: b -> a   -> b !{e}, init: b, xs: List a)  -> b !{e}   # new
```

- `range_fold` already exists (`prelude:104`) as a **pure** data-first function; this work widens
  its step to `!{e}` and reorders it to data-last. Pure callers are unaffected (`e` unifies to the
  empty row).
- The existing pure Foldable `fold` stays as-is; `list_fold` is the effect-capable concrete-List
  sibling. The mild redundancy is intentional and noted for later unification (scope decision 2).

Naming: fold combinators name the accumulating function `step` (matches `fold`/`fold_indexed`);
each combinators name it `f` (matches `map`/`filter`). The private range `_go` helper's integer
increment is renamed off `step` to avoid a name clash with the fold step function.

## Touch points

1. `stdlib/prelude.sprout`
   - Reorder + effect-generalize `range_fold` and `range_fold_go` (data-last, `!{e}`).
   - Add `range_each` (+ `range_each_go`), `list_each` (+ `list_each_go`), `list_fold`
     (reuse/adapt the existing `list_fold_go` at `:112`, generalized to `!{e}`).
   - Update the one internal caller at `:165` (`range_to_vec` helper) to the new arg order.
   - Complexity docs on each new export (all O(n), one pass), per guidelines.
2. `examples/int_range_demo.sprout:10` — reorder the `range_fold` call.
3. `examples/digit_recognizer/recognizer.sprout` — rewrite the ≈18 counter loops against the
   grid (separate follow-up commit; the combinators land first with their own tests).
4. `README.md` — delete the stale "Effectful list iteration: Not Yet Supported" entry; keep the
   `;`-sequencing gotcha only if B3 stays open (see backlog).
5. `docs/style-guide-v0.md` — document the data-last argument convention (satisfies the existing
   BACKLOG line-74 task §4).
6. `bootstrap/compile_driver.ll` — `just refresh-seed` (prelude changed; seed gate blocks commit
   otherwise). Delete `build/compile_driver_bin_stage1` first (stale-binary trap).
7. `tests/golden/ir/*.ll` — regenerate the snapshots that embed `range_fold`.

## Risks (honor before declaring done)

- **Effect-polymorphic prelude functions in the compiler bundle.** The prelude is bundled into
  every program including the self-hosted compiler. Adding `!{e}` exports must not perturb the
  compiler's own IR. Guard: full `just test`, `compile-examples-stage1`, and the bundle-smoke /
  seed-fixed-point gates (compiler-source-adjacent because prelude is bundled).
- **`range_fold` reorder correctness.** A silent wrong-arg-order at a caller would still typecheck
  if `init` and `r` had compatible shapes — they don't here (`IntRange` vs `b`), so a mistake is a
  type error, not a miscompile. Still: run the two real callers.
- **Seed staleness.** Prelude change ⇒ refresh seed BEFORE `just test` for compiler-adjacent
  changes (inverts normal DoD order; see `feedback_refresh_seed_before_test_for_compiler_changes`).

## TDD / staging

1. **RED test first** — `tests/stdlib/test_native_iteration_combinators.spr` (mirror
   `test_native_fold_indexed.spr`). Cases, each an observable-output assertion:
   - `range_each` writes `0..n` into a `MutVec`, read back → expected.
   - `range_fold` **pure** sum over `range(0, n)` → n(n-1)/2 (also the reorder regression).
   - `range_fold` **effectful** dot-product reading two `MutVec`s → expected scalar.
   - `list_each` accumulates into a `MutVec` cell.
   - `list_fold` **pure** and **effectful** over a `List` → expected.
   Confirm RED for the right reason (unknown name / arity mismatch, not a crash).
2. Implement the grid + reorder until GREEN.
3. Fix the two `range_fold` callers; regenerate golden IR.
4. `just fmt`, refresh seed, full `just test`, `compile-examples-stage1`, bundle/seed gates.
5. Separate commit: rewrite `recognizer.sprout` against the grid; verify it still trains
   (accuracy printout unchanged within noise) and runs to completion.

## Done criteria (stdlib + compiler-adjacent DoD)

Implementation complete; RED tests GREEN; `range_fold` callers fixed; golden IR regenerated;
README + style-guide updated; `just fmt` clean; seed refreshed & staged; full `just test` green;
`compile-examples-stage1` green; bundle-smoke + seed-fixed-point green; recognizer trains & runs;
BACKLOG line-74 item updated/closed; committed; self-reviewed.
