# String Interpolation v1 — Design Doc

Status: **Experimental** — not normative until Phase 5 lands.

---

## Problem Statement

Today's Sprout codegen (`stdlib/compiler/codegen.sprout`) constructs strings via
`++` chains and calls like `emit_line(indent ++ name ++ " = " ++ rhs)`.  During a
self-compile these chains allocate and immediately discard ~300–600 MB of
intermediate `String` values because every `++` materialises a fresh heap
allocation.  The root cause is that `String` concatenation is the only surface
for building strings at the call site; there is no way to express "I want the
parts, not the final string, so a downstream consumer can flatten them in one
pass."

String interpolation with **Mechanism A** (type-directed dispatch) solves this:
a template literal produces a structured `StringTemplate` value whose parts are
preserved.  When used in an emit path that accepts `StringTemplate` directly,
no intermediate `String` allocation is needed at all; the final write to the
output buffer can flatten parts in a single pass.  When a `String` is
genuinely required, an implicit coercion `template_to_string` is inserted by
the elaborator, which lowers to a single `string_concat_many` call, eliminating
the intermediate chain.

---

## Goals

- Add a backtick-delimited template-literal surface syntax with `${expr}`
  interpolation slots.
- Produce a structured `StringTemplate` ADT that preserves literal and
  interpolated parts without forcing a `String` at the call site.
- Insert an implicit `template_to_string` coercion only when the context
  requires a `String`, lowering to `string_concat_many([...])`.
- Require a `ToString a` constraint for each interpolated slot (the class is
  specified in Phase 3 stdlib work).
- Keep plain `"..."` string literals entirely unchanged.

## Non-Goals

- Tagged / raw template literals (a la JavaScript tag functions) — not in scope.
- Multi-line template semantics beyond standard escape handling.
- Runtime template composition (concatenating two `StringTemplate` values at
  runtime) — possible later but not in scope for this iteration.
- Locale-aware `to_string` formatting.

---

## Surface Syntax

Backtick-delimited literals with `${expr}` slots.

```sprout
# Literal-only (no interpolation)
let greeting = `Hello, world!`

# Single interpolation
let msg = `Hello, ${name}!`

# Multiple interpolations and arithmetic
let result = `x = ${x}, y = ${y}, sum = ${x + y}`

# Nested template
let nested = `outer ${`inner ${val}`} end`

# Escape sequences inside a template
let raw = `backtick: \` dollar-brace: \${ newline: \n tab: \t backslash: \\`

# Empty template
let empty = ``
```

Escape rules inside a template:
- `` \` `` — literal backtick
- `\${` — literal `${`, does not open an interpolation slot
- `\n`, `\t`, `\\` — as in regular string literals
- All other backslash sequences pass through unchanged (subject to tightening
  in a later phase)

Full expressions are allowed inside `${...}`: function calls, arithmetic,
conditionals, nested templates, etc.

---

## Type Rule

A template literal has type `StringTemplate`.  `StringTemplate` is a structured
ADT (defined in stdlib in Phase 2/3):

```
# Runtime / value level (Phase 3 stdlib — NOT part of Phase 1)
type StringTemplate =
  | StringTemplate (List TemplatePart)

type TemplatePart =
  | Lit String
  | Interp String   # interpolated expr already evaluated and to_string'd
```

Each `${expr}` slot introduces a `ToString a` constraint where `a` is the
inferred type of `expr`.  The constraint is satisfied by the `ToString`
typeclass instance for `a`.

**Implicit coercion (Mechanism A):**

- When a `StringTemplate` appears in a `String`-expected context the elaborator
  inserts a coercion `template_to_string : StringTemplate -> String`.
- When a `StringTemplate` appears in a `StringTemplate`-expected context the
  parts flow through unchanged — no allocation.

This is the only implicit coercion added; the type-directed elaboration is
guided by the expected type at the use site, exactly like the existing numeric
literal handling.

---

## Lowering

When the elaborator inserts `template_to_string(t)`:

```
template_to_string(StringTemplate [Lit s0, Interp e1, Lit s2, ...])
  =>  string_concat_many([s0, to_string(e1), s2, ...])
```

`string_concat_many` is a single-pass builtin that computes the total length,
allocates once, and fills the buffer.

When the context expects `StringTemplate` the expression lowers directly to a
`StringTemplate` constructor application — no intermediate `String` is created.
The codegen `emit_line` path can then accept `StringTemplate` directly and
iterate over its `List TemplatePart` to write parts to the output buffer one by
one.

---

## Why Mechanism A

| Approach | Intermediate alloc | Parts available downstream | Complexity |
|---|---|---|---|
| Always-String | Yes — at every call site | No | Low |
| Mechanism A (this design) | Only when `String` required | Yes | Medium |
| Tagged templates | Depends on tag function | Yes | High |

Always-String eliminates the syntactic noise but not the allocation pressure.
Tagged templates add expressive power we do not need yet and complicate the
type rules significantly.  Mechanism A gives us the allocation win for the
codegen path at the cost of one new type (`StringTemplate`) and one implicit
coercion site, both of which are comprehensible and auditable.

---

## Phased Implementation Roadmap

| Phase | Scope |
|---|---|
| **1 (this doc)** | Design doc, spec patch, AST node definitions in Python `ast.py` and `stdlib/compiler/ast.sprout`. No parser/typechecker/codegen changes. |
| **2** | Python frontend: lexer tokens (`BACKTICK`, `TEMPLATE_LIT_START/MID/END`), parser production for `StringTemplateExpr`, typechecker pass to generate `ToString` constraints and insert coercions, interpreter evaluation. |
| **3** | Bootstrap Sprout frontend: mirror Phase 2 in the self-hosted parser, infer, and eval paths in `stdlib/compiler/`. Requires `ToString` typeclass and `template_to_string` / `string_concat_many` in stdlib. |
| **4** | Codegen migration: update `emit_line` and similar hot paths in `stdlib/compiler/codegen.sprout` to accept `StringTemplate` directly, bypassing `template_to_string`. Measure peak RSS during self-compile. |
| **5** | Tests and measurement: parser tests, typechecker success/failure tests, runtime behavior tests, self-compile peak-RSS regression guard. Promote from experimental to normative when gate passes. |

---

## References

- `docs/spec-v0.md` — normative spec (template literals added as experimental
  in Phase 1 spec patch)
- `stdlib/compiler/codegen.sprout` — primary motivation; current `++` chains
- `sprout/ast.py` — Python AST node `StringTemplateExpr` added in Phase 1
- `stdlib/compiler/ast.sprout` — Sprout AST mirror added in Phase 1
