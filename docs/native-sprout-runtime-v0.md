# Moving the runtime toward native Sprout, for speed (v0, 2026-10-17)

**Status:** proposal. No code change. Normative GC behaviour stays in
[compiler-internals.md](compiler-internals.md); the builtin policy stays in `AGENTS.md`
§Builtin vs Stdlib.

The question this answers: can moving C runtime code into Sprout make programs **faster**, and in
what order should it happen? Short answer: yes, but only where Sprout knows something C cannot,
and only after Sprout code stops being ~5× slower than C on the same algorithm. Before that, a
port is a slowdown.

## 1. Problem

The runtime is ~12.7k lines of C (`sprout_runtime.c`, `sprout_scheduler.c`, `sprout_poll.c`,
`sprout_context.h`) exporting 194 builtins (`runtime/APPROVED_BUILTINS`).

**Where the compiler's time goes.** `--emit-ir stdlib/compiler/compile_driver.sprout`, IR at
`-O2`, per-TU link (not `link_whole_program.sh`), `sample` for 3 s, 2,460 samples, macOS arm64,
one run. Self time, grouped:

| bucket | samples | share |
|---|---:|---:|
| GC: `sprout_gc_collect_with_reason`, `sprout_heap_lookup`, `gc_mark_enqueue`, `slot_bytes`, alloc | 1,166 | ~47% |
| `Dict`: `bst_insert_node`, `map_get_unboxed`, `map_set`, `strcmp`, `intern_string` | 413 | ~17% |
| root stack: `sprout_gc_push_i64_root`, `sprout_gc_pop_roots` | 244 | ~10% |
| output I/O | 132 | ~5% |
| code emitted from Sprout source | ≤ ~250 | ≤ ~10% |

Static functions inlined at `-O2` fold into their callers, so the buckets are approximate (the
trap BACKLOG §GC & Runtime Performance records). The compiler is the GC-heaviest workload known:
outside it GC is not the bottleneck ([gc-generational-v0.md](gc-generational-v0.md) §5.1).

**`-O2` buys little.** The same compiler built from `-O0` IR runs the self-compile in 4.19 s
against 3.40 s at `-O2` (−19%, runtime C at `-O2` in both, one run each). LLVM optimises the
slice that is not runtime.

**Porting as-is would be slower.** On the same merge sort, Sprout is ~5× slower than C, cause
unknown ([sorting-v0.md](sorting-v0.md) §Where the time goes, item 3). Generic `Ord` dispatch
costs another ~2× (item 1). A builtin rewritten in today's Sprout inherits both.

## 2. Goals and non-goals

**Goals**

1. Make Sprout code fast enough that a pure-compute builtin can move without a regression.
2. Move the pieces where Sprout has an edge C cannot get: types, specialisation, per-program DCE.
3. Where code stays in C, hand it what the compiler knows (field kinds, string lengths).
4. Every step measured on more than one workload before and after.

**Non-goals**

- Zero C. The stack switch, OS calls, sockets, TLS (SecureTransport) and terminal stay in C.
- The GC, root stack or scheduler in Sprout. That needs an unsafe sublanguage (§5, Phase 4).
- Dropping LLVM. Set aside 2026-10-17: `clang -O2` is ~60% of self-compile time, but it buys
  ~19% of run time and costs nothing to keep; a new backend would cost far more.

## 3. What Sprout code can have that C cannot

1. **Types.** C sees every value as `i64`. Sprout can specialise per type: an `Int`-keyed map
   compares words, a sort on `Int` skips the `Ord` dictionary. `Dict` is `String`-keyed today
   because the C tree compares with `strcmp`.
2. **Field kinds.** `stdlib/compiler/field_kinds.sprout` knows which constructor fields are
   scalars. The C collector does not, so `sprout_gc_drain_marks` sends every field through
   `sprout_heap_lookup`, a binary search over regions.
3. **Per-program DCE and inlining.** Sprout stdlib code is bundled and compiled with the program,
   so it inlines into callers and unused code is dropped.

Limits on 3, both measured already:

- Inlining runtime code into the program costs compile time per binary whatever the source
  language: [cross-tu-inlining-v0.md](cross-tu-inlining-v0.md) §5.1 (771 ms of `clang -O2` per
  link for the C runtime).
