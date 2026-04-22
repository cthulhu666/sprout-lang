# Compiler Self-Hosting Roadmap

This document outlines a pragmatic path toward a Sprout compiler whose core
implementation is itself written in Sprout.

It is a tooling and architecture roadmap, not a normative language spec.

Status note: this is not the active near-term milestone. The current priority
remains the native-capable hosted REPL bridge and the reusable language-service
boundary below it. This roadmap describes the intended direction after that
bridge work reaches its pause point.

## Problem Statement

Sprout already has:

1. a working parser, typechecker, interpreter, and native backend,
2. a mostly Sprout-written REPL frontend,
3. a native-capable REPL launcher path, and
4. an emerging bridge between the frontend and host-backed analysis services.

That is enough to support language growth and native backend work, but it is
not yet a path to a compiler implemented in Sprout. The current implementation
still depends on host code for:

1. parsing and typechecking,
2. module loading and name resolution,
3. evaluation and analysis queries, and
4. native code generation and binary production.

Without a staged roadmap, “self-hosting” stays vague and risks collapsing into
an all-or-nothing rewrite project.

## Goals

1. Define what counts as meaningful compiler self-hosting for Sprout.
2. Reuse the current native REPL bridge and language-service seams as compiler
   infrastructure rather than treating them as REPL-only work.
3. Break self-hosting into stages that are independently testable and useful.
4. Preserve the current language surface while shifting implementation
   ownership toward Sprout code.
5. Keep bootstrap assumptions explicit at every stage.

## Non-Goals

1. Do not make self-hosting part of normative v0.
2. Do not require a full compiler rewrite before any value is delivered.
3. Do not require the first self-hosted milestone to emit native code.
4. Do not require immediate host elimination from every tooling path.
5. Do not commit yet to a specific bootstrap chain or distribution model.

## What “Implemented In Sprout” Means

For this roadmap, self-hosting is about ownership of compiler semantics and
compiler control flow, not immediate removal of every host dependency.

Useful milestones can count as progress even if they still rely on host
substrate for things like:

1. file I/O,
2. process launching,
3. native object emission,
4. bridge transport, or
5. bootstrap binary production.

The important question is which layer owns the compiler pipeline logic.

## Current Baseline

Today the repository has three important pieces of groundwork:

1. the host implementation in `sprout/`,
2. a Sprout-owned REPL frontend in `stdlib/repl.sprout`, and
3. a bridge that is increasingly shaped as reusable analysis/compiler
   infrastructure rather than REPL-only glue.

That means the most realistic self-hosting path is not “rewrite the compiler in
one jump”. It is:

1. make compiler-facing service boundaries explicit,
2. move session and snapshot policy into Sprout first,
3. move compiler pipeline orchestration into Sprout next,
4. then replace host implementations incrementally behind stable seams.

## Relationship To Existing Docs

This roadmap complements, rather than replaces:

1. [native-repl-roadmap.md](./native-repl-roadmap.md)
2. [repl-self-hosting-v1-draft.md](./repl-self-hosting-v1-draft.md)

Those documents cover the native-capable REPL and the first Sprout-owned
session-engine direction. This document is broader: it treats that work as the
front end of a later self-hosted compiler/tooling stack.

## Staged Roadmap

### Stage 0: Finish the Reusable Host Boundary

Objective:
- reach the native-REPL pause point with a clear analysis/compiler service seam

Why it matters:
- a self-hosted compiler needs a stable boundary before it can replace the host
  implementation behind that boundary

Expected outcomes:
1. the remaining Python dependency is isolated behind explicit adapter/backend
   seams,
2. active frontend behavior no longer depends on legacy REPL-specific host
   commands, and
3. docs describe the next work as backend replacement rather than REPL
   stabilization, with the execution-oriented backend bundle as the first
   replacement target.

This stage is already the active project priority.

### Stage 1: Sprout-Owned Session and Snapshot Engine

Objective:
- move REPL/session semantics and snapshot construction into Sprout

Scope:
1. session state represented in Sprout values,
2. submission classification in Sprout,
3. source-snapshot construction in Sprout,
4. host bridge reduced to lower-level analysis/eval capabilities.

Why it matters:
- this is the first place where Sprout starts owning compiler-adjacent
  orchestration rather than just terminal UX

Primary reference:
- [repl-self-hosting-v1-draft.md](./repl-self-hosting-v1-draft.md)

### Stage 2: Sprout-Owned Compiler Driver

Objective:
- introduce a compiler-driver layer in Sprout that orchestrates:
  parsing, name resolution, typechecking, lowering, and execution/compile
  requests through explicit capability seams

Scope:
1. define compiler pipeline data structures in Sprout,
2. drive analysis passes from Sprout code instead of monolithic host commands,
3. make compiler queries consume explicit inputs and produce explicit outputs,
4. keep host implementations as temporary backends.

Why it matters:
- this is the first milestone that looks like “compiler logic in Sprout”
  instead of just “REPL session logic in Sprout”

Key constraint:
- do not require the underlying parser/typechecker implementation itself to be
  rewritten yet

Current status:
1. an initial experimental `stdlib/compiler.sprout` module now wraps the
   snapshot-analysis bridge in Sprout-owned session and result types,
