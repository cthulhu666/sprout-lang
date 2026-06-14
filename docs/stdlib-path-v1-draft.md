# stdlib.path — v1 draft

**Status:** experimental design draft, not normative.
**Author:** TBD; design conversation pre-dates implementation.
**Origin:** the `wrap FilePath`/`wrap StdlibRoot` pair landed in PR #40 closed
the swap-bug class for compiler-internal paths, but left two questions open:
(a) the compiler still does naive `str_concat`-based path joining in
`module_loader.module_name_to_path` and `bundler.prelude_path`, with a
latent trailing-slash / empty-root bug; (b) once Sprout grows enough
stdlib for users to write filesystem-touching programs, they will need a
canonical `Path` type — and retrofitting one after users depend on raw
`String` paths is much more expensive than designing it now.

## Problem

There is no canonical Path type in Sprout's stdlib. Today:

- Compiler-internal paths are `wrap FilePath = String` / `wrap StdlibRoot = String`
  (PR #40), which gives swap-protection but no validation, no structured
  operations, and no shared API surface.
- Path *construction* in the compiler relies on hand-written
  `str_concat(root, str_concat("/", str_concat(rel, ".sprout")))` chains in
  `module_loader.sprout` and `bundler.sprout`. These break silently on
  trailing-slash `stdlib_root` (produces `//`) and on empty `stdlib_root`
  (produces a root-anchored `/compiler/ast.sprout`).
- There is no `dirname` / `basename` / `extension` / `with_extension`
  surface anywhere. Future user code that needs to derive an output path
  from an input path would have to re-invent it.
- There is no validation surface — `read_file("")` reaches the runtime
  and errors there, instead of being rejected at construction.

The compiler-internal surface is small enough (two functions) that
fixing it standalone is trivial. The forward-looking problem is the
*stdlib* surface — designing `stdlib.path` so the compiler becomes one
client of it alongside future user code.

## Goals

1. Define `stdlib.path` as the canonical Sprout API for filesystem paths,
   suitable for both compiler-internal use and arbitrary user programs.
2. Eliminate the naive-`str_concat` join sites in `module_loader.sprout`
   and `bundler.sprout` by routing them through the stdlib helper.
3. Make the *type system* enforce the file-vs-directory distinction at
   API boundaries — extending the wrap-philosophy that PR #36–#41
   established for compiler internals to the stdlib surface.
4. Keep the v1 surface small and POSIX-only; preserve room for platform
   abstraction and richer ops later without breaking the v1 API.

## Non-goals (v1)

- Platform-aware separators (Windows backslash, drive letters). Sprout has
  no Windows backend today; defer until one exists.
- Absolute-vs-relative type distinction. The bug class is rare and the
  conversion ceremony is high; expose `is_absolute` as a value-level
  predicate instead.
- Eager normalization (Python `Path.resolve()`-style canonicalization on
  construction). Preserve exact spelling so diagnostics quote what users
  wrote. Provide `normalize` as an explicit op.
- Symbolic-link resolution and any operation that touches the filesystem
  beyond `read_file` / `dir_list` / existence probes.
- Byte-level paths (`OsString`-style). Sprout strings are UTF-8; if a
  future user needs raw bytes, that is a separate stdlib facility.

## Design

### Two distinct types: `File` and `Dir`

```sprout
export wrap File = String   # path that names (or will name) a regular file
export wrap Dir  = String   # path that names (or will name) a directory
```

Both are zero-cost String wraps under PR #36's `wrap` semantics. The
distinction is type-level only: at runtime, both are identity wrappers
around `String`. The benefit is **at the API surface**, where joining a
directory and a relative name yields one or the other depending on
intent:

```sprout
export fn dir_file(d: Dir, rel: String) -> File   # Dir/rel  →  File
export fn dir_sub (d: Dir, rel: String) -> Dir    # Dir/rel  →  Dir
```

This catches a real bug class: "I passed a file path where a directory
was expected" (and vice versa) becomes a compile error, not a runtime
read_file failure on a path-shaped concatenation. The compiler-internal
`FilePath` becomes `File` and `StdlibRoot` becomes `Dir` — the two
wraps that PR #40 introduced reduce to the natural stdlib types.

Rejected alternatives:

- **Single `Path` type, value-level `is_dir`**: industry-standard (Python,
  Go, Rust). Cheaper to use. Does not pre-empt the file-vs-dir-confusion
  bug class. Inconsistent with the wrap-philosophy we've committed to.
- **Three types** (`AbsFile` / `RelFile` / `Dir` etc): catches the
  absolute-vs-relative class but adds large conversion surface; the bug
  class is too rare to justify.

### Internal representation: String

Backing both `File` and `Dir` by `String` rather than by `(List String, Bool)`:

- Sprout's heap-allocated strings + codepoint-indexed `str_slice`
  (memory `project_str_slice_codepoint_cost`) make segment-walk operations
  expensive. A segment-based representation would pessimize `parent` and
  `join` instead of optimizing them.
- Construction stays O(1) (the wrap is a no-op).
- Most paths are constructed once, consumed once, never decomposed. Storing
  them in their final form matches the access pattern.

Ops that need decomposition (`parent`, `basename`, `extension`) parse on
demand using `str_find` / `str_slice`. This is O(length) per call but the
call sites are rare and the constant factor is small.

### Smart constructor strategy

- Data ctors `File` and `Dir` stay **exported**: cheap construction at
  trusted internal sites (e.g. `dir_file(d, rel)`'s internal result).
- Add `file_checked: String -> Result PathErr File` and
  `dir_checked: String -> Result PathErr Dir` for the CLI-argv and
  user-input boundary. These reject:
  - empty string
  - embedded NUL byte
- No validation for `..` or `.` segments — preserve exact spelling.
  `normalize` is opt-in (see below).

```sprout
export type PathErr (..) =
  | PathErrEmpty
  | PathErrNullByte
```

### v1 API surface

```sprout
module stdlib.path

# --- Construction ---------------------------------------------------

export wrap File = String
export wrap Dir  = String

export fn file(s: String) -> File = File(s)            # trust caller
export fn dir(s: String)  -> Dir  = Dir(s)

export fn file_checked(s: String) -> Result PathErr File
export fn dir_checked(s: String)  -> Result PathErr Dir

# --- Inspection (lossless roundtrip) ---------------------------------

export fn file_str(f: File) -> String = match f with | File s -> s
export fn dir_str(d: Dir)   -> String = match d with | Dir  s -> s

# --- Joining ---------------------------------------------------------

# Strips a trailing "/" from d and a leading "/" from rel before joining.
# `rel` may itself contain "/"; it is taken verbatim, not parsed.
# Rejected at construction: an empty rel.
export fn dir_file(d: Dir, rel: String) -> File
export fn dir_sub (d: Dir, rel: String) -> Dir

# --- Decomposition (pure) -------------------------------------------

# Parent of a file is always a Dir.  Parent of a dir is Maybe Dir
# because the root has no parent.
export fn file_parent(f: File)    -> Dir
export fn dir_parent (d: Dir)     -> Maybe Dir

# Basename — the final segment.  For "/foo/bar.spr" → "bar.spr".
export fn file_basename(f: File) -> String
export fn dir_basename (d: Dir)  -> String

# Extension — last "." in the basename, excluding the dot.
# "foo.spr"     → Just "spr"
# "foo.tar.gz"  → Just "gz"
# "foo"         → Nothing
# ".hidden"     → Nothing   (leading dot is not an extension)
export fn file_extension(f: File) -> Maybe String

# Replace (or add) the extension.  Pass "" to strip.
export fn file_with_extension(f: File, ext: String) -> File

# --- Predicates (pure) ----------------------------------------------

export fn file_is_absolute(f: File) -> Bool
export fn dir_is_absolute (d: Dir)  -> Bool

# --- Normalization (pure, opt-in) -----------------------------------

# Collapses redundant "/" and "." segments.  Resolves ".." against
# preceding segments (without touching the filesystem).  Does NOT
# follow symlinks.
export fn file_normalize(f: File) -> File
export fn dir_normalize (d: Dir)  -> Dir
```

### IO surface

Filesystem IO stays in `stdlib.io` (or wherever `read_file` currently
lives), but the signatures migrate to take `File` / `Dir`:

```sprout
# in stdlib.io
export fn read_file(f: path.File) -> Result IoErr String !{IO}
export fn write_file(f: path.File, contents: String) -> Result IoErr Unit !{IO}
export fn file_exists(f: path.File) -> Bool !{IO}
export fn dir_exists (d: path.Dir)  -> Bool !{IO}
export fn dir_list   (d: path.Dir)  -> Result IoErr (List String) !{IO}
```

(The exact `IoErr` shape is out of scope for this draft.)

### Compiler migration

When `stdlib.path` lands, the compiler retires its private wraps:

- `source.FilePath`         → `path.File`
- `source.StdlibRoot`       → `path.Dir`
- `module_loader.module_name_to_path(name: ModuleName, root: Dir) -> Maybe File`
- `bundler.prelude_path(root: Dir) -> File`

The two naive `str_concat` join sites become `path.dir_file` calls, which
handle the trailing-slash and empty-root cases correctly by construction.

The unwrap helpers `source.filepath_str` and `source.stdlib_root_str` are
replaced by `path.file_str` / `path.dir_str` at the `read_file` boundary
(or eliminated entirely once `read_file` takes `path.File`).

The other wraps from PR #41 (`ModuleName`, `RawName`, `QualifiedName`)
stay in `source.sprout` — they are compiler-internal naming concepts,
not stdlib path concepts.

## Test plan

The TDD guard from AGENTS.md DoD #2 requires failing tests before
implementation. Three tiers of test cover the v1 surface:

1. **Type-level protection probe** (`tests/stdlib/test_path_type.spr`).
   Roundtrip `File` and `Dir` through `file_str` / `dir_str`. The probe
   *also* attempts a type-confused call (`dir_file(my_file, ...)`)
   commented-out with a directive that the test runner will eventually
   support; for now, the comment is documentation.

2. **Behavioral semantics** (`tests/stdlib/test_path_ops.spr`).
   Covers every documented op against a table of inputs, including the
   edge cases called out above (trailing slash on `dir_file`, leading
   slash on `rel`, extension stripping, `.hidden`, normalize against
   `..`).

3. **Compiler integration** (replaces the existing tests under
   `tests/stdlib/compiler/test_path_wrap_protection.spr`). Verifies that
   `module_name_to_path` and `prelude_path` produce paths free of the
   double-slash and empty-root bugs that the current naive concat
   permits.

## Open questions

1. **Module name** — `stdlib.path` vs `stdlib.fs` vs `stdlib.io.path`.
   Leaning `stdlib.path` for parity with Go/OCaml/Haskell, leaving
   `stdlib.io` for the effectful surface. Decide at implementation.

2. **`Path` umbrella type** — should there also be a tag-union
   `type Path = AsFile File | AsDir Dir` for code that needs to be
   agnostic? Lean: skip in v1; add only if a concrete use case appears.

3. **`File`/`Dir` data-ctor visibility** — keep them exported as proposed,
   or hide them and force all construction through smart constructors?
   Hiding rules out `dir("/literal/path")` as boilerplate-free
   construction. Lean: keep exported for v1, revisit if a class of bugs
   from unchecked construction shows up.

4. **Coexistence with current compiler code** — should the migration
   happen in the same PR that introduces `stdlib.path`, or as a follow-up?
   Leaning: same PR — otherwise the test plan's tier 3 has nothing to
   verify against. But this raises the diff size for the introductory PR.

## Compatibility

The current `source.FilePath` / `source.StdlibRoot` wraps are
compiler-private; replacing them with `path.File` / `path.Dir` does not
break any user-facing API.

`read_file`'s signature changes from `String -> Result IoErr String !{IO}`
to `File -> Result IoErr String !{IO}`. User code that calls
`read_file("foo.txt")` directly breaks at that point and must call
`read_file(path.file("foo.txt"))`. This is a breaking change to a public
stdlib API, but it is deliberately the kind of change `stdlib.path`
exists to force.

## Sequencing

`stdlib.path` blocks on nothing in the current backlog. It can land at
any time. The natural sequencing is: complete the open `wrap` ergonomics
work (backlog item 15) first if it would simplify the `File`/`Dir` wrap
surface (e.g. parameter-level destructuring would make `match f with |
File s -> s` accessor patterns disappear), or land `stdlib.path` first
and accept the small bit of pattern-match boilerplate that the ergonomics
follow-up will later sweep.
