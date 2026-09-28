# Lint rules as patterns (v0)

Status: **piece A (§4A, the matcher) implemented; B and C are design.** Experimental; no change to
`docs/spec-v0.md`, which does not describe the linter. Supersedes the "config file for per-rule
enable/disable" line in `BACKLOG.md`'s `Formatter/linter beyond the baseline` entry — that entry
points here.

Revision 6. §10's cost argument was wrong a third time, and in a way no more careful measurement
would have caught: its per-file numbers were right, but it multiplied them by 1145 without asking
where 1145 came from. It came from `xargs -0 -n 1`, because `fmt_bin lint` took one path and ignored
the rest. Bounding the batch (`-n 100`) removes the derivation cost that §10's generated module
existed to avoid, so §14 Q1 is closed without one. Two measured corrections came with it: process
startup is **not** negligible (~4.3ms × 1153 ≈ 5s), and batching *without* a bound is slower than
per-file, because killing the process was also keeping the heap young. §14 Q3 is closed too — an
`!{IO}` fallback typechecks and fires on the success path.

Revision 5. An ensemble review (`high`, 3 passes) found the matcher reading a **qualified name as one
atom**. `e.message` is a single `VarExpr`, so `free_vars` yielded `{"e.message"}`, which never
intersects `{"e"}` — a capture projecting a field off a branch binder passed §5.2a's closedness check
and would have been reported as a rewrite that does not compile. The head is the reference and the
rest are field labels (`infer.infer_var_or_field` splits the same way), so §4A now asks every
name question of the head. The same review found §5.2c's *other* direction still overstated: a
pattern-side `_` is not unconditional either.

Revision 4. A review of §4A's implementation found the (c) rule stated for constructors only, which
is not where it lives — refutability is, so a tuple pattern needed the same two conditions and a
variable pattern needed to fail them (§5.2c). §5.3's "never carries a hole" is now asserted rather
than assumed, and §4A records that a parameter's annotation and mode are part of a lambda's shape.
Line-number citations into `ast.sprout` and `lint_rules.sprout` became identifiers: consolidating
`pattern_names` moved every one of them, and a number drifts one way and never back
(`AGENTS.md` §Docs & Spec 6 already says this for `runtime/`).

Revision 3. Building §4A corrected §5.2c: a subject wildcard standing in for a pattern constructor
needs the constructor to *bind nothing*, which revision 2 left out, and stating only the position
rule admits `| Just u -> Ok(u)` against `| _ -> 0`. §12's matcher list is now
`tests/stdlib/compiler/test_lint_pattern.spr`, which also turned §5.2a's four corpus
counterexamples from prose into assertions.

Revision 2. An adversarial review of revision 1 found the matching half of the design sound and the
**substitution** half missing, which is where the two defects that would have shipped both live
(§5.2, §6). Revision 1 also claimed 3 of 7 existing rules were pattern-expressible; the real count is
0 (§9), and its cost section had the wrong baseline and a mitigation that contradicted §5 (§10).

The ask: make the linter catch a reinvented prelude combinator, and make adding the next one cost
one list entry instead of a new AST walk. Today's seven rules are each hand-written procedural code
in a 1166-line module, with no config and no way to add a rule as data.

## 1. Problem

Two problems, and only the second is about `result_from_maybe`.

**1a. Rules cost too much to add.** Every rule in `stdlib/compiler/lint_rules.sprout` is a bespoke
matcher plus a hook into `walk_expr`. Parameters are hardcoded (`min_staircase_depth`).
There is no enable/disable, no severity, no per-path scoping. A rule is a code change to the
compiler, which means the seed gate, `just test`, and a PR — for what is often one shape.

**1b. Reinvented combinators are invisible.** Measured over 1128 files under `stdlib/`, `ide/`,
`examples/` and `tests/` (11031 top-level functions), counting two-branch matches whose branches do
nothing but rewrap:

| shape | stdlib | examples | tests | total |
|---|---|---|---|---|
| `result_from_maybe` | 3 | 1 | 0 | **4** |
| `maybe_with_default` | 50 | 4 | 49 | **103** |
| `result_with_default` | 11 | 1 | 20 | **32** |

