# AGENTS.md

This file defines project-local working rules for humans and coding agents contributing to Sprout.

## Project Intent

Sprout is a statically typed, functional-first language aimed at strong safety with beginner-friendly ergonomics.

Primary design references:
- `docs/spec-v0.md`
- `docs/idiomatic-sprout.md` (**start here to write Sprout** — the idiomatic shapes: `let..else`, combinators, pipes, `wrap`)
- `docs/style-guide-v0.md`
- `docs/language-design-v0.md`
- `docs/language-design-best-practices.md`
- `docs/guidelines.md` (code authoring guidelines for stdlib and compiler)

## Definition of Ready

Before starting to write code, ensure the following are true. These are entry conditions for implementation, not exit conditions.

1. **Design alignment**: the change has been designed at a high level and approved by the user when required (see "Design Change Process").
2. **New features**: a failing test exists and has been confirmed to fail (TDD). *Practice: see "Code and Testing" §What tests to write #1.*
3. **Bug fixes**: a regression test exists that reproduces the defect (it will fail until the fix lands). *Practice: see "Code and Testing" §What tests to write #2.*
4. **Edits to files with coverage gaps**: at least one new test has been drafted that closes a gap in that file. *Practice: see "Code and Testing" §What tests to write #3.*

## Definition of Done

For coding tasks, work is done only when **all applicable** items below are true.

1. The implementation is complete.
2. The tests drafted under Definition of Ready (failing tests, regression tests, coverage-gap tests) now pass.
3. Relevant docs/spec updates are complete and in sync with the implementation.
4. `mise exec -- just fmt` has been run and any reformatted files staged, for any change that touches `.sprout` or `.spr` files.
5. The full test suite has been run via `mise exec -- just test` (no explicit test filter) for any change to **language semantics, `stdlib/`, builtins, runtime behavior, or the normative spec**.
6. `mise exec -- just compile-examples-stage1` passes (or the failing examples exactly match the pre-existing known-broken set). Run after every change that touches `stdlib/`, the runtime, or any example file.
7. **Compiler-source changes** (any edit under `stdlib/compiler/`) — smoke shapes: each shape in `tests/smoke_shapes/*.spr` emits IR cleanly via `compile_driver_bin_stage1 --emit-ir`, the IR contains at least one `define` block, and contains no `str_concat(ptr null,…)` occurrence (null-ptr codegen regression guard).
8. **Compiler-source changes** (any edit under `stdlib/compiler/`) — bundle smoke: `compile_driver_bin_stage1 --phase bundle` on `stdlib/compiler/token.sprout`, `stdlib/compiler/ast.sprout`, and `stdlib/prelude.sprout` produces non-empty output containing no dot-prefix qualified names (lines beginning with `.`).
9. **Compiler-source changes** (any edit under `stdlib/compiler/`) — bootstrap seed: run `just refresh-seed` and stage the updated `bootstrap/compile_driver.ll`. CI's `just verify-bootstrap-fixed-point` gates on this; a stale seed blocks all CI gates. Use the 2-step bootstrap if the committed seed predates a parser change (see [docs/debugging.md §2-Step Bootstrap Protocol](docs/debugging.md#2-step-bootstrap-protocol)).
10. **Runtime changes** (any edit to `runtime/sprout_runtime.c`) — APPROVED_BUILTINS: every newly-added `long long <name>(…)` function is also listed in `runtime/APPROVED_BUILTINS` with an inline justification explaining why the operation cannot be done in Sprout. Per "Builtin vs Stdlib" rules 4–6.
11. **Bootstrap/runtime changes** (any edit under `bootstrap/` or to `runtime/sprout_runtime.c`) — example canary: `examples/tuples.sprout`, `examples/factorial.sprout`, `examples/maybe_map.sprout`, `examples/typeclass_collections_demo.sprout`, `examples/fizzbuzz.sprout` each compile *and run* to completion without crash. (`just compile-examples-stage1` only covers compile; running these is currently a manual gate until CI covers it.)
12. The changes are committed.
13. A self-review has been performed before handoff.

