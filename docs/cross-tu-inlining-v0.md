# Cross-TU inlining: the Sprout ↔ runtime boundary is opaque (v0, 2026-09-20)

## 1. The finding

No C runtime function has ever been inlinable into Sprout-generated code. Not at `-O2`, not
under `-flto`, not with the whole program merged into a single LLVM module. The boundary has
always been a hard call, and the GC root push is the hottest thing sitting behind it.

The cause is a target-attribute mismatch, not an inlining heuristic. `ir_lowering` emits

```llvm
define i64 @vec_append(i64 %"p$value", i64 %"p$vec") {
```

with no attribute group at all, while clang gives every runtime function

```llvm
attributes #1 = { ... "target-cpu"="apple-m1" "target-features"="+aes,+neon,+v8.4a,..." }
```

LLVM's inliner only inlines a callee whose target features are a **subset** of the caller's —
otherwise the inlined body could contain instructions the caller's target does not have. Forty
features is not a subset of zero, so every call is refused. This is a correctness rule, which is
why no optimisation level or LTO flag gets past it.

## 2. Evidence

Call sites of `sprout_gc_push_i64_root` surviving in the final binary:

| build | `bench/gc_roots` | N-queens |
|---|---:|---:|
| `-O2` per TU (today) | 192 | 52 |
| `-O2 -flto` | 149 | 50 |
| `-O2 -flto -mcpu=generic -mtune=generic` | 149 | 50 |
| `-O2 -flto` + `__attribute__((always_inline))` on the push | 149 | — |
| `llvm-link` whole module + `opt -O2` | 192 | 52 |
| whole module, **runtime attributes stripped** | **0** | **0** |
| whole module, **Sprout functions given the runtime's group** | 9 | 0 |

Rows 5 and 6 are the same module and the same `opt -O2`; only the attributes differ. That
isolates the cause: remove the mismatch in either direction and the calls disappear.

`-mcpu=generic` looks like it should work and does not. On arm64-apple even generic carries
`+ete,+fp-armv8,+neon,+trbe,+v8a` — non-empty, so still not a subset of zero.

**`always_inline` is inert, and silently so.** Marking `sprout_gc_push_root` and
`sprout_gc_push_i64_root` `__attribute__((always_inline))` and building `-flto` produced 149 call
sites either way, binaries byte-identical at 77 224 bytes, and identical run times (1063 ms vs
1063 ms). LLVM declines rather than diagnosing. Rust, which has the same rule at a level where it
can be diagnosed, makes the equivalent combination a compile error instead — see 8.

**Counting note.** Every call-site figure in this document counts branch-and-link sites
(`bl … <sym>`), not lines of disassembly mentioning the symbol. The looser count reads two high in
every build — the function's own label line and its stub — which is where an earlier draft's
151-vs-149 and 194-vs-192 disagreements came from.

## 3. What it is worth, and how it decomposes

Interleaved, each program's own internal timer, **min of 12–20 rounds** (the machine is shared;
the least-disturbed run is the fastest one, so min is the statistic to read):

| workload | call (today) | fully inlined | change |
|---|---:|---:|---:|
| `bench/gc_roots` | 1063.7 ms | 652.5 ms | **−38.7%** |
| N-queens | 1728.5 ms | 1389.0 ms | **−20%** |

Answers identical to the baseline build. For scale: the root-stack array rewrite that landed the
same day was −37% on `bench/gc_roots`, so this is a second win of the same size.

**The win is concentrated in the root stack, not spread across the runtime.** The four root-stack
entry points — `sprout_gc_push_i64_root`, `push_ptr`, `push_scan` and `sprout_gc_pop_roots` —
share attribute group `#4` with about 50 other runtime functions, so the group itself must not be
edited. Instead `#4` is **cloned** as a new group with the target attributes removed, and only
those four `define`s are repointed at the clone; `#4` and its other members are left exactly as
clang emitted them.

| `bench/gc_roots` | push sites | pop sites | time | vs today |
|---|---:|---:|---:|---:|
| today | 192 | 68 | 1063.7 ms | — |
| **root stack alone inlinable** | 0 | 1 | **770.8 ms** | **−27.5%** |
| whole runtime inlinable | 0 | 1 | 652.5 ms | −38.7% |

**The root stack alone is 71% of the win.** That matches the call profile of the emitted IR: 156
pushes and 59 pops against roughly 110 calls to every other runtime function combined.

