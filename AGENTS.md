# AGENTS.md

This file defines project-local working rules for humans and coding agents contributing to Sprout.

## Project Intent

Sprout is a statically typed, functional-first language aimed at strong safety with beginner-friendly ergonomics.

Primary design references:
- `docs/spec-v0.md`
- `docs/style-guide-v0.md`
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
10. When editing Sprout source examples, stdlib code, or user-facing snippets, follow `docs/style-guide-v0.md` unless a stronger file-local convention already exists.

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
2. `docs/style-guide-v0.md` is the default non-normative source-style guide for repository Sprout code and code snippets.
3. `README.md` should summarize current capabilities, but must not silently contradict the normative spec.
4. Supporting design docs should explain rationale and tradeoffs; they do not override the normative spec.
5. Experimental or prototype-only features must be labeled consistently across `README.md`, `docs/*.md`, examples, and tests.
6. Do not widen the language contract implicitly through examples, stdlib surface area, implementation, or style guidance.
7. If a change alters syntax, semantics, typing rules, evaluation order, visibility/export rules, or diagnostics expectations, update the relevant spec/docs before considering the task complete.

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

### What tests to write

1. **TDD for new features and language changes.** Write the failing test(s) *before* touching implementation code. Confirm the test fails for the right reason (e.g. wrong output, not a crash or import error), then implement until it passes. Do not mark a feature task in-progress without at least one failing test already committed or staged.
2. **Regression test for every bug fix.** Before patching the root cause, add a test that reproduces the defect and fails on the unfixed code. The test must pass after the fix. This is non-negotiable — a bug fix without a regression test is considered incomplete.
3. **Coverage improvement when touching a file with gaps.** Whenever you edit a file that has untested branches, untested error paths, or untested edge cases, add at least one new test that closes a coverage gap in that file. You are not required to achieve full coverage in a single pass, but you must leave coverage better than you found it.
4. Parser changes need parser tests.
5. Typechecker changes need both success and failure tests.
6. Runtime/semantic changes need executable behavior tests.
7. Keep diagnostics stable and understandable; avoid noisy cascades.
8. Spec-affecting changes should also add or update conformance coverage where practical.

### How to run tests

9. Preferred execution path for local commands:
   `mise exec -- just <task>`
10. For intermediate verification during development, prefer targeted parallel runs through `mise exec -- just test ...` or `SPROUT_TESTS="..." mise exec -- just test` before falling back to the full parallel gate; use serial `just test-serial` only when the task requires order-sensitive debugging.
11. Final verification uses the authoritative parallel full-suite run via `mise exec -- just test` with no explicit test filter for any change that modifies code, language semantics, stdlib behavior, builtins, runtime behavior, or the normative spec; use `mise exec -- just test-serial` only as a fallback when diagnosing runner discrepancies or order-sensitive failures.
12. Docs-only or examples-only changes may use targeted verification instead of the full suite when they do not modify `sprout/`, `stdlib/`, test expectations, or the normative spec; at minimum, verify the commands and examples you changed still work as documented.

## Definition of Done

For coding tasks, work is done only when:

1. The change has been designed at a high level and approved by the user when required.
2. **For new features:** failing test(s) were written and confirmed to fail before implementation began (TDD).
3. **For bug fixes:** a regression test that reproduced the defect exists and now passes.
4. **For any edit to a file with coverage gaps:** at least one new test was added that closes a gap in that file.
5. The implementation is complete.
6. Relevant docs/spec updates are complete and in sync with the implementation.
7. Formatting and linting have been run when applicable.
8. The entire test suite has been run via `mise exec -- just test` with no explicit test filter for any change that modifies code, language semantics, stdlib behavior, builtins, runtime behavior, or the normative spec.
9. During implementation, the faster local loop should usually use targeted parallel runs such as `mise exec -- just test tests.test_parser tests.test_typechecker` or `SPROUT_TESTS="tests.test_parser tests.test_typechecker" mise exec -- just test`; use `mise exec -- just test-serial` only when the task specifically requires serial/full-suite debugging earlier.
10. Docs-only or examples-only changes may skip the full suite when they do not modify `sprout/`, `stdlib/`, test expectations, or the normative spec, but they must still be verified in a way that matches the change, such as re-running documented commands or executing the updated examples.
11. After the test suite passes, `mise exec -- just compile-examples-stage1` must also pass (or the failing examples must exactly match the pre-existing known-broken set). Run this after every change that touches `stdlib/`, `sprout/`, the runtime, or any example file.
12. If sandbox or environment restrictions block required full-suite verification, rerun it with escalated permissions rather than accepting partial verification.
13. Required verification passes.
14. Skipped tests are treated as a verification gap unless the user explicitly accepts that gap.
15. The changes are committed.
16. A self-review has been performed before handoff.

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

