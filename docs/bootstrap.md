# Bootstrap Debugging Tools

Diagnostic phases, GC ABI invariants, and break-glass protocols for working on the Sprout self-hosted compiler. For the stage pipeline and trust chain, see [bootstrap-chain.md](bootstrap-chain.md).

## Diagnostic Phases (`--phase` CLI option)

| Phase | What it does |
|---|---|
| `scan-info <stdlib> <file>` | Calls `bundler.scan_source_info` and prints `module:`, `export:`, `ctor:` lines. Diagnoses module-name extraction bugs without running the full bundler pipeline. |
| `dump-qualify <stdlib> <file>` | Runs full collection + qualify and prints original→qualified name mapping per module, plus `ctx: EMPTY` or `ctx: populated` for each. A `ctx: EMPTY` line means `build_resolve_ctx` failed to find the module's path in `all_symbols` — all names stay unqualified, triggering the `[assert]` error. |
| `bundle <stdlib> <file>` | Runs the full bundle phase and prints qualified decl names. Gold standard for detecting qualify-stage regressions; output must be identical between stage-0 and stage-1 (verified by `tests/stdlib/test_bundler.spr`). |

**Troubleshooting `BundleErr("[assert] qualify_decl: FnDecl starts with '.'...")`:**
1. Run `--phase scan-info` to confirm `scan_source_info` returns a valid non-empty module name.
2. If the name looks correct, run `--phase dump-qualify` to find which module has `ctx: EMPTY`.

## GC ABI Invariants

### Strings and chars travel as `i64` (GC Option C)

**Intentional design — do not "correct" it.**

In `stdlib/compiler/codegen.sprout`, String and Char values are represented as `i64` at the LLVM IR level — a raw pointer cast to an integer. The GC root-tracking table stores all heap roots as `i64`; making strings travel as `i64` throughout IR means every string slot is automatically compatible with the root table without `ptrtoint`/`inttoptr` casts at each GC-safe point.

Implications for codegen edits:
- `const_to_ll("String")` and `const_to_ll("Char")` return `ll_i64()` — correct.
- `emit_expr` for `TString`/`TChar` nodes: emits a `str_ptr`, then coerces to `i64`.
- String comparisons in `emit_binary`: must coerce both operands back to `ptr` before calling `str_eq`/`str_compare`. Use `compare_needs_ptr_dispatch(left_ty, right_ty)` to detect this.
- `str_concat`, `str_slice`, etc.: called with `i64` args, return `i64`.
- `string_const` uses `str_byte_len(s) + 1` for LLVM array sizing — not `str_len`, which counts Unicode codepoints rather than UTF-8 bytes.

If you see `ll_ptr()` for String/Char in codegen, that is a regression. Canonical form is `ll_i64()`.

### Non-moving GC (mark-sweep)

**Foundational invariant — relied on by every rooting helper.**

Sprout's GC is **non-moving mark-sweep**: `sprout_gc_sweep` in `runtime/sprout_runtime.c` (~line 1063) walks the managed-node list; unmarked nodes are `free()`d, marked nodes have their `marked` flag reset and stay in place — never relocated, never compacted. The address of a live heap object is stable for the entire program lifetime.

Implications for codegen / IR design:

- The "push the alloca holding an `i64` heap-address; never reload" pattern (used by `IRRoot` in `stdlib/compiler/ir_rooting.sprout` and `push_temp_root_typed` / `push_temp_root` in `stdlib/compiler/codegen.sprout`) is correct: the `i64` stored at the alloca remains a valid heap pointer for the entire function lifetime.
- If GC ever becomes moving (copying, compacting, generational), every root push must be paired with a re-load *after* its trigger op, and every heap-typed SSA use after a trigger must source from the reload — a sweeping rewrite affecting `codegen.sprout`, `ir_lowering.sprout`, and `ir_rooting.sprout`. This is not currently planned.

### Type-aware GC rooting — `push_temp_root_typed`

**Intentional design — do not simplify call sites back to `push_temp_root`.**

Two rooting helpers coexist in `stdlib/compiler/codegen.sprout`:

- `push_temp_root(v, em)` — consults only the LLVM-level type. Pushes a root for any `i64`-typed value, which includes both `Int` and boxed ADT handles.
- `push_temp_root_typed(v, ty, em)` — additionally consults the source-level Sprout type. Skips the push when `ty` is a known non-heap scalar (`Int`, `Bool`, `Char` — see `type_is_non_heap_scalar`). Falls back to `push_temp_root` conservatively for `TVar`, ADT names, and polymorphic vars.

**Why it matters:** before this distinction, every `Int`-returning expression emitted pointless `alloca i64; store; sprout_gc_push_i64_root; …; sprout_gc_pop_roots(1)`. Profiling N-queens showed 67% of CPU time in GC root calls, ~50% pure waste from Int args. Type-aware rooting gave a measured **1.5–2.7× speedup** (N=12: ~1.5 s → 928 ms).

Call sites that must use `push_temp_root_typed`:
- `emit_args_with_roots` and `emit_args_with_roots_lls` (function call argument rooting)
- `emit_tco_args` (tail-call argument rooting)
- `emit_tuple_items` (tuple construction)
- `emit_do` `TDoLetStep` (do-block let binding rooting)
- `build_param_locals_and_push_roots` (function-entry parameter rooting)
- `emit_pattern_bind` `VarPattern` (match binder rooting)
- `load_lambda_params` (lambda parameter rooting)
- `allocate_tco_slots_acc` (TCO slot rooting)

**Invariant:** when no source-level type is available, fall back to `push_temp_root`. A spurious extra root is harmless; a missing root corrupts the heap. Do not treat `TVar` as non-heap — it may resolve to a heap type in monomorphized code.

Regression tests: `tests/stdlib/compiler/test_codegen.spr` — "Int args to call must NOT emit gc_push_i64_root", "Vec arg to call MUST emit gc_push_i64_root", "mixed call: Vec arg rooted".

### GC safety linter

`just gc-safety-check` lints `runtime/sprout_runtime.c` for `const char*`/`char*` parameters live across `sprout_gc_maybe_collect_threshold()` calls. Run after editing any C builtin that allocates heap strings. Use `just gc-safety-check --strict` to fail on any finding; the default mode warns only.

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
