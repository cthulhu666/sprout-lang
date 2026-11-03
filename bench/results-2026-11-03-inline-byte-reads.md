# Inline byte reads against master — 2026-11-03

The check [inline-byte-reads-v0.md](../docs/inline-byte-reads-v0.md) §5 stage 2 asks for: no slower
under the whole-program link. One source, `bench/str_byte_at/str_byte_at_bench.sprout`, compiled
by two compilers: master's (its seed, linked with `runtime/*.c` at `-O2`) and the branch's stage 1.
Each compiler's IR is linked twice: `whole` (`scripts/link_whole_program.sh`, as
`just compile-native`) and `split` (`clang <ir> runtime/*.c -O2`, as `just test` links). Each round
runs every row on all four binaries, interleaved. A row is no slower when the branch median ≤ the
master median × (1 + master's spread), spread being (median − min) / min.

The runtime and every non-compiler `stdlib/` file are identical on the two sides, so the only
difference is the emitted IR: master's has 6 `call i128 @bytes_get_unboxed` /
`@str_byte_at_unboxed`, the branch's none.

`bytes_sum` is the `bytes_get` loop alone, with the copy made before timing; `copy_sum` times the
copy too. `c_strlen` calls C and is the control.

Machine: Apple M3 Pro, Homebrew clang 23.1.1. Checksums agree within every pair.

## 21 rounds, load 11–13, ns/byte (parse_int: ns/call)

| link | row | master median | spread | bound | branch median | branch / master |
|---|---|---:|---:|---:|---:|---:|
| whole | copy_sum | 0.665 | 20.5% | 0.801 | 0.652 | 0.98 |
| whole | bytes_sum | 0.471 | 10.8% | 0.522 | 0.483 | 1.03 |
| whole | byte_sum | 0.471 | 12.4% | 0.529 | 0.489 | 1.04 |
| whole | byte_num | 0.964 | 20.0% | 1.157 | 0.822 | 0.85 |
| whole | byte_strlen | 0.633 | 12.6% | 0.713 | 0.627 | 0.99 |
| whole | byte_parse_int | 40.998 | 11.2% | 45.573 | 41.024 | 1.00 |
| whole | c_strlen | 0.460 | 13.3% | 0.521 | 0.466 | 1.01 |
| split | copy_sum | 1.498 | 14.4% | 1.714 | 0.634 | 0.42 |
| split | bytes_sum | 1.364 | 22.1% | 1.666 | 0.471 | 0.35 |
| split | byte_sum | 1.665 | 17.1% | 1.950 | 0.463 | 0.28 |
| split | byte_num | 2.176 | 12.6% | 2.451 | 0.750 | 0.34 |
| split | byte_strlen | 2.410 | 15.0% | 2.772 | 0.629 | 0.26 |
| split | byte_parse_int | 61.723 | 20.9% | 74.607 | 50.180 | 0.81 |
| split | c_strlen | 0.587 | 17.4% | 0.689 | 0.565 | 0.96 |

No row is slower. Under `whole` every row is within its bound: LLVM already inlined the C bodies
there, and `bytes_sum` and `byte_sum` sit 3–4% above master, inside an 11–12% spread. Under
`split` the four read loops (`bytes_sum`, `byte_sum`, `byte_num`, `byte_strlen`) run 2.9–3.8×
faster and match their `whole` numbers: the link setup no longer prices a byte read. `copy_sum`
gains 2.4× (the copy dilutes it), `byte_parse_int` 1.2×. A Sprout `str_len` (`byte_strlen`) is
1.3× C under `whole` and 1.1× under `split`, against 4.1× under `split` on master.
