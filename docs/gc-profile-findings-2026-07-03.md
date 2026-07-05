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

Note: profiling `compile_driver.sprout` via the recipe runs the compiled driver
with no arguments (usage error, no work). For the self-emit workload, build the
profiled binary the same way and run it compiling its own source:
`SPROUT_GC_PROFILE=1 <profiled_driver> --emit-ir stdlib stdlib/compiler/compile_driver.sprout > /dev/null`.

## Addendum (2026-07-03, later): exact-size `SproutObj` results

Phase 1 of the header rewrite (exact-size `SproutObj`: `8 + arity*8` bytes,
per-arity freelists) landed on top of the 4.19M-bucket index STOPGAP. New
counters: `max_probe` (worst single probe) and a drain-phase hit/miss hop split
(`miss_hop_frac` = share of trace hash work spent proving scalars are not
pointers).

Measured on the same machine, sequential runs, self-emit workload:

| metric | before (STOPGAP base) | after exact-size | Δ |
|---|---|---|---|
| self-emit GC time (profiled) | 26.1s | 14.3s | −45% |
| self-emit wall (plain -O2) | 35.8s | 28.4s | −21% |
| self-emit peak memory footprint | 420.5 MB | 322.5 MB | −23% |
| self-emit max RSS | 437.8 MB | 415.3 MB | −5% |
| nqueens max RSS | 39.0 MB | 38.1 MB | −2% |

- The GC-time drop comes from locality (cycles and hop counts are unchanged;
  sweep/trace touch ~half the bytes). nqueens is vector-heavy with few ADT
  cells, so its flat result is expected, not a regression.
- Max RSS moves less than footprint because freelists retain the high-water
  mark and the enlarged index adds ~32 MiB static.
- `miss_hop_frac` = **6.9%** on self-emit: a `field_kinds`-based scalar skip at
  trace time would eliminate ~33% of trace lookups but only ~7% of hop work —
  a modest win, not a lever.
- Remaining per-object overhead is the 48-byte `ManagedNode` + hash table;
  that is the Phase 2 target (inline 1-word header + region allocator +
  address-range membership; decided direction: non-moving generational,
  integer tagging deferred).

## Addendum 2 (2026-07-05): Phase 2 complete — regions + header, table deleted

Phase 2 landed on `gc-phase2-regions`: inline 1-word header on every heap
object (kind | color | reserved GC bits | aux), 1 MiB region allocator with
per-region slot bitmaps (membership = region binary-search + payload-start
bit — exact, O(1), zero chain hops), header-color marking, linear region
sweep with per-sweep freelist rebuild and empty-region release, O(1)
`str_byte_len` + length-first `str_eq` from the CSTR header, and deletion of
`ManagedNode` (48 B/object), `g_heap_index` (the 32 MiB STOPGAP table), and
the whole hash machinery.

| metric | pre-phase-1 | phase 1 | phase 2 | total |
|---|---|---|---|---|
| self-emit wall (plain -O2) | 35.8s | 28.4s | **12.2s** | 2.9× |
| self-emit GC time | 26.1s | 14.3s | **3.6s** | 7.3× |
| self-emit peak footprint | 420.5 MB | 322.5 MB | **212.5 MB** | −49% |
| self-emit max RSS | 437.8 MB | 415.3 MB | **284.6 MB** | −35% |
| nqueens max RSS | 39.0 MB | 38.1 MB | **4.8 MB** | 8.1× |
| membership hash hops | 296.6M | 53.5M | **0** | — |

- Wall improvement exceeds the GC savings: the O(1) `str_byte_len` header
  read removed most of the ~41%-of-mutator `_platform_strlen` cost.
- nqueens' 8× memory drop is the static table + per-object nodes vanishing;
  its remaining GC time is sweep-per-cycle (33k cycles × O(heap)) — the
  lever for that is the generational nursery, the planned next step.
- Full suite + `test-stress` green under `SPROUT_GC_HDRCHECK=1`; suite wall
  itself dropped 164s → 85s and suite peak RSS 1067 → 456 MB.

### Benchmark A/B (2026-07-05, post-review-fixes 959144f)

Identical stage-1-emitted IR linked against master's runtime vs this
branch's — the collector is the only variable. Outputs identical.

| benchmark | master runtime | phase-2 runtime | Δ |
|---|---|---|---|
| nqueens wall | 6.02s | 5.75s | −4.5% |
| nqueens N=13 solve | 4825 ms | 4612 ms | −4.4% |
| nqueens max RSS | 38.0 MB | 4.3 MB | 8.9× less |
| astar wall (100 runs, 100×100) | 0.20s | 0.20s | flat |
| astar max RSS | 37.8 MB | 4.5 MB | 8.4× less |

Reading: the ~9× memory drop on both is the deleted 32 MiB index + 48 B/object
`ManagedNode`s — small programs' RSS now tracks their working set. Wall gains
here are single-digit by design: neither workload was hash-bound (nqueens probe
len was 0.30 pre-campaign); nqueens' remaining GC cost is 33k sweep cycles ×
O(heap), which is the generational-nursery follow-up's target. The large wall
wins live in the compiler itself (2.9× self-compile).

Repro: `./build/compile_driver_bin_stage1 --emit-ir stdlib examples/<b>.sprout
> b.ll && clang b.ll <runtime.c> -O2 -framework Security -framework
CoreFoundation -o bin && /usr/bin/time -l ./bin`.
