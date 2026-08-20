# Sprout Debugger v1 — Design Draft

## Problem

Compiled Sprout programs currently produce native binaries with no debug information.
When something goes wrong, developers have no way to set breakpoints at Sprout source
lines, step through execution, or inspect Sprout ADT values.  The source positions
(`SourcePos`, a record of `index` / `line` / `column`) needed to solve this are already
present on every `TypedExpr` node — they just need to reach the emitted IR.

## Goals

- Enable breakpoints at Sprout source line/column granularity in `lldb`/`gdb`.
- Enable `step`, `next`, and `bt` (backtrace) at the Sprout source level.
- Enable inspection of Sprout ADT values at breakpoints (show constructor name and fields).
- Integrate with the existing native toolchain — no new interactive debugger process.
- Keep debug metadata strictly opt-in (`--debug` flag); release builds are unchanged.

## Non-Goals

- Debugging the self-hosted compiler internals (see backlog item 12).
- Source-level expression tracing or watchpoints (future).
- A standalone interactive Sprout debugger UI (out of scope for v1).
- Live variable watches across GC cycles.
- Full multi-file DWARF (M1 scopes to the user's own module; see §Multi-file limitation).

---

## Architecture

Three components, delivered across three milestones.

---

### Component 1 — DWARF metadata in codegen

**Files changed:** `stdlib/compiler/codegen.sprout`, `stdlib/compiler/compiler.sprout`,
`stdlib/compiler/compile_driver.sprout`

A new `--debug` flag on the compile driver causes the codegen to emit LLVM debug
metadata alongside the IR.  Once DWARF is present, `lldb`/`gdb` can break at Sprout
source lines, step through them, and show backtraces with Sprout function names.

#### DWARF metadata structure

Emitted in a new 4th IR section (after header, string constants, function defs):

```llvm
; Module-level debug registration
!llvm.dbg.cu = !{!0}
!llvm.module.flags = !{!1, !2}
!0 = distinct !DICompileUnit(language: DW_LANG_C99, file: !3,
       producer: "sprout", isOptimized: false, runtimeVersion: 0,
       emissionKind: FullDebug, splitDebugInlining: false)
!1 = !{i32 2, !"Dwarf Version", i32 5}
!2 = !{i32 2, !"Debug Info Version", i32 3}
!3 = !DIFile(filename: "myprog.spr", directory: "/path/to")

; Per-function subprogram (one per TFnDecl in the user module):
!4 = distinct !DISubprogram(name: "main.greet", linkageName: "main.greet",
       file: !3, line: 3, type: !5, unit: !0, retainedNodes: !{})
!5 = !DISubroutineType(types: !6)
!6 = !{}       ; parameter/return types are opaque in v1

; Per source location (deduplicated by (line, col, scope)):
!7 = !DILocation(line: 5, column: 12, scope: !4)
```

Each emitted IR instruction gains `, !dbg !N` when its `TypedExpr` node carries a
`SourcePos`.  `typed_expr_pos` already extracts this from every `TypedExpr` variant.

#### Changes to `CgCtx`

`CgCtx` gains two fields (currently 6 positional fields → 8):

- `debug_mode: Bool` — when false, all debug emission is skipped and the release path
  is identical to today.
- `source_file: String` — the entry file path threaded from the compile driver, used
  as the single `!DIFile` reference in M1.

All `ctx_*` accessors and `ctx_with_*` helpers require updating; this is the
expected cost of positional ADT encoding.

#### Changes to `EmitterState`

`EmitterState` gains two fields (9 `Ref` fields → 11):

- `meta_id_counter: Ref Int` — monotonically increasing metadata node ID, shared
  across all metadata emission in the translation unit.
- `meta_lines: Ref (List String)` — debug metadata lines accumulated during emission;
  flushed as the 4th IR section after all `define` bodies are written.

#### Key function changes

**`emit_fn`** — when `debug_mode` is true:
1. Allocates a metadata ID for the `!DISubprogram` node.
2. Appends the `!DISubprogram` to `meta_lines`.
3. Appends `!dbg !N` to the `define` directive.
4. Stores the subprogram ID for use by `emit_expr` within this function.

**`emit_expr`** — when `debug_mode` is true, calls a `emit_dbg_loc` helper that:
1. Extracts the `SourcePos` via `typed_expr_pos`.
2. Allocates a metadata ID for a `!DILocation` node.
3. Appends `!DILocation(line: L, column: C, scope: !subprog)` to `meta_lines`.
4. Returns the `", !dbg !N"` suffix to be appended to the instruction string.

`emit_dbg_loc` is a no-op when `debug_mode` is false — zero overhead in release builds.

**`compile_to_ir_lines`** — returns a 4th `List String` for debug metadata (empty when
`debug_mode` is false).  `IrLinesOk` and all callers in `compiler.sprout` and
`compile_driver.sprout` are updated to carry and emit this 4th section.

#### Multi-file limitation (M1)

After bundling, declarations from multiple source files are merged into one
`TypedProgram`.  Each `SourcePos` is relative to its origin file, but codegen does not
record which file each declaration came from.  If we emitted `!dbg` for all functions
with a single `!DIFile`, stdlib frames would show the wrong source file in `lldb`.

**M1 mitigation:** emit `!dbg` only for `TFnDecl`s whose qualified name is prefixed by
the user's own module name.  The user module prefix is derived at the compile driver
level (from the entry file) and passed into `CgCtx.source_file` alongside `debug_mode`.

Stdlib/prelude functions get no `!dbg` attachment; LLDB shows "source not available"
for those frames, which is correct — stdlib sources are not distributed with binaries.

Full multi-file DWARF requires the bundle phase to record origin paths per declaration
(the most natural hook: a `source_file: Maybe String` field on `TFnDecl`).  That is a
separate follow-up tracked in the open questions below.

---

### Component 2 — Constructor type descriptor table

**Files changed:** `runtime/sprout_runtime.c`, `stdlib/compiler/codegen.sprout`

For the pretty-printer to show Sprout ADT fields meaningfully, each constructor's
existing `CtorMeta` entry is extended with a `field_kinds` descriptor string — one
character per field:

| Char | Meaning |
|---|---|
| `i` | Int (`i64` integer value) |
| `b` | Bool (`i1`, stored as `i64` 0 or 1) |
| `s` | String or Char (`i64` raw pointer — GC Option C) |
| `p` | ADT or closure handle (`i64` pointing to `SproutObj`) |
| `_` | Opaque / type variable (`i64`, type erased at emit time) |

The C runtime struct becomes:

```c
typedef struct {
  long long   tag;
  const char* name;
  long long   arity;
  const char* field_kinds;   /* one char per field, null-terminated */
} SproutCtorMeta;
```

The codegen has the information to derive `field_kinds` at constructor-emission time:
`CtorSig` carries both `arg_lltypes` (for the `i64`/`i1` distinction) and
`arg_type_exprs` (for distinguishing String/Char `i64` from Int/ADT `i64`).

This global is inert at runtime — never read by the program itself, only by debugger
tools introspecting the binary.

---

### Component 3 — ADT pretty-printer

**New files:** under `tools/`

A tool that accepts a running process ID and a heap address, walks the
`__sprout_ctor_meta_table` symbol to build `tag → (name, arity, field_kinds)`, reads
the `SproutObj` at that address, and formats the value recursively:

```
Cons(42, Nil)
Just("hello")
Pair(True, 3)
```

#### Implementation language

Deferred to M2 kickoff.  Three viable options, no Python:

1. **LLDB Lua plugin** (`tools/sprout_lldb.lua`) — LLDB 12+ ships Lua scripting;
   registers type summary providers and custom commands.  No Python, no external binary.
2. **Standalone binary** (`tools/sprout-inspect`) — small C program that accepts
   `--pid <pid> --addr <hex>` and reads process memory directly.  Invoked from the LLDB
   console as `!sprout-inspect <pid> <addr>`.  No LLDB Lua dependency.
3. **LLDB type summary format strings** (`.lldbinit` snippet) — LLDB's built-in
   `type summary add -s "${var.tag} ${var.f0}"` approach; no scripting at all, limited
   formatting power, useful as a fallback.

The interface contract (what the tool outputs) is stable regardless of which option is
chosen and does not affect M1.

---

## Milestones

| # | Scope | Deliverable | Status |
|---|---|---|---|
| **M1** | DWARF in codegen; `--debug` flag; 4th IR section | `lldb ./myprog` supports `b myprog.spr:N`, `n`, `s`, `bt` at Sprout source granularity for user-module functions | ✅ Done |
| **M2a** | Line-number offset fix | DWARF line numbers match actual file lines (not stripped-source-relative) | ✅ Done |
| **M2b** | `field_kinds` in `SproutCtorMeta` | `sprout_register_ctor` stores per-field type descriptor; runtime `CtorMeta.field_kinds` populated | ✅ Done |
| **M2c** | `sprout_debug_adt` / `sprout_debug_int` + `tools/sprout.lldb` | `call sprout_debug_adt($x0)` prints `Cons(42, Nil)` at a breakpoint; LLDB command aliases in `tools/sprout.lldb` | ✅ Done |
| **M3** | `just build-debug` / `just debug-run` recipes; `docs/debugging.md` §Debugging compiled programs | One-command debug workflow documented end-to-end | ✅ Done |

---

## Open Questions

1. **M2 tool language:** LLDB Lua vs standalone C binary vs format strings.  Decide at
   M2 kickoff based on target-platform Lua availability and complexity preference.

2. **Multi-file DWARF (post-M1):** Requires the bundle phase to record origin file paths
   per declaration and propagate them to codegen.  Most natural hook: add a
   `source_file: Maybe String` field to `TFnDecl` populated by the bundler.  Scope and
   scheduling TBD after M1 lands.

3. **`TFnDecl` start position:** `TFnDecl` carries no `SourcePos` of its own.  M1
   approximates the function start line as `typed_expr_pos(body)`, which is correct for
   single-expression bodies but may be slightly off for `do` blocks (where the first
   instruction is the bind target).  Exact start position would require a `SourcePos` on
   `TFnDecl` — deferred.

4. **`IrLinesOk` arity change:** adding a 4th debug-metadata list to `IrLinesOk` is a
   public codegen API change.  All call sites in `compiler.sprout` and
   `compile_driver.sprout` must be updated atomically.  M1 implementation must audit all
   callers.
