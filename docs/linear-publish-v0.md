# Publishing a linear type's constructor (v0, 2026-10-22)

**Status:** proposal; rule and plan chosen 2026-10-22 (Kuba). Experimental, as spec §5.8 is.
Follows [linear-wrap-v0.md](linear-wrap-v0.md); blocks the linear array
([persistent-array-v0.md](persistent-array-v0.md) §5.3).

## 1. Problem

A destructure binds each field at its own type, so a field whose type is not linear is untracked:
it may be copied or dropped (spec §5.8). Whoever can reach the constructor can therefore copy a
linear value or make one from nothing. Both compile on master:

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
- One criterion, the checker's own: a field is safe to publish exactly when a pattern binding it
  would be tracked.

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

**Rule.** A linear type (ADT, record or wrap) whose constructors are published may not have a
field that a pattern would bind untracked. "Published" means `export` with `(..)`, or declared in
the prelude. A field is tracked when its type contains a linear type
(`linear_check.type_mentions_linear`, the binder rule) or mentions a variable of its
constructor's `where linear` bound, which is marked linear where the pattern unpacks it.

| declaration | published? |
|---|---|
| `export wrap linear LArr a (..) = Vector a` | error: `Vector a` |
| `export type linear Fd (..) = Fd Int` | error: `Int` |
| `export type linear Box a (..) = Box a` | error: `a` is unbounded |
| `export type linear Weird (..) = Weird TcpConnection (Vector Int)` | error: `Vector Int` |
| `export type linear PgConnection (..) = PgConnection TcpConnection PgSession` | error: `PgSession` |
| `export type linear Pair (..) = Pair File File` | allowed |
| `export type Boxed (..) = \| exists a. Boxed a where linear a` | allowed |
| `export wrap linear LArr a = Vector a` | allowed: abstract |

Rejected criteria: "at least one linear field" lets `Weird`'s `Vector` alias; "never publish a
linear constructor" also forbids `Pair File File`, which cannot be copied or forged.

**Error.** At the declaration, naming the first untracked field:

```
`LArr` is linear, and `(..)` publishes its constructor, but its field `Vector a` is not linear:
another module could copy it out or build an `LArr` around a shared value. Remove `(..)` and
export functions that build and take apart `LArr`.
```

A fixture with the hint applied (`.fixed.spr`) checks the hint can be followed.

## 5. Implementation

1. `ast.TypeDecl` and `ast.WrapDecl` gain the `Representation` field `RecordDecl` carries
   (`ast.sprout`), all three stamped by the bundler from the export scan as records are today
   (`bundler.publish_if_marked`). Prelude declarations are stamped `Published`, since their
   constructors are in scope everywhere whatever their marker (BACKLOG P2 **`(..)` is
   unenforced for everything the prelude declares**).
2. `linear_check` gains a declaration pass, run under `program_has_linear`: for each linear,
   published declaration, read each constructor's field types from its scheme in the env and
   apply §4's test. Unverified until its tests pass: that the bounded-existential case needs the
   bound named explicitly (the scheme's variable carries no `@linear:` mark).
3. Spec §5.8 states the rule; §5.6.1, §5.6.4 and the ADT export text point to it.

### 5.3 Records

An instance method can read any abstract record's fields, from any module (BACKLOG P3 **An
`instance` method is the one way past record opacity**; reproduced 2026-10-22 with an abstract
`type linear LBuf = (vec: Vec Int)`). The rule above stops `(..)` on such a record, but cannot
close that path. Until the follow-up PR closes it, the spec says linear records are covered by the
rule and not by the guarantee. Wraps and ADTs are not affected: an instance cannot name an
abstract constructor (`Unknown constructor`, checked 2026-10-22).

## 6. Compatibility and migration

Breaking for a published linear type with an untracked field. In our four repos:

- sprout-pg `postgres/types.sprout`: `PgConnection (..)`. Its comment gives the reason it is
  published: `postgres.connection` destructures it in place, borrowing the `TcpConnection`, which
  no accessor can return. `PgConnection` and its five accessors move into `postgres.connection`
  without `(..)`, and `pg_connection` is deleted (sprout-pg#20). The import path changes, and
  Sprout has no re-export, so repbit's eight imports move too (repbit#15). The two merge together,
  before this change.
- `testsupport/rec_fixture.sprout`: `Tok (..)`, read field by field by
  `tests/stdlib/test_imported_records.spr`. Becomes non-linear, or gets accessors.

## 7. Follow-ups

- Record opacity through instances (§5.3), in its own PR.
- Narrowing the trusted module: a non-linear binder destructured from a *borrowed* linear value may
  not escape (be returned, captured or stored). `linear_check`'s escape walk (`esc_value`) is the
  place. It catches `larr_get` returning its `Vector`. A mode on non-linear binders, the stronger
  form, was rejected: M4.6 moved modes onto function types, and it needs borrowing extern
  parameters.

## 8. Tests

- Type errors: one fixture per error row of §4's table, each pinning its line; a `.fixed.spr` for
  the `LArr` one.
- Accepted: `Pair File File` and the bounded existential, published and used across modules (the
  package-resolution gate); an abstract linear wrap, unchanged.
- The prelude stamp: a unit test that the bundler marks a prelude declaration `Published`.

## 9. Spec and docs

§5.8 gains the rule and the records caveat; `idiomatic-sprout.md`'s linear-handle advice says why
the constructor stays private.
