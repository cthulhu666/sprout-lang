# AGENTS.md

This file defines project-local working rules for humans and coding agents contributing to Sprout.

## Project Intent

Sprout is a statically typed, functional-first language aimed at strong safety with beginner-friendly ergonomics.

Primary design references:
- `docs/spec-v0.md`
- `docs/style-guide-v0.md`
- `docs/language-design-v0.md`
- `docs/language-design-best-practices.md`
- `docs/guidelines.md` (code authoring guidelines for stdlib and compiler)

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
11. Adhere to `docs/guidelines.md` when authoring stdlib or compiler code; deviations require justification in the PR description.

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

1. **TDD for new features and language changes** *(produces the artifact required by Definition of Ready #2)*. Write the failing test(s) *before* touching implementation code. Confirm the test fails for the right reason (e.g. wrong output, not a crash or import error), then implement until it passes. Do not mark a feature task in-progress without at least one failing test already committed or staged.
2. **Regression test for every bug fix** *(produces the artifact required by Definition of Ready #3)*. Before patching the root cause, add a test that reproduces the defect and fails on the unfixed code. The test must pass after the fix. This is non-negotiable — a bug fix without a regression test is considered incomplete.
3. **Coverage improvement when touching a file with gaps** *(produces the artifact required by Definition of Ready #4)*. Whenever you edit a file that has untested branches, untested error paths, or untested edge cases, add at least one new test that closes a coverage gap in that file. You are not required to achieve full coverage in a single pass, but you must leave coverage better than you found it.
4. Parser changes need parser tests.
5. Typechecker changes need both success and failure tests.
6. Runtime/semantic changes need executable behavior tests.
7. Keep diagnostics stable and understandable; avoid noisy cascades.
8. Spec-affecting changes should also add or update conformance coverage where practical.

### How to run tests

These are *how* to execute tests during development. The *rules* about when a particular execution counts as verification live in Definition of Done and Verification Policy below.

9. Preferred execution path for local commands:
   `mise exec -- just <task>`
10. For intermediate verification during development, run a single test file directly with `./compile_driver_bin_stage1 --emit-ir stdlib_root tests/stdlib/test_foo.spr | clang - runtime/sprout_runtime.c -o /tmp/t && /tmp/t`. The full gate is `mise exec -- just test` (required by Definition of Done #5 for code/semantics changes; exceptions in Verification Policy).

## Definition of Ready

Before starting to write code, ensure the following are true. These are entry conditions for implementation, not exit conditions.

1. **Design alignment**: the change has been designed at a high level and approved by the user when required (see "Design Change Process").
2. **New features**: a failing test exists and has been confirmed to fail (TDD). *Practice: see "Code and Testing Expectations §What tests to write" #1.*
3. **Bug fixes**: a regression test exists that reproduces the defect (it will fail until the fix lands). *Practice: see "Code and Testing Expectations §What tests to write" #2.*
4. **Edits to files with coverage gaps**: at least one new test has been drafted that closes a gap in that file. *Practice: see "Code and Testing Expectations §What tests to write" #3.*

## Definition of Done

For coding tasks, work is done only when **all applicable** items below are true.

1. The implementation is complete.
2. The tests drafted under Definition of Ready (failing tests, regression tests, coverage-gap tests) now pass.
3. Relevant docs/spec updates are complete and in sync with the implementation.
4. `mise exec -- just fmt` has been run and any reformatted files staged, for any change that touches `.sprout` or `.spr` files.
5. The entire test suite has been run via `mise exec -- just test` with no explicit test filter, for any change that modifies code, language semantics, stdlib behavior, builtins, runtime behavior, or the normative spec.
6. `mise exec -- just compile-examples-stage1` passes (or the failing examples exactly match the pre-existing known-broken set). Run after every change that touches `stdlib/`, the runtime, or any example file.
7. **Compiler-source changes** (any edit under `stdlib/compiler/`) — smoke shapes: each shape in `tests/smoke_shapes/*.spr` emits IR cleanly via `compile_driver_bin_stage1 --emit-ir`, the IR contains at least one `define` block, and contains no `str_concat(ptr null,…)` occurrence (null-ptr codegen regression guard).
8. **Compiler-source changes** (any edit under `stdlib/compiler/`) — bundle smoke: `compile_driver_bin_stage1 --phase bundle` on `stdlib/compiler/token.sprout`, `stdlib/compiler/ast.sprout`, and `stdlib/prelude.sprout` produces non-empty output containing no dot-prefix qualified names (lines beginning with `.`).
9. **Runtime changes** (any edit to `runtime/sprout_runtime.c`) — APPROVED_BUILTINS: every newly-added `long long <name>(…)` function is also listed in `runtime/APPROVED_BUILTINS` with an inline justification explaining why the operation cannot be done in Sprout. Per "Builtin vs Stdlib" rules 4–6.
10. **Bootstrap/runtime changes** (any edit under `bootstrap/` or to `runtime/sprout_runtime.c`) — example canary: `examples/tuples.sprout`, `examples/factorial.sprout`, `examples/maybe_map.sprout`, `examples/typeclass_collections_demo.sprout`, `examples/fizzbuzz.sprout` each compile *and run* to completion without crash. (`just compile-examples-stage1` only covers compile; running these is currently a manual gate until CI covers it.)
11. The changes are committed (via the DoD-ack workflow — see "Commit Guidance").
12. A self-review has been performed before handoff.

## Verification Policy

These rules govern *how* the verification gates in Definition of Done are run, including allowed exceptions and required contingencies.

- **Fast local loop, full gate at end**: during implementation, run the specific `.spr` test file directly for fast iteration (concrete command in "Code and Testing Expectations §How to run tests" #10); `mise exec -- just test` is the full gate that must pass before considering DoD #5 met.
- **Docs/examples-only exception**: docs-only or examples-only changes may skip the full suite when they do not modify `stdlib/`, test expectations, or the normative spec, but they must still be verified in a way that matches the change — re-running documented commands, executing updated examples, etc.
- **Sandbox contingency**: if sandbox or environment restrictions block required full-suite verification, rerun it with escalated permissions rather than accepting partial verification.
- **Skipped tests**: any test skipped during verification is treated as a verification gap unless the user explicitly accepts that gap.

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

## Bootstrap and Debugging Tools

### Diagnostic phases (`compile_driver_bin --phase ...`)

| Phase | What it does |
|---|---|
| `scan-info <stdlib> <file>` | Calls `bundler.scan_source_info` and prints `module:`, `export:`, `ctor:` lines. Diagnoses module-name extraction bugs without running the full bundler pipeline. |
| `dump-qualify <stdlib> <file>` | Runs full collection + qualify and prints original→qualified name mapping per module, plus `ctx: EMPTY` or `ctx: populated` for each. A `ctx: EMPTY` line means `build_resolve_ctx` failed to find the module's path in `all_symbols` — all names stay unqualified, triggering the `[assert]` error. |
| `bundle <stdlib> <file>` | Runs the full bundle phase and prints qualified decl names. Gold standard for detecting qualify-stage regressions; output must be identical between stage-0 and stage-1 (verified by `tests/stdlib/test_bundler.spr`). |

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

### Type-aware GC rooting — `push_temp_root_typed` (codegen)

**This is an intentional design choice, not an oversight.** Do not "simplify" call sites of `push_temp_root_typed` back to the older `push_temp_root`.

In `stdlib/compiler/codegen.sprout`, two rooting helpers coexist:

- `push_temp_root(v, em)` — looks only at the LLVM-level type (`val_is_i64`, `val_is_ptr`, `val_is_tuple`). Pushes a root for any i64-typed value because in LLVM, both `Int` and boxed ADT handles are `i64`.
- `push_temp_root_typed(v, ty, em)` — additionally consults the **source-level Sprout type**. Skips the push entirely when `ty` is a known non-heap scalar (`Int`, `Bool`, `Char` — see `type_is_non_heap_scalar`). For all other types (including `TVar`, `TApp`, ADT names, polymorphic vars), falls back to `push_temp_root` (conservative).

**Why:** before this distinction, every call site of an `Int`-returning expression emitted `alloca i64; store; call sprout_gc_push_i64_root; … call sprout_gc_pop_roots(1)` for nothing — Int cannot ever be a heap pointer. Profiling N-queens showed **67% of CPU time** was in `sprout_gc_push_i64_root`/`sprout_gc_pop_roots`, of which ~50% was pure waste from Int args. Type-aware rooting eliminated that waste and delivered a measured **1.5–2.7× speedup on N-queens** (N=12 from ~1.5 s to 928 ms).

**Hot-path call sites that must use `push_temp_root_typed`:**

- `emit_args_with_roots` and `emit_args_with_roots_lls` (function call argument rooting)
- `emit_tco_args` (tail-call argument rooting)
- `emit_tuple_items` (tuple-construction item rooting)
- `emit_do` `TDoLetStep` (do-block let binding rooting)
- `build_param_locals_and_push_roots` (function-entry parameter rooting; consults `ast.Param` annotation via `type_expr_is_non_heap_scalar`)
- `emit_pattern_bind` `VarPattern` (match binder rooting; consults `scrut_type`)
- `load_lambda_params` (lambda parameter rooting; consults `ast.Param` annotation)
- `allocate_tco_slots_acc` (TCO slot rooting; consults `ast.Param` annotation)

**Invariant:** when in doubt — when no source-level type is available — fall back to `push_temp_root`. A spurious extra root is harmless; a missing root corrupts the heap. Polymorphic `TVar`s could resolve to heap types at runtime in monomorphized code; do **not** treat them as non-heap.

Tests pinning the invariant: `tests/stdlib/compiler/test_codegen.spr` includes regression tests "Int args to call must NOT emit gc_push_i64_root", "Vec arg to call MUST emit gc_push_i64_root", and "mixed call: Vec arg rooted".

### GC safety in `runtime/sprout_runtime.c`

`just gc-safety-check` lints `runtime/sprout_runtime.c` for `const char*`/`char*` parameters live across `sprout_gc_maybe_collect_threshold()` calls (callers are expected to root heap values before such calls). Run this after editing any C builtin in `runtime/sprout_runtime.c` that allocates heap strings. Use `just gc-safety-check --strict` to fail on any finding; the default mode warns only.

### Bootstrap binary rebuild protocol

1. Rebuild `compile_driver_bin_stage1` from the committed IR seed (`bootstrap/compile_driver.ll`):
   ```
   just bootstrap-from-seed
   ```
   The seed is platform-agnostic LLVM IR text; `clang` materializes the host target at link time. Refresh the seed with `just refresh-seed` after any compiler-source change that perturbs the IR (CI's `just verify-bootstrap-fixed-point` will flag this).
2. Rebuild stage-1 from an existing stage-0 binary (`compile_driver_bin`) if you have one:
   ```
   just build-stage1
   ```
3. After rebuild, verify the test suite passes:
   ```
   mise exec -- just test
   ```
   Bundler parity is covered by `tests/stdlib/test_bundler.spr`.

## Known Limitations

See [README.md §Not Yet Supported](./README.md#not-yet-supported-common-gotchas) for language features that are planned but not yet implemented (`!expr` negation, `let..in` in pure functions, effectful list iteration), along with the standard workaround for each.

## Commit Guidance

Use commit messages that explain intent, for example:
- `spec: define match exhaustiveness rules`
- `parser: add infix precedence for comparison operators`
- `types: improve error for mismatched function arguments`

### Agent commit workflow (DoD acknowledgement)

A Claude Code PreToolUse hook (`scripts/dod_check_on_commit.sh`, wired via `.claude/settings.json`) intercepts any Bash call running `git commit` and blocks it unless an acknowledgement file at `.git/dod-ack` matches the currently staged tree's `git write-tree` hash. The hook exists to force a deliberate Definition-of-Done check before committing.

Workflow:
1. Stage everything intended for the commit (`git add …`). Staged tree must be final before acking.
2. Mentally verify the DoD criteria above for the staged changes.
3. Run `just dod-ack` — writes the current `git write-tree` hash to `.git/dod-ack`.
4. Run `git commit …`. The hook re-computes `git write-tree` and compares; matching hash → commit proceeds.

Re-acking is required whenever the staged tree changes (re-`git add` after the ack invalidates it). `git commit --amend` for message-only changes does *not* require re-acking — the tree is unchanged.

The hook substring-matches `git commit` in the Bash tool's command text; this means a Bash invocation that *contains* the literal string `git commit` (e.g., inside an echo or a for-loop iteration list) will also trigger. Recovery is the same `just dod-ack && retry` ceremony; the false-positive cost is intentional and accepted.
