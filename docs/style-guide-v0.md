# Sprout Style Guide v0

This document defines the default source-style conventions for Sprout code in
this repository.

It is intentionally practical:

- `docs/spec-v0.md` remains the normative language contract.
- This style guide is non-normative. It defines how Sprout code should be
  written for readability, consistency, and tool friendliness.
- When the formatter already enforces a rule, treat that formatter output as
  authoritative.
- When the formatter does not enforce a rule yet, follow this guide in new and
  edited code.

The audience is both humans and AI agents. The goal is code that is precise,
predictable, easy to scan, and easy to transform mechanically.

## 1. Core Principles

1. Prefer the most obvious spelling over the shortest spelling.
2. Keep names descriptive enough that local comments are usually unnecessary.
3. Make control flow visually obvious.
4. Prefer stable, repetitive layout over clever formatting.
5. Keep related syntax shaped the same across examples, stdlib code, tests, and
   docs.

## 2. Formatting Baseline

Use the repository formatter as the baseline:

- Run `mise exec -- just fmt` for repo-wide formatting.
- Run `mise exec -- just fmt-file path/to/file.sprout` for a single file.
- Run `mise exec -- just fmt-check` or `mise exec -- just fmt-check-file ...`
  in verification flows.

Formatting rules that should be treated as fixed:

- Use spaces, not tabs.
- End files with a trailing newline.
- Do not leave trailing whitespace.
- Write `name: Type`, not `name : Type`.
- Put spaces around `=`, `->`, binary operators, and `++`. The one exception is the
  range operator `..`, which is written **tight** on both sides — `0..n`, `lo..hi`,
  `p.x..p.y` — matching how ranges are spelled elsewhere (Rust, Kotlin, Ruby). The
  formatter normalises it, so `lo .. hi` becomes `lo..hi`.
- Put a space before effect annotations: `-> Unit !{IO}`.
- Write `class` and `instance` bodies in the layout form — members indented two
  spaces past the keyword, no braces. The brace form still parses but is
  deprecated; the linter flags it as `deprecated-brace-body`, and because the
  pre-commit hook fails on any lint finding, a staged file using braces will not
  commit.

Examples:

```sprout
fn greet(name: String) -> String =
  "hello, " ++ name
```

```sprout
fn main() -> Unit !{IO} =
  print("hello")
```

## 3. File Structure

Use this top-level order when the file shape allows it:

1. `module` declaration, if present
2. `import` declarations
3. exported and public-facing type declarations
4. exported and public-facing function declarations
5. private helper declarations near the exported code that uses them

Additional rules:

- Keep one conceptual topic per file.
- Prefer a short group of related helpers over a grab bag of unrelated
  declarations.
- Keep helper declarations close to the public declaration they support.
- In docs and examples, prefer short files that demonstrate one primary idea.

## 4. Naming

Use names that communicate role, not implementation trivia.

Preferred conventions:

- Types and constructors: `UpperCamelCase`
- Functions and values: `snake_case`
- Type variables: short lowercase names such as `a`, `b`, `e`
- Predicate functions: start with a verb or boolean cue such as `is_`,
  `has_`, `can_`, or a domain verb like `contains`

Examples:

```sprout
type HttpResult a =
  | Success a
  | Failure String

fn parse_port(text: String) -> Result String Int =
  ...
```

Avoid:

- Unexplained abbreviations unless they are domain-standard
- Single-letter names outside tiny local scopes
- Names that encode incidental implementation details such as `tmp_value`,
  `helper2`, or `data1`

Nuance:

- Conventional short names such as `f`, `xs`, `acc`, `n`, `err`, or `r` are
  acceptable when the declaration is local and the role is obvious from the
  type and surrounding code.
- In exported signatures and teaching examples, rename only the placeholders
  that stay ambiguous even with their types. Prefer `value`, `left`, and
  `right` over bare `x` and `y` in public interfaces.

## 5. Type Annotations

Sprout supports inference, but repository style should still optimize for
readability.

Default guidance:

- Prefer explicit parameter and return annotations on exported functions.
- Prefer explicit annotations on functions that form part of an example or a
  document teaching surface syntax.
- It is acceptable to omit annotations on small private helpers when the type
  is obvious from the body and the surrounding context.
