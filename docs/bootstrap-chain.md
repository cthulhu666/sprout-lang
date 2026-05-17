# Sprout Bootstrap Chain

This document describes the reproducible stage pipeline from Python genesis to
self-hosted fixed-point. It is the normative reference for the bootstrap trust
chain described in Phase 11 of
[self-hosting-eliminate-python-backlog.md](self-hosting-eliminate-python-backlog.md).

## Overview

```
Python compiler (sprout/cli.py)
        │  just build-stage0
        ▼
compile_driver_bin          ← stage-0: Python-produced native binary
        │  just build-stage1
        ▼
compile_driver_bin_stage1   ← stage-1: self-hosted IR emission + clang link
        │  just build-stage2
        ▼
compile_driver_bin_stage2   ← stage-2: stage-1 self-compiles
        │  just build-stage3
        ▼
compile_driver_bin_stage3   ← stage-3: fixed-point verification
                              (byte-identical IR to stage-2; same binary size)
```

The fixed point was confirmed on 2026-05-17 (M7): stage-1 and stage-2 produce
byte-identical LLVM IR for `compile_driver.sprout` (8 912 435 bytes); all three
self-hosted binaries are 1 692 072 bytes.

## Stage Descriptions

### Stage 0 — Python genesis

**Command:** `just build-stage0`

```
python3 -m sprout.cli compile stdlib/compiler/compile_driver.sprout \
    --with-stdlib --native -o compile_driver_bin
```

Uses the Python compiler (`sprout/cli.py`) to compile `compile_driver.sprout`
end-to-end (parse → typecheck → lower → emit IR → link). This is the only step
that requires Python. The resulting binary (`compile_driver_bin`) is the seed
for all subsequent stages.

**When to rebuild stage-0:** Only when `stdlib/compiler/*.sprout` sources change
and you need the latest compiler code in the bootstrap chain. `just build-stage1`
prints a warning if compiler sources are newer than `compile_driver_bin`.

### Stage 1 — First self-hosted binary

**Command:** `just build-stage1`

```
./compile_driver_bin --emit-ir $(pwd)/stdlib stdlib/compiler/compile_driver.sprout \
    > /tmp/stage1.ll
clang /tmp/stage1.ll runtime/sprout_runtime.c -O2 [...] -o compile_driver_bin_stage1
```

`compile_driver_bin` (stage-0) emits LLVM IR for `compile_driver.sprout` using the
Sprout-native codegen in `stdlib/compiler/codegen.sprout`. The resulting IR is
linked with the C runtime (`runtime/sprout_runtime.c`). No Python is involved in
this step.

### Stage 2 — Self-compiled binary

**Command:** `just build-stage2`

```
./compile_driver_bin_stage1 --emit-ir $(pwd)/stdlib stdlib/compiler/compile_driver.sprout \
    > /tmp/stage2.ll
clang /tmp/stage2.ll runtime/sprout_runtime.c -O2 [...] -o compile_driver_bin_stage2
```

Stage-1 compiles itself. The output IR and binary are expected to be identical
to what stage-1 would produce (verified by `test_bootstrap_stage1.py`
`BootstrapStage3Tests`).

### Stage 3 — Fixed-point verification

**Command:** `just build-stage3`

Stage-2 compiles `compile_driver.sprout` again. The output must be byte-identical
to stage-2's output. If it is, the compiler has reached a fixed point: any
further self-compilation produces the same artifact.

## Artifact Formats

| Artifact | Format | Description |
|----------|--------|-------------|
| `compile_driver_bin` | ELF/Mach-O native binary | Python-produced stage-0 seed |
| `compile_driver_bin_stage{1,2,3}` | ELF/Mach-O native binary | Self-hosted stages |
| `runtime/sprout_runtime.c` | C source | GC runtime; linked by clang at each stage |
| LLVM IR (temporary) | LLVM IR text (`.ll`) | Intermediate artifact; not persisted |

## Verification

Each stage transition is verified by the test suite:

| Test | What it checks |
|------|---------------|
| `test_bootstrap_identity.py` | `--phase bundle` output identical between stage-0 and stage-1 across a stdlib corpus |
| `test_bootstrap_stage1.py` `BootstrapStage2Tests` | stage-1 binary typechecks the bootstrap corpus; output matches Python reference |
| `test_bootstrap_stage1.py` `BootstrapStage3Tests` | stage-2 binary typechecks the bootstrap corpus; output matches Python reference |
| `test_stage2_emit_ir.py` `Stage2SelfCompileTests` | stage-2 self-compiles `compile_driver.sprout` without errors (lightweight smoke gate) |

Run all verification: `mise exec -- just test`

## Updating the Runtime

If `sprout/cli.py`'s embedded C runtime template changes, regenerate the static
file before rebuilding:

```
just update-runtime
```

This is the only step that requires Python outside of `just build-stage0`.

## Trust Model

- **Stage-0** is trusted because it is produced by the auditable Python compiler
  in `sprout/cli.py`. Any engineer can inspect and re-run `just build-stage0` to
  reproduce it.
- **Stage-1** is trusted because it is produced by stage-0 from the same sources,
  and because `BootstrapStage2Tests` confirm behavioral parity with the Python
  reference.
- **Stage-2 and beyond** are trusted by the fixed-point property: if stage-N and
  stage-(N+1) produce byte-identical IR, then the compiler's behavior is stable
  and self-consistent.

Release policy for distributing self-built binaries has not yet been decided.
See Phase 11 of the self-hosting backlog.
