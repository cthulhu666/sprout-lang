# Handoff: CPR nested-product unboxing (flatten `Maybe (A, B)` returns)

Status: **proposed, not started.** Author context: came out of review F3 (PR #100,
`fix/string-scan-cursor`), which replaced the unsafe `str_char_at_byte` with a
native-Sprout `decode_char_at : Bytes -> Int -> Maybe (Char, Int)` and measured a
**~7% slower whole-compiler self-compile**. This doc explains exactly why, and the
scoped codegen change that should recover most of it — and speed up the whole
compiler besides.

## TL;DR

CPR ("Constructed Product Result") unboxing for **regular Sprout functions
already exists and works** — it is *not* missing. The limitation is that the
width classifier flattens only the **outer** constructor: a function returning
`Maybe (Char, Int)` is classified **width 2** (`{tag, f0}`), so the `Maybe` box is
eliminated but the inner `(Char, Int)` tuple is **still heap-allocated** and `f0`
holds a pointer to it. The task: extend CPR to flatten a **nested 2-tuple
payload** into a **width-3** unboxed struct (`{tag, f0, f1}`), so
`Maybe (A, B)`-returning functions fully unbox. This recovers the F3 regression
and benefits every `Maybe (X, Int)` combinator in `parser.sprout` /
`iface_codec.sprout` (there are many).

## Verified current state (2026-06-28, on `fix/string-scan-cursor`)

Emitting IR for `decode_char_at` and inspecting it:
```
./build/compile_driver_bin_stage1 --emit-ir stdlib tests/stdlib/compiler/test_source_decode.spr
```
- `define { i64, i64 } @stdlib.compiler.source.decode_char_at_worker(...)` IS
  emitted → the worker/wrapper CPR path fires for this regular function.
- It returns `{ i64, i64 }` (**width 2**): `f0` is the `Just` payload pointer.
- `sprout_alloc_tuple_blob` still appears → the `(Char, Int)` tuple is allocated
  per call. **That per-char tuple alloc is the residual cost.**

So: `Maybe` unboxed ✓, inner tuple still boxed ✗.

## How the existing CPR machinery is wired (all in `stdlib/compiler/codegen.sprout`)

Reference by function name (line numbers drift):

- **Eligibility / width classification**
  - `unboxed_width_for_arity(max_arity)`: `1 -> Just 2`, `2 -> Just 3`, else
    `Nothing`. (Width = `tag` + `max_arity` field slots; only widths 2 and 3
    exist — see runtime structs below.)
  - `cpr_width_for_type_expr(ret_ty, arities)`: takes the **outer** ctor name of
    the return type (`type_expr_ctor_name`), looks up that ADT's max constructor
    arity in `arities`, and maps it to a width. **This is the spot that only sees
    the outer ctor.** For `Maybe (Char, Int)`: outer = `Maybe`, max arity = 1
    (`Just a`), width = 2. The fact that `a` is itself a 2-tuple is ignored.
  - `build_cpr_fns` / `build_cpr_fns_acc`: builds `cpr_fns : Dict (name -> width)`
    over BOTH externs (gated by `is_cpr_extern_allowlisted`) and **regular
    `TFnDecl`s** (eligible unless in the `mutual_tco` set). `decode_char_at` is in
    here with width 2.
- **Worker/wrapper emission** (per function, during codegen)
  - `emit_fn_worker(name, params, body, sig, width, ...)`: emits
    `define {i64,..} @<name>_worker(params) { <body compiled in worker mode> }`.
  - The reboxing **wrapper** `define i64 @<name>(params)`: calls `_worker`,
    reboxes the struct into a heap ADT, returns `i64`. Used by callers that need
    the boxed value (result escapes / not immediately matched).
  - `emit_worker_expr(width, ...)` + `emit_worker_cpr_call`,
    `emit_worker_box_unbox`, `emit_worker_match`, `emit_worker_if`,
    `emit_worker_cpr_do_sret` (width-3 sret path): compile a function body so it
    returns the unboxed struct, threading CPR through tail calls.