- Add annotations when they improve diagnostics, reduce ambiguity, or clarify
  intent.

Examples:

```sprout
export fn list_length(xs: List a) -> Int =
  ...
```

```sprout
fn step(acc: Int, value: Int) -> Int =
  acc + value
```

Avoid using inference to make public declarations cryptic.

## 6. Functions and Bodies

Prefer multi-line function declarations once the body stops being trivially
short.

Good default:

```sprout
fn classify(n: Int) -> String =
  if n < 0 then "negative"
  else if n == 0 then "zero"
  else "positive"
```

For very short, self-evident helpers, a one-line definition is acceptable:

```sprout
fn add1(n: Int) -> Int = n + 1
```

Use these body rules:

- Indent function bodies by two spaces.
- Prefer one expression per visual line.
- Keep deeply nested expressions rare; introduce helpers or local bindings when
  structure becomes hard to scan.
- Keep binary operators surrounded by spaces — except `..`, which is tight (§2).

## 7. `where` Blocks

Use function-local `where` blocks to name intermediate pure values that make
the main body easier to read.

Preferred shape:

```sprout
fn score(n: Int) -> Int =
  doubled + 1
where
  base = n * 2
  doubled = base * 2
```

Rules:

- Use `where` only when it makes the main expression simpler.
- Order bindings so each binding depends only on parameters and earlier
  bindings.
- Prefer descriptive local names over comments.
- Keep `where` blocks short. If the logic becomes substantial, extract a helper
  function instead.

Do not use `where` blocks as a dumping ground for unrelated local logic.

## 8. Conditionals and Matching

Use `if` for small binary decisions. Use `match` when branching on constructors,
tuples, or several cases.

Preferred `if` shape:

```sprout
fn abs(n: Int) -> Int =
  if n < 0 then -n
  else n
```

Preferred `match` shape:

```sprout
fn unwrap_or(fallback: a, value: Maybe a) -> a =
  match value with
  | Just item -> item
  | Nothing -> fallback
```

Rules:

- Put each `match` branch on its own line.
- Keep branch bodies short when possible.
- When a branch body becomes large, extract a helper instead of hiding logic in
  one branch.
- Order branches from most specific to fallback.
- Prefer explicit constructor matches over `_` when the constructor name adds
  meaning.
- Prefer list-pattern sugar (`[]`, `[x]`, `[a, b]`, `[x | rest]`,
  `[a, b | rest]`) over long-form `Cons`/`Nil` chains — see
  [idiomatic-sprout.md § Match lists by shape](./idiomatic-sprout.md) for the
  shapes and what each matches.

## 9. Effects

Effects should remain visually explicit.

Rules:

- Write explicit `!{IO}` annotations on effectful functions. Since 2026-08-16 this is
  not merely style — a body that performs IO under a pure signature is a compile error
  (spec §7 rule 8; see [effect-enforcement-v0.md](./effect-enforcement-v0.md)). The
  style rule that remains yours to keep is *not over-declaring*: the compiler accepts
  `!{IO}` on a function that does none.
- Do not hide effectful work inside misleadingly named helpers.
- For structuring the pure/effectful split, see
  [idiomatic-sprout.md § Keep effects at the edges](./idiomatic-sprout.md).

Preferred shape:

```sprout
fn render_total(total: Int) -> String =
  "total: " ++ int_to_string(total)

fn main() -> Unit !{IO} =
  print(render_total(42))
```

## 10. Function Calls and Pipelines

Prefer whichever form makes data flow easiest to read. For *when* to reach for
`|>` and the combinator-chain shapes, see
[idiomatic-sprout.md § Chain transforms with `|>`](./idiomatic-sprout.md); the
layout and argument-order rules below are this guide's turf.

Rules:

- Use ordinary calls for simple local composition.
- Do not build long, dense operator chains when intermediate names would make
  the logic clearer.
- **Data-last argument order.** When a function takes a collection (or the value
  being transformed) alongside callbacks or configuration, put that data argument
  **last**. This matches the prelude's higher-order family (`map`, `fold`,
  `filter`, `range_each`/`range_fold`, `list_each`/`list_fold`) and keeps calls
  pipeline-friendly, since `|>` feeds the left value into the final position:
  `xs |> map(f)` desugars to `map(f, xs)`. Reserve data-first only for accessors
  where the receiver reads as a subject (`range_start(r)`, `vec_length(v)`).

