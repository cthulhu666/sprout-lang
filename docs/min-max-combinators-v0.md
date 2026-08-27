# `minimum` / `maximum` / `min_by` / `max_by` — design (v0)

Status: **approved** (2026-08-27). Decisions D1–D5 stand as written; the §9
sequencing question was answered **(1) — land the four on `where Ord k` now,
`Ord Double` follows**, with the coverage gap recorded in the spec text and in
`BACKLOG.md` rather than left to be rediscovered.

## 1. Problem statement

Selecting the smallest or largest element of a collection — and, more often, the
element whose *derived key* is smallest or largest (argmin/argmax) — has no
stdlib form. Every caller hand-rolls a tail-recursive fold that threads a
"best so far" pair.

This is not hypothetical. In `uncharted-suns`, the sole dogfooding consumer,
the shape appears open-coded at least four times:

| Site | Shape |
|---|---|
| `game/render_vista.sprout:1285` | fold planets into a running `(best_d2, best_target)` nearest-pick |
| `game/stations.sprout:70-80` | fold semi-major axes tracking `(best_idx, best_a)` |
| `game/economy.sprout:715` | a hand-written `argmax5(a, b, c, d, e) -> Int` |
| `loam/hydrology.sprout:437` | thread `(direction, water, hash)` picking the best neighbour |
| `grimward/combat.sprout:200` (unlanded branch) | a hand-written `fn min_by(score: a -> Double, xs: List a) -> Maybe a`, with a comment naming the prelude gap as the reason it exists — see §8 |

Each is a correct-but-bespoke reimplementation of the same fold, with its own
tie-breaking convention and its own empty-input sentinel (`best_idx < 0`,
`best_d = 0`). That is exactly the class of thing a prelude combinator removes.

The stdlib already has the *sorted* form of this operation (`vec_sort_by`), so a
caller who wants one extreme element today either pays `O(n log n)` and takes an
end of the sorted result, or writes the fold.

## 2. Goals and non-goals

**Goals**

- One `O(n)` argmin/argmax over any `Foldable`, evaluating the key function
  exactly once per element.
- Total: an empty collection is an ordinary, typed outcome, not a panic.
- Naming and argument order consistent with the `_by` convention the stdlib
  already established in `vec_sort_by`.
- Defined, documented, testable tie-breaking.

**Non-goals**

- A two-argument generic `min(x, y) / max(x, y) where Ord a`. `stdlib.math.int`
  already has `Int`-specific `min`/`max`/`clamp`; generalising those is a
  separate call with its own naming collision to resolve (see §7).
- Comparator-taking variants (`min_with` / `max_with`, mirroring Elm's
  `sortWith`). The stdlib has no `sort_with` either; if comparator-taking
  combinators are ever wanted, they should arrive as a set, not one-off.
- Resolving `Ord Double`. See §8 — this is the honest limitation of the proposal.
- `minimum_of` / `maximum_of` style helpers returning a default instead of `Maybe`.

## 3. Prior-art survey

Every row below is quoted from that language's own reference or library source.

| Language | Extremes | Empty | `*By` variant takes | Ties |
|---|---|---|---|---|
| **Haskell** (`base`, `Data.Foldable`) | `minimum, maximum :: Ord a => t a -> a` | **raises a runtime exception** ("non-total") | a **comparator** `(a -> a -> Ordering)` | min → leftmost, max → rightmost (documented) |
| **PureScript** (`Data.Foldable`) | `minimum :: Ord a => Foldable f => f a -> Maybe a` | `Nothing` | a **comparator** `(a -> a -> Ordering)` | not documented |
| **Rust** (`Iterator`) | `min, max -> Option<Self::Item>` | `None` | both: `min_by`/`max_by` = comparator, `min_by_key`/`max_by_key` = **key** | min → first, max → last (documented) |
| **Elm** (`elm/core` `List`) | `minimum : List comparable -> Maybe comparable` | `Nothing` | *(no `minimumBy` at all)*; `sortBy` takes a **key**, `sortWith` a comparator | not documented |

Three readings:

1. **Empty input.** Haskell's partiality is the outlier and is widely treated as
   a wart — `Data.List.NonEmpty` exists partly to route around it. Every
   safety-oriented successor (PureScript, Rust, Elm) returns the optional type.
2. **Key vs comparator.** Haskell and PureScript take a comparator because their
   `sortBy` does; Elm takes a key because its `sortBy` does; Rust ships both and
   distinguishes them *by name* (`_by` vs `_by_key`). The consensus is not about
   min/max at all — it is that **`_by` must mean the same thing across a
   stdlib's whole sorting/selection family**.
3. **Ties.** Where documented at all, Haskell and Rust agree exactly: min keeps
   the first, max keeps the last.

