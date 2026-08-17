# Sprout Plugin for JetBrains IDEs — v0

Status: **experimental**. This is an implementation-planning document for tooling. It
does not change the normative language contract; `docs/spec-v0.md` remains authoritative
for the language itself.

Companion: `docs/language-server-roadmap.md` (the server side, and the corrected record
of what the LSP actually does today).

## 1. Problem statement

Opening a `.sprout` file in RubyMine — or any JetBrains IDE — gives plain text. No
highlighting, no diagnostics, no navigation, no comment toggling. Sprout has a working
typechecker and a formatter, and none of it is reachable from the editor the language's
only real user actually writes code in.

A minimal LSP server exists (`stdlib/compiler/lsp_driver.sprout`, launched as
`build/sproutd --lsp <stdlib_root>`), so the transport problem is largely solved. What is
missing is a client, and — measured rather than assumed — most of the server's advertised
surface.

### 1.1 What the server actually does

Established 2026-08-17 by driving the real binary with framed messages (now automated as
`scripts/lsp_smoke.sh`):

| Request | Before this arc | Reality |
|---|---|---|
| `publishDiagnostics` | — | Works, with **precise** per-diagnostic line and column |
| `textDocument/hover` | advertised `hoverProvider: true` | returned `null` unconditionally |
| `textDocument/completion` | advertised a `.` trigger | returned `[]` |
| formatting, definition, documentSymbol | not advertised | absent |

Two notes in the repo were wrong and are corrected in `language-server-roadmap.md`: the
roadmap's "full-range first-error only" (positions were already precise; the defect is
that `end == start`), and an in-file comment claiming hover was blocked on a missing
`type_of_in_source` (it exists).

### 1.2 The server is the cheap part

Every unimplemented feature already has its compiler API:

| LSP feature | Existing API |
|---|---|
| hover | `compiler.type_of_in_source` |
| completion | `compiler.complete_in_state` |
| definition | `compiler.symbol_locations_in_source` |
| document symbols | `compiler.symbol_inventory_in_source` |
| formatting | `formatter.format_source` |

So `lsp_driver.sprout` is a transport with five unplugged sockets. This shapes the whole
arc: the plugin is the visible deliverable, but the leverage is server-side.

## 2. Goals and non-goals

**Goals**

- Syntax highlighting for `.sprout` and `.spr` in **every** IntelliJ-based IDE.
- Real diagnostics from the real typechecker in RubyMine and its paid siblings.
- One codebase for the whole IDE family.
- No second implementation of Sprout's syntax or semantics.

**Non-goals**

- **A PSI/parser-based native plugin.** Rejected on principle — see §4.
- **Marketplace publication and bundled server binaries.** Releases currently ship only
  the batch compiler for Linux (no `sproutd`, no stdlib, no macOS), so v0 points at a
  local checkout. Packaging is a separate problem.
- **Precise multi-token diagnostic spans**, semantic tokens, rename, find-references,
  inlay hints. All want the span refactor in `language-server-roadmap.md` §5.1 first.
- **Community-edition LSP support** — see §5.2 for why, and `BACKLOG.md` §7.6 for the
  LSP4IJ follow-up that would close it.

## 3. Prior art

Not a language-semantics decision, so no cross-language survey applies. The relevant
prior art is how other small languages reach JetBrains IDEs, and the field has converged:
ship an LSP server, then a thin client per editor, rather than reimplementing the compiler
front end inside each IDE's own AST framework. JetBrains itself made that route
first-class in 2023.2 with the platform LSP API, which is the mechanism used here.

## 4. Decision

**One Gradle project at `editors/intellij/`, producing one plugin with two layers.**

1. **Core language layer** — file type, language, lexer, syntax highlighter, colour
   settings, commenter, brace matcher. Depends only on `com.intellij.modules.platform`,
   so it works in every IntelliJ IDE. No LSP and no server process involved.
2. **LSP layer** — an `LspServerSupportProvider` launching `sproutd --lsp`, behind an
   optional dependency on `com.intellij.modules.lsp` so the plugin still loads where that
   module is absent.

**Why not a native PSI plugin.** It would require a Sprout parser written in Kotlin: a
second authority for the language's syntax, maintained by hand, drifting from the
self-hosted parser. That is precisely the failure mode
`docs/module-surface-authority-v0.md` was written to eliminate — the same fact re-derived
in two places, diverging silently. LSP keeps the self-hosted compiler as the only thing
that knows what Sprout means, at the cost of coarser editor integration.

**Why highlighting is *not* delegated to the server.** Semantic tokens would put
colouring behind the same process, but a lexer runs synchronously in the editor and works
with no server configured, in every IDE, on the first keystroke after install. The token
set is 19 keywords plus literals and comments — small enough that a hand-written lexer is
not a meaningful duplication of the compiler's lexer, and the classification already has
an authority to copy: `tree-sitter-sprout/queries/highlights.scm`.

### 4.1 Known imprecision, accepted

`module`, `import`, `as`, `alias`, `linear` and `record` are **not keywords** — they are
absent from `lexer.sprout`'s `is_keyword` and are ordinary identifiers that the parser
interprets positionally. A flat lexer cannot distinguish `import` the soft keyword from
`import` the variable name, so they are highlighted only in first-token-on-line position.
The residual case (a local binding named `import` at the start of a line) is coloured
wrongly and is accepted; the alternative is a parser in the plugin.

