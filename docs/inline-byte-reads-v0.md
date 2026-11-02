# Inline byte reads — v0

Status: design, 2026-11-02. BACKLOG P2 "Inline `Bytes` and `String` byte reads the way B1 inlines
`Vector Double`". Not a language change: no syntax, typing or spec text moves.

## 1. Problem

`bytes_get` and `str_byte_at` in a `match`, a `do` bind, a list-pattern split or an argument to a
function that matches on it (`@f_argw`) take the unboxed path: one
`call i128 @bytes_get_unboxed` / `@str_byte_at_unboxed` per byte. Under the whole-program link
LLVM inlines the C body; under separate units (`just test`, the bootstrap, any user build that
links `runtime/*.c` beside the program) every read is a full call. Measured in
`bench/results-2026-11-02-str-byte-at.md`: a Sprout `str_len` is 1.4× C whole-program and 4.3×
separate units, and `docs/byte-buffer-v0.md` §3 puts a base64 loop at 6.5× C with a call per byte
and 2.6× with reads and writes inlined by hand.

Second defect, same path: `emit_unboxed_call` gives the payload `IRTUnknown`, so `ir_rooting`
treats an `Int` byte as a heap value and roots it across any later allocation in the arm.

## 2. Goals and non-goals

Goals:
- The unboxed `bytes_get` and `str_byte_at` reads lower to inline loads, with the same results and
  the same failure messages as the C bodies.
- The payload of every unboxed `Maybe` extern carries its real kind, so a scalar is never rooted.
- No slower under the whole-program link; faster under separate units.

Non-goals:
- Alias tags (TBAA). Without them LLVM cannot hoist a header load past a store in the loop body;
  `docs/byte-buffer-v0.md` §3 prices that separately.
- The boxed `bytes_get` / `str_byte_at` (a `Maybe` stored or passed on). They stay C calls.
- Moving callers onto `str_byte_at` (its own BACKLOG entry).

## 3. Prior art

Compiler IRs read runtime objects through a generic load with a width, not one op per object
layout:
- OCaml's Cmm: `Cload { memory_chunk; ... }`, where `memory_chunk` names the width and signedness
  (`Byte_unsigned`, `Word_int`, ...) and separates `Word_int` ("integer or pointer outside heap")
  from `Word_val` ("pointer inside heap") for the GC (`asmcomp/cmm.mli`).
- Cranelift: `load MemFlags, p, Offset` and `uload8 MemFlags, p, Offset` (`ir::Opcode`).

B1 (`IRVecLenD`, `IRVecGetD`) took the other road, one op per layout. Two generic ops cost two arms
in each exhaustive match instead of four, and the Sprout runtime work in
`docs/native-sprout-runtime-v0.md` can reuse them.

## 4. Design

### 4.1 Two IR ops (`sprout_ir.sprout`)

- `IRLoadWord r base offset kind` — the `i64` at `base + offset` bytes. `offset` is a literal and
  may be negative (`-8` reads a String header). `kind` is the result's `IRType`, as `IRVecGetU`
  carries one: `IRTScalar` for a length, a header word or a pointer outside the GC heap, which
  `ir_rooting` then never roots.
- `IRLoadU8 r base index` — the byte at `base + index`, zero-extended to `i64`. Always scalar.

Both are unchecked and non-allocating. Lowering (`ir_lowering.lower_op`): `inttoptr`, a
`getelementptr i8`, then `load i64` (align 8), or `load i8` and `zext`.

Arms: the seven exhaustive matches — `ir_rooting.op_triggers_gc`, `op_produces_simple_heap`
(`IRLoadU8` → `Nothing`; `IRLoadWord` → by `kind`), `op_uses` (the base and index stay live),
`op_exposes_operands`, `sprout_ir.op_direct_callee`, `sprout_ir.print_op`,
`ir_lowering.lower_op` — plus an explicit `ir_rooting.op_def` arm, which has a `_` fallback but
needs the arm for liveness. `ir_verify` reuses `op_def` and `op_uses`.

### 4.2 The inline reads (`ast_to_ir.sprout`)

Four sites emit an unboxed read. Three (`translate_unboxed_maybe_match`,
`translate_do_bind_unboxed`, `translate_split_elem`) go through `emit_unboxed_call`; the fourth,
`translate_argw_call`, builds its own `IRCallUnboxed2` for an `ExternProducer` — the
`step(acc, str_byte_at(s, i))` loop shape. One emitter, `emit_inline_byte_read`, serves both:
`emit_unboxed_call` and `translate_argw_call` call it for these two names and keep the
`IRCallUnboxed2` for every other. Its `(tag, val)` feeds `@f_argw` unchanged. The blocks follow
`vec_guard`:

```
Bytes b, index i:                         String s, index i:
  b == 0      -> panic "bytes_get_unboxed:   s == 0          -> panic "str_byte_at: null input"
                 null bytes"               h    = IRLoadWord s -8 Scalar
  len = IRLoadWord b 0 Scalar              h & 255 != 10   -> panic "str_byte_at: String has
  i >=u len   -> miss                                          no header"
  data = IRLoadWord b 8 Scalar             len  = IRILShr h 14   (logical, as C's uint64_t)
  byte = IRLoadU8 data i  -> hit           i >=u len       -> miss
                                           byte = IRLoadU8 s i  -> hit
hit:  tag = Just, val = byte
miss: tag = Nothing, val = 0
join: tag = phi, val = phi                (the tuple emit_unboxed_call returns today)
```

