# Effect System v1 Draft

Status note:

- The minimal `!{IO}` effect system and restricted singleton effect variables
  such as `!{e}` are implemented in v0 and reflected in the normative spec.
- This document defines the recommended next effect milestone beyond that
  baseline. It is not part of normative v0.

This document is a design draft for the current recommended next effect
milestone in Sprout.

The main recommendation is intentionally narrow: treat the current mixed
`IO` plus inner `Maybe`/`Result` `do` model plus pure local `let` steps as the
intended v0-era endpoint for sequencing sugar, improve its usability and
diagnostics, and defer richer row machinery or more general sequencing
abstractions until real pressure appears.

## 1. Problem Statement

Sprout v0 already has a real effect surface:

- omitted effect annotation means pure
- `!{IO}` marks effectful functions
- singleton effect variables such as `!{e}` support restricted higher-order
  effect polymorphism

That baseline is enough to describe purity boundaries honestly, but the next
practical gap is no longer effect syntax. The next gap is effectful
programming ergonomics.

Today the main pain points are:

1. `IO` now participates in the experimental `do` notation, but the combined
   story for `IO` plus inner `Maybe`/`Result` sequencing still needs a clearer
   statement of intent so it reads as a deliberate design, not a halfway step.
2. Local sequencing often falls back to ad hoc helpers such as `after(...)`
   instead of a coherent language story.
3. Higher-order effect propagation is implemented, but the diagnostic story is
   still more compiler-shaped than beginner-shaped.
4. It is still unclear whether Sprout needs richer rows soon, or whether that
   would just add theory before the existing `IO` model is fully usable.

The risk is straightforward: if Sprout pushes toward open rows or more effect
labels too early, it may spend design complexity before the language has proven
the simpler model insufficient.

## 2. Goals

1. Make the current effect model pleasant enough for ordinary IO-heavy code.
2. Preserve strict, left-to-right execution and explicit effect boundaries.
3. Improve higher-order effect diagnostics without changing the core surface
   unnecessarily.
4. Keep the next milestone small enough to ship end-to-end, including docs and
   diagnostics.
5. Avoid committing to open rows, handlers, or user-defined effects before
   there is concrete pressure for them.

## 3. Non-Goals

1. Do not introduce open or multi-entry effect rows in this milestone.
2. Do not add user-defined effect labels or effect handlers.
3. Do not redesign the language around monads or higher-kinded types.
4. Do not change the strict evaluation model.
5. Do not widen the normative v0 contract pre-emptively through examples or
   stdlib APIs.

Current validation status:

- A focused audit of the current examples, stdlib code, and effect-heavy user
  flows did not show concrete pressure yet for multi-entry rows, multiple
  effect variables, or additional effect labels.
- The remaining awkward cases are narrower sequencing ergonomics issues,
  especially preserving the whole `Maybe`/`Result` container while continuing
  with more `IO`, rather than evidence that the function-effect model itself is
  too weak.
- Treat richer rows and labels as deferred until concrete examples accumulate
  beyond those ergonomics concerns.

## 4. High-Level Implementation Overview

Recommended order:

1. Keep the current v0 function-effect model unchanged:
   pure by default, explicit `!{IO}`, singleton `!{e}` only.
2. Extend the experimental `do` notation so `IO` can sequence through the same
   typed-core/elaboration seam already used for `Maybe` and `Result`.
3. Allow the narrow combined shapes `Maybe a !{IO}` and `Result e a !{IO}` in
   `do` blocks without introducing full generic sequencing.
4. Add pure local `let` steps inside `do` so effectful code does not require
   awkward helper extraction for simple intermediate values.
5. Improve effect diagnostics around:
   - calling `!{IO}` from a pure function
   - forgetting to propagate an effect variable through a higher-order helper
   - declaring `main` or another function with an effect that is too narrow
6. Only after this ergonomics pass and real usage experience, revisit whether
   richer rows or broader sequencing abstractions are still justified.

This keeps the next milestone focused on usability, not theory expansion.

## 5. Proposed Direction

The recommended next effect milestone is:

1. Keep the current row-shaped syntax.
2. Keep only closed `!{IO}` and singleton `!{e}` in the contract.
3. Keep `IO` integration in the existing experimental `do` notation.
4. Allow mixed `IO` plus inner `Maybe`/`Result` blocks for the combined shapes
   `Maybe a !{IO}` and `Result e a !{IO}`.
5. Add pure local bind steps inside `do`.
6. Sharpen effect diagnostics and examples.

This document should be read as the preferred roadmap direction for effect work
after the implemented v0 baseline, unless later code examples demonstrate that
the narrower ergonomics-first approach is insufficient.

Recommended stopping point for this slice:

- treat mixed `IO` plus inner `Maybe`/`Result` sequencing as sufficient for the
  current language stage
- require anything broader to justify itself with concrete examples that are
  genuinely awkward under the current model
- prefer explicit `match` over speculative generalization when code needs to
  preserve the whole container value

Illustrative target surface for a helper:

```sprout
fn prompt_name() -> Maybe String !{IO} =
  do
    print("name?")
    name <- term_read_line_once()
    print("hello")
    Just(name)
```

The surface stays intentionally small: bare `!{IO}` steps remain valid, and a
`<-` step may also unwrap `Maybe`/`Result` when the surrounding block returns
`Maybe ... !{IO}` or `Result ... !{IO}`. This is the recommended v0-era
sequencing story for ordinary sequential IO code with failure; it is not
intended to imply a broader monadic abstraction yet.
If code needs to keep the whole `Maybe` or `Result` value instead of
short-circuiting on it, it should use an explicit `match`.

## 6. Syntax and Semantics Impact

Recommended syntax direction:

