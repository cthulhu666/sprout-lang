# Native REPL Roadmap

This document outlines a pragmatic path from the current hosted Python REPL to
a future Sprout-native REPL binary.

For the longer-term self-hosting direction where the REPL session engine itself
becomes Sprout-owned rather than host-backed, see
[repl-self-hosting-v1-draft.md](./repl-self-hosting-v1-draft.md).

Current priority note: that self-hosting track is no longer the active
near-term milestone. The active goal is a native-capable REPL path that keeps
the current Sprout frontend and narrows the remaining host bridge below it.
That bridge should not be treated as REPL-only glue; it should be shaped as a
reusable language-service boundary that later compiler and language-server work
can also target.

It is an implementation/tooling roadmap, not a normative language spec.

## Problem Statement

The current REPL is implemented inside the Python CLI. It already supports
incremental declarations, imports, type queries, instance queries, auto-printing
of pure expressions, and a Sprout-side interactive input loop.

That is useful today, but it mixes three different concerns:

1. Sprout language semantics.
2. REPL session semantics.
3. Host-specific terminal and process behavior.

If the long-term goal is a REPL written in Sprout and shipped as a native
binary, those boundaries need to be made explicit first.

## Goals

1. Define one stable REPL session model independent of the current Python host.
2. Keep foundational prelude behavior and ordinary `import` usage aligned with
   ordinary Sprout modules.
3. Make the first native-capable REPL target the interpreter-style evaluation
   model before attempting incremental native compilation.
4. Separate terminal UI concerns from session/typecheck/evaluation concerns.
5. Identify the minimum runtime and stdlib hooks a Sprout-native REPL would
   need.
6. Keep the host bridge reusable enough that later self-hosted compiler and
   language-server work can consume the same service boundary instead of
   rebuilding parallel infrastructure.

## Non-Goals

1. This roadmap does not make the REPL part of normative v0.
2. This roadmap does not require immediate replacement of the Python CLI.
3. This roadmap does not assume JIT compilation or per-submission native code
   generation.
4. This roadmap does not commit to a specific line-editing library or terminal
   stack yet.

## Current Baseline

Today the REPL behaves like a synthetic module-backed session with:

1. foundational prelude available by default,
2. ordinary `import ...` lines accepted in the session,
3. incremental declarations and expression evaluation,
4. REPL-only commands such as `:type`, `:instances`, `:help`, and `:quit`.

This is already closer to the desired long-term surface than the earlier split
between “plain REPL” and “REPL with preloaded stdlib mode”.

Current experimental runtime progress:

1. `term_read_line()` now provides line-oriented stdin input.
2. `repl_eval_expr_in_source(...)`, `repl_check_source(...)`,
   `repl_declared_names_in_source(...)`, `repl_exported_names_in_source(...)`,
   `repl_symbol_inventory_in_source(...)`,
   `analysis_symbol_locations_in_source(...)`, `repl_diagnostics_in_source(...)`,
   `repl_type_of_in_source(...)`, `repl_instances_in_source(...)`,
   `repl_complete_in_state(...)`, and `repl_reset_session()` now form the
   active hosted bridge used by `stdlib/repl.sprout`. Snapshot analysis for
   that bridge now lives in `sprout.analysis` rather than the REPL host module.
3. Legacy compatibility hooks still exist in the host runtime:
   `repl_add_import(...)`, `repl_add_declaration(...)`, `repl_eval_expr(...)`,
   `repl_type_of(...)`, `repl_instances(...)`, and `repl_complete(...)`.
   They are no longer on the active Sprout REPL frontend path.
   Neutral `analysis_*` aliases now also exist for the snapshot-analysis
   subset so future tooling does not need to depend on REPL-specific names.
4. `stdlib/repl.sprout` now owns the Sprout-hosted REPL frontend, with
   `examples/repl_hosted.sprout` kept as a thin wrapper/example entrypoint.
   Interactive-mode detection, line editing, history traversal, and
   completion behavior now live in Sprout code rather than Python readline
   policy.
4. The user-facing `sprout.cli repl` command is still interpreter-launched by
   default, but now also exposes an experimental `--native` launcher path.
5. The current native-focused milestone is making the active frontend services
   callable from compiled clients without depending on Python REPL internals.
6. The canonical native-REPL subprocess boundary is now
   `python -m sprout.analysis_adapter` as the
   first explicit bridge for snapshot-oriented `check_source`,
   `declared_names_in_source`, `exported_names_in_source`,
   `symbol_inventory_in_source`, `diagnostics_in_source`,
   `type_of_in_source`, `instances_in_source`, and `eval_expr_in_source`
   queries, plus explicit-state completion. The hidden
   `sprout.analysis_service` and `sprout.cli analysis-service` remain only as
   compatibility wrappers.