Good:

```sprout
value |> normalize |> render
```

Also good:

```sprout
render(normalize(value))
```

Avoid:

```sprout
value |> f(a, b) |> g(c, d) |> h(e, f)
```

unless that chain is already familiar and clearly readable in context.

## 11. Comments

Comments should explain intent, invariants, or non-obvious tradeoffs.

Rules:

- Prefer code that does not need explanatory comments.
- Use `#` comments for brief, local context.
- Keep comments accurate when code changes.
- Do not restate obvious syntax or types in comments.

Good:

```sprout
# Keep the fallback branch explicit so diagnostics stay predictable.
fn decode_flag(text: String) -> Bool =
  ...
```

Avoid:

```sprout
# Add one to n.
fn add1(n: Int) -> Int = n + 1
```

## 12. Examples and Teaching Code

Examples carry extra weight because they shape user expectations.

Rules:

- Prefer explicit, idiomatic naming over clever brevity.
- Prefer small examples with one teaching goal.
- Keep experimental features labeled when the surrounding docs do so.
- Do not let examples silently imply language guarantees that are not yet
  normative.
- In examples meant for beginners, choose clarity over maximal concision.

## 12.5 Deriving vs hand-written instances

Prefer `deriving (Eq, Ord, ToString)` over a hand-written `instance` when the
synthesized body matches the intended semantics. The synthesized body is
structural — equal-by-fields for `Eq`, declaration-order for `Ord` (nullary-only
in v1), "CtorName(field, ...)" rendering for `ToString` (see spec §8.6).

Examples:

```sprout
# Idiomatic: derive when the structural body is what you want.
type Color (..) deriving (Eq, Ord, ToString) =
  | Red
  | Green
  | Blue
```

```sprout
# Hand-written: when the semantics differ from the structural default.
# (Here `Set` wants set-equality, not field-equality on the internal storage.)
type Set a (..) =
  | Set (List a)

instance Eq (Set a) where Eq a
  fn eq(left: Set a, right: Set a) -> Bool =
    set_equal_by_contents(left, right)
```

For a C-like enum (all constructors nullary) that needs a stable integer form —
serialization, a lookup table, an on-disk tag — prefer `deriving (Enum)` over a
hand-written `match` mapping each variant to a number. It makes declaration order
the single source of truth for `ordinal`/`from_ordinal` and removes the risk of a
tag table drifting out of sync with the type:

```sprout
# Idiomatic: let declaration order define the tags.
type TileKind (..) deriving (Enum) =
  | Water | Beach | Grass | Forest | Desert | Mountain | Snow | Tundra
```

`from_ordinal : Int -> Maybe a` is partial (out-of-range → `Nothing`) and must be
called where the target type is concrete — wrap it in a domain function with a
declared return type rather than calling it in a fully polymorphic position:

```sprout
# Concrete return type pins from_ordinal's dispatch; a total wrapper supplies a
# fallback if the domain wants one.
fn tile_kind_of(tag: Int) -> TileKind =
  match from_ordinal(tag) with
  | Just k -> k
  | Nothing -> Grass
```

Mix freely: `deriving (Eq)` on the type and a hand-written `instance Ord` is
fine — the derived instance and the hand-written one cover different classes.
Do not mix them for the same class on the same type (the synthesizer would
emit a duplicate instance; CI will reject).

When using `deriving` on a `type` with no `(..)` constructor-export marker,
the synthesized instance methods are still exported because instance methods
are first-class names in the prelude/imports. The `(..)` only governs whether
*constructors* are visible to other modules.

## 13. Guidance for AI Agents

When creating or editing Sprout source, agents should:

1. Preserve formatter-compatible spacing and layout.
2. Follow the file ordering and naming rules in this guide unless an existing
   file already establishes a stronger local convention.
3. Prefer explicit type annotations on exported declarations and teaching
   examples.
4. Keep effect annotations explicit and visible.
5. Extract helpers rather than introducing dense nested expressions.
6. Update nearby examples and docs when a style recommendation changes.

If an existing file conflicts with this guide, prefer small local consistency
over unrelated large rewrites. Apply the guide to the changed region first.