The isolation holds in the binary, which is what makes this a decomposition rather than a partial
strip. Branch-and-link sites for the other `#4` members, today → root-stack-only → whole-runtime:
`sprout_closure_arity_check` 18 → 14 → 0, `sprout_register_ctor` 14 → 14 → 0, `sprout_field`
29 → 29 → 0, `sprout_alloc_closure` 16 → 16 → 0. None of them is inlined in the middle column;
all of them are in the right-hand one. (The 18 → 14 is code motion after the pushes vanish, not
inlining — an inlined callee goes to 0, as the right-hand column shows.)

This decomposition is what decides between the two routes below. It was not available when this
document first recommended route A.

## 4. Why the compiler must not emit host target attributes

The obvious fix — teach `ir_lowering` to emit `"target-cpu"`/`"target-features"` — is wrong.
Emitted IR carries `target triple = "unknown-unknown-unknown"` deliberately, and
`bootstrap/compile_driver.ll` is a **committed** artifact that must build on macOS arm64, Linux
x86_64, Linux aarch64 and Windows. Baking the build host's feature set into it would break the
seed everywhere but the machine that refreshed it, and would make `just ir-golden-diff` report a
diff per developer machine.

## 5. Route A — strip the runtime's attributes and merge the module

Strip the attributes off the **runtime** side, and let the clang driver pick the CPU at codegen:

```sh
# once, cacheable: one .ll per runtime TU, attributes stripped, assembled and merged
for rt in runtime/*.c; do
  b=$(basename "$rt" .c)
  clang -O2 -emit-llvm -S "$rt" -o "$b.ll"
  sed -E 's/"target-(cpu|features|tune-cpu)"="[^"]*"//g' "$b.ll" > "$b.nf.ll"
  llvm-as "$b.nf.ll" -o "$b.bc"
done
llvm-link sprout_*.bc -o runtime.bc

# per binary
llvm-as <emitted>.ll -o prog.bc
llvm-link prog.bc runtime.bc -o merged.bc
clang merged.bc -O2 -o bin
```

It keeps the emitted IR and the seed target-neutral, so it touches neither the bootstrap nor the
golden corpus.

A separate `opt -O2` between `llvm-link` and `clang` is **not** needed — clang's own `-O2` over
the merged module produces the same result. Measured on one test binary: with the extra `opt`
step 1226 ms, without it 818 ms, both reaching 2 surviving push call sites, and on
`bench/gc_roots` 660.5 ms against 655.8 ms. The shorter pipeline is the one above.

### 5.1 Its real cost: the two link regimes

An earlier draft of this document claimed the route "links faster than today, 1.56 s vs 2.00 s".
That is true only against recipes that recompile all three runtime TUs per binary. But
`_test-stdlib` — the recipe behind `just test` — **already caches the runtime as `.o` files**
(`justfile:450-455`), and so do eight other recipes. Against that cache the route is far slower,
because whole-module inlining means codegenning the whole runtime into every binary:

| per-binary link (`tests/stdlib/test_let_else.spr`) | time |
|---|---:|
| cached `.o` link (today) | **119 ms** |
| `llvm-link` + `clang -O2` | **818 ms** |

Stage breakdown of the slow path: `llvm-as` 24 ms, `llvm-link` 47 ms, `clang -O2` 771 ms. The
771 ms is irreducible for this route.

### 5.2 What landed

`scripts/link_whole_program.sh`, wired into the two recipes that produce long-running binaries:
`compile-native` (what users and `uncharted-suns` build with) and `build-sproutd`. The runtime
bitcode is cached under `build/runtime-bc/` and rebuilt when any `runtime/*.c` or `*.h` is newer.

Verified through the recipe, `bench/gc_roots`: 192 → **0** surviving push call sites, 1081.1 ms →
669.4 ms (**−38.1%**), answers identical, binary 248 088 → 264 392 bytes (+6.6%).

Deliberately not wired: the 416-binary test path (see above), the `-O0` debug recipes,
`gc-profile` and `build-stage2-asan` (both change the C preprocessor or instrumentation, so they
cannot share a bitcode cache), and the compiler's own bootstrap — measured a wash at −11.8% per
invocation against +11.8 s of extra link per rebuild.

