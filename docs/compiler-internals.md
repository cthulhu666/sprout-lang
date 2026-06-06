# Compiler Internals

Invariants and design constraints to know **before editing** `stdlib/compiler/` or `runtime/sprout_runtime.c`. Violating these produces subtle, hard-to-diagnose bugs. For diagnostic tools to use when things are already broken, see [debugging.md](debugging.md).

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
