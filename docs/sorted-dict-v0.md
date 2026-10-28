# `SortedDict`: key order as a type (v0, 2026-10-18)

**Status:** proposal, not approved. No code change. Not normative until it lands in
[spec-v0.md](spec-v0.md). Related: [dict-representation-v0.md](dict-representation-v0.md) (why
`Dict` is slow) and the `BACKLOG.md` P1 **Polymorphic-keyed dicts** (`Dict k v` and a `Hash`
class).

## 1. Problem

`Dict v` and `Set` are one persistent AVL tree in C (`Map v`), keyed by `String`. Every function
that lists keys returns them in ascending order: `dict_keys`, `dict_values`, `dict_entries`,
`set_to_list`, and through them `Eq (Dict v)`, `ToString (Dict v)`, `ToString Set` and
`json.object_from_dict`. The spec promises none of this. `docs/stdlib-reference.md` says "in key
order" for one function, `dict_entries_with_prefix`. The order is a property of the tree, and
callers rely on it.

That ties the representation down. A hash map is what most languages use for a general-purpose
map, and `Dict` cannot become one without breaking every caller that reads keys in order. Nothing
marks which callers those are. A caller that needs order and one that does not look the same.

## 2. Goals and non-goals

Goals:

- Say in the type whether order is part of the contract. `SortedDict v` and `SortedSet` iterate in
  ascending key order, by promise. `Dict v` and `Set` promise no order.
- Free `Dict`'s representation, so a hash map can replace the tree when a measurement says it pays.
- Keep output stable where people read it: printing and JSON.

Non-goals:

- Changing `Dict`'s representation. That is a separate step; what it could gain is bounded in
  [dict-representation-v0.md](dict-representation-v0.md) §6.
- Generic keys. `Dict k v` and `SortedDict k v` come with the P1 entry; this design keeps `String`
  keys and must not make that step harder.
- Insertion order (Python's `dict`, Java's `LinkedHashMap`). Nothing in the corpus asks for it.

## 3. Prior art

Checked against each language's reference or standard-library docs, 2026-10-18:

| language | hash or default map | its order | sorted map | range queries |
|---|---|---|---|---|
| Java | `HashMap` | none, not even constant over time | `TreeMap` (`SortedMap`, `NavigableMap`) | `ceilingKey`, `subMap`, `tailMap` |
| Kotlin | `mutableMapOf()` | insertion | `sortedMapOf` (JVM only) | Java's |
| Rust | `HashMap`, randomly seeded against HashDoS | arbitrary | `BTreeMap` | `range` |
| Clojure | `hash-map` | none | `sorted-map` | `subseq`, `rsubseq` |
| Go | `map` | unspecified, may differ between iterations | none in the standard library | — |
| Swift | `Dictionary` | "stable between mutations but is otherwise unpredictable" | none in the standard library | — |
| Gleam | `dict` | "not ordered in any way" | not checked | — |
| Python | `dict` | insertion, guaranteed since 3.7 | none in the standard library | — |
| Elm | `Dict` is a red-black tree | ascending | `Dict` itself | — |
| Haskell `containers` | `Data.Map` is a balanced tree | ascending | `Data.Map` itself | `lookupGE`, `split`, `takeWhileAntitone` |

Most of these make the hash map the plain name and give the sorted map a name that says so
(`TreeMap`/`SortedMap`, `BTreeMap`, `sorted-map`). Elm and Haskell's `containers` make the plain
name sorted, as Sprout does today. Every sorted map here that has range queries keeps them on
the sorted type, which is where `dict_entries_with_prefix` belongs.

## 4. Design

**Types.** In the prelude, beside `Dict` and `Set`:

```
export type SortedDict v =
  | SortedDict (Map v)

export type SortedSet =
  | SortedSet (Map Unit)
```

Same runtime tree as today, so `SortedDict` costs what `Dict` costs now.

**API.** `SortedDict` gets every `dict_*` function under a `sorted_dict_` prefix, and
`SortedSet` every `set_*` function under `sorted_set_`. The order-only functions move:
`dict_entries_with_prefix` becomes `sorted_dict_entries_with_prefix` and leaves `Dict`. New
order functions belong on `SortedDict` only (a range, the first or last key). Conversions in both
directions are O(n) by contract, even while both types share one tree.

**What `Dict` promises after the split.** `dict_keys`, `dict_values`, `dict_entries` and
`set_to_list` return each key once, in an order the spec leaves unspecified. Within one build of
the runtime the order depends only on the key set, never on insertion history (§5).

**Instances.**

- `Eq (Dict v)` compares by lookup: equal sizes and every key of one found, equal, in the other.
  Today it zips two `dict_entries` lists, which is correct only while equal key sets list in the
  same order. `Eq (SortedDict v)` keeps the zip.
- `ToString (Dict v)` and `ToString Set` sort the keys before printing. Printing is not a hot
  path, and a sorted print keeps every test that prints a dict stable when the representation or
  hash changes.
- `Semigroup` as today, for both.
- `Functor`, `Filterable` and `Monoid` on `Dict` are order-independent and have landed;
  `Foldable` and `Traversable` wait for this doc's order decision.
- `json.object_from_dict` sorts too, so JSON output stays byte-identical to today's.

**Literals.** `{foo: 1, "bar": 2}` still builds a `Dict`; the spec builds literals from the bare
names `dict_empty` and `dict_set`. A `SortedDict` comes from `sorted_dict_from_list` or from a
`Dict`.

