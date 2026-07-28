# Unboxed Maybe Returns V1 — Implementation Plan

Escape analysis: local pattern optimisation for Maybe-returning C externs.
Date: 2026-05-27. Pre-implementation; advisor review pending.

> **STATUS (2026-07-09): IMPLEMENTED on the active typed path (Tier 1).** This
> draft targeted the now-retired `codegen.sprout` (direct backend). The shipped
> version lives in the active `ast_to_ir`→`ir_lowering` path instead: a new
> `IRCallUnboxed2` op + a dedicated peephole in `translate_match` (not the
> `emit_match_unboxed_maybe` sketch below). All 10 externs' `_unboxed` C variants
> already existed. Measured 6.5× on a direct-extern-match loop. The scope
> limitations below still hold verbatim — Sprout-wrapper functions (e.g.
> `mutvec_get`) and bind-then-match are NOT covered; that is **Tier 2** (CPR for
> regular Sprout functions), tracked in `BACKLOG.md` → "Sprout-IR / Model-C
> Codegen". See also `docs/cpr-nested-product-unboxing-plan-2026-06-29.md` for the
> width-3 nested-tuple follow-up.

---

## Problem

Every Maybe-returning builtin currently boxes its result as a heap object:

```c
// vector_get — today
long long vector_get(long long vec, long long index) {
  if (index < 0 || index >= v->len)
    return sprout_make0(find_ctor_tag_by_name("Nothing")); // malloc + GC register
  return sprout_make1(find_ctor_tag_by_name("Just"), v->data[index]); // malloc + GC register
}
```

The emitted LLVM IR for `match vector_get(v, i) with | Just x -> f(x) | Nothing -> 0`:

```llvm
%r   = call i64 @vector_get(i64 %v, i64 %i)      ; heap alloc + GC register
%tag = call i64 @sprout_tag(i64 %r)               ; deref heap object to read tag
%eq  = icmp eq i64 %tag, <just_tag>
br i1 %eq, label %just_arm, label %nothing_arm
; just_arm:
%x   = call i64 @sprout_field(i64 %r, i64 0)      ; deref heap object to read field
```

At N=12 in the N-queens benchmark this executes millions of times.
The `Just` object is never stored — it is created and consumed within the same
expression. The heap allocation and GC registration are pure overhead.

---

## Scope of this implementation

**What this covers**: `TMatch(TCall(extern_returning_maybe, args))` where the
match has only simple toplevel patterns (`Nothing`, `Just <var_or_wildcard>`,
or `_`). The scrutinee must be a direct call to a C extern that returns
`Maybe T`.

**What this does NOT cover**:
- `let r = vector_get(v, i); match r with ...` — the value was already boxed
  when bound to `r`; this plan does not optimize that case.
- `match vec_get(i, v) with ...` where `vec_get` is a Sprout wrapper around
  `vector_get` — the outer call is not a C extern; the inner `vector_get` call
  within `vec_get`'s body still benefits, but `vec_get` itself still boxes its
  return value.
- `Just (Cons h t)` — nested patterns inside Just arms (fall back to heap
  path; nested patterns on the payload are handled by the existing branch
  machinery after the value is extracted).
- `Result`-returning externs — natural follow-on; same mechanics, `{ i64, i64
  }` covers one-field arms (`Ok a`, `Err b`).

---

## Target externs

All 11 `Maybe`-returning C externs in `prelude.sprout`:

| Extern | Declared at | Payload |
|--------|-------------|---------|
| `vector_get` | `prelude.sprout:890` | element from vector data |
| `map_get` | `prelude.sprout:897` | BSTNode value field |
| `map_nth_key` | `prelude.sprout:901` | interned key string |
| `map_nth_value` | `prelude.sprout:902` | BSTNode value field |
| `bytes_get` | `prelude.sprout:914` | byte as Int |
| `argv_get` | `prelude.sprout:856` | argv string pointer |
| `env_get` | `prelude.sprout:857` | env string pointer |
| `str_char_at` | `prelude.sprout:876` | char string pointer |
| `str_char_at_byte` | `prelude.sprout:881` | char string pointer |
| `regex_find_range` | `prelude.sprout:928` | IntRangeVal* (still allocates payload) |
| `term_read_line` | `prelude.sprout:942` | string from readline |