- The root-stack win does **not** come from emitting push/pop as IR. It comes from the root
  context being an internal symbol, which lets LLVM prove root stores do not alias the heap
  ([gc-root-inline-lowering-v0.md](gc-root-inline-lowering-v0.md) §10: −6.1% exported, −38.7%
  internal). Internal is possible only if every reader of the root stack — the marker and the
  scheduler — is in the same module. So the root stack moves only with the GC, or not at all;
  `link_whole_program.sh` already gets the win for long-running binaries.

What Sprout code lacks today: unboxed byte access (`bytes_get` returns `Maybe Int`), raw memory,
and a checked guarantee that a function does not allocate.

## 4. Prior art

Where comparable languages draw the line between runtime and stdlib. Each row checked against
the project's own source tree or headers.

| language | runtime (GC, scheduler) in | stdlib in | how the GC knows pointers |
|---|---|---|---|
| Go | Go: `src/runtime` has 584 `.go` and 183 `.s` top-level files | Go | type descriptor `GCData`: "a bitmask that describes the ptr/nonptr fields of the type" (`internal/abi/type.go`) |
| Swift | C++: `stdlib/public/runtime` has 58 `.cpp` files | Swift: `stdlib/public/core` has 232 `.swift` files | n/a (reference counting) |
| GHC | C and Cmm: `rts/` has 77 `.c` and 14 `.cmm` top-level files | Haskell | info tables: `ptrs` / `nptrs` counts and layout bitmaps (`rts/include/rts/storage/InfoTables.h`) |
| OCaml | C: `runtime/` has 69 `.c` and 5 `.S` files | OCaml | tag bit: `Is_long(x)` is `((x) & 1) != 0` (`runtime/caml/mlvalues.h`) |

Go is the only one with its collector in the host language, and it pays for it with compiler
directives that ordinary Go never uses. Its `runtime/HACKING.md`: `go:nowritebarrierrec` "directs
the compiler to emit an error if the following function or any function it calls recursively …
contains a write barrier"; `go:systemstack` "indicates that a function must run on the system
stack"; and `panic` during `mallocgc` "will cause an immediate fatal error". The other three keep
the runtime in C/C++ and grow the stdlib in the language. Sprout is in that second group; this plan
moves its line, it does not remove it.

Every tracing GC in the table tells a pointer from a scalar without a heap lookup: Go and GHC from
type layout, OCaml from a tag bit. How runtime bodies cross into callers (GHC unfoldings, Swift `@inlinable`, Rust MIR):
[cross-tu-inlining-v0.md](cross-tu-inlining-v0.md) §8.

## 5. Plan

Each phase has an exit criterion. A phase that misses it stops the plan rather than starting the
next.

### Phase 0 — instruments (no behaviour change)

- **Port bench.** One directory per candidate under `bench/`, the C builtin and a Sprout version
  on the same inputs, interleaved min-of-N (single runs drift ~1.6× between sessions on this
  machine, BACKLOG §GC & Runtime Performance).
- **Workload set.** Self-compile, `nqueens`, `http_log_middleware`, `gc_roots`, and the
  `uncharted-suns` galaxy frame. A move must not regress any of them.
- **Drain-edge counter by field kind.** How many marked edges land on `i`/`b` fields, and how many
  `sprout_heap_lookup` calls return NULL. It sizes Phase 2a before it is built.

Exit: a baseline table in `bench/results-<date>-native-runtime.md`.

### Phase 1 — close the Sprout-vs-C gap

All of it is filed already; this plan only orders it:

- Specialise generic functions at concrete types (BACKLOG §Runtime and performance roadmap).
- Root call operands only for runtime builtins, not Sprout callees (same section; needs a written
  GC-safety argument first).
- Non-escaping combinator closures allocate; every apply re-checks arity (same section).
- Profile the ~5× `MutVec` gap, then inline element access if access is the cost (same section).
- Tight string-processing loops (same section).

Exit: the merge sort pair from [sorting-v0.md](sorting-v0.md) within an agreed bound of C (§9,
question 1).

### Phase 2 — give the C runtime what the compiler knows