1. Keep function effects unchanged:
   - omitted annotation means pure
   - `!{IO}` means observable runtime interaction
   - `!{e}` means a singleton effect variable
2. Keep `do` / `<-` as the sequencing surface.
3. Add pure local bind steps inside `do`, for example:

```sprout
do
  line <- term_read_line_once()
  let cleaned = trim(maybe_or("", line))
  print(cleaned)
```

4. Add an `IO` sequencing family to the existing `do` elaboration path rather
   than creating a separate special-case statement language.
5. Permit mixed blocks such as:

```sprout
fn greet() -> Maybe String !{IO} =
  do
    print("name?")
    name <- argv_get(0)
    print(name)
    Just(name)
```

where `name <- argv_get(0)` unwraps the inner `Maybe` after performing the `IO`
step, and `Nothing` short-circuits out of the whole block. User-facing `main`
must then handle that helper result explicitly and stay `Unit !{IO}`; the
executable entrypoint boundary rejects other return shapes.

Recommended interpretation rules:

1. A plain non-final expression step is only for `!{IO}` sequencing.
2. A `<-` step inside mixed `IO` sequencing unwraps the inner `Maybe` or
   `Result`; it does not bind the whole container.
3. A mixed block still has one container family, `Maybe` or `Result`, and
   must finish in that same family.
4. If code needs to observe or preserve the whole `Maybe`/`Result` value, it
   should leave `do` for an explicit `match`.

Semantics remain:

1. strict
2. left-to-right
3. effectful when evaluated, not delayed
4. explicit at function boundaries

This milestone should not change when effects happen. It should only make the
existing model easier to express and understand.

## 7. Type-System Impact

The recommended type-system stance is conservative.

Keep:

1. closed `!{IO}`
2. singleton effect variables `!{e}`
3. concrete-effect requirement for effectful `main`

Add or clarify:

1. `do` blocks may sequence `IO` in addition to `Maybe` and `Result`
2. a single `do` block still belongs to at most one container family:
   `Maybe` or `Result`
3. `!{IO}` steps may appear alongside either container family, producing
   `Maybe a !{IO}` or `Result e a !{IO}`
4. pure local `let` steps inside `do` do not change the surrounding effect
   family
5. higher-order helpers should keep propagating a shared singleton effect
   variable rather than inferring richer rows

Illustrative examples:

```sprout
fn apply_once(f: Int -> Int !{e}, x: Int) -> Int !{e} =
  f(x)
```

```sprout
fn show_twice(x: Int) -> Unit !{IO} =
  do
    print_int(x)
    print_int(x)
```

This milestone should explicitly defer:

1. `!{IO, e}`
2. `!{e, f}`
3. user-defined effect labels
4. row subtraction or handlers

## 8. Error-Message Impact

This should be one of the main deliverables, not a cleanup afterthought.

High-value diagnostics:

1. calling a `!{IO}` function from a pure function
2. forgetting to propagate an effect variable through a higher-order helper
3. giving `main` an effect-polymorphic signature
4. using a `do` bind step from the wrong sequencing family
5. using a plain non-final expression step without `!{IO}`
6. forgetting that a mixed `IO` plus `Maybe`/`Result` block must still finish
   in the same container family

Diagnostic style should say:

1. what failed
2. which expression introduced the effect
3. what function annotation or block family change would fix it

Representative tone:

- `This function is inferred pure, but this call requires !{IO}.`
- `This helper calls an effect-polymorphic argument, so its result type must also carry !{e}.`
- `This do bind must unwrap a Maybe/Result value, or require !{IO}.`
- `This do block started with Maybe bindings, so its final expression must also return Maybe.`

## 9. Compatibility and Migration Notes

Recommended migration stance:

1. existing v0 effect annotations remain valid
2. existing `Maybe`/`Result` `do` code remains valid
3. new `IO`-aware sequencing should be additive at first
4. the experimental mixed `IO` plus inner `Maybe`/`Result` block shapes are the
   preferred story for failure-aware effectful code at this stage
5. lightweight sequencing helpers such as `after(...)` may remain as
   compatibility/convenience surface, but they should no longer be the
   preferred story once `IO` sequencing lands

Compatibility rule of thumb:

- prefer additive ergonomic improvement first
- do not force a broad rewrite of existing `!{IO}` code just to introduce the
  new sequencing path

## 10. Tests to Add or Update

When implemented, this milestone should add or update coverage for:

1. parser tests for the chosen `IO`-related `do` forms
2. typechecker tests for valid and invalid `IO` sequencing
3. typechecker tests for clearer higher-order effect propagation failures
4. runtime tests showing `IO` `do` evaluation order remains strict
5. formatter/linter tests if the new `do` forms introduce additional spacing or
   layout rules

## 11. Spec and Documentation Impact

If this draft becomes an implementation plan, the corresponding change should
update:

1. `docs/spec-v0.md` only if any part becomes normative v0
2. `README.md` to describe the current implementation status accurately
3. `docs/sequencing-sugar-v1-draft.md` so it no longer treats `IO` integration
   as an undefined future direction
4. relevant examples and tests in the same change

Until then, this document remains a draft for post-v0 effect work.

## 12. Decision Summary

Recommended decision:

1. Do not make open rows the next effect milestone.
2. Do not add more built-in effect labels yet.
3. Make `IO` sequencing and effect diagnostics the next effect milestone.
4. Treat the current mixed `IO` plus inner `Maybe`/`Result` `do` model as the
   intended near-term stopping point.
5. Re-evaluate richer rows only after the existing effect model is pleasant
   enough for ordinary programs and concrete gaps remain.

## 13. Open Questions

1. Should pure local `let` inside `do` be layout-only, or share the ordinary
   `let` surface exactly?
2. Should `after(...)` remain only a small convenience for single-step `IO`
   sequencing, or shrink further over time?