`just test` links **416 binaries**, at 5 parallel jobs on the measurement host — `scripts/test_jobs.sh`
derives the count from P-core count capped at 8, so a different machine scales this differently:
about 10 s of link today against about 68 s, buying nothing, because a test binary runs once for
milliseconds. The route therefore pays
only for a binary whose total lifetime run time exceeds roughly 1.8 s — `(818−119) ms / 0.38`.

The compiler itself does not clearly clear that bar. Linking the committed seed both ways:
4.2 s against 16.0 s, for `--emit-ir stdlib/compiler/infer.sprout` 4238 ms against 3886 ms
(−8.3%), emitted IR byte-identical. On four typical test files the mean is 385 ms against 340 ms
(−11.8%), which over 416 files at 5 jobs is about 3.8 s of wall clock saved per suite run against
11.8 s of extra link whenever the compiler is rebuilt.

### 5.3 On a real program: −20.7%, and it is not wired to one

`bench/gc_roots` is a microbenchmark, so every figure above is measured where the runtime is most
of the binary. Repeated on `uncharted-suns`, the only program anyone actually runs — its perft-4
suite, 197281 leaf nodes over a copying board, headless and allocation-heavy:

| | today | whole-program |
|---|---:|---:|
| `bl sprout_gc_push_i64_root` | 807 | **4** |
| `bl sprout_gc_pop_roots` | 392 | **1** |
| time (min of 3, interleaved) | 83.4 s | **66.1 s** (−20.7%) |
| binary | 309,720 B | 375,608 B (+21%) |
| build, warm cache | 1.06 s | 1.05 s |

Answers byte-identical. Three of those rows disagree with 5.1 and 5.2, and the microbenchmark is
the one that misleads: the speedup is roughly half and the size growth three times larger.

### 5.4 The link cost is not a constant — it scales with module size

The ~700 ms in 5.1 was read as a property of the technique. It is a property of
`bench/gc_roots`. Warm-cache link of the same runtime into three programs:

| program | emitted IR | link today | whole-program |
|---|---:|---:|---:|
| `bench/gc_roots` | small | ~119 ms | ~818 ms (+700 ms) |
| perft-4 suite | 9 183 lines | 1.06 s | 1.05 s (nil) |
| `uncharted-suns` `game/app.sprout` | 267 213 lines | **7 s** | **148 s (+141 s)** |

Whole-module `-O2` re-optimises and codegens the runtime *alongside the program's own IR*, so
the cost tracks the combined module, not the runtime. On a tiny program the runtime dominates
and the overhead looks like a constant; on a large one it is 21× the whole build.

The inlining still fires at that size — the game goes 39 139 → 883 root-push branches and
8 119 → 15 pops — so this is a cost question, not a capability one. The binary grows 2.4 → 4.3 MB
(+79%). `uncharted-suns` therefore takes it unconditionally on its perft gate and behind
`UNSUNS_WHOLE_PROGRAM=1` on `run` and `run-gfx` (its PR #387).

That also sharpens what a future default must not be: any `--link` mode (§`BACKLOG.md` decision)
that turns this on unconditionally would make a large program's build unusable.

## 6. The runtime's own codegen survives the strip

Stripping per-function features does not downgrade the runtime's own code. Runtime compiled
**alone** three ways, so that inlining differences cannot confound the comparison:

| build | LSE atomics | ll/sc atomics | SIMD ld/st |
|---|---:|---:|---:|
| A — today: `clang -O2 -c`, attributes present | 8 | 0 | 211 |
| B — stripped, `llc -mcpu=apple-m1` | 8 | 0 | 235 |
| C — stripped, `llc` with **no `-mcpu`** (control) | **0** | **16** | 238 |

LSE is the discriminating class: `+lse` sits above baseline `v8a`, so a backend that lost the
feature set must fall back to `ldxr`/`stxr` retry loops. Row C is the proof the probe can see that
happen. The runtime's atomics are real, in the async DNS resolver (`stdatomic.h`, a genuine OS
thread).

**An earlier draft concluded from row C that the final codegen step must carry `-mcpu`. That was
wrong.** Row C used `llc`, which takes the module's triple at face value; the actual pipeline ends
in the clang **driver**, which performs target selection itself. Measured on the merged module:

| `bench/gc_roots` | LSE | ll/sc | push sites | time |
|---|---:|---:|---:|---:|
| today | 8 | 0 | 192 | 1067.9 ms |
| merged, `clang -O2 -mcpu=apple-m1` | 8 | 0 | 0 | 661.2 ms |
| merged, `clang -O2`, **no `-mcpu`** | 8 | 0 | 0 | **660.7 ms** |