The `result_from_maybe` four are `prelude.sprout:1517` (its own definition),
`analysis_service_driver.sprout:98` and `:106` (the sites issue #378 owns), and
`examples/sentry_issue_browser_tui.sprout:11`. These counts come from a text scan that only sees the
literal `match` spelling, so **every row is a floor**: `let..else` desugars to the same AST
(`parser.sprout:1032`, `build_let_binding_match`) and a text scan cannot see it. There are 121
`let Just/Ok … else` sites the table therefore misses.

## 2. Goals and non-goals

**Goals.**

1. Adding a detected combinator costs one name in a list. No pattern written by hand.
2. Patterns are *derived from the prelude's own definitions*, so they cannot drift from them.
3. Rules are configurable: enable/disable, severity, per-rule parameters, path scoping.
4. **The engine never suggests a rewrite that would not compile, or that changes behaviour, without
   saying so.** Revision 1 treated this as a footnote. It is the hard half.

**Non-goals.**

1. **Rule logic supplied externally.** ESLint-style plugins need `eval`; dylint-style plugins need
   runtime library loading. Sprout has neither — there is no evaluator module under
   `stdlib/compiler/` (the REPL's `StatefulSession` carries imports and declarations as source text,
   not values, `compiler.sprout:207`), and `runtime/` contains no `dlopen`/`dlsym`/`LoadLibrary`
   call. Rule *code* is compiled in. Rule *data* is not.
2. **Autofix.** Needs an AST-aware rewriter; today's formatter is a line-based text transform.
   Stays in `BACKLOG.md`.
3. **Replacing the seven procedural rules.** See §9 — none of them is pattern-expressible.
4. **Type-directed matching.** The engine is syntactic and never consults inferred types or effect
   rows. §6 explains why effect rows would not have helped anyway.
5. **A `?hole` pattern syntax.** v0 derives patterns from real parsed definitions, so holes need no
   spelling of their own and the lexer is untouched. §13 keeps the door open.

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
- **A repeated metavariable must bind equal code.** Semgrep states this explicitly. The engine
  therefore needs expression equality, which it gets as the zero-hole case of the matcher.
- **hlint is the near-exact precedent**, including the part Sprout needs most: `hlint --find` reads
  a module and *emits* hints derived from its definitions. That is this design's §5.
- **Clippy's split is the right model for the existing wall**: lints compiled in, a TOML file for
  enable/disable and per-lint parameters. It validates keeping `staircase-of-doom` procedural while
  making `min_staircase_depth` configurable.

**What the survey does not settle, and revision 1 wrongly took from it:** hlint's "attach a note
rather than withhold the hint" is right for *laziness*, which is a performance and termination
question in a lazy language. It is the wrong model for a rewrite that is simply invalid. §6 splits
the cases instead of copying hlint's single answer.

Sources: hlint README (ndmitchell/hlint), Semgrep pattern-syntax docs, ast-grep rule-config guide,
the Clippy book's configuration page, dylint README (trailofbits/dylint), ESLint custom-rules docs.

## 4. Architecture

Three separable pieces. Each is useful alone, and they land in this order.

**A. The matcher** — a new `stdlib/compiler/lint_pattern.sprout`. Structurally matches a pattern
`ast.Expr` against a subject `ast.Expr`, given a set of hole names, returning either no match or the
hole bindings. It must also provide, because nothing else in the compiler does:

- structural equality over `ast.Expr` (the zero-hole case). `ast.sprout` has none; the nearest
  existing thing is `cse_census.expr_key`, which keys over `typed_ast`
  rather than `ast` and so cannot be reused. "Up to binder names" does **not** extend to a
  parameter's type annotation or its mode: those are shape, because substituting one lambda for
  another substitutes one annotation for the other. Mode compares by *ownership*, since
  `ModeDefault` and `ModeConsuming` are one mode and only one survives a round trip
  (`ast.mode_of_flags`). An effect row compares as a set;
- **free variables of an `ast.Expr`**, which §5.2's closedness check needs. Also absent over `ast`,
  but `dce.is_free` and `ast_to_ir.compute_free_vars` are
  close structural templates — both over `typed_ast`, and `dce`'s binder half already works on
  `ast.Pattern`. Both templates run *after* inference, where a field access is already a
  `GetFieldExpr` on a resolved binder. Over `ast` it is still one dotted `VarExpr`, so the free-vars
  walk must split it: the **head** is the name in scope, and reading the whole string instead is a
  silent hole in closedness rather than an approximation of it.

**B. The combinator rule** — a list of prelude function names, with patterns derived at build time
(§10).

**C. The config file** — enable/disable, severity, parameters, path scoping (§8).

## 5. Deriving a pattern from a definition

The prelude is Sprout source the linter can already parse. For an admissible body (§5.3), the body
*is* the pattern and the parameters *are* the holes:

```
export fn result_from_maybe(err: e, value: Maybe a) -> Result e a =
  match value with
  | Nothing -> Err(err)
  | Just unwrapped -> Ok(unwrapped)

holes   = { err, value }
pattern = match ?value with | Nothing -> Err(?err) | Just unwrapped -> Ok(unwrapped)
```

Matching `match env.get(name) with | Just value -> Ok(value) | Nothing -> Err(concat(…))` binds
`?value := env.get(name)` and `?err := concat(…)`, and the finding reconstructs
`result_from_maybe(concat(…), env.get(name))`.

Note the branch orders differ — the pattern above is `Nothing`-first and that subject is
`Just`-first. **The prelude is not internally consistent here**: `result_from_maybe`
(`prelude.sprout:1517`) is `Nothing`-first while `maybe_with_default` (`:1532`) is `Just`-first. So
branch permutation is a *requirement* of the matcher, not a refinement (§5.2).

Two properties follow, and they are why this beats a hand-written table:

- **A derived pattern cannot drift from the definition.** Change `result_from_maybe`'s body and the
  pattern changes in the same commit. A table would keep matching the old shape and keep suggesting
  a function that no longer has it.
- **Both spellings come free.** `let Just v = e else Err(x) in Ok(v)` desugars to a two-branch
  `MatchExpr` whose second pattern is `residual_or_wild` — a wildcard when the else is a constant
  (`parser.sprout:1032-1037`). One rule, both spellings, subject to §5.2's wildcard rule.

The candidate list for v0, all verified present in `prelude.sprout`: `result_from_maybe` (:1516),
`maybe_with_default` (:1531), `result_with_default` (:1498). `guard` (:1524) has body
`if condition then Ok(()) else Err(err)` (`:1525`), which derives fine and has one live corpus match
(`stdlib/fs.sprout:247`, where the error is bound outside the `if`, so §5.2 admits it) — a test case
rather than a motivation. There is **no** `maybe_from_result` in the prelude; the rule must not
suggest one.

### 5.1 What the matcher must get right

- **Alpha-equivalence.** The `unwrapped` in `Just unwrapped -> Ok(unwrapped)` is bound by the
  pattern, not a hole. A subject writing `Just value -> Ok(value)` must match. Carry a renaming map
  down through branches and lambdas.
- **Non-linear holes.** A hole appearing twice must bind equal subterms. The zero-hole matcher is
  exactly the equality this needs.
- **Rewrap only.** `Just v -> Ok(f(v))` must **not** match `Just v -> Ok(v)`. This falls out of
  structural matching, but it needs a negative test: `http_server.sprout:289-292`
  (`content_length_result`, whose `Just` branch contains an `if`) is a live near-miss that must stay
  unreported.
- **Every `ast.Expr` variant.** A missed variant is a silent false negative, the worst failure for a
  coverage tool. `walk_expr` in `lint_rules.sprout` is modelled on `desugar_expr_no_ctx_i`
  (`desugar_ctx.sprout:180`) for exactly this reason; the matcher needs the same discipline. Note
  that the comment above `lint_rules.walk_expr` misattributes that function to `checker.sprout`.

### 5.2 The substitution side: when a match may be reported at all

A match proves the subject has the combinator's *shape*. It does **not** prove that replacing the
subject with a call is valid. Three rules, each with a corpus counterexample that revision 1 would
have shipped.

**(a) Closedness.** A hole may only bind a subterm that is **closed with respect to every binder
between the pattern root and that hole's position**. Reconstructing the call hoists the bound
subterm out of those binders, so a free reference to one of them becomes unbound.

`result_with_default` (`prelude.sprout:1498-1501`) is `| Ok x -> x | Err _ -> fallback`, so
`?fallback` sits under the `Err _` branch. In `examples/json_demo.sprout:19-20`:

```sprout
| Ok text -> text
| Err err -> "refused: " ++ json.json_error_message(err)
```

`?fallback` would bind an expression referencing `err`, and
`result_with_default("refused: " ++ json.json_error_message(err), …)` does not compile. The same
shape is at `stdlib/repl.sprout:665-667` and in four `tests/stdlib/compiler/` files. This is why the
matcher needs a free-variables function (§4A) and not merely a renaming map.

Closedness is also what makes the candidate list safe to *extend* without re-auditing by hand.
`result_map` (`prelude.sprout:1483`) has hole `f` under binder `x` in `Ok(f(x))`: a subject
`| Ok x -> Ok(pair(x)(x))` binds `f := pair(x)`, which is not closed, and is correctly rejected.
Nothing about the *name* `result_map` tells you that; only the check does.

**(b) Branch permutation, bounded.** Constructor-headed branches whose patterns are pairwise
disjoint may be matched in any order — required, per §5's prelude inconsistency and because
`build_let_binding_match` always emits the bound pattern first. But permutation plus a lenient
wildcard is unsound together: `| _ -> Err(e) | Just v -> Ok(v)` would match `result_from_maybe`'s
pattern while always taking the `Err` branch. So: **a subject wildcard may stand in for a pattern
constructor only in last position.** The compiler rejects that inverted subject anyway
(`unreachable_check`, `infer.sprout:4990`), but lint runs on a parse alone
(`lint_rules.ast_findings`), so the engine cannot rely on that.

**(c) Wildcard direction.** Both directions need stating, and they are not symmetric.

A *pattern* branch matching a *subject* `_` carries **two** conditions, not one. It must be in
last position, per (b) — and it must **bind nothing**. `Nothing` qualifies. `Just u`
does not: `u` would have no counterpart on the subject side, while the pattern's body references it,
so there is nothing for that body to match against. Stating only the position rule admits
`| Just u -> Ok(u)` against `| _ -> 0`, which is not the same expression at all. Together the two
conditions are what catches the `let..else` spelling, whose desugaring puts a wildcard in the second
branch (`parser.sprout:1032-1037`).

**The conditions are about refutability, not about which `Pattern` variant spells it.** A tuple
pattern is refutable when a sub-pattern is and binds when a sub-pattern binds, so `| (Just _, 3) -> b`
needs the same two conditions `| Nothing -> b` does, and a variable pattern — which always binds —
never satisfies them. Stating the rule for constructors alone is what let the first implementation
admit `| (Just _, 3) -> ?b` against a *leading* `| _ -> y`, reporting the body of a branch the
subject can never reach. Literal patterns (`1`, `true`, `'c'`, `()`) admit no subject `_` at all;
that is conservative rather than inconsistent, because refusing a pairing is always sound.

A *pattern* `_` matching a *subject* pattern carries **one** condition, not none and not two. The
pattern's `_` catches whatever the earlier pattern branches missed, so the subject branch it pairs
with must catch the same complement: either that branch is **irrefutable**, or it is **last** and
exhaustiveness makes it the catch-all. Calling this direction unconditional — which revision 4 did —
admits `| A -> ?x | _ -> ?y` against `| B -> y | _ -> x`, two branches whose *third* constructor goes
opposite ways; both matches are exhaustive and neither has an unreachable branch, so nothing
downstream catches it.

What it does **not** require is that the subject bind nothing, and that asymmetry against (b) is the
point. A pattern `_` leaves no binder unpaired, so whatever the subject binds is caught by (a)
instead — exactly the `Err _` vs `Err err` case above. Keeping the two checks separate is what lets
that case be *matched* and then *rejected for the stated reason* rather than silently failing to
match; requiring "binds nothing" here instead would refuse it outright and take §5.2a's diagnostic
with it.

### 5.3 Which bodies are derivable

"Single-expression function" is vacuous: every `ast.FnDecl` body is one `Expr`.
The real precondition must be stated and enforced, because `where` and `let..in` **also** desugar to
a `MatchExpr` (`parser.sprout:1032-1040`, and the comment at `:1833-1836` records that `where` and
`let` deliberately share the node). A `let`-bodied combinator would derive to `match ?v with | x -> …`
and silently match only subjects also spelled with a binding.

Admissible: a body that is one `MatchExpr` with constructor-headed branches, or one `IfExpr`.
Everything else — a `do` block, a single-arm match from a `where`/`let` body, a body that rebinds a
parameter name (making the hole ambiguous) — must be **rejected loudly at derivation**, naming the
function. Today's four candidates pass; the prelude has six `let`-bodied exports that would not, so
this is not hypothetical.

Until that derivation check exists, the matcher enforces the half it can see. A hole occurring inside
a pattern's template, `do` block or comprehension **panics** rather than comparing literally, and so
does a hole reached through a field path (`?v.a`), which would have to capture "the object this field
was read off" — something no binding can express. The alternative in both cases is a rule that
silently never fires, and "never fires" is the failure a coverage tool cannot report on itself. Only
the pattern is checked, and a pattern is ours, so this can only ever name an authoring bug — never a
user's file. A prelude body that *does* project a field off a parameter (`fn f(r) = r.count`) is a
real and derivable shape; supporting it is §13's business, and until then it is refused loudly.

## 6. When the rewrite is invalid, not merely eager

Sprout is strict, so `result_from_maybe(err, value)` builds `err` always while the hand-rolled match
builds it only on failure. The rewrite moves work from conditional to unconditional. Revision 1
adopted hlint's answer — always fire, attach a note — after rejecting the opposite. **Both were
wrong.** There are three cases, not one.

**Refuse: the fallback cannot be evaluated eagerly at all.** `stdlib/crypto/p256.sprout:521-525`:

```sprout
let scalar_field =
  match modular.modulus(group_order) with
  | Just m -> m
  | Nothing -> panic("p256: the group order was rejected as a modulus (internal error)")
```

That is `maybe_with_default`'s exact shape, same branch order. The suggested
`maybe_with_default(panic(…), modular.modulus(group_order))` **panics unconditionally at module
init**. The corpus has 16 such `Nothing`/`Err -> panic(…)` arms. So: when a hole binding contains a
call to `panic`, do not suggest the rewrite. Report the shape if useful, but say the call is not
equivalent.

`panic : String -> a` is **pure** — it carries no effect row (`docs/guidelines.md` §2) — so §13's
deferred "decide safety from effect rows" alternative could never have caught this. A syntactic check
for a `panic` callee catches it for free, which is the whole reason to state the rule here rather
than defer it.

**Note: the fallback is evaluable but not a value.** Anything that is not
`ast.is_syntactic_value` — a call, a template, a concatenation — now runs on the
success path too. Word the note as a **behaviour change**, not a cost:

```
lint/hand-rolled-combinator: this is `result_from_maybe(…)`
  note: the call evaluates its error argument on every path; this match builds it only on failure
```

**Silent: the fallback is a syntactic value.** A literal, a variable, a constructor of those. No
note; the rewrite is equivalent.

An `!{IO}` expression **can** appear in such a fallback, confirmed against the checker: it typechecks
in an `!{IO}` caller, runs the effect on the success path where the fallback goes unused, and is
rejected in a pure one. It needs no rule of its own — it is not `ast.is_syntactic_value`, so the
*Note* case above already covers it — and §14 records why the shape does not arise in the corpus.

Worth keeping straight, because the two cases are caught by opposite means: the effect row sees an
`!{IO}` fallback perfectly and reports nothing wrong, since in an `!{IO}` caller the rewrite really
is type-correct and only the *behaviour* changes. `panic` is the reverse — pure in its signature, so
only a syntactic check for the callee finds it.

## 7. Guards against false positives

1. **The definition itself.** `prelude.sprout:1517` trivially matches the pattern derived from it.
   Skip a match that *is* the named function's own body.
2. **Shadowing, local and imported.** A module defining its own top-level `result_from_maybe` must
   get no suggestion — `tests/stdlib/test_prelude_name_shadowing.spr:47,49` deliberately defines
   both a local `guard` and a local `result_from_maybe`. A selective import
   (`import m (result_from_maybe)`) shadows just as effectively and must be covered too.
3. **Prelude-less files**, where the suggested call would not compile.
4. **Guards 2 and 3 cannot be done on the AST.** `ast_findings` parses
   `source.strip_headers(src)`, so the imports are gone before the AST
   exists. Both guards must read `header_lines`, the same text-level view
   the suppression directives use.
5. **Not `drop_desugared_matches`.** That filter (`lint_rules.drop_desugared_matches`) drops findings whose
   source line does not literally begin with `match`, because `staircase-of-doom` was firing on
   already-flat `let..else` code. For *this* rule the desugared form is a **true** positive. The
   filter is keyed on the `"staircase-of-doom"` rule id, so the new rule is outside it by default — but
   that needs a comment, or someone will later generalise the filter and blind the rule to half its
   cases.

## 8. Config, and why it unblocks the `tests/` question

Clippy's shape: a `sprout-lint.toml` with enable/disable, severity override, per-rule parameters
(`min_staircase_depth` stops being a hardcoded `let`), and **path scoping**.

Path scoping is what makes the corpus tractable. Of the 139 measured sites, 69 are in `tests/`, and
they split two ways. Some are deliberate — the raw form *is* the test subject. The rest are
incidental helpers — `first_or` in `test_eta_forwarding.spr:23` is called by the test, not tested by
it — where a rewrite is possible but risks perturbing the codegen shape the test exists to pin. A
lint rule cannot tell the two apart, and neither case wants a finding, so the tree is the right unit
of decision.

Suppression today is **file-level and header-only**: one directive, `sprout-ignore-all`, spelled
`sprout-ignore-all lint/<rule>: <reason>` with both the rule id and the reason mandatory
(`lint_rules.parse_ignore_all`, with `needs_rule_msg` and `needs_reason_msg`). Header-only is deliberate — the header is read as text
before `tokenize` runs, which is what lets a file that never lexes suppress `unparsed`. Per-*line*
suppression does not exist; `BACKLOG.md` fixes its future spelling as `# lint: allow(<rule>)` and
calls it "bigger than it looks". **Path scoping in the config file sidesteps that dependency** for
whole-tree decisions, which is what the `tests/` case needs.

## 9. What stays procedural: all seven

Revision 1 claimed three of the seven could become patterns. Reading them, none can.

| rule | why not |
|---|---|
| `redundant-vec-from-list` | delegates to `desugar_ctx.find_redundant_vec_wraps` (`desugar_ctx.sprout:436`), which threads a function-signature index to compute Vec *context*. A syntactic `vec_from_list([…])` pattern fires on every wrap, redundant or not — the opposite of what the rule means. |
| `list-shape-pattern` | matches on `ast.Pattern`, not `Expr` (`lint_rules.find_list_shape_in_pattern`); walks Cons chains of unbounded length; needs a source-text post-filter because `[a, b]` sugar produces identical nodes. |
| `list-prefix-pattern` | same family, same three reasons (`find_prefix_pattern_in_pattern`). |
| `staircase-of-doom` | counts chain depth and checks whether a terminal branch uses its own payload. |
| `multi-line-lambda-arg` | layout, not shape. |
| `nullary-const-fn` | a predicate over the body, not a shape. |
| `deprecated-brace-body` | token-level: the AST discards which delimiter produced the body. |

Two consequences. The engine is **additive only** — no migration, and this table exists so nobody
attempts one. And the first two rows raise a real architectural question: an `Expr`-hole matcher
cannot express a `Pattern` pattern or a repetition. v0 does not need either, but the module should be
shaped so `Pattern` holes can be added without inverting it.

## 10. Cost: derive at build time

Measured in this worktree:

| what | time |
|---|---|
| the `just lint` loop over 1145 files (the `lint` recipe in `justfile`) | **~17s** |
| `just lint` including building stage-1 and `fmt_bin` from the seed | 26.2s |
| lint `stdlib/prelude.sprout` (2126 lines, parse + all 7 rules) | 0.20s |
| lint `stdlib/bytes.sprout` | 0.02s |

Cost is roughly linear in lines, ~0.09ms/line.

**Process startup is not negligible**, which revision 5 asserted without measuring it: 1153 bare
`fmt_bin` startups cost 5s, or ~4.3ms each — a quarter of `lint` and nearly half of `fmt-check`.

Revision 1 said the baseline was "roughly a minute" and the penalty therefore about 4×. **Both were
wrong**: parsing the prelude in each of 1145 processes adds ~1145 × 0.19s ≈ 3.6 min to a ~17s
baseline, a **13×** slowdown. It also proposed a token pre-filter — skip the prelude parse unless the
file contains `match` — which **contradicts §5**: `stdlib/args.sprout:50-51` is
`let Just value = arg_get(a, key) else dflt in value`, has no `match` token, and is exactly the
`let..else` spelling §5 insists is a true positive. 121 sites are of that form.

**Bound the batch instead.** The 1145 was never a property of the problem — it was `xargs -0 -n 1`
in `justfile`, forced by a `lint` subcommand that took one path and ignored the rest. `fmt_cli`
parses a path *list*, so the multiplier is now a choice, and derivation can stay at startup where it
belongs. No generated module, no freshness gate, no staleness class.

The batch size is a measured optimum, and both directions from it cost time for opposite reasons —
per-file pays startup, unbounded pays GC, since a process that lints one file usually exits before
collecting while one that lints all of them collects repeatedly. Peak RSS stays flat near 60MB
throughout, so that is collection cost and not retention. Medians of 3 over 1153 files:

| batch | lint | processes | derivation (~0.19s × processes) | total once derivation lands |
|---|---|---|---|---|
| 1 | 21s | 1153 | 219s | ~240s |
| 10 | **16s** | 116 | 22s | ~38s |
| 25 | 23s | 47 | 9s | ~32s |
| **100** | 20s | 12 | 2.3s | **~22s** |
| unbounded | 26s | 1 | 0.2s | ~26s |

`-n 100` is the minimum of the last column, and neutral against the per-file baseline today. `-n 10`
is the fastest *now* and among the worst once the prelude is parsed per process — which is why the
number lives next to a comment in `justfile` explaining it, not as a bare literal.

Startup derivation also keeps the property the generated module would have had to gate for: the
pattern set cannot be stale, because it is rebuilt from `prelude.sprout` on every run.

## 11. Impact

- **Syntax:** none. v0 derives patterns from real definitions, so there is no hole notation and the
  lexer and parser are untouched.
- **Semantics, type system:** none. Lint only; no type information consulted.
- **Error messages:** one new rule id, `hand-rolled-combinator`, plus an optional `note:` line
  (§6) — new output shape for `print_findings` in `fmt_driver.sprout`.
- **Compatibility:** `lint_ast(src: String)` gains the derived pattern set. Its callers are
  `fmt_driver.sprout:69` and `tests/stdlib/compiler/test_lint_rules.spr:692-703`; there is no
  IDE or LSP caller today (checked `ide/`, `lsp_driver`, `sproutd_driver`). The existing
  `sprout-ignore-all` directive is unchanged. The config file is optional and its defaults must
  reproduce today's behaviour exactly.
- **`just lint`'s current state:** 12 findings, all `[unparsed]`, one each in 12
  `tests/conformance/parse_error/*.spr` — files that deliberately fail to parse — and **zero**
  AST-rule findings. `BACKLOG.md`'s "10 findings across 4 files, two violating deliberately" is
  stale, and so is anything derived from it. `lint` is not in `.github/workflows/ci.yml`; wiring it
  in is a separate `BACKLOG.md` entry that this design does not move.
- **Gates:** adding `stdlib/compiler/lint_pattern.sprout` and editing `lint_rules.sprout` are
  compiler-source changes, so AGENTS.md Definition of Done #7–#9 and #12 apply (smoke shapes, bundle
  smoke, seed, golden IR) **even though `fmt_bin` is outside `compile_driver`'s import closure** —
  the gate table is keyed on path, not on closure. Expect the seed to be at its fixed point apart
  from the fingerprint line, so `verify-bootstrap-fixed-point` + `seed-fp-ack` rather than a full
  reseed; verify rather than assume.

