# Follow-up: represent `Char` as an immediate codepoint (not a heap string)

Status: **IMPLEMENTED** (2026-06-29, branch `feat/char-immediate-codepoint`).
Landed in two phases: Phase 1 made the AST/typed-AST char nodes carry the `Int`
codepoint (representation-neutral); Phase 2 flipped the runtime representation to
an immediate `i64` codepoint. The original proposal (captured 2026-06-28 during
the typed-codegen flip) follows for historical context.

## What landed

- Runtime: `char_from_codepoint` is the identity; `char_to_string` encodes the
  codepoint to a UTF-8 String (via `char_to_str`); `str_char_at` /
  `str_char_at_unboxed` return the decoded codepoint; the ASCII one-byte string
  cache was removed (dead).
- Both codegen backends: `'a'` lowers to an `i64` constant; `==`/`!=`/ordering
  and char patterns lower to integer `icmp`; `print`/`to_string` of a `Char`
  encode to a String first.
- Rooting: `Char` is a non-heap scalar in `type_kind`, the `codegen` duplicate,
  `capture_kind`, and `field_kinds` (`'i'`). The entire "Char rooting" class is
  gone — Char is never a managed pointer.
- Regression guard: `tests/stdlib/test_char_representation.spr` (codepoints across
  1–4 UTF-8 byte widths). GC-stress green; `ir_runtime_parity` unchanged.

## Measured impact (important)

This is a **correctness + char-op-perf** change, **not** an over-rooting/memory
win. Measured before/after (clean, same binary/method):

- `lexer.sprout` typed GC roots: 2323 → 2280 (**−1.9%**)
- `compile_driver.sprout` typed roots: 97818 → 97614 (**−0.2%**)
- whole-compiler peak RSS: unchanged within GC-threshold timing noise

The original "Why this helps → directly advances the P2 over-rooting goal" claim
below is **disproven**: the 2.76× typed-vs-direct over-rooting is structural
(per-GC-trigger push-all/pop-all bracketing — P11 option A), independent of which
types are heap, so reclassifying `Char` removes only a tiny fraction of roots.
The real over-rooting work remains options A/B in
`docs/p11-over-rooting-handoff-2026-06-28.md`. The value that *did* land:
removing the swept-multi-byte-char UAF class, integer char comparisons, and zero
per-char allocation in the lexer.

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
