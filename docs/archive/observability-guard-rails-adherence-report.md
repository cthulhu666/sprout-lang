# Self-Hosted Compiler Observability Guard Rails Adherence Report

Date: 2026-05-26

This report assesses the current self-hosted Sprout compiler against
`docs/observability-guard-rails.md`.

Scope is limited to Sprout compiler code in `stdlib/compiler.sprout` and
`stdlib/compiler/*.sprout`. The Python host implementation is intentionally
out of scope.

## Summary

Current adherence is partial.

The compiler has useful foundations: explicit high-level phase functions,
typed expression IR, structured result values, and an emerging debug metadata
path in codegen. However, the compiler does not yet fully preserve source
locations or type information through all IR stages, and observability hooks
are not modeled as explicit capabilities.

The highest-risk gap is type erasure during lowering: `lowering.lower_program`
returns an untyped `ast.Program`, and the IR pipeline re-typechecks the lowered
program before codegen. This directly conflicts with the guard rail that type
information should survive into typed core until an explicit erasure step.

## Guard Rail Assessment

### 1. Source locations are first-class in all IR types

Status: partial.

Good:

- Source positions are structural on tokens, parser errors, expressions,
  patterns, declarations, and typed expressions.
- `typed_ast.TypedExpr` carries both `types.Type` and `source.SourcePos` on
  every expression variant.

Gaps:

- Several source-model nodes do not carry their own location:
  `TypeExpr`, `Param`, `MatchBranch`, `DoStep`, `RecordField`,
  `TemplateExprPart`, `TypeConstraint`, `TypeConstructor`,
  `RecordFieldDecl`, `ClassMethodSig`, and `InstanceMethodImpl`.
- Several typed IR wrapper nodes do not carry their own location:
  `TypedMatchBranch`, `TypedDoStep`, `TypedRecordField`,
  `TypedInstanceMethod`, `TypedDecl`, and `TypedProgram`.
- Generated/internal nodes still use `dummy_pos()` or `SourcePos(0, 0, 0)`,
  especially in bundling, inference, and lowering.

Impact:

Debugging and diagnostics can usually point at expressions, but cannot reliably
identify all internal or structural nodes without reconstructing context.

### 2. Pipeline stages are named, typed, explicit functions

Status: mostly aligned.

Good:

- `compiler.sprout` exposes named phase functions:
  `compile_phase_bundle`, `compile_phase_check`, `compile_phase_lower`,
  `compile_phase_recheck`, `compile_full_ir_lines`, and related cache-aware
  variants.
- Each phase returns a typed result value instead of relying on hidden output.

Gaps:

- `bundler.bundle_file` still combines module discovery, file reads, lexing,
  parsing, symbol collection, and qualification behind one public phase.
- Parse and resolve can be debugged through helpers such as `dump_qualify_file`,
  but they are not exposed as first-class typed public pass outputs.

Impact:

Top-level profiling and debugging are practical, but fine-grained attribution
inside bundling remains awkward.

### 3. Effectful capabilities are passed explicitly, not captured globally

Status: weak.

Good:

- Core compiler APIs generally return structured values rather than printing.
- Debug output for codegen is controlled by an explicit `debug_mode` argument
  and emitted as a separate IR section.

Gaps:

- There is no explicit logging, debugging, profiling, or diagnostics capability
  record threaded through compiler passes.
- Some helper/debug paths still print directly.
- `debug_mode: Bool` is a switch, not a replaceable debug sink or hook.

Impact:

Future observability features can still be added, but they will require API
changes across passes instead of plugging into an existing capability argument.

### 4. Do not fuse pass boundaries before measuring

Status: mostly aligned.

Good:

- The main driver keeps bundle, check, lower, recheck, and codegen separated.
- Code comments explicitly preserve phase isolation to control live memory and
  support staged operation.

Gaps:

- Bundling is still a compound phase from the public API perspective.
- The parse/resolve boundary is present internally but not consistently exposed
  as stable typed pass results.

Impact:

The high-level pipeline remains instrumentable. More detailed parse/resolve
profiling would require additional pass APIs.

### 5. Type information must survive into the typed core

Status: not compliant.

Good:

- `TypedExpr` attaches `types.Type` to every expression node.
- Codegen consumes a rechecked `TypedProgram`, so the final emit step has type
  information available.

Gaps:

- `lowering.lower_program` converts `typed_ast.TypedProgram` back into
  untyped `ast.Program`.
- `compile_phase_recheck` then re-typechecks the lowered AST so codegen can
  recover typed expressions.
- This is type replay after lowering, not explicit erasure after typed core.

Impact:

This is the main architectural risk for introspection and debugging. A later
reflect/debug system would have to rely on rechecking or reconstructing type
facts instead of reading them from the lowered representation.

### 6. Effect annotations must be accurate on all pipeline functions

Status: conservative but imprecise.

Good:

- Functions that read files, write output, run processes, or interact with
  terminal services are annotated `!{IO}`.
- Driver and service loops consistently expose IO effects.

Gaps:

- Type inference and checking are also annotated `!{IO}` because fresh
  variables use refs through the current effect model.
- This conflates internal compiler state mutation with external IO.
- Direct debug prints in inference helpers make some analysis paths effectful
  for output reasons rather than data-flow reasons.

Impact:

The annotations are safe from an execution standpoint, but they are not precise
enough to cleanly separate future `Log`, `Debug`, `State`, and external `IO`
effects.

## Recent Positive Movement

The current codegen path now has a separate `DbgEmitter`, a `debug_mode`
argument, and a fourth IR section for debug metadata. This is a meaningful
step toward debugger support because it keeps debug emission explicit and
separable from the normal IR sections.

This should still be treated as an early debugging output mechanism, not full
guard-rail compliance. It does not yet solve location completeness, typed
lowered IR, or explicit debug-hook capability threading.

## Recommended Next Steps

1. Introduce a typed lowered/core IR.

   Lowering should produce a representation that preserves source positions
   and expression types. If type erasure is needed, make erasure a separate
   named pass.

2. Add locations to remaining AST and typed IR wrappers.

   Prioritize `TypeExpr`, `Param`, branch/step/field wrappers, and typed
   declaration nodes. Replace `dummy_pos()` use with derived or generated
   provenance where possible.

3. Split bundling into stable typed sub-passes.

   Expose module collection, parse, symbol collection, qualification, and
   validation as individually callable functions with typed outputs.

4. Define an observability capability record.

   Start with no-op-friendly fields for logging and debug events. Thread it
   through one or two top-level phases before expanding across the full
   compiler.

5. Separate internal state effects from external IO when the language supports
   it.

   Fresh-name generation and mutable compiler work buffers should eventually
   be distinguishable from file, terminal, and process IO.

## Overall Rating

The compiler currently preserves the intent of the guard rails better than it
fully satisfies their letter.

It is strongest on explicit high-level phases and typed expression nodes. It is
weakest on location completeness, type preservation through lowering, and
explicit observability capabilities.
