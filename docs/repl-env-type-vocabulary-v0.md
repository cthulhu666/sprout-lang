# Type vocabulary in env-schemes mode (REPL / analysis service) — v0

Status: **Fix A implemented** (§4.1) together with two import-list completions (§4.4). Fix B (§4.2)
and the two defects this work exposed (§11) are filed, not fixed. Normative spec is unaffected —
this is a compiler-internal defect, not a language-semantics change — though §11.2 raises a
visibility question the spec will need to settle. Diagnostics behaviour changes; see §7.

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

### 1.2.1 State after this change (measured)

Rerunning the same probe with the fix in §4.1 applied, plus the two one-line import completions
described in §4.4:

| before | after | module |
|---|---|---|
| broken | **loads** | `args`, `compiler`, `http_client`, `linalg`, `log`, `net`, `template` |
| broken | still broken | `http_middleware`, `http_server`, `repl`, `scram` |

Seven of eleven restored, and — importantly — **the four that remain now fail for a single
different root cause**, unrelated to type vocabulary:

```
repl:            Type mismatch: StatefulSession vs compiler.StatefulSession
http_middleware: Type mismatch: Logger vs log.Logger
scram:           Type mismatch: CryptoError vs crypto.CryptoError
http_server:     Type mismatch: Utf8Error vs bytes.Utf8Error
```

One imported type reaches two places under two different names — bare in one, alias-qualified in
the other — and unification treats them as distinct. That is a type-*identity* defect, not a
vocabulary one, and it is filed separately (§11). No module that loaded cleanly before this change
regressed.

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

Three edits, as implemented:

1. **Emit.** `mark_declared_types(names, env)` folds one `@type:<TypeName>` marker per declared
   type into the env, mirroring `mark_type_multiplicity` beside it. It is fed from
   `collect_declared_type_names(decls, Nil)` — the *same* walk the validation pass uses, so the
   exported vocabulary and the validated vocabulary cannot drift, and `TypeDecl`/`RecordDecl`/
   `AliasDecl`/`WrapDecl` are covered uniformly by construction rather than at four call sites.
2. **Read.** `type_names_from_env(env, acc)` mirrors `class_names_from_env` line for line.
3. **Seed.** In `typecheck_decls`, the vocabulary becomes
   `own_type_names ++ type_names_from_env(env, builtin_type_names())`, and the marker-bearing env
   is what flows into `typecheck_decls_inner` — so it reaches importers as the checked env.

Edit 3 makes the broken pass structurally identical to the working one beside it.

Threading note: `ftv_env` is computed over the marker-bearing env. That is safe by construction —
a marker's scheme body is a `TConst`, never a free type variable — and stated in-file so the two
env values are not "fixed" back apart later.

### 4.2 Fix B — stop swallowing `CheckErr` (secondary, smaller)

`load_module`'s silent `Nil` is *deliberate* for non-stdlib and unresolvable modules — they are
pre-seeded in `builtin_env`, and the doc comment at `module_loader.sprout:335` says so. Any change
here must distinguish:

- *intentionally skipped* — `module_name_to_path` returned `Nothing`; keep `Nil`, silently.
- *found on disk but failed to check* — `read_file` succeeded and `tokenize`/`parse_program`/
  `check_program_with_env` then failed; this must surface.

Only the second class changes. Fix A removes most of the current instances, but the swallow is what
turned a one-line diagnostic into a multi-hour investigation, and the next divergence will land in
the same trap. Recommended shape: return a result type distinguishing the two, and have
`op_session_update` report the underlying error, so `import stdlib.net` fails loudly at the import
line rather than mystifying the user one command later.

**Deferred out of this change, with measurement.** `load_module` returns
`List (String, types.Scheme)`; threading a `Result` through it reaches `apply_import_spec`,
`build_import_pairs_acc`, `build_import_pairs` and `load_prelude_pairs`, whose callers are **12
sites across 5 driver modules** (`compiler.sprout`, `analysis_service_driver.sprout`,
`lower_driver.sprout`, `type_driver.sprout`, plus `module_loader` itself), each of which must then
decide what to do with an error it currently cannot receive. That is an error-plumbing refactor,
and Collaboration Rule 2 says not to land one on top of a semantics change. The alternative — a
side-channel `Ref` of load failures — is the kind of workaround this project declines.

One trap for whoever does it: `load_module` calls `cache_put(cache, name, Nil)` *before* loading,
to break import cycles, and on failure that `Nil` stays cached. The `ModuleCache` is held on
`StartupState` and shared across every session op, so a naive fix reports the error once and then
silently serves the cached `Nil` to every later import of that module. Either do not cache
failures, or cache the failure itself.

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

