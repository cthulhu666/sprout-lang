# Sprout Tree-sitter Support Brief

Implementation brief for making `codebase-memory-mcp` (CBM) index Sprout source.
Not a normative language spec.

> **Status (2026-08-29): stalled mid-flight, both halves partly built.**
>
> - **Sprout side.** `tree-sitter-sprout/` has `grammar.js`, the layout external scanner
>   (`src/scanner.c`), generated `src/parser.c` / `src/grammar.json` / `src/node-types.json`,
>   `queries/highlights.scm`, `queries/tags.scm`, and a 145-line corpus. It took approach **1**
>   below (external scanner). It is a self-declared conservative scaffold and is **materially out
>   of sync with the language** — see "Verified grammar gaps".
> - **CBM side.** Contrary to this document's previous status note, the extension entry **does
>   exist** — on two unmerged branches of the fork `cthulhu666/codebase-memory-mcp`:
>   `codex/sprout-support` (`434926e`, 2026-04-21) and `codex/sprout-index-persistence-fix`
>   (`70f55b0`, 2026-04-22). They carry the vendored grammar, the `grammar_sprout.c` shim,
>   `CBM_LANG_SPROUT`, a full `lang_specs.c` row, `.sprout` in `EXT_TABLE`, Sprout cases in
>   `extract_defs.c` / `extract_calls.c` / `extract_imports.c` / `helpers.c`, `THIRD_PARTY.md`
>   attribution, and tests.
> - **Why it stopped.** Not Sprout. A CBM *core* defect: commit `94d30b0` replaces the direct
>   B-tree page writer with the store-backed path, noting *"The direct page writer currently
>   produces inconsistent on-disk graphs for mixed real-world projects, including Sprout
>   indexing."* `cbm_write_db` is still the live path on CBM `main`.

## Why This Is Needed

CBM supports a fixed set of vendored tree-sitter grammars. Sprout is not one of them, so
`.sprout` and `.spr` files are skipped entirely. The CBM project registered for this repo holds
**1 node, 0 edges** — the bare `Project` node.

CBM needs a real parser, not an extension mapping. `cbm_extract_file()` hard-fails with
`"no tree-sitter grammar"` when a language's `ts_factory` is `NULL`, and there is no `dlopen`,
LSP-client, ctags, or subprocess path for code extraction anywhere in the codebase. Tree-sitter
is the only way in.

## Scope Decision (2026-08-29)

**Full coverage in one pass** — declarations *and* expression bodies, so `CALLS` edges land and
`trace_call_path` works. The earlier "declarations first, expressions later" staging in this
document is superseded: without call edges CBM offers little over grep, and Sprout's two real
consumers (this repo and `uncharted-suns`) are both call-graph shaped.

## Verified Grammar Gaps

Measured against the 801-file corpus (147 `.sprout` + 654 `.spr`). Reference for syntax and
precedence is `stdlib/compiler/parser.sprout`; the operator/token list is
`stdlib/compiler/lexer.sprout:343`.

### Declarations

| Gap | Reality | Corpus evidence |
|---|---|---|
| `extern fn name(...) -> T`, no body | keyword absent from grammar | 141 uses; `stdlib/bits.sprout:46` |
| `deriving (C, ...)` trailing clause | absent | 206 uses; `examples/records_demo.sprout:12` |
| `wrap Name = TypeExpr` | absent — it is the 7th declaration keyword (`parser.sprout:2068`) | `stdlib/linalg.sprout:12` |
| `(..)` constructor-visibility marker | absent (`parser.sprout:2307`) | `stdlib/json.sprout:8` |
| `type linear Name` | contextual marker, absent | `stdlib/net.sprout:100` |
| **Records** | grammar says `{ }` (`grammar.js:142`); real syntax is `( field: T, ... )` | `examples/records_demo.sprout:14` |
| Effect rows `!{IO}` | absent | `stdlib/fs.sprout:31` |
| `where` block after a function body | absent | `stdlib/bytes.sprout:140` |

### Expressions (required for `CALLS`)

| Gap | Reality | Corpus evidence |
|---|---|---|
| `\|>` pipe | absent from `binary_expression` (`grammar.js:355`) | `parser.sprout:960` |
| Record literals | grammar says `Ident { }` (`grammar.js:380`); real syntax is `Ident(f = v, ...)` | `examples/records_demo.sprout:17` |
| Field access | grammar says `get e f` (`grammar.js:393`); real syntax is postfix `e.f` | `examples/records_demo.sprout:26` |
| `with (...)` record update | absent | `examples/records_demo.sprout:32` |
| `let … in` / `let … else` | absent; the current `let_declaration` rule misparses the expression form | `tests/stdlib/test_let_else.spr:9` |
| `_` placeholder partial application | absent (`parser.sprout:1170`) | — |
| Operators `++ >> << .. %`, string templates, `exists`/GADT forms | absent | `lexer.sprout:343` |

