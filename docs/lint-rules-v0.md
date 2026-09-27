# Lint rules as patterns (v0)

Status: **DESIGN, nothing implemented.** Experimental; no change to `docs/spec-v0.md`, which does
not describe the linter. Supersedes the "config file for per-rule enable/disable" line in
`BACKLOG.md`'s `Formatter/linter beyond the baseline` entry — that entry should point here.

The ask: make the linter catch a reinvented prelude combinator, and make adding the next one cost
one list entry instead of a new AST walk. Today's seven rules are each hand-written procedural code
in a 1179-line module, with no config and no way to add a rule as data.

## 1. Problem

Two problems, and only the second is about `result_from_maybe`.

**1a. Rules cost too much to add.** Every rule in `stdlib/compiler/lint_rules.sprout` is a bespoke
matcher plus a hook into `walk_expr`. Parameters are hardcoded (`min_staircase_depth`, line 260).
There is no enable/disable, no severity, no per-path scoping. A rule is a code change to the
compiler, which means the seed gate, `just test`, and a PR — for what is often one shape.

**1b. Reinvented combinators are invisible.** Measured over 1128 files / 11031 top-level functions,
counting two-branch matches whose branches do nothing but rewrap:

| shape | stdlib | examples | tests | total |
|---|---|---|---|---|
| `result_from_maybe` | 3 | 1 | 0 | **4** |
| `maybe_with_default` | 50 | 4 | 49 | **103** |
| `result_with_default` | 11 | 1 | 20 | **32** |

