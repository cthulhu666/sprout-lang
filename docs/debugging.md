# Debugging Tools

Tools and protocols to reach for **when something is broken** in the Sprout compiler or bootstrap pipeline. For invariants to know before editing codegen, see [compiler-internals.md](compiler-internals.md). For the stage pipeline and trust chain, see [bootstrap-chain.md](bootstrap-chain.md).

## Diagnostic Phases (`--phase` CLI option)

| Phase | What it does |
|---|---|
| `scan-info <stdlib> <file>` | Calls `bundler.scan_source_info` and prints `module:`, `export:`, `ctor:` lines. Diagnoses module-name extraction bugs without running the full bundler pipeline. |
| `dump-qualify <stdlib> <file>` | Runs full collection + qualify and prints original→qualified name mapping per module, plus `ctx: EMPTY` or `ctx: populated` for each. A `ctx: EMPTY` line means `build_resolve_ctx` failed to find the module's path in `all_symbols` — all names stay unqualified, triggering the `[assert]` error. |
| `bundle <stdlib> <file>` | Runs the full bundle phase and prints qualified decl names. Gold standard for detecting qualify-stage regressions; output must be identical between stage-0 and stage-1 (verified by `tests/stdlib/test_bundler.spr`). |

**Troubleshooting `BundleErr("[assert] qualify_decl: FnDecl starts with '.'...")`:**
1. Run `--phase scan-info` to confirm `scan_source_info` returns a valid non-empty module name.
2. If the name looks correct, run `--phase dump-qualify` to find which module has `ctx: EMPTY`.

## `just llvm-where <ll_file> <line>` — map an error line to its Sprout function

When `opt --passes=verify` (or clang) reports a malformed-IR error at line N of a large `.ll` file, this tool walks the file up from line N to the nearest enclosing `define` and prints the Sprout qualified name.

```
$ just llvm-where build/stage2.ll 130912
line 130912 in: @stdlib.compiler.codegen.emit_fn
```

Lambda wrappers also include the source comment header:

```
$ just llvm-where bootstrap/compile_driver.ll 263090
line 263090 in: @__sprout_lambda_1680_vec_sum_by  (header: ; __sprout_lambda_1680_vec_sum_by (in vec_sum_by))
```

Lines before the first `define` (module-level constant table):

```
$ just llvm-where bootstrap/compile_driver.ll 42
line 42 in: <module-level section — before first define>
```

**When to use it:** any time `opt --passes=verify` or `clang` emits an error with a raw line number and the IR file is too large to inspect manually. The `just refresh-seed` and `_build-stage` recipes both run `opt --passes=verify` before linking; failures there typically look like:

```
error: unable to create block named 'entry'
```

or phi/insertvalue type mismatches. Run `just llvm-where <ll_file> <N>` to identify the offending Sprout function, then search for it in the compiler source.

**Implementation:** `scripts/llvm_diag.sh` — single-pass awk; O(n) in file size, no temp files.

## Stack-overflow panic + backtrace

Compiled Sprout programs catch native stack overflow and report it instead of
dying with a silent `SIGSEGV`. The runtime installs its crash handler on an
**alternate signal stack** (`sprout_install_crash_handlers` in
`runtime/sprout_runtime.c`), which is what lets the handler run *after* the
thread stack is exhausted — without it the handler re-faults and the process
exits 139 with no output. On overflow you get:

```
[sprout] fatal: stack overflow - unbounded or too-deep recursion
0   prog   ... sprout_crash_handler
1   ...    ... _sigtramp
2   prog   ... stdlib.compiler.lexer.tokenize_from + 800
3   prog   ... stdlib.compiler.lexer.tokenize_from + 800
...
```

