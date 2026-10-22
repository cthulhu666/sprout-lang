# Publishing a linear type's constructor (v0, 2026-10-22)

**Status:** implemented 2026-10-22; rule and plan chosen the same day (Kuba). Experimental, as
spec §5.8 is, which states the rule (*Publishing a linear type*).
Follows [linear-wrap-v0.md](linear-wrap-v0.md); blocks the linear array
([persistent-array-v0.md](persistent-array-v0.md) §5.3).

## 1. Problem

A destructure binds each field at its own type, so a field whose type is not linear is untracked:
it may be copied or dropped (spec §5.8). Whoever can reach the constructor can therefore copy a
linear value or make one from nothing. Both compiled before this rule:

```sprout
wrap linear Fd = Int
fn dup(f: Fd) -> (Fd, Fd) = match f with | Fd n -> (Fd(n), Fd(n))
fn forge() -> Fd = Fd(5)
```

Inside the declaring module that is trusted code. Outside it, the constructor is reachable through
`(..)`, and through the prelude, whose constructors are in scope everywhere (spec §3.1). For a
handle over an fd that means a double close. For `LArr`, whose `larr_set` writes in place under a
pure signature, an `LArr` around a shared `Vector` changes a value someone else holds: a wrong
answer, not a crash.

Nothing reaches it today: every stdlib linear type is abstract, and the one published linear type
in our four repos, sprout-pg's `PgConnection (..)`, holds a linear `net.TcpConnection` beside its
non-linear `PgSession` (§6).

## 2. Goals and non-goals

Goals:

- The compiler rejects every declaration that lets another module see an untracked field of a
  linear value. No lint.
- One criterion that needs no look inside another type: a field is safe to publish when its type
  is linear.

Non-goals:

- The declaring module. It stays trusted, as `persistent-array-v0.md` §5.3 says; §7 lists the
  cheap check that narrows it.
- `extern fn` redeclaration, which is outside the module system (spec, *Externs are outside the
  module system*); unsafe primitives are where that closes.
- Record opacity through instances (§5.3).

## 3. Prior art

Checked 2026-10-22 against the cited source.

| system | how a non-copyable wrapper over a raw value stays unforgeable | source |
|---|---|---|
| Rust | `pub struct OwnedFd { /* private fields */ }`; the only raw-integer constructor is `unsafe fn from_raw_fd`, whose caller promises "The `fd` passed in must be an owned file descriptor" | std docs, `OwnedFd`, `FromRawFd` |
| Austral | linearity is "enforced entirely at compile time"; the resource sits behind a "linear interface, non-linear interior" (`struct File: Linear { handle: int; }`) | Austral spec, *Linear Types*, *The Trust Boundary* |

Both rest the guarantee on privacy. Rust's compiler enforces the privacy; whether Austral's checks
it was not found in its spec. This design makes Sprout's compiler enforce it.

## 4. Design

