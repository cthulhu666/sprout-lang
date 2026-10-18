# Moving the runtime toward native Sprout, for speed (v0, 2026-10-17)

**Status:** proposal. No code change. Normative GC behaviour stays in
[compiler-internals.md](compiler-internals.md); the builtin policy stays in `AGENTS.md`
§Builtin vs Stdlib.

The question this answers: can moving C runtime code into Sprout make programs **faster**, and in
what order should it happen? Short answer: rarely by moving code, mostly by moving **knowledge**.
The fastest wins hand the C runtime what the compiler already knows. Moving code pays only where
Sprout knows something C cannot, and only after Sprout code stops being ~5× slower than C on the
same algorithm. Before that, a port is a slowdown.

## 1. Problem

The runtime is ~12.7k lines of C including headers (`sprout_runtime.c`, `sprout_scheduler.c`,
`sprout_poll.c`, `sprout_context.h`, `sprout_scheduler.h`) exporting 194 builtins
(`runtime/APPROVED_BUILTINS`).

**Where the compiler's time goes.** `--emit-ir stdlib/compiler/compile_driver.sprout`, IR at
`-O2`, per-TU link (not `link_whole_program.sh`), `sample` for 3 s, 2,460 samples, macOS arm64,
**one run**. Self time, grouped:

| bucket | samples | share |
|---|---:|---:|
| GC: `sprout_gc_collect_with_reason`, `sprout_heap_lookup`, `gc_mark_enqueue`, `slot_bytes`, alloc | 1,166 | ~47% |
| `Dict`: `bst_insert_node`, `map_get_unboxed`, `map_set`, `strcmp`, `intern_string` | 413 | ~17% |
| root stack: `sprout_gc_push_i64_root`, `sprout_gc_pop_roots` | 244 | ~10% |
| output I/O | 132 | ~5% |
| code emitted from Sprout source | ≤ ~250 | ≤ ~10% |

Re-measured 2026-10-18 over 5 whole runs: GC 36.7%, `Dict` 19.8%, output I/O 15.4% (not a
lever: full buffering leaves wall time unchanged), root stack 9.5%
([dict-representation-v0.md](dict-representation-v0.md) §1).

BACKLOG §GC & Runtime Performance warns that `static` runtime functions are inlined at `-O2` and
cannot be attributed. In this build they were not: `sprout_heap_lookup`, `slot_bytes` and
`bst_insert_node` each appear as their own symbol. Time inlined elsewhere still folds into its
caller, so the buckets are approximate. The compiler is the GC-heaviest workload known: outside it
GC is not the bottleneck ([gc-generational-v0.md](gc-generational-v0.md) §5.1).

**`-O2` buys little.** Same session, one run each: the compiler built from `-O0` IR self-compiles
in 4.19 s, from `-O2` IR in 3.40 s (−19%, runtime C at `-O2` in both). `clang -c -O2` on the
16.5 MB of emitted IR took 5.3 s against 3.6 s for the front end. Indicative only; Phase 0 repeats
it properly.

**Porting as-is would be slower.** On the same merge sort, Sprout is ~5× slower than C, cause
unknown ([sorting-v0.md](sorting-v0.md) §Where the time goes, item 3); the Phase 0 pair measures
3.2–4.4× by median, whole-program linked. Generic `Ord` dispatch costs another ~2× (item 1). A
builtin rewritten in today's Sprout inherits both.

## 2. Goals and non-goals

**Goals**

1. Hand the C runtime what the compiler knows, where it is cheap and measurable.
2. Make Sprout code fast enough that a pure-compute builtin can move without a regression.
3. Move the pieces where Sprout has an edge C cannot get: types, specialisation, per-program DCE.
4. Every step measured on more than one workload before and after.

**Non-goals**

- Zero C. The stack switch, OS calls, sockets, TLS (SecureTransport) and terminal stay in C.
- The GC, root stack or scheduler in Sprout. That needs an unsafe sublanguage (§5, Phase 4).
- Dropping LLVM. Set aside 2026-10-17: `-O2` buys ~19% (§1), and a new backend would cost far
  more than the compile time it saves.

## 3. What a Sprout implementation can have that C cannot

1. **Types.** C sees every value as `i64`. Sprout can specialise per dictionary: an `Int`-keyed
   map written `where Ord k` compares words, a sort on `Int` skips the `Ord` dictionary
   ([specialisation-v0.md](specialisation-v0.md)). Code that is generic without a class
   constraint gets no copy.
2. **Per-program DCE and inlining.** Sprout stdlib code is bundled and compiled with the program
   (`dce.elim_unreachable`), so it inlines into callers and unused code is dropped.