## 4. Decisions

Each of these is a recommendation with the reasoning; I want a yes/no on the set,
and especially on D2 and D4.

**D1 — `Foldable`-generic, not `list_*`/`vec_*` pairs.**
`fold`, `fold_indexed`, `mconcat`, `concat_map`, `foldable_to_vec` and
`string.join` are already written against `where Foldable c`. Four generic
functions cover `List` and `Vec` today and any future instance for free;
prefixed pairs would be eight functions covering two types.

**D2 — `_by` takes a key function `a -> k`, not a comparator.**
`vec_sort_by(f: a -> k, vec: Vec a) -> Vec a where Ord k` already fixes what
`_by` means in this stdlib. Making `min_by` a comparator would mean `sort_by`
and `min_by` differ in the meaning of the same suffix, which is worse than
diverging from Haskell. This lands us on Elm's convention, and on Rust's
`min_by_key` semantics under a shorter name. The naming asymmetry with Rust is
acceptable because Sprout ships only the key form — there is no `min_by_key`
for `min_by` to be confused with.

**D3 — return `Maybe a`.**
Matches `vec_get`, matches PureScript/Rust/Elm, and keeps the functions total.
The alternative (panic on empty) is Haskell's, and Haskell's own ecosystem
treats it as a mistake.

**D4 — ties: `min_by` keeps the *first*, `max_by` keeps the *last*.**
This matches Haskell and Rust, and it buys a law that is checkable against code
already in the prelude. `vec_sort_by` is a **stable** merge sort (it decorates
each element with its index and breaks key ties on that index,
`prelude.sprout:463-470`), so for any non-empty `xs`:

```
min_by(f, xs) == vec_get(0, vec_sort_by(f, foldable_to_vec(xs)))
max_by(f, xs) == vec_get(n - 1, vec_sort_by(f, foldable_to_vec(xs)))
```

The symmetric alternative ("first wins for both") is easier to state in one
sentence but breaks the second identity, and would make `min_by`/`max_by`
disagree with a sort the stdlib already performs. Both laws go in the tests.

**D5 — names are `minimum`/`maximum`, not `min`/`max`.**
`stdlib.math.int` exports `min`/`max`. A prelude `min` would be shadowed by
`import stdlib.math.int (min)` — and per the known failure mode, shadowing a
prelude name produces a *misdirected* error rather than a clean one. `minimum`/
`maximum` is also what Haskell, PureScript and Elm call these.

## 5. Syntax and semantics impact

No syntax change. Four new prelude exports, placed with the other
`Foldable`-derived combinators (`prelude.sprout`, near `fold`/`fold_indexed`/
`mconcat`, ~line 729-748):

```
export fn min_by(f: a -> k, xs: c a) -> Maybe a where Foldable c, Ord k
export fn max_by(f: a -> k, xs: c a) -> Maybe a where Foldable c, Ord k
export fn minimum(xs: c a) -> Maybe a where Foldable c, Ord a
export fn maximum(xs: c a) -> Maybe a where Foldable c, Ord a
```

Semantics:

- Empty input → `Nothing`. Otherwise `Just(element)`.
- `min_by`/`max_by` return **the element**, not the key (argmin, not min).
- The key function is applied **exactly once per element** — the fold carries
  `Maybe (k, a)`, not `Maybe a`, so no key is recomputed. `O(n)` in both element
  count and key evaluations; `O(1)` extra allocation beyond the carried pair.
- The key function is **pure**: `Foldable.fold_values` has no effect row
  (`prelude.sprout:703`), so an effectful key is a type error, not a silent
  reordering hazard.
- `minimum`/`maximum` are defined as `min_by(\value -> value, xs)` /
  `max_by(\value -> value, xs)`, mirroring how `vec_sort` is defined over
  `vec_sort_by`.

Implementation sketch (two private step helpers keep the lambdas shallow):

```
fn min_by_step(f: a -> k, best: Maybe (k, a), x: a) -> Maybe (k, a) where Ord k =
  let key = f(x)
  in
    match best with
    | Nothing -> Just((key, x))
    | Just entry ->
        match entry with
        | (best_key, _) -> if ord_lt(key, best_key) then Just((key, x)) else best
```

`max_by_step` is the same with `ord_gte` in place of `ord_lt` — the strict-vs-
non-strict comparison is precisely what implements D4.

## 6. Type-system impact

None to the type system itself; this is stdlib code using existing features.
Two things it does exercise, both already used elsewhere in the prelude:

- **Multi-constraint `where`** (`where Foldable c, Ord k`) — same shape as
  `mconcat`'s `where Foldable f, Monoid a`.
