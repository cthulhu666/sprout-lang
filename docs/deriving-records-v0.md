# Deriving on records (v0)

Status: normative behaviour described in `docs/spec-v0.md` §Deriving; this doc is
the design rationale and the record-instance dispatch-soundness fixes that landed
alongside.

## Problem

`deriving (...)` was scoped to `type` declarations (sums-of-products). Records
(`type Point = (x: Int, y: Int)`, a distinct AST decl form `RecordDecl`) had no
`deriving` support, so every record needed hand-written `Eq`/`Ord`/`ToString`
instances. This is pure boilerplate: a record is a single product, so the
instances are mechanical.

## Goals / non-goals

- **Goal:** `deriving (Eq, Ord, ToString)` on record declarations.
- **Non-goal:** `Enum` on records — it requires a nullary-constructor ADT
  (ordinal↔constructor bijection); a record is a single field-bearing product.
  Rejected eagerly at the deriving site.
- **Non-goal:** `Serialize`/`Deserialize`/`Hash` — not in deriving v1 for ADTs
  either.

## Syntax

The clause is **trailing**, after the field list:

```sprout
type Point = (x: Int, y: Int) deriving (Eq, Ord, ToString)
type Box a  = (val: a, tag: String) deriving (Eq, Ord, ToString)
```

Records put `deriving` after the `= (fields)` right-hand side (a self-contained
parenthesised expression) whereas ADTs put it before `=` (which opens a
multi-line constructor list). The clause parser (`parse_deriving_clause`) is
placement-agnostic; only the call site in the two decl parsers differs.

## Synthesized bodies

A record is a single product — no sum, no tag, no constructor to match on — so
the emitters are the ADT same-constructor logic standalone, reading each field by
name via `GetFieldExpr` (`value.field`) rather than binding positional pattern
variables. For `type Point = (x: Int, y: Int)`:

- **Eq:** `eq(left.x, right.x) && eq(left.y, right.y)` — conjunction in
  declaration order; `true` when field-less.
- **Ord:** `match compare(left.x, right.x) with | 0 -> compare(left.y, right.y) | c -> c`
  — lexicographic (`compare` returns `Int`); `0` when field-less.
- **ToString:** `"Point(x = " ++ to_string(value.x) ++ ", y = " ++ to_string(value.y) ++ ")"`
  → `"Point(x = 3, y = 4)"`. **Named fields**, mirroring construction syntax so
  the output is (modulo unquoted `String` fields, matching the ADT path) valid
  source.

The instance scaffolding — head (`instance_head_typeexpr`), per-type-parameter
constraints (`instance_constraints_for`) — is shared with the ADT path.
Parametric records get `instance Eq (Box a) where Eq a`, as parametric ADTs do.

Implementation: `stdlib/compiler/deriving.sprout` (record emitters + record arms
in `expand_deriving_decls` / `validate_one_deriving_decl`). `RecordDecl` gained a
`(List String) deriving_classes` field, rippling through the parser, `iface_codec`
encode/decode, the bundler qualification pass, and the (mostly wildcard) match
sites across the compiler.

## Two dispatch-soundness fixes (pre-existing bugs)

Landing deriving surfaced two **pre-existing** bugs in typeclass dispatch for
parametric record instances. Both broke *hand-written* parametric record
instances too (verified on the unmodified seed compiler) — they are independent
of deriving; deriving was simply the first code to generate the triggering shape.
ADTs were unaffected in both cases. Regression coverage:
`tests/stdlib/test_record_parametric_instance.spr` (hand-written, no deriving).

### Fix 1 — record field-access typing (`infer.sprout`)

`lookup_record_field_typed` typed an accessed field by `instantiate`-ing the
field scheme (renaming the record's params to fresh vars) and then applying a
type-arg substitution that could no longer reach those renamed vars. So `left.val`
on `Box a` inside `instance Eq (Box a) where Eq a` got a **fresh var decoupled
from the instance's `a`**, and the inner `eq(left.val, right.val)` never matched
the instance's `@fwd:a:Eq` constraint marker.

Fix: `record_field_access_type` binds the record's params to the access's actual
type args **directly** (as `concrete_field_type` already did for record update —
its comment documents the `instantiate` trap), with a length-guard fallback to
`instantiate` for unapplied record types. Monomorphic records are byte-identical
to before (no params to bind).

### Fix 2 — stale-subst input dispatch (`infer.sprout`)

`check_instance_for_marker` resolved the dispatch argument's concrete type with
the **pre-argument-inference** `subst`, not the post-resolution `s3`. An inline
parametric-record argument (`Box(val = 5, tag = "a")`) has node type `Box $a`
whose `$a -> Int` binding lives only in `s3`; the stale `subst` left it
`Box $free`, so the instance's `where` constraint dict resolved to `EvUnresolved`
and lowering emitted a poison `__sprout_ir_unresolved_thunk` — a runtime
null-dict crash when the body dereferenced it. ADTs dodged this because a
constructor-application node is already concretely typed.

Fix: resolve the dispatch arg's type with `s3` (lines ~1009/1011/1028). Input-
position dispatch resolves eagerly here and the `resolve_dispatch_typed_expr`
post-pass only rewrites *return*-position calls, so `s3` is the last chance to see
the concrete type.

## Tests

- `tests/stdlib/test_deriving_records.spr` — Eq/Ord/ToString on concrete,
  single-field, nested (dispatch to another synthesized instance), and parametric
  records.
- `tests/stdlib/test_record_parametric_instance.spr` — hand-written parametric
  record instance; exercises both fixes directly (no deriving).
- `tests/conformance/type_error/deriving_enum_on_record.{spr,err}` — Enum
  rejection.
- `tests/stdlib/compiler/test_iface_ast_codec.spr` — RecordDecl round-trip with a
  non-empty `deriving_classes`.
