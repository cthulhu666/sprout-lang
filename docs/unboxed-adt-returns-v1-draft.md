# CPR (Constructed Product Result) Unboxing — V1 Implementation Notes

> **Status (2026-06-03): shipped + width=3 sret ABI follow-up landed.** CPR
> v1 (extern unboxed variants) and v2 (worker/wrapper for non-recursive
> user-defined functions) merged from `perf/cpr-unboxing` in May 2026.
> Width=3 unboxed externs were migrated to the sret ABI on 2026-06-03
> (see `git log -- stdlib/compiler/codegen.sprout`). The "draft" suffix is
> retained because the file name is referenced from BACKLOG.md; it is not
> a current design proposal.

Date: 2026-05-27.

Supersedes: `docs/archive/unboxed-maybe-v1-draft.md` (extern-only scope).

---

## Problem

Every ADT-returning call heap-allocates even when the caller immediately
pattern-matches the result and the heap object is never observed as a pointer.
For N-queens N=12, `vec_get_or` executes ~millions of times, each creating a
`Maybe Bool` that is consumed within the same expression.

Before CPR (N=12): **1.62 s**. After CPR v2: **1.47 s** (~10% improvement).

---

## Design: Two Layers

### Layer 1 — C extern `_unboxed` variants

12 C functions with `SproutUnboxed2 { int64_t tag; int64_t f0; }` return type
skip heap allocation entirely:

```c
SproutUnboxed2 vector_get_unboxed(long long vec_h, long long index) {
  // ... bounds check ...
  if (out_of_bounds) return (SproutUnboxed2){ cached_tag_nothing(), 0 };
  return (SproutUnboxed2){ cached_tag_just(), data[index] };
}
```

Allowlisted 12 functions (all have C `_unboxed` implementations):
`env_get`, `argv_get`, `term_read_line`, `str_char_at`, `str_char_at_byte`,
`regex_find_range`, `vector_get`, `map_get`, `map_nth_key`, `map_nth_value`,
`bytes_get`, `native_set_to_list`.

Codegen: `emit_match_unboxed_adt` routes `match f(args) with` to
`call { i64, i64 } @f_unboxed(...)` + `extractvalue` (no `sprout_tag`/`sprout_field`).

### Layer 2 — Worker/wrapper for user-defined functions

For every non-recursive function returning a small ADT (≤ 3 payload fields,
covering all of `Maybe a`, `Result a b`, `List a`, `Vec a`, `Dict v`), codegen
emits two LLVM symbols:

```llvm
define { i64, i64 } @vec_get_worker(i64 %i, i64 %v) { ... }   ; register struct
define i64          @vec_get(i64 %i, i64 %v) {
  %r = call { i64, i64 } @vec_get_worker(i64 %i, i64 %v)
  ; extract tag + field0, call sprout_rebox2 to box
  ret i64 %boxed
}
```

All existing call sites continue calling `@vec_get` (no ABI break).
The optimization fires only when the result is immediately matched:
`vec_get_or` calls `@vec_get_worker` directly via `emit_worker_expr` and uses
`extractvalue` — the `Maybe Bool` never touches the heap.

**TCO functions** (self-recursive) are excluded from worker emission in v1
(wrapper-only, correct but unoptimized). Recursive CPR deferred to v2.

---

## Key Invariants

- `ctx_cpr_fns: Dict Int` — maps function name → unboxed struct width (2 or 3).
  Built from ALL small-ADT-returning functions (externs + user-defined).
  Used by `emit_match` to decide if CPR applies.

- `ctx_cpr_extern_sigs: Dict FnSig` — maps `name_unboxed` → `FnSig` only for
  the 12 allowlisted externs. Used by `emit_match_unboxed_adt` and
  `emit_worker_cpr_call` to route to `_unboxed`. Non-allowlisted externs fall
  through to the heap path — no linker errors.

- `sprout_rebox2(tag, f0)` / `sprout_rebox3(tag, f0, f1)` — dispatch to
  `sprout_make0`/`sprout_make1`/`sprout_make2` based on `find_ctor(tag)->arity`.

---

## Remaining Bottlenecks (post-CPR)

For N-queens N=12 (~1.47 s baseline after CPR v2):

1. **`vec_set` O(n) copy** — each `vec_set` copies the entire backing array.
   Persistent vector with path copying would reduce this.
2. **`True`/`False`/`Nil` not singletons** — `sprout_make0` caches `Nothing`
   but not booleans or `Nil`. Each `is_free` call in N-queens allocates one
   `True` or `False` heap object.
3. **GC registration overhead** — `register_managed_ptr` on every allocation.
   Generational GC (see `docs/archive/generational-gc-v1-draft.md`) should help.

---

## Files Modified

| File | Change |
|------|--------|
| `stdlib/compiler/codegen.sprout` | `is_cpr_extern_allowlisted`, `build_cpr_fns_acc`, `build_cpr_extern_sigs_acc`, `emit_fn_worker`, `emit_fn_wrapper`, `emit_worker_expr`, `emit_match_unboxed_adt`, `emit_worker_cpr_call`; `CgCtx` 8th field `cpr_extern_sigs` |
| `runtime/sprout_runtime.c` | `SproutUnboxed2`/`SproutUnboxed3` typedefs; `sprout_rebox2`/`sprout_rebox3`; 12 `_unboxed` C variants; `Nothing` singleton cache in `sprout_make0` |
