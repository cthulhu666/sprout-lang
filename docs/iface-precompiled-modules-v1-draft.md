# Precompiled module interfaces (.iface) + bitcode (.bc) — v1 draft

**Status:** experimental design draft, not normative.
**Author:** in-flight on `perf/observability-round-1`.
**Origin:** `SPROUT_TIME_PHASES` instrumentation revealed that for tests
importing `stdlib.compiler.compiler`, bundle (~21s), check (~8s), and codegen
(~24s) all do work proportional to the *full transitive import graph* on every
single-file compile. The only way to reduce this is to compile each module
once and reuse the artifact downstream.

## Problem

`compile_driver --emit-ir <file>` re-parses, re-typechecks, and re-emits IR for
every transitively-imported module on every invocation. For a heavy test
(`tests/stdlib/compiler/test_compile_full_ir_lines.spr`):

```
bundle=21s  prelude=1.3s  check=8.6s  lower=1s  codegen=24s  total=58s
[cg] sigs+ctx=22ms decls_header=1028ms emit_all_fns=23s assemble=59ms
```

This makes `just test` ~67s wall on a fast machine, and ~9 min on CI
(`Verify bootstrap seed is a fixed point` step). It also makes any future
multi-file Sprout app build slow in proportion to its import graph size.

## Goals

1. Compile each stdlib (and stdlib.compiler) module exactly once to a pair
   `(<module>.iface, <module>.bc)` per source-content hash.
2. On user-file compile, skip parse/check/codegen for imported modules whose
   `.iface` (parsed AST + schemes) and `.bc` (LLVM IR) artifacts are available.
3. Wall-clock target: heavy tests drop from ~58s → ~5–10s per file.
4. Generic — same mechanism benefits any future Sprout app's build pipeline.

## Non-goals (v1)

- Incremental recompilation of *user* code based on hash diffs. (v2.)
- Daemon-mode compilation server. (v3.)
- Cross-machine artifact distribution (separate concern; CI cache covers it).
- Replacing `bootstrap/compile_driver.ll` IR seed. The seed is the bootstrap
  trust anchor and stays committed; `.iface`/`.bc` are derived artifacts.

## Design positioning

Although the *motivation* above is build speed, `.iface` should be built as a
**typed-AST canonical artifact** — Sprout's equivalent of Scala 3's TASTy and Idris
2's TTC — **not** a perf-only metadata layer or a bitcode-only shortcut. Encode what
downstream tooling (LSP, doc-gen, future incremental builds) will need to read, not
merely what the bundler needs to skip. Prior art surveyed: GHC `.hi`, OCaml `.cmi`,
Rust `rmeta`, Scala 3 TASTy, Swift `.swiftmodule`, Idris 2 TTC — and the C/C++
textual-`#include` model as the anti-pattern (re-parse per translation unit) this
avoids.

## Versioning and freshness policy

Three rules, each learned from another language's post-mortem:

1. **Version int, fail loud on mismatch — never collapse to `Nothing`.** `.iface`
   carries a format-version integer; a mismatch must produce an unambiguous, located
   error, not a generic parse failure that reads as "no iface, recompile." (Scala
   3.3.2 TASTy-version and Idris 2 #2033 both bled time to silent/ambiguous version
   handling.) *Current gap:* `decode_iface_file` returns `Maybe IfaceFile`, so a
   version mismatch collapses into `Nothing` — fix to a typed error before relying on
   `.iface` for correctness.