The `result_from_maybe` four are `prelude.sprout:1517` (its own definition),
`analysis_service_driver.sprout:98` and `:106` (the sites issue #378 owns), and
`examples/sentry_issue_browser_tui.sprout:11`. These counts come from a text scan that only sees the
literal `match` spelling, so **every row is a floor**: `let..else` desugars to the same AST
(`parser.sprout:1032`, `build_let_binding_match`) and a text scan cannot see it.

## 2. Goals and non-goals

**Goals.**

1. Adding a detected combinator costs one name in a list. No pattern written by hand.
2. Patterns are *derived from the prelude's own definitions*, so they cannot drift from them.
3. Rules are configurable: enable/disable, severity, per-rule parameters, path scoping.
4. A suggestion that changes evaluation order says so.

**Non-goals.**

1. **Rule logic supplied externally.** ESLint-style plugins need `eval`; dylint-style plugins need
   runtime library loading. Sprout has neither — there is no evaluator module under
   `stdlib/compiler/` (the REPL's `StatefulSession` carries imports and declarations as source text,
   not values), and `runtime/` contains no `dlopen`/`dlsym`/`LoadLibrary` call. Rule *code* is
   compiled in. Rule *data* is not.
2. **Autofix.** Needs an AST-aware rewriter; today's formatter is a line-based text transform.
   Stays in `BACKLOG.md`.
3. **Replacing the seven procedural rules.** See §9 — about three could become patterns, four
   cannot.
4. **Type-directed matching.** The engine is syntactic and never consults inferred types or effect
   rows. This is a real limit, and §6 is where it bites.
5. **A `?hole` pattern syntax.** v0 derives patterns from real parsed definitions, so holes need no
   spelling of their own and the lexer is untouched. §11 keeps the door open.

## 3. Prior art

Every row below was checked against the tool's own documentation, not recollection.

| tool | a rule is | pattern language | holes | derive from code? | semantics caveat |
|---|---|---|---|---|---|
| **hlint** | YAML data | Haskell itself | single letters | **yes, `--find=Module.hs`** | notes: "Increases laziness", "Decreases laziness", "Removes error" |
| **Semgrep** | YAML data | the target language itself | `$X`, uppercase only | no | — |
| **ast-grep** | YAML data | the target language itself | `$VAR`, `$_` | no | — |
| **Clippy** | compiled in | n/a | n/a | no | `clippy.toml` sets per-lint parameters; users cannot add lints |
| **dylint** | compiled dynamic library, loaded at runtime | n/a | n/a | no | exists *because* Clippy's set is static |
| **ESLint** | executable JS: `create(context)` returning AST visitors | n/a | n/a | no | loaded at runtime from plugin modules |

What the survey settles:

- **Pattern-as-data written in the target language's own syntax, with metavariables, is the
  consensus design.** Three independent tools converged on it. Sprout should not invent a third
  notation.
- **A repeated metavariable must bind equal code.** Semgrep states this explicitly ("Detect useless
  assignments", `$X = $Y` then `$X = $Z`). The engine therefore needs expression equality, which it
  gets as the zero-hole case of the matcher.
- **hlint is the near-exact precedent**, including the part Sprout needs most: `hlint --find` reads
  a module and *emits* hints derived from its definitions. That is this design's §5.
- **A semantics-changing suggestion is annotated, not withheld.** hlint attaches a note. §6 adopts
  this, having first designed the opposite and found it wrong.
- **Clippy's split is the right model for the existing wall**: lints compiled in, a TOML file for
  enable/disable and per-lint parameters. It validates keeping `staircase-of-doom` procedural while
  making `min_staircase_depth` configurable.

Sources: hlint README (ndmitchell/hlint), Semgrep pattern-syntax docs, ast-grep rule-config guide,
the Clippy book's configuration page, dylint README (trailofbits/dylint), ESLint custom-rules docs.

## 4. Architecture

Three separable pieces. Each is useful alone, and they land in this order.

**A. The matcher** — a new `stdlib/compiler/lint_pattern.sprout`. Structurally matches a pattern
`ast.Expr` against a subject `ast.Expr`, given a set of hole names, returning either no match or the
hole bindings. Nothing in `ast.sprout` provides structural equality today, so this module is also
the first place the compiler can ask "are these two expressions the same shape?".

**B. The combinator rule** — a list of prelude function names. At lint time the prelude is parsed,
each name's `FnDecl` is looked up, and its parameters become the holes of a pattern which is its
body (§5).

**C. The config file** — enable/disable, severity, parameters, path scoping (§8).

## 5. Deriving a pattern from a definition

The prelude is Sprout source the linter can already parse. For a single-expression function, the
body *is* the pattern and the parameters *are* the holes:

```
export fn result_from_maybe(err: e, value: Maybe a) -> Result e a =
  match value with
  | Just v -> Ok(v)
  | Nothing -> Err(err)

holes   = { err, value }
pattern = match ?value with | Just v -> Ok(v) | Nothing -> Err(?err)
```

Matching `match env.get(name) with | Just value -> Ok(value) | Nothing -> Err(concat(…))` binds
`?value := env.get(name)` and `?err := concat(…)`, and the finding reconstructs the call
`result_from_maybe(concat(…), env.get(name))`.

Two properties follow, and they are the reason to do it this way rather than with a hand-written
table:

- **A derived pattern cannot drift from the definition.** Change `result_from_maybe`'s body and the
  pattern changes in the same commit. A table would keep matching the old shape and keep suggesting
  a function that no longer has it.
- **Both spellings come free.** `let Just v = e else Err(x) in Ok(v)` desugars to a two-branch
  `MatchExpr` whose second pattern is a wildcard rather than `Nothing`. The matcher must therefore
  *not* require the second pattern to be `Nothing` — the pattern's own `Nothing` has to match a
  wildcard subject. One rule, both spellings.

The candidate list for v0, all verified present in `prelude.sprout`: `result_from_maybe` (:1516),
`maybe_with_default` (:1531), `result_with_default` (:1498). `guard` (:1524) is `if`-shaped rather
than `match`-shaped and needs no special casing — its body is an expression like any other — but its
corpus count is near zero, so it is a test case rather than a motivation. There is **no**
`maybe_from_result` in the prelude; do not let the rule suggest one.

### 5.1 What the matcher must get right

- **Alpha-equivalence.** The `v` in `Just v -> Ok(v)` is bound by the pattern, not a hole. A subject
  writing `Just value -> Ok(value)` must match. Carry a renaming map down through branches and
  lambdas.
- **Non-linear holes.** A hole appearing twice must bind equal subterms (Semgrep's rule). The
  zero-hole matcher is exactly the equality this needs.
- **Rewrap only.** `Just v -> Ok(f(v))` must **not** match `Just v -> Ok(v)`: `f(v)` is not `v`.
  This falls out of structural matching and needs no special rule — worth a negative test because
  a looser matcher gets it wrong and `http_server.sprout:290` (`content_length_result`, whose `Just`
  branch contains an `if`) is a live near-miss that must stay unreported.
- **Every `ast.Expr` variant.** A missed variant is a silent false negative, the worst failure for a
  coverage tool. `walk_expr` in `lint_rules.sprout` is modelled on `checker.sprout`'s
  `desugar_expr_no_ctx_i` for exactly this reason; the matcher needs the same discipline.

## 6. The evaluation-order problem

**Sprout is strict, so the rewrite is not always semantics-preserving.** The hand-rolled match
builds the error only on the failure path; `result_from_maybe(err, value)` builds it always. The
prelude's own comment says so — "both build `err` even on success, so keep it cheap". The rewrite
moves work from conditional to unconditional.

The first design here was for the engine to fire only when the hole's content is trivially cheap (a
literal, a variable, a constructor of those). **That was wrong**, and the corpus says so: the
`analysis_service_driver` error is `Err(string.concat("missing field: ", field))` — a call, and
therefore not trivial — so the rule would have silently dropped three of the four sites it exists
to find.

hlint's model is the right one. **Fire, and attach a note** when the bound expression is not
trivial:

```
lint/hand-rolled-combinator: this is `result_from_maybe(…)`
  note: the call evaluates its error argument eagerly; this match builds it only on failure
```

Severity stays `low`. The author decides. The engine cannot do better than this without effect rows,
and it has none (§2 non-goal 4) — a syntactic matcher cannot distinguish a pure `string.concat`
from something expensive or effectful, and pretending otherwise would be the unverified claim.

## 7. Guards against false positives

Four, each cheap, each a required test:

1. **The definition itself.** `prelude.sprout:1517` trivially matches the pattern derived from it.
   Skip a match that *is* the named function's own body.
2. **Shadowing.** A module defining its own top-level `result_from_maybe` must get no suggestion —
   `tests/stdlib/test_prelude_name_shadowing.spr` deliberately does exactly this. Scan the file's
   own top-level names first.
3. **Prelude-less files.** An importless file gets no prelude, so the suggested call would not
   compile.
4. **Not `drop_desugared_matches`.** That filter (`lint_rules.sprout:964`) drops findings whose
   source line does not literally begin with `match`, because `staircase-of-doom` was firing on
   already-flat `let..else` code. For *this* rule the desugared form is a **true** positive. The
   filter is keyed by rule id so the new rule is outside it by default — but that needs a comment,
   or someone will later generalise the filter and blind the rule to half its cases.

## 8. Config, and why it unblocks the `tests/` question

Clippy's shape: a `sprout-lint.toml` with enable/disable, severity override, per-rule parameters
(`min_staircase_depth` stops being a hardcoded `let`), and **path scoping**.

Path scoping is what makes the corpus tractable. Of the 139 measured sites, 69 are in `tests/`, and
they split two ways. Some are deliberate: `BACKLOG.md` records that two of the ten existing
`just lint` findings violate their rule because the raw form *is* the test subject. The rest are
incidental helpers — `first_or` in `test_eta_forwarding.spr:23` is called by the test, not tested by
it — where a rewrite is possible but risks perturbing the codegen shape the test exists to pin. A
lint rule cannot tell the two apart, and neither case wants a finding, so the tree is the right unit
of decision.

Suppression today is **file-level only** (`lint_ast` reads `file_directives(src)`); per-line
suppression is a separate open entry that `BACKLOG.md` itself calls "bigger than it looks".
**Path scoping in the config file sidesteps that dependency** for whole-tree decisions, which is
what the `tests/` case needs. Per-line suppression remains the answer for one deliberate site inside
an otherwise-linted file, and remains out of scope here.

## 9. What stays procedural

A pattern engine adds a declarative category beside the wall; it does not dissolve it.

| rule | pattern-expressible? | why |
|---|---|---|
| `redundant-vec-from-list` | likely | a shape |
| `list-shape-pattern` | likely | a shape |
| `list-prefix-pattern` | likely | a shape |
| `staircase-of-doom` | no | counts chain depth, checks whether a terminal branch uses its own payload |
| `multi-line-lambda-arg` | no | layout, not shape |
| `nullary-const-fn` | no | a predicate over the body, not a shape |
| `deprecated-brace-body` | no | token-level: the AST discards which delimiter produced the body |

Roughly three of seven. Nobody should migrate the other four, and this table exists so nobody tries.

## 10. Cost

Measured with the built `fmt_bin` on this worktree:

| what | time |
|---|---|
| lint a small file (`stdlib/bytes.sprout`) | 0.03s |
| lint `stdlib/prelude.sprout` (2126 lines, parse + all 7 rules) | 0.20s |

`just lint` is `rg --files -0 … | xargs -0 -n 1 fmt_bin lint` — **one process per file**, ~1128
files. A prelude parse per process adds up to 0.20s each, so **+3–4 minutes** against a current run
of roughly a minute. 0.20s is an upper bound: it includes running all seven rules over the prelude,
not just parsing it.

Two mitigations, in preference order. A **token pre-filter** — skip the prelude parse unless the
file contains `match` plus a constructor a pattern mentions — should remove nearly all of it, since
most files match nothing. **Batching** the lint invocation would also work but costs failure
isolation, and is not worth it if the pre-filter lands the number.

Measure again after A and B; a cost claim about the pre-filter would be unverified today.

## 11. Impact

- **Syntax:** none. v0 derives patterns from real definitions, so there is no hole notation and the
  lexer and parser are untouched. A hand-written pattern file (Semgrep/ast-grep style, for rules the
  prelude cannot express) would need one — deferred, not rejected.
- **Semantics, type system:** none. Lint only; no type information consulted.
- **Error messages:** one new rule id, `hand-rolled-combinator`, plus an optional `note:` line
  (§6). The note line is new output shape for `print_findings` in `fmt_driver.sprout`.
- **Compatibility:** `lint_ast(src: String)` gains the prelude's source (or a prepared pattern set),
  so `fmt_driver` changes with it. The existing `sprout-ignore` / `sprout-ignore-all` directives are
  unchanged. The config file is optional, and its defaults must reproduce today's behaviour exactly
  — a missing config cannot change what `just lint` reports.
- **CI:** none directly. `lint` is *not* in `.github/workflows/ci.yml`; it is pre-commit only, and
  `just lint` is already permanently red (ten findings across four files). Wiring it into CI is a
  separate `BACKLOG.md` entry with its own prerequisites, and this design does not move it.

## 12. Tests

Definition of Ready wants these failing first.

**Matcher** (`tests/stdlib/compiler/test_lint_pattern.spr`, new): hole binding; non-linear holes
rejected when the two subterms differ; alpha-equivalence over a branch-bound name; a `Nothing`
pattern matching a wildcard subject (the `let..else` case); `Ok(f(v))` *not* matching `Ok(v)`; one
case per `ast.Expr` variant, so a missed variant fails loudly rather than silently.

**Rule** (extends `tests/stdlib/compiler/test_lint_rules.spr`): each of the three combinators, in
both the `match` and `let..else` spellings; the eager-evaluation note present for a computed error
and absent for a literal; all four §7 guards; `content_length_result`'s shape reported clean.

**Config**: defaults reproduce today's findings; disable silences; a parameter override changes
`staircase-of-doom`'s depth; path scoping excludes a tree.

## 13. Rejected alternatives

- **A hand-written shape table** (the first proposal). Works, ~20 lines per combinator, and drifts
  from the prelude silently. §5 is strictly better for the same effort.
- **Firing only on trivially-cheap error expressions.** Would drop three of the four
  `result_from_maybe` sites. §6.
- **Matching post-typecheck, using effect rows to decide safety.** Correct, and far more machinery:
  the linter runs on a parse, not an inference. Revisit only if §6's note proves too noisy in
  practice.
- **A `?hole` pattern file in v0.** Needs lexer support for a rule nobody has asked for yet. The
  prelude-derived flavour covers the actual request.

## 14. Open questions

1. How does `fmt_driver` find the prelude? It takes a target path and has no stdlib-root argument.
   Either it gains one, or the pattern set is prepared by the caller.
2. Does the token pre-filter actually recover the 3–4 minutes? Unmeasured (§10).
3. Does the eager-evaluation note read as useful or as noise across all 139 sites? Only visible once
   the rule runs.
