# Extern visibility v0

Status: **approved** (Kuba, 2026-10-24): module privacy only, call sites bare, declaring an extern
yourself out of scope. It changes the normative spec §"Externs are outside the module system".

## 1. Problem

An `extern fn` is a global name. Spec §"Externs are outside the module system" says an extern "is
reachable by its **bare name** from anywhere its declaring module is part of the build", that
`export` on one "parses and is discarded", and that a selective import list cannot hide one. The
bundler drops `ExternFnDecl` from each module's names, so an extern resolves like a linker symbol.

Two consequences:

- **A module cannot keep a builtin to itself.** `stdlib.mutable` declares `vector_mutset`,
  `vector_push` and `vector_truncate` for `MutVec`'s own use. Any module that imports
  `stdlib.mutable` can call them on any `Vector`. This program compiles, and prints `99` (run
  2026-10-24). A borrowed linear value is written through a `Vector` it lent out:

  ```
  import stdlib.mutable

  wrap linear Box = Vector Int

  fn peek(b: borrowing Box) -> Vector Int =
    match b with
    | Box raw -> raw

  fn first(b: borrowing Box) -> Int =
    match b with
    | Box raw -> maybe_with_default(-1, vector_get(raw, 0))

  fn main() -> Unit !{IO} =
    let b = Box(vector_from_list([1, 2, 3]))
        raw = peek(b)
    in do
      vector_mutset(raw, 0, 99)
      print(int_to_string(first(b)))
      match b with
      | Box _ -> ()
  ```

- **`export extern fn` is dead syntax.** The tree has 9 of them: 7 in `stdlib/net.sprout`, one
  each in `stdlib/task.sprout` and `stdlib/linvec.sprout`. BACKLOG P3 **`export extern fn` is
  dead syntax** counts 8; `linvec_set` came later. Each one reads as a visibility decision that
  does nothing.

Only one case is private today. An extern whose signature writes `local` is reachable only from
its declaring module (`bundler.private_extern_in`, `guard_private_extern`), because its `local` is
a promise the compiler cannot check.

PR #714 deferred a checker rule for the alias above. It claimed only `stdlib.linvec` and
`stdlib.mutable` can write a `Vector` in place, and that is false today. Under this proposal the
claim becomes: a module can write a `Vector` in place only if it is one of those two, or if it
declares the extern itself (§2).

## 2. Goals and non-goals

Goals:

- An extern is private to its declaring module unless it is written `export extern fn`.
- `export` on an extern means what it means on a `fn`.
- The `local` rule becomes a special case of the general one: an extern that writes `local` stays
  private, and `export` on one is an error.
- Prelude externs, and the `no_prelude` floor (spec §"`no_prelude`"), are exported implicitly.
  They are reachable everywhere, as today.

Non-goals:

- **Stopping a module from declaring an extern itself.** Today "a module that declares an extern
  itself may always call it" (spec), and so it stays. Any program can write
  `extern fn vector_mutset(v: Vector a, i: Int, val: a) -> Unit !{IO}` and call it, and
  `tests/stdlib/test_extern_redeclare.spr` does exactly this kind of redeclaration. So privacy
  does not close §1's hole. It moves the hole from "import a module" to "declare the extern", which
  is deliberate FFI and visible in the source. That plays the role Rust's `unsafe` plays at an FFI
  call (§3). Rust's `unsafe` is a checked marker, though, and `extern fn` here is only a
  declaration. Gating the declaration is the open question in BACKLOG P3 **Unsafe primitives**.
- **Qualified call sites.** The symbol stays bare at link time, and so does the call (§4.2).
- **Selective import lists.** They keep having no effect on externs, exported or not. Naming an
  extern in one stays accepted. Making a list hide an exported extern needs a new binding path,
  because externs never enter a module's resolution maps (`bundler.sprout`, `parser.sprout`'s
  `surface_globals`). It can come later.

## 3. Prior art

Every quote is from the language's own reference.

| language | FFI declaration is | visibility | unsafe gate |
|---|---|---|---|
| Rust | an item in the module's value namespace | per item, private by default | calling is unsafe |
| Haskell 2010 | a new top-level variable | as any top-level value: the export list | none; `safe`/`unsafe` is a calling convention |
| OCaml | a value of the module | a signature can hide it | none |
| Zig | a top-level declaration | private to its file unless `pub` | none |