## 5. Determinism

A hash map's order is fixed by its hash function. Two choices:

- **Fixed hash, no per-process seed.** Every run lists a key set the same way. Compiler output,
  the bootstrap fixed point and golden IR stay reproducible without each compiler map moving to
  `SortedDict`. The cost: a client who knows the hash can choose colliding keys. The one
  untrusted-input `Dict` in stdlib is `http_server`'s headers, and `max_header_bytes` caps them.
  A collision bucket kept sorted bounds the damage to a log factor.
- **Seeded per process**, as Rust does. Resists collision attacks, but every compiler map whose
  order reaches the output must then be a `SortedDict`, or the build stops being reproducible.

Recommendation: fixed hash. It keeps reproducibility a property of the runtime instead of a rule
every caller must remember, and the input it exposes is already bounded.

"Unspecified" must still be enforced, or callers drift back to relying on one hash's order. §6
stage 2 adds a test mode for that.

## 6. Implementation overview

**Stage 1: add the types.** `SortedDict v` and `SortedSet` over today's `Map v`, the full API,
instances, and the order promise in the spec. Nothing breaks.

**Stage 2: make `Dict`'s order unspecified.** The spec says so; `dict_entries_with_prefix` moves;
`Eq`, `ToString` and JSON change as in §4. Callers that need order move to `SortedDict` or sort.
To find them, a runtime test mode lists `Dict` and `Set` keys in a scrambled but deterministic
order (for example, sorted by a hash of the key). Run every suite, golden IR and the four repos
under it; each difference is a caller that relied on order. The mode stays in CI so the rule
holds. It changes how existing builtins iterate rather than adding one, but it is a runtime change
and needs approval (`AGENTS.md` §Builtin vs Stdlib).

**Stage 3: change `Dict`'s representation**, together with `Dict k v` and `Hash`, so `Dict`
callers migrate once.

Stages 1 and 2 alone make nothing faster. Their value is the freedom stage 3 needs and a contract
that says what a caller can rely on. Speed alone does not pay for stage 2:
[dict-representation-v0.md](dict-representation-v0.md) §6 bounds what a hash `Dict` could save at
~4–5% of a self-compile. So stage 2 goes with `Dict k v`, which migrates every `Dict` site anyway;
stage 1 can go first.

## 7. Impact

- **Syntax:** none.
- **Semantics:** `Dict` and `Set` iteration order becomes unspecified (stage 2). Printing and JSON
  output keep today's order.
- **Type system:** two nominal types. No new rules.
- **Errors:** `dict_entries_with_prefix` on a `Dict` becomes an unknown name. The diagnostic should
  name `sorted_dict_entries_with_prefix` and say that order needs a `SortedDict`.

## 8. Compatibility and migration

Calls that read order (`dict_keys`, `dict_values`, `dict_entries`, `dict_entries_with_prefix`,
`set_to_list`), counted 2026-10-18, comments excluded:

| where | calls | note |
|---|---:|---|
| `stdlib/compiler/` | 62 in 14 files | 20 `set_to_list`, 16 `dict_entries`, 10 `dict_values`, 9 prefix, 7 `dict_keys` |
| rest of `stdlib/` | 13 | 12 in the prelude, 1 in `json.object_from_dict` |
| `tests/` | 18 | `test_collections.spr` asserts "dict_keys in key order" |
| `examples/` | 2 | |
| uncharted-suns | 1 | |
| sprout-pg | 0 | uses `Dict`, never its order |
| repbit | 0 | no `Dict` |

Not every call needs the order. Stage 2's test mode decides which do, not a hand count. The type
environment's prefix queries (9 calls) need it. It moves to `SortedDict` unless its marker
namespaces move to maps of their own ([dict-representation-v0.md](dict-representation-v0.md) §5),
and that choice decides whether the compiler's largest map gains anything from stage 3.

## 9. Tests

- Stage 1: `tests/stdlib/test_sorted_dict.spr` and `test_sorted_set.spr`. They cover the API,
  ascending order after inserts and removes in shuffled order, prefix queries at the boundaries
  (empty prefix, no match, a prefix that is a whole key), and both conversions.
- Stage 2: `Eq (Dict v)` holds for equal maps built in different insertion orders, run under the
  scrambled mode. `ToString` and `object_from_dict` give the same bytes under both modes.
- Conformance fixture for the spec's order promise on `SortedDict`.

## 10. Spec and docs

- `spec-v0.md`: a section for `Dict`/`Set` versus `SortedDict`/`SortedSet` that states the order
  contract. Normative from stage 1 for `SortedDict`, from stage 2 for `Dict`.
- `docs/stdlib-reference.md`: the new API, and "in key order" removed from `Dict`.
- `docs/idiomatic-sprout.md`: when to pick which.

## 11. Open questions

- Function names. `sorted_dict_get` is long. A shared class over both maps would avoid two copies
  of the API, which Haskell's `Map`/`HashMap`/`IntMap` lack
  ([haskell-lessons-learned.md](haskell-lessons-learned.md)). It needs a constructor class over
  `Dict` and `SortedDict`, and it is easier to design with `Dict k v`. So: prefixed functions now,
  class with P1?
- Whether `Set` should ever iterate at all, or only fold. Out of scope here.
