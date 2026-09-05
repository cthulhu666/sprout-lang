# A real filesystem surface — `stdlib.fs` and `stdlib.fs.path` — v0

Status: **implemented**. Supporting design doc; `docs/spec-v0.md` is unaffected — this adds a
stdlib module and seven builtins, no language semantics. Milestone M1 of the TUI/IDE arc.

Two things in this document were **wrong when first written and are corrected in place**, both
caught by running something rather than by review: `stat` was specified as `lstat` with no
following variant (§3.3), and the seed was predicted not to move (§8). Where a claim was later
measured, the measurement is what is recorded.

## 1. Problem statement

`stdlib/fs.sprout` is 36 lines and exposes two functions: `read_text` and `write_text`. It cannot

- **list a directory** — there is no `readdir` anywhere in the tree;
- **ask whether a path exists**, or whether it is a file or a directory;
- **read a binary file** — `read_file` runs `utf8_validate` over the whole buffer
  (`sprout_runtime.c:2984`) and returns `Err` on any non-UTF-8 byte, so the pair genuinely cannot
  read an image, an object file, or a `.ll`;
- **create, delete or rename** anything;
- **manipulate a path** — every path helper in the tree is ad hoc and private
  (`stdlib/repl.sprout:319 module_path`, `stdlib/string.sprout`'s `rsplit_once` /
  `substring_after_last`).

Its one error channel is a human-readable `String` that its own header comment documents as
unmatchable: *"`Err` is a human-readable message, not a matchable code … Do not pattern-match its
content."* A caller therefore cannot distinguish "no such file" from "permission denied", which is
the first question any file browser asks.

The concrete forcing consumer is the IDE's project tree (M5). The alternative available today is
`process.proc_run(["find", …])` — a subprocess per directory listing, whose output is a
newline-joined `Bytes` blob that loses any filename containing a newline. That is precisely the
class of workaround this arc exists to remove.

## 2. Goals and non-goals

**Goals**

- G1. Directory listing, metadata, binary read, and create/delete/rename, as ordinary Sprout.
- G2. A **matchable** error type, so `FsNotFound` and `FsPermissionDenied` are different values
  rather than different English.
- G3. Pure, testable path manipulation with no filesystem access and no builtins.
- G4. Keep the builtin count at the minimum that rule 4 admits: anything expressible in Sprout is
  written in Sprout, even when a syscall would be fewer instructions.

**Non-goals**

- N1. **Canonicalisation (`realpath`) and symlink *creation*.** Both `stat` variants ship (§3.3),
  but there is no `canonicalize` and no `symlink`. Canonicalisation touches the filesystem, unlike
  everything in `stdlib.fs.path`, and nothing in M1–M9 needs it. The absence of a symlink *creator*
  has one visible consequence: the follow/no-follow tests build their fixture with `ln(1)` through
  `proc_run`, because no in-language call can make a symlink.
- N2. **Permissions, ownership, timestamps as a writable surface.** `Entry` reports mode-derived
  *kind* and mtime; there is no `chmod`, `chown` or `utimes`. No consumer.
