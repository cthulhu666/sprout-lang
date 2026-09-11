# AGENTS.md

This file defines project-local working rules for humans and coding agents contributing to Sprout.

## Project Intent

Sprout is a statically typed, functional-first language aimed at strong safety with beginner-friendly ergonomics.

Primary design references: `docs/spec-v0.md` (normative), `docs/idiomatic-sprout.md`
(**start here to write Sprout** — `let..else`, combinators, pipes, `wrap`), `docs/style-guide-v0.md`,
`docs/language-design-v0.md`, `docs/language-design-best-practices.md`, and `docs/guidelines.md`
(authoring guidelines for stdlib and compiler code).

## Definition of Ready

Before starting to write code, ensure the following are true. These are entry conditions for implementation, not exit conditions.

1. **Design alignment**: the change has been designed at a high level and approved by the user when required (see "Design Change Process").
2. **New features**: a failing test exists and has been confirmed to fail (TDD). *Practice: see "Code and Testing" §What tests to write #1.*
3. **Bug fixes**: a regression test exists that reproduces the defect (it will fail until the fix lands). *Practice: see "Code and Testing" §What tests to write #2.*
4. **Edits to files with coverage gaps**: at least one new test has been drafted that closes a gap in that file. *Practice: see "Code and Testing" §What tests to write #3.*

## Definition of Done

For coding tasks, work is done only when **all applicable** items below are true.

**Run #14's self-review before #9 (reseed) and #12 (golden IR).** A finding after a reseed
invalidates it and costs that cycle twice. The review gate is a Stop hook — it fires after the
gates, so it cannot schedule this for you.

