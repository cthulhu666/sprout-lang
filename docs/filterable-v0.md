# Filterable — container-preserving generic `filter` (v0)

Status: **experimental**. Normative text: `docs/spec-v0.md` §8.5
("`Filterable` class and generic `filter`").

## 1. Problem

`filter` was `List`-only:

```sprout
export fn filter(pred: a -> Bool, xs: List a) -> List a
```

`Vec` had its own `vec_filter`. So the prelude's three core collection verbs
were generic in two cases out of three — `map` over `Functor`, `fold` over
`Foldable`, and `filter` over nothing. Code written against `where Foldable c`
or `where Functor c` had to stop being generic the moment it needed to drop
elements, and a reader had no rule for which spelling a given container wanted.

## 2. Goals / non-goals

Goals:

- One `filter` covering `List` and `Vec`, keeping the container kind.
- The prelude's existing method/free-fn split, so the class-method name stays
  out of the global namespace.
- No change to existing call sites.

Non-goals:

- `filter_map` and `partition` (§7).
- An effectful predicate (§6).
- `Dict`/`Set` instances — neither has a `Functor` or `Foldable` instance
  today, so filtering them is a wider question than this change.

## 3. Why not `Foldable`

`Foldable` only tears a container down:

```sprout
class Foldable f
  fn fold_values(step: b -> a -> b, init: b, xs: f a) -> b
```

Nothing in it builds an `f`. A fold-derived `filter` must therefore name one
concrete result type — in practice `List` — so `filter` over a `Vec` would
return a `List`, silently changing the type mid-`|>`-chain and forcing a
`vec_from_list` at every use. Rebuilding the input container requires the class
variable in the *result* position, which is a different class.

That is also why this could not be folded into the existing `Foldable`: adding
a structure-preserving method to it would make every `Foldable` instance owe an
implementation it may not be able to give.

## 4. Prior art

Verified against primary sources (source/reference, not summaries):

| Language | Shape |
|---|---|
| Haskell `base` | `filter :: (a -> Bool) -> [a] -> [a]` — monomorphic, list-only; `Data.Map.filter`/`Data.Set.filter` are separate qualified names |
| Haskell `witherable` | `class Functor f => Filterable f` with `filter :: (a -> Bool) -> f a -> f a`, plus `mapMaybe`/`catMaybes` |
| PureScript `Data.Filterable` | `class (Compactable f, Functor f) <= Filterable f`, members `filter`, `filterMap`, `partition`, `partitionMap` |
| Scala 2.13 | `IterableOps.filter(pred: A => Boolean): C`, where `C` is the collection's own type |
| Rust | `Iterator::filter(self, P) -> Filter<Self, P>` — an adapter; the container is recovered by `collect` |
| Elm | `filter : (a -> Bool) -> List a -> List a`, per module, no classes |

Two families. Languages with an iterator protocol (Rust) filter lazily and
rebuild explicitly. Languages without one either keep `filter` per-container
(Haskell `base`, Elm) or introduce a container-keyed abstraction returning the
same type (PureScript, witherable, Scala). Sprout has no iterator protocol, so
the Rust shape is unavailable, and it has typeclasses, so the per-container
route is a gap rather than a limit. The class is named after the two that
solved exactly this problem — `Filterable`.

## 5. Design

```sprout
export class Filterable f
  fn filter_values(pred: a -> Bool, xs: f a) -> f a

export fn filter(pred: a -> Bool, xs: c a) -> c a where Filterable c =
  filter_values(pred, xs)

instance Filterable List
  fn filter_values(pred: a -> Bool, xs: List a) -> List a = list_filter(pred, xs)

instance Filterable Vec
  fn filter_values(pred: a -> Bool, xs: Vec a) -> Vec a = vec_filter(pred, xs)
```

(The class has since grown `filter_map_values` and `partition_values` —
`docs/collection-combinators-v0.md`.)

The old `List` body became `export fn list_filter`, which also removes a naming
irregularity: `filter` had been the one `List` helper with no `list_` twin
(`list_map`, `list_fold`, `list_each`, `list_append`, `list_flat_map` all have
one). `vec_filter` is unchanged and still exported.

**No superclass.** PureScript requires `Compactable` + `Functor` and witherable
requires `Functor`. Both instances here are `Functor`s regardless, and neither
derives a method from the superclass, so requiring it would only widen every
dictionary. `Foldable` in this prelude has no superclass for the same reason.

**Instances delegate rather than recurse.** A class method that calls itself
from inside its own instance body fails dictionary resolution today (`BACKLOG`
B2), so `filter_values` calls the standalone `list_filter`/`vec_filter` — the
same shape `Foldable List.fold_values` already uses.

## 6. Purity

`pred : a -> Bool`, not `a -> Bool !{e}`. An effect-polymorphic class method is
the `class Each` generalization that B2 blocks; until that is fixed, an
effectful predicate stays a hand-written fold. This matches `Foldable`, whose
`fold_values` has no effect row either.

## 7. Deferred

- ~~`filter_map` over `Filterable`~~ and ~~`partition`~~ — **both landed**, as
  a second and third class method. See `docs/collection-combinators-v0.md` §6
  for why each is a method rather than a derivation (a derived `filter` would
  allocate a `Maybe` per surviving element; a two-pass `partition` would apply
  the caller's predicate twice per element).
- `Dict`/`Set` instances, gated on those types getting `Functor`/`Foldable`.
- An effectful predicate (§6).

Recorded in `BACKLOG.md`.

## 8. Tests

`tests/stdlib/test_filterable.spr` — 17 assertions covering both instances,
order preservation, empty/all/none edges, the `filter p . filter q ==
filter (p && q)` law, and dictionary **forwarding** through `where Filterable c`
and `where Filterable c, Functor c` callers. The forwarding cases are the load-
bearing ones: a concrete call site devirtualizes the dictionary away, so
without a constrained caller the dispatch path is never exercised.