- N3. **Windows path semantics.** Drive letters, UNC paths and `\` separators are out of scope;
  `stdlib.fs.path` is POSIX. The Windows CI job builds the compiler, it does not run a Sprout
  program against a Windows filesystem. Recorded so that a later Windows port is understood as a
  new decision rather than a bug fix.
- N4. **Streaming / incremental reads.** `read_bytes` reads a whole file. A partial-read surface
  belongs with the same streaming design that `proc_run` needs (see `BACKLOG.md`), not here.
- N5. **A `wrap Path = String` newtype.** Reasoning in §6.

## 3. Prior-art survey

Two decisions here are choices among established alternatives rather than free invention: what
`basename`/`extension` do at the edges, and how an I/O error is classified.

### 3.1 Path edge cases

Every row verified against the primary source named.

| input | POSIX `basename(3)` | Rust `Path` | Go `path/filepath` | Node `path` |
|---|---|---|---|---|
| `basename("")` | `"."` | `file_name()` → `None` | `Base` → `"."` | not documented |
| `basename("/")` | `"/"` | `file_name()` → `None` | `Base` → `"/"` | — |
| `basename("/usr/")` | `"usr"` | `Some("usr")` | `"usr"` | — |
| `extension(".bashrc")` | n/a | `None` | `Ext` → `".bashrc"` | `extname` → `""` |
| `extension("foo.tar.gz")` | n/a | `Some("gz")` | `".gz"` | `".md"`-style, **with** dot |
| `stem("foo.tar.gz")` | n/a | `Some("foo.tar")` | n/a | n/a |

Sources: [POSIX `basename`](https://pubs.opengroup.org/onlinepubs/9699919799/functions/basename.html)
(its EXAMPLES table is the authority for the first three rows);
[`std::path::Path`](https://doc.rust-lang.org/std/path/struct.Path.html) — *"The extension is
`None` if the file name begins with `.` and has no other `.`s within"*;
[`path/filepath`](https://pkg.go.dev/path/filepath) — *"The extension is the suffix beginning at the
final dot in the final element of path; it is empty if there is no dot"*;
[Node `path`](https://nodejs.org/api/path.html) — `path.extname('.index')` → `''`.

Two findings:

1. **Leading-dot files: 3-to-1, and Go is the outlier.** POSIX has no `extension`, but Rust and Node
   both say a name that begins with `.` and contains no other dot has *no* extension; Go's `Ext`
   returns the entire name. Go's answer makes `Ext(".bashrc") == ".bashrc"`, i.e. a dotfile is all
   extension and no stem, which no one wants. **Sprout follows Rust/Node.**
2. **Whether the dot is included is a genuine 2–2 split**, and it is decided by a feature Sprout
   has: Go and Node return `""` for "no extension", so they *need* the leading dot to distinguish
   `""` from a real empty extension. Rust returns `Option`, so the dot carries no information.
   Sprout has `Maybe`, so it takes Rust's shape: **`Maybe String`, no dot** — `Just "gz"`.

`basename("") == "."` looks odd for a pure string function, but POSIX and Go agree on it
independently, and inventing a third answer to a question two primary specs have settled is worse
than adopting theirs. Same for `dirname`. `normalize` is Go's `Clean`, whose four rules are quoted
in §4.3.

### 3.2 Error classification

| language | shape | exhaustive? | primary source |
|---|---|---|---|
| Rust | `std::io::ErrorKind` enum — `NotFound`, `PermissionDenied`, `AlreadyExists`, `NotADirectory`, `IsADirectory`, `DirectoryNotEmpty` | **No** — `#[non_exhaustive]`, and the docs say *"it is not recommended to exhaustively match against it"* | [`std::io::ErrorKind`](https://doc.rust-lang.org/std/io/enum.ErrorKind.html) |
| Go | `*fs.PathError` wrapping an `errno`, tested with `errors.Is(err, fs.ErrNotExist)` | No — sentinel comparison, open set | [`io/fs`](https://pkg.go.dev/io/fs) |

Consensus: a **classified, matchable** error, not a formatted string — which is what today's
`read_text` gets wrong. Sprout diverges on exhaustiveness, and deliberately: `FsError` is a closed
ADT with a catch-all `FsIoError` constructor, so `match` is total and the compiler can check it.
Rust and Go leave the set open because they can add variants without breaking callers; Sprout gets
the same freedom from `FsIoError` — a newly-classified errno moves out of `FsIoError` into its own
constructor, which is a breaking change only for a caller that was matching the catch-all *and*
depending on it to cover that case. That is a smaller cost than making every `match` on an fs error
inexhaustive, which is what `#[non_exhaustive]` amounts to.

`FsError` follows `TcpError`'s in-tree vocabulary (`stdlib/net.sprout:68`): a named ADT whose
payload is a human-readable detail string, plus a `fs_error_message` accessor mirroring
`tcp_error_message`.

### 3.3 Does `stat` follow a symlink?

| language | follows | does not follow | primary source |
|---|---|---|---|
| Rust | `fs::metadata` — *"This function will traverse symbolic links to query information about the destination file"* | `fs::symlink_metadata` | [`std::fs::metadata`](https://doc.rust-lang.org/std/fs/fn.metadata.html) |
| Go | `os.Stat` | `os.Lstat` — *"If the file is a symbolic link, the returned FileInfo describes the symbolic link. Lstat makes no attempt to follow the link"* | [`os`](https://pkg.go.dev/os) |

Unanimous, on both halves: **both operations are exposed, and the one named plainly `stat` is the
following one.** Sprout follows: `stat` follows, `symlink_stat` does not.

This was got wrong in the first draft, which made `stat` mean `lstat` and shipped no following
variant — and the mistake was caught by the tests, not by review. `/tmp` is a symlink to
`/private/tmp` on macOS, so `is_dir("/tmp")` answered **false** and every `make_dir_all` under
`/tmp` failed with `FsNotADirectory`. The lesson is the general one: "the more conservative
primitive" is not the same as "the right default", and a `stat` that cannot answer *"can I treat
this as a directory?"* answers nothing anyone asked.

Which variant each function uses is a decision per call site, not a global default:

| function | variant | why |
|---|---|---|
| `stat`, `exists`, `is_dir`, `is_file` | follows | the caller is asking about the thing at the end of the path |
| `symlink_stat` | does not | the caller is asking about the link |
| `read_dir` | does not | matches Rust's `DirEntry::file_type` and Go's `ReadDir`; it is also what makes a tree walk terminate, since a link to an ancestor is a leaf rather than a cycle |
| `remove_dir_all` | does not | so a link is deleted, never descended through — otherwise it would delete the link's *target's* contents |

## 4. High-level implementation overview

### 4.1 Seven builtins, and why each is one

Per "Builtin vs Stdlib" rule 4, each is a syscall with no Sprout expression. None is
performance-motivated (rule 6).

| builtin | syscall | why not Sprout |
|---|---|---|
| `fs_list_dir(path) -> Result FsError (List String)` | `opendir`/`readdir`/`closedir` | no directory-stream primitive exists |
| `fs_stat_path(path, follow) -> Result FsError Entry` | `stat` / `lstat` | no inode metadata primitive exists |
| `fs_read_bytes(path) -> Result FsError Bytes` | `open`/`read`/`close` | `read_file` validates UTF-8 and cannot return raw bytes |
| `fs_write_bytes(path, Bytes) -> Result FsError Unit` | `open`/`write`/`close` | `write_file` takes `String`, which is always valid UTF-8 with no NUL |
| `fs_make_dir(path) -> Result FsError Unit` | `mkdir` | — |
| `fs_remove(path) -> Result FsError Unit` | `remove(3)` (`unlink`/`rmdir`) | — |
| `fs_rename(from, to) -> Result FsError Unit` | `rename` | — |

The approved plan named six; `fs_write_bytes` is a seventh, added on explicit sign-off after the
tests exposed why the sixth cannot ship alone. Sprout can already send arbitrary bytes over a
**socket** (`tcp_write_all`) but has no way to put them in a **file**, so `read_bytes` would arrive
without its pair: a program could read a binary file — or receive `Bytes` from `proc_run` or a
socket — and have nowhere to put it. It is also what makes `read_bytes` testable on the case that
motivates it, since without a byte writer nothing in the language can *create* a non-UTF-8 file to
read back.

Three functions a filesystem module would normally also
carry as builtins are **written in Sprout instead**, which is the substance of G4:

- **`make_dir_all`** — split the path, fold over the prefixes calling `fs_make_dir`, treat
  `FsAlreadyExists` as success. The recursion is testable Sprout, not untestable C.
- **`remove_dir_all`** — `symlink_stat` + `list_dir` + recurse + `fs_remove`. A symlink to a
  directory reports `SymlinkEntry` and is removed rather than descended into, so it cannot delete
  the link's target's contents and cannot loop on a link pointing at an ancestor.
- **`read_dir`** — `fs_list_dir` gives names, `fs_stat` gives metadata, and pairing them is a
  `map`. This costs one `lstat` per entry that a C implementation could sometimes avoid via
  `dirent.d_type`; that is not a measured bottleneck (rule 6), and `d_type` is `DT_UNKNOWN` on
  several filesystems, so a correct C version would need the `lstat` fallback anyway.

`fs_make_dir` takes no `recursive` flag — that flag is what `make_dir_all` replaces, and dropping it
also avoids passing a `Bool` across the extern boundary.

### 4.2 Types

```sprout
export type EntryKind (..) = | FileEntry | DirEntry | SymlinkEntry | OtherEntry

export type Entry (..) =
  | Entry EntryKind Int Int   # kind, size in bytes, mtime in seconds

export type FsError (..) =
  | FsNotFound String          # ENOENT
  | FsPermissionDenied String  # EACCES, EPERM
  | FsAlreadyExists String     # EEXIST
  | FsNotADirectory String     # ENOTDIR
  | FsIsADirectory String      # EISDIR
  | FsDirectoryNotEmpty String # ENOTEMPTY, and EEXIST from rmdir
  | FsInvalidPath String       # empty path, ENAMETOOLONG, ELOOP
  | FsIoError String           # everything else; payload carries strerror
```

Positional constructors with accessor functions rather than records, matching every other extern
return in the tree (`TermSize`, `process.ProcResult`, `regex.Match`) — an extern returning a record
has no precedent in the runtime, and accessors keep the field order a private detail.

**`Entry` deliberately carries no name**, and `read_dir` returns `List (String, Entry)` instead.
The first draft had `Entry String EntryKind Int Int` with the runtime filling the name from the
path — which would have put a second implementation of POSIX `basename(3)` in C, beside
`path.basename` in Sprout, for the two to drift apart. Dropping it leaves exactly one definition of
"the last component of a path" and removes the equivalence test that would otherwise be needed to
police the duplicate.

The runtime's arity cap is what forced the question rather than what decided it:
`sprout_make_registered_obj` (`sprout_runtime.c:1506`) tops out at three fields, so a four-field
`Entry` would have needed either a `sprout_make4` on a helper the codegen paths share, or one field
dropped. `name` is the right field to drop on its own merits — it is identity, not metadata, and
the caller already has it.

### 4.3 `stdlib.fs.path` — pure, no builtins

New module `stdlib/fs/path.sprout`. `stdlib/math.sprout` + `stdlib/math/int.sprout` is the in-tree
precedent for a top-level module fronting a submodule directory of the same name.

`sep`, `is_absolute`, `basename`, `dirname`, `extension`, `stem`, `join`, `split`, `normalize`,
`relative_to`. `normalize` is Go's `Clean`, applied lexically and never touching the filesystem:

> 1. Replace multiple separators with a single one.
> 2. Eliminate each `.` path name element.
> 3. Eliminate each inner `..` along with the non-`..` element that precedes it.
> 4. Eliminate `..` elements that begin a rooted path.
> If the result is empty, return `"."`.

`relative_to(base, p)` is Rust's `strip_prefix`: `Just` the remainder when `p` is under `base`,
`Just "."` when equal, `Nothing` otherwise. It does **not** synthesise `../..` to reach a sibling —
that requires knowing whether `base` is a directory, which a pure function cannot know.

### 4.4 One addition to `stdlib.string`

`export fn split(sep: String, s: String) -> List String`. The path module needs it, and the tree
currently contains **five private re-implementations** of it: `template.split_on:81`,
`url.split_amp:70`, `ast_to_ir.split_on_comma:1198`, `http_server.split_header_lines:186`, and
`prelude.split_on_char:1692`. Argument order matches the existing `string.join(sep, xs)`.

Migrating the five duplicates onto it is a separate refactor and is **not** part of this change
(Collaboration Rule 2); it goes to `BACKLOG.md`.

## 5. Syntax and semantics impact

None. No surface syntax, no evaluation-order or visibility rule changes.

## 6. Type-system impact

None. All new types are ordinary ADTs.

**`wrap Path = String` is declined** (N5), against the approved plan's sketch. The plan proposed it
by analogy with `source.FilePath`, but the analogy does not hold here:

- `read_text`/`write_text` already take `String` and are called across the tree. Introducing `Path`
  for the six new functions only would leave one module with two path spellings; converting the
  existing two is a breaking change to every caller.
- The safety a newtype buys is preventing an argument mix-up, which needs *two* arguments of the
  wrapped type to confuse. Only `rename(from, to)` has two, and both would be `Path` — so the
  newtype would not catch the one mistake available.

Recorded as a reversible decision: if the IDE later wants it, it is introduced module-wide as one
change, not half a module at a time.

**Relationship to `docs/stdlib-path-v1-draft.md`.** That draft — non-normative, pre-dating this
work — proposes a heavier design: distinct `File` and `Dir` wrap types with `Result`-returning
smart constructors, module `stdlib.path`, and a breaking change to `read_file`'s signature. This
change does not implement it, and the draft has been annotated to say so rather than left to read
as the plan of record.

Worth stating plainly, because it is evidence rather than coincidence: the two designs were derived
independently — the draft from a design conversation, this one from the primary-source survey in
§3.1 — and their **pure semantics agree exactly**, down to `extension(".hidden") = Nothing` and the
dropped dot. Where they differ is entirely about typing discipline, which is the part §6 declines
and the draft still has an open case for. The draft's goal 2 (retiring the naive `str_concat` path
joins in `module_loader` and `bundler`, which mis-handle a trailing-slash or empty root) is also
untouched here; it edits compiler sources, so it carries the seed protocol and is its own change.

## 7. Error-message impact

New diagnostics are values, not compiler messages. `fs_error_message` yields `"<path>: <detail>"`.

The one behavioural subtlety worth stating: **`read_dir` skips an entry that disappears between the
listing and its `stat`** — an `FsNotFound` from the per-entry `stat` is dropped, any other error
propagates. A directory listing is a snapshot of a mutable thing, and an entry that no longer exists
is not in the directory; failing the whole call because of one vanished temp file would make
`read_dir` unusable on `/tmp`.

## 8. Compatibility and migration

Purely additive. `read_text`/`write_text` keep their signatures, their `String` error, and their
header comment about it. No existing caller changes.

`stdlib/fs.sprout`'s header currently asserts **"NO TYPE DECLARATIONS IN THIS MODULE, deliberately"**
because a `type` would make the module invisible to the REPL's loader. That was true when written
and is **now stale**: `docs/repl-env-type-vocabulary-v0.md` Fix A (`@type:` env markers) landed and
retired it. Measured with that document's own Appendix A probe on this branch — `stdlib.terminal`,
`stdlib.fs`, `stdlib.net`, `stdlib.http_server`, `stdlib.bytes` and `stdlib.process` all report
`OK`. The comment is replaced rather than deleted, so the constraint is recorded as lifted rather
than forgotten.

**Seed: a full `refresh-seed` IS required, and the prediction that it would not be was wrong.**
The first draft of this section reasoned that `stdlib/fs.sprout` sits outside `compile_driver`'s
import closure, as `stdlib/terminal.sprout` did for M0. It does not: `compile_driver` reads source
files, `read_file` lives in `stdlib.fs`, and `stdlib.string` is imported by a dozen compiler
modules. `verify-bootstrap-fixed-point` reported **FIXED POINT BROKEN**, which is the only reason
this is stated correctly here — the claim was hedged as "to be verified by the gates, not assumed",
and the gate is what settled it.

What the reseed actually moved, classified rather than waved through (1972 insertions / 1900
deletions in `bootstrap/compile_driver.ll`):

- **Zero `define` lines added or removed.** No compiler function changed, and `stdlib.string.split`
  is *not* in the seed — DCE prunes it, since nothing in the compiler calls it.
- `@sprout_register_ctor` calls went from **349 to 362**: exactly the 13 constructors this change
  declares (`EntryKind` ×4, `Entry`, `FsError` ×8).
- Every removed line is a renumbered `%t` temporary, `@.cname.`/`@.cfkinds.` constant, or
  `%creg_`/`%cname_ptr_`/`%cfkinds_ptr_` registration statement — i.e. the shift those 13 cause.

The golden corpus moved in 6 of 60 files with the same signature: no `define` removed anywhere, two
added (`stdlib.string.split` and its `split_go` helper), and removals confined to the same
renumbering classes. Five of the six are the files that reach `stdlib.fs` (four directly, plus the
compiler-bundle smoke shape) and so pick up the constructor shift. The sixth, `sentry_api`, gains
only the two new defines and does not call either — a DCE imprecision unrelated to this change,
filed in `BACKLOG.md` with the two hypotheses that probing refuted.

## 9. Tests

- `tests/stdlib/test_fs_path.spr` — the bulk, and entirely pure: every row of §3.1's table plus
  `join`/`split`/`normalize`/`relative_to` edge cases. Written first and confirmed RED.
- `tests/stdlib/test_fs.spr` — round-trips through a temp directory: create a tree, list it, stat
  it, read bytes back, rename, remove, and assert the *error constructors* for the failure paths
  (missing file → `FsNotFound`, `remove` on a non-empty directory → `FsDirectoryNotEmpty`,
  `list_dir` of a file → `FsNotADirectory`, a regular file blocking `make_dir_all`'s path →
  `FsNotADirectory`). Reducing every result to a constructor *name* rather than to `Ok`/`Err` is
  what makes these tests about classification: an `Ok`/`Err` test would pass equally well against
  the unmatchable-String error this change replaces.

  Two cases need fixtures the language cannot build: a **non-UTF-8 file** (created with
  `write_bytes` — the reason it had to ship with `read_bytes`), and a **symlink** (created with
  `ln(1)` through `proc_run`, since there is no symlink builtin). The symlink case pins the whole
  §3.3 table: `symlink_stat` reports the link, `stat` reports its target, and `read_dir` agrees
  with `symlink_stat`.

## 10. Spec/docs status

`docs/spec-v0.md` unaffected (§5). `docs/builtins-reference.md` gains the six builtins and the
`stdlib.fs` / `stdlib.fs.path` package surfaces. `BACKLOG.md` gains the `stdlib.string.split`
migration follow-up. This document is supporting, not normative.
