# `Dict`'s representation (v0, 2026-10-18)

**Status:** investigation. No code change. Phase 3.4 of
[native-sprout-runtime-v0.md](native-sprout-runtime-v0.md). The spec promises no order for `Dict`.
Keys come out ascending because the runtime is a tree, and only `docs/stdlib-reference.md` says so,
for `dict_entries_with_prefix`. [sorted-dict-v0.md](sorted-dict-v0.md) proposes making the order a
type of its own.

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
the key it points to, 5.5 times per lookup. §6 measures it.

**A lookup memo.** It skips 45% of lookups and gains nothing measurable (§6).

**Moving `Dict` to Sprout as it is.** It keeps every cost above and adds Sprout's own
([native-sprout-runtime-v0.md](native-sprout-runtime-v0.md) §1).

## 3. Who needs the order

Nothing in the spec promises the order, but three things read it:

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
instead of one, and none promises an order from the hash map. Sprout has one `Dict`, and its
callers rely on the order, so a hash map cannot replace it without either a second type
([sorted-dict-v0.md](sorted-dict-v0.md)) or a sorted view.

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
  order becomes interning order, which breaks every caller that reads keys in order.
- **Function — a lookup memo.** A persistent map at a given address never changes, so a lookup is
  a function of (root, key). 48% of lookups repeat a pair, which bounds what a cache could skip. It
  costs a key hash and a probe on every lookup, and must be cleared when a collection can reuse a
  root's address. Ceiling: half of lookup time (`map_get_unboxed` is 6.9%), ~3.5% of the
  compile. Measured in §6: no gain.
- **Representation — fewer dependent loads per step.** Keep the order, cut the latency: a wider
  persistent node (B-tree) with each key's leading bytes or hash stored in the node, so most steps
  decide without loading the key. Inserts copy wider nodes. Or a hash map beside the tree for
  lookups only, which doubles insert work (8% of samples today).

## 6. The memo, measured, and what it leaves

**Memo.** Hand-applied in a scratch copy of the runtime, master `8d2a9d8d`: a direct-mapped table
of 8,192 (root, key, value) entries in front of `bst_get`, tagged with `g_gc_cycle_count` so a
collection invalidates it. Output identical byte for byte. Two variants:

| variant | hits | instructions | cycles |
|---|---:|---:|---:|
| slot by key pointer, hit on pointer equality | 25.1% | −0.6% | −0.5% |
| slot by key hash (FNV-1a), hit on `strcmp` | 44.7% | −0.1% | −0.6% |

Medians of 8 runs, order alternated, from `time -l` (cycles and instructions retired, which
other load on the machine moves less than wall time). The cycle ranges overlap: base
11.72–12.43G, hashed 11.43–12.06G. The hashed variant reaches 44.7% of the 48% ceiling and gains
nothing that survives the noise.

**Why.** A build that always walks the tree and times each walk (`mach_absolute_time`, 5 ns of
its own per walk subtracted), split by whether the memo would have hit, over 3 runs:

| walk | count | ns per walk |
|---|---:|---:|
| first of its (root, key) | 2.34M | 62–81 |
| repeat | 1.90M | 53–71 |

Repeats are not cache-warm freebies: they cost 85–90% of a first walk. So the memo skips
100–135 ms of walking per compile, and costs about as much itself: a probe of a 256 KB table on
each of 4.24M lookups, plus a key hash in the hashed variant.

**What is left to win.** All walks together are 245–325 ms, 8–10% of user time, about 11 ns per
step. That bounds any fix to lookup, at any layer. A hash map still pays a hash, a load and a
compare per lookup, so it might save half: ~4–5% of a compile, for a new runtime structure and
the type split in [sorted-dict-v0.md](sorted-dict-v0.md). Inserts (~6%) and order-statistic walks
are separate and smaller.

**Recommendation.** Stop on `Dict` for speed. GC at 37% is the larger lever. Revisit `Dict`'s
representation only together with `Dict k v`, which needs a runtime change anyway.

## 7. Tests

Any representation change keeps the existing `Dict` and `Set` tests and adds a differential test:
the C tree and the new structure agree on lookups, `dict_keys`, `dict_entries` and
`dict_entries_with_prefix` over a corpus of inserts and removes. No spec or language change.