## Sprout-Specific Parser Constraints

Sprout is not brace-delimited. The hosted parser is layout-sensitive for `do` blocks, `match`
branches, and local `where` bindings. Three possible approaches were considered:

1. an external scanner tracking indentation and layout tokens — **chosen**,
2. a weaker grammar parsing declaration headers and treating bodies conservatively,
3. a two-stage indexer separating structural discovery from expression parsing.

The scanner must stay **C**, not C++: CBM's `scripts/vendor-grammar.sh` warns about but cannot
handle `scanner.cc`.

Two facts that shape any Sprout indexer:

- **`module` and `import` never reach the AST.** `source.sprout:252 strip_headers` removes header
  lines byte-wise before lexing, replacing them with blanks to preserve line numbers. In the
  compiler, import extraction is a line scan (`module_loader.collect_imports`). Tree-sitter
  parses raw text, so it *does* see `import_declaration` nodes — but the two authorities differ,
  and only the compiler's is normative.
- **`|>` desugars at parse time by appending the LHS as the last argument**
  (`parser.sprout:967 pipe_into_call`): `x |> f(a)` becomes `CallExpr f [a, x]`. Tree-sitter does
  not desugar, so any consumer must replicate this or lose most call edges.

## Where Each Half Lives

| Concern | Home | Why |
|---|---|---|
| `grammar.js`, `scanner.c`, corpus tests | `tree-sitter-sprout/` (this repo) | The corpus and the reference parser are here. |
| Generated `parser.c` / `scanner.c` | `internal/cbm/vendored/grammars/sprout/` | How CBM carries all 153 of its grammars. `tools/tree-sitter-form` and `tools/tree-sitter-magma` live inside CBM only because those languages have no repo of their own. |

## Plan of Record

### 0. Sync the CBM fork

`upstream/main` has never been fetched (no remote-tracking ref; `.git/FETCH_HEAD` dated
2026-04-22). Local `main` is at `53f8f46` (2026-04-19), roughly four months behind DeusData.
Fetch, rebase, push — then check whether the B-tree writer defect was fixed upstream, since that
decides step 5.

### 1. Resurrect the branch

Cherry-pick the clean feature commit `434926e` onto the synced `main`; leave the debug
checkpoints (`94d30b0`, `70f55b0`) for now. Resolve drift across `cbm.h`, `lang_specs.c`,
`language.c`, `extract_{defs,calls,imports}.c`, `helpers.c`. Reach a green
`make -f Makefile.cbm test` before changing anything.

> `lang_specs.c`'s `_Static_assert` only catches a *shrunk* array. A missing designated
> initializer silently yields an all-`NULL` spec — verify the row by hand.

### 2. Build the measuring instrument first

The grammar is currently **not gated at all** — no `justfile` recipe runs `tree-sitter`, which is
why it drifted. Before editing `grammar.js`:

- `scripts/ts_parse_coverage.sh` — `tree-sitter parse` over all 801 files, reporting per-file
  `ERROR`/`MISSING` counts and a corpus-wide rate.
- `just tree-sitter-test` — `tree-sitter test` plus the coverage script, wired into CI.

**Differential oracle.** `sproutd --analysis-service <stdlib_root>` speaks JSON-lines and answers
`symbol_locations_in_source` (`{categories, names, lines, columns}`) and
`symbol_inventory_in_source` (`{declared, imported, exported}`). Diff those against the
`queries/tags.scm` captures per file; every disagreement is a grammar bug with a location. The
service skips `ExternFnDecl` and `InstanceDecl` (`analysis_service_driver.sprout:187-188,199-200`),
so those two need direct corpus assertions instead.

### 3. Repair the grammar

Close every row in "Verified grammar gaps". Regenerate and commit `src/parser.c`,
`src/grammar.json`, `src/node-types.json`. Extend `test/corpus/` past the single `basic.txt` —
one file per declaration form, following the `tools/tree-sitter-form/test/corpus/` layout.

**Exit criterion: 0 `ERROR` nodes across all 801 files**, per the step-2 harness.

