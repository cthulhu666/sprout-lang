# Self-Hosting Backlog: Eliminate Python From The Compiler Pipeline

This document turns the broader self-hosting direction into a concrete
implementation backlog with an explicit end goal:

- the Sprout compiler, REPL/session engine, analysis tooling, and packaging
  flow are implemented in Sprout rather than Python

It is a planning document, not a normative language spec.

## Goal

Reach a state where:

1. ordinary user-facing compiler and tooling paths no longer depend on
   `python -m sprout.cli ...`,
2. compiler pipeline ownership lives in Sprout code,
3. frontend, analysis, module-loading, and packaging logic no longer live in
   `sprout/*.py`,
4. bootstrap steps are explicit and mechanically testable, and
5. any remaining non-Sprout substrate is narrow, intentional, and not a Python
   host runtime.

## Non-Goals

1. Do not require the first self-hosted milestone to remove C/LLVM/native
   runtime substrate.
2. Do not require a one-shot rewrite of parser, typechecker, evaluator, and
   codegen.
3. Do not silently widen the stable language contract just to make
   self-hosting easier.
4. Do not treat temporary bootstrap binaries or transitional host tools as the
   final architecture.

## Scope Clarification

"Replace all Python code with Sprout" should be interpreted as the long-term
goal for repository-owned compiler/tooling logic, especially:

1. CLI and driver orchestration in `sprout/cli.py`,
2. parser/typechecker/module-loading pipeline logic in `sprout/*.py`,
3. REPL host/session logic and analysis-service adapters,
4. compiler-facing tooling orchestration currently shaped around Python entry
   points,
5. packaging/bootstrap flows that currently assume Python as the control plane.

This does not automatically mean every low-level runtime primitive must also be
rewritten in Sprout. Native runtime support, GC substrate, object emission, or
LLVM integration may remain lower-level implementation layers as long as Python
is no longer the compiler/tooling owner.

## Exit Criteria

The self-hosting effort is complete when all of the following hold:

1. the default compiler entrypoint is Sprout-owned,
2. the default REPL/session engine is Sprout-owned,
3. module loading, source discovery, and package resolution policy are
   Sprout-owned,
4. parsing, checking, lowering, and compile orchestration are Sprout-owned,
5. Python compatibility shims are no longer required for normal development
   flows,
6. bootstrap builds are reproducible from a documented Sprout-first pipeline,
7. host-vs-self-hosted parity tests pass on the same conformance corpus, and
8. the remaining Python code in the repository, if any, is limited to archival
   or migration-only artifacts scheduled for removal.

## Backlog

### Phase 0: Define the Target and Freeze the Seams

- [ ] Write a single canonical bootstrap document describing stage-0 through
  self-hosted build order, artifact formats, and trust chain.
- [ ] Inventory every Python-owned compiler/tooling responsibility under
  `sprout/*.py` and classify each as frontend, analysis, runtime bridge,
  codegen, packaging, or test harness.
- [ ] Mark each existing Python entrypoint as one of:
  active architecture, compatibility shim, or migration-only surface.
- [ ] Define the target "Python-free default path" explicitly in docs so the
  project can reject changes that deepen Python coupling.
- [ ] Add a removal tracker for each Python module with an intended successor
  module or phase.

Definition of done:
- every Python-owned responsibility has a planned successor and phase
- the bootstrap chain is documented, not implied

### Phase 1: Make Compiler Requests Explicit

- [ ] Replace hidden mutable-session and command-shaped boundaries with
  compiler-oriented request/response contracts.
- [ ] Continue shrinking REPL-specific host APIs in favor of neutral
  `analysis_*` or future compiler-driver capabilities.
- [ ] Define stable data shapes for source units, module snapshots, diagnostics,
  symbol inventories, typed query results, and compile requests.
- [ ] Make those shapes usable from Sprout without relying on CLI-text parsing
  or ad hoc host-rendered strings.
- [ ] Ensure every new capability is snapshot-oriented and deterministic rather
  than ambient-state driven.

Definition of done:
- Python remains an implementation backend, but no longer defines the semantic
  unit of work

### Phase 2: Sprout-Owned Compiler Driver

- [ ] Expand `stdlib/compiler.sprout` or a successor compiler/tooling package
  into a real driver layer rather than a thin analysis wrapper.
- [ ] Represent compiler inputs, phases, pass results, and diagnostic bundles
  as Sprout types.
- [ ] Implement `check`, analysis queries, and later lowering/compile requests
  through a Sprout driver API.
