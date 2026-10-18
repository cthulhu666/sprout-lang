# Native-runtime Phase 0 baseline — 2026-10-18

`bench/native_runtime/bench.sh 9`, at `d6916b81`. Phase 0 of
[native-sprout-runtime-v0.md](../docs/native-sprout-runtime-v0.md): the merge-sort pair and the
workload set, 9 rounds, every row once per round so load lands on all rows alike.

Machine: Apple M3 Pro (11 cores), macOS 15.7.5, Homebrew clang 23.1.1. Sprout binaries are built
with `just compile-native` (whole-program link); the C half with `clang -O2`; self-compile runs
`build/compile_driver_bin_stage1` as `just bootstrap-from-seed` builds it.

**The machine was loaded**: load average 9.6 on 11 cores at the end (other sessions, an IDE, and
XProtect scanning the freshly built binaries). Self-compile's minimum here is 3.72 s against
3.09–3.13 s wall in quieter runs the day before. So the spreads below are this session's noise
floors, not the machine's.

| row | unit | min | median | spread |
|---|---|---:|---:|---:|
| sort, C | ns/elem, n=440 | 12.8 | 13.6 | 6.2% |
| sort, C | ns/elem, n=100k | 34.8 | 36.1 | 3.7% |
| sort, Sprout specialised to Int | ns/elem, n=440 | 55.0 | 64.8 | 17.8% |
| sort, Sprout specialised to Int | ns/elem, n=100k | 109.6 | 124.8 | 13.9% |
| sort, Sprout `mutvec_sort` (generic) | ns/elem, n=440 | 119.1 | 137.3 | 15.3% |
| sort, Sprout `mutvec_sort` (generic) | ns/elem, n=100k | 223.7 | 266.7 | 19.2% |
| self-compile | ms | 3720.1 | 4260.1 | 14.5% |
| `examples/nqueens.sprout` | ms | 1561.4 | 1806.7 | 15.7% |
| `bench/gc_roots` | ms | 767.4 | 910.3 | 18.6% |
| `bench/http_log_middleware` | ms | 4546.6 | 5397.8 | 18.7% |

Spread is (median − min) / min. Sort rows are the programs' own ns per element, for fill+sort
minus fill alone; all three print the same checksum, so they sort the same input to the same
output.

## The Sprout-vs-C gap

The pair is one algorithm — `mutvec_sort`'s insertion blocks of 16 and bottom-up merge passes —
in C (`sort_c.c`) and in Sprout (`sort_bench.sprout`, an Int-specialised copy beside the shipped
generic one). By minimum:

| | n=440 | n=100k |
|---|---:|---:|
| Sprout Int-specialised / C | 4.3× | 3.1× |
| Sprout generic / C | 9.3× | 6.4× |
| generic / Int-specialised | 2.2× | 2.0× |

Ratios within one round are no steadier than the minimums: Int-specialised / C ran 2.96–4.25×
at n=100k and 4.12–6.21× at n=440 across the 9 rounds.

The ~2× between generic and specialised matches what [sorting-v0.md](../docs/sorting-v0.md)
attributes to `Ord` dispatch. What is left, 3–4× by minimum on identical code, is the gap Phase 2
has to close, and the number its exit bound (§9, question 1) is set against.

## Not in this table

- The `uncharted-suns` galaxy frame: another repository, left for a run that builds it.
- Per-candidate port benches for Phase 3 (`crypto_base64_*`, `json_*`, string helpers): Phase 3
  cannot start before Phase 2's bound is met, so they wait.
- A quiet-machine run. Rerun `bench.sh` with nothing else running before stating a gate against
  these floors.