**Verification notes:**
- During implementation, run individual test files for fast feedback (see "Code and Testing" §How to run tests); `mise exec -- just test` is the full gate required for #5.
- Docs/examples-only changes may skip the full suite when they do not modify `stdlib/`, test expectations, or the normative spec, but must still be verified in a way that matches the change.

## Commit Guidance

Use commit messages that explain intent:
- `spec: define match exhaustiveness rules`
- `parser: add infix precedence for comparison operators`
- `types: improve error for mismatched function arguments`

**Seed gate** — `scripts/seed_gate.sh`, wired as a PreToolUse Bash hook. Intercepts `git commit` and blocks if `stdlib/compiler/*.sprout` or `stdlib/*.sprout` is staged without a refreshed `bootstrap/compile_driver.ll`. Bypass (when IR is genuinely unchanged): run `just verify-bootstrap-fixed-point` then `just seed-fp-ack`.

> **Caveat — a new prelude `extern fn` is NOT an IR-unchanged edit.** `ir_lowering.lower_extern_decls` emits a `declare` for *every* bundled prelude extern, and `compile_driver` bundles the prelude, so adding one `extern fn` to `stdlib/prelude.sprout` adds one `declare` line to `bootstrap/compile_driver.ll`. `verify-bootstrap-fixed-point` will break; use a full `just refresh-seed` (delete the stale stage-1 binary first), **not** the `seed-fp-ack` bypass — even though `stdlib/compiler/` was untouched. No 2-step bootstrap is needed (no parser/compiler-source change; the seed diff is purely the additive declare line).

Workflow:
1. Do the work. Run all applicable DoD checks (tests, smoke-shapes, etc.).
2. Commit with `git commit …`. The seed gate blocks if seed is stale.

## Collaboration Rules

1. Keep changes small and reviewable.
2. Do not mix unrelated refactors with language-semantics changes.
3. Update docs and tests in the same change when behavior changes.
4. Use repository-managed tools via `mise` and `just` (avoid ad-hoc global tool versions).
5. Before making any non-trivial change, present a short high-level implementation overview and wait for user approval.
6. Explicitly call out any proposal to add a new builtin or to keep functionality in the host runtime instead of implementing it in Sprout; builtin/runtime additions require user approval up front.
7. Prefer fixing root-cause issues over introducing workarounds when the root cause is reasonably tractable.
8. When editing Sprout source examples, stdlib code, or user-facing snippets, follow `docs/style-guide-v0.md`; adhere to `docs/guidelines.md` for stdlib/compiler code; deviations require justification.

## Docs & Spec

1. Keep `README.md`, `docs/spec-v0.md`, and relevant `docs/*.md` aligned with current behavior after every feature or semantics change.
2. If a task listed in roadmap/TODO sections is completed, update or remove it in the same change.
3. If new follow-up work is discovered during implementation, add it to the appropriate roadmap/TODO section with concise scope.
4. `docs/spec-v0.md` is the normative source of truth for the stable Sprout core; supporting design docs explain rationale and tradeoffs but do not override it.
5. If a change alters syntax, semantics, typing rules, evaluation order, visibility/export rules, or diagnostics expectations, update the relevant spec/docs before considering the task complete.

## Agent Memory Discipline

Coding agents keep a private auto-memory outside the repo. It is a cache of **non-repo continuity**, not an archive — keep it small and evictable. The repo is the durable record; memory is not.

1. **Write gate.** Before saving a memory, route repo-appropriate content to the repo instead:
   - Design rationale, goals/non-goals, prior-art surveys, API/semantics decisions → a `docs/<feature>-v0.md` design doc.
   - Deferred or newly-discovered follow-up work → `BACKLOG.md` (the single canonical backlog).
   - Anything a PR reviewer would want to see, or that git history / a design doc / `BACKLOG.md` / code+comments already records → the repo. Write the repo artifact and stop; do not also mirror it into memory.
