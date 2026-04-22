# Observability Guard Rails for the Self-Hosted Compiler

This document records design constraints that keep four future features —
logging, debugging, profiling, and introspection — practical to implement in
the self-hosted Sprout compiler without requiring large retroactive rewrites.

It does not schedule implementation of those features. It records what must
not be closed off while building the compiler pipeline in Sprout.

**Scope:** self-hosted Sprout compiler code only (`stdlib/compiler.sprout`,
future `stdlib/compiler/*.sprout` modules, typed core representations, etc.).
The Python host implementation in `sprout/*.py` is a transitional artifact
being replaced, not a design target.

---

## What Each Feature Needs

### Logging

Compiler pipeline stages emit structured diagnostic or trace messages to a
configurable sink without breaking purity discipline.

In Sprout: any pipeline function that logs must be `!{IO}`. The capability
(log sink) is passed explicitly as a function-value argument rather than
captured from global state. This is consistent with the "explicit over
implicit" design policy.

### Debugging

Step-through visibility into the compiler's own execution — watching how a
type is inferred, how a pattern is matched, how a lowering step fires.

In Sprout: requires (a) source locations carried on all compiler IR nodes and
(b) pipeline stages that are individually callable and produce inspectable
output, not entangled monolithic passes.

### Profiling

Per-pass and per-function timing for the self-hosted compiler.

In Sprout: requires the compiler pipeline to be structured as a sequence of
explicit, named, typed passes so each can be timed independently. Fused or
implicit pass boundaries make cost attribution impossible.

### Introspection

A Sprout program querying information about Sprout values and compiler state
at runtime — `type_of`, `arity_of`, call stack, active environment.

In Sprout: requires type information to survive past erasure at the point
where introspection is called. The typed core representation (Stage 4) must
carry enough information to support a `reflect` module later without a full
repass.

---

## Guard Rails

These apply to all new Sprout compiler code written from Stage 2 onward.

### 1. Source locations are first-class in all IR types

Every node type in the source model, typed core, or elaborated IR must carry
a source location field — not attached dynamically, not omitted for "internal"
nodes.

```
-- Good: location is structural
type Expr = { node: ExprNode, loc: Loc }
type Loc = { file: String, line: Int, col: Int }

-- Bad: location bolted on optionally or stored in a side map
type Expr = ExprNode
```

If locations are optional or missing on internal nodes, they cannot be
reconstructed later without a separate pass.

### 2. Pipeline stages are named, typed, explicit functions

Each compilation pass (parse, resolve, check, lower, elaborate, emit) must be
a named top-level function with an explicit typed input and output.

```
-- Good
fn check(program: ParsedProgram) -> Result CheckedProgram CheckError !{IO}
fn lower(program: CheckedProgram) -> LoweredProgram

-- Bad: passes fused into one opaque function or expressed as implicit side effects
```

Profiling requires wrapping individual passes; debugging requires calling them
in isolation. This is also already a design bias in the roadmap ("snapshot-based
deterministic operations over implicit ambient state").

### 3. Effectful capabilities are passed explicitly, not captured globally

Any pipeline function that needs a log sink, debug hook, or diagnostic output
channel receives it as an explicit argument — a function value or a capability
record — not via a global or implicitly captured binding.

```
-- Good
fn check(program: ParsedProgram, log: String -> Unit !{IO}) -> Result CheckedProgram CheckError !{IO}

-- Bad: log writes to a globally captured stderr binding
```

This keeps every pass independently testable (pass a no-op logger in tests)
and lets future tooling swap in structured logging, debug capture, or profiling
hooks without rewriting the pass.

### 4. Do not fuse pass boundaries before measuring

Keep parse, resolve, typecheck, elaborate, and lower as separate data
transformations with explicit typed output values, even if a fused version
would be faster. Fusing permanently closes off per-pass instrumentation and
per-pass caching.

Optimization can fuse later with measurement; un-fusing is expensive.

### 5. Type information must survive into the typed core

The typed core (Stage 4) must attach type information to every expression
node, not only to declarations. Type erasure, if it happens, must be an
explicit step producing a separate erased representation — not the implicit
default during lowering.

If erasure is implicit, re-threading type information for introspection or
debugging later requires replaying the typechecker.

### 6. Effect annotations must be accurate on all pipeline functions

Every self-hosted compiler function that performs I/O (file reads, error
output, environment queries) must be annotated `!{IO}`. Pure analysis passes
must remain pure.

When a `Log` or `Debug` effect label is added later, it will need to be
threaded through the call graph. Inaccurate existing `!{IO}` annotations make
that threading unreliable.

---

## Relationship to Self-Hosting Stages

| Stage | Guard rails that apply |
|---|---|
| Stage 2 (compiler driver) | #2 explicit passes, #3 explicit capabilities, #6 accurate effects |
| Stage 3 (frontend slices) | #1 source locations, #4 no premature fusion |
| Stage 4 (typed core) | #1, #5 type survival |
| Stage 5+ (bootstrap) | all six are load-bearing |

---

## What This Does Not Decide

- Which effect label logging or debugging uses (`!{IO}` or a future `!{Log}`)
- Whether introspection is a stdlib module or a language primitive
- The concrete shape of a debug protocol or profiler output format
- When any of these features are actually implemented
