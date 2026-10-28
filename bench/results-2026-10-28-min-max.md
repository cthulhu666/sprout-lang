# Generic `max` against `imath.max` — 2026-10-28

`bench/min_max/bench.sh 7`, on `3faec881` plus the list-toolkit step 1 prelude. The measurement
[list-toolkit-v0.md](../docs/list-toolkit-v0.md) §4.7 asks for: what does the prelude's
`max(x, y) where Ord a` cost against `imath.max` in a DP loop?

Machine: Apple M3 Pro, built with `just compile-native`. Load average 15–20 during the runs, so
read the ratios; the absolute numbers are high. Kadane's maximum subarray sum over 100k Ints in
[-16384, 16384), 200 passes, two `max` calls per element. Checksums agree across the variants.

Variants:
- `generic` — the prelude's `max`.
- `int` — `imath.max`.
- `inline` — `if a >= b then a else b`, written out.

| row | unit | min | median | spread |
|---|---|---:|---:|---:|
| generic 100k | ns/elem | 12.9 | 14.5 | 12.4% |
| int 100k | ns/elem | 1.3 | 1.5 | 15.4% |
| inline 100k | ns/elem | 1.3 | 1.3 | 0.0% |

## Reading

- The generic `max` costs ~10× `imath.max` here: ~6.5 ns more per call.
- The emitted `@max` takes the `Ord` dictionary's two slots as arguments, roots `x` and `y`
  (a type-variable value is rooted as if it were heap, `docs/compiler-internals.md` §Type-aware
  GC rooting) and the dictionary slots, and calls `compare` through `__cm_Ord_compare`.
  `imath.max` is a direct `>=` on two `i64`s.
- Specialising `max` at `Int` would remove all of it; that is `docs/specialisation-v0.md`,
  blocked.
- Consequence: a hot `Int` loop keeps `imath.max` (or a selective `import stdlib.math.int (max)`,
  which beats the prelude name). The generic one is for the non-`Int` cases and code off the hot
  path.