2. **Lifecycle (the anti-bloat rule).** A `project`-type memory is transient in-flight scaffolding, valid only while its work is unlanded. **When the work lands (merged/committed) and its durable facts are in git + docs + `BACKLOG.md`, delete the memory or collapse it to a one-line pointer — as part of landing, not a later sweep.** "Landed" is the death trigger. This is what removes the need for periodic index cleanups.
3. **What memory is legitimately for.** Working-style feedback, cross-repo/workflow lessons that touch no repo file, and session/branch continuity. These are stable and few — they are not the bloat. If unsure whether an item is memory- or repo-worthy, it goes in the repo.

## Design Change Process

For any non-trivial language change, include:

1. Problem statement.
2. Goals and non-goals.
3. **Prior-art survey.** When the decision is a choice among established alternatives — a
   semantics, diagnostics, or policy question comparable languages have also faced — present
   a brief survey of how state-of-the-art languages handle it: the options they took and the
   consensus or notable divergences, so the choice is grounded in prior art rather than
   invented. Keep it short (a handful of languages); every claim must be verified against a
   primary source (language reference/spec) — do not present an unconfirmed survey row or
   hedge it with a confidence label. Present this *with* the decision, before asking for a call.
4. High-level implementation overview for approval before editing.
5. Syntax and semantics impact.
6. Type-system impact.
7. Error-message impact.
8. Compatibility/migration notes.
9. Tests added/updated.
10. Spec/docs updated, with the normative vs experimental status made explicit.

## Code and Testing

### What tests to write

