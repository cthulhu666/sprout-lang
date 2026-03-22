# Language Server Roadmap

This document outlines a pragmatic plan for adding a Sprout language server for
editor and IDE support.

Status:

- This is an implementation-planning document.
- It does not change the normative language contract.
- Any language-server support shipped from this plan should be labeled
  experimental until the feature set and protocol surface stabilize.
- This work is currently deferred behind native REPL bridge work and should be
  treated as a v2+ direction rather than an active near-term milestone.

## 1. Problem Statement

Sprout already has the core ingredients needed for IDE support:

- tokenizer
- parser
- module loader
- typechecker
- formatter
- REPL-oriented completion helpers

However, those pieces are currently optimized for batch CLI execution and
human-readable diagnostics. A language server needs:

- persistent workspace state
- in-memory handling of unsaved editor buffers
- structured diagnostics with precise source ranges
- symbol and type queries at arbitrary positions
- incremental re-analysis after edits
- an LSP protocol adapter

The main gap is not parsing or type inference. The main gap is turning the
compiler pipeline into a persistent analysis service.

## 2. Goals

- Reuse the existing Python implementation where practical.
- Keep language-semantics changes out of the language-server project.
- Deliver a useful MVP quickly.
- Make diagnostics, hover, and definition quality good enough to be trusted.
- Build internal APIs that remain useful even if the transport or editor
  integration changes later.

## 3. Non-Goals

- No language-surface expansion as part of the language-server effort.
- No native-backend integration requirement for the MVP.
- No requirement for full fine-grained incremental type inference in the first
  milestone.
- No commitment to shipping every advanced LSP feature before an initial
  release.

## 4. Proposed Architecture

The implementation should be split into four layers.

### 4.1 Analysis Layer

Add a reusable compiler-service layer, for example `sprout.analysis`, that:

- accepts source text and file/module context
- parses and typechecks programs
- returns structured diagnostics
- returns symbol metadata and definition locations
- returns inferred type information for queried nodes
- exposes formatting and completion helpers

This layer should be callable from tests, the CLI, REPL integrations, and the
language server.

### 4.2 Workspace Layer

Add a workspace/document manager, for example `sprout.workspace`, that:

- stores open-document contents
- resolves imports against editor buffers first and the filesystem second
- tracks module dependencies
- invalidates and recomputes affected analysis results on change
- indexes exported names, types, constructors, and modules

This is the main stateful layer needed to avoid re-running the entire compiler
pipeline on every LSP request.

### 4.3 Protocol Layer

Add an LSP-facing module, for example `sprout.lsp_server`, that:

- handles JSON-RPC/LSP message flow
- converts file URIs to workspace paths
- translates editor positions to internal spans and back
- publishes diagnostics
- answers hover, definition, completion, and formatting requests

This layer should remain thin. It should not own core semantic logic.

### 4.4 Source Metadata Layer

The existing AST and diagnostics machinery should be upgraded so that:

- relevant nodes carry full source spans, not just ad hoc line and column data
- identifiers and declarations can be linked reliably
- diagnostics carry machine-readable ranges and optional related locations

This is the key prerequisite for high-quality editor features.

## 5. Required Refactors in the Current Codebase

### 5.1 Standardize Source Spans

Current state:

- tokens carry line and column
- AST nodes can receive location attributes dynamically
- most code paths do not preserve full start/end ranges

Needed:

- introduce a `Span` model or equivalent start/end range representation
- attach spans to declarations, expressions, patterns, type expressions, and
  import clauses
- preserve spans through parse, name resolution, and typechecking

Without this, hover and go-to-definition quality will remain fragile.

### 5.2 Introduce Structured Diagnostics

Current state:

- parser, module-loader, and typechecker failures are largely surfaced as
  formatted exception strings

Needed:

- a shared diagnostic model with severity, message, primary range, and optional
  related locations
- adapters from existing error paths to that diagnostic model
- preservation of current human-readable error quality for CLI output

The CLI can still print readable messages, but the internal representation
should stop being string-only.

### 5.3 Build Symbol and Definition Metadata

Current state:

- `sprout.analysis` now exposes `symbol_locations_in_source(...)` and
  `symbol_metadata_in_source(...)` for top-level declarations and explicit
  imports in a single checked snapshot, including definition-site locations for
  imported symbols when the provider declaration is available in the checked
  module bundle
- `sprout.analysis` also exposes `structured_diagnostics_in_source(...)` with
  severity/stage/location records, while the builtin-facing diagnostics query
  still uses the older tuple bridge
- that is enough for early symbol indexing experiments, but not yet for locals,
  full definition links, or range-accurate editor features

Needed semantic information includes:

- local bindings
- top-level declarations
- imported names and their origin modules
- type constructors
- exported module members
- later, class and instance metadata

This likely means creating explicit symbol-table/index structures instead of
reconstructing definitions from raw syntax on demand.

### 5.4 Make Module Loading Workspace-Aware

Current state:

- module loading is primarily filesystem-oriented

Needed:

- support for unsaved editor buffers
- import resolution that can read from open documents
- dependency tracking and invalidation

### 5.5 Split Editor Completion from REPL Completion

Current state:

- the repository already contains REPL completion support

Needed:

- a reusable completion engine aware of lexical scope, imports, constructors,
  module members, and keywords
- protocol-neutral completion data that the REPL and LSP can both consume when
  appropriate

## 6. MVP Feature Set

The initial language-server milestone should ship only the highest-value
features.

### 6.1 Diagnostics

- parse errors
- module/import errors
- type errors

This should be the first shipped capability.

### 6.2 Document Formatting

Use the existing formatter to support `textDocument/formatting`.

### 6.3 Hover

Hover should show:

- inferred type
- declared type when present
- effect annotation when relevant
- declaration origin when helpful

### 6.4 Go to Definition

Support:

- local bindings
- top-level names
- imported names
- constructors
- type declarations

### 6.5 Completion

Support:

- keywords
- in-scope locals
- top-level names
- imported names
- module-qualified names
- constructors

## 7. Later Features

After the MVP is stable, add:

- document symbols
- workspace symbols
- find references
- rename
- signature help
- semantic tokens
- code actions such as missing-import suggestions
- inlay hints for inferred types

These should come only after spans, symbol metadata, and workspace invalidation
are proven reliable.

## 8. Recommended File Layout

Likely new files:

- `sprout/analysis.py`
- `sprout/workspace.py`
- `sprout/diagnostics.py`
- `sprout/lsp_server.py`

Likely touched existing files:

- `sprout/ast.py`
- `sprout/parser.py`
- `sprout/module_loader.py`
- `sprout/typechecker.py`
- `sprout/formatter.py`
- `sprout/cli.py`

This separation keeps semantic logic out of the LSP transport layer.

## 9. Testing Strategy

The language-server work should add tests alongside implementation changes.

Recommended coverage:

- span/source-range tests
- structured diagnostic snapshot tests
- workspace dependency invalidation tests
- hover tests
- definition tests
- completion tests
- LSP protocol smoke tests

Where practical, analysis tests should exercise internal APIs directly rather
than going through full JSON-RPC round trips.

## 10. Delivery Phases

### Phase 1: Analysis Foundations

- add source-span support
- add structured diagnostics
- extract reusable analysis APIs
- add tests

This phase should end before any real LSP transport work starts.

### Phase 2: Workspace Model

- add document store
- add in-memory module loading
- add dependency graph and invalidation
- add symbol indexing

### Phase 3: LSP MVP

- add server bootstrap and capabilities
- wire diagnostics
- wire formatting
- wire hover
- wire go-to-definition

### Phase 4: Completion and Index Features

- add completion
- add document symbols
- add workspace symbols

### Phase 5: Advanced Editor Features

- references
- rename
- semantic tokens
- signature help
- code actions

## 11. Tradeoffs

### Build the Server in Python First

This is the pragmatic default because:

- the compiler is already written in Python
- there is no need to bridge across languages or processes to reach semantic
  logic
- MVP iteration speed should be much higher

### Keep the Internal APIs Transport-Neutral

Even if the first language server is written in Python, the analysis and
workspace layers should not be tightly coupled to JSON-RPC details. That keeps
future migration options open.

### Avoid Semantics Work in the Tooling Track

The language server should not become a vehicle for sneaking in language
semantics changes. Tooling should reflect the current language contract rather
than widen it implicitly.

## 12. Risks

- Source-location metadata may need a broader refactor than first expected.
- Typechecker internals may need restructuring to expose node-level type data.
- Module resolution for unsaved buffers can become subtle once imports,
  stdlib-loading rules, and experimental features interact.
- Completion quality can look poor if symbol indexing is incomplete or
  diagnostics are too lossy.

The strongest mitigation is to invest early in spans, diagnostics, and
workspace-aware analysis before shipping the transport layer.

## 13. Recommended First Change Set

The first implementation slice should be:

1. Add a proper span model across AST and parser output.
2. Add structured diagnostics shared by parser, module loader, and typechecker.
3. Extract a reusable `sprout.analysis` API for whole-file analysis.
4. Add tests for spans and diagnostics.
5. Review that foundation before starting protocol work.

This keeps the first milestone small, reviewable, and aligned with the
project's preference for root-cause fixes over workarounds.