### 4.4 Two import lists completed (included here)

With Fix A in place, `net` and `template` still failed — on *constructors*, not type names:

```
net:      Unknown constructor: Utf8DecodeError
template: Unknown constructor: JsonFloat
```

Neither is a compiler gap. `stdlib/net.sprout:3` imports the type `Utf8Error` from `bytes` but not
its constructor `Utf8DecodeError`, which it then applies; `stdlib/template.sprout:12` lists eleven
`Json` constructors and omits `JsonFloat`, which it then matches on at two sites. The bundling path
inlines the whole module and so never noticed; the env path honours the selective list. Adding the
two missing names is correct under either reading of selective-import semantics — you should import
what you use — so both one-line completions are included here, and each clears its module entirely.

This does raise a real semantics question the change does **not** decide: should
`import M (T)` bring `T`'s constructors into scope, the way `T(..)` does in Haskell? Sprout's
`select_named_pairs` matches names exactly, so today it does not, while the bundler effectively
does. Filed in §11; the two completions above are correct whichever way it is ruled.

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

No migration, and no change required in user code. The file-compilation path is untouched (G3) —
the bundled prelude already puts these names in `decls`, so `type_names_from_env` adds names
already present and duplicates are absorbed (the vocabulary is only ever membership-tested).

Two `stdlib/` source lines do change (§4.4): `net.sprout` and `template.sprout` gain the
constructor names they already use. Both are no-ops for the bundling path, which had those
constructors in scope regardless.

