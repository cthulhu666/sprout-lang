# REPL Self-Hosting v1 Draft

This document is a draft design for a Sprout-implemented REPL session engine in
v1.

It is not part of normative v0. Its purpose is to define the first credible
path from the current host-backed REPL session bridge to a REPL whose session
semantics are implemented in Sprout code.

## 1. Problem Statement

Sprout now has a REPL frontend that is mostly written in Sprout:

- the prompt loop lives in `stdlib/repl.sprout`,
- line editing, history, completion behavior, and command parsing live in
  Sprout code,
- Python is down to launcher and host-service responsibilities.

But the actual session engine still lives in host code:

- `sprout/repl_host.py` owns session state,
- `sprout.analysis.py` now owns snapshot-oriented parsing, name resolution,
  typechecking, evaluation, instance lookup, symbol inventory, and diagnostics,
- completion data and session mutation are still host-implemented,
- the frontend reaches those services through the experimental `repl_*`
  builtins.

That is a good hosted architecture, but it is not a self-hosted REPL.

## 2. Goals

1. Define what “REPL implemented in Sprout” means for the first v1 milestone.
2. Replace opaque host REPL services with lower-level compiler/runtime
   capabilities that Sprout code can drive directly.
3. Keep the user-facing REPL semantics stable while changing the underlying
   implementation boundary.
4. Preserve ordinary module/import behavior rather than inventing REPL-only
   language rules.
5. Make the migration incremental and testable.

## 3. Non-Goals

1. Do not make the REPL part of normative v0.
2. Do not require a fully self-hosting compiler in the first milestone.
3. Do not require per-submission native compilation.
4. Do not redesign terminal UX beyond what is needed for the session-engine
   transition.
5. Do not promise that all compiler internals become stable public APIs in one
   step.

## 4. What Counts as “Self-Hosted” for v1

For this document, the relevant ownership boundary is:

1. REPL command parsing should be in Sprout.
2. REPL session state should be represented and evolved in Sprout.
3. Submission classification should be decided in Sprout.
4. Session operations should be composed from lower-level compiler/runtime
   capabilities, not from monolithic `repl_*` host commands.

This still allows some implementation substrate in host code. The v1 milestone
does not require the parser, typechecker, interpreter, or native backend
themselves to be rewritten in Sprout first.

## 5. Current Host-Backed Boundary

Today the temporary host bridge exposes:

```sprout
repl_eval_expr_in_source(module_source: String, expr: String) -> Result String (Vec String) !{IO}
repl_check_source(module_source: String) -> Result String Unit !{IO}
repl_declared_names_in_source(module_source: String) -> Result String (Vec String) !{IO}
repl_diagnostics_in_source(module_source: String) -> Vec (String, Int, Int) !{IO}
repl_type_of_in_source(module_source: String, expr: String) -> Result String String !{IO}
repl_instances_in_source(module_source: String, query: String) -> Result String (String, Vec String) !{IO}
repl_complete_in_state(line_buffer: String, imports: Vec String, declarations: Vec String) -> (String, Vec String) !{IO}
repl_reset_session() -> Unit !{IO}

// legacy compatibility hooks
repl_add_import(source: String) -> Result String Unit !{IO}
repl_add_declaration(source: String) -> Result String Unit !{IO}
repl_eval_expr(source: String) -> Result String (Vec String) !{IO}
repl_type_of(source: String) -> Result String String !{IO}
repl_instances(source: String) -> Result String (String, Vec String) !{IO}
repl_complete(line_buffer: String) -> (String, Vec String) !{IO}
```

Those hooks are useful for bootstrapping, but they encode full REPL policy in
the host runtime. They are too high-level to support a real Sprout-owned
session engine. `repl_type_of_in_source(...)` is a first step toward the
intended v1 direction because it operates on an explicit source snapshot rather
than implicit host-owned REPL state. `repl_instances_in_source(...)` extends
that same pattern to `:instances`. `repl_complete_in_state(...)` does the same
for tab completion by taking explicit frontend state instead of reading hidden
host session state. `repl_check_source(...)` applies the same idea to import
and declaration acceptance. `repl_eval_expr_in_source(...)` does the same for
expression execution. `repl_declared_names_in_source(...)` is the first
explicit symbol-inventory primitive that is useful beyond the REPL, including
future language-server and compiler tooling work. `repl_diagnostics_in_source(...)`
is the first shared diagnostics primitive; it currently returns either an empty
vector or a single `(message, line, column)` diagnostic from the checked
snapshot pipeline. Diagnostics that do not yet expose machine-readable source
locations use `0` for `line` and `column`.

