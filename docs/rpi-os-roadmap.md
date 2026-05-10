# Sprout on Bare Metal — Raspberry Pi OS Roadmap

## Overview

This document describes what is needed to write a rudimentary operating system for Raspberry Pi in Sprout, and a phased plan to get there.

All language and codegen work targets the **self-hosted compiler** (`stdlib/compiler/`), not the Python bootstrap. The self-hosted compiler emits LLVM IR; clang and the build system handle linking.

End state: a developer can write an RPi3 kernel in Sprout, compile it with the self-hosted compiler, and boot it in QEMU to see "Hello, World" over UART.

---

## Gap Analysis

Writing an OS means running as the first code on bare hardware — no OS beneath, no C library, no heap management. Sprout currently assumes all of these.

| Gap | Current state | What's needed |
|---|---|---|
| ARM target | Triple hardcoded as `"unknown-unknown-unknown"` in `codegen.sprout:3070` | `--target` flag, AArch64 data layout |
| `extern fn` syntax | Not in language; all C bindings are hardcoded in the compiler | New keyword, AST node, parser, typechecker, codegen support |
| Bare-metal C runtime | OS runtime has `malloc`, `<unistd.h>`, `<sys/*>`, GC | New `runtime/bare_metal.c` — bump allocator, no OS headers |
| `--bare-metal` codegen mode | Entry is `main(argc, argv)`, GC calls emitted unconditionally | Entry renamed `_start_sprout`, no GC calls, no `argc/argv` |
| MMIO / volatile memory access | No raw pointer type, no volatile IR emission | `raw_mmio_read32` / `raw_mmio_write32` builtins emitting LLVM `volatile load/store` |
| Freestanding stdlib | All stdlib assumes OS (net, terminal, crypto, HTTP) | `stdlib/kernel/` — mmio, uart, gpio; `prelude_kernel.sprout` |
| Boot assembly + linker script | None | `boot.S` (halt secondary cores, zero BSS, set SP, jump to `_start_sprout`), `link.ld` |

---

## Phase 1 — AArch64 Cross-Compilation Target

**Goal:** Self-hosted compiler accepts `--target <triple>` and emits the correct LLVM triple and AArch64 data layout.

**Changes:**

- `stdlib/compiler/codegen.sprout:3070` — parameterise `compile_to_ir` to accept `target: Maybe String`; emit `target datalayout = "e-m:e-i8:8:32-i16:16:32-i64:64-i128:128-n32:64-S128"` and the correct triple when set; default to `"unknown-unknown-unknown"` when absent
- `stdlib/compiler/compile_driver.sprout` — parse `--target <triple>` from argv, thread to `compile_to_ir`
- `justfile` — add `build-rpi` recipe that invokes the compiler with `--target aarch64-unknown-none` and pipes IR to `clang --target aarch64-unknown-none`

**Done check:** `file kernel.elf` reports `ARM aarch64`. All existing tests still pass.

**Risk:** Low. One guarded branch in codegen.

---

## Phase 2 — `extern fn` Source Language Feature

**Goal:** Sprout source can declare external C/assembly functions with their types.

```sprout
extern fn uart_send(c: Int) -> Unit
```

Resolves to a `declare i64 @uart_send(i64)` in LLVM IR. Implicitly carries `IO` effect. Must be monomorphic (all param types explicit, no polymorphism).

**Changes:**