- **2a. Skip scalar fields when marking.** Store a per-constructor mask of `i`/`b` fields. The OBJ
  header's `aux` is `(tag << 8) | arity` with 42 tag bits, so small arities fit there; larger
  ones use a table by tag. Only scalars are skipped; every other field is still looked up, so a
  wrong kind costs a lookup, never the heap. `Double` is classified `p` today (any capitalised type
  name) and needs its own kind to be skipped. Trusting `p` fields without a lookup is a separate,
  riskier step: string literals live outside the heap, and a wrong kind becomes a use-after-free.
  Gate: Phase 0's counter shows enough scalar edges to matter.
- **2b. Static string literals get a length header** (BACKLOG §Strings, filed). Every string then
  has a header, so `str_eq`'s length fast-reject no longer asks `sprout_heap_lookup` whether one
  exists.

Exit: measured on the workload set; kept only where it wins.

### Phase 3 — move pure-compute builtins, one per PR

Order, least risky first:

1. Encoders off the compiler's path: `crypto_base64_*`, hex, `json_*`. A move here does not change
   the seed.
2. String helpers: `split_words`, `str_split_lines`, `string_join_newlines`, `regex_escape`. These
   are on the compiler's path (`module_loader` calls `str_split_lines`), so each move reseeds.
3. Byte-level crypto (`crypto_sha256`, `crypto_hmac_sha256`) — only once byte access is unboxed.
4. **`Dict` as a Sprout persistent map, generic in its key.** The largest prize (17% of the
   compiler) and the largest risk: the compiler uses it everywhere, it changes the seed, and
   `dict_keys` order (sorted, from the in-order walk) must not change. Non-`String` keys are a
   semantic change and get their own design doc.

Each move: a differential test (C and Sprout agree on a corpus) lands first, then the Sprout
version, then the C function and its `APPROVED_BUILTINS` line are deleted. Gate: port bench at
parity or better, and no workload regresses.

### Phase 4 — decide, do not assume

Stop and look at what is still hot. If it is the GC, root stack or scheduler, moving them needs an
unsafe sublanguage (raw loads/stores, unboxed words) and a compiler-checked no-allocation subset —
Go's model above. That is a language change and gets its own design doc. If what is hot is
elsewhere, the plan ends here.

**Stays in C regardless:** `sprout_context.h` (stack switch), the poller, sockets and DNS, TLS,
terminal, process, fs, and the REPL/analysis plumbing. POSIX `regex.h` stays until there is a
reason to own a regex engine.

## 6. Impact on the language

Phases 0–3: none. Specialisation (Phase 1) is an optimisation with no surface change. Removing a
builtin keeps its stdlib name and type. Phase 4 would be a language change, out of scope here.

## 7. Compatibility and migration

All Sprout code lives in four repos: this one, `uncharted-suns`, `repbit`, `sprout-pg`. Each Phase 3
move greps all four for the `extern fn` name before deleting it. A move of a builtin the compiler
uses changes the seed and follows `AGENTS.md` Definition of Done #9 and #12.

## 8. Tests

- Phase 0: the port bench and counter are themselves the deliverable.
- Phase 2a: a GC test that names the collection count it must exceed (the lesson in
  [gc-root-inline-lowering-v0.md](gc-root-inline-lowering-v0.md) §10), with scalar and pointer
  fields in one constructor.
- Phase 3: one differential test per moved builtin; existing conformance tests unchanged.

## 9. Open questions

1. Phase 1's exit bound: how close to C must the merge-sort pair get before Phase 3 starts?
2. Whether `Dict` should gain non-`String` keys (Phase 3.4) or keep its current surface.
3. Whether the Phase 0 workload set is the right one.

## 10. Sources

- Go: [runtime/HACKING.md](https://github.com/golang/go/blob/master/src/runtime/HACKING.md),
  [internal/abi/type.go](https://github.com/golang/go/blob/master/src/internal/abi/type.go)
- Swift: [docs/Runtime.md](https://github.com/swiftlang/swift/blob/main/docs/Runtime.md) — the
  runtime provides "memory management, including allocation and reference counting; the runtime
  type system"; [stdlib/public/runtime](https://github.com/swiftlang/swift/tree/main/stdlib/public/runtime)
- GHC: [rts/include/rts/storage/InfoTables.h](https://github.com/ghc/ghc/blob/master/rts/include/rts/storage/InfoTables.h)
- OCaml: [runtime/caml/mlvalues.h](https://github.com/ocaml/ocaml/blob/trunk/runtime/caml/mlvalues.h),
  [manual, Interfacing C with OCaml](https://ocaml.org/manual/5.2/intfc.html)
