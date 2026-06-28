# Follow-up: represent `Char` as an immediate codepoint (not a heap string)

Status: **proposed**, not scheduled. Captured 2026-06-28 during the typed-codegen
flip (PR fix/typed-codegen-tco), prompted by the question "why is Char heap at
all?".

## Problem

A Sprout `Char` is currently represented as a 1-codepoint UTF-8 **heap string**
(`runtime/sprout_runtime.c` — "Char and String share the heap-string
representation"). ASCII chars are immortal cached singletons; non-ASCII chars
are freshly allocated by `char_to_str`.

Consequences of the heap representation:

- **GC rooting burden.** Every `Char` live across an allocation must be GC
  rooted, exactly like a `String`. Misclassifying `Char` as a non-heap scalar
  caused a use-after-free (a swept multi-byte char), fixed in the flip by
  correcting `type_kind` + `codegen` + `capture_kind` to treat `Char` as heap.
  That fix is correct but adds rooting churn in the hottest paths (the lexer
  touches every source char).
- **Allocation per non-ASCII char**, and **string-equality comparisons** for
  `ch == '\n'` instead of integer compares.

## Proposal

Represent `Char` as an immediate `i64` Unicode scalar value (codepoint), the way
Rust (`char` = `u32`) and Haskell (`Char` ~ `Int`) do.

- `char_from_codepoint` returns the codepoint unchanged (no allocation).
- `char_to_string` / `char_to_str` encode the codepoint to a UTF-8 heap string
  **on demand**, moving the allocation to where a string is actually needed.
- `'a'` literals lower to an `i64` constant (the codepoint), not a string-ptr.
- `ch == ch2` becomes an integer compare, not `str_eq`.
- `Char` becomes a genuine non-heap scalar — the `type_kind` predicates would
  then *correctly* return true for it, and no `Char` would ever need a GC root.

## Why this helps

- Eliminates the entire "Char rooting" bug class at the root rather than paying
  rooting cost forever. Directly advances the P2 over-rooting precision goal
  (see `docs/p11-over-rooting-handoff-2026-06-28.md`): fewer roots, not more.
- Faster lexing: no per-char allocation, integer char comparisons.

## Why it is NOT bundled into the flip

- Large runtime/ABI change: touches every `Char` builtin, char-literal lowering
  in both backends, char/string equality coercion, and any code that treats a
  `Char` as a string pointer.
- It does **not** fix the second flip blocker (the phi-operand liveness UAF in
  `take_while`), so it could not have unblocked the flip on its own.
- Mixing a representation change with the semantics-neutral flip would violate
  "keep changes small and reviewable" / "don't mix refactors with semantics
  changes" (AGENTS.md).

## Scope sketch (when scheduled)

1. Runtime: change `char_from_codepoint` to identity; make `char_to_str`/
   `char_to_string` the only allocation points; audit every `long long char_*`
   and any builtin returning/accepting `Char`.
2. Codegen (both backends): `'a'` → `i64` const; drop `is_char_type` from the
   string-coercion paths for equality/printing where it currently treats a Char
   as a string pointer; restore `Char` to the non-heap-scalar predicates.
3. Rooting: `Char` returns to scalar — remove the heap classification added in
   the flip (`type_kind`, `codegen:type_is_non_heap_scalar`, `capture_kind`).
4. Tests: char round-trip, char-in-tuple, char equality, UTF-8 boundaries; run
   the whole suite under `SPROUT_GC_STRESS=1`.
