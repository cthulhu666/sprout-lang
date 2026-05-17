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
- [x] Produce a structured AST artifact that can be consumed by the existing
  hosted checker during transition.
  *(The compile driver became the default compile path; the hosted checker is no
  longer the downstream consumer — transition complete.)*
- [x] Run parser conformance suites against both implementations.
  *(`test_parser_parity.py` runs driver.sprout + dump_ast.py on a shared corpus;
  26/26 pass, one known cosmetic divergence in where-binding internal name.)*
- [x] Make the Sprout parser the default on at least one bootstrap path before
  removing the hosted parser.
  *(`just compile` uses `compile_driver_bin_stage1` → `parser.sprout` as of
  2026-05-17; Python parser is now secondary.)*

Definition of done:
- Python no longer owns syntax parsing on the default compiler path

### Phase 7: Self-Hosted Module Loading and Package Resolution

- [x] Move source discovery, import resolution, module graph construction, and
  package/dependency policy into Sprout.
  *(`stdlib/compiler/module_loader.sprout` + `bundler.sprout` implement full
  topological loading, cycle detection, prelude injection, and name qualification;
  used by the compile driver on the default `just compile` path.)*
- [x] Add the runtime/stdlib capabilities needed for file-system traversal,
  path normalization, and package metadata loading without reintroducing Python
  as the control plane.
  *(`read_file` builtin provides the file I/O needed; the compile driver resolves
  import paths without Python involvement.)*
- [x] Replace Python-owned module-loader logic with a Sprout-owned module graph
  builder.
  *(compile driver uses `bundler.sprout`; `test_bundler_parity.py` confirms parity
  with `sprout/module_loader.py` on shared corpus.)*
- [ ] Define caching/incremental invalidation strategy at the same boundary.
  *(genuinely open — no incremental build support yet)*
- [~] Add integration tests that compare graph construction and diagnostics
  across hosted and self-hosted implementations.
  *(`test_bundler_parity.py` covers graph construction (3/3); diagnostic
  parity (error messages, cycle detection output) not yet tested.)*

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
  generation/solving for expressions, patterns, and declarations; constraint-
  satisfaction checking implemented via env-encoding of `@class:`/`@inst:` markers;
  missing-instance errors produced at concrete call sites)*
- [~] Keep typechecker diagnostics stable and non-cascading throughout the
  migration.
  *(`BodyLenient` silent swallowing removed 2026-05-04; hard errors now propagate.
  Full diagnostic stability (stable message text, non-cascading on complex programs)
  is an ongoing quality goal, not a one-time task.)*
- [~] Add success/failure parity suites for the hosted and self-hosted
  checkers.
  *(`stdlib/compiler/checker.sprout` wraps `infer.typecheck_decls` with a `CheckResult`
  ADT; `stdlib/compiler/type_driver.sprout` is the Sprout-side executable;
  `tools/dump_types.py` is the Python-side comparison tool;
  `tests/test_checker_parity.py` confirms 8/8 corpus files match — forall
  generalization fully implemented, ClassDecl/InstanceDecl handled, builtin env
  seeded; type aliases (`AliasDecl`) parsed and transparently expanded as a
  pre-desugar pass in the Python typechecker and skipped in the bootstrap
  checker; Set type added for constraint-satisfaction work; corpus expanded from
  6→8; constraint-satisfaction checking landed; record field access landed —
  `RecordDecl`/`RecordExpr`/`GetFieldExpr` inference via env-encoding;
  `record_types.spr` fixed and added to conformance corpus; parity corpus 9/9)*
- [x] Make the self-hosted checker authoritative on at least one bootstrap
  path before deleting the Python checker.
  *(`just compile` uses `compile_driver_bin_stage1` → `infer.sprout` /
  `checker.sprout` as of 2026-05-17; Python checker is now secondary.)*

Definition of done:
- Python no longer owns semantic analysis on the default compiler path

### Phase 9: Self-Hosted Evaluation and Compiler Services

- [x] Replace Python-backed analysis-service responsibilities with a Sprout
  compiler-service implementation over the new internal artifacts.
  *(2026-05-17: `stdlib/compiler/analysis_service_driver.sprout` is a JSON-over-stdio
  daemon that handles `declared_names_in_source`, `exported_names_in_source`,
  `symbol_inventory_in_source`, `symbol_locations_in_source`, `check_source`, and
  `diagnostics_in_source`; built as `analysis_service_bin` via `just build-analysis-service`.
  Root cause of crash fixed: `term_read_line`, `term_write`, `json_parse` added to
  `extern_sigs_list()` in `codegen.sprout`. All 5 manual test cases pass.)*