- Rust, *External blocks*: "The external block defines its functions and statics in the value
  namespace of the module or block where it is located", with `Visibility?` on each item in the
  grammar. *Visibility and privacy*: "By default, everything is _private_". Calling one is
  unsafe: "A function declared in an extern block is implicitly `unsafe` unless the `safe`
  function qualifier is present."
- Haskell 2010 §8: an import declaration "declares the variable v of type t to be defined
  externally", and "an import declaration defines a new variable". §5.2: a value "whether declared
  in the module body or imported" may be exported, and with no export list "all values, types and
  classes defined in the module are exported".
- OCaml manual §23.1.1: `external` "defines the value name name as a function", and such functions
  "can be specified in interface files or sig…end signatures either as regular values … thus
  hiding their implementation as C functions".
- Zig reference, keyword `pub`: "The pub in front of a top level declaration makes the declaration
  available to reference from a different file". `@hasDecl` "returns false for private
  declarations".

All four agree that an FFI declaration is an ordinary module name, with the same visibility as any
other. Sprout is the outlier. They differ on the unsafe gate. Only Rust has one, and it is on
calling, not on declaring.

## 4. Design

### 4.1 Rule

An extern is **exported** if some module in the build that declares it writes `export extern fn`,
or if the prelude or the floor declares it. Otherwise it is **private**, and only a module that
declares it may call it. An exported extern is reachable as today: by bare name, from anywhere its
declaring module is part of the build.

An extern whose signature writes `local` is private whether exported or not. `export` on one is a
compile error, because it would promise what the `local` rule withholds:

```
`export` on extern `__task_fork` has no effect: its signature writes `local`, so it is
reachable only from `stdlib.task`. Remove `export`
```

Two declarations must still agree on the signature (`infer.extern_decl_ok`, unchanged).

### 4.2 Call sites stay bare

An exported extern keeps its bare name at the call site, so the migration adds `export` to each
public-API extern and touches no call site (§5). The cost is that two modules cannot export
different externs under one name. That is already true, because the signatures must agree.

Rejected: qualified call sites (`bits.bit_and`). That would make an exported extern behave exactly
like an exported `fn`. But it rewrites 727 call sites in this repo (§5), and more in the other three
repos, and it needs a qualified name to lower to a bare symbol. It can come later, on its own,
without breaking anything this proposal adds.

### 4.3 The other layers (AGENTS.md rule 9)

- **Caller, in the checker.** A rule that a binder unwrapped from a borrowed linear value may not
  escape (`docs/linear-publish-v0.md` §7). It was declined in PR #714 because it leaves the owned
  duplicate `(raw, Box(raw))` and `id(raw)` open, and Sprout has no `Copy` or `copy` to exempt
  harmless fields. That argument is unchanged.
- **Representation.** Type the four mutating externs (`vector_mutset`, `vector_push`,
  `vector_truncate`, `linvec_set`) on an opaque type that only their module can name, such as a
  private `wrap Raw a = Vector a` in `stdlib.mutable`. Then a user's redeclaration over `Vector a`
  fails `extern_decl_ok`'s signature check. It loses for two reasons. The check fires only while
  `stdlib.mutable` is in the build, so dropping the import reopens the hole. And it puts a
  conversion at every `MutVec` operation for that partial gain.
- **Module: this proposal.** It is general, not about `Vector`. It closes the accidental path:
  importing a module for one thing and reaching its internals. And it gives `export` a meaning.
  What remains is deliberate FFI (§2).

### 4.4 Implementation

1. **AST.** `ast.ExternFnDecl` gains an `Exported` / `Private` field, set by
   `parser.parse_extern_fn_decl` from a preceding `export`. `parser.scan_exported_decl` stops
   discarding it, and `ModuleSurface`'s globals carry the flag, so completion offers only what a
   module may reach.