- **Call-site selection** (use worker when result is immediately matched)
  - `emit_match_unboxed_adt`: if the scrutinee is a call to an extern with an
    `_unboxed` variant → call `<name>_unboxed`; else if it's a regular fn in the
    sigs → call `<name>_worker`; else fall back to `emit_match_heap`.
  - `emit_match_unboxed_call` + `emit_unboxed_branches`: read `tag`/`f0`[/`f1`]
    from the returned struct via `extractvalue` and bind the match pattern's
    variables to them — **no heap `Just` allocated**.

- **Runtime structs** (`runtime/sprout_runtime.c`):
  `typedef struct { int64_t tag; int64_t f0; } SproutUnboxed2;`
  `typedef struct { int64_t tag; int64_t f0; int64_t f1; } SproutUnboxed3;`
  Width-3 returns must use the **sret ABI** (declare/call with `ptr sret(...)`),
  see `emit_extern_decls` and memory `project_cpr_width3_sret_abi`. Width 2 is a
  direct two-register return.

## The gap to close

When a CPR-eligible ADT's value-carrying constructor holds a **single field that
is itself a small tuple**, flatten it into the unboxed struct instead of boxing
it. Concretely, `Maybe (A, B)`:
- classify as **width 3** (`tag` + the tuple's 2 components), not width 2;
- worker returns `{tag, a, b}` with `a`,`b` stored directly (no
  `sprout_alloc_tuple_blob`);
- a `Just (x, y)` match binds `x <- f0`, `y <- f1` from the struct (no tuple load
  from heap);
- the reboxing wrapper, when it must box, allocates the tuple from `f0`,`f1`.

Bound to what the structs allow: only flatten when `1 (tag) + flattened-field-
count <= 2`, i.e. the payload tuple has arity ≤ 2. That exactly covers the
ubiquitous `Maybe (Node, Int)` / `Result E (A, B)` parser-combinator shape.

## Implementation plan (sketch)

1. **Classification** — `cpr_width_for_type_expr`: when the outer ctor has max
   arity 1 and that field's type is a tuple `(T1, T2)`, return width 3 **and a
   marker that the payload is a flattened 2-tuple** (not an opaque pointer). The
   function currently returns `Maybe Int`; it will likely need to return a small
   record/ADT like `CprShape { width : Int, flatten_tuple : Bool }` (or a new
   `Maybe CprKind`) so the worker/caller codegen know the f0/f1 semantics. Thread
   that through `cpr_fns` (its value type changes from `Int` to the shape).
2. **Worker emission** — `emit_fn_worker` + `emit_worker_*`: in flatten mode, a
   `Just((a, b))` in (tail) return position stores `a -> f0`, `b -> f1`,
   `tag = Just`; `Nothing` stores `tag = Nothing` (f0/f1 undef). Must handle the
   recursion (`decode2/3/4` all return the same `Maybe (Char, Int)`) so workers
   call workers — `emit_worker_cpr_call` already routes CPR tail calls; confirm it
   carries the flattened shape.
3. **Call-site extraction** — `emit_unboxed_branches`: for a `Just (x, y)`
   pattern over a flattened struct, bind `x <- extractvalue f0`,
   `y <- extractvalue f1`. Today it binds the single `Just z` field to `f0`; add
   the tuple-pattern case.
4. **Reboxing wrapper** — the `define i64 @<name>` path: when boxing a flattened
   result, allocate the `(a, b)` tuple from `f0`,`f1` then `Just(tuple)`.
5. **ABI** — width 3 ⇒ sret; reuse `emit_worker_cpr_do_sret` and the
   `emit_extern_decls` width-3 declare branch.

## Risks / correctness (read before touching codegen)

- **GC rooting is the sharp edge.** Moving the tuple's components from a heap
  object into registers changes what the collector sees across calls. The
  flattened `f0`/`f1` (a `Char` pointer and an `Int`) must be rooted exactly as
  the boxed form was. **Run `SPROUT_GC_STRESS=1` (`just test-stress`) and the
  gc-trace oracle** — a wrong rooting here is a silent use-after-free, not a
  crash (see memory `project_gc_stress_oracle`). This is the #1 way to get this
  wrong.
- **Three-site consistency.** Worker return shape, caller extraction, and rebox
  wrapper must agree on "f0/f1 are raw components" vs "f0 is a tuple pointer." A
  mismatch reads an int as a pointer (or vice versa) → memory corruption.
- **Only flatten on an exact static type.** A polymorphic / escaping / partially-
  applied use must go through the boxed wrapper. The call-site decision
  (`emit_match_unboxed_adt`, immediately-matched) already gates this; verify it
  holds for the flattened case.
- **Width-3 sret has bitten before** (`project_cpr_width3_sret_abi`); an
  automatic pass generates many more width-3 returns, multiplying that surface.
- **`mutual_tco` exclusion**: flattened workers in tail position still must not
  break TCO. Functions in the mutual-TCO set are currently excluded from CPR;
  keep that exclusion.

## Verification / done criteria

- IR: `decode_char_at_worker` returns `{ i64, i64, i64 }` (width 3) and the decode
  hot path emits **no** `sprout_alloc_tuple_blob` for the result tuple.
- Re-run the F3 benchmark (below) — expect most of the ~7% to disappear.
- Full DoD for a compiler-source change: `just check-approved-builtins`,
  `just test`, `just test-stress` (**required** — GC), `just smoke-shapes`,
  `just bundle-smoke`, `just verify-bootstrap-fixed-point`, `just
  compile-examples-stage1`, `just run-example-canary`. Refresh the seed
  (`just refresh-seed`) — pure codegen change, no builtin add/remove, so the
  bridge protocol is NOT needed this time.
- `cpr_differential_check` (`scripts/cpr_differential_check.sh`) — the
  direct-vs-typed codegen parity check; extend its allowlist only with
  justification.

## Benchmark (reuse from F3)

```
# master baseline (no checkout needed — read-only):
git show origin/master:bootstrap/compile_driver.ll > /tmp/m.ll
git show origin/master:runtime/sprout_runtime.c > /tmp/m.c
clang -O2 /tmp/m.ll /tmp/m.c -framework Security -framework CoreFoundation -o /tmp/mc
for i in 1 2 3; do /usr/bin/time -p /tmp/mc --emit-ir stdlib stdlib/compiler/compile_driver.sprout >/dev/null; done
for i in 1 2 3; do /usr/bin/time -p ./build/compile_driver_bin_stage1 --emit-ir stdlib stdlib/compiler/compile_driver.sprout >/dev/null; done
```
F3 baseline numbers (macOS arm64, under load): master min 42.1s, new min 45.1s.

## Payoff / why it's worth it

`parser.sprout` and `iface_codec.sprout` are built almost entirely from
`Just((node, next_offset))`-style combinators returning `Maybe (X, Int)`. Today
every one boxes the result tuple per step. Nested-product flattening de-allocates
that whole style across the compiler — this is a compiler-wide allocation/GC-
pressure reduction, with `decode_char_at` (review F3) as the concrete motivating
case, not a one-off.

## First steps for the next session

1. Re-confirm the current state holds (emit IR for `test_source_decode.spr`,
   check `decode_char_at_worker` is `{ i64, i64 }` and tuple is boxed).
2. Read `cpr_width_for_type_expr`, `emit_fn_worker`, `emit_worker_expr`,
   `emit_unboxed_branches`, and the rebox wrapper end-to-end before editing.
3. Decide the `cpr_fns` value-type change (Int → shape) — it ripples through all
   the above; do it first.
4. Prototype on `Maybe (A, B)` only; gate hard on `just test-stress`.
