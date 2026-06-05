# Sprout Bootstrap Chain

This document describes the reproducible stage pipeline used to build the
self-hosted Sprout compiler from source. Since the Python pipeline was removed
(2026-05-24), genesis is a committed LLVM IR seed rather than a hosted
compiler.

## Overview

```
bootstrap/compile_driver.ll           ← committed IR seed (text LLVM IR)
        │  just bootstrap-from-seed
        │  (opt --passes=verify   +   clang … runtime/sprout_runtime.c)
        ▼
build/compile_driver_bin_stage1       ← stage-1: first self-hosted binary
        │  just build-stage2
        │  (stage-1 --emit-ir   +   opt verify   +   clang)
        ▼
build/compile_driver_bin_stage2       ← stage-2: self-compiled binary
        │  just build-stage3
        ▼
build/compile_driver_bin_stage3       ← stage-3: fixed-point verification
                                        (byte-identical IR to stage-2)
```

The seed is platform-agnostic LLVM IR text. `clang` materializes the host
target at link time, so the same `bootstrap/compile_driver.ll` produces stage-1
binaries on every supported host (macOS arm64, Linux x86_64). The first line of
the seed carries a `; seed-fingerprint: <sha256>` comment recording the hash of
all `stdlib/compiler/*.sprout` sources at the time of the last `refresh-seed`.

## Stage Descriptions

### Genesis — IR seed

**File:** `bootstrap/compile_driver.ll` (checked in)

This is `compile_driver.sprout`'s LLVM IR produced by a previous stage-1
binary at the time the seed was refreshed. It carries no platform assumptions
beyond the LLVM target triple (which is overridden by clang at link time).

### Stage 1 — First self-hosted binary

**Command:** `just bootstrap-from-seed`

```
opt --passes=verify bootstrap/compile_driver.ll -o /dev/null
clang bootstrap/compile_driver.ll runtime/sprout_runtime.c -O2 \
    -o build/compile_driver_bin_stage1
```

Validates the IR then links it with the C runtime. No prior compiler binary is
required — only `clang` and `opt` on `PATH`. This is the only stage that
consumes the committed seed.

### Stage 2 — Self-compiled binary

**Command:** `just build-stage2`

```
build/compile_driver_bin_stage1 --emit-ir stdlib stdlib/compiler/compile_driver.sprout \
    > /tmp/stage2.ll
opt --passes=verify /tmp/stage2.ll -o /dev/null
clang /tmp/stage2.ll runtime/sprout_runtime.c -O2 \
    -o build/compile_driver_bin_stage2
```

Stage-1 compiles `compile_driver.sprout` end-to-end (bundle → typecheck →
lower → re-typecheck → codegen). The resulting IR must pass `opt verify` and
link cleanly.

### Stage 3 — Fixed-point verification

**Command:** `just build-stage3`

Stage-2 compiles `compile_driver.sprout` again. The output IR must be
byte-identical to stage-2's output. If it is, the compiler has reached a fixed
point: any further self-compilation produces the same artifact.

The fixed point was first confirmed on 2026-05-17 (M7) and is the contract
the seed must continue to satisfy.

## Refreshing the seed

After any compiler-source change that perturbs the emitted IR, regenerate
the committed seed:

**Command:** `just refresh-seed`

```
1. Build stage-1 from the existing (possibly stale) seed.
2. Loop until convergence (max 5 iterations):
   a. stage-N --emit-ir compile_driver.sprout > stageN+1.ll
   b. opt verify stageN+1.ll
   c. cmp prev stageN+1.ll — if equal, fixed point; else rebuild and continue.
3. Compute the seed-fingerprint over all stdlib/compiler/*.sprout sources.
4. Write the fingerprint comment + the fixed-point IR to bootstrap/compile_driver.ll.
```

Stage the updated `bootstrap/compile_driver.ll` in the same commit as the
compiler-source change. CI's `just verify-bootstrap-fixed-point` blocks any
commit where the seed diverges from current stage-1 output.

### When the seed predates a parser change (2-step bootstrap)

If the committed seed predates a syntax change in `parser.sprout`, `clang
bootstrap/compile_driver.ll` still produces a working stage-1 — but that
stage-1 cannot parse the new source. See AGENTS.md §"Bootstrap binary rebuild
protocol" for the temporary-revert sequence that breaks the catch-22.

## Verification gates

| Gate | Command | What it checks |
|------|---------|----------------|
| Instant freshness | `just seed-stale` | Compares stored `seed-fingerprint` against current `stdlib/compiler/*.sprout` hash. No compilation. |
| Full fixed point | `just verify-bootstrap-fixed-point` | Rebuilds stage-1 from the seed, re-emits IR for `compile_driver.sprout`, and `cmp`s against the seed body. |
| Bundle parity | `just test` runs `tests/stdlib/test_bundler.spr` | Bundle-phase output identical between rebuilt stages. |
| Stage-2 self-compile smoke | `just test` (when stage-2 present) | Stage-2 self-compile produces no `ERROR:` lines. |

A pre-commit hook (`scripts/seed_gate.sh`, wired as a PreToolUse Bash hook)
intercepts `git commit` and blocks if any `stdlib/compiler/*.sprout` or
`stdlib/*.sprout` is staged without a matching seed refresh. When the IR is
genuinely unchanged, bypass with `just verify-bootstrap-fixed-point` followed
by `just seed-fp-ack`.

## Artifact Formats

| Artifact | Format | Description |
|----------|--------|-------------|
| `bootstrap/compile_driver.ll` | LLVM IR text + fingerprint header | Committed seed; the trust root. |
| `build/compile_driver_bin_stage{1,2,3}` | ELF/Mach-O native binary | Self-hosted stages. |
| `runtime/sprout_runtime.c` | C source | GC runtime; linked by clang at each stage. |
| `/tmp/sprout_*.ll` (transient) | LLVM IR text | Emitted by stage-N during build-stageN+1; not persisted. |

## Trust Model

- **The seed** is trusted because (a) it has a `seed-fingerprint` line recording
  the source hash at the time it was generated; (b) every commit touching
  compiler sources updates the fingerprint via `refresh-seed`, gated by the
  seed-gate pre-commit hook; (c) `verify-bootstrap-fixed-point` in CI confirms
  the seed produces a stage-1 binary that re-emits the seed body byte-for-byte.
- **Stage-1** is trusted because it is the deterministic output of the
  trusted seed plus a single `clang` invocation.
- **Stage-2 and beyond** are trusted by the fixed-point property: if stage-N
  and stage-(N+1) produce byte-identical IR, then the compiler's behavior is
  stable and self-consistent.

The seed is the only artifact a maintainer needs to audit. Everything below
it is mechanical reproduction.

Release policy for distributing self-built binaries has not yet been decided.
