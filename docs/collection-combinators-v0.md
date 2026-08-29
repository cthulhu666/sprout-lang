# Collection combinators — the Foldable/Filterable split (v0)

Status: **experimental**. Normative text: `docs/spec-v0.md` §8.5. Companion to
`docs/filterable-v0.md`, which introduced the `Filterable` class this extends.

## 1. Problem

The prelude's coverage of ordinary collection verbs was uneven by container
rather than by design:

| verb | List | Vec | generic |
|---|---|---|---|
| map | `list_map` | `vec_map` | `map` (Functor) |
| fold | `list_fold` | `vec_fold` | `fold` (Foldable) |
| filter | `list_filter` | `vec_filter` | `filter` (Filterable) |
| filter_map | — | `vec_filter_map` | — |
| any / all | — | `vec_any` / `vec_all` | — |
| count | — | `vec_count` | — |
| find | — | — | — |
| partition | — | — | — |

`find` existed for neither container. The one downstream consumer hand-rolls
ten `find_*` loops in a single file (`game/cmd.sprout`), plus `any_*`, `has_*`
and `count_*` helpers elsewhere, and `grimward/mods.sprout` defines its own
`any_of(f: a -> Bool, xs: List a)` — the prelude function that was missing,
written out by hand.

## 2. Goals / non-goals

Goals: one spelling per verb, working on both containers, with the container
preserved where the verb returns one.

Non-goals (§7): `length`/`is_empty`, `position`/`index_of`, `take`/`drop`/`zip`,
an effectful predicate, `Dict`/`Set` instances.

## 3. The organising rule

The survey (§4) produces one clean result: **every language splits these by
what the verb returns, not by which container it came from.**

- **Consumers** return a scalar or a `Maybe` — `any`, `all`, `find`,
  `find_map`, `count`, `member`. They are keyed on the *source* alone, so they
  need only `Foldable` and are plain free functions with no class change.
- **Producers** return the same container — `filter`, `filter_map`,
  `partition`. The class variable appears in the result, so they need
  `Filterable` methods.

That rule decides every placement below, and it is the same rule that already
put `minimum`/`maximum` on `Foldable` and `filter` on `Filterable`.

## 4. Prior art

Verified against primary sources (language reference or library source).

**Consumers** — keyed on the source in all five:

| | Haskell `base` | PureScript | Rust | Elm | Scala 2.13 |
|---|---|---|---|---|---|
| any / all | `Foldable t => (a -> Bool) -> t a -> Bool`, standalone | `Foldable f =>`, standalone | `Iterator` | `List.any` / `List.all` | `exists` / `forall` on `IterableOnceOps` |
| find | `Foldable t => (a -> Bool) -> t a -> Maybe a` | `Foldable f => … -> Maybe a` | `-> Option<Self::Item>` | **absent** | `find(p): Option[A]` |
| find_map | — | `findMap` | `find_map` | absent | `collectFirst` |
| count | — (`length ∘ filter`) | — | `count()`, no predicate | absent | `count(p): Int` |

**Producers** — keyed on the container in all five:

| | Haskell | PureScript | Rust | Elm | Scala |
|---|---|---|---|---|---|
| filter_map | `Data.Maybe.mapMaybe`, list-only | `Filterable.filterMap`, **class member** | `Iterator::filter_map` | `List.filterMap` | `collect` (partial function) |
| partition | `Data.List.partition -> ([a], [a])` | `Filterable.partition`, **class member**, `-> {no, yes}` | `-> (B, B)` where `B: Default + Extend` | `-> (List a, List a)` | `-> (C, C)` |

Naming: `any`/`all` over Scala's `exists`/`forall` (4 of 5, and `vec_any`/
`vec_all` already exist); `find` universally except Elm, which omits it;
`count(pred)` follows Scala and the existing `vec_count`; `member` follows Elm
and the existing `list_member` rather than Haskell's `elem`.

`partition` returns a **tuple**, following Haskell, Rust, Elm and Scala.
PureScript's record `{no, yes}` is the outlier, and Sprout tuples already carry
`Eq`/`ToString` instances.

## 5. Design

Six free functions over `Foldable`, no class change:

```sprout
any(pred: a -> Bool, xs: c a)      -> Bool     where Foldable c
all(pred: a -> Bool, xs: c a)      -> Bool     where Foldable c
find(pred: a -> Bool, xs: c a)     -> Maybe a  where Foldable c
find_map(f: a -> Maybe b, xs: c a) -> Maybe b  where Foldable c
count(pred: a -> Bool, xs: c a)    -> Int      where Foldable c
member(x: a, xs: c a)              -> Bool     where Foldable c, Eq a
```

