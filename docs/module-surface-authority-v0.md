# One authority for module surface — v0

Status: **Parts A and B implemented** (§5, §6), each gated by a conformance test (§8). The env-path
retirement (§7) is designed here and deferred. The normative spec is **unaffected and was never
wrong** — `docs/spec-v0.md` §"Externs are outside the module system" already states the rule this
change makes the analysis path obey.

## 1. Problem statement

`import stdlib.bits` followed by `bit_or(3, 5)` fails in the REPL with `Unknown variable: bit_or`,
and so does `bits.bit_or(3, 5)`. Neither spelling works, while `:type bits.bit_or` cheerfully
answers `Int -> Int -> Int`. `stdlib.bits` is unusable from the REPL and offers no completions.

The module is not the cause; it is the first module to consist *entirely* of `extern fn`
declarations, so it is the first whose REPL surface is empty rather than merely incomplete. The
same defect affects every extern in every non-prelude module — `bytes.bytes_length`,
`math.double_to_bits`, `fs.read_file`.

### 1.1 The question that is answered five times

"What names does a module publish, and under what spelling does an importer see them?"

| # | Site | Reads | Verdict for an extern | Matches spec §Externs? |
|---|---|---|---|---|
| 1 | `bundler.scan_source_info` | **raw text** | — (recovers the `export` set) | feeds #2 |
| 2 | `bundler.add_decl_to_symbols` | AST | excluded ⇒ stays bare | yes |
| 3 | `module_loader.decl_value_names` → `prefix_pairs` | AST | included ⇒ **prefixed with the alias** | **no** |
| 4 | `analysis_service_driver.collect_decl_names` | AST | excluded | yes, for its purpose |
| 5 | `repl.gather_exported_names` | **raw text**, greps `export ` | invisible | **no** — the completion gap |
| — | `infer.pre_scan_fn_decls` | AST | bound bare | yes |

Sites 2 and 3 had literally opposite arms for the same constructor. Two of the five read source
text rather than the AST.

### 1.2 Why no spelling works

Sprout typechecks through two front ends:

- **file/bundler path** — `bundler.bundle_file` inlines every module into one program and
  canonicalises names. Used by `just run`, `--emit-ir`, `just test`, and by the REPL's *own eval
  step* (`analysis_service_driver.compile_and_run_eval` → `compile_full_ir_with_cache`).
- **REPL/env path** — `module_loader.build_import_pairs` re-checks each module and extracts
  `(name, Scheme)` pairs. Used by the REPL's check, `:type`, the LSP, and every analysis-service
  diagnostic.

A REPL eval crosses both. Measured before the fix:

| input | verdict | rejected by |
|---|---|---|
| `import stdlib.bits` → `bit_or(3, 5)` | `Unknown variable: bit_or` | env check (wanted qualified) |
| `import stdlib.bits` → `bits.bit_or(3, 5)` | `Unknown variable: bits.bit_or` | bundler (wants bare) |
| `import stdlib.bits` → `:type bits.bit_or` | `Int -> Int -> Int` | — env only, so it passes |
| `import stdlib.bits (bit_or)` → `bit_or(3, 5)` | `7` | — worked, for the wrong reason |
| file: `bit_or(3, 5)` | compiles | — |
| file: `bits.bit_or(3, 5)` | `check: Unknown variable: bits.bit_or` | bundler |

The fourth row is itself a spec violation being leaned on: §Externs says a selective list has no
bearing on an extern, so naming one should be inert — instead it was the only thing that worked.

### 1.3 Third instance of one bug class

`docs/repl-env-type-vocabulary-v0.md` §1.3 records the first two, and names the mechanism: *any
pass that answers a question by walking `decls` will disagree between the two paths*, because the
env path supplies imported modules as schemes and markers. Its §10 turned that into an invariant in
`docs/compiler-internals.md`.

This instance is a variant worth distinguishing, because the invariant as written does not catch it:
`module_loader` was not missing information. It pattern-matched `ExternFnDecl` and decided
differently. Reading a fact from `env` as well as `decls` does not help when the two sites disagree
about what the fact *means*.

## 2. Goals and non-goals

**Goals**

- G1. The two front ends accept exactly the same set of spellings for an imported name.
- G2. The rule has one definition; every other site consumes it.
- G3. A future divergence fails a test rather than reaching a user (§8).
- G4. No change to the file-compilation path's behaviour — it is the one that was already correct.

