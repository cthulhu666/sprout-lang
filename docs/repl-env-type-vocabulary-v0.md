# Type vocabulary in env-schemes mode (REPL / analysis service) — v0

Status: **design proposal, not implemented.** Normative spec is unaffected (this is a
compiler-internal defect, not a language-semantics change). Diagnostics behaviour changes;
see §7.

## 1. Problem statement

Eleven of the twenty-seven importable top-level `stdlib/` modules are silently invisible in the
REPL. Importing one reports `ok`, and every name from it is then `Unknown variable`:

```
sprout> import http_server
ok
sprout> http_server.default_config()
error: check: Unknown variable: http_server.default_config
```

The same wall blocks types *declared in the REPL itself*:

```
sprout> type Box = Box (Vec Int)
error: check: type-validation: unknown type name `Vec` in declaration `Box`
sprout> type Pair = Pair Int Int
ok
```

### 1.1 Causal chain

1. **`ok` is truthful but uninformative.** `op_session_update`
   (`stdlib/compiler/analysis_service_driver.sprout:915`) re-checks the synthetic session module
   after appending the line. At that point the import is *unused*, and an import contributing zero
   names checks clean. The `ok` reports that the session still type-checks, not that the module
   loaded.

2. **`load_module` swallowed a real error.** `stdlib/compiler/module_loader.sprout:366`:

   ```sprout
   | checker.CheckErr _ _ -> Nil
   ```

   A module that fails to check contributes an empty pair list. `apply_import_spec` then computes
   `prefix_pairs(Nil, "http_server")` = `Nil` — no diagnostic, no warning.

3. **The swallowed error is the strict type-name validation pass.** `infer.sprout:4729-4730`:

   ```sprout
   let declared_types = collect_declared_type_names(decls, builtin_type_names())
   match validate_all_decls(decls, declared_types) with
   ```

   `collect_declared_type_names` (`infer.sprout:4798`) walks **only the module's own decls**, seeded
   with a hardcoded primitive list (`infer.sprout:4791-4795`): `Int Double Bool String Char Unit
   Bytes IntRange` plus the C-runtime opaques `Vector Map NativeSet Ref`. `Result`, `Maybe`, `Vec`,
   `Dict` and `MutVec` are none of those — they are ordinary declarations in `stdlib/prelude.sprout`
   (lines 36, 4, 58, 61).

   In the file-compilation path the prelude is *bundled inline*, so `type Dict v` is physically
   present in the same `decls` list and validation passes. In the REPL path the prelude arrives as
   **env schemes** — a `List (String, types.Scheme)` — and a decl-scanning pass finds nothing to
   scan. Same source file, two answers, depending on how the prelude was supplied.

4. `stdlib.http_server` therefore contributes no pairs, and the eval reports `Unknown variable`
   from `infer.sprout:866`.

### 1.2 Blast radius (measured)

A probe replaying `load_module`'s exact steps (Appendix A) over every top-level `stdlib/` module
except `prelude` itself:

| module | swallowed error |
|---|---|
| `args` | unknown type name `Dict` in declaration `Args` |
| `compiler` | unknown type name `Vec` in declaration `CompilerSession` |
| `http_client` | unknown type name `Result` in declaration `HttpResult` |
| `http_server` | unknown type name `Dict` in declaration `HttpRequestMeta` |
| `linalg` | unknown type name `MutVec` in declaration `Vec3` |
| `log` | unknown type name `Vec` in declaration `Logger` |
| `net` | unknown type name `Result` in declaration `TcpResult` |
| `repl` | unknown type name `Vec` in declaration `SubmissionResult` |
| `scram` | unknown type name `Result` in declaration `ScramResult` |
| `template` | unknown type name `Vec` in declaration `Template` |
| `http_middleware` | *cascade* — `log` fails to load, so `log.info_f` is unknown in its body |

Loading cleanly: `bytes chan collections crypto http json math mutable process regex rng string
task terminal test url`.

Scope note: the sweep covers top-level `stdlib/*.sprout` only. The `stdlib/compiler/` and
`stdlib/math/` submodule trees are not included and are likely to add further hits — the two
top-level modules that front them, `stdlib.compiler` and `stdlib.repl`, both fail.

The trigger is precise. The pass covers **TypeDecl constructor fields, RecordDecl fields, and
AliasDecl RHS** only — `infer.sprout:4771` records that "ClassDecl/InstanceDecl/FnDecl signature
positions are not yet covered". That is why `url.parse_query -> Vec (String, String)` loads fine
while `type alias TcpResult a = Result TcpError a` is fatal.