Two additions to `Filterable`, with the free functions delegating as `filter`
already does:

```sprout
export class Filterable f
  fn filter_values(pred: a -> Bool, xs: f a) -> f a
  fn filter_map_values(f: a -> Maybe b, xs: f a) -> f b
  fn partition_values(pred: a -> Bool, xs: f a) -> (f a, f a)

filter_map(f: a -> Maybe b, xs: c a) -> c b        where Filterable c
partition(pred: a -> Bool, xs: c a)  -> (c a, c a) where Filterable c
```

New concrete builders behind the instances: `list_filter_map`,
`list_partition`, `vec_partition`. `vec_filter_map` already existed.

`any` is safe as a name despite `any C` being existential syntax: that spelling
is *contextual*, matched by identifier text in type position only
(`parser.sprout:1683`).

## 6. Three decisions worth recording

**`filter_map` is a second class method, not the primitive `filter` derives
from.** PureScript and witherable both make `filterMap` the primitive and
derive `filter` from it. In a strict language that derivation allocates a
`Maybe` per surviving element — a cost laziness and fusion pay for in Haskell
and Sprout cannot. Two methods, each a one-line delegation per instance.

**`partition` is a method, not two `filter` passes.** The two-pass derivation
applies the caller's predicate *twice per element*. Every language surveyed
partitions in one pass, and `min_by`/`max_by` already made "applied exactly
once per element" a documented guarantee; breaking it here would be
inconsistent within one prelude.

**The `Foldable` consumers could not short-circuit the traversal — RESOLVED
2026-08-29 by `fold_while` (`docs/fold-while-v0.md`).** As shipped here,
`fold_values` had no early exit, so `any`/`all`/`find`/`find_map`/`member`
visited every element even after the answer was settled: *O(n) traversal with
at most k predicate calls*, the identical limitation PureScript has for the
identical reason (strict, derives these from a fold), where Rust and Scala
escape only via an iterator protocol Sprout does not have. `Foldable` now
carries a second method, `fold_while_values`, and all five are rebuilt on it,
so each stops at the deciding element. No signature here changed, exactly as
predicted. Two consequences for the text above: `vec_any`/`vec_all` are no
longer "the faster spelling for a `Vec`" — they are these same functions
specialised to `Vec`, and their hand-written index loops are deleted — and
`count` is now the only consumer that still visits everything, which is
inherent rather than a limitation.

## 7. Deferred

- **`length` / `is_empty`.** A `fold`-derived version is O(n) *even on Vec*,
  where `vec_length` is O(1). Haskell avoids this by making `length`/`null`
  class methods with per-instance overrides; Sprout has no default method
  bodies (`parse_class_body` collects signatures only), so that route makes
  them mandatory for every future instance. Left out rather than shipped at the
  wrong complexity. Note `list_length` is currently **private**, so List still
  has no public length; `count(\_ -> true, xs)` is the O(n) stand-in.
- `position` / `index_of`, and `take`/`drop`/`zip` (List-shaped, not derivable
  from `Foldable`).
- An effectful predicate — the same blocker as `class Each`.
- `Dict`/`Set` instances, gated on those types getting `Functor`/`Foldable`.

Recorded in `BACKLOG.md`.

## 8. Tests

- `tests/stdlib/test_foldable_search.spr` — 44 assertions: both instances for
  every function, the vacuous cases (`any` of empty is false, `all` of empty is
  **true**), `find` returning the *first* match, the De Morgan `all`/`any` pair
  and the `any`/`find`/`count` agreement laws, and dictionary **forwarding**
  through `where Foldable c` callers.
- `tests/stdlib/test_filterable.spr` — extended to 37: `filter_map` and
  `partition` on both instances, the `filter_map`-with-always-`Just`-is-`map`
  law, the `filter` = `filter_map` derivation, and partition's halves matching
  the two filters.
- `tests/stdlib/test_prelude_name_shadowing.spr` — a module defining its own
  top-level `find`, `any` and `count` still compiles, still gets its own
  definitions, and still resolves the unshadowed prelude combinators. Not
  hypothetical: `grimward/combat.sprout` downstream defines
  `find(cs: List Combatant, id: Int)`, and `examples/ref_union_find.sprout`
  defines its own `find` in this repo.