## 5. Platform constraints

### 5.1 Verified availability

The JetBrains LSP API is available in the commercial IDEs — IntelliJ IDEA, WebStorm,
PhpStorm, PyCharm, DataSpell, **RubyMine**, CLion, DataGrip, GoLand, Rider, RustRover —
and **unavailable in IntelliJ IDEA open-source builds and Android Studio**. Baseline
2023.2. Feature arrival matters for sequencing: completion 2023.2, document formatting
2023.3, document symbols 2025.3, range formatting 2026.1.

Extension point `com.intellij.platform.lsp.serverSupportProvider`, renamed to
`…integrationProvider` at 2026.1.4+ — too new to require, so v0 targets the older name.

### 5.2 The one unverified assumption

The platform documentation describes `<depends optional="true" config-file="…">` for
*plugin* dependencies and does not state whether it works for platform modules such as
`com.intellij.modules.lsp`. The design assumes it does. **This must be checked in a
sandbox before the LSP layer is considered done**; if it does not hold, the fallback is
two artifacts (core plugin plus LSP add-on) from the same Gradle project — a build-file
change, not a redesign.

### 5.3 Server discovery

No release ships `sproutd`, a stdlib tree, or a macOS build, so the plugin cannot obtain
a server on its own. It takes three settings — sproutd path, stdlib root, package roots —
with walk-up auto-detection from the project root and an explicit notification when they
are unset. A silently dead server is the one outcome to avoid.

## 6. Milestones

| # | Scope | Touches Sprout? |
|---|---|---|
| M0 | Drive the server under test: smoke harness, unit tests, `just lsp-smoke`, CI job | capabilities only |
| M1 | Plugin core: file type, lexer, highlighting, commenter, brace matcher | no |
| M2 | Package roots reach the env typecheck path | yes (reseed) |
| M3 | LSP client layer, settings, optional-depends verification | no |
| M4 | Wire the five features, one change each; add a `ModuleCache` to `LspState` | yes (reseed) |
| M5 | Diagnostic ranges wider than zero | yes (reseed) |

M0–M3 is the smallest sequence producing a plugin worth installing. M4 is where it becomes
worth using daily. Per-milestone detail and open items live in `BACKLOG.md` §7.6.

## 7. Capability honesty as an invariant

M0 established the rule the rest of the arc follows: **a capability is advertised only in
the change that makes it answer.** `scripts/lsp_smoke.sh` asserts the implication — for
each capability present in the `initialize` result, the corresponding request must return
something other than null or empty — and skips the check when the capability is absent. So
the gate arms itself as handlers land, and an advertisement without an implementation
fails it.

This matters more for an LSP client than for a CLI. A missing capability makes the IDE hide
a feature; a lying capability makes the IDE show a feature that does nothing, which reads
as a broken language rather than an incomplete one.

## 8. Syntax, semantics, type-system and error-message impact

**None.** No language surface changes: no new syntax, no typing rules, no evaluation-order
or visibility changes, and no new diagnostics. The only compiler-source edits in the arc
are in the LSP transport (`lsp_driver.sprout`) and in threading an existing resolver's
extra-roots parameter through the env path (M2) — neither alters what any program means.

Diagnostic *text* is unchanged throughout; M5 changes only the range attached to it.

## 9. Compatibility and migration

Purely additive. `editors/intellij/` is a new subtree with its own toolchain, beside
`tree-sitter-sprout/` which is a foreign-toolchain subproject on the same footing. The
JVM toolchain is pinned in `mise.toml`; the plugin build runs in its own CI job and is not
part of the required `test` check.

**Seed impact.** `lsp_driver.sprout` is outside `compile_driver`'s import closure, so
edits to it move the seed fingerprint without changing emitted IR — the
`verify-bootstrap-fixed-point` + `seed-fp-ack` path, not a full reseed. M2 is different:
it touches `module_loader.sprout` and `compiler/compiler.sprout`, both inside the closure,
so it needs a full `just refresh-seed` and will move golden IR.

## 10. Tests

- `tests/stdlib/compiler/test_lsp_driver.spr` — the exported pure helpers (header parsing,
  word-at-cursor including the qualified-name case hover depends on, and hover-param
  extraction with every required-field rejection). Already existed; extended 22 → 29 for
  the gaps found while writing the smoke gate.
- `scripts/lsp_smoke.sh` via `just lsp-smoke` — framing, diagnostic line and column,
  document lifecycle (`didChange` re-checks, `didClose` clears), shutdown behaviour, and
  the §7 honesty invariant. Includes an explicit non-vacuity assertion, because "the
  server said nothing" would otherwise pass every grep-shaped check.
- Plugin side: `./gradlew buildPlugin verifyPlugin`, plus a `runIde` sandbox check that the
  plugin loads in an IDE **without** the LSP module.
- Acceptance: open `uncharted-suns` with its package root configured and confirm
  `loam.*`/`game.*` symbols resolve — the case that fails today (§1.1, `BACKLOG.md` §7.6).