Limits on 2, both measured already:

- Inlining runtime code into the program costs compile time per binary whatever the source
  language: [cross-tu-inlining-v0.md](cross-tu-inlining-v0.md) §5.1 (771 ms of `clang -O2` per
  link for the C runtime).
- The root-stack win does **not** come from emitting push/pop as IR. It comes from the root
  context being an internal symbol, which lets LLVM prove root stores do not alias the heap
  ([gc-root-inline-lowering-v0.md](gc-root-inline-lowering-v0.md) §10: −6.1% exported, −38.7%
  internal). Internal is possible only if every reader of the root stack — the marker and the
  scheduler — is in the same module. So the root stack moves only with the GC, or is replaced
  (Phase 4); `link_whole_program.sh` already gets the win for long-running binaries.

**Field kinds are not on this list.** The compiler computes them (`field_kinds.sprout`) and
already passes them to the runtime: `sprout_register_ctor` stores them in `g_ctor_meta`, indexed
by tag. Only `sprout_debug_adt` and the `gc-profile` drain-edge counter read them. Using them in
the marker was Phase 1a, dropped once measured.

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
type layout, OCaml from a tag bit. How runtime bodies cross into callers (GHC unfoldings, Swift
`@inlinable`, Rust MIR): [cross-tu-inlining-v0.md](cross-tu-inlining-v0.md) §8.

**Stack roots.** LLVM's statepoints are "a set of extensions to LLVM to support garbage
collection": pointer locations "are provided in a separate section of the generated object file",
so the collector finds roots without a shadow stack. They are "still listed as 'experimental' to
indicate that no forward or backward compatibility guarantees are offered across versions". The
`gcroot` shadow stack, Sprout's model, is "mostly of historical interest" there but "still
supported" ([LLVM, Statepoints](https://llvm.org/docs/Statepoints.html)).

## 5. Plan

Ordered by the profile: the cheap C-side wins on the 47% GC bucket come first, open-ended compiler
work second. Each phase has an exit criterion; a phase that misses it stops the plan rather than
starting the next.

### Phase 0 — instruments (no behaviour change)

- **Workload set.** Self-compile, `nqueens`, `http_log_middleware`, `gc_roots`, and the
  `uncharted-suns` galaxy frame. Interleaved min-of-N, since single runs drift ~1.6× between
  sessions on this machine (BACKLOG §GC & Runtime Performance). The spread of N runs is each
  workload's **noise floor**; later gates are stated against it. Baseline:
  [bench/results-2026-10-18-native-runtime.md](../bench/results-2026-10-18-native-runtime.md),
  from `bench/native_runtime/bench.sh`: spreads 1.8–6.8%, without the galaxy frame. At load
  average 9.6 the same run spread 14–19%, so check `uptime` before trusting a floor.
- ~~**Drain-edge counter by field kind.**~~ Landed: `just gc-profile` prints a second line,
  `drain_edges by kind`, with edges and lookup hits per field kind, plus buckets for unregistered
  OBJs and non-OBJ objects. The 1a result below is its first reading.
- **Port bench.** One directory per Phase 3 candidate under `bench/`: the C builtin and a Sprout
  version on the same inputs. The merge-sort pair, Phase 2's exit metric, is in
  `bench/native_runtime/`: Int-specialised Sprout is 3.2× C at n=100k and 4.4× at n=440, by
  median; generic `mutvec_sort` 6.6× and 9.6×. Those runs sorted one input per n, every round;
  Phase 2 found what that hides. The Phase 3 candidates' benches wait for Phase 2.

Exit: a baseline table in `bench/results-<date>-native-runtime.md`.

### Phase 1 — give the C runtime what the compiler knows

- ~~**1a. Skip scalar fields when marking.**~~ **Dropped: it failed its gate (2026-10-17).** The
  plan was to read the tag from the OBJ header and skip `i`/`b` fields. Counter readings, `-O2`,
  macOS arm64:

  | workload | edges | `i`+`b` edges | lookup misses | misses come from |
  |---|---:|---:|---:|---|
  | self-compile | 52.98M | 113k (0.2%) | 14.71M | unregistered OBJ 13.25M, non-OBJ 0.95M |
  | `nqueens` | 5.35M | 0 | 5.11M | non-OBJ (vector slots) |
  | `gc_roots` | 1.35M | 0 | 0.85M | unregistered OBJ |
  | `astar` | 2.17M | 0 | 1.77M | non-OBJ (vector slots) |

  Misses are not only rare on `i`/`b`, they are free: `region_find` rejects a value outside the
  heap span with one inline compare, before any search. Two A/B runs, interleaved min-of-5:
  `nqueens` with vector slots not scanned at all (5.1M fewer lookups), 1.76 s against 1.76 s;
  self-compile with every missed lookup done twice (14.7M more), 3.10 s against 3.10 s. So no
  variant that removes misses pays: not 1a, not registering records, not typed vectors.
  What the profile's `sprout_heap_lookup` time buys is hits, which this did not measure.