- **Dictionary forwarding through a constrained call.** `minimum` is
  `where Ord a` and calls `min_by`, which is `where Ord k`; `min_by` is
  `where Foldable c` and calls `fold`, which is `where Foldable f`. Concrete
  call sites devirtualize this away, so a concrete-only test would not exercise
  the forwarding path at all — the tests must include a caller that is itself
  polymorphic over the classes (see §9).

`k` appears only in argument position, so no return-type-directed dispatch is
involved (unlike `pure`/`from_ordinal`).

## 7. Error-message impact

No new diagnostics. Two foreseeable user-facing messages, both pre-existing
machinery:

- `minimum([1.0, 2.0])` fails with the ordinary missing-instance error for
  `Ord Double` — see §8.
- A user top-level `fn maximum(...)` now shadows a prelude name, which is the
  known misdirection failure mode. This is the cost of taking four names in the
  global namespace and is why D5 avoids the two *most* likely collisions
  (`min`/`max`). `min_by`/`max_by`/`minimum`/`maximum` were grepped against this
  repo and `uncharted-suns`: no existing top-level definition collides.

## 8. Compatibility, and the `Ord`-vs-`<` split — read this before approving

Purely additive; no existing behaviour changes. The problem is not compatibility,
it is *coverage*.

**Sprout has two orderings, and they barely overlap.**

| | Types covered | Mechanism |
|---|---|---|
| the `<` `<=` `>` `>=` **operators** | `Int`, `Char`, `Double` — and nothing else | monomorphic, built into the typechecker (`infer.sprout:3362-3391`, `check_compare`); unifies the operands, then tries `Int`, then `Char`, then `Double`, else `"< needs Int, Char, or Double"`. Lowers to `icmp`/ordered `fcmp`. |
| the **`Ord` class** | `Int`, `Bool`, `String`, 2–5-tuples, `Maybe`, `List`, `Result`, `Vec` (`prelude.sprout:893-989`) | `compare(l, r) -> Int`, dispatched through a dictionary; `ord_lt`/`ord_gt` wrap it |

**`Int` is the only type in both.** There is no `Ord Double` and no `Ord Char`;
there is no `<` for `String`, tuples, or `List`. Any `where Ord k` combinator is
therefore *blind to `Double`*, and any combinator written against `<` is blind to
`String` and every structural key.

This is not a hypothetical mismatch. The downstream user has **already written
this function**, and wrote it on the other side of the split —
`grimward/combat.sprout:200` (branch `feat/grimward-bestiary-art`, unlanded):

```
# ... the prelude has no `min_by` to borrow (`grimward/BACKLOG.md`).
# A TIE KEEPS THE FIRST, which is what makes a choice stable tick to tick.
fn min_by(score: a -> Double, xs: List a) -> Maybe a =
  list_fold(\ (best, x) ->
              match best with
              | Nothing -> Just(x)
              | Just kept -> if score(x) < score(kept) then Just(x) else best,
            Nothing, xs)
```

That is a strong independent confirmation of D2 (key function), D3 (`Maybe`),
D5-adjacent naming (`min_by`), and the min half of D4 (first wins) — it reached
the same four answers unprompted, and its own comment says the prelude gap is
why it exists. But its key type is `Double`, which the proposed `where Ord k`
signature **cannot accept**. Shipping this design as written would leave that
call site exactly where it is, now shadowing a prelude name.

The same applies to three of the four §1 sites (squared distance, semi-major
axis, supply sums — all `Double`).

**What this means for the decision.** The four combinators are correct, cheap
and independently useful for `Int`/`String`/tuple/ADT keys — but presented as
"the prelude now has argmin", they would under-deliver against the one real
consumer. The honest framing is that **`Ord Double` (`BACKLOG.md:603`, `P2`) is
the load-bearing prerequisite**, not a nice-to-have downstream item. It is held
open for a real reason (IEEE NaN is unordered, so a total `Ord Double` is a lie,
and Haskell's is a documented footgun that breaks `maximum` and `sort` — the
very functions proposed here), and `numeric-types-v1-draft.md` §7.1 lists three
candidate resolutions.

I am not proposing to resolve `Ord Double` inside this change; that is a
language-level call and its own design. I *am* flagging that the sequencing
question belongs to you — see §9.

## 9. The sequencing question

Three coherent orders, in my order of preference:

1. **Land these four on `where Ord k` now; treat `Ord Double` as the declared
   follow-up.** The combinators are useful immediately for `Int`/`String`/tuple
   keys, the API never changes when `Ord Double` lands (every `Double` site
   starts working with zero edits), and no design debt is created. Cost: the
   `uncharted-suns` `Double` sites stay hand-rolled in the meantime, and
   `grimward`'s local `min_by` must be renamed or kept knowingly shadowing.
2. **Decide `Ord Double` first, then land these.** Delivers the real consumer in
   one step and avoids a shadowing window — but blocks four easy functions
   behind a `P2` NaN-semantics decision that has been open a while.
