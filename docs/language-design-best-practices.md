# Language Design Best Practices (Research Notes)

This document captures practical language-design guidance from established language ecosystems and primary references, then translates each point into a Sprout action.

## 1) Define goals and non-goals explicitly

Why:
- Rust's RFC process requires motivation and drawbacks, which forces design clarity before implementation.

Sprout action:
- Every major design change should include: problem statement, goals, non-goals, alternatives, drawbacks.

Source:
- Rust RFC Book: <https://rust-lang.github.io/rfcs/>

## 2) Keep semantics precise and centralized

Why:
- The Go specification provides a single normative source of syntax and semantics. This reduces ambiguity and implementation drift.

Sprout action:
- Maintain a single normative spec for syntax, type rules, and evaluation order.
- Treat implementation notes as secondary to the spec.

Source:
- Go Language Specification: <https://go.dev/ref/spec>

## 3) Start small and iterate in slices

Why:
- Crafting Interpreters advocates building a minimal core language first, then layering features incrementally.

Sprout action:
- Ship a minimal v0 (expressions, functions, ADTs, match, inference) before adding advanced abstractions.
- Prefer vertical milestones (parse + typecheck + run) over large unfinished subsystems.

Source:
- Crafting Interpreters: <https://craftinginterpreters.com/>

## 4) Make the type system a usability feature, not a barrier

Why:
- Standard ML demonstrates practical static typing with inference, reducing annotation burden while preserving safety.

Sprout action:
- Default to inference-first APIs.
- Require annotations only where ambiguity hurts diagnostics or readability.

Source:
- Standard ML overview and type inference examples: <https://homepages.inf.ed.ac.uk/mfourman/teaching/mlCourse/notes/sml-basics.html>

## 5) Invest early in human-readable errors

Why:
- Elm's guidance and tooling philosophy emphasize clear, actionable compiler feedback for everyday developers.

Sprout action:
- Compiler errors should include:
  - what failed,
  - where it failed,
  - a likely fix.
- Prefer one clear diagnostic over many cascading ones.

Source:
- Elm Guide (compiler/helpful workflow orientation): <https://guide.elm-lang.org/>

## 6) Build a conformance suite from day one

Why:
- Test262 shows how a shared conformance suite keeps implementations aligned with spec behavior over time.

Sprout action:
- Add spec-driven tests for parser behavior, type errors, and runtime semantics.
- Any semantics change should come with spec updates and test updates in the same PR.

Source:
- Test262 project: <https://github.com/tc39/test262>

## Working Rules for Sprout

1. Spec first, then implementation.
2. Deterministic behavior beats clever behavior.
3. New syntax must justify learning cost.
4. Features ship with diagnostics and tests.
5. Prefer explicit extension points over hidden magic.
6. Keep memory management abstract in core-language design docs unless the proposal intentionally adds visible ownership, lifetime, destructor, or allocator semantics.
