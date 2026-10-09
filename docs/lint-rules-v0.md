# Lint rules as patterns (v0)

Status: **pieces A (§4A, the matcher) and B (the `hand-rolled-combinator` rule) implemented; C, the
config file, is design.** The rule is live in `just lint` and pre-commit, and is **not** in CI —
`lint` never was, and at 12 findings it cannot be until suppression ships (`BACKLOG.md`).
Experimental; no change to `docs/spec-v0.md`, which does not describe the linter.
Supersedes the "config file for per-rule enable/disable" line in `BACKLOG.md`'s `Formatter/linter
beyond the baseline` entry — that entry points here.

Revision 9. An ensemble review (`high`, 3 passes) of revision 8 found four defects, all of the same
shape: a guard written against the symptom that had been seen rather than the mechanism that
produces it. The wrapper guard keyed on the TYPE name while the matcher dispatches on CONSTRUCTOR
names, so `type Box a = | Just a | Nothing` sailed through (§7 guard 2). `free_to_hoist` admitted
every `VarExpr`, though a top-level function read as a value eta-expands to a closure and a nullary
constructor is an interned-constructor call — so the "costs nothing" test passed two things that
cost an allocation each (§6). The shadowing guard stopped at top level, leaving a `guard` PARAMETER
to collect a suggestion that resolves to itself. And `walk_chain` recursed into a nested match
without going back through `walk_expr`, so no combinator inside a 2-branch chain was ever offered to
the rule at all — which means revision 8's "corpus at 0" was partly a measurement of where the
walker declined to look. The fix for the first is to derive the guard from the patterns; for the
next two, a local-binding environment threaded through the walk, which answers "is this name a local
or a top-level function" once and settles both. Two of revision 8's own 20 rewrites turn out to be
pessimisations under the corrected test (`checker.arrow_labels` over `Nil`, `test_fs.names_of` over
`[]`) and are reverted, and a fifth `sprout-ignore-all` that suppressed nothing is removed.

Revision 8. Revision 7 shipped the rule and measured it, and the measurement condemned it: of 165
findings, ~140 suggested a rewrite that is **slower**, because a combinator takes its wrapper as an
argument and an argument is boxed while a `match` on the producing call takes an unboxed worker
(`bench/results-2026-09-28-vec-box-tax.md`, 70–90x at 100k live). Revision 7 knew of a "hot loop"
carve-out and called it unguardable; the real criterion is not heat but **argument position**, and
that *is* syntactic. §6 and §7.4 now form one test — report only a rewrite that costs nothing —
which takes the rule from 165 findings over 98 files to **25 over 23**, 2 outside `tests/`. Those 25
are then swept — 20 helpers rewritten, 4 files suppressed with a reason — leaving `just lint` at
**12**, all of them `unparsed` fixtures, which is what it was before the rule existed. Applying the
21st suggestion broke the build and exposed a third shadowing case the guards missed: a file that
redefines `Maybe` (§7 guard 2). Nothing short of *applying* a suggestion would have found it.
Two corrections fell out: `ast.is_syntactic_value` is too broad for the fallback test (it admits a
constructor application, which allocates once hoisted), and `docs/idiomatic-sprout.md`'s headline
combinator example was itself the pessimised shape while its mechanism sentence said the peephole
was builtin-only. Both fixed here; the second is why the corpus had 165 sites to begin with.

Revision 7. Revision 6 closed §14 Q1 with "derivation stays at startup" and never asked what it
derives *from*. `fmt_bin` reads only its argv paths, so reading `prelude.sprout` would have tied the
rule to this repo — working for `just lint` and the pre-commit hook, absent everywhere else, and
with no good failure mode there. The definitions are now embedded in `lint_rules.sprout` and a test
pins them to the prelude by alpha-equality (§10). Two consequences: `lint_ast` keeps its signature,
and `-n 100` in `justfile` loses its rationale — `-n 10` is the measured optimum again. §7's guard 3
was also stale: it guarded "prelude-less files", a category `docs/prelude-scope-v0.md` abolished on
2026-08-20, so as written it tested a condition that is never true. The real opt-out is the
`no_prelude` directive.

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
literal `match` spelling, so **every row is a floor**: `let..else` means the same match
(`ast.let_bind_match`) and a text scan cannot see it. There are 121
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
- **Both spellings come free.** Lint reads bindings through `ast.elaborate_bindings`, so
  `let Just v = e else Err(x) in Ok(v)` arrives as a two-branch `MatchExpr` whose second pattern is
  the residual, or a wildcard when the else is a constant (`ast.let_bind_match`). One rule, both
  spellings, subject to §5.2's wildcard rule.

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
`ast.let_bind_match` always emits the bound pattern first. But permutation plus a lenient
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
wrong**, and so was revision 7's middle course of three outcomes: see below, and §7.4 for the cost
half. The rule reports one case, the free rewrite, and is silent on every other.

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

**The syntactic check is one hop deep, and that is a known gap.** A fallback that calls a helper
whose *body* is a panic — `lowering.eta_inner_dict_unreachable`, reached at `lowering.sprout:1064` —
reads as an ordinary call, so the rule reports it as merely eager when the rewrite would in fact
panic on every call. Closing it needs a call graph; the stdlib has 2–3 such helpers, so v0 does not
build one. Worth revisiting if autocorrect (§9) ever lands, because *suggesting* a bad rewrite and
*applying* one are not the same risk.

**Silent: the fallback is evaluable but not free.** Revision 8 removed the *note* case this section
used to describe. Reporting an eager rewrite advisorily produced 51 findings nobody should act on,
and a rule whose advice is mostly wrong teaches readers to skip it. A capture is now reportable only
when hoisting it is free — a literal, or a variable **that names a local binding** (`free_to_hoist`).
The scope qualifier is revision 9's, and it is what makes the predicate mean what it says: a nullary
constructor, `[]`, and a bare top-level function name are all spelled exactly like a variable, and
each costs an allocation when evaluated eagerly (an interned-constructor call for the first two, a
`sprout_alloc_closure` for the third). Reading every `VarExpr` as free let all three through. The
walk now carries the names bound at each point — parameters, `let`, lambda arguments, match and
comprehension patterns, do-binds — so the test is "is this name local", which is exactly the
question. It also settles `cfg.cached` against `string.trim`: both are one dotted `VarExpr` (§4A),
and only the root segment tells a field read on a local from a module-qualified function.

`ast.is_syntactic_value` is the wrong test and was the one used: it admits a lambda, a tuple and a
**constructor application**, each of which allocates once hoisted. `parser.sprout:1028`'s
`Nothing -> ast.WildcardPattern(pos)` was reported as exactly equivalent while the rewrite would
allocate on every call. So the predicate is local and narrower, and `panic(…)` needs no case
own — it is a `CallExpr`.

A dotted field read is free and needs no case of its own: `cfg.cached` is a single `VarExpr`, not a
base plus a field (§4A revision 5), so `GetFieldExpr` covers only a non-variable base like
`f(x).field`, which is not free. Admitting `GetFieldExpr` recursively was measured: zero extra
corpus sites.

An `!{IO}` expression **can** appear in such a fallback, confirmed against the checker: it typechecks
in an `!{IO}` caller, runs the effect on the success path where the fallback goes unused, and is
rejected in a pure one. It needs no rule of its own — it is not free to hoist — and §14 says why
the shape does not arise in the corpus.

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
   (`import m (result_from_maybe)`) shadows just as effectively and must be covered too. Three
   shapes each defeated a structural scan of the line, and each made the rule suggest the very
   name the file had shadowed: a list that **wraps** (neither half of `import m (a,\n b)` is a
   declaration on its own), a **comment inside** one, and a **`T(..)` group**, whose `)` ends the
   list early — the same bug `module_loader.parse_import_after_module` records on the load path.
   So `imports_a_combinator` does not parse a declaration at all: it strips comments, blanks
   `(`, `)` and `,`, and compares words. The header block holds only `module`, `import`,
   `no_prelude`, comments and blanks, and a module path keeps its dots, so the only bare word
   that can match is an imported name or an `as` alias — both genuine shadows.

   **Shadowing the TYPE is the other half, and revision 8 missed it until the sweep.** A file
   declaring its own `Maybe` or `Result` shadows the type the combinators are declared over, so the
   suggested call does not typecheck — the checker says `Type mismatch: Maybe vs Maybe`, naming the
   same word twice. **10 corpus files** redefine one (`examples/maybe_map.sprout`,
   `tests/conformance/run/instance_constraints.spr`, the three `test_type_name_collision_*.spr`, …),
   and `tests/stdlib/test_ir_codegen_unary_arith.spr` both redefines `Maybe` and carries a matching
   shape, so it was reported and the rewrite broke the build. `defines_a_wrapper_type` refuses a
   file declaring any of `wrapper_type_names` as a `type`, `record`, `alias` or `wrap`. Found only by
   *applying* a suggestion: the rule, its 30 tests and the corpus count were all green with it wrong.
   **Revision 9 adds the half that matters.** The type name is not what the matcher reads —
   `lint_pattern` dispatches on CONSTRUCTOR names, so `type Box a = | Just a | Nothing` is matched
   however its own head is spelled, and `type Tri = | Ok Int | Warn Int | Bad` collects a
   `result_with_default` suggestion while being no `Result` at all. `redefines_a_matched_ctor` keys
   on the constructor names taken FROM the derived patterns, so the guard and the matcher cannot
   disagree, and it reads them through `ast.decl_value_scopes` rather than a private enumeration.
   The same change fixes guard 2's shadowing half twice over: `decl_value_scopes` reports class
   methods, which the old `decl_bound_name` scored as `""`, and the walk's local-binding environment
   catches the parameter and local-`let` cases that no file-level guard can see.
3. **`no_prelude` files**, where the suggested call would not compile. Revision 6 said
   "prelude-less files" and meant a file with no named module in its import closure — a rule
   `docs/prelude-scope-v0.md` **deleted on 2026-08-20**, because it inferred the language available
   in a file from its import graph. The prelude is now unconditional and the opt-out is an explicit
   whole-file directive: a line that is exactly `no_prelude` (`source.no_prelude_directive`). So the
   guard survives, but a check written against the old rule would have tested a condition that is
   never true — protection in name only. There is no selective hiding, so the guard is whole-file.
4. **Not guardable: the escaping `Maybe`.** A combinator is a real call, so the wrapper is boxed to
   reach it, while a `match` on the producing call takes its unboxed worker. What decides it is
   **argument position**, not heat and not whether the callee is a builtin: the worker exists for
   any top-level fn with a concrete ADT result and composes through user-defined wrappers
   (`ast_to_ir.cpr_result_known`; it declines a polymorphic `Maybe a`). Measured at
   **+17.5 ns/read, rising to 110–135 ns with 100k live — 70–90x**
   (`bench/results-2026-09-28-vec-box-tax.md`). The carve-out is far wider than the "hot loop"
   `docs/idiomatic-sprout.md` used to describe: **every** combinator over a call scrutinee is a
   pessimisation, converters included — `result_from_maybe` over a call boxes its input and takes 4
   GC roots where the `match` takes a worker and 1. The eliminators are merely the starkest case,
   one allocation against none.

   **Revision 8 makes it guardable after all, and guards it.** "Is this hot" is not syntactic, but
   "did a call produce this wrapper" is: `scrutinee_is_in_hand` requires a `match` subject's
   scrutinee to be free to hoist. A `guard`-shaped pattern is exempt, because it consumes a Bool —
   an immediate, never boxed, so a computed condition costs nothing (checked in IR). Measured
   effect: 165 findings to 25, 99 files to 23. For a wrapper already in hand the two spellings are
   cost-equivalent — `sprout_tag`+`sprout_field` against one call, neither allocating nor rooting,
   and `-O2` inlines the call away entirely. `BACKLOG.md`'s P2 to extend the peephole through a
   known non-escaping combinator would retire the restriction and let the rule widen again.
5. **Guards 2 and 3 cannot be done on the AST.** `ast_findings` parses
   `source.strip_headers(src)`, so the imports are gone before the AST
   exists. Both guards must read `header_lines`, the same text-level view
   the suppression directives use.
6. **Not the staircase rule's written-match check.** `staircase-of-doom` counts only matches
   at a `match` keyword (`lint_rules.written_matches`, read from the tokens), because it was firing
   on already-flat `let..else` and `try` code. For *this* rule the desugared form is a **true**
   positive. The check lives in `walk_chain` and `walk_match`, so the new rule is outside it — but
   that needs a comment, or someone will later move it into the shared walk and blind the rule to
   half its cases.

## 8. Config, and why it unblocks the `tests/` question

Clippy's shape: a `sprout-lint.toml` with enable/disable, severity override, per-rule parameters
(`min_staircase_depth` stops being a hardcoded `let`), and **path scoping**.

Revision 8's narrowing did most of what path scoping was for, and the sweep did the rest: 165 sites
became 25, then 0. Config is no longer what stands between this rule and a green gate. What the
sweep confirmed is that the `tests/` split is real and needs no config to act on, because the two
halves want opposite treatment and both are cheap. Some are deliberate — the raw form *is* the test
subject, and four files say so in a `sprout-ignore-all` reason: `test_let_else.spr` (the `let..else`
spellings), `test_comprehension_parse.spr` (a `match` in element position), and the two type-alias
conformance fixtures. The rest are
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
| `list-shape-pattern` | matches on `ast.Pattern`, not `Expr` (`lint_rules.find_chain_roots` with `chain_terminates_in_nil`); walks Cons chains of unbounded length; needs a source-text post-filter because `[a, b]` sugar produces identical nodes. |
| `list-prefix-pattern` | same family, same three reasons (`find_chain_roots` with `chain_terminates_in_wildcard`). |
| `staircase-of-doom` | counts written matches in a chain (a binding adds none) and checks whether a terminal branch uses its own payload. |
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

`-n 100` was the minimum of the last column, and that column is now hypothetical: the derivation it
priced never landed, because §10 embedded the definitions instead. **Revision 9 re-measured on the
shipped binary** — best of 3 over 1165 files: `-n 1` 21.1s, `-n 10` **19.6s**, `-n 25` 21.4s,
`-n 100` 24.7s. So `-n 10` is the optimum with nothing hypothetical left in the comparison, and
`justfile` uses it. The bowl is shallow — 5.1s between best and worst — which is why the number
lives beside a comment saying what it was measured against rather than as a bare literal.

**Where the pattern source comes from — settled against the wrong question.** Revision 6 closed this
as "rebuilt from `prelude.sprout` on every run" without asking how `fmt_bin` *finds* that file. It
reads only the paths in argv; nothing in it locates a repo file. Both callers today happen to run
from the repo root (`just lint`, and `.githooks/pre-commit`, which hardcodes `./build/fmt_bin`), so
a relative path would work — **for this repo only**. In any other repo the file is absent, and each
way of handling that is bad: failing hard breaks downstream users, and skipping silently leaves the
rule permanently unfired there, which is §5.3's "never fires" failure applied to a whole repo.

So the four definitions are **carried in `lint_rules.sprout` as source text** and parsed at module
init. The rule then works anywhere, and derivation costs a ~20-line parse rather than a 2126-line
one — which also means `-n 100` in `justfile` bought nothing. Revision 9 measured that and set it to
`-n 10`; the table above carries both sets of numbers.

The cost is a second copy, so the anti-drift property §5 got structurally now has to be *enforced*:
`tests/stdlib/compiler/test_lint_combinators.spr` derives the four from the real prelude and
requires them alpha-equal to the embedded ones. Alpha-equality means a rename in the prelude does
not trip it — that is deliberate, since a rename changes no pattern — so the test catches semantic
drift, not cosmetic drift. Reading the prelude is safe *there* for the reason it was not safe in the
linter: the suite runs from the repo root, and no downstream repo runs it.

## 11. Impact

- **Syntax:** none. v0 derives patterns from real definitions, so there is no hole notation and the
  lexer and parser are untouched.
- **Semantics, type system:** none. Lint only; no type information consulted.
- **Error messages:** one new rule id, `hand-rolled-combinator`, plus an optional `note:` line
  (§6) — new output shape for `print_findings` in `fmt_driver.sprout`.
- **Compatibility:** `lint_ast(src: String)` keeps its signature — the embedded pattern set (§10) is
  a module-level `let`, so nothing is threaded through it. Its callers are
  `fmt_driver.sprout:69` and `tests/stdlib/compiler/test_lint_rules.spr:692-703`; there is no
  IDE or LSP caller today (checked `ide/`, `lsp_driver`, `sproutd_driver`). The existing
  `sprout-ignore-all` directive is unchanged. The config file is optional and its defaults must
  reproduce today's behaviour exactly.
- **`just lint`'s state, after revision 8's narrowing AND the sweep: 12** findings, all `[unparsed]`,
  one each in 12 `tests/conformance/parse_error/*.spr` (files that deliberately fail to parse).
  **Zero `hand-rolled-combinator`** — the rule is green on the corpus it was written against, which
  is the state a gate needs. The path there: 177 findings / 165 combinator over 98 files before
  narrowing (115 `maybe_with_default` / 44 `result_with_default` / 5 `result_from_maybe` / 1 `guard`,
  51 of them eager), then 37 / 25 over 23 files after it, then 12 / 0 after 20 helper rewrites and 4
  file-level suppressions. Count through `just lint`, not `lint_ast`: the latter skips
  `sprout-ignore-all`, so it totals higher.
  Before the rule the count was also 12, so this is what makes suppression a
  prerequisite rather than a nicety for putting `lint` in `.github/workflows/ci.yml` — still a
  separate `BACKLOG.md` entry, now with a real number in it.
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

**Evaluation** (§6, §7.4) — **done**: not suggested for a panicking fallback (`p256.sprout:521`), a
call fallback, or a constructor-application fallback (`parser.sprout:1028`); not suggested over a
call scrutinee; suggested for a literal fallback over a variable scrutinee, and for a `guard` whose
*condition* is a call, since a Bool is never boxed. Revision 9 adds the three `VarExpr` shapes that
are not variables: a bare top-level function name (a closure), a nullary constructor (an interned-
constructor call), and a module-qualified name, which is spelled exactly like the field read beside
it and separated from it only by whether the root segment is bound.

**Scope** (revision 9): a parameter, a local `let` and a class method each shadow a combinator and
suppress the suggestion; an unrelated class method does not, which is the control that proves the
first is testing the guard and not a parse failure — it was not, when first written. A combinator
nested inside a 2-branch chain is reached at all, which it was not: `walk_chain` recursed past
`walk_expr`, and the 3-branch case took the other path and reported, so the hole looked like
behaviour. Two constructor-reuse cases (`Just`/`Nothing` under another type name, `Ok` on a
non-`Result`) cover the guard the matcher's own dispatch requires.

**A case this list missed twice, both times found only by running the rule over the corpus.** First
the note landed on a computed *scrutinee*, which a match evaluates too — 139 of 165 findings wrong,
while every fixture held the scrutinee fixed as a plain variable. Then the scrutinee turned out to
matter for a second, opposite reason: a call there is the pessimisation, so those findings should not
exist at all. Both times the suite covered the axis it was designed around and was blind along the
one it never varied, and both times the corpus was the only thing that noticed.

**Derivation** (§5.3): a `let`-bodied prelude function rejected at derivation, by name; a body
rebinding a parameter name rejected.

**Rule** — **done**, `tests/stdlib/compiler/test_lint_combinators.spr`, 42 cases covering the
`match` and `let..else` spellings, the `if`-bodied `guard`, §5.2a closedness in both directions,
§6's and §7.4's free-rewrite cases and the §7 guards — including all three shapes that defeated the
line-structural import scan (wrapped list, comment inside the list, name after a `T(..)` group),
each of which must disable the rule, plus two near-misses that must NOT disable it
(`import m (guard_rail)`, and `maybe_with_defaults` on a continuation line), so the word scan
cannot decay into "a combinator name appears somewhere in the header".

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

1. Should `Pattern` holes be in v0 after all? §9's first two rows say the matcher will eventually
   want them; nothing in the combinator rule does.
2. **Q4. Should `wrapper_type_names` be derived?** Half-closed by revision 9, and the half it closed
   is the one that was load-bearing. The guard's *constructor* names are now derived from the
   patterns themselves (`matched_ctor_names`), so the names the matcher dispatches on and the names
   the guard refuses cannot drift apart — which was not a hypothetical, it was the defect. The
   *type* half is still a hand-written `wrapper_type_names`, and deriving it needs `Combinator` to
   keep each parameter's `ast.TypeExpr` (`Param String (Maybe TypeExpr) ParamMode`) plus a 6-variant
   walk collecting uppercase `TypeName` heads. Lower stakes now: a fifth combinator that forgot the
   type entry would still be caught by the constructor guard unless its wrapper shares no
   constructor name with `Maybe` or `Result`. Deferred on that basis.

Closed by measurement:

- **Does the eager-evaluation note read as useful or as noise?** Noise: 51 of 165 findings carried
  it and none was worth acting on. Revision 8 removed the note and stopped reporting the case (§6).

Closed:

- **Where the derivation step lives.** Neither: §10 bounds the batch instead, so derivation stays at
  startup and no generated artifact exists to keep fresh. Revision 7 then found the closure was
  incomplete — "at startup" did not say *from what*, and reading `prelude.sprout` only works in this
  repo. The definitions are embedded in `lint_rules.sprout` and a test pins them to the prelude
  (§10).
- **Whether an `!{IO}` expression can occupy a fallback position.** Yes. `maybe_with_default(side(),
  Just(42))` typechecks in an `!{IO}` caller and runs the effect **on the success path**, where the
  fallback is never used; a pure caller is rejected (`performs IO but is declared pure`, spec §7 rule
  8). This changes no rule — an effectful fallback is not `ast.is_syntactic_value`, so §6 already
  routes it to the *Note* case — and the shape does not occur: of 20 corpus sites with an effectful
  `Nothing`/`Err` arm, none pairs it with the bare `| Just x -> x` the rule requires (the arms either
  ignore the payload, as `stdlib/repl.sprout:28`, or consume without returning it, as
  `stdlib/compiler/checker.sprout:423`). That count is from single-line arms on adjacent lines, so
  the rule running over the corpus is what makes it exact.
