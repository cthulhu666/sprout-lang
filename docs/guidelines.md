# Sprout Code Authoring Guidelines

**Status:** v0. Each rule is tagged with the audience it binds — see **Audience tags** below.

These are *code-authoring* guidelines, distinct from:

- `docs/style-guide-v0.md` — source formatting (whitespace, line length, naming case).
- `docs/language-design-best-practices.md` — what to put in the language itself.
- `docs/spec-v0.md` — the normative language semantics.
- `docs/observability-guard-rails.md` — pass structure and instrumentation. In particular: a new pipeline pass ships with an env-gated `SPROUT_TRACE_<pass>` hook emitting one structured line per event (guard-rail #2; `SPROUT_TRACE_DISPATCH` is the exemplar).

This document constrains *what* the code does, not *how* it looks.

A `PreToolUse` hook (`scripts/guidelines_reminder.sh`) surfaces a short reminder of the basics when an agent is about to edit a `.sprout` or `.spr` file.

## Audience tags

Not every rule binds every author. Each rule below carries one or more tags:

- **[Universal]** — all Sprout code, including user programs. These are the load-bearing correctness rules.
- **[Library]** — code that exposes an API for others to call: the stdlib (`stdlib/`) and any published library. The stdlib is the canonical library author.
- **[Compiler]** — the self-hosted compiler and its pipeline (`stdlib/compiler/`).

A **[Universal]** rule also binds [Library] and [Compiler] code; the narrower tags mark rules whose audience is *only* that group. When a rule does not carry a tag you fall under, ignoring it is not a deviation — it simply does not apply. (User-facing idiom and formatting live in `docs/idiomatic-sprout.md` and `docs/style-guide-v0.md`, not here.)

## The basics

### 1. Functional core, IO at edges

*[Universal].* Most mechanically enforced in `stdlib/`/`stdlib/compiler/` (the effect system verifies it), but the discipline applies to any Sprout code.

A function should not carry `!{IO}` in its effect row unless it genuinely needs IO. Push IO to the edges of the call graph (drivers, runners, the compile driver). Pure functions are easier to test, easier to reason about, and Sprout's effect system makes the discipline mechanically verifiable.

- **Bad:** a pure transformation threaded through an IO function because "it was convenient."
- **Good:** the pure transformation returns a value; the caller (already in IO) handles the IO part.

### 2. Total over partial

*[Universal] as a principle; [Library] as a hard mandate — the stdlib must not export a partial function.*

Stdlib must not expose partial functions. Every `f : A -> B` is defined on all of `A`. Failure modes return `Maybe T` or `Result E T`.

- `vec_get`, `dict_get` already return `Maybe a` for missing keys — no panic, no exception.
- A division that can fail returns `Result DivByZero Int`, not "trust me, the input is never 0."

The compiler may break this rule locally for unreachable-by-invariant cases, but the unreachable arm must surface an explicit error with location, never silent garbage.

No naming suffix is needed to mark fallibility — the return type carries the information. The compiler's existing `try_*` prefix is permitted as a semantic cue ("this is a search that might not match"), not as a partiality marker.

### 2a. When a match classifies an ADT, enumerate every variant

*[Compiler] where a function answers a question **about** a variant; [Universal] as a habit.*

A catch-all is fine when its answer is genuinely right for anything not named above. It is a defect when the answer is only right for *today's* variants. The test to apply before writing `| _ ->`:

> **Would this arm still give the right answer for a variant that does not exist yet?**

If the answer is "no, a future variant would need its own arm", spell every variant out. The exhaustiveness check is a compile-time one and names what is missing —

```
ERROR: check: Non-exhaustive match on stdlib.compiler.ast.Pattern — no branch matches AsPattern in function ast.pattern_pos
```

— so the enumeration turns "someone must remember to update every site" into "the build stops until they have". That is a mechanical guarantee; a comment asking people to remember is not.

This is not a licence to sweep wildcards out of the codebase. Most of the ~500 catch-alls under `stdlib/compiler/` are legitimate defaults, and a nested match on a *different* type inside a variant's arm is a common and correct one (`pattern_linear_binders` matches `types.Type` inside its tuple arm).

**Before writing a classifier, look for an existing one.** "Which names does this pattern bind?" is answered in five places — `ast_to_ir.pattern_names`, `dce.pat_binds`, `linear_check.pattern_all_binders`, `linear_check.pattern_linear_binders`, `verify_dispatch.pattern_bound_names`. Reuse beats a sixth copy; if the copy is unavoidable (different return shape, module layering), match the exhaustive form the others use.

Recorded because the cost is measured, not hypothetical: on 2026-08-14 a name-keyed dispatch check handled the top-level shadowing case and missed every local binder, making `fn f(append: …)` a hard compile error for seven hours. The fix for it was then written with a catch-all of its own, in a file whose four sibling functions were all exhaustive.

### 3. Make illegal states unrepresentable

*[Universal]. The "no boolean blindness in public APIs" note below is specifically [Library].*

Encode invariants in ADTs, not runtime checks or convention.

- `Visibility = Public | Private` over `is_public: Bool`.
- `NonEmpty a` over "a list, but it's never empty, trust me."
- Phase-distinct ASTs (`RawExpr`, `ResolvedExpr`, `TypedExpr`) over a single `Expr` with optional decorations set per pass.

**No boolean blindness in public APIs.** `do_thing(Verbose)` over `do_thing(true)`. Callers read clearer, and adding a third case later doesn't require a breaking signature change.

### 4. Parse, don't validate

*[Universal]. The internal corollary below is [Library]/[Compiler].*

At every system boundary (file read, parser input, user-supplied data, deserialization), transform raw input into a structured type *once*. Internal code consumes only the precise type; it does not re-validate.

- **Validation** — receive a value, check predicates, return `Bool` or `Result E ()`. Information is thrown away; callers re-check.
- **Parsing** — receive a value, transform into a representation that *can only exist if valid*. Information is preserved in the type.

Sprout's `qualify` pass and the `RawExpr → ResolvedExpr → TypedExpr` AST chain are the existing exemplars; new passes inherit the pattern by default.

**Internal corollary — staged operations return their post-condition type.** The same discipline applies *inside* the pipeline, not only at system boundaries. When a function returns a value that is only safe after a fixed follow-up step, perform that step inside the function and return the finished value; do not hand callers a not-yet-valid intermediate and trust them to remember the step. If the raw intermediate is genuinely needed elsewhere, expose two functions with distinct names — never a single function whose return type cannot tell the two states apart. `infer_expr` (raw — substitution not yet applied) is private; the public `typecheck_expr` applies `apply_subst_typed_expr` before returning, so no caller can forget it. The omission previously stored a typed AST with unresolved type variables and surfaced as a SIGBUS far from its cause. Where the language cannot yet give the two states distinct types, encapsulation (private producer + public safe wrapper) is the v0-feasible form of the same rule.

Basics #3 and #4 are paired: #3 designs the type, #4 disciplines the boundary that produces values of that type.

### 5. Errors carry a source location from inception

*[Library] and [Compiler] — any code that constructs error ADTs or diagnostics. User code rarely does.*

Every error ADT has a `Span` field, set at the point of construction, not glued on by a caller later.

```sprout
type ParseError = ParseError(Span, ParseErrorKind)
```

A spanless error in stdlib or the compiler is a bug. Locations are the difference between a usable diagnostic and a frustrating one.

### 6. Data-last argument order in public APIs

*[Library] only — a public-API ergonomics rule. It does not bind internal, never-composed entry points; see Scope below.*

Public-facing functions place the collection / receiver / "thing being acted on" in the **last** parameter position.

- `fn vec_get(index: Int, vec: Vec a) -> Maybe a` ✓
- `fn dict_get(key: String, dict: Dict v) -> Maybe v` ✓
- `fn add_format_issue(issues: List LintIssue, src: String) -> List LintIssue` ✗ — `issues` should be last.

The convention prevents nested expressions like the current `lint_source` five-call chain from becoming illegible, and it keeps `|>` available to callers who want it.

**Scope — public APIs only.** A "public API" is a function whose callers live outside its defining module. This rule does **not** bind internal pipeline entry points that are never `|>`-chained: the compiler's `lower_program(prog, env)`, `check_program(prog)`, and `bundle_file(path, stdlib_root)` place the program receiver *first* deliberately — they are invoked by name in a fixed sequence, not composed into pipelines, so receiver-first is the clearer order there and is **not** a deviation from this rule.

**Pipe-style with `|>` is permitted, not required.** Use it for linear sequences of pure transforms where it reads top-to-bottom better than nested calls:

```sprout
run_format_fold(non_eof, vec_length(non_eof))
  |> code_state_parts
  |> list_reverse
  |> string_concat_many
```

Avoid pipe for:

- Single-step calls (`x |> f` is just `f(x)` with extra noise).
- Chains where intermediate `let` bindings *aid* understanding.
- Effect-crossing calls that need explicit `match` unwrapping.

The `>>` / `<<` composition operators exist in the language; this document neither promotes nor restricts them. Use them locally if a site is genuinely clearer composed than as a lambda.

### 7. Use `wrap` for semantic distinctions on shared representations

*[Universal] — any code where two semantic kinds share a primitive type. Heavily used in the compiler's naming seams.*

When two distinct semantic kinds share a primitive type (`String`, `Int`, `Dict T`) and confusing them would be a bug, declare each with `wrap` so the typechecker enforces the distinction. `wrap Foo = T` is zero-cost: construction and destruction are identity at the IR level, so the type-safety benefit costs no runtime.

**Apply `wrap` whenever any of the following holds** (any single bullet is sufficient — these are independent reasons, not a checklist):

- Two functions accept the same primitive but with different semantic meanings — `fn f(path: String, stdlib_root: String)` should become `fn f(path: FilePath, stdlib_root: StdlibRoot)`.
- A value crosses a module boundary where callers might confuse it with a similarly-typed value (qualified vs raw names, body env vs global env).
- A retro documents a bug caused by swapping two same-typed values — `wrap` makes the same bug class a compile error.

**Retro evidence is a *priority signal*, not a *requirement*.** Cases where a swap bug has already cost session time are higher priority because the bug class is proven real, but **preventative application is encouraged**. Don't wait for the bug to happen first. The cost of catching a swap statically is negligible; the cost of the bug — silent dispatch errors, leaked markers, corrupted GC roots — is hours of investigation per recurrence.

**Do not use when:**

- The inner type is the natural API surface (an `Int` width that callers do arithmetic on, a `String` message threaded directly to `print`).
- The value lives inside one function and never crosses a function boundary — local naming is enough.
- The cost of wrap/unwrap ceremony at the call sites genuinely exceeds the bug-prevention value (wrapping every dict key would force ceremony at hundreds of access sites for one bug class).

The middle bullet of "do not use" is doing the real work — it's a *cost-benefit* judgment, not a *necessity* test. The "preventative is encouraged" rule above means the burden is on "this is too expensive to wrap" rather than "this isn't proven dangerous enough to wrap."

**Where to wrap — apply at extraction seams, not everywhere.** The placement heuristic that keeps the ceremony bounded: introduce a `wrap` type only at the *extraction seam* — the point where a raw `String` (or other primitive) first comes *out* of the AST / scanner / parse result and takes on a specific meaning. Keep internal helpers and dict/map storage as the raw `String`. Wrapping at the seam catches the swap where confusion actually happens (at the boundary), while leaving internals raw avoids flooding hundreds of interior call sites with wrap/unwrap ceremony for no additional safety.

**Worked examples in this codebase:**

- `wrap FilePath = String` / `wrap StdlibRoot = String` distinguish the two `String` parameters threaded through every compiler entry point. No retro evidence, but two semantically-different strings that are adjacent at every call site — applied preventatively.
- `wrap ProgVarName = String` / `wrap FreshTVarName = String` distinguish user-written type-variable names ("a") from compiler-generated fresh names ("a42") at the cascade boundary. Retro-anchored.

**Constraints (v1):**

- Single-field only. Multi-field semantic structs use records or ADTs.
- No type parameters on the wrap itself.
- No constructor hiding. If invariants need a smart constructor, document the convention as a comment until private constructors land (see Deferred to v1 below).

See `docs/spec-v0.md` §5.6.1 for the normative wrap declaration semantics.

## Documenting load-bearing invariants

*[Compiler] primarily; applies to any code with multi-arm invariants not visible from a single function body.*

Any structural or ordering invariant that is **not visible from a single function body** must be documented in a header comment at its definition site. Priority cascades, paired-list length invariants (`list_length(params) == arity`), key-prefix conventions, and ABI bit-layout choices are the recurring cases: each was reordered or violated by a later session because the constraint lived only in the author's head. Git history is not a substitute — future agents do not read it.

Document the *invariant* (present-tense — what must hold and why), not the *history* (what changed when); this keeps the rule compatible with the short-comment discipline. Enumerate the scenarios explicitly with "do not reorder without verifying …" language, so a reader editing one arm sees which other arms they are about to break:

```
# RESOLUTION CASCADE — do not reorder without verifying all three scenarios:
# A. Container-wrapped (Eq (Maybe a)): scan_ptf_for_prog_var ...
# B. Polymorphic forwarded (Foldable f in join->fold): resolve_via_fwd ...
# C. Concrete direct (Eq Int for assert_eq): scan_prog_to_fresh_for_instance
# D. Final fallback: resolve_one_constraint_tdict
```

The canonical failure this prevents: a reordering that silently breaks scenario B while the scenario-A and -C tests still pass. (Runtime ABI invariants follow the same rule — see AGENTS.md.)

## Process

- Deviations from these guidelines require a justification in the PR description.
- Additions or revisions follow the design-discussion process in `AGENTS.md` §Design Change Process.
- This is v0; lived experience and the items below will inform v1.

## Deferred to v1

Each deferred item is annotated with the trigger condition that would prompt its inclusion.

- **Smart constructors** (hiding raw constructors behind validating builders). *Trigger:* a private-constructor language feature (per-module export controls finer than per-symbol). With `wrap` shipped, the type-safety half is now achievable; the *enforcement* half (preventing direct construction from outside the defining module) still requires private constructors.
- **Error-accumulation strategy** (fail-fast vs accumulate across the compiler). *Trigger:* enough multi-error UX work to know whether the cost of accumulation pays off.
- **Naming convention for partial wrappers** (`head_opt` vs `try_head` vs `head?`). *Trigger:* the first stdlib case where a partial and total sibling both exist and need to be distinguished by name. Currently unneeded under basic #2.
- **Pipe-style as positive style guidance** (mandate, not permission). *Trigger:* enough new data-last code accumulating that the cost/benefit of `|>` is empirically clear.
