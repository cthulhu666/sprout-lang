# `char_to_str` in Sprout — 2026-11-01

`bench/char_to_str/bench.sh run <rounds>`, on `7a2f04b9`, after `bench.sh build baseline` on
master and `bench.sh build candidate` on the branch. The no-slower check
[runtime-memory-safety-v0.md](../docs/runtime-memory-safety-v0.md) stage 2 asks for before the
`char_to_str` builtin is deleted.

Machine: Apple M3 Pro, built with `just compile-native`. Load average 8–15 during both runs. One
row per UTF-8 length of the result, 10M calls each; 4 bytes is the slowest input.

Variants:
- `baseline` — the C builtin `char_to_str`, which `char_to_string` called.
- `candidate` — `char_to_string(char_from_codepoint(codepoint))`, with the encoder moved into
  `char_to_string`. LTO inlines `char_from_codepoint`, so the compiled `char_to_str` is its range
  check and a tail branch to `char_to_string`, which checks the range again.

31 rounds:

| row | unit | min | median | spread |
|---|---|---:|---:|---:|
| 1 baseline | ns/call | 9.8 | 10.3 | 5.1% |
| 1 candidate | ns/call | 9.9 | 10.5 | 6.1% |
| 2 baseline | ns/call | 10.1 | 10.4 | 3.0% |
| 2 candidate | ns/call | 10.2 | 10.6 | 3.9% |
| 3 baseline | ns/call | 11.7 | 12.3 | 5.1% |
| 3 candidate | ns/call | 11.7 | 12.2 | 4.3% |
| 4 baseline | ns/call | 12.2 | 12.9 | 5.7% |
| 4 candidate | ns/call | 12.1 | 12.9 | 6.6% |

15 rounds, run first:

| row | unit | min | median | spread |
|---|---|---:|---:|---:|
| 1 baseline | ns/call | 9.9 | 10.2 | 3.0% |
| 1 candidate | ns/call | 9.7 | 10.2 | 5.2% |
| 2 baseline | ns/call | 9.8 | 10.7 | 9.2% |
| 2 candidate | ns/call | 10.0 | 10.3 | 3.0% |
| 3 baseline | ns/call | 11.7 | 12.2 | 4.3% |
| 3 candidate | ns/call | 11.6 | 12.0 | 3.4% |
| 4 baseline | ns/call | 12.2 | 12.4 | 1.6% |
| 4 candidate | ns/call | 12.1 | 12.6 | 4.1% |

## Reading

- The 31-round run passes every row. The 15-round run passes rows 1–3 and misses row 4 by
  0.002 ns: 12.6 against 12.4 × 1.016 = 12.598.
- The real difference is the second range check, six instructions, well under a nanosecond. Both
  runs put it inside the noise; neither shows a cost a caller could see.
