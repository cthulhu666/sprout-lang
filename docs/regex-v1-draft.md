# Regex Support v1 Draft

Status: experimental design and implementation note. This document does not
change normative `docs/spec-v0.md`.

## Problem statement

Sprout has basic string search helpers but no compact way to express
first-match search, validation against structured text, or repeated literal
replacement.

## Goals

- Add minimal but usable regex support with good module-level ergonomics.
- Keep regex out of core syntax and `match` patterns for the first milestone.
- Preserve the existing builtin-vs-stdlib split: raw runtime hooks stay narrow,
  user-facing APIs live in `stdlib.regex`.
- Keep semantics aligned across interpreter and native execution.

## Non-goals

- Regex literals.
- Regex-aware `match` patterns.
- Capture extraction APIs.
- Named captures, lookaround, backreferences, or replacement mini-languages.

## Public API

`stdlib.regex` exposes:

- `compile(pattern: String) -> Result RegexError Regex`
- `is_match(re: Regex, text: String) -> Bool`
- `find_first(re: Regex, text: String) -> Maybe Match`
- `split_first(re: Regex, text: String) -> Maybe (String, String)`
- `replace_all_literal(re: Regex, replacement: String, text: String) -> String`
- `escape(raw: String) -> String`

`Regex` is exported opaquely so callers must construct it through `compile`.
`Match(..)` remains destructurable as `Match start end`.

## Supported syntax

This milestone supports:

- literals
- `.`
- `*`, `+`, `?`
- grouping with `(...)`
- alternation with `|`
- character classes with `[abc]`, `[^abc]`, and ranges like `[a-z]`
- anchors `^` and `$`
- escaped metacharacters
- ASCII shorthands `\d`, `\w`, and `\s`

Because regexes are supplied as ordinary `String` values, source code must also
escape the backslash for the Sprout string literal itself. For example, use
`"\\\\d+"` in source to pass `\d+` to `compile`.

This milestone rejects:

- counted repetition `{m,n}`
- non-greedy quantifiers such as `*?`
- extended `(?...)` group syntax
- backreferences

## Semantics

- Matching uses the same code-point indexing model as `stdlib.string`.
- `find_first` returns the first match only.
- `split_first` splits around the first match only.
- `replace_all_literal` treats the replacement as plain text, not as a capture
  expansion language.
- Invalid or unsupported patterns are reported at `compile` time via
  `RegexInvalidPattern` or `RegexUnsupportedFeature`.

## Implementation overview

- Raw builtins:
  `regex_validate`, `regex_is_match`, `regex_find_match`,
  `regex_replace_all_literal`, `regex_escape`
  (`regex_find_match` was named `regex_find_range` until 2026-08-19, when it
  stopped transporting its span in an `IntRange` and began returning
  `Maybe stdlib.regex.Match` directly.)
- `stdlib.regex` wraps those raw hooks and exposes the stable user-facing API.
- Raw `regex_*` builtins remain restricted to `stdlib.*` modules by surface
  checks.

## Compatibility

- No syntax changes.
- No effect-system changes.
- No changes to normative v0 until a later spec pass explicitly promotes this
  surface.