### 1.3 Why this recurs

This is the second instance of the same divergence. The first was the REPL rejecting a
`where ToString a` constraint: a decl-scanning pass met an env that carries schemes rather than
declarations. Any pass that answers a question by walking `decls` will disagree between the two
paths, and `load_module`'s `CheckErr -> Nil` guarantees the disagreement is silent.

Note that the pass one line below the broken one already does the right thing
(`infer.sprout:4733`):

```sprout
match validate_constraints_all(decls, collect_declared_class_names(decls, class_names_from_env(env, Nil))) with
```

Class-name validation seeds its vocabulary from `decls` **and** from the env, by scanning `@class:`
marker keys (`class_names_from_env`, `infer.sprout:4918`). Type-name validation ignores `env`
entirely. The fix below is a near-exact mirror of the working sibling.

## 2. Goals and non-goals

**Goals**

- G1. An imported module's type vocabulary is visible to the type-name validation pass in
  env-schemes mode, for prelude types and for cross-module imports alike.
- G2. A module that fails to check during import surfaces its error instead of silently
  contributing nothing.
- G3. No behaviour change in the file-compilation path (bundled prelude).

**Non-goals**

- N1. Extending the validation pass to `FnDecl`/`ClassDecl`/`InstanceDecl` signature positions.
  That is a separate pre-existing gap (`infer.sprout:4771`) and widening coverage while fixing the
  vocabulary would confuse two changes.
- N2. Kind/arity checking of type applications. `@type:` carries names; `Dict Int Int` remains
  unification's problem, exactly as it is today for module-local types.
- N3. Constructor field metadata, class methods, instance heads. Those already ride the env via
  `@class:`/`@inst:` markers, and the `.iface` work stream (§4.3) owns the richer story.
- N4. Merging `module_loader` with `iface_codec`. Related, larger, and separable (§4.3).

## 3. Prior-art survey

The question — *does a module interface carry type declarations, or only the types of values?* —
is one every language with separate compilation has answered, and the answer is unanimous.