2. that slice currently covers explicit session construction plus `check`,
   `declared_names`, `exported_names`, `type_of`, `eval_lines`,
   `symbol_inventory`, `diagnostics`, and `instances` queries,
3. it still relies on host-backed `analysis_*` capabilities and
   `repl_complete_in_state(...)`, while execution-oriented helpers now live
   directly in `sprout.analysis_execution_backend`, so compile/lower
   orchestration remains future Stage 2 work rather than completed
   self-hosting.
4. Phase 1 work should keep that wrapper compiler-facing rather than
   REPL-shaped, starting with the execution-oriented backend bundle behind the
   existing bridge contract.

### Stage 3: Self-Hosted Frontend Slices

Objective:
- replace narrow compiler frontend pieces with Sprout implementations one slice
  at a time

Likely order:
1. source-model and diagnostic rendering helpers,
2. symbol inventory / source snapshot transforms,
3. constrained parsing helpers or lightweight surface transforms,
4. selected lowering/elaboration passes,
5. eventually parser and typechecker slices if the language/runtime surface is
   ready.

Why this order:
- lowering and analysis helpers are more tractable bootstrap targets than a
  full HM typechecker rewrite

Primary requirement:
- every replaced slice must be testable against the existing host
  implementation

### Stage 4: Typed Core and Elaborator Boundary

Objective:
- define a smaller internal compiler representation that both host and future
  Sprout implementations can target

Why it matters:
1. self-hosting is much easier against a compact typed core than against the
   full surface AST,
2. future desugarings such as generalized `do` should not stay entangled with
   the host typechecker forever,
3. a stable elaboration boundary lets Sprout-owned compiler components replace
   host passes incrementally.

Expected scope:
1. surface AST remains parser-facing,
2. typecheck/elaboration produces a smaller typed core,
3. later optimization or codegen work targets that core.

This is likely the first compiler-architecture milestone that benefits both the
host compiler and the future self-hosted compiler substantially.

Current status:
1. the first explicit elaboration boundary is now in place for `do` notation,
2. typechecking resolves `do` families but no longer performs the rewrite
   itself,
3. that elaboration step now emits a narrow core expression form for `do`
   lowering before adapting back into the existing AST pipeline,
4. runtime and codegen still consume the adapted AST form with `do` removed.

### Stage 5: Bootstrap-Capable Self-Hosted Analysis

Objective:
- make a Sprout program capable of performing a useful subset of compiler work
  on Sprout source

Candidate MVPs:
1. parse/check a restricted module subset,
2. run source snapshot analysis queries,
3. perform elaboration into a core form,
4. emit an interpreter-targeted IR or serialized checked form.

This stage does not yet require native code emission. The point is to produce a
real compiler artifact path implemented in Sprout, even if the final execution
or packaging step still uses host support.

### Stage 6: Bootstrap-Capable Self-Hosted Compiler

Objective:
- support a credible bootstrap story where a Sprout-implemented compiler can
  build meaningful Sprout programs and eventually participate in building
  itself

Possible landing points:
1. self-hosted compiler emits checked/core artifacts consumed by the host
   backend,
2. self-hosted compiler emits an interpreter-targeted representation,
3. self-hosted compiler drives an existing native backend through a narrower
   host boundary,
4. only later does it gain end-to-end native code emission ownership.

This is the first stage that should reasonably be called “Sprout compiler
implemented in Sprout” in the stronger sense.

## Bootstrap Strategy Questions

The roadmap does not settle the bootstrap chain yet, but any serious
self-hosting milestone will need explicit answers for:

1. What is the first self-hosted artifact format?
2. Does the self-hosted compiler target the interpreter first, typed core
   first, or the native backend first?
3. How are compatibility checks run between host and Sprout implementations?
4. What is the minimum subset needed before the self-hosted compiler can build
   its own sources?
5. When, if ever, does the project require host/implementation parity as a
   release gate?

## Recommended Near-Term Design Biases

To avoid blocking the long-term roadmap, near-term implementation work should
prefer:

1. explicit request/response data over hidden mutable host session state,
2. reusable analysis/compiler boundaries over REPL-specific commands,
3. typed intermediate forms over ad hoc direct AST rewriting,
4. snapshot-based deterministic operations over implicit ambient state, and
5. tests that can later compare host and self-hosted implementations on the
   same inputs.

### Observability Design Constraints

All self-hosted Sprout compiler code written from Stage 2 onward must also
respect the constraints in [observability-guard-rails.md](./observability-guard-rails.md).
That document records what must not be closed off to keep logging, debugging,
profiling, and introspection practical to add later. The six constraints are:
source locations first-class in all IR types, pipeline stages as named typed
functions, effectful capabilities passed explicitly, no premature pass fusion,
type information surviving into the typed core, and accurate effect annotations
on all pipeline functions.

## Definition of Success

This roadmap succeeds if it gives the project a clear progression from:

1. hosted compiler and hosted analysis services,
2. to Sprout-owned session/compiler orchestration,
3. to Sprout-owned compiler slices,
4. to a bootstrap-capable compiler path implemented substantially in Sprout.

The main point is not to promise immediate self-hosting. It is to keep current
native-REPL and compiler-architecture work aligned with that eventual outcome.