On the seed, to be unambiguous: the IR emitted for *compiled programs* does not change, so the
golden-IR gate (DoD #12) should stay clean. The seed itself **does** need a full `just refresh-seed`
— the implementation edits `stdlib/compiler/infer.sprout` and `module_loader.sprout`, so
`bootstrap/compile_driver.ll` changes and DoD #9 applies. This is not a `seed-fp-ack` case.

## 9. Tests added/updated

`tests/stdlib/compiler/test_repl_type_vocabulary.spr` — four assertions, written first and
confirmed RED (3 failing, 1 passing) against the unmodified compiler, GREEN after. It drives
`compile_source_with_cache`, the path the REPL actually uses; `--phase check` on a file passes
today and would prove nothing. Modelled on `test_repl_constraint_check.spr`, the regression test
for the first instance of this same divergence.

1. **Session-local declaration** — `type Box = | Box (Vec Int)` is accepted in a session. No import
   involved: the session's own decls hit the pass with the prelude supplied as schemes.
2. **Aliased import resolves** — `import stdlib.args` then `args.arg_flag(args.parse(t), "x")`.
   `args` declares `Args (Dict String) …` and contributed nothing before the fix.
3. **Selective import carries the vocabulary** — `import stdlib.bytes (Utf8Error, from_string)`
   makes `Utf8Error` usable in a session-declared type. Guards the `select_pairs` path, the half a
   prelude-only fix would miss.
4. **Negative** — `type Bad = | Bad (NoSuchType Int)` is still rejected. Confirms the fix widens
   the vocabulary rather than disabling the pass; this one passed in the RED run, which is how we
   know the pass was live and the other three failures were real.

The negative case doubles as the coverage-gap test required by Definition of Ready #4: it is the
first test to exercise `validate_te`'s rejection path with a non-empty env-derived vocabulary.

Note the test file deliberately does **not** use Kuba's original `http_server.default_config()`
repro — that module is still blocked by the separate defect in §11, and a test asserting it would
fail for a reason this change does not own. It becomes the natural regression test for §11.

Two gaps worth recording rather than pretending away:

- **`just test-file` does not set `SPROUT_STDLIB_ROOT`.** This test, like
  `test_repl_constraint_check.spr`, no-ops to `0 passed, 0 failed` without it and reports
  `SUITE PASSED`. Only the full `just test` runner (`justfile:416`) sets it. A test that silently
  skips and reports success is a trap for the next person doing fast iteration; worth either
  setting the var in `_test-file` or failing loudly on its absence.
- **A standing guard is missing.** Asserting that every stdlib module loads cleanly through
  `load_module` — exactly the probe in Appendix A — would have caught all eleven modules the day
  they broke, and would catch §11's four today. Not added here because four modules are currently
  red, so the guard would land failing; it should land with §11's fix.

## 10. Spec/docs status

`docs/spec-v0.md` is **unaffected** — no normative rule changes. This document is a supporting
design doc; it does not override the spec. On landing, update `BACKLOG.md` §Native REPL &
Analysis Service to mark the item done, and add a line to `docs/compiler-internals.md` recording
the invariant, which is the durable lesson:

> Any pass that derives a fact by scanning `decls` must also read that fact from `env`, because the
> REPL/LSP/analysis-service path supplies imported modules as schemes and markers rather than as
> declarations. `class_names_from_env` is the reference implementation.

Also update §7's item 3 when Fix B lands; it describes behaviour this change does not yet deliver.

## 11. What this change exposed (filed, not fixed)

### 11.1 An imported type has two identities — `T` vs `alias.T` — **FIXED**

The four modules still failing after the vocabulary change all failed the same way:

```
repl:            Type mismatch: StatefulSession vs compiler.StatefulSession
http_middleware: Type mismatch: Logger vs log.Logger
scram:           Type mismatch: CryptoError vs crypto.CryptoError
http_server:     Type mismatch: Utf8Error vs bytes.Utf8Error
```

**Root cause.** `prefix_pairs` qualifies an aliased import's binding KEYS but leaves the type
constructors inside those schemes untouched. Measured directly:

```
import stdlib.bytes as bytes
  bytes.to_string :: Bytes -> Result Utf8Error String
  ^^^^^^ key qualified                ^^^^^^^^^ type still short
```

while `lookup_type_var` keeps an annotation `Result bytes.Utf8Error String` verbatim, per T7. The
two spellings can never unify. The bundling path never sees it because inlining gives every type
exactly one name.

**Resolution: drop a known alias prefix.** `import M as a` records `@qualalias:a` in the env;
`lookup_type_var` resolves `a.T` to the short `T`, meeting the scheme on the name the env path
already uses. A prefix that is *not* an import alias (`main.Foo`) is still returned verbatim, so
T7's distinction survives. Implementation: `module_loader.apply_import_spec` emits the marker,
`infer.import_aliases_from_env` lifts it into the `alias_env` — which is the base of every
`local_vars` dict (`build_type_var_dict`), so the sentinel reaches every annotation position
without threading a new parameter through inference.

Result: **26 of 27** top-level stdlib modules load. The last one, `stdlib.repl`, fails inside the
unswept `stdlib/compiler/` subtree (`parser.submission_starts_decl` *is* exported, so it is a
separate defect). `import http_server` works in the REPL — the original bug report.

### 11.1a Why NOT canonical `<module>.<Type>` names — measured

The principled alternative is one canonical name per type, matching `bundler.qualified_name`. It
was implemented and **rejected on evidence**, which is worth recording because it looks obviously
correct on paper:

Every marker family in the env path is keyed by **short type name**, so qualifying types anywhere
silently breaks the lookups:

- `@linear:<TypeName>` — read via `head_type_name(t)` (`linear_check.sprout:112`). Canonicalizing
  made an imported linear type read as `stdlib.net.TcpConnection`; the lookup missed and
  `http_server` failed with ``  `borrowing` is only allowed on a parameter of a linear type ``.
- `@inst:<Class>:<head>` — typeclass dispatch. Keys are built bare
  (`type_from_ast(head_te, dict_empty())`) but looked up from resolved types, so canonicalization
  breaks instance dispatch for imported types — a failure a module-load probe does *not* surface.

Measured side by side: canonical reached 22/27 modules with new interaction classes still
appearing each iteration; alias-stripping reached 26/27 and disturbs no marker family. Making
canonical correct means canonicalizing (or short-name-stripping) **every** marker family — a
change to typeclass dispatch and linearity, not a bug fix. Filed separately; see `BACKLOG.md`.

**Known limitation inherited, not introduced.** Under alias-stripping, two modules' same-named
types collapse to one identity inside an importer. That is already true today for selectively
imported types, which arrive bare; this extends it to alias-qualified spellings. Canonical naming
is what closes it.

### 11.2 Should `import M (T)` bring `T`'s constructors?

Raised by §4.4. `select_named_pairs` matches names exactly, so a selective import of a type does
not import its constructors; the bundler, by inlining, behaves as if it does. Two stdlib modules
were relying on the permissive behaviour. Prior art is clear that this is a real design axis
(Haskell spells the permissive form `T(..)` explicitly, precisely because `T` alone does not imply
it), so the options are to require explicit constructor listing, to make `T` imply `T`'s
constructors, or to add a `T(..)` form. A ruling belongs in `docs/spec-v0.md` §visibility/exports.
Until then the bundler and the env path disagree, and that disagreement is exactly the class of
silent divergence this document is about.

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
