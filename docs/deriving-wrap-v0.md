# Deriving on wraps (v0)

Status: normative behaviour described in `docs/spec-v0.md` §8.6 and §5.6.1; this
doc is the design rationale and the codegen fix that landed alongside.

## Problem

`deriving (...)` was scoped to `type` declarations and records. `wrap Line = Int
deriving (Eq, Ord, ToString)` was a **parse error** at the `deriving` keyword:
`parse_wrap_decl` never called `parse_deriving_clause`, and `ast.WrapDecl` had no
field to store the result. `spec-v0.md` §5.6.1 stated the restriction outright
("A `wrap` cannot derive typeclasses").

The cost was not confined to wraps. A record containing wrap fields could not
derive either, because the record's synthesized `eq` calls `eq(left.line,
right.line)` and no path existed to produce an `Eq Line` witness:

```
wrap Line = Int
wrap Col = Int
type Caret = (line: Line, col: Col) deriving (Eq, ToString)
→ ERROR: check: No instance of Eq for Line in instance method eq
```

So the guideline "use `wrap` for semantic distinctions on shared
representations" (`docs/guidelines.md` #7) was in direct tension with using
`deriving` anywhere upstream of a wrap. Hand-writing `Eq`/`Ord`/`ToString` per
wrap is pure boilerplate — a wrap has exactly one field.

## Goals / non-goals

- **Goal:** `deriving (Eq, Ord, ToString)` on `wrap` declarations.
- **Non-goal:** `Enum` — `from_ordinal` must *construct* a value, and a wrap's
  payload cannot be rebuilt from an `Int` alone. Rejected eagerly at the
  deriving site.
- **Non-goal:** instance *lifting* — reusing the inner type's instances so that
  `Num`, `+`, `++` etc. work on the wrap. Still open in `BACKLOG.md`.

## Decision: structural rendering, not lifted

`Eq` and `Ord` are observationally identical whether derived structurally or by
delegating to the inner type — a wrap has one field, so both reduce to the same
comparison. The choice only bites `ToString`, and it bites transitively through
every containing type:

| | `to_string(Line(3))` | `to_string(Caret(...))` |
|---|---|---|
| structural | `Line(3)` | `Caret(line = Line(4), col = Col(7))` |
| lifted | `3` | `Caret(line = 4, col = 7)` |

**Structural was chosen.** Rationale:

- It keeps a `wrap` visible in output. Lifted `ToString` makes a wrap
  indistinguishable from a `type alias` in every log line and error message,
  erasing exactly the distinction `wrap` exists to draw (§5.6.1).
- It requires no new emitters: a wrap *is* a single-constructor,
  single-positional-field product, so the ADT emitters apply unchanged.
- Deriving then requires the inner type to be a class member, which is a real
  error rather than a silent fallback to the representation.

### Prior art

Verified against primary sources.

| Language | Plain derive on a newtype/wrapper |
|---|---|
| Haskell | `Eq`/`Ord`/`Ix`/`Bounded` always use the **newtype** strategy, even without `GeneralizedNewtypeDeriving`; every other stock class, `Show` included, resolves to **stock**. Derived `show` "contains only the constructor names defined in the data type" — so `Line 3`. ([deriving strategies](https://ghc.gitlab.haskell.org/ghc/doc/users_guide/exts/deriving_strategies.html), [Haskell 2010 report §11](https://www.haskell.org/onlinereport/haskell2010/haskellch11.html)) |
| Rust | `#[derive(Debug)]` on `struct Line(i32)` renders `Line(3)`; derive never forwards to the inner field. ([`std::fmt::Debug`](https://doc.rust-lang.org/std/fmt/trait.Debug.html)) |
| Swift | Synthesizes `Equatable`/`Hashable` only, gated on "all of the types of all of its stored properties conform to P". `CustomStringConvertible` is **not** synthesized. ([SE-0185](https://github.com/swiftlang/swift-evolution/blob/main/proposals/0185-synthesize-equatable-hashable.md)) |
| Scala 3 | Opaque types get nothing automatically; instances and operations are hand-written via `extension`. ([opaque types](https://docs.scala-lang.org/scala3/book/types-opaque-types.html)) |

Haskell is the closest analogue and splits exactly where Sprout now does: the
comparison classes delegate (indistinguishable at one field), the rendering class
is structural. Notably no surveyed language auto-delegates its rendering class
under a plain deriving clause.

## Syntax

**Trailing**, after the right-hand side, as on records — `wrap Name = T` is a
self-contained RHS, unlike an ADT's `=` which opens a multi-line constructor
list. `deriving` is a hard keyword, so it cannot be mistaken for a further type
argument in `wrap Env = Dict Int deriving (Eq)`.

```sprout
wrap Line = Int deriving (Eq, Ord, ToString)
```

## Synthesized bodies

No new emitters. `expand_deriving_decls` reconstructs the wrap's implicit
constructor as `ast.TypeConstructor(name, [inner], Nil, Nil)` and hands it to the
existing ADT path (`derive_instances_for_classes`) with `Nil` type parameters — a
wrap takes none in v0. `Eq`/`Ord` compare the single inner value; `ToString`
renders `Line(3)`, positional rather than a record's `Line(v = 3)`.

Validation gets its own arm rather than reusing the ADT validator, for one
reason: the ADT `Enum` rejection reads "constructor `Line` has fields", naming a
constructor the user never wrote. Wraps say "a wrap always carries one payload,
not an enumeration".

Implementation: `stdlib/compiler/deriving.sprout` (§Wrap support + wrap arms in
`expand_deriving_decls` / `validate_one_deriving_decl`). `WrapDecl` gained a
`(List String) deriving_classes` field, rippling through the parser,
`iface_codec` encode/decode, the bundler qualification pass, and the (mostly
wildcard) match sites across the compiler — the same ripple `RecordDecl` took,
see `docs/deriving-records-v0.md`. No interface-format version bump: `IfaceFile`
carries no decls, so `encode_ast_decl` is exercised only by the round-trip test.

## A nested-pattern codegen bug (pre-existing)

Landing this surfaced a **pre-existing** miscompile, verified reachable from
hand-written code with no deriving involved. Deriving `Eq` was simply the first
thing to generate the triggering shape — a tuple of two wrap constructor
patterns:

```sprout
match (left, right) with
| (Line l0, Line r0) -> eq(l0, r0)
```

A wrap value is unboxed: it *is* its payload, carries tag sentinel `-1`, and has
no header. `translate_match_arm_ctor` honours that at the **top level** of a
match — it skips the tag hoist and binds the inner arg by identity. But the two
nested binders, `bind_tuple_items` and `bind_ctor_field_args`, emitted an
unconditional `IRGetTag` + tag comparison for any nested `ConstructorPattern`,
with no `tag == -1` check at all. The bug had two faces:

- **wrap over an unboxed type** (`wrap Line = Int`): `sprout_tag` dereferenced
  the raw integer as a pointer → SIGSEGV.
- **wrap over a heap type** (`wrap Name = String`): the tag read succeeded but
  returned the payload's own tag, which never equals `-1`, so the arm **silently
  missed** and control fell through to `runtime error: non-exhaustive match`.

The second face is the worse one, and it is why the regression test asserts
values rather than mere liveness: a fix that only stopped the crash could still
take the wrong arm.

Fix: both binders now test `nested_tag == -1` and, for a wrap, fetch the
element/field and bind the single inner arg to it directly via the existing
`bind_wrap_inner_arg` — no tag test, no branch, and the current block stays open,
because a wrap has one constructor and cannot fail to match. Boxed ADTs are
untouched (the gate is strictly `tag == -1`), which the regression test pins with
a two-variant ADT nested in a tuple beside a wrap.

The do-bind paths (`do_bind_captures`, `do_bind_tuple_items`) were never affected
— they reject nested constructor patterns with a clear `Err` rather than
miscompiling them.

Note: comments in `ast_to_ir.sprout` reference a `codegen.sprout` "ctor_is_wrap
path" for the mirror behaviour. That file no longer exists; the references are
stale.

## Tests

- `tests/stdlib/test_deriving_wrap.spr` — Eq/Ord/ToString on wraps over `Int` and
  `String`, two distinct wraps over one representation, a wrap with no clause,
  and a record of wraps deriving through them (the motivating case).
- `tests/stdlib/test_wrap_nested_patterns.spr` — the codegen regression: wrap
  patterns nested in tuples, in ADT constructor fields, and in both at once;
  unboxed and heap payloads; plus a boxed ADT that must keep its tag test.
- `tests/conformance/type_error/deriving_enum_on_wrap.{spr,err}` — `Enum`
  rejection with wrap-specific wording.
- `tests/conformance/type_error/deriving_wrap_inner_lacks_instance.{spr,err}` —
  structural derivation requires an inner-type instance.
- `tests/stdlib/compiler/test_parser.spr` — clause placement, multi-class,
  backward compatibility, empty/unparenthesised rejection, and the
  applied-inner-type case (`= Dict Int deriving (Eq)`).
- `tests/stdlib/compiler/test_iface_ast_codec.spr` — `WrapDecl` round-trip with a
  non-empty `deriving_classes`.