**Rule.** A linear type (ADT, record or wrap) whose constructors are published may only have
fields whose type, aliases expanded, is a linear type or a variable of its constructor's `where
linear` bound, which is marked linear where the pattern unpacks it. "Published" means `export`
with `(..)`, or declared in the prelude. A linear field's own declaration guards what it holds:
it is published, so this rule checks it, or abstract, so its module is trusted.

| declaration | published? |
|---|---|
| `export wrap linear LArr a (..) = Vector a` | error: `Vector a` |
| `export type linear Fd (..) = Fd Int` | error: `Int` |
| `export type linear Box a (..) = Box a` | error: `a` is unbounded |
| `export type linear Weird (..) = Weird TcpConnection (Vector Int)` | error: `Vector Int` |
| `export type linear Weird (..) = Weird (TcpConnection, Vector Int)` | error: not itself linear |
| `export type linear PgConnection (..) = PgConnection TcpConnection PgSession` | error: `PgSession` |
| `export type linear Slot (..) = Slot (Maybe File)` | error: over-strict |
| `export type linear Pair (..) = Pair File File` | allowed |
| `export type Boxed (..) = \| exists a. Boxed a where linear a` | allowed |
| `export wrap linear LArr a = Vector a` | allowed: abstract |

A `where linear` bound can only name an `exists` variable (a data parameter's is a parse error),
so the importer cannot open it, and a value it packs is its own.

Rejected criteria:

- "At least one linear field" lets `Weird`'s `Vector` alias.
- "Never publish a linear constructor" also forbids `Pair File File`, which cannot be copied or
  forged.
- "The field's type contains a linear type", the first version of this rule, lets
  `Weird (TcpConnection, Vector Int)` through. A pattern binds each part of a tuple at its own type,
  so it is the first criterion again, one level down.
- "Every value a nested pattern can bind is tracked", walking tuples, type arguments and the fields
  of reachable constructors, accepts `(File, File)` and `Maybe File`. But what it accepts would
  depend on whether other modules' types are published, which the checker cannot see today: a
  module that publishes its type could break a declaration in another. Since it accepts a superset
  of this rule, it can be adopted later without breaking anything (§7).

**Error.** At the declaration, naming the first field whose type is not linear:

```
`LArr` is linear, and `(..)` publishes its constructor, but its field `Vector a` is not linear:
another module could copy it out, or build one around a value it shares. Remove `(..)` and
export functions that build and take apart `LArr`
```

A record says "its constructor and fields"; a bare type variable "can hold a value that is not
linear". A field that holds a linear type without being one says why the rule looks no further:

```
`Weird` is linear, and `(..)` publishes its constructor, but its field `(main.Res, Vector Int)` is
not itself a linear type: a pattern can take it apart, and its parts are not checked. Remove `(..)`
and export functions that build and take apart `Weird`
```

A fixture with the hint applied (`.fixed.spr`) checks the hint can be followed.

## 5. Implementation

1. `ast.TypeDecl` and `ast.WrapDecl` gain the `Representation` field `RecordDecl` carries
   (`ast.sprout`), all three stamped by the bundler from the export scan as records were
   (`bundler.publish_if_marked`). Prelude declarations are stamped `Published`, since their
   constructors are in scope everywhere whatever their marker (BACKLOG P2 **`(..)` is
   unenforced for everything the prelude declares**).
2. `infer.check_published_linear_types` runs with the other declaration checks in
   `typecheck_decls_resolved`, under `program_has_linear`. For each linear, published
   declaration it converts each field's type expression as registration does (`type_from_ast`,
   which expands aliases), marks the constructor's `where linear` variables linear in the env, and
   asks whether the type's head carries the `@linear:` marker. The bound has to be marked there: a
   scheme's variable carries no `@linear:` mark until a pattern unpacks it.
3. Spec §5.8 states the rule; §5.6.1 and §5.6.4 point to it.

### 5.3 Records

An instance method can read any abstract record's fields, from any module (BACKLOG P3 **An
`instance` method is the one way past record opacity**; reproduced 2026-10-22 with an abstract
`type linear LBuf = (vec: Vec Int)`). The rule above stops `(..)` on such a record, but cannot
close that path. Until the follow-up PR closes it, the spec says linear records are covered by the
rule and not by the guarantee. Wraps and ADTs are not affected: an instance cannot name an
abstract constructor (`Unknown constructor`, checked 2026-10-22). A published record with two
linear fields also rests on a separate bug: reading one field drops the others unchecked
(BACKLOG P2 **A field read drops the record's other linear fields**). The rule neither causes
nor closes it.

## 6. Compatibility and migration

Breaking for a published linear type with an untracked field. In our four repos:

- sprout-pg `postgres/types.sprout`: `PgConnection (..)`. Its comment gives the reason it is
  published: `postgres.connection` destructures it in place, borrowing the `TcpConnection`, which
  no accessor can return. `PgConnection` and its five accessors move into `postgres.connection`
  without `(..)`, and `pg_connection` is deleted (sprout-pg#20). The import path changes, and
  Sprout has no re-export, so repbit's eight imports move too (repbit#15). The two merge together,
  before this change.
- `testsupport/rec_fixture.sprout`: `Tok (..)`, read across the module boundary by
  `tests/stdlib/test_imported_records.spr`. Its one field is now a linear `Ticket`, which keeps
  what the test checks: a field read consumes an imported linear record. Two linear fields
  would rest on a separate bug, BACKLOG P2 **A field read drops the record's other linear
  fields**.

## 7. Follow-ups

- Record opacity through instances (§5.3), in its own PR.
- Relaxing the rule to accept `(File, File)` and `Maybe File` (§4, the last rejected criterion),
  if a real program needs it. It needs a published/abstract mark on every type, visible across
  modules, and is not breaking.
- Narrowing the trusted module: a non-linear binder destructured from a *borrowed* linear value may
  not escape (be returned, captured or stored). `linear_check`'s escape walk (`esc_value`) is the
  place. It catches `larr_get` returning its `Vector`. A mode on non-linear binders, the stronger
  form, was rejected: M4.6 moved modes onto function types, and it needs borrowing extern
  parameters.

## 8. Tests

- Type errors: `tests/conformance/type_error/linear_publish_*`, one per kind of rejected field
  (a wrap, an `Int`, an unbounded parameter, a field beside a linear one, a second constructor,
  a record, a tuple holding a linear type, the same tuple behind an alias, a `Maybe` of a linear
  type), each pinning its line and the full message, with a `.fixed.spr` that applies the hint.
- Accepted: `tests/stdlib/test_linear_publish.spr` (a pair of linear fields, a linear wrap over a
  linear type, another module's linear type, the bounded existential, an abstract linear wrap);
  across modules, the
  package-resolution gate builds and destructures a published `Pair` and refuses a published
  `wrap linear Ticket (..) = Int` in the module that declares it.
- The prelude stamp: `tests/stdlib/compiler/test_bundle_prelude_scope.spr`.

## 9. Spec and docs

§5.8 gains the rule and the records caveat; `idiomatic-sprout.md`'s linear-handle advice says why
the constructor stays private.