2. **Content hash, not mtime.** Freshness (including transitive freshness across the
   import graph) is keyed on a hash of source content, not file modification time
   (Rust's `svh`, GHC recompilation avoidance). mtime is unreliable under checkout,
   caching, and generated files.
3. **Forward-compat round-trip tests from day one.** Ship a golden `.iface` fixture
   and a decode round-trip test per version bump, in CI, from the first version — so
   an accidental format break is caught by a test, not by a corrupted build.

## Format — text-based `.iface`

Decision (confirmed): text format, parsed by Sprout itself. Rationale:
- Self-describing, debuggable (`grep`-able).
- No new builtin needed (existing string + parse primitives suffice).
- Aligned with the committed `bootstrap/compile_driver.ll` precedent.
- Trade-off: larger files (~3–5× binary). Acceptable; iface is `build/`-local.

### Sketch

```
iface-version: 1
module: stdlib.prelude
source-path: stdlib/prelude.sprout
source-hash: sha256:<...>
emit-time: 2026-06-09T...
compiler-fingerprint: <bootstrap-seed-hash>

# Constructor table (one per line)
ctor Just/Maybe 1 a
ctor Nothing/Maybe 0
ctor Cons/List 2 a (List a)
...

# Scheme table (qualified name → scheme)
scheme stdlib.prelude.list_length :: forall a. (List a) -> Int
scheme stdlib.prelude.vec_singleton :: forall a. a -> Vec a
...

# Instance table
instance Eq Int = stdlib.prelude.int_eq_inst
instance Show Int = stdlib.prelude.int_show_inst
...

# Class table (with super-classes)
class Ord a => Eq a { eq :: a -> a -> Bool ; neq :: a -> a -> Bool }
...

# Parsed AST hash (used to detect "iface matches source")
ast-hash: <hash of post-parse AST>
```

The `ast-hash` is computed from the typed-AST shape after parse but before
lower, so the iface format is stable across lowering-strategy changes.

### What gets serialized

For each module:
1. **Schemes** for every exported declaration. Reuses `types.Scheme` ADT
   serialization (recursive on `Type`/`Effect`).
2. **Parsed AST** for the module — needed by `bundle_file_with_iface` so it
   doesn't re-parse from source. Uses `ast.Program` ADT serialization.
3. **Ctor/class/instance metadata** for typeclass resolution.
4. **Source hash** for freshness check.

### What doesn't get serialized

- Pre-lower (typed) AST. Re-lowering from parsed AST is cheap (~1s on heavy
  tests) and avoids serializing the most complex internal IR.
- LLVM IR text. That goes into the parallel `.bc` artifact (PR 3).

## Driver CLI extensions

- `compile_driver --emit-iface <stdlib-root> <module-path> > out.iface`:
  parse + check a single module, serialize the iface to stdout.
- `compile_driver --emit-bc <stdlib-root> <module-path> > out.ll`:
  parse + check + lower + codegen for the module ONLY (no imports in IR
  output). The module's IR uses `declare` for each external symbol.
- `compile_driver --check-iface <iface-path>`: parse the iface, report
  whether it's well-formed.
- `compile_driver --emit-ir` (existing): grows an `--iface-dir <dir>` flag.
  When set, imports are resolved against pre-built iface files first, falling
  back to source.

## Workflow

1. **Local dev (first time after clone)**: `just refresh-iface` compiles every
   `stdlib/**/*.sprout` module to `build/iface/<qualified.name>.iface`.
2. **Local dev (iterating)**: source change triggers iface staleness on next
   `just test`; recipe regenerates the affected iface(s) before running tests.
3. **CI**: `actions/cache@v4` keyed on
   `iface-${{ runner.os }}-${{ env.CLANG_VERSION }}-${{ hashFiles('stdlib/**/*.sprout', 'stdlib/**/*.spr') }}`
   restores `build/iface/` and `build/bc/`. Cache miss → regenerate.
4. **CI gate**: `just verify-iface-freshness` reads each cached iface, computes
   the current source hash, fails if any iface is stale. Analogous to
   `just verify-bootstrap-fixed-point`.

## Phased PR plan

| PR | Scope | Wins | Risk |
|---|---|---|---|
| PR 1 | iface serialization for `types.Scheme` + `ast.Program` ADTs; `--emit-iface` driver mode; `just refresh-iface`; round-trip tests on a real module | None on speed yet — pure infrastructure | Low — additive |
| PR 2 | `bundle_file_with_iface` reads parsed AST from iface; driver `--iface-dir` flag wires it through | ~20s on heavy tests (bundle skipped for imports) | Medium — touches bundler |
| PR 3 | `--emit-bc` mode; codegen emits `declare` for non-own decls when iface present; `just refresh-bc` produces all `.bc` | ~24s on heavy tests (codegen skipped for imports) | High — codegen path is critical |
| PR 4 | justfile recipes (`compile`, `run`, `_test-stdlib`) link `.bc` artifacts instead of in-source-tree compile; CI cache + freshness gate | Final wall savings; user-facing build model changes | Medium — workflow change |

**Estimate**: 2-3 days per PR × 4 PRs = 7-12 days total.

## Open questions

1. **AST serialization granularity**: full positions (for error messages) or
   stripped? Stripped is smaller but breaks LSP "go-to-definition" through
   iface boundaries.
2. **What about user-app modules?** A user app with its own `app/*.sprout`
   modules should get the same treatment — but committed iface for stdlib only,
   user's modules cached in `build/`.
3. **Cross-PR migration**: between PR 1 and PR 2, iface files are produced but
   unused. Should `--emit-iface` be a no-op stub in PR 1 to enable infrastructure
   first, or fully functional from the start?
4. **Versioning**: when iface format changes, the `iface-version: N` field lets
   driver reject incompatible artifacts. But: should bumps trigger a CI cache
   purge automatically? (Probably yes, via cache-key prefix.)

## Smallest verifiable Phase 1 unit

For the very first iteration:
- A `types.scheme_serialize(s) -> String` + `types.scheme_parse(s) -> Maybe Scheme`
  pair, round-trip-tested on hand-built schemes covering all `Type`/`Effect`
  variants.
- A `tests/stdlib/compiler/test_scheme_roundtrip.spr` proving the round-trip is
  lossless on every scheme variant.

This is independently committable, validates the textual format, and unblocks
the rest of PR 1.

## Phase 2 — AST codec (shipped on `feat/iface-ast-codec`)

Phase 2 adds encode/decode for `ast.Program` and all constituent ADTs to
`stdlib/compiler/iface_codec.sprout`.  The implementation is additive — no
existing Scheme/IfaceFile encode/decode functions were modified.

**New entry points:**
- `encode_ast_expr` / `decode_ast_expr` — all 19 `ast.Expr` constructors
- `encode_ast_pattern` / `decode_ast_pattern` — all 9 `ast.Pattern` constructors
- `encode_ast_decl` / `decode_ast_decl` — all 8 `ast.Decl` constructors
- `encode_ast_program` / `decode_ast_program` — top-level `ast.Program`
- Supporting encoders/decoders for `TypeExpr`, `Param`, `MatchBranch`, `DoStep`,
  `RecordField`, `TemplateExprPart`, `TypeConstraint`, `TypeConstructor`,
  `RecordFieldDecl`, `ClassMethodSig`, `InstanceMethodImpl`, `SourcePos`

**Format additions** (S-expression extensions to Phase 1 grammar):
- `<quoted>` — `"..."` with `\\`, `\"`, `\n`, `\r`, `\t` escape sequences
  for arbitrary string and char content
- `<int>` — bare signed decimal atom (handles negative literals)
- `<bool>` — `true` / `false` bare atoms
- `<char>` — `(Char "<c>")` single codepoint as quoted string
- `<pos>` — `(Pos <idx> <line> <col>)` for `source.SourcePos`
- `<maybe X>` — `(Just X)` / `(Nothing)` for optional fields

**SourcePos**: positions are encoded faithfully (not stripped) per the TASTy
precedent, enabling downstream LSP go-to-definition through iface boundaries.

**Tests:** `tests/stdlib/compiler/test_iface_ast_codec.spr` — 58 assertions
covering all constructor variants, escape edge cases, SourcePos preservation,
and decode-rejection for malformed input.  All pass; no suite regressions.

## References

- Existing CI cache pattern: `.forgejo/workflows/ci.yml` `Cache stage-1 binary`
  step (commit `ae082e3`).
- Memory: `feedback_build_artifacts_not_versioned.md` — artifacts go in
  `build/`, cached on CI, never in `bootstrap/` for new types of artifact.
- Bootstrap precedent: `bootstrap/compile_driver.ll` is the exception that
  proves the rule — committed only because it's the trust anchor.
