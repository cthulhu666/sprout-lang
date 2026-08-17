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

> **Correction after wiring hover (2026-08-17): read that table with one caution.** An
> `analysis_*`-backed entry in `stdlib/compiler.sprout` is not Sprout code — it is a
> `declare` into the C runtime, which talks to a **co-process** launched as
> `sproutd --analysis-service <stdlib_root>`. That command line carries no package roots,
> so any feature routed through it silently loses them, and would fail on exactly the
> projects whose diagnostics succeed. Hover therefore went through
> `compiler.type_of_expr_in_source` — in-process and roots-aware — not through
> `compiler.type_of_in_source`, whose name suggests otherwise. Definition never had the
> problem: `compiler.declaration_position` is in-process. Completion and document symbols
> still need checking against this, and formatting does not (it is pure text).

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
- **Community-edition LSP support** — the platform's LSP API is absent there, so the
  language layer works and diagnostics do not. `BACKLOG.md` §7.6 carries the LSP4IJ
  follow-up that would close it.
- **Go-to-definition for locals, parameters and constructors.** The server's
  `symbol_locations_in_source` covers top-level declarations only, and adding scope
  tracking is a separate piece of work from wiring the request.

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

### 4.1 A flat PSI tree, and why there is one at all

Rejecting a real parser leaves a gap: several editor features — the commenter, brace
matching, extend-selection — reach for the PSI of the file they act on, and a language
file type with no `ParserDefinition` has none.

`SproutParserDefinition` supplies a **flat** tree: one root node over the lexer's tokens,
with no structure above them. That is enough for all three features and honest about
knowing nothing more. It is not a placeholder for a future real parser; per §4, a
hand-maintained Kotlin parser is a non-goal, not a deferral.

### 4.2 Known imprecision, accepted

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

### 5.1.1 Toolchain, and a corrected assumption

**Java 21, for the whole supported range.** The arc was initially planned around JDK 17;
JetBrains' build-number-ranges table lists Java 21 as required from **2024.2** onward —
that is, for every version this plugin targets, not only recent ones. Pinned in
`editors/intellij/mise.toml` alongside Gradle 9.7, deliberately *not* in the root
`mise.toml`: `jdx/mise-action` installs everything in `[tools]` for every CI job, and no
other job needs a JVM.

Build: IntelliJ Platform Gradle Plugin 2.x (`org.jetbrains.intellij.platform`).

**The plugin ID may not contain the word `intellij`.** `dev.sprout.intellij` is rejected by
the verifier (`TemplateWordInPluginId`) as a leftover from the project template, so the ID
is `dev.sprout.lang`. The Kotlin package keeps the longer name. Worth recording because
`buildPlugin` and the test suite both accepted the bad ID — only `verifyPlugin` caught it,
which is the argument for keeping the verifier in the release path.

### 5.2 The optional-module split — RESOLVED, and the check is not the obvious one

The platform documentation describes `<depends optional="true" config-file="…">` for
*plugin* dependencies and does not state whether it works for platform modules such as
`com.intellij.modules.lsp`. It does: `plugin.xml` carries the optional dependency, every
LSP extension is registered in `sprout-lsp.xml`, and the plugin builds and verifies as one
artifact. The two-artifact fallback is not needed.

**The interesting part is how that is enforced, because the obvious check does not work.**
Running the plugin verifier against IntelliJ IDEA Community reports:

```
Package 'com.intellij.platform.lsp' is not found along with its 4 classes.
```

and fails the build. That problem is *expected and harmless* — the referencing classes are
registered only in `sprout-lsp.xml`, which an IDE without the module never loads, so they
are never classloaded. But the verifier cannot distinguish "safely absent" from "will throw
`NoSuchClassError`"; its own wording is "may be caused by absence of optional dependency",
which hands the judgement back to the reader. It is therefore red whether the split is
intact or broken — useless as a gate, and corrosive as a habit, because a permanently red
check trains people to ignore it.

The real discriminator is not *whether* the package is referenced but *from where*, and
that is decidable from the bytecode. `scripts/plugin_lsp_split_check.sh`
(`just plugin-split-check`, wired into CI) asserts that every class referencing
`com.intellij.platform.lsp` lives under `dev.sprout.intellij.lsp`. It needs no IDE
download, and it refuses to pass vacuously in either direction — no classes found, or *no*
class referencing the LSP API, both fail loudly rather than reporting a reassuring OK.