7. Native compiled programs now use that bridge for `repl_check_source(...)`,
   `analysis_check_source(...)`, `repl_declared_names_in_source(...)`,
   `analysis_declared_names_in_source(...)`,
   `repl_exported_names_in_source(...)`,
   `analysis_exported_names_in_source(...)`,
   `repl_symbol_inventory_in_source(...)`,
   `analysis_symbol_inventory_in_source(...)`,
   `repl_diagnostics_in_source(...)`, `analysis_diagnostics_in_source(...)`,
   `analysis_symbol_locations_in_source(...)`,
   `repl_type_of_in_source(...)`, `analysis_type_of_in_source(...)`,
   `repl_instances_in_source(...)`, and `analysis_instances_in_source(...)`,
   plus `repl_eval_expr_in_source(...)` and compatibility-only
   `repl_complete_in_state(...)`.
   The remaining snapshot hooks are still unsupported in native binaries.
8. End-to-end native execution of the current Sprout REPL frontend is now
   covered in tests by compiling and running `examples/repl_hosted.sprout`
   against that bridge.
9. The product surface now exposes that path experimentally via
   `sprout.cli repl --native`, while still depending on the Python
   `analysis-service` subprocess underneath. The launcher now also reuses a
   cached compiled REPL binary between launches instead of recompiling on
   every run, and the cached native REPL binary now carries its own default
   `analysis-service` command based on the Python used at compile time instead
   of relying on the launcher to inject `SPROUT_ANALYSIS_SERVICE_CMD`. Native
   programs now also reuse one long-lived
   `analysis-service` subprocess across multiple snapshot queries in the same
   process, with one automatic restart for replay-safe snapshot queries if the
   child exits mid-run. Native REPL cache-build failures now surface the
   underlying native compile error with an interpreter-REPL fallback hint.
   Invalid analysis-service commands now surface an
   explicit `SPROUT_ANALYSIS_SERVICE_CMD` hint instead of only a generic empty
   response error. Native REPL startup itself is bridge-lazy again: the cached
   native frontend can start and quit without a live `analysis-service`, and
   the subprocess is now only contacted on the first snapshot-backed action.
   The Python module is also reduced to the transport adapter over a reusable
   dispatcher seam plus a shared JSON-line protocol/session loop, so the next
   replacement step can target service execution without reworking the
   request/response protocol again. The dispatcher now targets a backend
   contract in `sprout.analysis_backend`, and the current Python-backed
   implementation lives below that seam in `sprout.analysis_backend_python`.
   Bridge command resolution, startup/error
   messages, and replay-safe retry policy are now centralized in shared
   analysis-bridge contract helpers instead of being spread across the native
   launcher, tests, and embedded runtime template. The persistent-child stdio
   lifecycle block is now also rendered from a dedicated bridge-runtime helper
   instead of living inline inside `sprout.cli`, and the repeated
   `module_source` request builders above that seam now use shared bridge
   helper functions rather than open-coded `snprintf` blocks in each wrapper.
   The simplest shared response shapes now do the same for native-side
   `Err`, `Ok String`, and `Ok (Vec String)` result construction. The
   `instances` bridge path now also uses a shared `(String, Vec String)` tuple
   decoder instead of open-coded response shaping in `sprout.cli`, and the
   active REPL completion and symbol-inventory paths now do the same for their
   `(prefix, matches)` and `(declared, imported, exported)` tuple decoders, and
   diagnostics and symbol-locations now use shared structured-vector decoders
   as well. The native bridge helper block is also rendered centrally now, so
   `sprout.cli` no longer assembles request/response helper fragments itself.
   The Python stdio and compatibility-service entrypoints are also down to thin
   shims over a neutral adapter/session runner, and the adapter-facing
   dispatcher now depends on a narrow backend facade instead of importing the
   broader `sprout.analysis` module directly. The dispatcher also accepts an
   injected backend now, so the request/response protocol is no longer tied to
   one concrete Python implementation object. The neutral adapter/session
   runner now accepts an injected backend as well, while `python -m
   sprout.analysis_adapter` still defaults to the current Python-backed
   implementation. The default Python backend is now also starting to split
   into smaller implementation bundles below that seam, with the read-only
   snapshot symbol/query operations living separately from execution-oriented
   backend operations.
10. Verification should increasingly target the dedicated module entrypoint
    rather than the hidden CLI compatibility wrapper, so the remaining Python
    dependency is narrowed to the analysis-service module boundary itself.
11. The active `complete_in_state` path now belongs with the shared
    analysis/service helpers rather than the stateful REPL-host shim, which
    keeps one more native-REPL dependency out of `sprout.repl_host`.
12. The Sprout REPL frontend itself no longer uses `repl_complete_in_state(...)`
    for `Tab` completion; that behavior now runs locally in
    `stdlib/repl.sprout` from the current imports/declarations text state, so
    interactive completion no longer depends on the Python analysis-service
    subprocess. Local completion is now ASCII case-insensitive and can resolve
    imported namespace members such as `json.string` after `import stdlib.json`.
13. The active frontend startup path no longer calls `repl_reset_session()`;
    that hook is now compatibility-only and no longer part of the current
    Sprout-hosted REPL loop.
