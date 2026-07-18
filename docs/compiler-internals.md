# Compiler Internals

Invariants and design constraints to know **before editing** `stdlib/compiler/` or `runtime/sprout_runtime.c`. Violating these produces subtle, hard-to-diagnose bugs. For diagnostic tools to use when things are already broken, see [debugging.md](debugging.md).

## GC ABI Invariants

### Strings and chars travel as `i64` (GC Option C)

**Intentional design — do not "correct" it.**

Throughout the IR pipeline (`ast_to_ir.sprout` → `ir_lowering.sprout`), String and Char values are represented as `i64` at the LLVM IR level — a raw pointer cast to an integer. The GC root-tracking table stores all heap roots as `i64`; making strings travel as `i64` throughout IR means every string slot is automatically compatible with the root table without `ptrtoint`/`inttoptr` casts at each GC-safe point.

Implications for IR-pipeline edits:
- `type_kind.type_is_non_heap_scalar` classifies `Int`/`Bool`/`Char` as scalars; String is heap, so a String SSA value is an `i64` heap handle that must be rooted across GC-triggering ops.
- String/Char literals lower to a `str_ptr` global coerced to `i64`.
- String comparisons must coerce both operands back to `ptr` before calling `str_eq`/`str_compare` (see the `emit_ptr_comparison` lowering in `ir_lowering.sprout`).
- `str_concat`, `str_slice`, etc.: called with `i64` args, return `i64`.
- String globals size the LLVM array with `str_byte_len(s) + 1` — not `str_len`, which counts Unicode codepoints rather than UTF-8 bytes.

If you see `ll_ptr()` for String/Char in emitted IR, that is a regression. Canonical form is `ll_i64()`.

### Non-moving GC (mark-sweep)

**Foundational invariant — relied on by every rooting helper.**

Sprout's GC is **non-moving mark-sweep**.  Every heap object occupies a 16-byte-aligned slot inside a 1 MiB region (`SproutRegion`).  Each slot begins with an 8-byte inline header at `payload_ptr - 8`; the payload starts at `payload_ptr`.  Header layout (64 bits):

```
bits  0– 7  kind   (SproutHeapKind: FREE=0, OBJ=1, CLOSURE=2, …, CSTR=10; POISON=0xFF)
bit   8      color  (mark bit, toggled during the mark phase)
bits  9–13  (reserved)
bits 14–63  aux    (OBJ: (tag<<4)|arity; CSTR: byte length; CLOSURE: n_caps; TUPLE: word count; FREE/POISON: slot_bytes)
```

Per-region 1-bit slotmaps track live slot starts; `sprout_heap_lookup` does a binary search over the region table, verifies the slotmap bit, and rejects FREE-kind headers — giving exact membership in O(log region_count).  Large objects (slot > 4096 bytes) are stored as single-slot dedicated `malloc` blocks registered in the region table with `is_large=1`.

`sprout_gc_sweep` (~line 1488 in `runtime/sprout_runtime.c`) runs three passes: (1) scan all slots — live slots clear their color bit; dead slots release external storage and get a FREE header (or a POISON header in lineage mode, retaining the corpse with the slotmap bit set); (2) release regions with no live and no poison objects (keeping at least one normal region); (3) rebuild per-class freelists from surviving FREE slots so future allocations reuse them without scanning.

Because objects never move, the address of a live heap object is **stable for the entire program lifetime**.

**Load-bearing invariant — registration/adoption paths must never trigger GC.** Functions that register or adopt an already-allocated object into the managed set (e.g. `register_cstr` and its callers) run with earlier, not-yet-rooted objects held in registers: a string builder registers freshly-built strings back-to-back while still holding the previous ones unrooted. The contract "registration itself never collects" is therefore load-bearing — **never add a `sprout_gc_maybe_collect*` call (or any operation that can) to a registration/adoption path**, or those in-flight unrooted objects get swept mid-sequence. Allocation paths may collect; registration paths may not.

Implications for codegen / IR design:

- The "push the alloca holding an `i64` heap-address; never reload" pattern (used by `IRRoot` in `stdlib/compiler/ir_rooting.sprout`) is correct: the `i64` stored at the alloca remains a valid heap pointer for the entire function lifetime.
- If GC ever becomes moving (copying, compacting, generational), every root push must be paired with a re-load *after* its trigger op, and every heap-typed SSA use after a trigger must source from the reload — a sweeping rewrite affecting `ast_to_ir.sprout`, `ir_lowering.sprout`, and `ir_rooting.sprout`. This is not currently planned.

### Type-aware GC rooting — the `ir_rooting` pass

**Intentional design — do not root non-heap scalars.**

Rooting is a dedicated pass over the IR (`ir_rooting.insert_roots`), not a set of
per-call-site helpers. It computes per-op liveness and inserts `IRRoot` ops so that
every heap SSA value stays reachable across GC-triggering ops. Type-awareness comes
from classifying which SSA values are heap:

- `op_triggers_gc` — which ops are GC-safe points (allocations, calls, etc.).
- `op_produces_simple_heap` — which op *results* are heap values that must be tracked. Scalars (`Int`/`Bool`/`Char`, via `type_kind.type_is_non_heap_scalar`) are excluded; an `IRCall` result is rooted unless its carried return `IRType` is `IRTScalar`.
- `compute_heap_origin` / `roots_across` — track the heap-origin set and compute, for each op, the values that must be rooted across it (live-after ∪ heap operands the op exposes).

**Why it matters:** rooting every `Int`-returning expression emits pointless
`alloca i64; store; sprout_gc_push_i64_root; …; sprout_gc_pop_roots(1)`. Profiling
N-queens showed 67% of CPU time in GC root calls, ~50% pure waste from Int args.
Type-aware rooting gave a measured **1.5–2.7× speedup** (N=12: ~1.5 s → 928 ms).

**Invariant:** when no source-level type is available, root conservatively (treat the value as heap). A spurious extra root is harmless; a missing root corrupts the heap. Do not treat `TVar` as non-heap — it may resolve to a heap type in monomorphized code.

Regression tests: `tests/stdlib/compiler/test_codegen.spr` — "Int args to call must NOT emit gc_push_i64_root", "Vec arg to call MUST emit gc_push_i64_root", "mixed call: Vec arg rooted".

### GC safety linter

`just gc-safety-check` lints `runtime/sprout_runtime.c` for `const char*`/`char*` parameters live across `sprout_gc_maybe_collect_threshold()` calls. Run after editing any C builtin that allocates heap strings. Use `just gc-safety-check --strict` to fail on any finding; the default mode warns only.