- `stdlib/compiler/lexer.sprout:39–54` — add `"extern"` to `is_keyword`
- `stdlib/compiler/ast.sprout:105–112` — add `ExternDecl String (List Param) TypeExpr SourcePos` variant to `Decl` ADT
- `stdlib/compiler/parser.sprout:1142` — add branch in `parse_decl_body`; add `parse_extern_decl` (consume `extern fn`, name, typed params — error if untyped, `->` return type required, no body)
- `stdlib/compiler/checker.sprout` — no change needed for basic support (externs are not registered via `builtin_entries`; they are handled by the inferencer)
- `stdlib/compiler/infer.sprout` — add `ExternDecl` branch in `typecheck_decls`: build function type from annotations, register as `Scheme { vars=[], type=fn_type, effect=EffectIO }`; no body inference
- `stdlib/compiler/typed_ast.sprout` — reuse `TPassThrough` or add `TExternDecl` variant (must carry enough info for codegen to emit `declare`)
- `stdlib/compiler/lowering.sprout` — add pass-through branch (no typeclass transforms apply to extern)
- `stdlib/compiler/codegen.sprout` — after `emit_extern_decl_keys`, add a second pass over `ExternDecl` nodes; merge names into the `sigs` dict so `emit_call` resolves them at callsites

**Done check:** File with `extern fn uart_send(c: Int) -> Unit` type-checks cleanly; IR contains `declare i64 @uart_send(i64)`.

**Risk:** Medium. Inference must short-circuit for externs (no generalisation, always IO). `TPassThrough` may not carry enough for codegen — verify before choosing between reuse and a new variant.

---

## Phase 3 — Bare-Metal C Runtime

**Goal:** A freestanding C file providing all `sprout_*` runtime symbols — no `malloc`, no OS headers, no GC.

**New file:** `runtime/bare_metal.c`

Must provide:
- `SproutObj` struct (identical ABI to the OS runtime — must match self-hosted codegen assumptions)
- `sprout_make0` … `sprout_make9`, `sprout_nothing`, `sprout_tag`, `sprout_field`, `sprout_register_ctor`
- `sprout_alloc_closure_env`, `sprout_alloc_tuple_blob` — backed by a static bump allocator:
  ```c
  static uint8_t _heap[SPROUT_HEAP_SIZE] __attribute__((aligned(16)));
  static size_t  _heap_top = 0;
  ```
- `sprout_gc_push_*_root`, `sprout_gc_pop_roots`, `sprout_gc_register_*_root` — **no-op stubs** (leaks but never corrupts; acceptable for kernel code)
- `__sprout_init_globals` — no-op stub
- `sprout_abort_match` — `while(1){}` or `__asm__("wfi")`; no `fprintf`, no `exit`
- `print_str`, `print_int` — no-op stubs (real UART added in Phase 5)
- `str_concat`, `str_len` — minimal implementations using the bump allocator

Only headers: `<stdint.h>`, `<stddef.h>`, `<stdbool.h>`. No `<stdlib.h>`, `<unistd.h>`, `<sys/*>`.

**Done check:** `clang --target aarch64-unknown-none -ffreestanding -nostdlib -c runtime/bare_metal.c` exits 0 with zero warnings.

**Risk:** High (most effort-dense task). `SproutObj` pointer boxing must be i64-width (correct for AArch64). Bump allocator must be 16-byte aligned per AAPCS64. `SPROUT_HEAP_SIZE` must be a `#define` knob.

---

## Phase 4 — `--bare-metal` Codegen Mode

**Goal:** Self-hosted compiler emits bare-metal–safe LLVM IR: no GC calls, no `argc/argv`, entry function named `_start_sprout`.

**Changes:**

