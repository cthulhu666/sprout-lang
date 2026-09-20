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
| `llvm-link` whole module + `opt -O2` | 192 | 52 |
| whole module, **runtime attributes stripped** | **0** | **0** |
| whole module, **Sprout functions given the runtime's group** | 9 | 0 |

The last two rows are the same module and the same `opt -O2`; only the attributes differ. That
isolates the cause: remove the mismatch in either direction and the calls disappear.

`-mcpu=generic` looks like it should work and does not. On arm64-apple even generic carries
`+ete,+fp-armv8,+neon,+trbe,+v8a` — non-empty, so still not a subset of zero.

## 3. What it is worth

Interleaved, each program's own internal timer, **min of 20 rounds** (the machine was shared with
other sessions; the least-disturbed run is the fastest one, so min is the statistic to read):

| workload | call (today) | inlined | change |
|---|---:|---:|---:|
| `bench/gc_roots` | 1069.5 ms | 663.5 ms | **−38%** |
| N-queens | 1728.5 ms | 1389.0 ms | **−20%** |

Both produce answers identical to the baseline build. For scale: the root-stack array rewrite that
landed the same day was −37% on `bench/gc_roots`, so this is a second win of the same size.

**This is whole-program optimisation, not the root push alone.** Unblocking the attribute mismatch
unblocks inlining for *every* runtime call, and no attempt has been made to decompose the win. Do
not quote it as the cost of the root push.

## 4. Why the compiler must not emit host target attributes

The obvious fix — teach `ir_lowering` to emit `"target-cpu"`/`"target-features"` — is wrong.
Emitted IR carries `target triple = "unknown-unknown-unknown"` deliberately, and
`bootstrap/compile_driver.ll` is a **committed** artifact that must build on macOS arm64, Linux
x86_64, Linux aarch64 and Windows. Baking the build host's feature set into it would break the
seed everywhere but the machine that refreshed it, and would make `just ir-golden-diff` report a
diff per developer machine.

## 5. Recommended fix: a build-pipeline change, no compiler change

Strip the attributes off the **runtime** side instead, and hand the CPU to the backend at codegen —
a function with no `target-cpu` attribute inherits the target machine's, so host-specific codegen
survives:

```
clang -O2 -emit-llvm -S runtime/*.c            # once, cacheable
sed -E 's/"target-(cpu|features)"="[^"]*"//g'  # strip on the runtime side only
llvm-link <emitted>.ll runtime_*.ll -o merged.bc
opt -O2 merged.bc -o opt.bc
clang opt.bc -O2 -mcpu=<host> -o bin
```

It keeps the emitted IR and the seed target-neutral, so it touches neither the bootstrap nor the
golden corpus. The `-mcpu` on the last line is load-bearing, not decoration — see 6.

**It is also faster to link than today**: 1.56 s vs 2.00 s per binary, because the runtime bitcode
is built once instead of recompiling all three runtime TUs for every binary. `just test` links
hundreds of binaries, so a slow link would have been the thing that killed this; it is an
improvement instead.

## 6. The runtime's own codegen survives the strip — if `-mcpu` is passed

The obvious objection to §5 is that stripping per-function features downgrades the runtime's own
code. It does not, provided the CPU reaches the backend. Runtime compiled **alone** three ways, so
that inlining differences cannot confound the comparison:

| build | LSE atomics | ll/sc atomics | SIMD ld/st |
|---|---:|---:|---:|
| A — today: `clang -O2 -c`, attributes present | 8 | 0 | 211 |
| B — stripped, `llc -mcpu=apple-m1` | 8 | 0 | 235 |
| C — stripped, **no `-mcpu`** (control) | **0** | **16** | 238 |

LSE is the discriminating class: `+lse` sits above baseline `v8a`, so a backend that lost the
feature set must fall back to `ldxr`/`stxr` retry loops. Row C is the proof the probe can see that
happen — it is why rows A and B agreeing is evidence rather than a probe that measures nothing.
The runtime's atomics are real, in the async DNS resolver (`stdatomic.h`, a genuine OS thread).

So the strip route's requirement is exact: **the final codegen step must carry `-mcpu`.** Today
each TU carries its own features and cannot be silently downgraded; after the strip, dropping that
flag turns eight lock-free atomics into sixteen retry-loop instructions with nothing to warn you.
That belongs in the justfile recipe, not in a comment.

Total instruction count rises 35 877 → 36 847 (+2.7%) from A to B, which is `opt -O2` running a
second time over already-optimised IR, not a regression in quality.

## 7. What is still not verified

1. **Linux and Windows.** Measured on macOS arm64 only. The release workflow builds linux x86_64
   and aarch64; each needs the same pipeline and its own measurement.
2. **The `-O0` debug link path** and `just test`'s link path are untouched by these experiments.
3. **Binary size.** The `-flto` build was 77 KB against 248 KB; the strip route is unmeasured.
4. **Any workload that is not these two microbenchmarks.** The compiler itself is unmeasured here,
   and so is `uncharted-suns`, which is the only real user and the workload that prompted the GC
   work in the first place.
5. **Observable behaviour under whole-module `opt -O2`.** Answers matched on two programs. That is
   two programs, not a gate — the full suite has not been run against such a build.

## 8. Relationship to the root-stack work


`bench/results-2026-09-20-gc-root-stack.md` records the array rewrite and notes that
`sprout_gc_push_i64_root` held ~31% of top-of-stack afterwards, with the per-push cost identified
as "the function-call boundary to the C runtime". This document explains why that boundary could
never be optimised away, and shows that the fix is in the build pipeline rather than in codegen.
