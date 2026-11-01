# `str_starts_with` in Sprout — 2026-11-01

`bench/str_starts_with/bench.sh run <rounds>`, after `bench.sh build baseline` on master
(`45521571`) and `bench.sh build candidate` on the branch. The no-slower check
[runtime-memory-safety-v0.md](../docs/runtime-memory-safety-v0.md) stage 2 asks for before the
`str_starts_with` builtin is deleted.

Machine: Apple M3 Pro, built with `just compile-native`. Load average 7–15 during the runs.

Variants:
- `baseline` — the C builtin `str_starts_with`.
- `candidate` — `str_starts_with_at_byte(s, 0, prefix)`, in Sprout, with `str_starts_with_at_byte`
  in `ir_rooting.is_nonallocating_read`. The wrapper roots nothing and LTO inlines it: the
  candidate binary has no call to `_str_starts_with`.

Rows: `hit` ("hello world" / "hello"), `miss` (fails at the first byte), `long` (an 8192-byte
string against its first 4096 bytes, the slowest input), `empty` (prefix ""), and `selfcompile`
(each tree's stage-1 compiler emitting IR for `stdlib/compiler/compile_driver.sprout`).

31 rounds:

| row | unit | min | median | spread |
|---|---|---:|---:|---:|
| empty baseline | ns/call | 2.1 | 2.2 | 4.8% |
| empty candidate | ns/call | 2.1 | 2.2 | 4.8% |
| hit baseline | ns/call | 3.9 | 4.0 | 2.6% |
| hit candidate | ns/call | 3.9 | 4.0 | 2.6% |
| long baseline | ns/call | 102.1 | 106.0 | 3.8% |
| long candidate | ns/call | 101.9 | 105.6 | 3.6% |
| miss baseline | ns/call | 1.6 | 1.6 | 0.0% |
| miss candidate | ns/call | 1.6 | 1.6 | 0.0% |
| selfcompile baseline | ms/run | 3763.0 | 3944.9 | 4.8% |
| selfcompile candidate | ms/run | 3715.2 | 3919.8 | 5.5% |

15 rounds, without the `is_nonallocating_read` entry:

| row | unit | min | median | spread |
|---|---|---:|---:|---:|
| empty baseline | ns/call | 2.1 | 2.2 | 4.8% |
| empty candidate | ns/call | 3.0 | 3.1 | 3.3% |
| hit baseline | ns/call | 4.0 | 4.0 | 0.0% |
| hit candidate | ns/call | 5.0 | 5.1 | 2.0% |
| long baseline | ns/call | 103.9 | 107.5 | 3.5% |
| long candidate | ns/call | 121.1 | 125.9 | 4.0% |
| miss baseline | ns/call | 1.6 | 1.7 | 6.2% |
| miss candidate | ns/call | 3.0 | 3.1 | 3.3% |
| selfcompile baseline | ms/run | 3850.5 | 4157.1 | 8.0% |
| selfcompile candidate | ms/run | 3866.5 | 4107.3 | 6.2% |

## Reading

- With the entry, every row passes; medians are equal or lower.
- Without it, every micro row fails (+17% to +82%). The wrapper pushed and popped a GC root for
  each param around a call that cannot collect, and the root calls kept LTO from inlining it. The
  same IR with the root calls removed by hand matched master on every row.
- The entry also drops the roots around every other `str_starts_with_at_byte` call: 10 of the 75
  golden IR files push fewer roots, none more.
