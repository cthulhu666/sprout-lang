# Effect System v1 Draft

Status note:

- The minimal `!{IO}` effect system and restricted singleton effect variables
  such as `!{e}` are implemented in v0 and reflected in the normative spec.
- This document defines the recommended next effect milestone beyond that
  baseline. It is not part of normative v0.

This document is a design draft for the next effect milestone in Sprout.

The main recommendation is intentionally narrow: improve the usability of the
existing effect model before adding richer row machinery.

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

1. `IO` does not yet participate in the experimental `do` notation.
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

## 4. High-Level Implementation Overview

Recommended order:

1. Keep the current v0 function-effect model unchanged:
   pure by default, explicit `!{IO}`, singleton `!{e}` only.
2. Extend the experimental `do` notation so `IO` can sequence through the same
   typed-core/elaboration seam already used for `Maybe` and `Result`.
3. Add pure local `let` steps inside `do` so effectful code does not require
   awkward helper extraction for simple intermediate values.
4. Improve effect diagnostics around:
   - calling `!{IO}` from a pure function
   - forgetting to propagate an effect variable through a higher-order helper
   - declaring `main` or another function with an effect that is too narrow
5. Only after this ergonomics pass, revisit whether richer rows are still
   justified.

This keeps the next milestone focused on usability, not theory expansion.

## 5. Proposed Direction

The recommended next effect milestone is:

1. Keep the current row-shaped syntax.
2. Keep only closed `!{IO}` and singleton `!{e}` in the contract.
3. Add `IO` integration to the existing experimental `do` notation.
4. Add pure local bind steps inside `do`.
5. Sharpen effect diagnostics and examples.

Illustrative target surface:

```sprout
fn prompt_name() -> String !{IO} =
  do
    print("name?")
    raw <- term_read_line_once()
    let name = maybe_or("anonymous", raw)
    print("hello")
    pure(name)
```

The exact surface spelling for plain effectful steps and final return helpers
is still open, but the milestone should target this shape of sequential IO code
rather than richer effect rows.

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
2. a single `do` block still belongs to one sequencing family
3. pure local `let` steps inside `do` do not change the surrounding effect
   family
4. higher-order helpers should keep propagating a shared singleton effect
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
5. using an `IO`-sequencing form in a pure-returning `do` block

Diagnostic style should say:

1. what failed
2. which expression introduced the effect
3. what function annotation or block family change would fix it

Representative tone:

- `This function is inferred pure, but this call requires !{IO}.`
- `This helper calls an effect-polymorphic argument, so its result type must also carry !{e}.`
- `This do block sequences IO, so its final expression must remain in the IO family.`

## 9. Compatibility and Migration Notes

Recommended migration stance:

1. existing v0 effect annotations remain valid
2. existing `Maybe`/`Result` `do` code remains valid
3. new `IO`-aware sequencing should be additive at first
4. ad hoc sequencing helpers such as `after(...)` may remain temporarily, but
   they should no longer be the preferred story once `IO` sequencing lands

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
4. Re-evaluate richer rows only after the existing effect model is pleasant
   enough for ordinary programs.

## 13. Open Questions

1. What is the best surface for plain effectful steps inside `do` blocks:
   bare expressions, a dedicated keyword, or both?
2. Should `IO` `do` blocks require an explicit final constructor/helper, or
   should the elaboration infer the final sequencing form?
3. Should pure local `let` inside `do` be layout-only, or share the ordinary
   `let` surface exactly?
4. Once `IO` sequencing lands, does `after(...)` stay as a convenience helper,
   or become legacy compatibility surface?