- ~~**1b. `str_eq` reads the header.**~~ **Landed (2026-10-17).** The length fast-reject reads
  both CSTR headers at payload-8, as `sprout_cstr_byte_len` does, instead of two
  `sprout_heap_lookup` calls. Literals and interned strings are now rejected too. Self-compile
  makes 13.34M `str_eq` calls, 12.44M of them unequal: the old check rejected 1.73M, the new one
  9.16M, with no wrong reject. Interleaved 11-run A/B, user time, two sessions: medians
  3.01 → 2.95 s, 2.90 → 2.82 s (−2–3%). Other workloads were not timed; every path does less
  work than before. Verifying each reject under `SPROUT_GC_HDRCHECK=1` cost half the gain (2.84 s),
  so it is not done: the sweep and `str_byte_len` already assert header length = `strlen`. What
  HDRCHECK does check, off the hot path, is a String with no header at all: `tcp_net_err1` handed
  out raw C literals and `strerror()` text, which a stray `0x0A` at s-8 would have made compare
  unequal. It now copies them to managed strings.

Exit: each change kept only if self-compile improves by more than its noise floor and no workload
regresses past its own.

### Phase 2 — close the Sprout-vs-C gap

All of it is filed already; this plan only orders it. The first is what the exit below still
needs:

- ~~What is left of the merge step's cost.~~ Done 2026-10-18: the exit below is met. The 1.2×
  aim needs `insert` inlined (BACKLOG §Runtime and performance roadmap); see below.
- ~~The merge loop's branch, which C turns into a select.~~ Done 2026-10-18; see below.
- Root call operands only for runtime builtins, not Sprout callees (same section; needs a written
  GC-safety argument first).
- Non-escaping combinator closures allocate; every apply re-checks arity (same section).
- Tight string-processing loops (same section).
- Specialise generic functions at concrete types (same section;
  [specialisation-v0.md](specialisation-v0.md)). It brings generic code down to the
  Int-specialised level, so it does not move the exit metric; it matters for Phase 3 code written
  against `Ord`/`Eq`.

Exit: the Int-specialised half of the merge-sort pair (`bench/native_runtime/`) within **1.3× of
C by median, at both n=440 and n=100k**; 1.2× is the aim. Baseline: 4.4× and 3.2× (2026-10-18,
one input repeated). Until then, Phase 3 cannot start. The bound keeps Sprout's checks: element
access stays bounds-checked and arithmetic overflow-checked.

Where it stands: **1.26× at n=440, 1.19× at n=100k** (2026-10-18, median of 31), inside the
bound. At n=440 the margin is about one run-to-run spread. Four steps:

1. Two calls per element access, both removed. A match on `MutVec`, a type with one constructor,
   read the tag through `sprout_tag`, which LLVM can neither inline nor hoist. `vector_mutset`
   inlined its age-profile path and grew too big to inline itself. That read 1.18× and 1.76×.
2. The 1.18× was the bench's. Each round sorted the same input, and at n=440 (2000 rounds) the
   branch predictor learns it, so branchy code looked cheap: C's insertion sort, and Sprout's
   merge far more. With a fresh seed per round, the same code is 1.92× and 1.82×.
3. The merge's `if y < x` was a branch, which random input mispredicts about half the time; C
   compiles its ternary to `csel`. LLVM makes selects of one store and one tail call whose
   arguments carry the choice, not of two arms that each store and recurse: an arm's inlined
   bounds and overflow checks make it several blocks. `mutvec_sort`'s merge has that shape now.
   Generic `mutvec_sort` gains too: 6.87× and 6.55× to 6.35× and 5.82×.
4. Three things that kept LLVM from holding a store loop's loads in registers. The age-profile
   store hook's slow call could write any memory; it is now a build flag
   (`-DSPROUT_GC_AGEPROF_STORES`). A TCO loop saved and restored the stack even with no root
   slots to free, and LLVM treats `stackrestore` as a write; that pair now goes when there are
   none. A store into a vector slot shared a TBAA type with object fields and the vector
   header; `SproutSlot` and `SproutField` now separate them. Alone, only the hook moves the
   bench (1.40× to 1.30×); all three reach 1.26× and 1.19×.