No explicit flag is needed, and adding one would be actively harmful: `release.yml` ships the
binary built by `just bootstrap-from-seed`, so `-mcpu=native` there would host-tune a released
artifact to the CI runner. Letting the driver decide leaves every platform with exactly the
default it has today — generic x86-64 on the Linux release build.

## 7. Route B — lower the root stack in `ir_lowering` (built, rejected)

Emit the push and pop as Sprout's own IR instead of a call into C. Given 3 this looked like ~71%
of the win at no build-time cost, on every binary and every platform. It was built and measured,
and it delivers **−6.1%**, not −27.5%.

The reason is that 3's decomposition cannot be reproduced outside a merged module. Route B needs
the root-context pointer exported so emitted IR can name it; exporting it is what destroys the
alias analysis that made the decomposition fast. Same code, same merge, visibility as the only
variable: −6.1% with the symbol external, −38.7% with it internalized.

So `static` on that global was load-bearing optimisation information, not an access-control
preference. Full numbers, the two other hypotheses that were tested and refuted, and the
implementation as built: `docs/gc-root-inline-lowering-v0.md` §10.

## 8. Prior art

How other implementations let runtime or stdlib code inline into generated code. Every row
verified against a primary source.

| implementation | what crosses the boundary | where it lives |
|---|---|---|
| GHC | Core "unfoldings" — the *inline* RHS, not the optimised one | `.hi` interface files; for `INLINE` "the inline-RHS (not the optimised RHS) is recorded in the interface file", and `INLINABLE` persists it "regardless of the size of the RHS" |
| Swift | serialized SIL for `@inlinable` functions | the `.swiftmodule` |
| Rust | MIR for `#[inline]` and generic functions, codegened into the **caller's** codegen unit | rlib metadata |
| Sprout today | nothing — the runtime is C, opaque to `ir_lowering` | — |

The pattern is unanimous: all three export the body **at their own IR level, above LLVM**. None
solves this by merging LLVM modules at link time. Route B is the analogue of that choice; route A
is the thing nobody does.

Rust independently hit the identical LLVM rule and surfaced it where it could be diagnosed: its
Reference states that functions marked `target_feature` "are not inlined into a context that does
not support the given features", and that "the `#[inline(always)]` attribute may not be used with
a `target_feature` attribute". C cannot diagnose it, which is why 2's `always_inline` row is a
silent no-op and why this sat undetected in Sprout for the project's whole life.

Swift's SE-0193 names the cost the other three share and Sprout does not yet pay: an exported body
becomes part of the binary interface, so a shipped inlined copy constrains future change.

Sources: [rustc dev guide, monomorphization](https://rustc-dev-guide.rust-lang.org/backend/monomorph.html) ·
[Rust Reference, codegen attributes](https://doc.rust-lang.org/reference/attributes/codegen.html) ·
[GHC User's Guide, pragmas](https://downloads.haskell.org/ghc/latest/docs/users_guide/exts/pragmas.html) ·
[Swift SE-0193](https://github.com/swiftlang/swift-evolution/blob/main/proposals/0193-cross-module-inlining-and-specialization.md)

## 9. What is still not verified

1. **Linux and Windows.** Measured on macOS arm64 only. The mechanism is target-independent, but
   the numbers are not.
2. **Binary size.** `bench/gc_roots`: 248 088 bytes today, 264 392 under route A, 77 224 under
   plain `-flto`. On perft the growth is 21%, not 6.6%, so it tracks the workload. Route B is
   unmeasured.
3. ~~Any workload that is not these two microbenchmarks.~~ Answered in 5.3: `uncharted-suns`
   perft-4 gives −20.7%, about half the microbenchmark figure. What is still open is that it
   does not use this path at all.
4. **Observable behaviour under whole-module optimisation.** Answers matched on two programs and
   the seed's emitted IR was byte-identical. That is not the full suite.

## 10. Relationship to the root-stack work

`bench/results-2026-09-20-gc-root-stack.md` records the array rewrite and notes that
`sprout_gc_push_i64_root` held ~31% of top-of-stack afterwards, with the per-push cost identified
as "the function-call boundary to the C runtime". This document explains why that boundary could
never be optimised away by any build flag, and 3 shows the root stack is where almost all of the
recoverable cost sits.
