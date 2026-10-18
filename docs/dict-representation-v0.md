# `Dict`'s representation (v0, 2026-10-18)

**Status:** investigation. No code change. Phase 3.4 of
[native-sprout-runtime-v0.md](native-sprout-runtime-v0.md). The order `Dict` keys follow is
normative ([spec-v0.md](spec-v0.md) §`Eq` and `Ord` on `Double`: `Dict` keys use `compare`'s
total order); nothing
here changes it.

## 1. Problem

`Dict` is a persistent AVL tree in C (`bst_get`, `bst_insert_node` in `runtime/sprout_runtime.c`),
keyed by `strcmp`, with each node's subtree size kept for order statistics. It is the second
largest cost when the compiler compiles itself, after GC.

**Profile.** `--emit-ir stdlib/compiler/compile_driver.sprout` with the stage-1 binary (seed IR
and runtime both at `-O2`, per-TU link), master `773e3274`, macOS arm64. `sample` at 1 ms over 5
whole runs, ~3,100 samples each. Self time, each sample charged to the nearest caller that names a
bucket, so `strcmp` called from `map_get_unboxed` counts as `Dict`:

| bucket | share | range over 5 runs |
|---|---:|---:|
| GC and allocation | 36.7% | 35.7–37.5% |
| **`Dict`** | **19.8%** | 19.3–20.2% |
| output I/O (`write`) | 15.4% | 14.9–15.9% |
| other runtime and libc (`sprout_tag`, `memmove`, `sprout_field`, `str_*`) | 12.9% | |
| root stack | 9.5% | |
| code emitted from Sprout | 5.2% | |

Output I/O is not a lever: stdout is line-buffered (`sprout_set_argv`), so 440k lines of IR are
440k `write` calls, but a fully buffered build cut `sys` from 0.13 s to 0.06 s and left wall time
where it was (median of 7 interleaved runs, 3.63 s against 3.68 s).

Within `Dict`, by entry point (samples per run): `map_get_unboxed` 213, `bst_insert_node` 177,
`bst_to_list_acc` 62, `intern_string` 56, rotations and rebalancing 72.

**Counted, one self-compile:**

| what | count |
|---|---:|
| lookups | 4.23M, **63% miss** |
| tree steps per lookup | 5.5 |
| bytes compared per step | 9.0 (keys share long prefixes: `stdlib.compiler.`, `@pat`) |
| hits whose query is the stored pointer | 24% |
| inserts | 1.67M, 3.8 steps each |
| `map_keys` calls | 329k, 2.2 keys each |
| order-statistic lookups (`map_nth_*`) | 32k, on maps of 7,446 keys on average (largest 10,856) |
| lookups that repeat an earlier (root, key) pair | 2.02M (48%) |

Lookups have no dominant caller: the largest, `unifier.apply_subst_lookup`, is 4% of lookup time.
Inserts do: `ir_rooting.compute_live_out` 15%, `infer.partition_segment` 12%,
`ir_pipeline.build_alloc_summary_worker` 12%.

## 2. What is ruled out

**The comparison itself.** Two variants, same output byte for byte, 9 interleaved runs each,
wall median: an inline byte loop in place of `strcmp` (no call, no stub) 3.70 s against 3.70 s; the
same plus a descent that skips the prefix already matched by both bounds on the path 3.65 s. Within
noise. Bytes compared are not the cost. What is left is latency: each step loads a node and then
the key it points to, 5.5 times per lookup. That is the working hypothesis, and it is untested.

**Moving `Dict` to Sprout as it is.** It keeps every cost above and adds Sprout's own
([native-sprout-runtime-v0.md](native-sprout-runtime-v0.md) §1).

## 3. Who needs the order

The order is normative, and three things read it:

- `dict_keys` / `set_to_list`: an in-order walk. Small maps: 2.2 keys per call.
- `dict_values`, `dict_entries`: `n` order-statistic descents, O(n log n). 19k of the 32k `nth`
  calls.