**Non-goals**

- N1. Changing the language. The spec already says what should happen; nothing normative moves.
- N2. Unifying the *export* axis. That is Part A (§6) and is a separate cause with a separate fix.
- N3. Retiring the env path in favour of the bundler. Scoped as §7, after the authority exists.
- N4. The `import M (T)` / constructor question left open by `repl-env-type-vocabulary-v0.md` §11.2.
  It is the same bug class on the type axis and still needs a ruling; it is not resolved here.

## 3. Prior art

Not a semantics choice — the spec has already ruled, and this is a conformance fix — so no survey of
alternatives is owed. What *is* worth recording is the structural precedent for the mechanism used:
GHC, OCaml and Rust all reify a module's public surface into a single artifact (`.hi`, `.cmi`, crate
metadata) that every consumer reads, rather than letting each pass re-derive it from source. That
survey is already written up in `docs/repl-env-type-vocabulary-v0.md` §3, and its §4.3 records
converging `module_loader` with `iface_codec` as the end state. This change is a step toward that,
not away from it: it makes the surface a *derived* fact with one definition.

## 4. What "global" means, and where it bites

Per spec §"Externs are outside the module system", an extern is never qualified, never renamed,
never in a module's exported set, is reachable by **bare** name wherever its declaring module is
part of the build, and is unaffected by selective import lists.

"Part of the build" includes **transitively**, which is easy to miss and is verified: `stdlib.http`
imports `stdlib.bytes`, so

```sprout
import stdlib.http
fn f() -> Int = bytes_length(bytes_from_utf8("hi"))
```

compiles. A fix that stopped an extern at its direct importer would satisfy every other case and
still diverge, so §8 pins this case explicitly.

## 5. Part B — one authority for name scope (implemented)

### 5.1 The authority

`ast.sprout` gains the classification, beside the existing `decl_pos`:

```sprout
export type NameScope (..) =
  | ScopeModule   # qualified canonically; visibility follows the import form
  | ScopeGlobal   # bare everywhere the declaring module is in the build

export fn decl_value_scopes(d: Decl) -> List (String, NameScope)
export fn scope_names_all(scoped: List (String, NameScope)) -> List String
export fn scope_names_module(scoped: List (String, NameScope)) -> List String
export fn scope_names_global(scoped: List (String, NameScope)) -> List String
```

`ast` is the right home: it is a pure data module already imported by all four consumers, so no new
import edge and no new module outside `compile_driver`'s seed closure. The function covers the
*value* axis only — types and classes are a separate axis, and a `ClassDecl` contributes its method
names because those are values.

### 5.2 Provenance: the `@extern:` marker

`prefix_pairs` operates on `(String, Scheme)` pairs, which carry no record of which declaration
produced them — there was nothing to exempt even had it wanted to. Rather than thread a new
structure through `load_module`'s twelve call sites (the plumbing cost measured in
`repl-env-type-vocabulary-v0.md` §4.2), scope rides the existing marker mechanism:

`infer.pre_scan_extern` records `@extern:<name>` where an extern's signature already enters the env.
This is the natural home — the same function sets `@arity:` — and it inherits exactly the
propagation a global name needs, because `module_loader` already treats every `@`-prefixed key as
unprefixable (`prefix_pairs`) and unfilterable (`select_pairs`). A global name differs from a marker
only in that its key is callable.

The marker is emitted even when the name is already bound, so a module re-declaring an extern
already in scope keeps the existing scheme *and* still registers as global.

### 5.3 Consumers

| site | before | after |
|---|---|---|
| `module_loader.program_module_value_names` | `decl_value_names`, externs included | `ast.scope_names_module ∘ ast.decl_value_scopes` |
| `module_loader.load_module` own_pairs | declared names + markers | module-scoped names + **globals named by markers** + markers |
| `module_loader.prefix_pairs` | exempts markers | exempts markers **and globals** |
| `module_loader.select_pairs` | named + markers | named + markers + **globals unconditionally** |
| `bundler.add_decl_to_symbols` | `ExternFnDecl -> acc`, a hardcoded skip | delegates to `add_scoped_values`, so the skip is **derived** |

Selecting globals *by marker* rather than by declaration is what makes transitive visibility work:
the markers reaching a module cover both its own externs and every extern arriving through an
import, so one code path serves both and §4's transitive case needs no special handling.

