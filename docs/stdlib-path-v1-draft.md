# stdlib.path — v1 draft

**Status:** experimental design draft, not normative. **Partly superseded 2026-09-05** — see the
box below before implementing anything here.

> ### Superseded in part by `stdlib.fs.path`
>
> A path module landed as **`stdlib/fs/path.sprout`** (module `stdlib.fs.path`) with the
> filesystem work in `docs/stdlib-fs-v0.md`. It answers open question 1 — the module name is
> `stdlib.fs.path`, not `stdlib.path`, because it ships as the pure half of `stdlib.fs` rather than
> as a standalone facility.
>
> **What this draft got right and what landed unchanged.** The pure semantics agree completely, and
> that agreement is worth noting because the two were derived independently — this draft from a
> design conversation, the landed module from a primary-source survey (`stdlib-fs-v0.md` §3.1).
> Both give `extension("foo.tar.gz") = Just "gz"`, `extension(".hidden") = Nothing`, and no leading
> dot; both make `normalize` explicit, lexical and non-resolving rather than eager; and both take
> the same non-goals (POSIX-only separators, no absolute-vs-relative *type* distinction, no symlink
> resolution in the pure layer).
>
> **What did NOT land, and remains this document's to argue for.**
>
> 1. **The `File` / `Dir` type distinction and its smart constructors.** The landed module operates
>    on plain `String`. `stdlib-fs-v0.md` §6 records why even the weaker `wrap Path = String` was
>    declined: `read_text`/`write_text` already take `String`, so a newtype for half the module is
>    worse than none, and the only two-path signature (`rename(from, to)`) would have both sides the
>    same type — so the swap-protection a newtype buys has nothing to protect. Neither argument
>    touches the *`File`-vs-`Dir`* distinction this draft proposes, which is a stronger claim and
>    still open.
> 2. **Changing `read_file`'s signature to take a `File`.** That breaking change did not happen and
>    is not planned.
> 3. **Goal 2 — retiring the naive `str_concat` join sites** in `module_loader.module_name_to_path`
>    and `bundler.prelude_path`, with their trailing-slash and empty-root bugs. Untouched. Those are
>    compiler sources, so the migration carries the full seed protocol and is its own change; the
>    landed `path.join` is now available to do it with.
>
> Anyone reviving this draft is arguing for the typed surface specifically, against a working
> `String`-based one — so the case has to be made on the bug class it prevents, with evidence.
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
  no Windows backend today; defer until one exists. A port is planned but
  gated: [windows-port-v0.md](windows-port-v0.md) Milestone A ships a Windows
  *runtime* without ever running the compiler there, and path handling only
  becomes load-bearing at Milestone B.
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

### Construction: total, because there is nothing left to validate

**Decided 2026-09-12 as *validated*; reversed 2026-09-13 on evidence.** The
reversal is not a change of mind about the principle — it is that the premise
was false. "Validation has to happen somewhere, so make it construction" was
never checked against the runtime, which had been doing it all along.

Neither wrap carries `(..)`, so both data constructors stay module-private
(spec-v0 §5.6.1). The only entry is:

- `file: String -> File`
- `dir:  String -> Dir`

Both total. The validated form proposed two rejections, and **neither is a
real case**:

- **Empty string.** Already rejected by `fs_path_rejected`
  (`runtime/sprout_runtime.c`), which returns
  `FsInvalidPath("empty path")` before the syscall, and carries a comment
  saying why it belongs there: a syscall on `""` reports `ENOENT`, which
  classifies as "not found" — "true of nothing in particular and misleading
  about the caller's actual mistake". A check in `path.file` would be a second
  copy of that, and the runtime's is the one at the authoritative boundary.
- **Embedded NUL byte.** Unrepresentable. A Sprout `String` *is* a C string —
  an extern receives it as `const char* path = (const char*)(uintptr_t)path_i`
  and `str_len` is a UTF-8 walk over the NUL-terminated buffer — so a NUL
  terminates the string rather than sitting inside it. The case guards a state
  that cannot exist.

