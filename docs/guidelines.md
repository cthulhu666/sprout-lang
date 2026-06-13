# Sprout Code Authoring Guidelines

**Status:** v0. Applies to stdlib (`stdlib/`) and the self-hosted compiler (`stdlib/compiler/`).

These are *code-authoring* guidelines, distinct from:

- `docs/style-guide-v0.md` — source formatting (whitespace, line length, naming case).
- `docs/language-design-best-practices.md` — what to put in the language itself.
- `docs/spec-v0.md` — the normative language semantics.

This document constrains *what* the code does, not *how* it looks.

A `PreToolUse` hook (`scripts/guidelines_reminder.sh`) surfaces a short reminder of the six basics when an agent is about to edit a `.sprout` or `.spr` file.

## The six basics

### 1. Functional core, IO at edges

A function should not carry `!{IO}` in its effect row unless it genuinely needs IO. Push IO to the edges of the call graph (drivers, runners, the compile driver). Pure functions are easier to test, easier to reason about, and Sprout's effect system makes the discipline mechanically verifiable.

- **Bad:** a pure transformation threaded through an IO function because "it was convenient."
- **Good:** the pure transformation returns a value; the caller (already in IO) handles the IO part.

### 2. Total over partial

Stdlib must not expose partial functions. Every `f : A -> B` is defined on all of `A`. Failure modes return `Maybe T` or `Result E T`.

- `vec_get`, `dict_get` already return `Maybe a` for missing keys — no panic, no exception.
- A division that can fail returns `Result DivByZero Int`, not "trust me, the input is never 0."

The compiler may break this rule locally for unreachable-by-invariant cases, but the unreachable arm must surface an explicit error with location, never silent garbage.

No naming suffix is needed to mark fallibility — the return type carries the information. The compiler's existing `try_*` prefix is permitted as a semantic cue ("this is a search that might not match"), not as a partiality marker.

### 3. Make illegal states unrepresentable

Encode invariants in ADTs, not runtime checks or convention.

- `Visibility = Public | Private` over `is_public: Bool`.
- `NonEmpty a` over "a list, but it's never empty, trust me."
- Phase-distinct ASTs (`RawExpr`, `ResolvedExpr`, `TypedExpr`) over a single `Expr` with optional decorations set per pass.

**No boolean blindness in public APIs.** `do_thing(Verbose)` over `do_thing(true)`. Callers read clearer, and adding a third case later doesn't require a breaking signature change.

### 4. Parse, don't validate

At every system boundary (file read, parser input, user-supplied data, deserialization), transform raw input into a structured type *once*. Internal code consumes only the precise type; it does not re-validate.

- **Validation** — receive a value, check predicates, return `Bool` or `Result E ()`. Information is thrown away; callers re-check.
- **Parsing** — receive a value, transform into a representation that *can only exist if valid*. Information is preserved in the type.

Sprout's `qualify` pass and the `RawExpr → ResolvedExpr → TypedExpr` AST chain are the existing exemplars; new passes inherit the pattern by default.

Basics #3 and #4 are paired: #3 designs the type, #4 disciplines the boundary that produces values of that type.

### 5. Errors carry a source location from inception

Every error ADT has a `Span` field, set at the point of construction, not glued on by a caller later.

```sprout
type ParseError = ParseError(Span, ParseErrorKind)
```

A spanless error in stdlib or the compiler is a bug. Locations are the difference between a usable diagnostic and a frustrating one.

### 6. Data-last argument order in public APIs

Public-facing functions place the collection / receiver / "thing being acted on" in the **last** parameter position.

- `fn vec_get(index: Int, vec: Vec a) -> Maybe a` ✓
- `fn dict_get(key: String, dict: Dict v) -> Maybe v` ✓
- `fn add_format_issue(issues: List LintIssue, src: String) -> List LintIssue` ✗ — `issues` should be last.

The convention prevents nested expressions like the current `lint_source` five-call chain from becoming illegible, and it keeps `|>` available to callers who want it.

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

When two distinct semantic kinds share a primitive type (`String`, `Int`, `Dict T`) and confusing them would be a bug, declare each with `wrap` so the typechecker enforces the distinction. `wrap Foo = T` is zero-cost: construction and destruction are identity at the IR level, so the type-safety benefit costs no runtime.

**Use when:**

- Two functions accept the same primitive but with different meanings — `fn f(path: String, stdlib_root: String)` should become `fn f(path: FilePath, stdlib_root: StdlibRoot)`.
- A value crosses a module boundary where callers might confuse it with a similarly-typed value (qualified vs raw names, body env vs global env).
- A retro documents a bug caused by swapping two same-typed values — `wrap` makes the swap a compile error.

**Do not use when:**

- The inner type is the natural API surface (an `Int` width that callers do arithmetic on, a `String` message threaded directly to `print`).
- The value lives inside one function and never crosses a function boundary — local naming is enough.
- The cost of wrap/unwrap exceeds the bug-prevention value (wrapping every dict key would force ceremony at hundreds of access sites for one bug class).

**Worked examples in this codebase:**

- `wrap FilePath = String` / `wrap StdlibRoot = String` distinguish the two `String` parameters threaded through every compiler entry point.
- `wrap BodyEnv = Dict types.Scheme` / `wrap GlobalEnv = Dict types.Scheme` enforce the `@fwd:` vs `@eta_fwd:` scope distinction documented in the constrained-dispatch retros.

**Constraints (v1):**

- Single-field only. Multi-field semantic structs use records or ADTs.
- No type parameters on the wrap itself.
- No constructor hiding. If invariants need a smart constructor, document the convention as a comment until private constructors land (see Deferred to v1 below).

See `docs/spec-v0.md` §5.6.1 for the normative wrap declaration semantics.

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