- `dict_entries_with_prefix`: a binary search by `map_nth_key` for the first key at or after the
  prefix, then a run of reads. 13k `nth` calls: 10k binary-search steps at ~13 per query, so
  about 770 queries. All on the type environment,
  which keeps marker namespaces in the same `Dict` as its bindings and reads them by prefix:
  `@fwd:` (187 keys), `@super:` (4), `@class:` (19), `@aliasty:` (1), `@rec:` (136). The
  environment holds 7–11k keys and changes with every binding.

The third is what makes a hash map expensive: an order that is only produced on demand would be
re-sorted for each of ~770 queries over a map that has changed since the last one.

## 4. Prior art

| library | structure | iteration order | order statistics |
|---|---|---|---|
| Haskell `containers` `Data.Map` | size-balanced binary tree | increasing key | `elemAt`, `lookupIndex`, O(log n) |
| Haskell `unordered-containers` `Data.HashMap` | hash array mapped trie | unspecified, may change between versions | — |
| Clojure `hash-map` / `sorted-map` | HAMT (`PersistentHashMap`) / red-black tree (`PersistentTreeMap`) | none / comparator | — |
| Scala 2.13 `immutable.HashMap` / `TreeMap` | CHAMP / red-black tree | none / `Ordering` | — |
| OCaml `Map.Make` | balanced binary tree | increasing key (`bindings`) | — |
| Rust `HashMap` / `BTreeMap` (not persistent) | hash table / B-tree | arbitrary / key order | — |

Every library here that offers a persistent hash map offers it **beside** an ordered tree, not
instead of one, and none promises an order from the hash map. Sprout has one `Dict` whose order is
normative, so a hash map cannot replace it without either a second type or a sorted view.

Compilers avoid string keys in hot maps instead: rustc's `Symbol` is an index into an interner,
and equality, hashing and **ordering** all work on the index (`rustc_span/src/symbol.rs`), so a map
keyed by symbols is not in alphabetical order. GHC's `UniqFM` is a `Word64Map` keyed by `Unique`
(`GHC/Types/Unique/FM.hs`). HAMT: Bagwell, *Ideal Hash Trees* (EPFL, 2001).

## 5. Options

At each layer:

- **Caller — the environment's prefix queries.** Give each marker namespace its own `Dict`, so a
  prefix query becomes `dict_entries` of a small map. That removes the only order use on a large,
  changing map, which is what makes a hash map possible. It is a type-checker change across every
  place a marker is written or read. Interning keys to `Int` symbols, the
  rustc and GHC shape, is the larger form of this: every compiler map changes key type, and the
  order becomes interning order, which the spec does not allow for `Dict`.
- **Function — a lookup memo.** A persistent map at a given address never changes, so a lookup is
  a function of (root, key). 48% of lookups repeat a pair, which bounds what a cache could skip. It
  costs a key hash and a probe on every lookup, and must be cleared when a collection can reuse a
  root's address. Ceiling: half of lookup time (`map_get_unboxed` is 6.9%), ~3.5% of the
  compile.
- **Representation — fewer dependent loads per step.** Keep the order, cut the latency: a wider
  persistent node (B-tree) with each key's leading bytes or hash stored in the node, so most steps
  decide without loading the key. Inserts copy wider nodes. Or a hash map beside the tree for
  lookups only, which doubles insert work (8% of samples today).

## 6. Recommendation

Do not pick a representation yet: the claim that would choose between them, that descent latency
is the cost, is untested, and the one cheap experiment that tests it is the memo. Hand-apply it in
a scratch runtime, clear it on each collection, and measure wall time over interleaved runs. If
it gains near its ~3.5% ceiling, latency is the cost and the wider node is worth prototyping next.
If it gains nothing, `Dict`'s 20% is spread too thin to recover in `Dict`, and GC's 37% is the
better target.

## 7. Tests

Any representation change keeps the existing `Dict` and `Set` tests and adds a differential test:
the C tree and the new structure agree on lookups, `dict_keys`, `dict_entries` and
`dict_entries_with_prefix` over a corpus of inserts and removes. No spec or language change.