- One unsigned compare covers `i < 0`, as in the C bodies and `vec_guard`.
- Failure is `IRPanic`, which calls `panic`, which calls `sprout_fail`: the message and exit
  status match the C path exactly.
- The tag values come from `prelude_ctor_tag`. `255`, `10` (`SPROUT_HEAP_CSTR`) and `14` (the aux
  shift) join `ir_lowering.hdr_aux_unit` as named constants beside a "SYNC WITH" note, as
  `hdr_aux_unit` has one.

Premises, each with a test or a gate:
- The GC never moves an object, so a loaded `data` pointer stays valid for the read.
- No op between the `data` load and the byte load allocates, so the Bytes cannot be freed in
  between. They are emitted adjacent. `op_triggers_gc` is not exported, so the test is end to end
  through `insert_roots`: a heap value live across an inline read gets no root push (the shape of
  `probe_live_string` in `test_ir_codegen_cpr_maybe_externs.spr`), and a mirror across an
  allocating extern does.
- `data` is NULL when `len` is 0; the bounds check runs first, so it is never dereferenced
  (an empty-Bytes read test).
- A String's header aux is its byte length (HDRCHECK checks this on CI).

### 4.3 The payload kind

The rule: the payload is `IRTScalar` only when the producer's type is `Maybe T` (as
`maybe_unboxable` checks), `Just`'s field is the type parameter (`field_kinds` is `"_"`, which a
program-defined `type Maybe a = | Nothing | Just Box` does not satisfy), and
`type_kind.scalar_of_type(T)` is `Just`. Every other width-2 type keeps `IRTUnknown`. That includes
`Result`, because the `do`-bind short path rebuilds `Err(val)` with an allocation and an `Err
String` payload must stay rooted across it. Taking "the last type argument" would get this wrong.

The kind comes from the producer's type, and the routers compute it where they pick the target:
- `unboxed_maybe_match_target` returns `(ufn, args, kind)`, and so does `do_bind_unbox_target`;
  `translate_split_elem` uses the first.
- `ArgwSite` stores `payload_kind`, computed in `argw_shape`.
- `emit_unboxed_call` and `translate_argw_call` take the kind as an argument.
- `emit_argw_fn` declares `@f_argw`'s `$val` parameter with the kind of the scrutinee's type, and
  `params_heap_origin` seeds rooting from it. The scrutinee's type is the instantiated parameter
  type, not the declared one.

`bytes_get`, `str_byte_at` (`Int`) and `str_char_at` (`Char`) become scalar everywhere;
`vector_get` and the `map_*` reads, and `Maybe`-returning workers, when `T` is scalar. The inline
reads' `val` phi joins two scalars and is never rooted.

## 5. Staging

1. ~~**Payload kind.**~~ Landed (§4.3). `tests/stdlib/test_ir_unboxed_payload_kind.spr`: an `Int`
   payload live across an allocation gets no root push at a `match`, a `do` bind, a split and an
   `@f_argw` parameter; a `Vector String` payload, a `Result String Int` worker in a `do` bind,
   and a phantom `Maybe` whose `Just` holds a heap type stay rooted.
2. **Inline reads.** §4.1 and §4.2, with:
   - IR tests: a `match`, a `do` bind, a split and an argw call over each read emit no
     `call i128` to it, emit the loads, and keep both panic messages.
   - `ir_verify` unit tests for the two ops (`op_def`, `op_uses` are exported); rooting through
     `insert_roots` as in §4.2.
   - `tests/stdlib/test_ir_codegen_cpr_maybe_externs.spr` expects loads for these two names.
     `scripts/ctor_tag_fold_check.sh` check (a) keys on a `call @..._unboxed` in the fixture and
     would go blind; for these two it instead requires a `load i8` and no `call i128` to them.
   - Behaviour: the existing `test_str_byte_at` and `test_bytes_byte_at_unboxed`, plus empty
     Bytes and a split, run under HDRCHECK.
   - Bench: `bench/str_byte_at` plus a `bytes_get` sum row, both link setups, ≥15 interleaved
     rounds against master; no slower whole-program.

Each stage is one PR, reseeded, with the golden IR diff read. Both `_unboxed` C bodies stay:
the boxed `str_byte_at` wraps `str_byte_at_unboxed` (the boxed `bytes_get` has its own body), a
function value or an unlisted path may still call them, and `just cpr-declare-check` pins their
declares.

## 6. Compatibility and docs

No source change. Emitted IR changes at every unboxed read of these two names; 10 golden files
call them today.

Docs updated with stage 2: `docs/compiler-internals.md` (a pointer outside the GC heap as a scalar
SSA value, and the adjacency premise), `docs/string-byte-read-v0.md` §4 "Link setups" (it points at
the BACKLOG entry this work closes), and the comment over
`ir_rooting.is_nonallocating_unboxed_read`.
Stage 1 updates that comment's note on payload kinds.
