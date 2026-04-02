# AGENTS.md

This file defines project-local working rules for humans and coding agents contributing to Sprout.

## Project Intent

Sprout is a statically typed, functional-first language aimed at strong safety with beginner-friendly ergonomics.

Primary design references:
- `docs/spec-v0.md`
- `docs/language-design-v0.md`
- `docs/language-design-best-practices.md`

## Collaboration Rules

1. Keep changes small and reviewable.
2. Do not mix unrelated refactors with language-semantics changes.
3. Update docs and tests in the same change when behavior changes.
4. Prefer explicit tradeoff notes over implicit assumptions.
5. Use repository-managed tools via `mise` and `just` (avoid ad-hoc global tool versions).
6. Treat the normative spec as a maintained artifact, not a backlog item.
7. Before making any non-trivial change, present a short high-level implementation overview and wait for user approval.
8. Explicitly call out any proposal to add a new builtin or to keep functionality in the host runtime instead of implementing it in Sprout; builtin/runtime additions require user approval up front.
9. Prefer fixing root-cause issues over introducing workarounds when the root cause is reasonably tractable.

## Docs and TODO Hygiene

1. Keep `README.md`, `docs/spec-v0.md`, and relevant `docs/*.md` aligned with current behavior after every feature or semantics change.
2. If a task listed in roadmap/TODO sections is completed, update or remove it in the same change.
3. If new follow-up work is discovered during implementation, add it to the appropriate roadmap/TODO section with concise scope.
4. Do not leave stale examples or commands in docs (`.spr` vs `.sprout`, old flags, outdated syntax).
5. Treat documentation drift as a bug: fix it before marking work complete.
6. When implementation and docs disagree, resolve the mismatch in the same change; do not leave “known drift” behind.
7. Distinguish clearly between normative docs and implementation-status docs.
8. If a feature is implemented but not yet normative, mark it explicitly as experimental in user-facing docs.
9. If a feature becomes part of the language contract, update the normative spec in the same change.

## Spec and Source of Truth

1. `docs/spec-v0.md` is the normative source of truth for the stable Sprout core.
2. `README.md` should summarize current capabilities, but must not silently contradict the normative spec.
3. Supporting design docs should explain rationale and tradeoffs; they do not override the normative spec.
4. Experimental or prototype-only features must be labeled consistently across `README.md`, `docs/*.md`, examples, and tests.
5. Do not widen the language contract implicitly through examples, stdlib surface area, or implementation alone.
6. If a change alters syntax, semantics, typing rules, evaluation order, visibility/export rules, or diagnostics expectations, update the relevant spec/docs before considering the task complete.

## Design Change Process

For any non-trivial language change, include:

1. Problem statement.
2. Goals and non-goals.
3. High-level implementation overview for approval before editing.
4. Syntax and semantics impact.
5. Type-system impact.
6. Error-message impact.
7. Compatibility/migration notes.
8. Tests added/updated.
9. Spec/docs updated, with the normative vs experimental status made explicit.

## Code and Testing Expectations

1. Parser changes need parser tests.
2. Typechecker changes need both success and failure tests.
3. Runtime/semantic changes need executable behavior tests.
4. Keep diagnostics stable and understandable; avoid noisy cascades.
5. Preferred execution path for local commands:
   `mise exec -- just <task>`
6. For intermediate verification during development, prefer targeted parallel runs through `mise exec -- just test ...` or `SPROUT_TESTS="..." mise exec -- just test` before falling back to the full parallel gate; use serial `just test-serial` only when the task requires order-sensitive debugging.
7. Final verification uses the authoritative parallel full-suite run via `mise exec -- just test` with no explicit test filter; use `mise exec -- just test-serial` only as a fallback when diagnosing runner discrepancies or order-sensitive failures.
8. Spec-affecting changes should also add or update conformance coverage where practical.

## Definition of Done

For coding tasks, work is done only when:

1. The change has been designed at a high level and approved by the user when required.
2. The implementation is complete.
3. Relevant docs/spec updates are complete and in sync with the implementation.
4. Formatting and linting have been run when applicable.
5. The entire test suite has been run via `mise exec -- just test` with no explicit test filter.
6. During implementation, the faster local loop should usually use targeted parallel runs such as `mise exec -- just test tests.test_parser tests.test_typechecker` or `SPROUT_TESTS="tests.test_parser tests.test_typechecker" mise exec -- just test`; use `mise exec -- just test-serial` only when the task specifically requires serial/full-suite debugging earlier.
7. If sandbox or environment restrictions block the full suite, rerun it with escalated permissions rather than accepting partial verification.
8. The full suite passes.
9. Skipped tests are treated as a verification gap unless the user explicitly accepts that gap.
10. The changes are committed.
11. A self-review has been performed before handoff.

## Directory Conventions

- `docs/` normative and supporting design docs.
- `examples/` user-facing language examples.
- `sprout/` implementation source.
- `stdlib/` language-level standard library source (`prelude.sprout`).
- `mise.toml` toolchain definition.
- `justfile` standard developer tasks.

## Builtin vs Stdlib

1. Keep host-side builtins minimal and effect-oriented.
2. Prefer implementing pure helpers in `stdlib/prelude.sprout`.
3. When moving functionality from builtin to stdlib, add/adjust conformance tests.
4. Add a builtin only when the feature is impossible to implement in Sprout or cannot be implemented efficiently enough in Sprout with the current language/runtime surface.
5. If a feature could plausibly live in Sprout stdlib, discuss that tradeoff with the user before implementing it as a builtin.

## Commit Guidance

Use commit messages that explain intent, for example:
- `spec: define match exhaustiveness rules`
- `parser: add infix precedence for comparison operators`
- `types: improve error for mismatched function arguments`