The repeated frame names the **recursing function** — the usual culprit is a
self-recursive function that the code generator failed to turn into a TCO loop
(it overflows only for large inputs, so small smoke tests pass). This is the
first thing to read when a large program crashes at exit 139; it pinpoints in
one run what an `lldb` backtrace cannot (the overflow faults mid-prologue, so
`lldb` typically can't unwind the blown stack at all).

**Notes:**
- macOS symbolises frames from the symbol table automatically; on **Linux**,
  named (vs bare-address) frames require linking the program with `-rdynamic`.
- Detection compares the fault address against the stack bounds captured at
  startup; a genuine wild-pointer fault falls through to a generic
  `[sprout] SIGSEGV` message plus the same backtrace.
- Regression: `just stack-overflow-smoke` (`tests/stack_overflow_smoke/`).

## Typed-codegen flip gates (`just tco-diff`, `just flip-readiness`)

Two checks for the "typed codegen diverges from direct codegen only at scale or
under a runtime condition" bug class — the class the parity corpus structurally
cannot catch (it runs only small files, with no argv). Both are RED until the
typed-codegen flip work lands; make them hard CI gates once green.

- **`just tco-diff [PROBE]`** — emits IR for `PROBE` via both `--emit-ir`
  (direct) and `--use-ir-codegen` (typed), counts `tco_loop` basic-block labels,
  and fails if typed emits fewer. Direct currently emits 55 and typed 0 for the
  default probe — i.e. typed codegen does **no** tail-call optimization, so any
  self-tail-recursive function overflows the stack at scale (flip blocker #2,
  `lexer.tokenize_from`). Doubles as the progress meter for the TCO fix (watch
  `typed` climb to match `direct`).
- **`just flip-readiness`** — the real flip gate: typed self-compile the compiler
  → `opt --passes=verify` → link → have that binary self-compile to a **fixed
  point**, asserting the result is *verifiable IR with `define` blocks* (not just
  non-empty — the argv blocker prints a ~240-byte usage string with exit 0, a
  false green against a naive check). When it fails, read the failing binary's
  stderr: the stack-overflow panic names the recursing culprit.

## 2-Step Bootstrap Protocol

**When this applies:** the committed seed (`bootstrap/compile_driver.ll`) predates a parser change. `just refresh-seed` calls `just bootstrap-from-seed` internally, which rebuilds the binary from the committed seed. If that seed has the old parser, the rebuilt binary cannot parse source files that use the new syntax — a catch-22.

**Why the protocol works:** by temporarily reverting the files that *use* new syntax (but not `parser.sprout` itself), you can run the current stage-1 binary (which has the old parser and can still parse old syntax) to compile a `compile_driver.sprout` that includes the new parser in its output IR. That IR becomes the updated seed, which has the new parser baked in.

**Steps:**

1. Temporarily revert the files that *use* the new syntax to old syntax on disk. Do **not** revert `parser.sprout`:
   ```
   git checkout master -- stdlib/compiler/compile_driver.sprout stdlib/compiler/codegen.sprout stdlib/compiler/lowering.sprout stdlib/prelude.sprout
   ```
   Adjust the file list to match what you changed.

2. Emit intermediate IR using the current stage-1 binary (which can parse the old syntax):
   ```
   ./build/compile_driver_bin_stage1 --emit-ir stdlib stdlib/compiler/compile_driver.sprout > /tmp/intermediate.ll
   ```

   > **Verify success before proceeding:** check that `/tmp/intermediate.ll` starts with `; Generated by sprout codegen.sprout`. The old binary writes errors to stdout and exits 0, so the first line is the only reliable success indicator.

   Copy the verified output to the seed:
   ```
   cp /tmp/intermediate.ll bootstrap/compile_driver.ll
   ```

3. Restore the HEAD files reverted in step 1:
   ```
   git checkout HEAD -- <files reverted in step 1>
   ```

4. Run `just refresh-seed` — now succeeds because the seed contains the updated parser:
   ```
   scripts/memwatch.sh 4096 1 -- just refresh-seed
   git add bootstrap/compile_driver.ll
   ```
