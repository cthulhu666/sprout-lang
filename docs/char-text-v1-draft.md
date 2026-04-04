# Char and Text v1 Draft

This document defines the current experimental `Char` and text-semantics slice
for Sprout. It is a draft for a possible v1 feature and is not part of the
normative v0 core.

## Goals

- Introduce a distinct `Char` type for single Unicode code points.
- Make `String` indexing and length-style helpers operate on Unicode code
  points rather than UTF-8 byte offsets.
- Keep the first slice small enough to preserve implementation clarity across
  the interpreter, native backend, stdlib, and diagnostics.

## Current Experimental Contract

- `Char` is a distinct type.
- Char literals use single quotes, for example `'a'`, `'\n'`, and `'ż'`.
- A char literal must contain exactly one Unicode code point after escapes are
  decoded.
- Source string and char literals currently reject `\0`; embedded NUL code
  points are not part of the experimental contract until the native runtime can
  preserve them consistently.
- `String` values are defined in terms of Unicode code points for:
  `length`, `slice`, `take`, `drop`, `find`, `char_at`, `char_at_or`, and
  `string_chars`.
- `stdlib.string` currently exposes:
  `char_at(raw, index) -> Maybe Char`
  `char_at_or(raw, index, fallback) -> Char`
  `string_from_char(ch) -> String`
  `string_chars(raw) -> Vec Char`

## Non-Goals For This Slice

- No grapheme-cluster-aware indexing or slicing.
- No normalization guarantees.
- No locale-aware or one-to-many case mapping APIs.
- No promise yet about a broader Unicode stdlib beyond the minimal helper
  surface above.

## Notes

- The implementation currently represents `Char` runtime values with the same
  low-level UTF-8 string payload shape used for one-code-point strings, while
  preserving `Char` as a distinct source/typechecker type.
- This keeps the first slice compatible with the current interpreter and native
  runtime architecture without committing the language to that representation as
  part of the long-term contract.