The bundler's per-constructor `match` is retained — it is a routing table across three symbol
tables, not a visibility decision — but `FnDecl`, `LetDecl` and `ExternFnDecl` now all delegate to
one helper that asks the authority which names are module-scoped. Exhaustiveness is preserved (no
`_` arm), so a new declaration kind still has to be handled explicitly.

### 5.4 Behaviour change, stated plainly

`bits.bit_or` **stops** typechecking in the REPL and in `:type`. That spelling never compiled; the
env path accepting it is what made the failure so confusing. Bare `bit_or` now works, which is what
the spec says and what the file compiler has always done.

`analysis_service_driver.collect_decl_names` (site #4) is left alone here. It is correct today, it
does not participate in name resolution, and converting it touches REPL redefinition semantics; it
moves to the authority in Part A's change, which already edits that surface.

## 6. Part A — one token-derived export scan (implemented)

The second root cause: `parse_decl` calls `skip_export`, and `parse_type_decl` calls
`skip_visibility`, so both the `export` keyword and the `(..)` constructor-export marker are
**consumed and discarded, and neither reaches the AST**. Every consumer needing the publish-set had
to re-derive it from source text, which is why sites #1 and #5 were line scanners. Nothing noticed
because `formatter.format_source` is line-based too, so `export` survives formatting textually and
never round-trips through an AST.

Consequence, and the second half of the reported symptom: `repl.gather_exported_names` accepted only
lines starting with `export `. An `extern fn` line never does — and per spec, `export` on an extern
is parsed and discarded, so no author writes it. So **no extern was ever a completion candidate,
from any module, including the prelude**: `print`, `panic` and `int_to_string` were missing too.

### 6.1 `parser.scan_module_surface`

```sprout
export type ModuleSurface (..) =
  | ModuleSurface (List String) (List String) (List String)
    # exported names; types whose constructors are exported; global (extern) names
```

A **token** scan, not a parse — callers want a module's surface far more often than its AST — but
exact, because it decides by *calling* the parser's own predicates (`is_alias_type_decl`,
`skip_linear_marker`, `skip_visibility`) instead of matching text. That is what removes the hazard
the text scanner documented in its own comments: it had to test the prefix `export type linear `
*before* `export type `, or the generic branch read the contextual marker word `linear` as the
type's name, exporting a phantom type called `linear`.

Chosen over a real `export` field on `ast.Decl`: the field is the better long-term model but costs a
244-site constructor sweep including `tests/` and `iface_codec` in both directions, and buys no
property the token scan does not. It does not block adding the field later.

### 6.2 Consumers

| site | before | after |
|---|---|---|
| `bundler.scan_source_info` | line scan for module name + exports + `(..)` | delegates; only the module name is still read from text (headers are stripped before tokenizing, so `module x.y` never reaches the token stream) |
| `bundler.process_wi_finalize` | text scan, then a **separate** tokenize for the parse | one tokenize serving both — lexing twice is how the surface could differ from the AST |
| `repl.gather_exported_names` | `export `-prefix line scan | **deleted** |
| `repl` completion | line scan | parses: export set from `scan_module_surface`, names per declaration from `ast.decl_value_scopes` |
| `analysis_service_driver.collect_decl_names` | per-constructor, externs excluded by hand | value names from the authority; the extern exclusion is derived |

Dead after the swap and removed: `read_ident_at`, `read_ident_chars`, `is_ident_char`,
`has_ctor_export`, `skip_type_params` in `bundler`; `ctor_names`, `method_sig_names` in
`analysis_service_driver`.

### 6.3 Completion now parses, and that was measured

Tokenize+parse of the prelude (the largest module) is ~81ms against ~38ms to tokenize alone.
Completion runs on TAB only, so exactness was worth the difference. Externs are offered **bare** and
never `alias.`-qualified, matching §5.4 — which is why they appear in the unqualified candidate set
and are deliberately absent from the dotted one.

**Constructor names are gated on the export set, not on `(..)`,** and that is deliberate. The
looser-looking choice is the accurate one: the prelude declares `Maybe`, `List` and `Result` with no
`(..)` at all, yet `Just` and `Ok` are in scope everywhere because the prelude is inlined rather
than imported. A first attempt gated on `(..)` and silently dropped all four from completion.
Whether an *aliased* import should offer `ns.Ctor` for a type without `(..)` is genuinely open
(`repl-env-type-vocabulary-v0.md` §11.2 — the bundler behaves as if it does, the env path does not);
preserving the previous completion behaviour keeps this change from ruling on it by accident.

