# `bytes.builder_byte` in Sprout — 2026-10-31

`bench/bytes_builder_byte/bench.sh run 15`, on `cdd58634`, after `bench.sh build baseline` on
master and `bench.sh build candidate` on the branch. The no-slower check
[runtime-memory-safety-v0.md](../docs/runtime-memory-safety-v0.md) stage 2 asks for before the
`bytes_builder_byte` builtin is deleted.

Machine: Apple M3 Pro, built with `just compile-native`. Load average 5–6 during the run. Rows:
- `byte` — one `builder_byte` per iteration, 10M iterations.
- `chain` — 1024 `builder_byte` appended and built, per iteration, 10k iterations. The shape
  `hex` and `bigint` use; `builder_append` dominates it.

Variants:
- `baseline` — the C builtin `bytes_builder_byte`.
- `candidate` — `builder_bytes(singleton(value))`. LLVM inlines both wrappers, so the compiled
  `builder_byte` calls `bytes_singleton` and `bytes_builder_bytes` directly.

| row | unit | min | median | spread |
|---|---|---:|---:|---:|
| byte baseline | ns/iter | 41.4 | 44.5 | 7.5% |
| byte candidate | ns/iter | 40.7 | 44.9 | 10.3% |
| chain baseline | ns/iter | 309922.5 | 316923.4 | 2.3% |
| chain candidate | ns/iter | 310458.8 | 323364.6 | 4.2% |

## Reading

- Both rows pass: `byte` 44.9 ≤ 44.5 × 1.075, `chain` 323365 ≤ 316923 × 1.023 = 324213.
- The work is the same: one `Bytes` and one one-chunk `Builder` either way, and both root the
  `Bytes` while the `Builder` is allocated. The Sprout form makes two runtime calls where C made
  one.
- An earlier run of `bytes_builder_bytes(bytes_singleton(value))`, the same two calls, gave `byte`
  42.4 against 45.5 and `chain` 321539 against 321320. The two runs differ by more than either
  gap, so the rows measure noise around equal cost.
- Identical binaries differed by up to 18% in single smoke runs here, so one round decides
  nothing.