## 12. Tests

Definition of Ready wants these failing first.

**Matcher** — **done**, `tests/stdlib/compiler/test_lint_pattern.spr`, 102 cases: hole binding;
non-linear holes rejected when the subterms differ; alpha-equivalence over a branch-bound name;
branch permutation accepted for disjoint constructor patterns; a pattern `Nothing` matching a subject
`_` in last position, and **rejected** in first position (§5.2b); the same two conditions on a tuple
pattern and a variable pattern, and the mirror direction's one condition (§5.2c); a lambda's
parameter annotation and mode; a field path off a branch binder refused by closedness and one off a
*renamed* binder still alpha-equal; `Ok(f(v))` not matching `Ok(v)`; one case per `ast.Expr` variant,
and one *discriminating* case per payload those reflexive cases cannot see — a variant ignoring its
own field is equal to itself either way.

Every load-bearing check was mutated rather than trusted for being green. Disabling closedness fails
exactly its two cases; dropping the last-position condition exactly two; dropping the binds-nothing
condition exactly two; leaving the tuple arm unguarded exactly two; ignoring a parameter annotation
exactly four, its mode exactly one, and a `TypeApply` argument exactly one. No collateral failures in
any. Two mutations paid for themselves immediately: ignoring the `once` half of the mode comparison
failed **nothing**, which is how the two `once` cases got written, and forcing the other branch of
`sets_intersect`'s size test failed nothing, which is the proof that branch is a performance choice
and not a second answer. A suite that passes on its first run has not yet shown it can fail.