| language | interface artifact | carries type declarations? | primary source |
|---|---|---|---|
| Haskell (GHC) | `.hi` interface file | Yes — "the types of exported functions, **definitions of data types**, and so on" | [GHC User's Guide, Filenames and separate compilation](https://downloads.haskell.org/ghc/latest/docs/users_guide/separate_compilation.html) |
| OCaml | `.cmi` compiled interface | Yes — interfaces "declare value names with their types, **define public data types, declare abstract data types**, and so on" | [OCaml Manual §Batch compilation](https://ocaml.org/manual/5.2/comp.html) |
| Rust | crate metadata (`.rmeta` / `rlib`) | Yes — "information about exported macros, **traits, types**, and items … anything that's needed to be known when a path references something inside a crate dependency" | [rustc dev guide, Libraries and Metadata](https://rustc-dev-guide.rust-lang.org/backend/libs-and-metadata.html) |

Consensus, with no notable divergence: the unit of cross-module knowledge is a *signature*
containing types and values together, never a value-only symbol table. Sprout's env pair list
(`List (String, types.Scheme)`) is a value-only symbol table with three ad-hoc escape hatches
bolted on (`@class:`, `@inst:`, `@linear:`). The proposal below adds the fourth, which is the
cheap correct step; §4.3 records the structural direction the survey actually points at.

## 4. High-level implementation overview

### 4.1 Fix A — a `@type:` marker family (primary)

Sprout already routes type-level facts through the env using `@`-prefixed marker keys keyed by
*type name*, e.g. `infer.sprout:1021`:

```sprout
dict_set("@linear:" ++ name, types.mono(types.TConst(types.type_id("linear"))), env)
```

`module_loader` treats every `@`-prefixed key specially and by design: `is_marker_key`
(`module_loader.sprout:234`) makes `prefix_pairs` pass markers through **unprefixed**
(`:247`) and makes `select_pairs` retain them **even on a selective import** (`:256`). So a
`@type:` marker is carried by both `import stdlib.net` and `import stdlib.bytes (Utf8Error, …)`
with no further plumbing. That property is why this shape covers cross-module imports and a
prelude-only patch would not: `net` selectively imports `Utf8Error` from `bytes` and uses it in
signature positions today — the moment N1 is lifted, a prelude-only seed breaks again.

Three edits:

1. **Emit.** Where a `TypeDecl`/`RecordDecl`/`AliasDecl`/`WrapDecl` is registered, also
   `dict_set("@type:" ++ name, …, env)` with a dummy scheme, mirroring `@linear:`.
2. **Read.** Add `type_names_from_env(env, acc)` mirroring `class_names_from_env`
   (`infer.sprout:4918-4930`) — scan for the `@type:` prefix, `after_last_dot` the name,
   `list_add_unique`.
3. **Seed.** Change `infer.sprout:4729` to
   `collect_declared_type_names(decls, type_names_from_env(env, builtin_type_names()))`.

Edit 3 is a one-line change that makes the broken pass structurally identical to the working one
beside it.

### 4.2 Fix B — stop swallowing `CheckErr` (secondary, smaller)

`load_module`'s silent `Nil` is *deliberate* for non-stdlib and unresolvable modules — they are
pre-seeded in `builtin_env`, and the doc comment at `module_loader.sprout:335` says so. Any change
here must distinguish:

- *intentionally skipped* — `module_name_to_path` returned `Nothing`; keep `Nil`, silently.
- *found on disk but failed to check* — `read_file` succeeded and `tokenize`/`parse_program`/
  `check_program_with_env` then failed; this must surface.

Only the second class changes. Fix A removes the current instances, but the swallow is what turned
a one-line diagnostic into a multi-hour investigation, and the next divergence will land in the
same trap. Recommended shape: return a result type distinguishing the two, and have
`op_session_update` report the underlying error, so `import stdlib.net` fails loudly at the import
line rather than mystifying the user one command later.

### 4.3 What the survey points at, deferred

There are two module-interface mechanisms in the tree that do not know about each other:

- `module_loader.load_module` — re-checks source, extracts **value schemes + markers**. Used by
  the REPL, analysis service, and LSP.
- `iface_codec` — `IfaceFile` v6 carrying **schemes, ctor infos, class infos, instance infos**
  (`iface_codec.sprout:370-404`). Used only by `compile_driver`'s `--emit-iface`/`--read-iface`
  (`compile_driver.sprout:187-241`), per `docs/iface-precompiled-modules-v1-draft.md`.

The second is the artifact the prior-art survey describes; the first is the value-only table that
produced this bug. Converging them — having the REPL path consume the same interface the iface
work stream is already building — is the structural fix, and would retire this bug class rather
than patching its current instance. It is out of scope here: the iface series is phased and
independently sequenced, and Fix A unblocks users now at a fraction of the cost. Recorded so the
`@type:` marker is understood as a deliberate interim step, not the end state.

## 5. Syntax and semantics impact

None. No surface syntax changes; no change to what a valid program means. The change makes the
REPL accept programs it currently rejects — programs the file compiler already accepts.

## 6. Type-system impact

None to the type system proper. The change restores an existing validation pass's intended input
in one execution mode. Two prior looseness properties are inherited unchanged, and are worth
stating so they are not mistaken for regressions:

- **Name-only vocabulary.** `@type:` records names, not kinds. `Dict Int Int` passes name
  validation and fails later in unification — the same as a module-local type today (N2).
- **Markers are global and unprefixed by construction.** A `@type:` for a *private* type in module
  A enters module B's validation vocabulary. This matches how `@class:`/`@inst:` already behave.
  The consequence is bounded: the vocabulary gates *validation*, not *resolution*, so at worst a
  reference to an unreachable type is rejected later by unification with a less pointed message
  than the validation pass would have given. Emitting for exported types only is the tighter
  option; it needs `collect_marker_pairs` to become filterable and is not obviously worth it.
  **Open question for review — recommendation: emit for all declared types, accept the looseness,
  match the existing marker families.**

## 7. Error-message impact

Three changes, all improvements:

1. The `type-validation: unknown type name …` error stops appearing in the REPL for prelude and
   imported types. It remains correct and reachable for a genuinely undeclared type.
2. `Unknown variable: <module>.<name>` after a successful-looking import stops appearing for the
   eleven modules in §1.2.
3. (Fix B) A module that fails to check during import reports *its* error at the import line,
   instead of `ok` followed by an unrelated-looking failure later.

No message text is reworded.

## 8. Compatibility and migration

No migration. No source change in `stdlib/` or user code. The file-compilation path is untouched
(G3) — the bundled prelude already puts these names in `decls`, so `type_names_from_env` adds
names already present and `list_add_unique` absorbs the duplicates.

On the seed, to be unambiguous: the IR emitted for *compiled programs* does not change, so the
golden-IR gate (DoD #12) should stay clean. The seed itself **does** need a full `just refresh-seed`
— the implementation edits `stdlib/compiler/infer.sprout` and `module_loader.sprout`, so
`bootstrap/compile_driver.ll` changes and DoD #9 applies. This is not a `seed-fp-ack` case.

## 9. Tests added/updated

Per Definition of Ready #2, these are written and confirmed failing before implementation:

1. **Regression, driven through the real REPL path** — not `--phase check` on a file, which passes
   today and would prove nothing. Per the standing lesson from the `where ToString a` bug, drive
   `compile_source_with_cache` / a real `build/sproutd`:
   - `import stdlib.net` then `net.tcp_error_message` resolves.
   - `import stdlib.http_server` then `http_server.default_config()` evaluates.
2. **Session-local declaration** — `type Box = Box (Vec Int)` is accepted in a REPL session.
   Distinct code path from (1): no import involved, the session's own decls hit the same pass.
3. **Selective import carries the marker** — `import stdlib.bytes (Utf8Error, from_string)` makes
   `Utf8Error` usable in a session-declared type. Guards the `select_pairs` path, which is the
   half a prelude-only fix would miss.
4. **Negative** — a genuinely undeclared type name still errors with the same message. Confirms
   the fix widens the vocabulary rather than disabling the pass.
5. **Coverage gap in the edited file** (Definition of Ready #4) — `type_names_from_env` on an env
   with no `@type:` markers returns the seed unchanged.
6. **Fix B** — a module that is found but fails to check surfaces its error; a module that is
   unresolvable still returns `Nil` silently. Both directions, or the builtin path breaks.

A cheap standing guard worth considering: assert every stdlib module loads cleanly through
`load_module`. That is exactly the probe in Appendix A, and it would have caught all eleven.

## 10. Spec/docs status

`docs/spec-v0.md` is **unaffected** — no normative rule changes. This document is a supporting
design doc; it does not override the spec. On landing, update `BACKLOG.md` §Native REPL &
Analysis Service to mark the item done, and add a line to `docs/compiler-internals.md` recording
the invariant, which is the durable lesson:

> Any pass that derives a fact by scanning `decls` must also read that fact from `env`, because the
> REPL/LSP/analysis-service path supplies imported modules as schemes and markers rather than as
> declarations. `class_names_from_env` is the reference implementation.

---

## Appendix A — reproduction probe

Replays `load_module`'s steps and prints the error it swallows. Emit with
`compile_driver_bin_stage1 --emit-ir stdlib <file>`, link against `runtime/*.c`.

```sprout
module probe
import stdlib.compiler.module_loader as module_loader
import stdlib.compiler.checker as checker
import stdlib.compiler.lexer as lexer
import stdlib.compiler.parser as parser
import stdlib.compiler.source as source

fn report(name: String) -> Unit !{IO} =
  do
    cache <- module_loader.new_cache()
    prelude_pairs <- module_loader.load_prelude_pairs("stdlib", cache)
    match module_loader.module_name_to_path(source.ModuleName(name), "stdlib") with
    | Nothing -> term_write(name ++ ": NO PATH\n")
    | Just path ->
        match read_file(path) with
        | Err _ -> term_write(name ++ ": READ ERROR\n")
        | Ok src ->
            do
              import_pairs <- module_loader.build_import_pairs(module_loader.collect_imports(src), "stdlib", cache)
              match lexer.tokenize(source.strip_headers(src)) with
              | Err _ -> term_write(name ++ ": TOKENIZE ERROR\n")
              | Ok tokens ->
                  match parser.parse_program(tokens) with
                  | Err _ -> term_write(name ++ ": PARSE ERROR\n")
                  | Ok prog ->
                      do
                        result <- checker.check_program_with_env(prog, prelude_pairs ++ import_pairs)
                        match result with
                        | checker.CheckErr _ msg -> term_write(name ++ ": CHECK ERROR: " ++ msg ++ "\n")
                        | checker.CheckOk _ -> term_write(name ++ ": OK\n")

fn main() -> Unit !{IO} =
  list_each(report, ["stdlib.net", "stdlib.http_server"])
```
