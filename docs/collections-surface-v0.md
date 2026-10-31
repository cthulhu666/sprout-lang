# Collections surface: what the prelude holds and what it is called (v0, 2026-10-31)

**Status:** proposal, not approved. No code change. The spec does not specify which collection
functions exist or where. §5.8 lists which combinators take `local`; §3.1 names a subset of the
prelude functions built-in syntax expands to. Depends on
[specialisation-v0.md](specialisation-v0.md) (§8). Related:
[list-toolkit-v0.md](list-toolkit-v0.md) §4.6.2, which named this change,
[collection-combinators-v0.md](collection-combinators-v0.md) (the Foldable/Filterable split) and
spec §8 (the per-type module convention for math).

## 1. Problem

The prelude exports 20 `list_*`, 23 `vec_*`, 10 `dict_*` and 9 `set_*` names beside a generic
surface (`map`, `fold`, `filter`, `any`, `count`, `member`, …). No rule says which concrete
names exist, which live in the prelude, or what a verb means.

- **One verb, three meanings.** `list_append(xs, ys)` joins two lists; `vec_append(x, v)` adds
  one element; `Semigroup.append` (`++`) joins.
- **Size is named five ways:** `list_length`, `vec_length`, `range_count`, `mutvec_len`,
  `set_size`/`linheap.size`. `Dict` has none; uncharted-suns (`loam/terrain.sprout`) takes
  `vec_length` of `dict_keys` for a count the AVL nodes cache.
- **Parity is accidental.** `vec_any`/`vec_all`/`vec_count`/`vec_sum` exist, their `List` twins
  do not. `list_each`/`list_fold` take an effect row, `Vec` has no `vec_each` and a pure
  `vec_fold`, so effectful `Vec` iteration goes through `vec_to_list`. `list_flat_map` has no
  `Vec` twin. `Dict` has `Functor` and `Filterable` but not `Foldable`, so `filter` works on a
  `Dict` and `any` does not.
- **Gaps are filled by hand:** `list_nth_or`, `list_index_of`, `list_is_empty` and
  `dict_get_or` in the compiler, `list_at` in uncharted-suns; 71 sites test emptiness with an
  O(n) `list_length(…) == 0`. sprout-postgres's `vec_contains_string` is the generic `member`,
  unfound.
- **Module styles differ.** `stdlib.list`, `stdlib.linvec` and `stdlib.linheap` use plain
  names, qualified and data-last (`list.take(n, xs)`). `stdlib.mutable` uses prefixed names
  imported bare, receiver-first (`mutvec_get(v, i)`), which guidelines §6's carve-out for
  qualified modules does not cover.

## 2. Goals and non-goals

Goals: one written rule for when a concrete `T_verb` exists and where it lives; one meaning per
verb; `List`/`Vec` parity wherever the reason for a function applies to both; no caller slows
down, per element or per call.

Non-goals:
- **Changing `dict_*` or `set_*` beyond `dict_size` and `dict_get_or`.** Both sit on one runtime
  map and face two pending changes: `Dict k v` with `Hash` (`BACKLOG.md` P1 **Polymorphic-keyed
  dicts**) and the sorted split of [sorted-dict-v0.md](sorted-dict-v0.md). No renames, moves or
  other additions until those land, so their surface moves once. `dict_entries_with_prefix`
  stays in the prelude on the same ground.
- **`range_*`**, beyond the `range_count` rename.
- `Foldable Dict`/`Set`, whose fold order is sorted-dict-v0's open question.
- Generic `length`/`is_empty` (BACKLOG P3 **Deferred non-goals**: O(n) on `Vec` without
  per-instance methods).
- Builders (`list_builder_*`, `bytes.builder_*`), whose verbs name what they append.
- Linear elements in a `Vec`. `vec_fold` reads each element with `vec_get`, which leaves it in
  the vector, so `list_fold`'s `where linear a` cannot be copied over; that needs a consuming walk.

## 3. Prior art

Every row is checked against the language's own source or reference.