`regex_find_range` and `term_read_line` still allocate their payload objects
(an `IntRangeVal*` and a string respectively); the optimisation eliminates the
`Just` wrapper heap object, not the payload.

---

## Part 1 — C runtime changes (`runtime/sprout_runtime.c`)

### 1a. New typedef

After the existing type definitions (around line 100):

```c
/* Unboxed Maybe: returned as a two-register struct instead of a heap object.
 * tag == cached_tag_just()  → Just, value holds the payload.
 * tag == cached_tag_nothing() → Nothing, value is 0.
 * ABI: on ARM64 and x86-64 System V, { int64_t, int64_t } is returned in
 * two integer registers (x0:x1 / rax:rdx), matching LLVM { i64, i64 }. */
typedef struct { int64_t tag; int64_t value; } SproutMaybe;
```

### 1b. Cached Nothing tag

`cached_tag_just()` already exists (around line 3540). Add the symmetric:

```c
static long long g_cached_tag_nothing = 0;
static long long cached_tag_nothing(void) {
  if (g_cached_tag_nothing == 0)
    g_cached_tag_nothing = find_ctor_tag_by_name("Nothing");
  return g_cached_tag_nothing;
}
```

### 1c. `_unboxed` variants

For each of the 11 externs, add a new function named `foo_unboxed` that takes
the same parameters and returns `SproutMaybe`. The existing `foo` functions are
NOT removed — they are still needed for the non-optimised path (where the
Maybe value is bound to a variable or escapes).

Example — `vector_get_unboxed`:

```c
SproutMaybe vector_get_unboxed(long long vec, long long index) {
  VectorVal* v = (VectorVal*)(uintptr_t)vec;
  if (v == NULL || index < 0 || index >= v->len)
    return (SproutMaybe){cached_tag_nothing(), 0};
  return (SproutMaybe){cached_tag_just(), v->data[index]};
}
```

No `SPROUT_GC_PUSH_I64_LOCAL` / GC rooting required: no allocation occurs, so
GC cannot be triggered.

For functions that do allocate (`regex_find_range_unboxed`,
`term_read_line_unboxed`), the payload allocation still occurs; the GC rooting
conventions follow the existing function exactly, except the `sprout_make1`
wrapping step is omitted:

```c
SproutMaybe regex_find_range_unboxed(const char* pattern, const char* text) {
  // ... same as regex_find_range up to the successful regexec ...
  IntRangeVal* range = sprout_alloc_range_val("regex_find_range: out of memory");
  range->start = ...;
  range->end   = ...;
  SPROUT_HANDLE(h_range, (long long)(uintptr_t)range);
  // No sprout_make1 — return struct directly
  return (SproutMaybe){cached_tag_just(), sprout_handle_get(h_range)};
}
```

GC safety note for allocating variants: `sprout_alloc_range_val` calls
`sprout_gc_maybe_collect_threshold()`. The input string pointers (`pattern`,
`text`) are already consumed by `regexec` before the allocation, so they do
not need rooting. This is unchanged from the existing function.

---

## Part 2 — Codegen changes (`stdlib/compiler/codegen.sprout`)

### 2a. Predicate: is this call unboxed-eligible?

Add after the existing `is_maybe_type` / `is_result_type` predicates
(around line 249):

