# Records in Sprout (v0 design)

**Status:** experimental language design. Non-normative — `docs/spec-v0.md` is
the normative source of truth and is unchanged by this document until the surface
below is implemented and promoted. This doc fixes the **surface syntax and
semantics** for nominal record types and supersedes the ad-hoc experimental
record surface currently in the compiler (brace literals + `get p x` access).

This is the "dedicated records draft/spec" called for in `BACKLOG.md` item 5.

---

## 1. Problem statement

Sprout has experimental record groundwork — `RecordDecl`/`RecordExpr`/
`GetFieldExpr` exist across the AST, parser, inference (`@rec:<Name>:<field>`
markers), bundler, DCE, and the `.iface` codec, and `tests/conformance/run/record_types.spr`
exercises them — but the surface was never designed, only prototyped. The
prototype has three problems:

1. **Braces.** Declaration `type Point = { x: Int }` and construction
   `Point { x = x }` use `{ }`, which in Sprout otherwise denotes **dict
   literals** (`{k: v}`), **class/instance bodies**, and **effect rows**
   (`!{IO}`). Worst of all, a record literal `{x = v}` and a dict literal
   `{x: v}` differ only by their separator — visually conflating two types the
   language deliberately keeps distinct (see §7).
2. **Access.** `get p x` is a contextual keyword form used by no mainstream
   language (§3) and it does not compose (`get (get p o) x` vs `p.o.x`).
3. **No update.** There is no way to produce a new record with some fields
   changed — the single largest functional gap.

Records are also not yet trusted inside the self-hosted compiler: `BACKLOG.md`
defers the `RuntimeLet` cleanup on "records maturing." A designed, stable surface
is the precondition for that.

## 2. Goals and non-goals

**Goals**

1. Immutable nominal record values: named fields, heterogeneous field types,
   closed (fixed) field set known at compile time.
2. A surface that reads as part of Sprout's existing grammar — records are
   **products**, so they join the parenthesis family (`(a, b)`, `f(a)`,
   `Just(x)`), not the brace family.
3. Total field access and functional update, both composing with pipes and
   `let`.

**Non-goals (v0)**

- **Row polymorphism / extensible records** — no `{ r | x : T }` open rows.
- **Structural subtyping** — records are nominal (§5).
- **Field punning**, implicit conversions, or duck typing.
- **`deriving` on records** — `type` ADTs only, per `docs/spec-v0.md` §5.6.x.
- **Folding records into the ADT surface** — a record is its own declaration
  form, not sugar for a single-constructor ADT (kept as a deliberate line;
  revisit only with its own design).
- **Dot access on arbitrary expressions** (`f(x).field`) — v0 supports dot
  access on variable chains only; see §4.3 (Scope A) and §12.

## 3. Prior-art survey

Every row verified against the language's official reference (not recalled).

| Language | Declaration | Construction — separator | Access | Update | Nom/Struct | Record ↔ Map |
|---|---|---|---|---|---|---|
| Rust | `struct Point { x: i32 }` | `Point { x: 10 }` — `:` | `p.x` | `Point { x: 3, ..base }` | Nominal | Distinct (`HashMap`) |
| OCaml | `type r = {num: int}` | `{num = 1}` — `=` | `r.num` | `{ r with num = v }` | Nominal | Distinct (`Map` module) |
| Haskell | `data P = P { x :: Int }` | `P { x = 1 }` — `=` | `x p`; `p.x` (ORD) | `p { x = 3 }` | Nominal | Distinct (ADT sugar) |
| Scala 3 | `case class P(name: String)` | `P(name = "…")` — named `=` | `p.name` | `p.copy(x = 3)` | Nominal | Distinct (JVM class) |
| Elm | `type alias P = { x : Int }` | `{ x = 3 }` — `=` | `p.x`; `.x` fn | `{ r \| x = 3 }` | **Structural** | Distinct (`Dict`) |
| F# | `type P = { X: float }` | `{ X = 1 }` — `=` | `r.X` | `{ r with X = 3 }` | Nominal | Distinct (`Map`) |
| TypeScript | `interface P { x: number }` | `{ x: 3 }` — `:` | `pt.x` | `{ ...r, x: 3 }` | **Structural** | Layered (`Record<K,V>`/`Map`) |
| Swift | `struct R { var w = 0 }` | `R(w: 640)` — named `:` | `r.w` | none (mutate copy) | Nominal | Distinct (`Dictionary`) |
| Clojure | `(defrecord P [x y])` | `(map->P {:x 3})` — `:` | `(:x p)` | `(assoc p :x 3)` | Nominal | **Record IS a map** |
| Elixir | `defstruct [:name]` | `%U{name: "J"}` — `:` | `u.name` | `%{u \| name: "J"}` | Tagged map | **Struct IS a map** (`__struct__`) |