| Language | Default scope | Per-container operations | Size |
|---|---|---|---|
| Gleam | types and constructors only (`compiler-core/src/type_/prelude.rs`) | `import gleam/list`, `list.map` | `list.length`; `dict.size`, `set.size`, no `length` |
| Elm | `import List exposing (List, (::))` among the default imports (elm/core README) | `List.map`, qualified | `List.length`, `Array.length`; `Dict.size`, `Set.size` |
| Haskell | Prelude: list-only `map` beside `fmap`; `length`, `null`, `elem`, `sum` Foldable-generic | `Data.Map` "is intended to be imported qualified, to avoid name clashes with Prelude functions" | `Data.Map.size`, `Data.Set.size`; `length` via `Foldable` |

All three keep per-container extras out of the default scope, and all three say `length` for a
sequence and `size` for a keyed or unordered collection. Haskell keeps a concrete `map` beside
a generic `fmap` in the default scope.

## 4. Measurements

Generic against concrete, `-O2`, min of three runs, ns per element. "Long" is n=100 000 × 200
calls; "short" is n=4 × 2 000 000 calls. Measured before specialisation.

| op | long concrete / generic | short concrete / generic |
|---|---|---|
| `list_map` / `map` | 44.3 / 44.0 | 39.1 / 42.0 |
| `list_fold` / `fold` | 5.2 / 5.3 | 13.1 / 17.1 |
| `list_filter` / `filter` | 42.7 / 44.9 | 24.8 / 29.6 |
| `vec_map` / `map` | 47.1 / 45.5 | 48.4 / 50.3 |
| `vec_fold` / `fold` | 3.2 / 3.2 | 17.8 / 22.9 |
| `vec_filter` / `filter` | 45.5 / 45.5 | 35.3 / 40.5 |
| `vec_any` / `any` | 6.5 / 6.6 | 25.1 / 31.4 |
| `list_member` / `member` | 5.1 / 12.9 | 17.6 / 39.1 |
| `vec_count` / `count` | 3.9 / 6.3 | 20.4 / 32.1 |

Two costs, with different causes:

- **Per call, 8–25 ns, every generic function.** A constrained free function receives its
  dictionary as `EvForward` evidence, which devirtualization does not touch
  ([devirtualization-v0.md](devirtualization-v0.md)). `any` pays it twice, through
  `fold_while`. It shows on short collections only. specialisation-v0's dictionary copies remove
  it.
- **Per element, 1.6–2.5×, in the consumers built on a fold.** `fold` hands its step straight to
  the instance. `count`, `member` and `any` wrap the caller's function in another closure, so
  each element pays two or three indirect calls where a hand-written loop pays one. `vec_any`
  is built on `vec_fold_while` the same way, which is why its long column matches. No design
  removes this yet: specialisation-v0 lists function values as a non-goal and only counts the
  sites.

Two semantic differences, independent of speed:

- **`local`.** Spec §5.8: `map`, `filter`, `filter_map`, `partition` and `and_then` take their
  function without `local`, since a `Functor` for a parser or state type keeps it. `list_map`,
  `vec_filter` and the other concrete producers take it `local`, so only they accept a lambda
  that captures a borrowed value.
- **Diagnostics.** `list_map(\x -> x + 1, 5)` reports `List Int vs Int`; `map(\x -> x + 1, 5)`
  reports `$t3190 Int vs Int`, a raw type-constructor variable (BACKLOG P3 **A type mismatch
  prints a raw type-constructor variable**).

## 5. Rules

**R1. A concrete `T_verb` exists only when the generic cannot stand in for it.** The reasons:
- **no generic exists**: no class abstracts it (`list_reverse`, `vec_get`, `dict_get`,
  `list_each` — there is no generic `each`);
- **effects**: the generic is pure (`list_fold`);
- **`local`**: the generic does not lend (`list_map`, `vec_filter`, …);
- **speed**, measured as in §4 after specialisation, per element and per call;
- **pinned by the compiler**: compiler string literals name it (`list_append`, `list_reverse`,
  `list_fold`, `range_fold`, `range_up`, `range_down`, `vec_from_list`, `dict_empty`,
  `dict_set`, in `infer`, `dce`, `ast_to_ir`, `parser` and `desugar_ctx`). Spec §3.1 names a
  subset and states that every name the compiler writes is exported.

A concrete name with none of these is a duplicate and is removed.

