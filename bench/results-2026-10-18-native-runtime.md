# Native-runtime Phase 0 baseline — 2026-10-18

`bench/native_runtime/bench.sh 9`, at `d6916b81`. Phase 0 of
[native-sprout-runtime-v0.md](../docs/native-sprout-runtime-v0.md): the merge-sort pair and the
workload set, 9 rounds, every row once per round so load lands on all rows alike.

Machine: Apple M3 Pro (11 cores), macOS 15.7.5, Homebrew clang 23.1.1, load average 4.8–4.9
throughout. Sprout binaries are built with `just compile-native` (whole-program link); the C half
with `clang -O2`; self-compile runs `build/compile_driver_bin_stage1` as `just bootstrap-from-seed`
builds it.

| row | unit | min | median | spread |
|---|---|---:|---:|---:|
| sort, C | ns/elem, n=440 | 11.7 | 12.5 | 6.8% |
| sort, C | ns/elem, n=100k | 33.1 | 33.9 | 2.4% |
| sort, Sprout specialised to Int | ns/elem, n=440 | 54.0 | 55.1 | 2.0% |
| sort, Sprout specialised to Int | ns/elem, n=100k | 107.3 | 109.2 | 1.8% |
| sort, Sprout `mutvec_sort` (generic) | ns/elem, n=440 | 118.1 | 120.2 | 1.8% |
| sort, Sprout `mutvec_sort` (generic) | ns/elem, n=100k | 220.0 | 224.0 | 1.8% |
| self-compile | ms | 3100.5 | 3167.9 | 2.2% |
| `examples/nqueens.sprout` | ms | 1360.9 | 1386.2 | 1.9% |
| `bench/gc_roots` | ms | 723.3 | 753.3 | 4.1% |
| `bench/http_log_middleware` | ms | 4272.4 | 4431.1 | 3.7% |

Spread is (median − min) / min: each row's noise floor, which later gates are stated against.
Sort rows are the programs' own ns per element, for fill+sort minus fill alone; all three print
the same checksum, so they sort the same input to the same output.

**Load matters more than the floors suggest.** An earlier run the same morning at load average
9.6 gave spreads of 14–19% and a self-compile minimum of 3.72 s. Run this with the machine as
quiet as it gets, and check `uptime` before trusting a floor.

## The Sprout-vs-C gap

The pair is one algorithm — `mutvec_sort`'s insertion blocks of 16 and bottom-up merge passes —
in C (`sort_c.c`) and in Sprout (`sort_bench.sprout`, an Int-specialised copy beside the shipped
generic one).

| | n=440 | n=100k |
|---|---:|---:|
| Sprout Int-specialised / C, by median | 4.4× | 3.2× |
| Sprout generic / C, by median | 9.6× | 6.6× |
| generic / Int-specialised, by median | 2.2× | 2.1× |

Medians, because a ratio of minimums pairs each side's luckiest run: C's 11.7 ns at n=440 came
from one round of nine, and makes that ratio 4.6×. Round 1's C runs were slow (51.0 and 19.0 ns,
against ~34 and ~12.5 after), so Int-specialised / C was 2.1× and 2.9× there, and 2.9–3.4× and
4.2–4.6× in every later round.

The ~2× between generic and specialised matches what [sorting-v0.md](../docs/sorting-v0.md)
attributes to `Ord` dispatch. What is left, 3–4× on identical code, is the gap Phase 2 has to
close, and the number its exit bound (§9, question 1) is set against.

## Not in this table

- The `uncharted-suns` galaxy frame: another repository, left for a run that builds it.
- Per-candidate port benches for Phase 3 (`crypto_base64_*`, `json_*`, string helpers): Phase 3
  cannot start before Phase 2's bound is met, so they wait.