Revision 5's fixes were mutated back out the same way: reading a whole dotted name in `free_vars`
fails exactly three, in `ren_same` exactly three, admitting any subject under a pattern `_` exactly
two, and dropping the tuple arm's variable case exactly one. One of those is the useful kind —
narrowing the pattern-`_` guard to *last position only*, dropping the irrefutable half, fails four
cases including three of §5.2a's own, which is the evidence that the asymmetry between (b) and (c) is
load-bearing and not an oversight.

**Closedness** (§5.2a): `examples/json_demo.sprout:19`'s shape reported clean; `repl.sprout:665`'s
shape reported clean; a closed fallback in the same position still reported.

**Evaluation** (§6): `p256.sprout:521`'s shape **not** suggested (panic); a call fallback suggested
*with* the note; a literal fallback suggested *without* it.

**Derivation** (§5.3): a `let`-bodied prelude function rejected at derivation, by name; a body
rebinding a parameter name rejected.

**Rule**: each of the three combinators in both the `match` and `let..else` spellings, including
`args.sprout:50`'s form; all five §7 guards; `content_length_result` reported clean.

**Config**: defaults reproduce today's 12 findings; disable silences; a parameter override changes
`staircase-of-doom`'s depth; path scoping excludes a tree.

## 13. Rejected alternatives