**R2. The prelude holds types, classes, generic verbs and each type's core.** The core is:
construction; size and emptiness; access by index or key where it costs O(log n) or less;
conversion to and from `List`; iteration (`each`, `fold`); the `local` producers; `reverse`.
Names R1 keeps for speed stay in the prelude too, since moving them would only add an import.
Everything else — slicing, set algebra on lists, searches, and `List` access by index, which is
O(n) and belongs where the cost is visible — goes in the type's module (`stdlib.list`, a new
`stdlib.vector`) under plain names, qualified. Collection modules are data-last like the prelude
they are used beside, as `stdlib.list` is.

The `Vec` module is `stdlib.vector`, not `stdlib.vec`. A local beats an import alias at a dotted
head (spec §3.1), and `vec` is a parameter name at 47 sites across the four repos, so
`vec.slice(…)` inside any of those functions would read the local. `vector` names no parameter.

The prelude cannot import (`no-prelude-core-v0.md`) and a module cannot call a private prelude
function (spec §3.1), so a function the prelude uses internally keeps a private copy there.

**R3. One verb, one meaning.** `append` joins two values of one type. Adding one element to a
sequence is `push` (as `linvec.push` and `mutvec_push` already are) or `prepend`. Size is
`length` on a sequence (`List`, `Vec`, `IntRange`, `LinVec`, `MutVec`) and `size` on a keyed or
unordered collection (`Dict`, `Set`, `LinHeap`), as in all three languages of §3. `Vec` and `Set`
convert both ways with `List`; `Dict` waits with the rest of its surface (§2).

**R4. Parity follows the reason.** When R1 gives `List` a concrete function and the same reason
holds for `Vec`, `Vec` gets it too, and the other way round.

## 6. Disposition

Rows marked **after re-measure** depend on speed and are decided once, on the numbers taken
after specialisation (§8 stage 2). The rest do not depend on speed.

| Name | Uses¹ | Action | Reason |
|---|---|---|---|
| `vec_append` | 59 | rename `vec_push` | R3 |
| `range_count` | 30 | rename `range_length` | R3 |
| `mutvec_len` | 114 | rename `mutvec_length` | R3 |
| `vec_fold` | 41 | add effect row `!{e}` | R4 (`list_fold`: effects) |
| `vec_each` | new | add | R4 (`list_each`: no generic) |
| `vec_flat_map` | new | add | R4 (`list_flat_map`: `local`) |
| `list_is_empty`, `vec_is_empty` | new | add | R2 core (size and emptiness) |
| `dict_size`, `dict_get_or` | new | add | R2 core, R3; gaps in §1 |
| `sum`, `sum_by` | new | add, Foldable-generic | will replace `vec_sum*` |
| `list_union`, `list_diff`, `list_dedup`, `list_add_unique` | 96 | move → `list.union`, … | R2 |
| `vec_slice` | 17 | move → `vector.slice`; add `vector.take`, `drop`, `last` | R2 |
| — | new | add `list.get`, `list.index_of` (both `Maybe`), `list.last` | R2; gaps in §1 |
| `vec_any`, `vec_all`, `vec_sum`, `vec_sum_by` | 36 | remove → `any`, `all`, `sum`, `sum_by`, **after re-measure** | duplicate if equal |
| `vec_fold_while`, `list_fold_while` | 13 | remove → `fold_while`, **after re-measure**; stay as private instance helpers | duplicate if equal |
| `list_member`, `vec_count` | 155 | keep until a function-values design lands, then re-measure | speed, per element (§4) |
| `vec_member`, `list_count` | new | add if stage 2 shows the same per-element gap | R4 |
| all other `list_*`, `vec_*`, `dict_*`, `set_*`, `range_*` | | keep | R1 reason recorded per name in the prelude comment |

¹ Occurrences across sprout_lang, uncharted-suns, repbit and sprout-postgres, definitions
included.

Hand-written copies that the new names replace are deleted in the stage that adds the name:
`list_is_empty` (`lint_rules`) and `dict_get_or` (`bundler`, whose `(key, dict, default)` order
flips to data-last `(key, default, dict)`) in stage 3; `list_index_of` and `list_nth_or`
(`infer`, which returns `-1` and a default where `list.index_of` and `list.get` return `Maybe`)
in stage 4. Left in place, each would shadow the prelude name silently (spec §3.1).

Beyond the `mutvec_len` rename, `stdlib.mutable`'s style (prefixed, receiver-first) is a
migration of its own and not part of this design.

## 7. Impact

