# The empty constructors in Sprout — 2026-11-01

`bench/empties/bench.sh run <rounds>`, after `bench.sh build baseline` on master (`de20d94a`) and
`bench.sh build candidate` on the branch. The no-slower check
[runtime-memory-safety-v0.md](../docs/runtime-memory-safety-v0.md) stage 2 asks for before a
builtin is deleted.

Machine: Apple M3 Pro, built with `just compile-native`. Load average 4–11 during the runs.

Rows: `vec` (`vec_empty()`), `mutvec` (`mutvec_empty()`), `bytes` (`bytes.empty()`), `builder`
(`bytes.builder_empty()` built to Bytes). One call per iteration, result consumed.

## `bytes_empty` and `bytes_builder_empty` move

`bytes.empty()` and `bytes.builder_empty()` return one shared top-level value each
(`bytes_from_utf8("")`, and `bytes_builder_bytes` of it): no extern mutates a Bytes or a Builder in
place. The values are built once at startup and registered as GC roots. `vector_empty` stays C.

31 rounds:

| row | unit | min | median | spread |
|---|---|---:|---:|---:|
| builder baseline | ns/call | 22.0 | 23.1 | 5.0% |
| builder candidate | ns/call | 13.4 | 14.4 | 7.5% |
| bytes baseline | ns/call | 9.4 | 10.0 | 6.4% |
| bytes candidate | ns/call | 2.3 | 2.8 | 21.7% |
| mutvec baseline | ns/call | 18.7 | 19.5 | 4.3% |
| mutvec candidate | ns/call | 19.0 | 19.4 | 2.1% |
| vec baseline | ns/call | 9.4 | 9.9 | 5.3% |
| vec candidate | ns/call | 9.4 | 9.8 | 4.3% |

## `vector_empty` stays

It must be fresh per call (MutVec and LinVec mutate their vector in place), and a shared
`Vector a` is rejected by the value restriction anyway. The fresh Sprout form,
`vector_from_list([])`, was measured with the two moves above, 31 rounds:

| row | unit | min | median | spread |
|---|---|---:|---:|---:|
| mutvec baseline | ns/call | 18.9 | 19.5 | 3.2% |
| mutvec candidate | ns/call | 22.5 | 23.4 | 4.0% |
| vec baseline | ns/call | 9.2 | 10.0 | 8.7% |
| vec candidate | ns/call | 13.0 | 13.7 | 5.4% |

## Reading

- The shared values pass with margin: `bytes` 3.6× and `builder` 1.6× faster, since a call no
  longer allocates. `vec` and `mutvec` match master, as they should with `vector_empty` unchanged.
- `vector_from_list([])` fails by ~3.7 ns a call (+37% `vec`, +20% `mutvec`). `[]` lowers to
  `sprout_alloc_obj(tag, 0)`, a `find_ctor` lookup for the `Nil` singleton, plus a root push and
  `vector_from_list`'s own count pass and rooting, against one allocation in C. So `vector_empty`
  stays, with this as its recorded reason.