```sprout
# True when e is a direct TCall to a Maybe-returning C extern.
# Only direct calls are eligible; indirect calls (closures, local vars) are not.
fn is_unboxed_maybe_call(e: typed_ast.TypedExpr, ctx: CgCtx) -> Bool =
  match e with
  | typed_ast.TCall (typed_ast.TVar name _ _) _ call_ty _ ->
      is_maybe_type(call_ty) &&
      (dict_get(strip_module_prefix(name), ctx_extern_sigs(ctx)) != Nothing)
  | _ -> false
```

`strip_module_prefix` already exists in the codebase; `ctx_extern_sigs` is at
line 1572.

### 2b. Predicate: are all branches simple enough for the fast path?

```sprout
# True when every branch has a toplevel pattern that is either:
#   Nothing (zero-arg ctor), Just <var_or_wildcard> (one-arg ctor), or wildcard/var.
# Nested patterns inside Just arms fall back to the heap path.
fn all_branches_simple_maybe(branches: List typed_ast.TypedMatchBranch) -> Bool =
  match branches with
  | Nil -> true
  | Cons (typed_ast.TypedMatchBranch pat _) rest ->
      is_simple_maybe_pat(pat) && all_branches_simple_maybe(rest)

fn is_simple_maybe_pat(pat: ast.Pattern) -> Bool =
  match pat with
  | ast.WildcardPattern _                          -> true
  | ast.VarPattern _ _                             -> true
  | ast.ConstructorPattern "Nothing" Nil _         -> true
  | ast.ConstructorPattern _ Nil _                 -> true  # any zero-arg ctor
  | ast.ConstructorPattern _ (Cons p Nil) _ ->
      match p with
      | ast.WildcardPattern _ -> true
      | ast.VarPattern _ _    -> true
      | _                     -> false
  | _ -> false
```

### 2c. Modify `emit_match` to route to the fast path

Current `emit_match` (line 2079):

```sprout
fn emit_match(scrut, branches, ty, ctx, locals, em, tco_ctx) =
  do
    sv <- emit_expr(ctx, locals, em, Nothing, scrut)
    ...
```

New routing logic (insert before `sv <- emit_expr`):

```sprout
fn emit_match(scrut, branches, ty, ctx, locals, em, tco_ctx) =
  if is_unboxed_maybe_call(scrut, ctx) && all_branches_simple_maybe(branches) then
    emit_match_unboxed_maybe(scrut, branches, ty, ctx, locals, em, tco_ctx)
  else
    do
      sv <- emit_expr(ctx, locals, em, Nothing, scrut)
      ...   # existing code unchanged
```

### 2d. New function `emit_match_unboxed_maybe`

This function handles the entire fast path. It does NOT call the existing
`emit_branches`, `emit_ctor_pattern_test`, or `emit_ctor_field`.

Key design: the unboxed call returns `{ i64, i64 }`. We use the existing
`{ i64, i64 }` LLVM struct return syntax — no `declare` statement is required;
LLVM infers the signature from the call instruction.

```
Emitted IR structure (before vs after):

BEFORE:
  %r   = call i64 @vector_get(i64 %v, i64 %i)
  %tag = call i64 @sprout_tag(i64 %r)
  %eq  = icmp eq i64 %tag, <just_tag>
  br i1 %eq, label %just_arm, label %nothing_arm
just_arm:
  %x   = call i64 @sprout_field(i64 %r, i64 0)
  ...

AFTER:
  %s   = call { i64, i64 } @vector_get_unboxed(i64 %v, i64 %i)
  %tag = extractvalue { i64, i64 } %s, 0
  %val = extractvalue { i64, i64 } %s, 1
  %eq  = icmp eq i64 %tag, <just_tag>
  br i1 %eq, label %just_arm, label %nothing_arm
just_arm:
  # %val already in register — no heap deref needed
  ...
```

`<just_tag>` is a compile-time integer: `ctor_tag(just_ctor)`. This matches
`cached_tag_just()` in the C runtime because both are set from the same
`@sprout_register_ctor` call emitted by codegen.

Pseudocode for `emit_match_unboxed_maybe`:

```sprout
fn emit_match_unboxed_maybe(scrut, branches, ty, ctx, locals, em, tco_ctx) =
  do
    # scrut is TCall(TVar name, call_args, _, _) — guaranteed by predicate
    let (call_name, call_args, call_ty) = extract_call_info(scrut)
    let base_name = strip_module_prefix(call_name)

    # Look up original FnSig for argument coercion
    ext_sig <- match dict_get(base_name, ctx_extern_sigs(ctx)) with
               | Just s -> s
               | Nothing -> ... # unreachable; predicate already checked

    # Emit the unboxed call
    (arg_vals, rooted_args) <- emit_args_with_roots(call_args, ctx, locals, em)
    coerced <- coerce_args(fn_params(ext_sig), arg_vals, em)
    let args_ir = join_comma(list_map(format_ll_ir, coerced))
    struct_tmp <- fresh_tmp(em)
    emit_line(em, "  " ++ struct_tmp ++ " = call { i64, i64 } @"
                      ++ base_name ++ "_unboxed(" ++ args_ir ++ ")")
    pop_temp_roots(rooted_args, em)

    # Extract tag and payload
    tag_tmp <- fresh_tmp(em)
    emit_template(em, `  ${tag_tmp} = extractvalue { i64, i64 } ${struct_tmp}, 0`)
    val_tmp <- fresh_tmp(em)
    emit_template(em, `  ${val_tmp} = extractvalue { i64, i64 } ${struct_tmp}, 1`)
    let extracted_val = val_make(ll_i64(), val_tmp)

    # Emit branch structure
    let out_ll = match tco_ctx with | Nothing -> type_to_ll(ty) | Just tc -> tco_ret_ll(tc)
    done_lbl <- fresh_block(em, "match_done")
    branch_results <- emit_unboxed_maybe_branches(
      branches, tag_tmp, extracted_val, out_ll,
      ctx, locals, em, tco_ctx, done_lbl, Nil)

    if list_is_nil_branch_vals(branch_results) then
      val_for_ll(out_ll, "undef")
    else
      do
        emit_label(em, done_lbl)
        phi <- fresh_tmp(em)
        let parts = join_comma(...)
        emit_template(em, `  ${phi} = phi ${out_ll} ${parts}`)
        val_for_ll(out_ll, phi)
```

### 2e. `emit_unboxed_maybe_branches`

Iterates branches. For each branch:

- **`ConstructorPattern("Nothing", [], _)`** or any zero-arg ctor:
  - Get `ctor_tag` for the pattern's constructor from `ctx_ctor_sigs`
  - Emit `icmp eq i64 %tag_tmp, <ctor_tag>` 
  - Conditional branch to body block / next branch
  - No binding

- **`ConstructorPattern("Just", [VarPattern(name, _)], _)`** or `Just _`:
  - Get `ctor_tag` for `Just`
  - Emit `icmp eq i64 %tag_tmp, <just_ctor_tag>`
  - Conditional branch to body block / next branch
  - Bind `name` → `extracted_val` in body locals (no GC rooting needed — it's
    in a register; it will be rooted by the existing body expression machinery
    if it participates in an allocating call)

- **`WildcardPattern _`** or **`VarPattern(name, _)`**:
  - Unconditional branch to body
  - For `VarPattern`: bind `name` → `extracted_val`

The body of each arm is emitted via the existing `emit_expr`. The extracted
`val_make(ll_i64(), val_tmp)` value flows directly into the arm body locals.

### 2f. GC rooting of `extracted_val`

`extracted_val` is a register-held `i64` that may be a heap pointer (e.g.,
the element from `vector_get` could itself be a GC-tracked object). It is not
separately rooted before the arm body executes.

This is correct for the same reason the existing scrutinee result is not
double-rooted: the caller (`emit_expr` for the scrutinee) would root it if
needed, and within the arm body the `emit_expr` for sub-expressions roots
their own temporaries. `extracted_val` is only live until first use in the
arm body, which is usually as an argument to a subsequent call or a direct
return.

