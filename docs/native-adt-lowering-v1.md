# Native ADT Lowering V1

This document describes the first planned optimization pass for short-lived ADTs
in the native LLVM backend. It is a design and implementation plan document, not
part of the normative language spec.

## Problem Statement

The current native backend materializes algebraic data type values eagerly,
including very small values such as `Nothing`, `Just Int`, `Ok Int`, and
`Err String`. When a producer expression is matched immediately, the backend
still:

1. allocates a runtime object with `sprout_makeN`
2. stores constructor payloads into the object
3. reads the object back with `sprout_tag` and `sprout_field`
4. dispatches to the selected match branch

This is correct but expensive relative to the underlying work. For example,
`math.mod(x, n)` does only a few integer operations, but a caller that matches
its `Maybe Int` result immediately pays an extra allocation and object
destructuring step in native mode.

## Goals

1. Reduce allocation cost for the most common zero-argument optional value by
   making native `Nothing` a singleton.
2. Remove box-then-immediately-unbox patterns for direct constructor-producing
   scrutinees in the native backend.
3. Preserve surface syntax, typing rules, evaluation order, and match semantics.
4. Keep the first implementation narrow and reviewable.

## Non-Goals

1. No language-surface changes.
2. No changes to the normative spec.
3. No whole-program escape analysis.
4. No broad ADT scalar replacement across arbitrary expression graphs.
5. No interpreter optimization requirement for v1.

## Current Representation

In native mode, constructors lower to heap-allocated `SproutObj` values. The
runtime exposes `sprout_make0` through `sprout_make3`, plus `sprout_tag` and
`sprout_field`. This representation is simple and uniform, but it makes tiny
short-lived ADTs disproportionately expensive.

## V1 Scope

V1 is intentionally staged.

### Step 1: Singleton `Nothing` — SUPERSEDED by general interning

Shipped first as a name-based allowlist: `sprout_make0` matched `Nothing`, later
the three `IRType` ctors, by `strcmp`, and every other nullary ctor allocated.

That narrowness was the wrong call, and the "separate design question" it
deferred turned out to have the same answer for every nullary ctor. Arity 0
means no fields, so two values with one tag are indistinguishable and neither
can be mutated: sharing one object is observationally equivalent universally,
not as a property of `Nothing`. `sprout_make0` now interns on the **tag**, with
the shared object held in `CtorMeta.singleton`.

Keying on the tag also closed a hazard the allowlist carried — an entry file's
own `Nothing` could `strcmp`-match and be handed the prelude's cached object,
bearing the wrong tag.

Two supporting changes were load-bearing rather than incidental:
- `find_ctor` was a linear scan of up to 2048 entries, run on **every** nullary
  construction. A tag-indexed table makes it O(1); without that, generalising
  would have cost more than it saved.
- Invalidation was four pointer compares against singleton globals on every
  reclaimed object *of any kind*. It now reads the dying object's own tag from
  the header the sweep already holds, so it is O(1) and touches only nullary
  objects. The cache stays weak, so no singleton needs rooting.

Measured on the self-hosted compiler emitting IR for `stdlib/compiler/parser.sprout`:
20,530,202 → 17,824,030 objects (**-13.2%**), wall clock 1.27 s → 1.155 s median
of six interleaved pairs (**-9%**), peak RSS 67.1 → 62.9 MB (**-6.3%**). GC
*cycles* rise (215 → 236) because the threshold is `live x adapt_factor` and
interning shrinks the live set — each cycle sweeps a smaller heap, and total
sweep work falls by exactly the allocation delta.

The win scales with how many nullary constructions a workload makes, so it is
not universal: nqueens moved by 7 objects, because it allocates `Vec` wrappers
and its `Bool`s are native `i1`.

### Step 2: Immediate-match optimization for direct constructor-producing scrutinees

Scope:
- Native LLVM backend only
- Scrutinees that are syntactically direct constructor producers:
  - `Ctor`
  - `Ctor(...)`
  - `if cond then <direct-constructor-expr> else <direct-constructor-expr>`
- Top-level `match` branches must not bind the whole scrutinee with a variable
  pattern in the optimized path.

Approach:
- Detect when a `match` scrutinee is known to produce a constructor directly.
- Instead of materializing the ADT, lower the scrutinee into control flow.
- Evaluate constructor payload expressions only on the selected control-flow
  path.
- Test branch patterns directly against the known constructor and payload SSA
  values.
- Bind payload variables directly from SSA values for the selected branch.
- Fall back to the existing generic match lowering when the scrutinee or branch
  shape is outside this limited subset.

Example:

Current lowering shape:

```sprout
match if flag then Just(42) else Nothing with
| Just x -> x
| Nothing -> 0
```

becomes:

1. branch on `flag`
2. call `sprout_make1` or `sprout_make0`
3. match on the resulting handle with `sprout_tag` and `sprout_field`

V1 optimized lowering becomes:

1. branch on `flag`
2. in the `then` path bind payload `42`
3. jump directly to the `Just` branch body
4. in the `else` path jump directly to the `Nothing` branch body
5. merge branch results with a final `phi`

## Why This Is Not `math.mod`-Specific

`math.mod` is a motivating example, not a special case. The optimization target
is the general pattern:

```sprout
match <expr-producing-adt> with ...
```

when `<expr-producing-adt>` is consumed immediately and can be lowered directly
as control flow rather than as a heap object.

The same approach applies to `Maybe`, `Result`, and user-defined ADTs as long as
the scrutinee fits the supported direct-constructor subset.

## Correctness Constraints

The optimized path must preserve:

1. Evaluation order. Constructor payload expressions must only run on the
   selected constructor path.
2. Pattern order. Branch selection still follows source order.
3. Fallback behavior. Unsupported shapes continue to use the existing generic
   match lowering.
4. Branch typing. Optimized and non-optimized matches still produce one merged
   backend value type.
5. Constructor payload coercions. The optimized path must coerce constructor
   arguments the same way the normal constructor call path does.

## Error Message Impact

There should be no user-facing parser or typechecker diagnostic changes.
Backend-only internal errors may gain more precise messages when a direct
constructor fast path encounters an unsupported shape unexpectedly.

## Compatibility and Migration

This change is backward-compatible.

Program behavior should remain unchanged. The user-visible effect is improved
native performance in narrow cases plus reduced allocation pressure for native
`Nothing`.

## Testing Plan

V1 should include:

1. LLVM codegen tests proving immediate direct-constructor matches omit
   `sprout_makeN`, `sprout_tag`, and `sprout_field`.
2. LLVM codegen tests proving unsupported top-level variable-pattern matches
   still use the generic boxed path.
3. Native execution tests proving optimized matches still produce correct
   results.
4. Full test suite verification through `mise exec -- just test`.

## Follow-up Roadmap After V1

Planned follow-up work after this first slice:

1. Extend the direct-constructor fast path to more producer shapes, such as
   nested constructor-producing matches and additional expression forms.
2. Support optimized paths that still allow binding the whole scrutinee when
   profitable, potentially by materializing only in the branches that need it.
3. Explore generalized constructor forwarding and scalar replacement for
   short-lived ADTs.
4. Evaluate specialized native representations for common tiny shapes such as
   `Maybe Int` and `Result e Int`.
5. Revisit whether interpreter-side representation optimizations are worth the
   added complexity.

## Recommended Implementation Sequence

1. Add native runtime `Nothing` singleton support.
2. Add direct-constructor match lowering behind strict syntactic guards.
3. Add focused codegen and native execution tests.
4. Run the full suite and measure whether broader follow-up work is justified.