**Synthesis.**

- **Separator.** `=` (has-value) is the ML/functional-nominal consensus — OCaml,
  Haskell, Elm, F#, and Scala's named args. `:` (has-type / key–value) clusters
  in the systems/dynamic family — Rust, TypeScript, Swift, Clojure, Elixir.
  Sprout is squarely ML-family.
- **Access.** *Every* surveyed language uses dot `p.x` (some add accessor
  functions). None uses a `get` keyword.
- **Update.** The ML family uses a `with` expression (`{ r with x = 3 }`, OCaml
  and F#) or a close cousin (Haskell `p { x = 3 }`, Scala `.copy`).
- **Record vs map.** Only the two dynamically-typed languages (Clojure, Elixir)
  make a record a *specialization* of their map substrate. Every statically-typed
  language keeps records and maps fully distinct — the static/dynamic axis is the
  whole explanation (fixed-offset heterogeneous fields vs an open homogeneous
  key→value bag). This directly settles §7.

## 4. Surface syntax

### 4.1 Declaration

```sprout
type Point = (x: Int, y: Int)
```

A record type is a `type` declaration whose right-hand side is a
**parenthesised, labelled field list**. Fields use `:` because `field: Type` is a
**type annotation** — the same `:` as function params (`x: Int`). Parametric
records are permitted (`type Boxed a = (value: a, tag: String)`), tracking the
existing `RecordDecl` type-parameter slot.

The label distinguishes a record type `(x: Int, y: Int)` from a tuple type
`(Int, Int)`: any parenthesised list whose entries are `label: Type` is a record;
entries that are bare types form a tuple. (Parser detail: `is_record_scan`
currently keys on `{`; it moves to "a `(` immediately followed by `ident :`".)

### 4.2 Construction

```sprout
fn origin() -> Point = Point(x = 0, y = 0)
```

Construction is **tag-prefixed** — the type name names the constructor, exactly
as ADT construction does (`Just(x)`). Fields use `=` because `field = value` is a
**value binding**, the same `=` as `let x = 0`. This is the OCaml/Haskell/Elm/F#
convention. All fields must be supplied (no partial records, no defaults in v0).

The `:` (declaration) / `=` (construction) split is intentional and semantically
honest: `:` means *has type*, `=` means *has value*. They are one convention each
for two different things, not two conventions for one.

### 4.3 Access — dot, variable chains (Scope A)

```sprout
fn manhattan(p: Point) -> Int = p.x + p.y
```

Field access is dot access. Because Sprout's lexer absorbs `.` into identifiers
(`is_ident_continue` includes `'.'`), `p.x` and `p.origin.x` already lex as a
single dotted-identifier token — the same token shape as module-qualified names
(`stdlib.string`). Access is therefore implemented as a **name-resolution rule**,
not a lexer change:

> Split a dotted name on `.`. If the head component is an **in-scope value**,
> the name is a field-access chain on that value. If the head is a **module**,
> it is module qualification.

Values and modules occupy separate namespaces, so this is unambiguous except when
a local **shadows** a module name; the rule is **local-binding wins in head
position**. Field access is **total** — `p.x` always yields the field's value
(no `Maybe`), in contrast to `dict_get` (§7).

**Scope A (v0):** the head must be a bare variable, so access on a compound
expression is written with an intermediate binding:

```sprout
let p = make_point(3, 4) in p.x        # not make_point(3, 4).x in v0
```

Scope B (postfix `.field` on any expression) requires making `.` a real operator
token and moving qualified-name assembly into the parser; it is deferred (§12)
and is purely additive.

### 4.4 Update — `with` expression

```sprout
fn shift_right(p: Point) -> Point = p with (x = p.x + 1)
```

Functional update produces a **new** record with the named fields replaced and
all others copied from the base. It reuses the existing `with` keyword (already
Sprout's match keyword, `match e with | …`); update-`with` follows a value
expression, match-`with` follows `match`, so the two are unambiguous — this is
exactly OCaml/F#'s reuse of `with`. The field list uses `=`, identical to
construction. Only declared fields may be named; naming an unknown field is a
compile error (§8).

A Rust/TypeScript-style spread (`..p`) is **rejected**: `..` is already the range
operator (`1..5`) and the constructor-export-all marker (`type Foo (..)`).

## 5. Semantics

- **Nominal identity.** Two record types with identical fields but different
  names are different types (`Point` ≠ `Vector`). This matches Sprout's existing
  identity model (ADTs, `wrap`, module-qualified type identity) and is what lets
  a record compose with `wrap` and carry typeclass instances keyed by its name.
- **Immutable.** Records are values; there is no in-place field mutation. `with`
  is the only "change" and it allocates a new record.
- **Strict.** Field values are evaluated eagerly at construction, consistent with
  Sprout's strict evaluation model.
- **Field scoping is per-record.** A field `x` of `Point` is distinct from a
  field `x` of `Vector`; fields are not global selector functions. (This is the
  `@rec:<Name>:<field>` qualification the inference already uses, and it avoids
  Haskell's original global-field-name clash.)

## 6. Type-system impact

- A `RecordDecl` introduces a nominal type constructor with a fixed, ordered set
  of typed fields; parametric records bind type variables in the field types.
- `RecordExpr` (construction) checks: the tag resolves to a record type, every
  field is supplied exactly once, and each field's value type unifies with the
  declared field type (with the record's type arguments substituted).
- `GetFieldExpr` (now produced by the dot-resolution rule, §4.3) types as the
  declared field's type; the head's type must be the owning record.
- `with` checks: the base has a record type; every named field belongs to that
  record and its new value unifies with the declared field type; the result type
  equals the base type.
- Field-name references validate against declared type names exactly as
  `docs/spec-v0.md` §5.6 already specifies for `RecordDecl` field types.

## 7. Records vs `Dict` (why they stay distinct)

Sprout's `Dict v` (`stdlib/prelude.sprout`) is `Dict (Map v)`: **String-keyed**,
**homogeneous-valued**, open/dynamic key set, partial lookup (`dict_get ->
Maybe v`). A record is the opposite on every axis:

| Axis | Record | `Dict v` |
|---|---|---|
| Field/key set | fixed, closed, compile-time | open, dynamic, runtime |
| Value types | heterogeneous (per field) | homogeneous (single `v`) |
| Access | direct & total (`p.x`) | partial (`dict_get → Maybe v`) |
| Identity | nominal type | structural container |
| Layout | fixed offsets | hash/tree nodes |

The **heterogeneity** axis is the wall: `{name: String, age: Int}` cannot be a
`Dict v` because a Dict forces one value type. Unifying them would require going
dynamic (the Clojure/Elixir "record is a map" model — forfeits static
heterogeneous fields) or adding row polymorphism (a §2 non-goal). Every
statically-typed language in §3 keeps them distinct; Sprout does the same.

The two *do* touch at one point — literal syntax — and moving records to the
paren family resolves it: records read as `Point(x = v)`, dicts stay `{k: v}`.

**Generalising `Dict v` → `Dict k v`** (arbitrary key types, gated on an `Ord k`
or `Hashable k` constraint) is a worthwhile but **orthogonal** piece of work that
does not interact with records. It is out of scope here; see §12.

## 8. Error-message impact

- Unknown field in construction/update: `record P has no field \`z\``.
- Missing field(s) in construction: `record P is missing field(s): \`y\``.
- Duplicate field in a literal: `field \`x\` supplied twice for record P`.
- Dot access whose head is neither a value nor a module: reuse the existing
  unresolved-name diagnostic; when the head is a value but the field is unknown,
  prefer `record P has no field \`z\`` over a generic message.
- Naming a record type where a tuple was expected (or vice versa) should point at
  the label/no-label distinction (§4.1).

## 9. Compatibility / migration

The prototype surface is experimental and not in the normative spec, so this is a
replacement, not a breaking change to stable syntax. Concrete migrations:

| Prototype (current) | v0 design |
|---|---|
| `type Point = { x: Int, y: Int }` | `type Point = (x: Int, y: Int)` |
| `Point { x = a, y = b }` | `Point(x = a, y = b)` |
| `get p x` | `p.x` |
| (no update) | `p with (x = v)` |

`tests/conformance/run/record_types.spr` and any example using the prototype
surface are updated in the same change. The `get` contextual form is removed from
the parser and `get` reverts to an ordinary identifier.

## 10. Implementation overview (for approval before editing)

High-level, root-cause-first; each is a small, reviewable step.

1. **Parser — declaration.** Replace the brace form: `is_record_scan` keys on
   `(` + `ident :`; `parse_record_type_decl` reads a parenthesised
   `label: Type` list. Tuple types (bare types in parens) are unaffected.
2. **Parser — construction.** Parse `TypeName( field = expr, … )` into
   `RecordExpr`. Distinguish from a normal call by the `ident =` lookahead after
   `(` (a call argument is an expression, never `ident =`).
3. **Resolver — dot access (Scope A).** Add the dotted-name rule (§4.3):
   head-is-value → build a `GetFieldExpr` chain; head-is-module → qualification;
   local shadows module. Delete the `get p x` special case; `get` becomes a
   plain identifier.
4. **Parser — update.** Parse `expr with ( field = expr, … )` into a new
   `RecordUpdateExpr` (or lower to `RecordExpr` + field copies).
5. **Inference.** `RecordExpr`/`GetFieldExpr` inference already exists; extend to
   the update node and to the totality/coverage checks in §6.
6. **Codegen.** Records lower to fixed-arity heap objects with fields at fixed
   offsets (reuse the ADT-object layout); access is an offset load; `with` is a
   fresh allocation copying unchanged fields.
7. **Bundler/DCE/iface codec.** Already handle `RecordDecl`/`RecordExpr`/
   `GetFieldExpr`; add the update node to each.

No new builtins and no runtime changes are anticipated (records reuse the
existing GC object model). Any deviation requires up-front approval per
`AGENTS.md`.

## 11. Tests

- **TDD (new feature):** failing tests first for each surface — declaration +
  construction, dot access, chained access, `with` update, parametric record.
- Type-error fixtures: unknown field, missing field, duplicate field, wrong
  field-value type, `with` on a non-record, access of an unknown field.
- Nominal-distinctness: two same-shaped records are not interchangeable.
- Migrate `tests/conformance/run/record_types.spr` to the v0 surface and keep it
  in the parity corpus.
- Parser tests for the record-vs-tuple and record-vs-call disambiguations.

## 12. Deferred / open

- **Dot access Scope B** — postfix `.field` on arbitrary expressions
  (`f(x).field`); needs `.` as an operator token + parser-side qualified-name
  assembly. Additive; own design.
- **Generic `Dict k v`** — arbitrary key types via `Ord`/`Hashable`; orthogonal
  to records (§7).
- **Accessor functions** — an Elm-style first-class `.field` for point-free
  pipelines (`ps |> map(.x)`); revisit once Scope B or a dedicated syntax exists.
- **Field defaults / partial construction**, **`deriving` for records**,
  **row polymorphism** — explicit non-goals for v0.

## 13. Spec/docs status

**Implementation status.** PR1 landed **declaration, construction, and
dot-access**; PR2 landed **functional update `with`** (§4.4) — both end-to-end on
the active IR codegen path (branch `feat/records-v0`). `docs/spec-v0.md` §5.6.3 is
the normative record section, and this doc is now rationale for it. Still to land
(PR3): §8 error-message fixtures, **parametric-record construction type-arg
inference**, and record-vs-tuple / record-vs-call / shadowing parser tests.
`BACKLOG.md` item 5 tracks the remaining work.