The one unsafe case: if the arm body evaluates multiple allocating expressions
before using `extracted_val`. In this case the extracted value could be freed
before use. **Conservative fix**: push `extracted_val` as a temp root at the
start of each Just-arm body (one `push_temp_root` per arm), pop it before
leaving the arm. This is cheap (one runtime call, stack slot) and safe.

The implementation should use this conservative approach unless profiling shows
it matters.

---

## Part 3 — What does NOT change

- `emit_branches` — not touched
- `emit_ctor_pattern_test` / `emit_ctor_field` — not touched
- `emit_pattern_bind` / `emit_ctor_pattern_bind` — not touched
- All existing `vector_get`, `map_get`, etc. C functions — still present
- All existing call sites where Maybe value is bound to a variable — unchanged

---

## Key codegen reference points

| Site | File | Line | Purpose |
|------|------|------|---------|
| `is_maybe_type` | `codegen.sprout` | 250 | Type predicate (already exists) |
| `ctx_extern_sigs` | `codegen.sprout` | 1572 | Extern lookup dict |
| `emit_match` entry | `codegen.sprout` | 2079 | Where routing guard goes |
| `emit_regular_direct_call` | `codegen.sprout` | 2456 | Model for arg emission |
| `emit_ctor_pattern_test` | `codegen.sprout` | 1082 | Shows tag comparison pattern |
| `emit_ctor_field` | `codegen.sprout` | 1136 | Shows field extraction pattern |
| `strip_module_prefix` | `codegen.sprout` | (grep) | Used for extern name lookup |
| `vector_get` C impl | `sprout_runtime.c` | 5200 | Primary target |
| `cached_tag_just()` | `sprout_runtime.c` | ~3540 | Model for `cached_tag_nothing` |

---

## Expected impact

**N-queens N=12**: The `vector_get` call in `is_free` fires millions of times
per second. Eliminating `sprout_make1` + `register_managed_ptr` from each call
removes the main source of the 29B instruction count. Expected: **3–5× faster**
(from ~1,620 ms toward 300–500 ms range, approaching Go-mutable territory).

**Compiler self-compile**: `str_char_at_byte` is on the lexer hot path — every
character of every source file goes through it. Speedup will be proportional
to the fraction of time in `str_char_at_byte` within the full compile.

**Long-running servers**: any tight loop over a `Dict` or `Vector` benefits.

---

## Limitations

1. Sprout wrapper functions (e.g., `vec_get`, `dict_get`) are not eligible —
   they are not C externs. Callers of `vec_get` that immediately match the
   result still pay for the `Just` allocation inside `vec_get`. The fix is
   either inlining Sprout wrappers or extending to Sprout-defined
   Maybe-returning functions (requires CPR analysis — see
   `docs/archive/generational-gc-v1-draft.md` Option 2 discussion).

2. `let r = vector_get(v, i)` before the match is not optimised. The compiler
   sees a `TVar` scrutinee, not a `TCall`.

3. `Result`-returning externs are not included in V1 but follow the same
   pattern identically.

---

## Implementation steps

1. Add `SproutMaybe` typedef and `cached_tag_nothing()` to runtime
2. Add all 11 `_unboxed` C variants, placing them immediately after the
   corresponding original function for readability
3. Add `is_unboxed_maybe_call` and `all_branches_simple_maybe` predicates in
   `codegen.sprout`
4. Add `emit_match_unboxed_maybe` and `emit_unboxed_maybe_branches`
5. Add the routing guard to `emit_match`
6. Rebuild stage-1 binary, run `bench/nqueens/bench.sh`, confirm speedup
7. Run `just compile-examples-stage1` to check no regressions
8. Run test suite (`mise exec -- just test`)

---

## Branch

`perf/unboxed-maybe` (to be created).