- [~] Move `type_of`, `declared_names`, `exported_names`, diagnostics,
  instances, symbol inventory, and evaluation requests onto self-hosted
  compiler services.
  *(Structural ops complete; `type_of`, `instances`, `eval_expr` stub to `not_implemented`.)*
- [ ] Ensure REPL, editor/language-service, and compiler CLI consumers share
  the same service boundary.
  *(Blocked: binary needs stdlib-root passed as argv[0]; `SPROUT_ANALYSIS_SERVICE_CMD`
  must include the path, e.g. `./analysis_service_bin /path/to/stdlib`.)*
- [ ] Remove Python subprocess assumptions from native and interpreter tool
  flows.
- [ ] Keep service behavior reproducible through snapshot-based contracts.
  *(Parity test suite `test_analysis_service_parity.py` not yet written.)*

Definition of done:
- Python no longer owns interactive analysis/evaluation services

### Phase 10: Self-Hosted Backend Orchestration

- [~] Move compile planning, lowering orchestration, artifact emission policy,
  and packaging flow into Sprout.
- [x] Decide whether the first self-hosted backend target is:
  interpreter IR, typed-core artifact, existing native backend bridge, or
  another narrow target.
  *(Decided: LLVM IR via `--emit-ir`; `codegen.sprout` owns the IR emitter.)*
- [x] Keep LLVM/object emission or native runtime substrate behind explicit
  non-Python seams until Sprout can reasonably own more of that stack.
  *(LLVM IR emitted by Sprout; clang links `runtime/sprout_runtime.c`; no Python
  in the link step as of 2026-05-17.)*
- [x] Add end-to-end self-hosted compile tests for representative programs.
  *(Stage-1/2/3 bootstrap verified: `test_bootstrap_stage1.py` (BootstrapStage2Tests
  + BootstrapStage3Tests), `test_bootstrap_identity.py`, `test_stage2_emit_ir.py`.
  M7 fixed-point confirmed 2026-05-17: stage-1 and stage-2 binaries are the same
  size (1 692 072 bytes); IR byte-identical across rounds.)*
- [x] Make the Sprout-owned compiler pipeline the default build path.
  *(2026-05-17: `just compile` and `just compile-native` now use `compile_driver_bin_stage1` for IR emission;
  `just compile-examples` defaults to stage-1. Python remains only for `--emit-runtime-c`.)*

Definition of done:
- Python is no longer the compiler launcher/control plane

### Phase 11: Bootstrap and Self-Compilation

- [ ] Define the minimum language/compiler subset required for the compiler to
  build its own sources.
  *(See `docs/bootstrap-chain.md` for stage chain; full language surface is used —
  no restricted subset was needed. Formal subset specification not yet written.)*
- [x] Produce a reproducible stage pipeline such as:
  hosted compiler -> first Sprout compiler artifact -> self-built compiler.
  *(Fully reproducible: `just build-stage0` (Python genesis) → `just build-stage1`
  → `just build-stage2` → `just build-stage3` (fixed-point verification).
  Documented in `docs/bootstrap-chain.md` (2026-05-17).)*
- [x] Add artifact comparison or semantic parity checks between bootstrap
  stages.
  *(`test_bootstrap_stage1.py`: BootstrapStage2Tests (IR-emission parity),
  BootstrapStage3Tests (behavioral parity, stage-2 vs Python reference);
  `test_bootstrap_identity.py`: bundle-phase identity across stage-0 and stage-1;
  `test_stage2_emit_ir.py`: IR structural health + self-compile smoke gate.)*
- [ ] Decide release policy for trusting self-built artifacts.
  *(genuinely open — no release process defined yet)*
- [~] Make self-compilation a regular CI/dev verification path.
  *(CI runs `just build-stage1` on every push/PR; `BootstrapStage3Tests` run
  in CI when `compile_driver_bin_stage2` artifact is present. Stage-3 artifact
  is not produced in CI yet — would require caching the stage-2 binary.)*

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
- [~] Add bootstrap-stage verification to CI once the first self-hosted stages
  exist. Stage-1 bootstrap parity: `test_bootstrap_stage1.py` (BootstrapStage2Tests
  + BootstrapStage3Tests); CI runs `just build-stage1` on every push/PR.
  Stage-3 fixed-point (M7) confirmed 2026-05-17: binary sizes identical across
  rounds; IR byte-identical. Remaining gap: CI does not yet cache/produce the
  stage-2 binary, so `BootstrapStage3Tests` skips in CI unless the artifact
  is pre-built.

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