- [ ] Route at least one real CLI path through that driver while keeping Python
  as a temporary backend.
- [ ] Add parity tests so the driver-backed path matches current hosted
  behavior.

Definition of done:
- compiler control flow is Sprout-owned even though parser/typechecker/codegen
  implementations may still be hosted

### Phase 3: Sprout-Owned REPL and Session Engine

- [ ] Finish moving session state, submission classification, and snapshot
  construction into Sprout.
- [ ] Remove dependence on Python-owned mutable REPL session semantics.
- [ ] Keep completion, `:type`, `:instances`, and evaluation driven by explicit
  compiler capabilities rather than REPL-shaped host commands.
- [ ] Make the native-capable REPL path consume the same Sprout-owned session
  engine as the interpreter-backed path.
- [ ] Retire transitional `repl_*` compatibility hooks once parity coverage is
  sufficient.

Definition of done:
- REPL behavior is no longer a Python-hosted semantic subsystem

### Phase 4: Typed Core and Internal Artifact Boundary

- [ ] Define a stable typed core or checked artifact format that both hosted
  and future self-hosted compiler slices can target.
- [ ] Move more elaboration and normalization logic out of ad hoc host AST
  rewriting and into explicit compiler passes.
- [ ] Decide which internal artifact becomes the first self-hosted compiler
  output:
  typed core, checked module graph, interpreter IR, or another narrow format.
- [ ] Add round-trip and parity tests for artifact production and consumption.
- [ ] Keep codegen/runtime consumers converging on this boundary instead of the
  full surface AST where practical.

Definition of done:
- there is a narrow internal boundary that a Sprout compiler can realistically
  target before owning end-to-end native emission

### Phase 5: Self-Hosted Frontend Slices

- [ ] Choose the first Sprout-owned frontend/compiler slices to replace in
  production paths.
- [ ] Candidate early slices:
  source-model transforms, diagnostic rendering helpers, symbol inventory,
  constrained lowering helpers, selected elaboration passes.
- [ ] Add dual-implementation tests that compare Python and Sprout outputs on
  the same inputs.
- [ ] Keep each slice independently swappable so partial migration remains
  reviewable.
- [ ] Avoid starting with the full parser or HM typechecker unless the runtime
  surface is ready and the slice boundary is already explicit.

Definition of done:
- real compiler sub-passes in production are implemented in Sprout and checked
  against the hosted implementation

### Phase 6: Self-Hosted Parser

- [~] Specify the parser migration boundary:
  full surface syntax vs staged parser for a restricted bootstrap subset.
  *(decided: bootstrap subset first; full-surface parity follows)*
- [~] Implement parser infrastructure in Sprout with strong error recovery and
  stable diagnostics requirements.
  *(`stdlib/compiler/source`, `token`, `lexer` at Python tokenizer parity;
  `stdlib/compiler/ast` defines the full surface AST; `stdlib/compiler/parser`
  exists — parity and error-recovery gaps remain)*
- [ ] Produce a structured AST artifact that can be consumed by the existing
  hosted checker during transition.
- [ ] Run parser conformance suites against both implementations.
- [ ] Make the Sprout parser the default on at least one bootstrap path before
  removing the hosted parser.

Definition of done:
- Python no longer owns syntax parsing on the default compiler path

### Phase 7: Self-Hosted Module Loading and Package Resolution

- [ ] Move source discovery, import resolution, module graph construction, and
  package/dependency policy into Sprout.
- [ ] Add the runtime/stdlib capabilities needed for file-system traversal,
  path normalization, and package metadata loading without reintroducing Python
  as the control plane.
- [ ] Replace Python-owned module-loader logic with a Sprout-owned module graph
  builder.
- [ ] Define caching/incremental invalidation strategy at the same boundary.
- [ ] Add integration tests that compare graph construction and diagnostics
  across hosted and self-hosted implementations.

Definition of done:
- Python no longer owns source graph discovery or import semantics

### Phase 8: Self-Hosted Typechecking and Elaboration

- [~] Decide the migration shape:
  restricted-subset checker first or full-surface checker behind a typed-core
  boundary.
  *(decided: restricted-subset bootstrap checker first)*
- [~] Implement inference, constraint solving, instance resolution, and
  diagnostic shaping in Sprout incrementally.
  *(`stdlib/compiler/types` — Effect/Type/Scheme ADTs; `stdlib/compiler/unifier`
  — pure HM substitution/unification; `stdlib/compiler/infer` — constraint
  generation/solving for expressions, patterns, and declarations; instance
  resolution and diagnostic shaping are not yet started)*