2. **Bundler.**
   - `private_externs_in_module` skips the prelude and the floor (module `""`).
   - The externs list becomes the names that no declaring module exports, plus every name whose
     signature writes `local`. Today `foreign_externs` builds name → module last-wins
     (`dict_set`). Under the general rule, a private redeclaration in one module would then hide an
     exported extern from every other module. That is why exportedness is decided per name across
     the build.
   - `guard_private_extern` is unchanged. It already resolves another module's private extern to a
     marked name, which `infer` reports, naming the declaring module.
3. **`export` on a `local` extern.** `private_extern_in` sees both facts, so it raises §4.1's
   error.
4. **Diagnostic.** The message `source.sprout` renders for the `local` case gains a general form:
   "`vector_mutset` is private to `stdlib.mutable`. Mark it `export extern fn` there, or declare it
   in this module". It keeps the `local` wording for a `local` extern.
5. **`.iface`: nothing yet.** `iface_codec` records schemes, constructors, classes and instances.
   It has no externs, and nothing compiles against an interface (`compile_driver --emit-iface`).
   When an externs section is added, it must carry the flag.

## 5. Compatibility and migration

**Breaking.** In this repo, 38 of the 111 externs that `stdlib/` declares outside the prelude are
called by bare name from a file that does not declare them. That is 727 call sites in 94 files.
The count is a regex scan of `stdlib/`, `tests/`, `examples/`, `ide/` and `testsupport/`
(2026-10-24). A call site is `name(` in a file with no `extern fn name` declaration; names the
prelude declares are excluded.

Each of the 111 gets a decision in the migration PR:

- **Called from another module: gains `export`; its callers are unchanged.** Examples:
  `stdlib.bits`'s `bit_*`, `stdlib.string`'s byte-offset functions, `bytes_from_utf8`,
  `term_write`, `split_words`, `json_stringify`, and the 9 already marked.
- **A bridge its own module wraps: stays private.** Its module's Sprout wrapper is the API, as
  `read_text` is over `read_file`. Decided by Kuba, 2026-10-24: 56 such externs had no caller
  outside their module, here or in uncharted-suns, repbit or sprout-sqlite. Examples: the `fs_*`,
  `crypto_*`, `term_*` and `bytes_builder_*` sets, `http_request`, `proc_run_vec`.
- **Internal: stays private.** Examples: `vector_mutset`, `vector_push`, `vector_truncate`,
  `vector_get_direct`, `__task_fork`, `__task_await`. A caller in another file declares it itself:
  - The tests under `tests/stdlib/` that call one, as `test_extern_redeclare.spr` already does.
  - The four `tests/b1_fixtures/fixture_b1_*.spr`, which call `vector_get_direct` after
    `import stdlib.mutable` and declare nothing. `just test` does not run them; `just b1-gate`
    does, so the migration PR runs it.
- **`vec_make_filled`** is declared in `stdlib.mutable` and called by `stdlib.linvec`
  (`linvec_new`), which declares no copy. It is pure: it allocates a fresh vector and changes
  nothing that exists. So it gains `export`.

The other three repos (uncharted-suns, repbit, sprout-pg) get the same scan before the change
lands. A call that breaks gets §4.4's diagnostic, which names the module and the fix.

## 6. Tests

- Conformance, rejected:
  - §1's program, at `vector_mutset`, naming `stdlib.mutable`;
  - a call to a `package_resolution` demo module's private extern;
  - `export` on an extern that writes `local`.
- Conformance, accepted:
  - an exported extern called by bare name;
  - a module calling its own private extern;
  - a module that redeclares another module's private extern (§2's non-goal, pinned);
  - a prelude extern and a floor extern from a user module and a `no_prelude` file.
- Collision: module A exports `tcp_write` and module B redeclares it without `export`. A third
  module may still call it.
- The existing `private_extern_local_*` fixtures keep passing under the general rule.

## 7. Spec and docs

- Spec §"Externs are outside the module system" is rewritten. An extern becomes a module-scoped
  name for visibility only, and its resolution stays bare. The governing rule on where a builtin is
  declared stays. The `no_prelude` floor text says floor externs are exported.
- `docs/linear-publish-v0.md` §7 and PR #714: the deferral's premise becomes the claim in §1's last
  paragraph, and the text says so.
- BACKLOG: the P3 **`export extern fn` is dead syntax** entry closes. The **Unsafe primitives**
  entry gains a line: module privacy is in place, and the open question is a gate on *declaring*.
