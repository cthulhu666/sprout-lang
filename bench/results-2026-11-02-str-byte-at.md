# `str_byte_at` against probes and copies — 2026-11-02

`bench/str_byte_at/bench.sh run <rounds>`, after `bench.sh build` on the branch. The check
[string-byte-read-v0.md](../docs/string-byte-read-v0.md) §6 asks for before the builtin lands.
Baseline and candidate are rows of one source, built twice: `whole` (`just compile-native`, the
whole-program link) and `split` (separate units, as `just test` and the bootstrap link). A row
wins when its median ≤ the baseline's median × (1 + the baseline's spread).

Machine: Apple M3 Pro, Homebrew clang 23.1.1. Checksums agree within every pair in every round.

| Row | Baseline | Candidate | Unit |
|---|---|---|---|
| 1 | `probe_num`: `json.is_num_byte_at` by one-byte `str_starts_with_at_byte` probes | `byte_num`: one `str_byte_at`, integer compares | ns/byte, 64 KB |
| 2 | `copy_sum`: `bytes_from_utf8`, then a `bytes_get` loop | `byte_sum`: a `str_byte_at` loop | ns/byte, 64 KB |
| 3 | `c_strlen`: C `str_len` | `byte_strlen`: Sprout codepoint count over `str_byte_at` | ns/byte, 64 KB |
| 4 | `probe_parse_int`: prelude `parse_int` | `byte_parse_int`: the same over `str_byte_at` | ns/call |

## 15 rounds, load 5–6

| binary | row | baseline median | bound | candidate median | verdict |
|---|---|---:|---:|---:|---|
| whole | 1 | 15.456 | 16.533 | 0.791 | win |
| whole | 2 | 0.564 | 0.614 | 0.417 | win |
| whole | 3 | 0.404 | 0.425 | 0.589 | lose |
| whole | 4 | 151.423 | 155.140 | 36.641 | win |
| split | 1 | 58.802 | 60.300 | 1.909 | win |
| split | 2 | 1.336 | 1.465 | 1.385 | win |
| split | 3 | 0.505 | 0.547 | 2.164 | lose |
| split | 4 | 380.927 | 387.316 | 52.410 | win |

## 31 rounds, load 6–7

| binary | row | baseline median | bound | candidate median | verdict |
|---|---|---:|---:|---:|---|
| whole | 1 | 18.472 | 22.594 | 0.888 | win |
| whole | 2 | 0.752 | 0.999 | 0.496 | win |
| whole | 3 | 0.489 | 0.595 | 0.686 | lose |
| whole | 4 | 165.233 | 182.339 | 46.720 | win |
| split | 1 | 67.074 | 74.048 | 2.347 | win |
| split | 2 | 1.645 | 2.053 | 1.742 | win |
| split | 3 | 0.604 | 0.722 | 2.642 | lose |
| split | 4 | 418.978 | 458.114 | 65.981 | win |

Rows 1, 2 and 4 decide the builtin, and win in both setups in both runs. Under `split`, row 2's
median is 4–6% above the baseline's in both runs and passes on the spread: each read there is a C
call, against `bytes_get`'s. Row 3 prices §7 only: a Sprout `str_len` is 1.4–1.5× C under
`whole` and 4.3–4.4× under `split`.

## How the C body got there

The first body read the length through `sprout_cstr_byte_len` and took `s` through
`sprout_str_of_word`. Row 2 lost under both setups (`whole` 1.871 against 0.689, 15 rounds, load
14–17). Two causes, each shown by LLVM:

- **Not inlined.** `-Rpass-missed=inline` gave `str_byte_at_unboxed` cost 340 against a threshold
  of 225, with the null failure, the HDRCHECK branch and the `strlen` fallback inline. Moving them
  to a cold function let it inline; row 2 was then 0.832 against 0.559.
- **Not hoisted.** The cold function returned into the loop, so LLVM had to assume it writes
  memory, and reloaded the header and kind every byte. A cold function that never returns (null,
  or no CSTR header: both fail) leaves the loop with no such call, and row 2 won.
