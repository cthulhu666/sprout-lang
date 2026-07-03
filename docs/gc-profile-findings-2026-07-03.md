# GC collector profile — findings (2026-07-03)

Profile of the mark-sweep collector in `runtime/sprout_runtime.c`, measured at the
shipped `-O2` runtime optimisation level using the opt-in instrumentation added in
this branch (`-DSPROUT_GC_PROFILE`, run with `SPROUT_GC_PROFILE=1`; see
`just gc-profile <file>`). GC time below is the collector's own accumulated
microseconds, free of `SPROUT_DEBUG_GC` per-cycle accounting overhead.

## Two workloads, opposite heap shapes

| workload | wall | GC time | GC % | cycles |
|---|---|---|---|---|
| compiler self-emit (`stdlib/compiler/compile_driver.sprout`) | 51.4s | 32.2s | **63%** | 406 |
| `examples/nqueens.sprout` | 8.5s | 2.2s | **26%** | 33,256 |

`sample` on the compiler run: `sprout_gc_collect_with_reason` (mark/drain/sweep/
`find_managed_ptr` all inline into it at `-O2`) is the largest leaf symbol at ~46%
of on-CPU samples. `_platform_strlen` at ~41% is a separate *mutator* cost.

## Internal operation counts

| metric | compiler | nqueens |
|---|---|---|
| `find_managed_ptr` calls | 71.9M | 29.4M |
| total hash-chain hops | **296.6M** | 8.8M |
| **avg probe length** | **4.13** | **0.30** |
| drain edges | 70.8M | 21.7M |
| sweep visits | 84.2M | **136.2M** |

## The bottleneck is workload-dependent

1. **Compiler → `find_managed_ptr` pointer-chasing.** `g_heap_index` is a fixed
   131071-bucket hash table. As the live heap grows into the millions, average
   probe length degrades from 0.30 to **4.13**, so the collector performs ~296M
   chained, cache-missing dereferences. Nearly all 71.9M lookups originate in the
   drain phase (one per heap edge). **Lever: resize the index with the live heap.**

2. **nqueens → sweep is O(total heap) per cycle.** 136M sweep visits across 33k
   very frequent collections. Probe length is fine here. **Lever: collect less
   often (adaptive threshold already helps), or a region/bitmap sweep.**

## Memory overhead

Per live heap object:

- `ManagedNode` header = **48 bytes** (`ptr`, `kind`, `aux_slots`, `marked`,
  `next`, `hash_next`) — one per object, pure GC bookkeeping.
- `SproutObj` payload = **80 bytes, fixed** (`tag` + `f0..f8`), regardless of
  constructor arity.
- Example: `Just(42)` — logically 16 bytes — costs 48 + 80 = **128 bytes ≈ 8×**.
  The fixed-80 `SproutObj` is the dominant small-constructor waste.

Fixed costs: `g_heap_index` = **1.0 MiB** static (always resident);
`g_handle_table` = 16 KiB.

Retention: swept `SproutObj`s and `ManagedNode`s are placed on freelists and never
returned to `malloc`, so overhead is high-water-mark, not current-live — the
mechanism behind RSS not shrinking after a workload's peak.

## Does a mark bitmap help?

No. The measured costs are address→node resolution (`find_managed_ptr`) and sweep
traversal; neither is the mark-bit representation. A mark bitmap improves neither.
Ranked levers: (1) grow the hash index, (2) size-class `SproutObj`, (3) contiguous
regions — which is the change that would in turn make a sweep bitmap worthwhile.

## Reproducing

```
just gc-profile examples/nqueens.sprout
just gc-profile stdlib/compiler/compile_driver.sprout
```

Prints a `[gc profile] ...` summary to stderr at exit. The hot-path counters are
compile-time gated behind `-DSPROUT_GC_PROFILE`, so a normal build (`just run`) is
byte-identical and pays nothing.