14. The active frontend now supports explicit multiline entry with `:{` / `:}`
    and executes the accumulated block as sequential REPL submissions in both
    interpreter and native launcher modes, while still preserving multiline
    declarations inside the block and using a distinct `block| ` continuation
    prompt during interactive entry.
15. Block mode now also supports `:cancel` (and `:abort`) to discard the
    buffered block and return to the main prompt without mutating session
    state.

## Target Architecture

### 1. Session Engine

Create or formalize a reusable REPL session engine with operations such as:

1. add import,
2. add declaration,
3. typecheck expression,
4. evaluate expression,
5. query instances for a type,
6. render value output and diagnostics.

The important constraint is that this engine should describe REPL behavior in
terms of Sprout concepts rather than terminal or CLI concerns.

### 2. Command Layer

Keep REPL commands outside the core language parser:

1. `:type EXPR`
2. `:instances TYPE`
3. `:help`
4. `:quit`

The command layer should translate these requests into session-engine
operations. That keeps the eventual Sprout-native REPL small and makes command
behavior testable independently of the line editor.

### 3. Frontend Layer

Treat line reading, prompt rendering, history, completion, and terminal control
as a separate frontend.

That frontend may initially remain host-provided even after the session model is
cleaned up. The point is to keep the boundary explicit so a Sprout-native
frontend can replace it later.

## Recommended Execution Strategy

### Phase 1. Formalize the REPL Session Contract

Define the session behavior explicitly and keep tests centered on:

1. persistent imports and declarations,
2. auto-printing of pure values,
3. effectful evaluation rules,
4. type query behavior,
5. instance query behavior,
6. diagnostic quality.

This phase is mostly about freezing semantics before changing runtime strategy.

### Phase 2. Extract Session Logic from CLI Policy

Reduce Python-side REPL special cases so the CLI becomes a thin wrapper around a
session engine. Success looks like:

1. fewer ad hoc source-rewriting branches,
2. clearer distinction between session state and host UI,
3. easier reuse from non-interactive tests and future tools.

### Phase 3. Introduce an Explicit Host-Service Bridge

Before native compiled REPL clients can use the current session and analysis
services, those services need a transport boundary that is separate from the
Python REPL frontend.

Success looks like:

1. snapshot-oriented analysis/session operations are reachable through one
   dedicated host-service entrypoint,
2. the bridge protocol is explicit and testable,
3. future native support can target that bridge instead of importing Python
   REPL modules or depending on CLI-specific control flow.

This phase does not make the REPL self-hosted. It only makes the remaining host
bridge cleaner and more native-friendly.

### Phase 4. Identify Missing Runtime Hooks

A Sprout-native REPL will need a minimal set of hosted capabilities. Likely
requirements:

1. line-oriented stdin input,
2. stdout/stderr text output,
3. filesystem access for module loading,
4. argv/environment access,
5. terminal helpers for optional prompt polish.

The key question is which of these belong as stable runtime builtins and which
should remain thin host integration points.

### Phase 5. Build a Minimal Sprout REPL App

Start with a line-based REPL app in Sprout that:

1. reads one line,
2. dispatches REPL commands,
3. hands Sprout input to the session engine boundary,
4. prints results and diagnostics.

Do not block this phase on advanced history, completion, or full-screen
terminal behavior; basic Sprout-side line editing is enough to move the REPL
surface out of Python first.

### Phase 6. Replace the Current Entry Point

Once the Sprout implementation is feature-complete enough, the Python CLI `repl`
entry point can become a launcher for the Sprout-native REPL binary.

At that point, Python remains a bootstrap/build tool rather than the owner of
REPL semantics.

## Interpreter-First vs Native-Code-First

The first Sprout-native REPL should target interpreter-style incremental
evaluation, not per-input native compilation.

Reasons:

1. startup and per-submission latency are more important than peak throughput,
2. incremental native compilation would introduce much more state-management
   complexity,
3. debugging REPL semantics is easier when evaluation strategy is simpler,
4. the native backend can still be used later for explicit compile/run flows.

Native compilation for REPL submissions should be treated as a later optimization
or separate execution mode, not the first milestone.

## Open Design Questions

1. Should the session engine operate on accumulated source text, accumulated
   typed AST, or another explicit IR?
2. Should REPL value rendering reuse `print(...)` semantics directly, or should
   the REPL grow a distinct “show” path?
3. Which terminal capabilities are worth standardizing in `stdlib.terminal`
   versus leaving in host-specific launcher code?
4. How much of module loading should become callable from Sprout itself?
5. Is there value in a machine-readable REPL protocol before a terminal-native
   frontend exists?

## Near-Term Concrete Work

The next practical steps are:

1. document the REPL session contract and keep tests aligned with it,
2. extract the current Python REPL into a clearer session-engine boundary,
3. audit runtime hooks needed for a Sprout-hosted interactive app,
4. prototype a minimal line-based REPL application in Sprout once those hooks
   exist,
5. return to the self-hosting draft only after the native-capable hosted path
   is working; that later phase can then replace the temporary bridge with a
   lower-level compiler/session capability layer.
