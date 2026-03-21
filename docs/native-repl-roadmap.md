# Native REPL Roadmap

This document outlines a pragmatic path from the current hosted Python REPL to
a future Sprout-native REPL binary.

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
2. `repl_add_import(...)`, `repl_add_declaration(...)`, `repl_eval_expr(...)`,
   `repl_type_of(...)`, `repl_instances(...)`, `repl_complete(...)`, and
   `repl_reset_session()` expose the hosted REPL session through an
   experimental interpreter-backed service bridge.
3. `stdlib/repl.sprout` now owns the Sprout-hosted REPL frontend, with
   `examples/repl_hosted.sprout` kept as a thin wrapper/example entrypoint.
   Interactive-mode detection, line editing, history traversal, and
   completion behavior now live in Sprout code rather than Python readline
   policy.
4. Native compiled programs do not support that session bridge yet.

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

### Phase 3. Identify Missing Runtime Hooks

A Sprout-native REPL will need a minimal set of hosted capabilities. Likely
requirements:

1. line-oriented stdin input,
2. stdout/stderr text output,
3. filesystem access for module loading,
4. argv/environment access,
5. terminal helpers for optional prompt polish.

The key question is which of these belong as stable runtime builtins and which
should remain thin host integration points.

### Phase 4. Build a Minimal Sprout REPL App

Start with a line-based REPL app in Sprout that:

1. reads one line,
2. dispatches REPL commands,
3. hands Sprout input to the session engine boundary,
4. prints results and diagnostics.

Do not block this phase on advanced history, completion, or full-screen
terminal behavior; basic Sprout-side line editing is enough to move the REPL
surface out of Python first.

### Phase 5. Replace the Current Entry Point

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
   exist.
