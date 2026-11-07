# One-byte probes to `str_byte_at` in json, uuid and the prelude parsers — 2026-11-07

`bench/byte_probes/byte_probes_bench.sprout`, one source compiled by one compiler against two
stdlib trees: master's, which probes with `str_starts_with_at_byte`, and the branch's, which
reads with `str_byte_at`. Each tree is built twice: `whole` (`scripts/link_whole_program.sh`, the
whole-program link) and `split` (separate units, as `just test` and the bootstrap link). Rows
interleave across binaries. A row passes when its median ≤ master's median × (1 + master's spread).

Machine: Apple M3 Pro, Homebrew clang 23.1.1. 9 rounds, load 7. Checksums agree within every pair
in every round. The branch's IR calls `str_starts_with_at_byte` 5 times (the `true`/`false`/`null`
and `\u` probes, and `str_starts_with`), against 96 on master, and has no boxed `str_byte_at` call.

| link | row | unit | master median | spread | branch median | branch/master | verdict |
|---|---|---|---:|---:|---:|---:|---|
| whole | json_num | ns/byte | 78.584 | 4.0% | 38.909 | 0.50 | pass |
| whole | json_str | ns/byte | 20.145 | 6.6% | 14.468 | 0.72 | pass |
| whole | json_obj | ns/byte | 39.024 | 6.0% | 33.325 | 0.85 | pass |
| whole | parse_int | ns/call | 124.300 | 3.7% | 23.268 | 0.19 | pass |
| whole | parse_double | ns/call | 305.045 | 3.8% | 197.660 | 0.65 | pass |
| whole | uuid | ns/call | 993.660 | 4.5% | 981.620 | 0.99 | pass |
| split | json_num | ns/byte | 161.057 | 4.7% | 41.920 | 0.26 | pass |
| split | json_str | ns/byte | 36.785 | 4.9% | 16.011 | 0.44 | pass |
| split | json_obj | ns/byte | 65.385 | 6.3% | 36.471 | 0.56 | pass |
| split | parse_int | ns/call | 338.224 | 1.1% | 30.741 | 0.09 | pass |
| split | parse_double | ns/call | 557.215 | 3.1% | 235.745 | 0.42 | pass |
| split | uuid | ns/call | 1092.520 | 6.2% | 1050.480 | 0.96 | pass |

Every row passes under both links. `uuid` reads four bytes and then decodes hex, so the read is a
small share of a call and the row is flat. It was 1.07× master under `split` before `Just
<literal>` arms took the unboxed path (`docs/inline-byte-reads-v0.md` §4.3): each `| Just 45 ->`
read then allocated a `Maybe`.