## Bootstrap and Debugging Tools

### Diagnostic phases (`compile_driver_bin --phase ...`)

| Phase | What it does |
|---|---|
| `scan-info <stdlib> <file>` | Calls `bundler.scan_source_info` and prints `module:`, `export:`, `ctor:` lines. Diagnoses module-name extraction bugs without running the full bundler pipeline. |
| `dump-qualify <stdlib> <file>` | Runs full collection + qualify and prints original→qualified name mapping per module, plus `ctx: EMPTY` or `ctx: populated` for each. A `ctx: EMPTY` line means `build_resolve_ctx` failed to find the module's path in `all_symbols` — all names stay unqualified, triggering the `[assert]` error. |
| `bundle <stdlib> <file>` | Runs the full bundle phase and prints qualified decl names. Gold standard for detecting qualify-stage regressions; output must be identical between stage-0 and stage-1 (enforced by `tests/test_bootstrap_identity.py`). |

**If `bundle_file` returns `BundleErr("[assert] qualify_decl: FnDecl starts with '.'...")`:**
1. Run `--phase scan-info` to check `scan_source_info` returns a valid non-empty module name.
2. If the name looks correct, run `--phase dump-qualify` to see which module has `ctx: EMPTY`.

### GC Option C ABI — strings and chars travel as `i64`

**This is an intentional design choice, not a bug.** Do not "correct" it.

In the self-hosted codegen (`stdlib/compiler/codegen.sprout`), String and Char values are represented as `i64` at the LLVM IR level — a raw pointer cast to an integer. This is called **GC Option C** in the codebase.

**Why:** The GC's root-tracking table stores all heap roots as `i64` values. By making strings travel as `i64` throughout IR, every string slot is automatically compatible with the root table without needing `ptrtoint`/`inttoptr` casts at each GC-safe point. The alternative (keeping strings as `ptr`) requires constant coercions at every rooting call site, and historically caused subtle bugs where a `ptr` value bypassed the root table.

**Implications for codegen edits:**
- `const_to_ll("String")` and `const_to_ll("Char")` return `ll_i64()` — correct.
- `emit_expr` for `TString`/`TChar` nodes: emits a `str_ptr`, then coerces to `i64`.
- String comparisons in `emit_binary`: must coerce both operands back to `ptr` before calling `str_eq`/`str_compare`. Use `compare_needs_ptr_dispatch(left_ty, right_ty)` to detect this case.
- `str_concat`, `str_slice`, etc.: called with `i64` args, return `i64`.
- `string_const` uses `str_byte_len(s) + 1` (not `str_len`) for LLVM array sizing — `str_len` counts Unicode codepoints, `str_byte_len` counts UTF-8 bytes.

If you see `ll_ptr()` for String/Char in codegen, that is a regression. The canonical form is `ll_i64()`.

### GC safety in `sprout/cli.py`

`just gc-safety-check` lints the embedded C runtime for `const char*`/`char*` parameters live across `sprout_gc_maybe_collect_threshold()` calls (callers are expected to root heap values before such calls). Run this after editing any C builtin in `cli.py` that allocates heap strings. Use `just gc-safety-check --strict` to fail on any finding; the default mode warns only.

### Bootstrap binary rebuild protocol

1. Rebuild stage-0 (wraps Python compiler; OOM-risk — always use memwatch):
   ```
   scripts/memwatch.sh 4096 1 -- python3 -m sprout.cli compile \
     --with-stdlib --native -o compile_driver_bin \
     stdlib/compiler/compile_driver.sprout
   ```
2. Rebuild stage-1 from stage-0:
   ```
   just build-stage1
   ```
3. After rebuild, verify identity:
   ```
   mise exec -- just test tests.test_bootstrap_identity
   ```
   All corpus files are expected to pass. If a file is newly broken in stage-1, add it to `XFAIL_FILES` in `test_bootstrap_identity.py` with a comment explaining the regression, and remove it once fixed.

## Known Limitations

See [README.md §Not Yet Supported](./README.md#not-yet-supported-common-gotchas) for language features that are planned but not yet implemented (`!expr` negation, `let..in` in pure functions, effectful list iteration), along with the standard workaround for each.

## Commit Guidance

Use commit messages that explain intent, for example:
- `spec: define match exhaustiveness rules`
- `parser: add infix precedence for comparison operators`
- `types: improve error for mismatched function arguments`