What is left is the insertion sort over 16-element blocks, not the merge. Timed apart before
step 4, the merge phase was ~1.25× C and the insertion phase ~1.65×. LLVM does not inline
`insert` into `insertion` (cost 325 against a threshold of 225, after step 4), so each element
pays a call and the loop around it cannot keep its loads in registers. Forced by hand, the
inline reaches 1.14× and 1.11×; without step 4 it gains almost nothing, since each of the four
blocks a different optimisation. Instruction count was the wrong target: with every invariant
load hoisted out of the merge loop, two loads and a store per step as in C, it measured the same.

### Phase 3 — move builtins, one per PR

**3.1–3.3 are builtin-surface reduction, not speed.** None of them is in the profile, and each
will cost time until Phase 2 lands. Do them for the smaller C surface, or not yet.

1. Encoders off the compiler's path: `crypto_base64_*`, `json_*` (used by `lsp_driver`, outside
   the seed). No reseed.
2. String helpers: `split_words`, `str_split_lines` (both declared in the seed — `module_loader`
   calls `str_split_lines` — so each move reseeds), `string_join_newlines`, `regex_escape` (not in
   the seed).
3. Byte-level crypto (`crypto_sha256`, `crypto_hmac_sha256`), once byte access is unboxed.

**3.4 `Dict`: fix the representation, then decide where it lives.** Re-profiled over 5 runs:
`Dict` is 19.8% of a self-compile, and 63% of its 4.23M lookups miss. Neither a cheaper
comparison nor skipping matched prefixes moves wall time, and a hash map cannot keep the key
order callers rely on cheaply: the type environment answers ~770 prefix queries per compile over
7–11k changing keys. A (root, key) lookup memo skips 45% of lookups and gains nothing measurable.
All tree walks together are 8–10% of user time, which bounds any lookup fix. Measurements and
options per layer: [dict-representation-v0.md](dict-representation-v0.md). A Sprout port of the
same tree keeps its cost and adds Sprout's.

Each move: a differential test (C and Sprout agree on a corpus) lands first, then the Sprout
version, then the C function and its `APPROVED_BUILTINS` line are deleted. Gate: port bench at
parity or better, and no workload regresses past its noise floor.

### Phase 4 — decide, do not assume

Stop and look at what is still hot. Two directions remain, both language- or ABI-level, each with
its own design doc:

- **Precise stack maps instead of the root stack.** The collector reads root locations from LLVM
  statepoint stack maps, so no push or pop is emitted at all, and the GC stays in C. Costs: an
  experimental LLVM interface, a seed whose IR depends on it, and a rewrite of `ir_rooting`.
- **The GC or scheduler in Sprout.** Needs an unsafe sublanguage (raw loads/stores, unboxed words)
  and a compiler-checked no-allocation subset — Go's model above.

If what is hot is elsewhere, the plan ends here.

**Stays in C regardless:** `sprout_context.h` (stack switch), the poller, sockets and DNS, TLS,
terminal, process, fs, and the REPL/analysis plumbing. POSIX `regex.h` stays until there is a
reason to own a regex engine.

## 6. Impact on the language

Phases 0–3: none. Specialisation (Phase 2) is an optimisation with no surface change. Removing a
builtin keeps its stdlib name and type. Phase 4 would be a language or ABI change, out of scope
here.

## 7. Compatibility and migration

All Sprout code lives in four repos: this one, `uncharted-suns`, `repbit`, `sprout-pg`. Each Phase 3
move greps all four for the `extern fn` name before deleting it.

**Bootstrap.** A move of a builtin the compiler uses changes the seed and follows `AGENTS.md`
Definition of Done #9 and #12. For `Dict` and the string helpers the risk is sharper: a bug in the
Sprout version breaks the compiler that has to build its fix, which is the 2-step bootstrap case
([debugging.md](debugging.md) §2-Step Bootstrap Protocol). And the prelude is unconditional, so
every binary carries the port and every golden IR snapshot changes with it.

## 8. Tests

- Phase 0: the bench and counter are themselves the deliverable.
- Phase 3: one differential test per moved builtin; existing conformance tests unchanged.

## 9. Open questions

1. ~~Phase 2's exit bound.~~ Decided 2026-10-18: 20–30% slower than C is acceptable, written as
   the Phase 2 exit above (≤ 1.3× by median, aim 1.2×).
2. Whether `Dict` should gain non-`String` keys, or keep its current surface.
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
- LLVM: [Garbage Collection Safepoints in LLVM](https://llvm.org/docs/Statepoints.html)