- `stdlib/compiler/compile_driver.sprout` — parse `--bare-metal` flag; thread to `compile_to_ir`
- `stdlib/compiler/codegen.sprout:2472–2481` — when `bare_metal`:
  - Entry function name: `_start_sprout` (not `main` — avoids collision with boot stub's `_start`)
  - Entry params: none (no `i32 %argc, ptr %argv`)
  - Skip `sprout_set_argv` call
  - Skip `__sprout_init_globals` call
- `stdlib/compiler/codegen.sprout:2759–2783` — define `bare_metal_externs` (subset of full extern dict, only symbols defined in `runtime/bare_metal.c`); emit `declare` lines from this subset when `bare_metal` is set. Prevents linker errors from `declare tcp_listen(...)` etc. that have no definition.
- `justfile` — extend `build-rpi` recipe to pass `--bare-metal`:
  ```sh
  ./compile_driver_bin kernel.sprout --target aarch64-unknown-none --bare-metal > kernel.ll
  clang --target aarch64-unknown-none -ffreestanding -nostdlib -nostartfiles \
        -T stdlib/kernel/link.ld kernel.ll runtime/bare_metal.c boot.o -o kernel.elf
  llvm-objcopy -O binary kernel.elf kernel8.img
  ```

**Done check:** `llvm-nm kernel.elf | grep "U "` empty. `readelf -d kernel.elf` shows no NEEDED entries.

**Risk:** Medium. Residual GC symbol references are the main failure mode; `bare_metal_externs` filter surfaces them at IR-emit time.

---

## Phase 5 — MMIO Builtins + Freestanding Stdlib

**Goal:** `raw_mmio_read32` / `raw_mmio_write32` emit LLVM volatile load/store; `stdlib/kernel/` provides RPi UART and GPIO.

**Changes:**

- `stdlib/compiler/codegen.sprout` — special-case in call emitter (no C function behind these; emit inline IR):
  ```
  raw_mmio_write32(addr, val):
    %ptr   = inttoptr i64 <addr> to ptr
    %trunc = trunc i64 <val> to i32
    store volatile i32 %trunc, ptr %ptr, align 4
    → i64 0 (Unit)

  raw_mmio_read32(addr):
    %ptr = inttoptr i64 <addr> to ptr
    %raw = load volatile i32, ptr %ptr, align 4
    %out = zext i32 %raw to i64
    → i64 %out
  ```
- `stdlib/compiler/checker.sprout` — add to `builtin_entries()`:
  ```sprout
  ("raw_mmio_read32",  bt_mono(bt_io(bt_int(), bt_int())))
  ("raw_mmio_write32", bt_mono(bt_io2(bt_int(), bt_int(), bt_unit())))
  ```
- New `stdlib/kernel/mmio.sprout` — `raw_mmio_read32` / `raw_mmio_write32` wrappers + RPi3 (`0x3F000000`) / RPi4 (`0xFE000000`) peripheral base constants
- New `stdlib/kernel/uart.sprout` — PL011 UART0: `uart_init`, `uart_putchar`, `uart_puts`, `uart_print_int`
- New `stdlib/kernel/gpio.sprout` — `gpio_set_function`, `gpio_set`, `gpio_clear` (ACT LED = GPIO 47 on RPi3)

**Done check:** `llvm-dis kernel.ll | grep volatile` finds `store volatile i32` and `load volatile i32`.

**Risk:** Low for codegen (LLVM LangRef guarantees volatile not eliminated by optimiser). Medium for stdlib: module resolver must handle `stdlib.kernel.*` cross-imports.

---

## Phase 6 — Freestanding Prelude + Boot Assembly

**Goal:** Minimal prelude with no OS deps; AArch64 boot stub.

**Changes:**

- New `stdlib/prelude_kernel.sprout` — keep `Maybe`, `Result`, `List`, `Cons`, `Nil`, `Just`, `Nothing`, `Ok`, `Err`; remove `Vec`, `Dict`, `Set`, `print`, `range`, `StringTemplate`, anything with OS deps
- `stdlib/compiler/compile_driver.sprout` — when `--bare-metal`, load `prelude_kernel.sprout` instead of `prelude.sprout`
- New `stdlib/kernel/boot.S`:
  ```asm
  .section ".text.boot"
  .global _start
  _start:
      mrs x0, mpidr_el1       // halt cores 1-3
      and x0, x0, #3
      cbz x0, 1f
      wfe
      b   .
  1:  adr x0, __bss_start     // zero BSS
      adr x1, __bss_end
      mov x2, #0
  2:  cmp x0, x1
      b.ge 3f
      str x2, [x0], #8
      b   2b
  3:  ldr x0, =0x80000        // stack below load address
      mov sp, x0
      bl  _start_sprout
      wfe
      b   .
  ```
- New `stdlib/kernel/link.ld` — entry `_start`; `.text.boot` at `0x80000`; `.text`, `.rodata`, `.data`, `.bss` following; symbols `__bss_start`, `__bss_end`

**Done check:** `readelf -s kernel.elf | grep -E "_start|_start_sprout"` — both symbols present.

**Risk:** Medium. Secondary-core halt is critical — cores 1–3 will corrupt the bump allocator without it. Stack placed below `0x80000` (grows down); confirmed safe for RPi3 firmware load address.

---

## Phase 7 — Proof-of-Life Integration

**Goal:** Sprout kernel boots in QEMU, prints "Hello, World" over UART, blinks ACT LED.

**New files:**

`examples/rpi3_hello/kernel.sprout`:
```sprout
import stdlib.kernel.uart as Uart
import stdlib.kernel.gpio as Gpio

fn main() -> Unit !{IO} =
  do
    _ <- Uart.init()
    _ <- Uart.puts("Hello, World\n")
    _ <- Gpio.set_function(47, 1)
    _ <- blink_loop(0)
```

`justfile` `rpi3-hello` target:
```sh
cd examples/rpi3_hello
clang --target aarch64-unknown-none -ffreestanding -nostdlib \
      -c ../../stdlib/kernel/boot.S -o boot.o
../../compile_driver_bin kernel.sprout \
      --target aarch64-unknown-none --bare-metal > kernel.ll
clang --target aarch64-unknown-none -ffreestanding -nostdlib -nostartfiles \
      -T ../../stdlib/kernel/link.ld kernel.ll ../../runtime/bare_metal.c boot.o \
      -o kernel.elf
llvm-objcopy -O binary kernel.elf kernel8.img
```

QEMU verification:
```sh
qemu-system-aarch64 -M raspi3b -kernel kernel8.img -serial stdio -display none
# Expected: Hello, World
```

Hardware verification: SD card with `kernel8.img` + `config.txt` (`arm_64bit=1`); UART0 at 115200 baud 3.3 V.

---

## Bootstrap Note

All changes to `stdlib/compiler/` require a full bootstrap cycle before they can be tested in kernel code:

1. Edit source in `stdlib/compiler/`
2. Run `mise exec -- just test` to re-bootstrap Stage 1 and validate Stage 2
3. The new `compile_driver_bin` reflects the changes

Each phase has a non-trivial integration cost. Plan phase boundaries around bootstrap checkpoints.

---

## Risk Register

| Risk | Severity | Mitigation |
|---|---|---|
| GC symbols emitted in bare-metal IR | High | `bare_metal_externs` subset filter (Phase 4) |
| Secondary CPU cores corrupt bump allocator | Critical | `mpidr_el1` spin in `boot.S` (Phase 6) |
| Entry symbol collision `_start` vs boot stub | Medium | Entry renamed `_start_sprout` in bare-metal mode (Phase 4) |
| Bump allocator too small | Medium | `#define SPROUT_HEAP_SIZE` knob; 64 KiB for hello-world |
| Closures leak without GC | Acceptable | GC stubs are no-ops; leaks never corrupt; acceptable for kernel code |
| `inttoptr` + `-O2` elides volatile | None | LLVM LangRef guarantees volatile is not eliminated |
| RPi3 vs RPi4 UART base (`0x3F201000` vs `0xFE201000`) | Low | Board constant in `mmio.sprout`; target RPi3 first |
| Bootstrap cycle makes iteration slow | Operational | Phase boundaries align with bootstrap checkpoints |

---

## Verification Checklist

1. `just rpi3-hello` exits 0
2. `readelf -e kernel.elf` — no INTERP section, no NEEDED entries
3. `readelf -s kernel.elf | grep -E "_start|_start_sprout"` — both symbols present
4. `llvm-nm kernel.elf | grep "U "` — empty
5. `llvm-dis kernel.ll | grep volatile` — `store volatile` and `load volatile` present
6. `qemu-system-aarch64 -M raspi3b -kernel kernel8.img -serial stdio -display none` prints `Hello, World`