### 6.4 The module-name list stays a literal, checked by a test

`repl.stdlib_module_completion_names` was a hardcoded string missing **15 of 30** modules —
`json`, `time`, `fs`, `rng`, `regex`, `task`, `chan` and everything added since it was written,
`bits` included. It remains a literal: there is no directory-listing primitive in the language, so
enumerating at runtime means `sh -c ls` per keypress, which costs a subprocess and would silently
offer nothing on the Windows port. The staleness is caught in
`tests/stdlib/compiler/test_repl_module_list.spr` instead, where a subprocess is free — verified to
fail, naming the missing module, by removing `bits` from the list.

## 7. Retiring the env path (scoped, not designed here)

The maximal reading of G1 is to have one front end. Measured cost: a cold bundler check is 1.1s on
`infer.sprout` (the largest module) and 0.41s on `http_server.sprout`, against tens of milliseconds
for a warm env check — a real LSP regression without a parsed-module cache. It also needs a
bundle-from-source entry point and touches ~6 analysis ops, and it would not remove the text
scanners, so it does not subsume Part A. Sequenced after Parts A and B.

## 8. Tests

`tests/stdlib/compiler/test_module_surface_agreement.spr`, written first and confirmed RED: 9
passed / 5 failed, with **every** failure on the env path and both controls green — which is what
made the failures diagnostic rather than a broken harness. 14/14 after Part B.

Each case asserts *both* front ends reach the *same* verdict, and the expected verdict is the spec's.
Two assertions per case rather than one equality, so a dissenting path is named:

1. plain import, bare extern → both accept
2. plain import, alias-qualified extern → both reject (§5.4)
3. selective import naming an unrelated name, bare extern → both accept (spec bullet 3)
4. aliased import, bare extern → both accept
5. transitive import, bare extern from an indirect module → both accept (§4)
6. control: an ordinary exported fn **is** alias-qualified → both accept
7. control: an undeclared name → both reject

Cases 6 and 7 are the ones that stop the fix from being "disable qualification" or "accept
everything". The file defaults `SPROUT_STDLIB_ROOT` to the relative `stdlib` rather than no-opping
when it is unset, avoiding the silent `0 passed / SUITE PASSED` trap recorded in
`repl-env-type-vocabulary-v0.md` §9.

Part A adds three more:

`tests/stdlib/compiler/test_module_surface_scan.spr` (21 assertions) — every declaration form that
can carry `export`, the `(..)` marker after both a plain and a `linear` type, `export extern` being
inert, and **equivalence with the text scanner being replaced** on the same source. That last group
is what makes the swap provable rather than hopeful; the pre-existing
`tests/stdlib/compiler/test_bundler.spr` (17 assertions, untouched) passes against the new
implementation and covers module-name extraction.

`tests/stdlib/compiler/test_repl_completion_surface.spr` (14) — `print`, `panic`, `int_to_string`
and `bit_or` are offered; an extern is *not* offered as a qualifiable name; `stdlib.bits` has zero
qualifiable names because it is all externs; and the four prelude constructors are pinned, which is
what caught the `(..)`-gating mistake in §6.3.

`tests/stdlib/compiler/test_repl_module_list.spr` (4) — enumerates `stdlib/` and fails per missing
module. Verified to fail by deleting `bits` from the list. It also asserts the enumeration found
something, because a vacuous pass is the failure mode this file exists to prevent.

Still owed, and now unblocked by that document's §11.1 fix: a standing guard that every top-level
`stdlib/` module loads cleanly through `load_module`. Its §9 asks for exactly this and could not
land while four modules were red.

## 9. Compatibility, seed, migration

No user-source migration; no `stdlib/` source changes. `stdlib/compiler/ast.sprout`,
`infer.sprout`, `module_loader.sprout` and `bundler.sprout` change, so a full `just refresh-seed` is
required (DoD #9) — not the `seed-fp-ack` bypass. Golden IR for compiled programs is not expected to
move: no lowering or codegen path is touched, and the file path's accepted-name set is unchanged.

`iface_codec` is unaffected. `NameScope` is a compiler-internal type and is not serialised.