RED-verified by adding an LSP reference to `SproutLanguage` and watching it name that class.

Consequently `pluginVerification` targets commercial IDEs only. That is not a gap in
coverage; it is the same question asked by an instrument that can answer it.

### 5.3 Server discovery

No release ships `sproutd`, a stdlib tree, or a macOS build, so the plugin cannot obtain
a server on its own. It takes three settings — sproutd path, stdlib root, package roots —
with walk-up auto-detection from the project root and an explicit notification when they
are unset. A silently dead server is the one outcome to avoid.

## 6. Milestones

| # | Scope | Touches Sprout? |
|---|---|---|
| M0 | **Done.** Drive the server under test: smoke harness, unit tests, `just lsp-smoke`, CI job | capabilities only |
| M1 | **Done.** Plugin core: file type, lexer, highlighting, colour page, commenter, brace matcher | no |
| M2 | **Done.** Package roots reach the env typecheck path | yes (reseed) |
| M3 | **Done.** LSP client layer, settings + toolchain detection, split gate | no |
| M4 | Wire the five features, one change each; add a `ModuleCache` to `LspState`. **Definition and hover done**; formatting, document symbols and completion remain | yes (reseed) |
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
or visibility changes, and no new diagnostics. The compiler-source edits in the arc are in
the LSP transport (`lsp_driver.sprout`), in threading an existing resolver's extra-roots
parameter through the env path (M2), and in promoting two analysis mechanisms into
`compiler/compiler.sprout` so the transport and the analysis service share them
(`declaration_position`, `scheme_of_expr_in_source`) — none of which alters what any
program means. The analysis service's error strings are preserved byte-for-byte, since the
REPL prints them.

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
  document lifecycle (`didChange` re-checks, `didClose` clears), shutdown behaviour, package
  roots, definition, hover, and the §7 honesty invariant. Includes an explicit non-vacuity
  assertion, because "the server said nothing" would otherwise pass every grep-shaped check.

  Two of its assertions are load-bearing beyond the feature they name. The package-root
  **hover** case cannot pass if hover is routed across the analysis-service fork, so it was
  written before that design was chosen and decided it. And the "declines on a non-name
  position" cases pass *vacuously* while a handler is unwired — noted next to each, because
  an assertion that is green for the wrong reason is how a fixture the server had never
  parsed once looked like a working decline.
- `tests/stdlib/compiler/test_expr_type_in_source.spr` — the promoted type-of-expression
  core: rendering, the raw `Scheme` form evaluation needs, each distinguishable failure,
  the sentinel-collision guard, and a package-root case with a negative control.
- `editors/intellij/src/test/kotlin/.../SproutLexerTest.kt` — 25 tests, fixture-free JUnit
  (the lexer touches no platform service, so no IDE sandbox is needed and a failure can
  only mean a lexing bug). Pins the restatement against the compiler's actual rules: the
  19 keywords and no more, contextual words recognised only at line start, `0x`/`0b`
  literals, `0x` with no digits degrading to `0` + `x` exactly as `scan_radix_end` does,
  `1..5` as int-operator-int rather than a malformed float, an unterminated string stopping
  at the newline, and interpolation depth so a record literal inside `${…}` does not end it.

  The strongest test is the last: it lexes **real** sources — `prelude.sprout`,
  `string.sprout`, `json.sprout`, `lexer.sprout`, `parser.sprout`, `lsp_driver.sprout` —
  and asserts full buffer coverage with zero unclassifiable characters. Hand-written
  fixtures only prove the lexer handles what its author thought of. It fails rather than
  skips when a file is missing, since a silent skip would quietly retire it.
- Plugin side: `just plugin-test`, `just plugin-build`, `just plugin-verify`. The verifier
  reports **Compatible** against 2024.2, 2024.3, 2025.1 and 2025.2 — all IntelliJ IDEA
  *Community*, which is the empirical form of §4's claim that the language layer needs no
  commercial API.
- Later, for the LSP layer: a `runIde` sandbox check that the plugin loads in an IDE
  **without** the LSP module.
- Acceptance: open `uncharted-suns` with its package root configured and confirm
  `loam.*`/`game.*` symbols resolve — the case that fails today (§1.1, `BACKLOG.md` §7.6).