### 4. Re-vendor and finish the CBM wiring

1. Copy regenerated artifacts into `internal/cbm/vendored/grammars/sprout/`. The branch's copy
   predates both `e90b31d8 "tree-sitter: fix sprout binding and layout regressions"` and all of
   step 3.
2. **Register `.spr`.** The branch registered only `.sprout`, missing **654 of 801** files. No
   collision: no `.spr*` entry exists in CBM's `EXT_TABLE`.
3. `helpers.c` — `cbm_is_test_file()` for `tests/**/*.spr`; `func_kinds_sprout[]`; a Sprout
   keyword list for `cbm_is_keyword()` (currently falls back to `generic_keywords`).
4. **Pipe call edges.** Add `binary_expression` to `sprout_call_types` and extend the Sprout
   branch of `extract_callee_lang_specific()` (`extract_calls.c:324`) to accept it only when the
   operator is `|>`, taking the callee from the RHS.
5. **Verify import aliases survive.** Sprout has three spellings — `import M as alias`, bare
   `import M` (implicitly aliased to the last segment, *not* a wildcard), and `import M (a, b)`.
   `pass_calls.c:94 build_import_map()` needs the alias to resolve `string.trim` →
   `stdlib.string`. Check what `parse_generic_imports` actually records.
6. **Module path resolution.** Sprout has no package manifest, so CBM's `pass_pkgmap.c`
   contributes nothing. `module_name_to_path` (`module_loader.sprout:179`) is three rules: strip a
   leading `stdlib.`, dots → slashes, append `.sprout`; a dotted non-stdlib name falls back to the
   package root. Confirm CBM's path-derived QN scheme (`<project>.<path_parts>.<name>`) lines up
   before adding a resolver hook.

### 5. The persistence defect

- **Fixed upstream** → drop the workaround, keep `cbm_write_db`.
- **Still present** → root-cause it in `internal/cbm/sqlite_writer.c`. It is a core defect
  affecting "mixed real-world projects", not a Sprout one, and `94d30b0`'s store-backed detour
  trades the fast page writer for correctness across *all* languages. Either way keep that
  checkpoint's reopen-and-count assertion as a regression test — it is what detects the failure.

### 6. Tests and docs

- `tests/test_language.c` — `TEST(lang_ext_sprout)`, `TEST(lang_ext_spr)`.
- `tests/test_extraction.c` — the branch's `TEST(sprout_basics)` **encodes the record bug**
  (`type Person = { name: String }`, which Sprout does not have). Rewrite to real syntax and add
  `sprout_extern_fn`, `sprout_deriving`, `sprout_wrap`, `sprout_record`, `sprout_typeclass`,
  `sprout_instance_method`, `sprout_pipe_call`, `sprout_qualified_call`, and `sprout_imports`
  (all three spellings). Sources are inline C string literals — CBM has no fixtures directory.
- CBM `README.md` / `CONTRIBUTING.md` language counts are already stale on `main` (claim 66 and
  64; actual 155). Correct to the real number rather than incrementing a wrong one.
- `src/discover/userconfig.c:63` — add `"sprout"` to the name→enum table.

## Verification

1. `just tree-sitter-test` — corpus green, 0 `ERROR` nodes across 801 files.
2. Differential oracle — zero disagreements between `tags.scm` and
   `symbol_locations_in_source`.
3. `make -f Makefile.cbm test` and `scripts/lint.sh` green.
4. Rebuild and install CBM (the binary at `~/.local/bin/codebase-memory-mcp` is dated
   2026-04-06 and contains no `sprout` symbol), re-index this repo, and check the label
   breakdown via `get_graph_schema`: `Function`, `Module`, `Class`, `Type` non-zero, and
   `DEFINES`, `IMPORTS`, `CALLS` non-zero.
5. `trace_call_path` across a known chain — e.g. `compile_driver` into `parser.parse_program` —
   plus a piped call, to prove the `|>` handling.
6. Index `uncharted-suns`. It resolves modules through `--package-root`, exercising the
   non-stdlib import path this repo alone does not.
7. Re-open the dumped DB and assert node/edge counts match what the pipeline reported.

## Compatibility Notes

Sprout's self-hosted parser (`stdlib/compiler/parser.sprout`, `stdlib/compiler/ast.sprout`) stays
the reference implementation and the validation oracle. CBM cannot consume it as an extractor —
there is no non-tree-sitter code path — but the analysis service makes it usable as a check on
the grammar's output.