1. **TDD for new features and language changes** *(produces the artifact required by Definition of Ready #2)*. Write the failing test(s) *before* touching implementation code. Confirm the test fails for the right reason (e.g. wrong output, not a crash or import error), then implement until it passes. Do not mark a feature task in-progress without at least one failing test already committed or staged.
2. **Regression test for every bug fix** *(produces the artifact required by Definition of Ready #3)*. Before patching the root cause, add a test that reproduces the defect and fails on the unfixed code. The test must pass after the fix. This is non-negotiable — a bug fix without a regression test is considered incomplete.
3. **Coverage improvement when touching a file with gaps** *(produces the artifact required by Definition of Ready #4)*. Whenever you edit a file that has untested branches, untested error paths, or untested edge cases, add at least one new test that closes a coverage gap in that file. You are not required to achieve full coverage in a single pass, but you must leave coverage better than you found it.
4. Parser changes need parser tests.
5. Typechecker changes need both success and failure tests.
6. Runtime/semantic changes need executable behavior tests.
7. Spec-affecting changes should also add or update conformance coverage where practical.

### How to run tests

8. Preferred execution path: `mise exec -- just <task>`
9. For fast iteration during development, run a single test file directly:
   `./build/compile_driver_bin_stage1 --emit-ir stdlib_root tests/stdlib/test_foo.spr | clang - runtime/sprout_runtime.c -o /tmp/t && /tmp/t`
   The full gate is `mise exec -- just test` (required by Definition of Done #5).

## Directory Conventions

- `docs/` normative and supporting design docs.
- `examples/` user-facing language examples.
- `stdlib/` language-level standard library source (`prelude.sprout`).
- `stdlib/compiler/` self-hosted compiler source (`parse`, `infer`, `codegen`, `compile_driver`, etc.).
- `runtime/` C runtime and GC (`sprout_runtime.c`).
- `tests/stdlib/` native Sprout test files (`.spr`); run via `just test`.
- `tests/conformance/` executable language behavior fixtures.
- `bootstrap/` committed LLVM IR seed (`compile_driver.ll`) for stage-1 bootstrap.
- `mise.toml` toolchain definition.
- `justfile` standard developer tasks.

## Builtin vs Stdlib

1. Keep host-side builtins minimal and effect-oriented.
2. Prefer implementing pure helpers in `stdlib/prelude.sprout`.
3. When moving functionality from builtin to stdlib, add/adjust conformance tests.
4. Add a builtin only when the feature is impossible to implement in Sprout or cannot be implemented efficiently enough in Sprout with the current language/runtime surface. **Before proposing a new builtin, exhaust alternatives:** can existing `term_write`/`term_read_line`/`process.proc_run` compose the behaviour? Can a workaround (e.g. single-line-body assumption for LSP) eliminate the need? If the answer is yes, use the workaround and add a comment explaining the constraint.
5. If a feature could plausibly live in Sprout stdlib, discuss that tradeoff with the user before implementing it as a builtin.
6. Performance is **not** sufficient justification for a builtin unless there is a concrete, measured bottleneck. Correctness requirements (e.g. raw byte I/O that `term_read_line` cannot express) are sufficient.

## Compiler Internals and Debugging Tools

- **Before editing `stdlib/compiler/` or `runtime/`:** read [docs/compiler-internals.md](docs/compiler-internals.md) for GC ABI invariants, type-aware rooting rules, and the GC safety linter.
- **When something is broken:** see [docs/debugging.md](docs/debugging.md) for diagnostic phases (`--phase`), the 2-step bootstrap protocol (parser-change catch-22), and `just llvm-where <ll_file> <line>` (maps an `opt --passes=verify` error line to its enclosing Sprout function).

## Known Limitations

See [README.md §Not Yet Supported](./README.md#not-yet-supported-common-gotchas) for current syntax and naming gotchas (e.g. the word `not` is not an operator — use the `!` prefix), along with the idiomatic form for each. (Prefix `!` and `-` negation and effectful list iteration via `list_each`/`list_fold` all work now; a `let..in` block — including refutable `<pat> = <e> else <fb>` bindings — works as a pure function body, e.g. `tests/stdlib/test_let_else.spr`.)

## Pull Requests (GitHub)

The remote is **GitHub** (`github.com/cthulhu666/sprout-lang`). Land work via a feature branch + PR to `master`.

**`master` is branch-protected — every change goes through a PR, including docs.** Direct pushes to `master` are rejected for everyone (the rules are enforced for admins, and all pushers authenticate as the repo owner). The protection is:

- Require a PR before merging (0 required approvals, so you can self-merge your own PR).
- Require the `test` status check (the CI `test` job) to pass.
- Require the branch to be up to date with `master` before merging (strict).
- Require linear history; merges are **rebase-only** (merge-commit and squash are disabled).
- Force-pushes and branch deletion on `master` are blocked.

The standard loop:

```
git switch -c my-change
# ... edit, commit ...
git push -u origin my-change
gh pr create --base master --fill
gh pr merge --auto --rebase        # lands automatically when CI is green + up-to-date
```

- **`gh` is managed by mise** (`gh` in `mise.toml`); run `gh auth login` once if unauthenticated. Sprout is developed in git worktrees; `gh` reads the repo through the git CLI, so it works from a worktree (unlike the former Gitea `tea` client). Auto-merge is enabled and merged branches are auto-deleted.
- **CI runs on GitHub-hosted runners** (`.github/workflows/ci.yml`, `runs-on: ubuntu-latest`) — there is no self-hosted worker to provision or dispatch. Releases (`.github/workflows/release.yml`) build linux x86_64 + aarch64 artifacts on tag push and publish via `softprops/action-gh-release`.
- **Seed-staleness merge cascade.** Because the branch must be up to date before merging, when `master` moves under an open PR that touches `stdlib/compiler/` you must rebase onto the new `master` **and** regenerate `bootstrap/compile_driver.ll` (`just refresh-seed`) before the merge unblocks — the up-to-date rule turns this into a pre-merge gate rather than a post-merge surprise.
