# A linear `wrap` (v0, 2026-10-22)

**Status:** landed 2026-10-22, Experimental as spec §5.8 is (spec §5.6.1 *Linear wraps*).
Comes from [persistent-array-v0.md](persistent-array-v0.md) §12 step 12.

## 1. Problem

A linear type had to be an ADT or a record (spec §5.8 said `linear` "applies to ADT and record
declarations"). Both are boxed: a one-field linear type costs an allocation to build and a load
to read, and a function that consumes one and returns a new one allocates again.

That cost decides the linear array. A prototype `type linear LArr = LArr (MutVec Int)` that
rebuilt `LArr(m)` on every set ran the sieve in 767 ms against 7
([persistent-array-v0.md](persistent-array-v0.md) §5.3). The way around it was to make `LArr`
opaque in C, with five builtins. A `wrap` costs nothing (spec §5.6.1: construction and
destruction "are identity at the LLVM IR level"), but cannot be linear. With a linear wrap, `LArr`
is `Vector` at runtime, four of its five operations are Sprout over existing pure externs, and
only the in-place write needs C.

The same box sits under the stdlib's own linear handles: `TcpConnection`, `TcpListener`
(`stdlib/net.sprout`) and `Task a` (`stdlib/task.sprout`) are each a one-field ADT over `Int`.

## 2. Goals and non-goals

Goals:

- `wrap linear Name params = T`: a distinct type with a wrap's representation and a linear
  type's rules.
- No new rule in the linear checker: a linear wrap is checked as a one-constructor linear ADT is.

Non-goals:

- Moving `TcpConnection`, `TcpListener` or `Task` onto it. Each saves one allocation per handle;
  a follow-up, measured first.
- Containment virality for wraps. A plain wrap over a linear type is already a tracked container
  (BACKLOG P3 **Containment virality**), and that does not change.
- `deriving` on a linear wrap beyond what `type linear` allows (§7).

## 3. Prior art

Each row checked against the cited source, 2026-10-22.

| system | a zero-cost wrapper that is not copied | source |
|---|---|---|
| Rust | `#[repr(transparent)]` gives a one-field struct "the same layout and ABI as the only non-size 0 non-alignment 1 field"; a struct without `Copy` moves | Reference, *Type layout*, `layout.repr.transparent.layout-abi` |
| Swift | `struct FileDescriptor: ~Copyable`; unlike a class, "no object needs to be allocated" | SE-0390 |
| Linear Haskell | "In a newtype declaration, the field must be linear"; a newtype's "representation is the same as an existing type" | GHC User's Guide, *Linear types*; Haskell 2010 Report §4.2.3 |
| `linear-base` 0.4 | `data Array a = Array (Array# a)`, and `unsafeSet` rebuilds it: `Array (Unlifted.set ix val arr)` — a `data` box, not a newtype | `Data.Array.Mutable.Linear.Internal` source |
| Futhark | no wrapper: uniqueness is on the array type itself, `*[]i32` | language reference §3.11 |

Rust, Swift and Linear Haskell all let a wrapper be both zero-cost and non-copyable; Sprout's
`wrap` is the zero-cost half and `linear` the other.

## 4. Design

**Syntax.** The marker sits where `type linear` puts it, between the keyword and the name:

```sprout
export wrap linear LArr a = Vector a     # abstract: importers cannot build or unwrap one
wrap linear Handle s = Int               # `s` phantom, as in a plain wrap
```

`linear` is a contextual word there, as it is after `type`. The export marker `(..)` keeps its
slot, after the parameters.

**Semantics.** A linear wrap is a linear type (spec §5.8), with every rule that implies: used
exactly once, borrowed through `borrowing`, the leak and reuse errors. Construction `LArr(v)`
consumes `v`, as every constructor does. A `match` on an owned `LArr` consumes it; on a borrowed
one it borrows it and binds the field as borrowed (spec §5.8: "destructuring a borrowed value
binds its linear fields as borrowed too").

**Linearity ends at the destructure.** The field's type decides whether its binder is tracked.
`match arr with | LArr raw -> …` binds `raw: Vector a`, which is not linear, so `raw` may be
copied or dropped. `type linear File = File Int` has the same property today. So a linear
wrap's guarantee is only as strong as its constructor's privacy: an abstract wrap
(`export wrap linear X`, no `(..)`) is unwrapped only by its own module. `LArr` relies on this,
and so cannot live in the prelude, whose constructors are in scope everywhere (spec §3.1). The
compiler now enforces it: `(..)` on a linear type with an untracked field is an error
([linear-publish-v0.md](linear-publish-v0.md)).

**Representation.** Unchanged: a wrap is its inner value (`ast_to_ir.build_ctor_table_acc`, tag
sentinel −1). CPR already works through an unwrap: `fn w_get(i: Int, w: W a) -> Maybe a = match
w with | W raw -> vector_get(raw, i)` compiles to a `w_get_worker` that calls
`vector_get_unboxed`, checked on master 2026-10-22. `borrowing` could not be part of that check,
since it is refused on a non-linear parameter; over a boxed `type linear` the borrowed form gets
the same worker, and `ast_to_ir` reads no parameter mode, so the two combine.

**Type system.** One change: the declaration sets the type's `@linear:` marker, as `type linear`
does. Everything that reads the marker (`linear_check.type_is_linear`,
`infer.type_name_is_linear`, the bound of [linearity-bound-v0.md](linearity-bound-v0.md)) then
sees the wrap. An unbounded type variable still admits no linear type, so `LArr File` is
rejected where it is instantiated, by the bound's existing error.

**Errors.** No new diagnostic. A linear wrap reports reuse, leak and borrow errors in the words
used for a linear ADT, naming the wrap.

## 5. Implementation

The gaps, read from the compiler on 2026-10-22, all closed in the landing change:

1. ~~`ast.WrapDecl` gains a `Multiplicity` field, as `TypeDecl` and `RecordDecl` carry.~~
2. ~~`parser.parse_wrap_decl` reads the marker with `linear_marker_mult` and
   `skip_linear_marker`.~~ Before, `wrap linear LA a = Vector a` compiled as a wrap named
   `linear` with parameters `LA` and `a`; only a later use of `LA` failed.
3. ~~The export pre-scan's `wrap` arm skips the marker.~~ It now shares `scan_named_type` with
   the ADT arm.
4. ~~`infer.register_type_decl_raw`'s `WrapDecl` arm calls `mark_type_multiplicity`.~~
5. ~~`lint_rules.linear_ctors_of` gains a `WrapDecl` arm.~~
6. ~~A cross-module test that an importer sees the wrap as linear.~~ `iface_codec` carries no
   `@linear:` marker for any type; the bundle merges every module's declarations, and infer
   registers the marker from each.
7. ~~A formatter test for `export wrap linear X a (..) = T deriving (…)`.~~ The formatter
   already left it alone; the test pins that.

`linear_check` and `ast_to_ir` need no wrap-specific change: infer registers a wrap as a
one-constructor ADT, so the checker already walks its constructor and pattern.

## 6. Compatibility

Additive. The one source that changes meaning is a wrap named `linear`, which is how
`wrap linear …` was silently read before (§5 item 2). None exists in this repo,
uncharted-suns, repbit or sprout-pg, worktrees included (grepped 2026-10-22; sprout-pg as
checked out, not fetched).

## 7. Open questions

None. `deriving` on a linear type works: the derived methods consume their arguments, so
`a == a` is a reuse (`tests/conformance/type_error/linear_derived_eq_reuse.spr`), on an ADT and
a wrap alike.

## 8. Tests

- Parser: `tests/stdlib/compiler/test_parser.spr`, with parameters, a phantom parameter, `(..)`
  and `deriving`, and that the marker is not read as a parameter.
- Type errors: `tests/conformance/type_error/linear_wrap_{reuse,leak,consume_borrowed}`. The
  unbounded type variable is the grid's `pass` column.
- Cross-module: `scripts/package_resolution_gate.sh` checks that an importer sees the wrap as
  linear and cannot unwrap an abstract one (`Unknown constructor`);
  `tests/stdlib/test_linear_cross_module.spr` uses one.
- Run: `tests/stdlib/test_linear_wrap.spr` builds, borrows, consumes and freezes a
  `wrap linear` over `Vector`; `tests/stdlib/test_linear_wrap_cpr.spr` checks in IR that a
  borrowed unwrap gets a CPR worker over `vector_get_unboxed`.
- The linear grid repeats every `HDR_L` cell as `w_<name>` under a linear-wrap header
  (`scripts/gen_linear_grid.sh`): a new way to declare a linear type is a new header, not a new
  reach path.

## 9. Spec and docs

- §5.6.1: a wrap may be `linear`, with the syntax above.
- §5.8: "It applies to ADT, record and wrap declarations", and the sentence on where `linear` is
  recognized names both positions.
- `docs/idiomatic-sprout.md`: a linear handle over one value is a `wrap linear`.