1. The implementation is complete.
2. The tests drafted under Definition of Ready (failing tests, regression tests, coverage-gap tests) now pass.
3. Relevant docs/spec updates are complete and in sync with the implementation.
4. `mise exec -- just fmt` has been run and any reformatted files staged, for any change that touches `.sprout` or `.spr` files.
5. The full test suite has been run via `mise exec -- just test` (no explicit test filter) for any change to **language semantics, `stdlib/`, builtins, runtime behavior, or the normative spec**.
6. `mise exec -- just compile-examples-stage1` passes (or the failing examples exactly match the pre-existing known-broken set). Run after every change that touches `stdlib/`, the runtime, or any example file.
7. **Compiler-source changes** (any edit under `stdlib/compiler/`) — smoke shapes: every `tests/smoke_shapes/*.spr` emits IR cleanly via `compile_driver_bin_stage1 --emit-ir`, with at least one `define` block and no `str_concat(ptr null,…)` (null-ptr codegen regression guard).
8. **Compiler-source changes** — bundle smoke: `compile_driver_bin_stage1 --phase bundle` on `stdlib/compiler/token.sprout`, `stdlib/compiler/ast.sprout` and `stdlib/prelude.sprout` produces non-empty output containing no dot-prefixed qualified names (lines beginning with `.`).
9. **Compiler-source changes** — bootstrap seed: run `just refresh-seed` and stage the updated `bootstrap/compile_driver.ll`. A stale seed blocks every CI gate (`just verify-bootstrap-fixed-point`). Use the 2-step bootstrap if the committed seed predates a parser change ([docs/debugging.md §2-Step Bootstrap Protocol](docs/debugging.md#2-step-bootstrap-protocol)).
10. **Runtime changes** (any edit to `runtime/sprout_runtime.c`) — APPROVED_BUILTINS: every newly-added `long long <name>(…)` function is also listed in `runtime/APPROVED_BUILTINS` with an inline justification for why the operation cannot be done in Sprout (per "Builtin vs Stdlib" 4–6).
11. **Bootstrap/runtime changes** (any edit under `bootstrap/` or to `runtime/sprout_runtime.c`) — example canary: `just run-example-canary` compiles *and runs* five examples, which `just compile-examples-stage1` does not. It is part of `just ci-fast-gates`, so running that aggregate satisfies this item ([docs/gates.md](docs/gates.md)).
12. **Codegen-affecting changes** (anything that can alter emitted IR — `stdlib/compiler/`, `stdlib/`, the prelude, a module's public name surface) **and any change that ADDS a file to `examples/` or `tests/smoke_shapes/`** — golden IR: `just ir-golden-diff` passes. For a compiler-source edit, `just refresh-seed` **first** or the gate runs the pre-edit binary and proves nothing. If it reports diffs, read them before regenerating — the report is truncated, so run `just ir-golden-snapshot` and read the complete `git diff tests/golden/ir` before staging. Regenerating an unread diff launders a regression into an "expected" snapshot, the one way this gate is defeated. Details and the traps: [docs/gates.md §Golden IR](docs/gates.md). Also run `just o2-codegen-smoke` (in `just ci-fast-gates`): every other test path links IR at `-O0`, so a backend failure only `-O2` reaches is invisible to `just test`.
13. The changes are committed.
14. A self-review has been performed against the review-gate checklist (§Commit Guidance) — early, per the note above, and again before handoff.

**Verification notes:**
- During implementation, run individual test files for fast feedback (§Code and Testing); `mise exec -- just test` is the full gate required for #5.
- **Changes to `runtime/`, the scheduler, `stdlib/net.sprout` or `stdlib/http_server.sprout`: run `mise exec -- just linux-smoke` before pushing.** Local gates run kqueue while CI runs epoll + timerfd, and the two diverge in ways unreachable on macOS ([docs/gates.md](docs/gates.md)). A recommendation, not a Definition of Done item, because it needs a container runtime.
- Docs/examples-only changes may skip the full suite when they do not modify `stdlib/`, test expectations or the normative spec, but must still be verified in a way that matches the change.

## Commit Guidance

Use commit messages that explain intent:
- `spec: define match exhaustiveness rules`
- `parser: add infix precedence for comparison operators`
- `types: improve error for mismatched function arguments`

**Review gate** — `scripts/review_gate.py`, a Stop hook. Once per distinct working-tree state it
refuses the turn and prints a path-aware checklist: idiomatic Sprout for `.sprout`/`.spr`,
`docs/guidelines.md` for `stdlib/`, GC/rooting for `stdlib/compiler/` and `runtime/`, docs+spec sync
always. Generated artifacts (the seed, `tests/golden/ir/`, `build/`, `.claude/`) are invisible to it,
so a reseed or a golden snapshot never trips it. Test with `just test-review-gate`.

**Seed gate** — `scripts/seed_gate.sh`, a PreToolUse Bash hook. Intercepts `git commit` and blocks if
`stdlib/compiler/*.sprout` or `stdlib/*.sprout` is staged without a refreshed
`bootstrap/compile_driver.ll`. Bypass when the IR is genuinely unchanged: `just
verify-bootstrap-fixed-point`, then `just seed-fp-ack` as its own step with nothing touching the
index before the commit. A new prelude `extern fn` is **not** an IR-unchanged edit — reseed fully.

Both hooks scope to the worktree named by the session's `cwd`. Budgets, failure modes and the
`seed-stale`-vs-CI distinction: [docs/gates.md](docs/gates.md).

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
2. If a task listed in roadmap/TODO sections is completed, **remove** it in the same change — see "Backlog Discipline".
3. If new follow-up work is discovered during implementation, add it to the appropriate roadmap/TODO section with concise scope, under the shape rule in "Backlog Discipline".
4. `docs/spec-v0.md` is the normative source of truth for the stable Sprout core; supporting design docs explain rationale and tradeoffs but do not override it.
5. If a change alters syntax, semantics, typing rules, evaluation order, visibility/export rules, or diagnostics expectations, update the relevant spec/docs before considering the task complete.

## Backlog Discipline

`BACKLOG.md` is the single canonical backlog. It is a list of **open work**, not a record of
finished work — git, the design docs and `docs/spec-v0.md` are the record.

1. **Shape.** Wrap at **100 columns**; an entry is **at most 10 lines** — a bold title, then what is
   broken, where, and why it matters. Anything longer is a design doc: write `docs/<feature>-v0.md`
   and link it, and keep measurements, prior-art surveys and rejected alternatives there.
   `just backlog-shape` checks both halves, since a line budget means nothing while one line can
   hold a paragraph. 10 is the existing p90, not an aspiration — a gate that is red on arrival gets
   switched off.
2. **Death trigger (the anti-bloat rule).** When the work lands, delete the entry as part of
   landing. Its durable content moves to the design doc it names, or to the spec; a lesson about
   *process* rather than the feature goes in this file. No `[x]` entries — the gate rejects one —
   and no "original report follows" block, because git has it.
3. **Before filing, grep for it.** A duplicate filing is the failure this discipline exists to
   prevent, and it has happened at least three times. A bold title makes the grep work — write one.
4. **Open work does not hide inside a closed entry.** A "still open" or "remaining" bullet under a
   `[x]` item is invisible to anyone scanning for todos. Promote it to its own `[ ]` entry, or it
   does not exist.

## Agent Memory Discipline

Coding agents keep a private auto-memory outside the repo. It is a cache of **non-repo continuity**, not an archive — keep it small and evictable. The repo is the durable record; memory is not.

1. **Write gate.** Route repo-appropriate content to the repo instead: design rationale, goals/non-goals, prior-art surveys and API/semantics decisions to a `docs/<feature>-v0.md`; deferred or newly-discovered work to `BACKLOG.md`; anything a PR reviewer would want to see, or that git, a design doc or code+comments already records, to the repo. Write the repo artifact and stop — do not also mirror it into memory.
2. **Lifecycle (the anti-bloat rule).** A `project`-type memory is transient in-flight scaffolding, valid only while its work is unlanded. **When the work lands (merged/committed) and its durable facts are in git + docs + `BACKLOG.md`, delete the memory or collapse it to a one-line pointer — as part of landing, not a later sweep.** "Landed" is the death trigger. This is what removes the need for periodic index cleanups.
3. **What memory is legitimately for.** Working-style feedback, cross-repo/workflow lessons that touch no repo file, and session/branch continuity. These are stable and few — they are not the bloat. If unsure whether an item is memory- or repo-worthy, it goes in the repo.

## Design Change Process

For any non-trivial language change, present: the problem statement; goals and non-goals; a
prior-art survey; a high-level implementation overview for approval **before editing**; the syntax
and semantics impact, type-system impact and error-message impact; compatibility and migration
notes; tests added or updated; and the spec/docs update, with normative vs experimental status made
explicit.

**Prior-art survey** — when the decision is a choice among established alternatives that comparable
languages have also faced, show briefly how a handful of state-of-the-art languages handle it and
where they diverge, so the choice is grounded rather than invented. Every claim must be verified
against a primary source (language reference or spec) — do not present an unconfirmed row or hedge
it with a confidence label. Present this *with* the decision, before asking for a call.

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
9. Fast single-file iteration — three details are easy to get wrong, see
   [docs/debugging.md §Running one test file](docs/debugging.md#running-one-test-file):
   `./build/compile_driver_bin_stage1 --emit-ir stdlib tests/stdlib/test_foo.spr > /tmp/t.ll && clang /tmp/t.ll runtime/*.c -O2 -o /tmp/t && /tmp/t`
   - the root argument is the literal path `stdlib`, not the justfile variable's name;
   - the runtime is all three `runtime/*.c`; on macOS add `-framework Security -framework CoreFoundation`;
   - a test importing `testsupport.*` needs `--package-root <repo-root>`, which `just test-file` does **not** pass.
   The full gate is `mise exec -- just test` (Definition of Done #5).

## Directory Conventions

- `docs/` normative and supporting design docs.
- `examples/` user-facing language examples.
- `stdlib/` language-level standard library source (`prelude.sprout`).
- `stdlib/compiler/` self-hosted compiler source (`parser`, `infer`, `ast_to_ir`, `ir_lowering`, `compile_driver`, etc.).
- `runtime/` C runtime, GC, poller and scheduler (`sprout_runtime.c`, `sprout_poll.c`, `sprout_scheduler.c`); link all three.
- `tests/stdlib/` native Sprout test files (`.spr`, run via `just test`); `tests/conformance/` executable language behavior fixtures.
- `bootstrap/` committed LLVM IR seed (`compile_driver.ll`) for stage-1 bootstrap.
- `mise.toml` toolchain definition; `justfile` standard developer tasks.

## Builtin vs Stdlib

1. Keep host-side builtins minimal and effect-oriented.
2. Prefer implementing pure helpers in `stdlib/prelude.sprout`.
3. When moving functionality from builtin to stdlib, add/adjust conformance tests.
4. Add a builtin only when the feature is impossible to implement in Sprout or cannot be implemented efficiently enough in Sprout with the current language/runtime surface. **Before proposing a new builtin, exhaust alternatives:** can existing `term_write`/`term_read_line`/`process.proc_run` compose the behaviour? Can a workaround (e.g. single-line-body assumption for LSP) eliminate the need? If the answer is yes, use the workaround and add a comment explaining the constraint.
5. If a feature could plausibly live in Sprout stdlib, discuss that tradeoff with the user before implementing it as a builtin.
6. Performance is **not** sufficient justification for a builtin unless there is a concrete, measured bottleneck. Correctness requirements (e.g. raw byte I/O that `term_read_line` cannot express) are sufficient.

## Compiler Internals and Debugging Tools

- **Before editing `stdlib/compiler/` or `runtime/`:** read [docs/compiler-internals.md](docs/compiler-internals.md) for GC ABI invariants, type-aware rooting rules, and the GC safety linter.
- **When a gate fires, or you are about to override or change one:** [docs/gates.md](docs/gates.md) — what each gate checks, how it has been defeated, and the evidence.
- **When something is broken:** see [docs/debugging.md](docs/debugging.md) for diagnostic phases (`--phase`), the 2-step bootstrap protocol (parser-change catch-22), and `just llvm-where <ll_file> <line>` (maps an `opt --passes=verify` error line to its enclosing Sprout function).

## Known Limitations

See [README.md §Not Yet Supported](./README.md#not-yet-supported-common-gotchas) for current syntax and naming gotchas (e.g. the word `not` is not an operator — use the `!` prefix), along with the idiomatic form for each. (Prefix `!` and `-` negation and effectful list iteration via `list_each`/`list_fold` all work now; a `let..in` block — including refutable `<pat> = <e> else <fb>` bindings — works as a pure function body, e.g. `tests/stdlib/test_let_else.spr`.)

## Pull Requests (GitHub)

The remote is **GitHub** (`github.com/cthulhu666/sprout-lang`). Land work via a feature branch + PR
to `master`, which is branch-protected: every change goes through a PR, including docs, and direct
pushes are rejected for everyone. The protection requires a PR (0 approvals, so you can self-merge),
the `test` status check, the branch to be up to date (strict) and linear history (rebase-only); it
blocks force-pushes and deletion of `master`.

```
git switch -c my-change
# ... edit, commit ...
git push -u origin my-change
gh pr create --base master --fill
# stop here: leave the PR open. Merging is Kuba's call.
```

**Agents must not enable auto-merge** — no `--auto`, no API or web toggle. An agent's job ends at
"PR open, CI running"; a queued merge lands the change later, unwatched. Merge only when asked for
that PR, in that turn (`gh pr merge <n> --rebase`), once CI is green on the current head.

- **`gh` is managed by mise**; run `gh auth login` once if unauthenticated. It reads the repo through the git CLI, so it works from a worktree. Merged branches are auto-deleted.
- **CI runs on GitHub-hosted runners** (`.github/workflows/ci.yml`, `ubuntu-latest`) — no self-hosted worker to provision. Releases (`.github/workflows/release.yml`) build linux x86_64 + aarch64 on tag push.
- **Seed-staleness merge cascade.** The up-to-date rule means that when `master` moves under an open PR touching `stdlib/compiler/`, you must rebase **and** `just refresh-seed` before the merge unblocks — a pre-merge gate rather than a post-merge surprise.