So `PathErr` has no constructors left and is **deleted**, which also closes the
`PathErr`/`IoErr` seam below: there is nothing to map, and no composed
`read_path` is needed.

No validation for `..` or `.` segments either — preserve exact spelling;
`normalize` is opt-in (see below). Every derived op (`dir_file(d, rel)` and the
rest) is total, as before.

**What the types buy, stated honestly.** Not "this is a file": nothing consults
the filesystem, so `file("/tmp")` succeeds and names a directory. What they buy
is the `guidelines.md` §7 distinction — a dir-path cannot be passed where a
file-path is expected — plus intent in every signature. That is worth having,
but it is weaker than "parse, don't validate", and this section should not be
read as an instance of #4.

An earlier revision of this draft kept the data constructors exported "for
cheap construction at trusted internal sites" and added `file_checked` /
`dir_checked` beside them. That does not work, for two reasons:

- **An exported infallible `file : String -> File` is the hidden constructor
  under another name.** Hiding `File` while exporting `file` enforces nothing,
  so "typed with an unchecked escape hatch" is just the labelled design with
  extra ceremony.
- **No call site wanted the escape hatch.** All 22 construction sites for the
  compiler's `source.FilePath` / `source.StdlibRoot` live in the five driver
  files and every one wraps an argv-derived string. There is no trusted-literal
  site to serve, and after migration each driver parses argv once and threads
  the typed value.

The `_checked` suffix went with the escape hatch it existed to contrast with.
`docs/guidelines.md` §2 is explicit that a suffix marking fallibility is not
needed — the `Result` return type carries it — which is also how the landed
`stdlib.fs.path` spells `extension` and `relative_to`. With construction now
total the question is moot, but the reasoning stands for any future fallible
constructor here.

Note what total construction costs and saves: `file` is now an exported
infallible `String -> File`, which the first bullet above calls "the hidden
constructor under another name" — and that is exactly right. It enforces
nothing, and it is not meant to: with no invariant left to carry, `File` is a
distinctness marker, not a proof. The saving is that no call site gains a
`let Ok … else`, so migrating the ~90 sites is mechanical.

**Prior art.** The design space is bimodal, and this draft sits at the typed
end of it:

| | File vs Dir in the type? | Construction |
|---|---|---|
| Rust `std::path` | No — one `Path`, with runtime `is_file()`/`is_dir()` | `Path::new` "directly wraps a string slice … a cost-free conversion": no validation, cannot fail |
| Haskell [`path`](https://hackage.haskell.org/package/path) | Yes — `Path b t` over base × type | Type is abstract; `parseAbsFile :: MonadThrow m => FilePath -> m (Path Abs File)` throws on invalid input |

Haskell's `path` is the closest prior art and takes the same position. Note
what it pays: four QuasiQuoters (`[absfile|/home/chris/foo.txt|]`) exist to
construct paths from literals at compile time. Sprout has no equivalent, so a
literal path here goes through the `Result` like any other string.

### v1 API surface

```sprout
module stdlib.path

# --- Construction ---------------------------------------------------

export wrap File = String
export wrap Dir  = String

# The data ctors are NOT exported: no `(..)` on either wrap above, so these
# two are the only way a String becomes a File or a Dir.
export fn file(s: String) -> File
export fn dir(s: String)  -> Dir

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

Filesystem IO lives in `stdlib.fs` — this draft said `stdlib.io`, which never
existed — and the signatures migrate to take `File` / `Dir`. The names below
are the ones `stdlib.fs` actually exports:

```sprout
# in stdlib.fs
export fn read_text (f: path.File) -> Result FsError String !{IO}
export fn write_text(f: path.File, content: String) -> Result FsError Unit !{IO}
export fn is_file   (f: path.File) -> Bool !{IO}
export fn is_dir    (d: path.Dir)  -> Bool !{IO}
export fn list_dir  (d: path.Dir)  -> Result FsError (List String) !{IO}
```

`FsError` is the shipped error type, so its shape is no longer an open
question. `read_text`/`write_text` return `Result String String` today — see
Compatibility for that inconsistency, which this migration should settle.

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

1. ~~**Module name**~~ — **closed.** It shipped as `stdlib.fs.path`, the pure
   half of `stdlib.fs`, not as a standalone `stdlib.path`. See the banner at
   the top; the rest of this document still says `stdlib.path` in places and
   should be read as naming that module.

2. **`Path` umbrella type** — should there also be a tag-union
   `type Path = AsFile File | AsDir Dir` for code that needs to be
   agnostic? Lean: skip in v1; add only if a concrete use case appears.

3. **Coexistence with current compiler code** — should the migration
   happen in the same PR that introduces the typed surface, or as a follow-up?
   Leaning: same PR — otherwise the test plan's tier 3 has nothing to
   verify against. But this raises the diff size for the introductory PR.
   Measured 2026-09-13: ~90 call sites (62 in `tests/stdlib`, 20 in
   `stdlib/compiler`, ~10 across `tools`/`repl`/`examples`/`ide`), plus 27
   lines of golden IR that regenerate. Mechanical, since construction is
   total — but it touches every example that reads a file.

4. **Does the typed surface still earn ~90 edits?** Open, and sharper now that
   `PathErr` is gone. "Parse, don't validate" pays when a parsed value is
   threaded through many functions without re-checking; paths in this repo are
   overwhelmingly parsed and used **once**. The one place that does thread a
   path — the compiler — already has `source.FilePath`/`StdlibRoot`, so
   retiring those into `path.File`/`path.Dir` is a rename, not new safety.
   What remains is the §7 mixup-prevention argument, which is real but smaller
   than the draft originally assumed.

## Compatibility

The current `source.FilePath` / `source.StdlibRoot` wraps are
compiler-private; replacing them with `path.File` / `path.Dir` does not
break any user-facing API.

`read_text`'s signature changes from `String -> Result FsError String !{IO}`
to `File -> Result FsError String !{IO}`. User code that calls
`read_text("foo.txt")` breaks at that point and must name the path first — one
mechanical edit, because construction is total:

```sprout
read_text(path.file("foo.txt"))
```

**The seam is closed** (2026-09-13). It read: `path.file` fails with `PathErr`
and `read_file` with `IoErr`, so a caller threading the two must map one into
the other — decide it with the `IoErr` shape, which was out of scope. Both
halves have since resolved themselves:

- `IoErr` is `stdlib.fs.FsError`, which landed with `stdlib.fs` — closed, eight
  constructors, and already carrying `FsInvalidPath String`.
- `PathErr` is deleted (see Construction), so there is nothing to map and no
  composed `read_path` to add.

The dependency direction would have decided it anyway: `stdlib/fs.sprout`
imports `stdlib.fs.path`, and `bundler.sprout` rejects import cycles, so `path`
cannot name `FsError`. "Absorb `PathErr` into the IO error" was never available
without moving the type.

**Still open, and unrelated to the seam:** `read_text` / `write_text` return
`Result String String` while `read_bytes`, `list_dir` and `stat` return
`Result FsError _`. Two error conventions in one module; whoever migrates these
signatures should settle that at the same time.

This is a breaking change to a public stdlib API, but it is deliberately the
kind of change the typed surface exists to force.

## Sequencing

`stdlib.path` blocks on nothing in the current backlog. It can land at
any time. The natural sequencing is: complete the open `wrap` ergonomics
work (backlog item 15) first if it would simplify the `File`/`Dir` wrap
surface (e.g. parameter-level destructuring would make `match f with |
File s -> s` accessor patterns disappear), or land `stdlib.path` first
and accept the small bit of pattern-match boilerplate that the ergonomics
follow-up will later sweep.