- **A hand-written shape table.** Works, ~20 lines per combinator, and drifts from the prelude
  silently. §5 is strictly better for the same effort.
- **Firing only on trivially-cheap fallbacks.** Would drop three of the four `result_from_maybe`
  sites, since `Err(string.concat("field must be a string: ", field))`
  (`analysis_service_driver.sprout:98-100`) is a call. §6's three-way split replaces it.
- **Copying hlint's "always fire with a note".** Adequate for laziness changes in a lazy language;
  wrong for an unconditional `panic`. §6.
- **Deciding safety from effect rows.** Cannot work: `panic` is pure (`docs/guidelines.md` §2), so
  the worst case carries no effect. Revisit only for the `!{IO}` case §6 leaves open.
- **A per-process prelude parse with a token pre-filter.** 13× slower and blind to 121 `let..else`
  sites. §10.
- **A `?hole` pattern file in v0.** Needs lexer support for a rule nobody has asked for yet.

## 14. Open questions

1. Does the eager-evaluation note (§6, middle case) read as useful or as noise across the corpus?
   Only visible once the rule runs.
2. Should `Pattern` holes be in v0 after all? §9's first two rows say the matcher will eventually
   want them; nothing in the combinator rule does.

Closed:

- **Where the derivation step lives.** Neither: §10 bounds the batch instead, so derivation stays at
  startup and no generated artifact exists to keep fresh.
- **Whether an `!{IO}` expression can occupy a fallback position.** Yes. `maybe_with_default(side(),
  Just(42))` typechecks in an `!{IO}` caller and runs the effect **on the success path**, where the
  fallback is never used; a pure caller is rejected (`performs IO but is declared pure`, spec §7 rule
  8). This changes no rule — an effectful fallback is not `ast.is_syntactic_value`, so §6 already
  routes it to the *Note* case — and the shape does not occur: of 20 corpus sites with an effectful
  `Nothing`/`Err` arm, none pairs it with the bare `| Just x -> x` the rule requires (the arms either
  ignore the payload, as `stdlib/repl.sprout:28`, or consume without returning it, as
  `stdlib/compiler/checker.sprout:423`). That count is from single-line arms on adjacent lines, so
  the rule running over the corpus is what makes it exact.