- [ ] Keep typechecker diagnostics stable and non-cascading throughout the
  migration.
- [ ] Add success/failure parity suites for the hosted and self-hosted
  checkers.
- [ ] Make the self-hosted checker authoritative on at least one bootstrap
  path before deleting the Python checker.

Definition of done:
- Python no longer owns semantic analysis on the default compiler path

### Phase 9: Self-Hosted Evaluation and Compiler Services

- [ ] Replace Python-backed analysis-service responsibilities with a Sprout
  compiler-service implementation over the new internal artifacts.
- [ ] Move `type_of`, `declared_names`, `exported_names`, diagnostics,
  instances, symbol inventory, and evaluation requests onto self-hosted
  compiler services.
- [ ] Ensure REPL, editor/language-service, and compiler CLI consumers share
  the same service boundary.
- [ ] Remove Python subprocess assumptions from native and interpreter tool
  flows.
- [ ] Keep service behavior reproducible through snapshot-based contracts.

Definition of done:
- Python no longer owns interactive analysis/evaluation services

### Phase 10: Self-Hosted Backend Orchestration

- [ ] Move compile planning, lowering orchestration, artifact emission policy,
  and packaging flow into Sprout.
- [ ] Decide whether the first self-hosted backend target is:
  interpreter IR, typed-core artifact, existing native backend bridge, or
  another narrow target.
- [ ] Keep LLVM/object emission or native runtime substrate behind explicit
  non-Python seams until Sprout can reasonably own more of that stack.
- [ ] Add end-to-end self-hosted compile tests for representative programs.
- [ ] Make the Sprout-owned compiler pipeline the default build path.

Definition of done:
- Python is no longer the compiler launcher/control plane

### Phase 11: Bootstrap and Self-Compilation

- [ ] Define the minimum language/compiler subset required for the compiler to
  build its own sources.
- [ ] Produce a reproducible stage pipeline such as:
  hosted compiler -> first Sprout compiler artifact -> self-built compiler.
- [ ] Add artifact comparison or semantic parity checks between bootstrap
  stages.
- [ ] Decide release policy for trusting self-built artifacts.
- [ ] Make self-compilation a regular CI/dev verification path.

Definition of done:
- the compiler can participate in building itself through a documented stage
  chain

### Phase 12: Remove Python Compatibility Layers

- [ ] Delete superseded Python entrypoints once equivalent Sprout paths are the
  default and verified.
- [ ] Remove compatibility wrappers such as hidden CLI fallback surfaces that
  only exist to preserve Python ownership.
- [ ] Migrate or delete Python-based build/test/dev scripts that still assume
  Python as the project control plane.
- [ ] Update docs, examples, and contributor workflows so Sprout-first tooling
  is the documented default.
- [ ] Add a policy check that blocks new production-path Python ownership from
  re-entering the repository.

Definition of done:
- Python is no longer required for normal compiler/tooling use or maintenance

## Cross-Cutting Tracks

These tracks run across multiple phases and should be maintained explicitly.

### A. Runtime Surface For Self-Hosting

- [ ] File-system and path APIs sufficient for module/package resolution.
- [ ] Process/subprocess policy only if the self-hosted toolchain still needs
  external executables.
- [ ] Stable serialization/deserialization support for compiler artifacts.
- [ ] Performance work needed so compiler workloads are practical in Sprout.
- [ ] Memory-management/runtime observability good enough for compiler-scale
  workloads.

### B. Test and Parity Infrastructure

- [ ] Build a shared corpus for parser/checker/lowering parity.
- [ ] Add host-vs-self-hosted differential tests for each replaced slice.
- [ ] Preserve stable diagnostics where intended and flag intentional
  differences explicitly.
- [ ] Add bootstrap-stage verification to CI once the first self-hosted stages
  exist.

### C. Documentation and Architecture Hygiene

- [ ] Keep `compiler-self-hosting-roadmap.md` as the high-level staged design.
- [ ] Use this document as the concrete execution backlog for eliminating
  Python.
- [ ] Update `README.md`, `BACKLOG.md`, and relevant design docs when a phase
  meaningfully advances.
- [ ] Keep experimental vs normative status explicit for every self-hosting
  milestone.

## Recommended Execution Order

Execute Phases 0 through 12 in order.

Phase 6 can begin earlier for a restricted bootstrap parser if the project
decides syntax ownership is the best first deep replacement slice, but the
default bias should be driver-first and boundary-first rather than full parser
rewrite first.
