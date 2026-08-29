# `fold_while` — a short-circuiting `Foldable` primitive (v0)

Status: **normative for the stable core** once `docs/spec-v0.md` §8.5 carries it. This file records
the design and the rejected alternatives; the spec is the source of truth for behaviour.

## 1. Problem

`class Foldable f` had exactly one method:

```sprout
fn fold_values(step: b -> a -> b, init: b, xs: f a) -> b
```

That is a **left fold**, and a left fold structurally cannot exit early. Every combinator derived
from it therefore walks the whole container even once the answer is settled: `any`, `all`, `find`,
`find_map` and `member` stopped *applying the predicate* after the deciding element but kept
*walking*. `count` is unaffected — it has to see every element by definition.

This was not a missing convenience function. No free function derived from `fold_values` can fix it,
because the walk belongs to the method, not to the caller. The gap was recorded in `BACKLOG.md`
§(b) and was blocking real downstream code: `game/cmd.sprout` in `uncharted-suns` hand-rolls five
first-wins payload finders as explicit recursion precisely because `find_map` would not stop, with
comments naming the early exit as the reason they are not combinators.

## 2. Goals and non-goals

**Goals.** Give `Foldable` an early exit. Rebuild the five consumers on it so they stop for free.
Keep `fold` allocation-free and unchanged in cost — it is the primitive the self-hosted compiler
leans on hardest.

**Non-goals.** Laziness. An iterator/generator protocol. Monomorphization. Effectful `cond`
(the same `class Each` blocker that gates an effectful `filter` predicate). A `Step`/`Continue`/`Done`
sentinel type in the prelude — derivable, see §6, and shipped only when a caller needs it.

## 3. Prior art

Every claim below is from the language's own reference; each was checked against a primary source.

| language | `Foldable`-ish primitive | how early exit happens |
|---|---|---|
| **Haskell** | minimal complete definition is `foldMap` **or** `foldr` | laziness. `foldr` "can produce a terminating expression from an unbounded list" when the operator is lazy in its right argument; "a left fold of a structure that is infinite on the right cannot terminate" |
| **PureScript** | `foldr`, `foldl`, `foldMap` (all three mandatory) | **it does not.** Strict, no iterators; `find p = foldl go Nothing` with a latching accumulator visits every element |
| **Rust** | `Iterator::try_fold` is the primitive; `fold` is built on it | the closure returns `R: Try<Output = B>`; the residual stops iteration |
| **itertools** (Rust) | `fold_while` | explicit sentinel: `enum FoldWhile<T> { Continue(T), Done(T) }` |
| **Clojure** | `reduce` | `reduced` — "Wraps x in a way such that a reduce will terminate with the value x" |

The organising observation: **a fold short-circuits via laziness, via an iterator protocol, or via a
sentinel — and a strict language without iterators has only the third.** PureScript is the
controlled experiment for Sprout's exact position, and it simply gives up.

The `while`-with-a-continue-predicate convention, by contrast, is universal: `take_while`,
`drop_while`, `takeWhile`, `span` all take a keep-going predicate. `fold_while` is that predicate
attached to a fold.

## 4. Design

```sprout
export class Foldable f
  fn fold_values(step: b -> a -> b, init: b, xs: f a) -> b
  fn fold_while_values(step: b -> a -> b, cond: b -> Bool, init: b, xs: f a) -> b

export fn fold_while(step: b -> a -> b, cond: b -> Bool, init: b, xs: f a) -> b where Foldable f =
  fold_while_values(step, cond, init, xs)
```

**THE LAW.** `fold_while` returns the first accumulator — `init` included — for which `cond` answers
false; with no such accumulator it is exactly `fold`. `cond` is a **continue** condition: folding
proceeds while it holds. It is pure, and may be asked more than once about equal values, because a
non-linear instance re-checks it between substructures.

Checking `cond(init)` makes the degenerate case well defined (`fold_while(step, never, 7, xs) == 7`)
and makes "re-check after each subtree" the natural reading for a future tree instance.

Concrete builders `list_fold_while` / `vec_fold_while` back the two instances, per the existing rule
that an instance body delegates rather than recursing (a class method calling itself from its own
instance body fails dictionary resolution today). `vec_any` / `vec_all` are now `vec_fold_while`
specialisations, which deleted the hand-written `vec_any_indexed` / `vec_all_indexed` index loops —
the generic and the `Vec` spelling can no longer drift apart in behaviour or complexity.

## 5. Why a second method rather than a replacement

Making the short-circuiting fold the *sole* primitive and deriving `fold_values` from it is Rust's
structure, and it would keep the class at one mandatory method. It was rejected on cost:

- Deriving `fold_values` from a **sentinel** form allocates one object per element for *every* fold
  in the language, including the compiler's hot paths.