3. **Ship `Double`-monomorphic `min_by`/`max_by` alongside the generic ones.**
   Covers everything today. I recommend against it: it is two APIs for one idea,
   it makes `Double` permanently second-class in the prelude, and per the
   standing rule it is exactly the kind of workaround that gets kept forever.

I recommend **(1)**, with §8's limitation written into the spec text and an
explicit `BACKLOG.md` follow-up linking these combinators to `Ord Double`, so
the gap is recorded rather than rediscovered.

## 10. Tests

Per Definition of Ready, these are written and confirmed failing before any
implementation. New file `tests/stdlib/test_min_max.spr`:

1. **Empty** — `minimum(Nil)`, `maximum(Nil)`, `min_by`/`max_by` over an empty
   `Vec` → `Nothing`. (Requires a type anchor, since `a`/`k` are otherwise free.)
2. **Singleton** — returns that element.
3. **`List Int` / `String`** — `minimum`/`maximum` over both instances of `Ord`.
4. **`Vec`** — the same over the other `Foldable` instance, confirming D1 pays off.
5. **argmin, not min** — `min_by` on a record list returns the *record* whose key
   is least, not the key.
6. **Tie-breaking (D4)** — a list of records sharing the minimum key and sharing
   the maximum key, distinguishable by a second field: `min_by` yields the first,
   `max_by` yields the last.
7. **The sort law (D4)** — `min_by(f, xs)` equals element 0 of
   `vec_sort_by(f, foldable_to_vec(xs))`, and `max_by(f, xs)` equals element
   `n-1`, on an input with duplicate keys.
8. ~~**Key evaluated once per element**~~ — **planned, then found infeasible.**
   Every mutation path in `stdlib/mutable.sprout` is `!{IO}`, and
   `Foldable.fold_values` carries no effect row, so a key function passed to
   `min_by` is necessarily pure and its call count is not observable from
   Sprout. The property holds by construction — the accumulator is
   `Maybe (k, a)`, so the incumbent's key is carried rather than recomputed —
   and is enforced by review of `min_by_step`/`max_by_step`, not by a test.
9. **Dictionary forwarding (§6)** — a test-local
   `fn pick_smallest(xs: c a) -> Maybe a where Foldable c, Ord a = minimum(xs)`
   called at two element types, so the dict is genuinely passed rather than
   devirtualized away.

## 11. Spec and docs

- `docs/spec-v0.md` §8.5 currently documents the `Functor → Applicative → Monad`
  tower and its derived free functions, but mentions `Foldable` only in passing
  (`:2345`). This change adds a short **`Foldable`-derived combinators**
  subsection covering the four new functions — their signatures, the `Maybe`
  return, and the tie-breaking rule with the stable-sort law. Status:
  **Experimental**, matching the rest of §8.5.
- This document is the rationale record and is non-normative.
- `README.md` and `docs/idiomatic-sprout.md` list no prelude functions today, so
  neither needs a change. (`vec_sort_by` is likewise undocumented in both — not
  a gap this change is taking on.)
- No `BACKLOG.md` entry is removed. If D2's non-goals are wanted later
  (`min_with`/`max_with`, generic two-argument `min`/`max`), they are added
  there as follow-ups when this lands.

## 12. Definition-of-Done notes specific to this change

Touching `stdlib/prelude.sprout` means: full `just test`,
`just compile-examples-stage1`, `just ir-golden-diff`, and a seed step.

**Answered by running it:** a new *ordinary* prelude `fn` changes the seed, not
only a new `extern fn`. The bundler emits every prelude definition into
`compile_driver`'s IR whether or not the driver calls it, so these four
functions plus three private helpers moved `bootstrap/compile_driver.ll` by
+721/−202 lines. The full `just refresh-seed` is therefore the correct step
here; the `verify-bootstrap-fixed-point` + `seed-fp-ack` bypass does **not**
apply. Fixed point reached at iteration 2.

Both large diffs were classified line-by-line rather than eyeballed, since the
`ir-golden-diff` report is truncated to 40 lines per file and cannot be read as
the diff:

| diff | removed lines | all accounted for by |
|---|---|---|
| `bootstrap/compile_driver.ll` | 202 | `__sprout_ir_lambda_N` / `__sprout_ir_eta_…_N` renumbering |
| `tests/golden/ir` (57 files, +31519) | 1936 | the same, plus `@.str.N` declarations and their `getelementptr` references |

Zero removed lines in either diff were existing code that changed. Adding four
lambdas shifts the sequential counter, which renames every later lambda — e.g.
`eta_$entry.is_repeated_twice_21` → `_25` — so a large removal count here is
mechanical, not semantic.