- **Syntax:** none. **Type system:** none; `vec_fold` gains an effect row, which widens what it
  accepts.
- **Diagnostics:** callers moved to `sum` or `any` get §4's `$t…` message on a misuse until its
  BACKLOG entry lands. The producers, where beginners start, keep concrete names.
- **Speed:** every removal is decided on post-specialisation numbers, so no caller slows down.

## 8. Compatibility and migration

No aliases. A rename, move or removal lands as one sprout_lang PR plus one PR in each downstream
repo that uses the name. Each downstream branch is built and tested locally against the
sprout_lang branch (`mise exec -- env SPROUT_ROOT=<sprout_lang worktree> just test`). uncharted-
suns CI builds against one repo-level `vars.SPROUT_REF`, so its PR cannot go green before the
sprout_lang PR merges; it merges straight after, and its master is red for that window. repbit
and sprout-postgres have no CI against sprout_lang. Three test files define their own `sum`
(`tests/bce_fixtures/copy_cases.spr`, `tests/linear_reuse_smoke/tree.spr`,
`tests/stdlib/test_local_combinators.spr`), which then shadows the prelude's.

Every stage that edits `stdlib/prelude.sprout` reseeds, since the prelude is bundled into the
seed, and runs `just ir-golden-snapshot` with the full `git diff tests/golden/ir` read before
staging (AGENTS.md DoD #12).

Stages, each its own PR:
1. **Specialisation**: [specialisation-v0.md](specialisation-v0.md) M0 to M2. It removes the
   per-call dictionary cost, not the per-element closure cost (§4).
2. **Re-measure** §4 with specialisation on, and fix every **after re-measure** row of §6.
   The benchmark lands as `bench/generic_vs_concrete/`.
3. **Renames and additions** (§6 rows 1–9).
4. **Module moves** (§6 rows 10–12). `types`, `infer`, `unifier`, `linear_check` and `bce` gain
   `import stdlib.list`; `parser` gains `import stdlib.vector`, which puts `stdlib.vector` in the
   seed's import closure. Outside the compiler: `stdlib/math/bigint.sprout` (`vec_slice`).
5. **Removals** fixed in stage 2. In the seed's closure, `cse_census` uses `vec_sum` and
   `vec_sum_by`; outside it, `formatter` (`fmt_driver`) and `stdlib/http_server.sprout` use
   `vec_any`.

## 9. Tests

Per stage: each new function gets a behaviour test in `tests/stdlib/`, including the empty case;
`vec_fold` gets an effectful-step test and `vec_each` an order test; each renamed function's
existing tests move with it; `sum` gets a `List`, a `Vec` and an empty case; `list.index_of` and
`list.get` get a miss case.

## 10. Spec and docs

Spec §5.8's list of `local` combinators; §8.3's empty-range rule, which names `range_count`;
§8.5's prose naming `vec_sum_by`, `vec_any` and `vec_all`, and its instance tables;
`docs/stdlib-reference.md`; `docs/idiomatic-sprout.md` and the style guide where they name a
renamed function; `list-toolkit-v0.md` §4.6.2 points here. Guidelines §6 gains R1–R4, and its
"module-qualified modules are receiver-first" paragraph is narrowed to `stdlib.string` and
`stdlib.bytes`, which predate it.

BACKLOG: stage 1 closes P2 **Specialise generic functions at concrete types**. Stage 4 deletes item
(c) `position`/`index_of` from **Deferred non-goals**, and edits **`stdlib.collections` is a
7-line module** to say `stdlib.vector` is the collections module and `stdlib.collections`
survives only as the fixture that entry describes.

## 11. Alternatives

At each layer:
- **Caller**: accept the generic forms' cost and remove the duplicates now. Rejected: §4 shows
  short-collection and consumer callers would slow down, against §2.
- **Function**: hand-write each concrete consumer as a loop, as `vec_count` is, and keep every
  twin. Rejected: it keeps two spellings of each operation for a cost the compiler can remove.
- **Compiler**: specialise first, then decide (chosen). It costs a compiler change before any
  removal, and decides each dictionary-cost row once. The per-element rows wait for a
  function-values design.

Also rejected: a deprecation alias for each rename. Every caller is in four repos that migrate in
the same window, so an alias would only be a second spelling to remove later.

**`vec_prepend`** stays: O(n) like `vec_push`, and no other name fits it.
