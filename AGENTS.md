# AGENTS.md

This file defines project-local working rules for humans and coding agents contributing to Sprout.

## Project Intent

Sprout is a statically typed, functional-first language aimed at strong safety with beginner-friendly ergonomics.

Primary design references:
- `docs/language-design-v0.md`
- `docs/language-design-best-practices.md`

## Collaboration Rules

1. Keep changes small and reviewable.
2. Do not mix unrelated refactors with language-semantics changes.
3. Update docs and tests in the same change when behavior changes.
4. Prefer explicit tradeoff notes over implicit assumptions.
5. Use repository-managed tools via `mise` and `just` (avoid ad-hoc global tool versions).

## Design Change Process

For any non-trivial language change, include:

1. Problem statement.
2. Goals and non-goals.
3. Syntax and semantics impact.
4. Type-system impact.
5. Error-message impact.
6. Compatibility/migration notes.
7. Tests added/updated.

## Code and Testing Expectations

1. Parser changes need parser tests.
2. Typechecker changes need both success and failure tests.
3. Runtime/semantic changes need executable behavior tests.
4. Keep diagnostics stable and understandable; avoid noisy cascades.
5. Preferred execution path for local commands:
   `mise exec -- just <task>`

## Directory Conventions

- `docs/` normative and supporting design docs.
- `examples/` user-facing language examples.
- `sprout/` implementation source.
- `mise.toml` toolchain definition.
- `justfile` standard developer tasks.

## Commit Guidance

Use commit messages that explain intent, for example:
- `spec: define match exhaustiveness rules`
- `parser: add infix precedence for comparison operators`
- `types: improve error for mismatched function arguments`