The current host implementation of those snapshot-oriented services now lives
in `sprout.analysis.py`; `sprout.repl_host.py` remains the stateful REPL
session shim.

At this point the active `stdlib/repl.sprout` frontend path no longer uses the
legacy compatibility hooks. They remain only as transitional runtime surface
and compatibility coverage.

## 6. Proposed v1 Direction

The main shift is:

- move from “host executes REPL commands for Sprout”
- to “Sprout drives compiler/session capabilities directly”

That implies replacing the current opaque REPL bridge with lower-level
capability layers.

### 6.1 Session State Layer

Sprout should own an explicit session value that tracks:

1. accumulated imports,
2. accumulated declarations,
3. generated temporary names,
4. command-local scratch state such as pending completion context.

This layer is language-level policy and should not remain trapped in host code.

### 6.2 Submission Classification Layer

Sprout should classify a line as:

1. REPL command,
2. import,
3. declaration,
4. expression,
5. empty input.

This is already mostly true today and should remain a Sprout responsibility.

### 6.3 Compiler Capability Layer

Instead of `repl_eval_expr(...)` and friends, the host/runtime should expose
smaller capabilities such as:

1. build or extend a synthetic module/session,
2. parse module text,
3. resolve names in a session context,
4. typecheck a session or query a bound name type,
5. lower and evaluate a submission,
6. inspect instance heads and exported names.

These may still be host-implemented in v1, but they should no longer be shaped
as REPL commands.

### 6.4 Rendering and Introspection Layer

Completion, type rendering, instance rendering, and value rendering should be
expressed in Sprout-facing data rather than ad hoc host strings where practical.

The first v1 milestone can still use some host-rendered strings, but the
direction should be toward structured results instead of REPL-specific text.

## 7. First v1 Capability Targets

The smallest useful capability set for a Sprout-owned session engine is:

1. session creation/reset,
2. append import/declaration text to a session source model,
3. parse and typecheck a session snapshot,
4. evaluate an expression against a session snapshot,
5. inspect in-scope names and matching instances.

That is enough to rebuild the current REPL behavior in Sprout without relying
on `repl_*` commands as the semantic unit.

## 8. Representation Strategy

The safest first step is to keep the session source model explicit:

```sprout
type ReplSession =
  | ReplSession {
      imports: Vec String,
      declarations: Vec String,
      counter: Int
    }
```

The compiler-facing layer can still consume whole source snapshots in early v1.
That is less efficient than incremental typed-state reuse, but much simpler and
closer to the current implementation model.

Incremental typed-session caching should be treated as a later optimization.

## 9. Diagnostics Direction

The self-hosted REPL should preserve current diagnostic quality:

1. parse/type/runtime errors remain clear and non-cascading,
2. `:type` and `:instances` keep stable readable rendering,
3. module/import failures still include useful source context.

Where possible, compiler-facing capabilities should return structured failures
that Sprout can format, rather than preformatted REPL strings.

## 10. Compatibility and Migration

This should be staged rather than flipped in one change:

1. keep the current `repl_*` bridge working while lower-level capabilities are
   added,
2. teach `stdlib/repl.sprout` to use the lower-level capabilities behind the
   same external behavior,
3. remove or deprecate the opaque `repl_*` layer only after the new path is
   stable.

The user-facing REPL command set does not need to change during this migration.

## 11. Milestone Plan

Proposed v1 self-hosting milestones:

1. Document the target capability split and mark the current `repl_*` bridge as
   temporary.
2. Introduce a lower-level session/compiler service boundary below the current
   REPL hooks.
3. Rework the Sprout REPL frontend to own an explicit session value instead of
   delegating session semantics to the host.
4. Replace opaque completion/type/instance queries with narrower compiler-facing
   operations.
5. Retire the monolithic `repl_*` bridge once the Sprout session engine is
   feature-complete.

Deferred beyond the first milestone:

- fully self-hosted parser/typechecker implementation,
- incremental typed-session caching,
- native per-submission compilation,
- richer IDE/protocol-oriented REPL services.

## 12. Open Questions

1. Which compiler-facing capabilities should return structured values versus
   already-rendered strings?
2. How much of module loading and bundle construction should become callable
   from Sprout in v1?
3. Should instance/completion queries operate on parsed/typechecked state or on
   source snapshots in the first milestone?
4. How much of the eventual compiler service surface should be considered stable
   language tooling API versus internal implementation detail?