- Deriving it from the **cond** form costs one indirect closure call per element (`cond(acc)`
  answering false, forever) on the most-used primitive in the stdlib.

Both invert the tax: a small one-time authoring cost per instance becomes a permanent per-element
runtime cost everywhere. And the benefit is fully recoverable under the two-method design — an
instance author who would rather write one loop derives the plain fold from their own helper with a
constant `cond`, paying that call only for their own type. Two methods make the derivation opt-in;
replacement forces it on everyone.

The mandatory-method tax is real (classes carry no default bodies — `parse_class_body` collects
signatures only) but this prelude has already priced it: `Filterable` carries **three** mandatory
methods for the same reason, that in a strict language the derivation costs too much so it becomes a
method.

## 6. Why `cond: b -> Bool` and not `Continue`/`Done`

The sentinel is what itertools, Clojure and Rust ship, and it is what a reader arriving from those
languages will reach for. It was rejected because **Sprout boxes every constructor**:
`IRMakeCtor` lowers to `@sprout_alloc_obj(tag, nfields)` for every boxed ADT, nullary included
(`stdlib/compiler/ir_lowering.sprout`), so `Continue b` is a real heap allocation per element. Rust
pays nothing for `ControlFlow`; Sprout would pay *n* allocations on every `any`/`all` — buying an
early exit with GC pressure, which is not obviously a win when there is no match.

**"Fix codegen instead" is not available.** The Tier-2 CPR/scalar-replacement path unboxes small ADT
returns only when the scrutinee is a direct call to a bare top-level fn
(`docs/scalar-replacement-v0.md`), and `cpr_result` is false for a type-variable result on purpose:
one worker symbol cannot serve both an ADT and a tuple instantiation without monomorphization, and
getting this wrong previously produced silent wrong answers plus an out-of-bounds load
(`docs/bug-adt-through-generic-param-2026-08-12.md`). A `step` reached through a typeclass dictionary
is indirect twice over. Unboxing it is monomorphization, not a peephole.

**The sentinel remains available, derivably, at cost only to its users.** Running the `cond` form
with `Step b` *as* the accumulator recovers it exactly:

```sprout
export type Step b = Continue b | Done b

export fn fold_step(step: b -> a -> Step b, init: b, xs: f a) -> b where Foldable f =
  match fold_while(\ (s, x) -> match s with | Continue acc -> step(acc, x) | Done _ -> s,
                   \s -> match s with | Done _ -> false | Continue _ -> true,
                   Continue(init), xs) with
  | Continue acc -> acc
  | Done acc -> acc
```

The reverse derivation also exists but allocates per element for everyone. So `cond`-as-primitive is
the pay-for-what-you-use direction. **Not shipped** — no caller needs it yet (§2 non-goals); this
sketch is the record that the generality is not lost.

## 7. The expressiveness gap, stated honestly

`cond` sees only the accumulator, so it cannot express a **peek-and-refuse** fold: stop *before*
incorporating the current element, when incorporation is not invertible. Concrete case —
concatenate strings while the total stays under N: the sentinel's step returns `Done(acc)` on seeing
the overshooter, whereas `cond` sees an accumulator that meant "continue" a moment ago and cannot
now mean "stop". The workaround is to widen the accumulator to `(String, Bool)`, which costs a tuple
allocation per element — i.e. the sentinel's price, paid only by the caller who needs it. §6's
`fold_step` is the general form of the same escape hatch.

## 8. Known hazard for instance authors

Forgetting the `cond` re-check after a recursive descent — the tree case, where the walk must
re-check between the left subtree and the node — **type-checks and returns correct answers**, and
silently loses only the short-circuit. The sentinel form cannot be ignored that way, because `Done`
has to be unwrapped. This is the real cost of the choice, and it is closed by testing the traversal
rather than the result (§9).

## 9. Tests

`tests/stdlib/test_fold_while.spr` (24 assertions). The property under test is a **traversal** claim,
not a result claim, so the accumulator is the instrument: each step conses the element it saw, and
the finished accumulator is the exact list of elements visited. A `fold_while` that walked to the end
and merely skipped work returns all 100 elements of the fixture and fails. Covers both instances, the
law at `init`, agreement with `fold` under an always-true `cond`, empty structures, dictionary
forwarding through a `where Foldable c` caller, all five rebuilt consumers, and `count` continuing
*not* to short-circuit.

## 10. Deferred

- The `Step` / `fold_step` sentinel form (§6), when a peek-and-refuse caller appears.
- `length` / `is_empty` as `Foldable` methods — unchanged by this, still `BACKLOG.md` §(a).
- An effectful `cond`, blocked on the same `class Each` work as an effectful `filter` predicate.
