# List comprehensions — design (v0)

Status: **landed** (2026-09-05), experimental. Decisions D1–D7 below are settled
with the user and implemented. Normative surface: spec §5.10.

Supersedes the scope V1 Roadmap Candidate 1 in `BACKLOG.md` originally set
("single generator, optional guard, list-only, no pattern generators, no nested
or multi-generator") — see §2 for why that slice was rejected. That entry now
points here.

> **Revised twice on 2026-09-05, both times over D2 — where the elaboration
> runs.** The first review moved it from *mid-inference, into surface syntax* to
> *post-inference, over the typed tree*, fixing three defects that were really
> one: accumulator binders capturing user names, no rule for a source whose type
> is not yet solved, and generator patterns being illegal in the lambda-parameter
> position the desugar put them in. The second review then moved it **back inside
> inference**, because post-inference is *unsound* rather than merely riskier —
> `fn_linear_gate` runs `linear_check` during inference, so a later pass is
> invisible to it. The current design keeps the first review's three fixes (fresh
> position-derived binders, an explicit unsolved-source error, a one-arm `match`
> for non-variable patterns) while checking and elaborating in one place. §11
> records both reviews.

## 1. Problem statement

Sprout has no comprehension syntax. Element-wise construction is written with
data-last combinators and `|>`:

```sprout
xs |> list_filter(\n -> n > 2) |> list_map(\n -> n * n)
```

For the **single-generator** case this is already flat and idiomatic, and a
comprehension buys only the removal of two lambdas. Three things are genuinely
missing, and none are addressed by a single-generator form:

1. **A range cannot be a source.** The most natural comprehension there is —
   `[i * i for i in 1..n]` — has no short spelling, because `IntRange` is a
   distinct type from `List`. Today it is `range_fold` with a hand-threaded
   accumulator. (`range_to_list` exists but is used almost nowhere: every call
   site is in the range tests themselves, except `tests/stdlib/test_fold_while.spr:41`,
   which uses it to build a fixture.)
2. **The cartesian shape is nested and allocates per outer element.**
   `[(r, c) for r in rows, c in cols]` is
   `list_flat_map(\r -> list_map(\c -> (r, c), cols), rows)`, which builds and
   then concatenates one intermediate list *per element of `rows`*.
   `list_flat_map` currently has **zero call sites outside the prelude**, which
   is honest evidence in both directions: either the shape is not needed here,
   or it is painful enough that callers restructure around it. This proposal
   does not claim a measured pain point in this repository.
3. **The combinator chain is O(n) stack, twice.** `list_filter`
   (`prelude.sprout:218`) and `list_map_go` (`:195`) are both non-tail-recursive
   — each builds `Cons(…, recurse(…))`. The elaboration in §5 is tail-recursive
   throughout.

**What §5 does *not* buy**, stated plainly because the first draft overclaimed
it: against `filter |> map` the elaboration is a wash on both traversal count
(2 either way) and allocation (2k cells either way, since the fold's
accumulator is reversed into a fresh list). The wins are the O(1) stack, and
the absence of per-outer-element concatenation in the multi-generator case.

### Downstream survey (2026-09-05)

`uncharted-suns` is Sprout's only real user, so it is the one place to check
whether this feature retrofits onto existing code. It largely **does not**, and
that is worth stating plainly rather than leaving implied.

It contains **171** `list_fold` call sites and **zero** uses of `list_filter` or
`list_filter_map`. Its list-building folds fall into three groups, none of which
a comprehension can take over:

1. **`filter_map` shape** — `list_fold` with `match … | Just x -> Cons(x, acc) | Nothing -> acc`
   (`combat.sprout:1294`, `:1391`, `:1545`, `:1809`). D3 excludes exactly this on
   purpose: dropping elements must be said out loud. These sites are the argument
   *for* D3, not against it.
2. **Index-dependent** — `fold_indexed`, usually `if i < cap` (`run.sprout:77`,
   `debug_verbs.sprout:77`, `kit.sprout:201`, `:204`, `combat.sprout:1562`). A
   comprehension exposes no index.
3. **Accumulator-reading element** — `names.sprout:81` passes `acc` into the
   element expression itself. Structurally impossible in a comprehension, since
   the accumulator is not in scope.

Two conclusions follow. The value of comprehensions here is **prospective** —
new code, especially the `1..n` source of §1.1 — not a cleanup of what exists;
this proposal should not be sold as the latter. And the zero-versus-four split in
group 1 says `list_filter_map` is being hand-rolled because it is not known,
which makes D3's diagnostic naming it (§7) do real work beyond the rejection.

## 2. Goals and non-goals

**Goals**

- A comprehension form over `List` and `IntRange` sources.
- Multiple generators, with later generators able to depend on earlier binders.
- Boolean guards, attached to the generator whose scope they filter.
- An elaboration that runs in O(1) stack and allocates nothing per outer
  element.
- Diagnostics that name the fix, not just the rejection.

**Non-goals**

- No `Vec` sources in v0 (`vec_to_list` at the call site; the closed set in D2
  is designed to be widened later).
- No pattern generators — see D3. Refutable patterns are **rejected**, not
  filtered.
- No `Dict`/`Set`/generic-container sources; no user-extensible generator
  protocol (D2 explains why a class cannot express one today).
- No comprehension over `Maybe`/`Result`, no monad-generic comprehension.
- No `let` qualifier (Haskell's third qualifier form). Deferred; additive.
- No parallel/zip generators (Erlang's `&&`).

## 3. Prior-art survey

Verified against primary sources. Every row was read; nothing here is recalled.

| Language | Multiple generators | Guard | Refutable pattern in generator | Range as source |
|---|---|---|---|---|
| Haskell 2010 §3.11 | ✓ "nested, depth-first evaluation" | ✓ + `let` decls | ✓ **skips** | `[1..10]` is already a list |
| Python | ✓ multiple `for` *and* `if` | ✓ | ✗ no refutable binding form at all | `range(n)` is an iterable |
| Erlang | ✓ (+ `&&` zip generators) | ✓ | ✓ `<-` skips; `<:-` raises `badmatch` | — |
| Scala 2.13 §6.19 | ✓ | ✓ | ✓ **skips**, via `withFilter` | — |
| F# | ✓ (nested `for`) | ✓ | — | ✓ `[ for i in 1..10 -> i * i ]` |

Multiple generators and a guard are **universal — five for five**. No language
in the set shipped the single-generator-only slice `BACKLOG.md:3274` described,
which is why §2 takes multi-generator as the entry bar.

On the pattern question the wording matters:

- **Haskell**: *"Binding of variables occurs according to the normal pattern
  matching rules, and if a match fails then that element of the list is simply
  skipped over."*
- **Scala**: *"every generator `p <- e`, where `p` is not irrefutable for the
  type of `e` is replaced by `p <- e.withFilter { case p => true; case _ =>
  false }`"*
- **Erlang**: *"A relaxed generator ignores that term and continues on. A strict
  generator fails with an exception."*

**Python is not a vote.** Its `for` target is an irrefutable destructuring
target; a mismatch is a runtime error, not a skip. Having no refutable binding
form, it abstains. The honest tally is **4/4 among languages that have
refutable generator patterns**.

### 3.1 Why those four agree

The rationale is in the Haskell report as a translation equation rather than as
a policy statement:

```
[ e | p <- l,  Q ]  =  let ok p  = [ e | Q ]
                           ok _  = []
                       in concatMap ok l
```

`ok _ = []` is not a decision about pattern generators. It is forced: `ok` must
be total, its result type is `[b]`, and the only value the language can supply
without consulting the programmer is the monoid identity. **The container
supplies the failure value, so nothing needs to be asked.** Scala's `withFilter`
is the same move made explicit; Erlang's list generator is the same move again.

This also shows that skipping and Sprout's `let..else` are *not* opposite
disciplines, which an earlier framing of this design got wrong:

- `let Just x = m else fb` — the result type is an arbitrary `a`. There is **no
  zero**, so the language cannot invent a failure value and must ask. `else` is
  the answer.
- `[e for Just x in ms]` — the element contributes a `List b`, which **has** a
  zero meaning "contributes nothing".

One rule — *never invent a failure value* — with a canonical one available in
the second case and not the first.

### 3.2 Why the consensus is nevertheless not adopted

The zero answers *"what value?"*. It does not answer *"did you intend to?"*

Erlang re-opened exactly this after roughly 25 years.
[EEP 70](https://www.erlang.org/eeps/eep-0070) (Final, implemented in OTP 28)
adds strict generators because *"relaxed generators can hide the presence of
unexpected elements in the input data of a comprehension"*; the motivating case
is `[{User, Email} || #{user := User, email := Email} <- all_users()]` silently
dropping users with no email, which *"masks potentially corrupted data"*. The
semantics were never wrong. What is missing is that a reader cannot distinguish
*"I mean to drop these"* from *"I forgot these existed."*

Sources: [Haskell 2010 Report §3.11](https://www.haskell.org/onlinereport/haskell2010/haskellch3.html)
· [Python language reference](https://docs.python.org/3/reference/expressions.html)
· [Erlang list comprehensions](https://www.erlang.org/doc/system/list_comprehensions.html)
· [EEP 70](https://www.erlang.org/eeps/eep-0070)
· [Scala 2.13 spec §6](https://scala-lang.org/files/archive/spec/2.13/06-expressions.html)
· [F# lists](https://learn.microsoft.com/en-us/dotnet/fsharp/language-reference/lists)

Elm was considered as the closest comparator (beginner-oriented, combinator-first
ML) and **left out**: the 2013 request `elm/compiler#147` is closed with no
reachable maintainer comment, so its position could not be verified from a
primary source. An unverified row is worse than an absent one.

## 4. Decisions, and the implementation overview

### D1 — Surface syntax: `for` / `in` / `if`

```
comprehension ::= '[' expr 'for' generator { ',' generator } ']'
generator     ::= pattern 'in' expr { 'if' expr }
```

Guards attach to the generator they follow, with no separating comma; generators
are comma-separated. This makes a guard's scope syntactically obvious and lets an
outer guard skip the whole inner loop.

```sprout
[i * i for i in 1..n]
[i * i for i in 1..n if i > 2]
[(r, c) for r in rows, c in cols]
[(r, c) for r in rows if r > 0, c in cols if c != r]
[a * b for a in 1..3, b in 1..a]          # inner source depends on outer binder
```

**Why not Haskell's `[e | x <- xs]`.** Not because `|` is taken — in *expression*
position it is free (`parse_list_pattern` at `parser.sprout:411-432` has the
tail-pattern branch; `parse_list_literal` at `:1298-1307` has no `|` branch at
all, so `[e | …]` is currently just a parse error). The first draft's claim that
the spelling was "forced" was wrong. The actual reasons are weaker but still
sufficient: expression and pattern brackets should not read as the same shape
with different meanings, and a `match` in the head position would collide with
arm-`|` greediness (`[match m with | A -> 1 | x <- xs]` has no good reading).
The Python-style spelling avoids both and is what the `BACKLOG` scope already
assumed.

`for` becomes a reserved word. Verified free **in both repositories**:

- In-repo — across `stdlib/`, `examples/`, `tests/` and `testsupport/`, every
  occurrence of `for` is inside a comment or a string literal
  (`stdlib/template.sprout` uses `"for"` as *template-language* syntax in a
  string, which is unaffected).
- Downstream — `uncharted-suns`, the sole dogfooding consumer, was scanned with
  comments and both single- and multi-line backtick templates stripped. The only
  hits are GLSL `for` loops inside shader templates (`loam/planet.sprout:54`,
  `loam/nebula.sprout:53`), both within the backtick block spanning lines 31–88.
  String content, not identifiers.

Migration cost today is therefore zero, and grows with every month this is
deferred.

Parsing is single-token lookahead: after `[`, parse one expression, then `for`
means comprehension while `,` or `]` means list literal. A trailing `if` is
unambiguous because Sprout's `if` is always `if … then … else` and never infix,
so it cannot continue the preceding source expression. Guards terminate at `,`
or `]`, neither of which is an operator.

### D2 — Checked, then elaborated, both inside inference

> **Revised again after a second adversarial review.** The previous version had
> `infer` build a typed `TComprehension` node which a *post-inference pass* then
> rewrote. That is unsound — see "Why the elaboration must not be a later pass"
> below — and has been replaced. There is now no typed comprehension node at
> all.

The comprehension survives parsing as a real AST node. `infer_comprehension`
then does two things, in order:

1. **Check.** Walk the generators left to right to resolve each source against
   the closed set — which is what *selects the fold* — and to raise §7's
   diagnostics while the user's own expressions are still in hand.
2. **Elaborate.** Build the §5 fold form as ordinary **untyped** `ast.Expr` and
   infer that. The synthesized code goes through the same typechecker as
   hand-written code.

| Source type | Pattern binds | Fold used |
|---|---|---|
| `List a` | `a` | `list_fold` |
| `IntRange` | `Int` | `range_fold` |
| still unsolved | — | positioned error, §7 |
| anything else | — | positioned error, §7 |

**`a..b` is always ascending, and a crossed literal range is already a compile
error.** `..` lowers to `range_up(a, b)` unconditionally (`prelude.sprout:1788`),
so `5..1` does not mean "count down" and does not mean "empty" — it is rejected
by `static_empty_range` / `reversed_literal_range` (`infer.sprout:5165-5186`),
which fire when **both** bounds are static int literals and point the author at
`range_down(5, 1)`. Computed bounds (`n - 1`) are not flagged and may be empty at
runtime.

Comprehensions inherit this for free: the check runs on the `..` call itself,
regardless of the context it appears in, so `[e for i in 5..1]` is rejected with
the existing diagnostic and needs no comprehension-specific rule. Descending
iteration is written `range_down(hi, lo)`. This is worth stating because the
obvious reading of `[i * i for i in 5..1]` — "iterate down" — is wrong in Sprout,
and the *reason* it is safe to leave alone is that an existing check already
covers it.

#### Why the elaboration must not be a later pass

`fn_linear_gate` (`infer.sprout:8069-8075`) is called from `typecheck_decl`
(`:8697`, `:9175`), so **`linear_check` runs on each declaration's typed body
*during* inference**, not after it. A pass scheduled "after inference, before
`ast_to_ir`" therefore runs strictly *later* than every linear check.

Under the previous design that was not a gap to fill later — it was a live
soundness hole. `linear_check` would meet a `TComprehension` it had no rule for,
and the natural stub, `LinOk(Nil, Nil)`, claims "binds nothing, consumes
nothing". So:

```sprout
[release(r) for i in 1..3]   # consumes r — reported as consuming nothing
release(r)                   # …so this reads as the first consume: accepted
```

A double-consume compiles. The inverse also holds: a comprehension that is a
linear value's only consumer yields a false "never consumed".

Writing a real arm instead is not a small job, and it is the wrong job. The
rules it would need — reject a linear binder, reject a linear capture, shadow
the generator binders — are `lin_lambda`'s rules (`linear_check.sprout:1092-1112`),
because a comprehension *is* a nest of lambdas. Hand-copying them onto a node
that will be rewritten into the very construct they were written for, and then
keeping the copy in sync forever, is the signal that the node should not exist
at that point in the pipeline.

Elaborating inside inference means the synthesized fold lambdas are present when
the gate runs, so the existing rules apply unchanged. See D7 for what that
implies for users.

#### What this buys, beyond soundness

- **Mistakes are type errors, not miscompiles.** Hand-building typed nodes means
  hand-building `types.TFunc` chains (`types.sprout:112` — curried, one arrow per
  parameter, each carrying an effect row and an ownership). Nothing downstream
  re-verifies them: `verify_dispatch` checks only dictionary resolution, and
  `opt --passes=verify` passed on every "compiles clean, then SIGSEGVs" episode
  in `BACKLOG.md`, item 9 included. A new golden IR file is snapshotted
  presumed-correct, so it catches later drift, not initial wrongness.
- **Errors still point at user code.** The synthesized tree splices in the user's
  own element, guard and source expressions unchanged, carrying their original
  positions.
- **Precedent.** `deriving.sprout` synthesizes untyped AST for inference to type,
  and `desugar_ctx` wraps every template interpolation in a synthesized bare
  `to_string(…)` call on the bundled program. Post-bundle synthesis referencing
  bare prelude names is an existing, load-bearing pattern — it works because the
  prelude bundles under module name `""` (`bundler.sprout:734-738`), so
  `list_fold`, `range_fold`, `list_reverse`, `Cons` and `Nil` are uniquely the
  bare names in the bundled environment.

#### The cost, stated plainly

Each generator's source is inferred **twice** — once by the check phase, whose
typed output is discarded, and once as part of the synthesized tree. This is
compile time only. The synthesized tree embeds each source expression exactly
once, so runtime evaluation multiplicity is D4's, and the counters in
`tests/stdlib/test_comprehension_hygiene.spr` would catch any violation.

#### Superseded: the three defects that killed mid-inference elaboration once

An earlier review rejected elaborating inside inference on three grounds. Each
is addressed, and recorded here so the decision is not re-litigated from memory:

- **Capture** — fixed binder names collided with user names, so
  `[acc + 1 for acc in xs]` produced `list_fold(\ (acc, acc) -> …)`.
  **Fixed:** binders are position-derived and prefixed, `comp_tmp_name`
  producing `__cmp_acc{line}_{col}_{depth}` — the scheme `parser.do_tmp_name`
  (`parser.sprout:525-526`) already uses, plus a depth index so two generators
  of one comprehension cannot collide.
- **Patterns in parameter position** — a lambda parameter must be an identifier
  (`parse_param`, `parser.sprout:1414-1417`), so a generator pattern could not
  be one. **Fixed:** any pattern that is not a plain variable is destructured by
  a one-arm `match`, exactly as `parser.build_do_total` (`:548-555`) does for a
  no-`else` do-bind. A plain variable pattern still becomes the fold parameter
  directly, so the common case emits no match at all.
- **Under-determined sources** — parameter annotations are optional in v0
  (spec §291), so in `fn f(xs) = [x for x in xs]` the source may still be a
  metavariable. **Fixed, but honestly:** this was never solved by *waiting*. The
  source kind is resolved the moment the check phase reaches that generator,
  left to right — which is also what the shipped implementation does. If it is
  unresolved then, it is an error (§7), not a default. There is no legitimate
  container-polymorphic comprehension to support, since D2's set is closed, so
  "unsolved here" and "unsolved forever" coincide. An earlier draft claimed this
  worked because elaboration was "post-solve"; that framing was never accurate.

A **fourth** hazard, found by the second review and not present in the list
above: a local can shadow a bare prelude name the elaboration emits, as in
`let list_fold = 5 in [x * x for x in xs]`. Locals are not module-qualified, so
the synthesized call resolves to the local. Under the rejected typed-node design
this was a *silent miscompile*; here it is a type error, in the same
already-known wart class as shadowing `append` misdirecting a `Semigroup` error.
Pinned by `tests/conformance/type_error/comprehension_shadowed_prelude.spr`.

Three alternatives for the *dispatch* (as opposed to its timing) were rejected:

- **A generator class.** Not possible without a type-system change. `Foldable f`
  is kind `* -> *` while `IntRange` is `IntRange Int Int Int`
  (`prelude.sprout:79`) — monomorphic in `Int`, so it can never have an
  instance. A two-parameter `class Generator src elem` cannot dispatch either:
  instance keying is single-parameter throughout, of which
  `add_class_param` (`resolve.sprout:343-346`, keeping only the head parameter
  via `| [p | _] -> dict_set(class_name, p, acc)`) is one visible symptom rather
  than the whole proof.
- **A syntactic special-case** on the `IntRangeExpr` node (`ast.sprout:118`).
  Cheap, but it works only in the literal shape: `let r = 1..n in [i * i for i
  in r]` and `[i * i for i in bounds()]` would both fail with an error
  mentioning `List`.
- **Always materialise** via `range_to_list`. Inherits the syntactic limit above
  *and* adds a defect: `range_to_list_go` (`prelude.sprout:159-162`) is
  non-tail-recursive, so a large range would exhaust the stack.

A closed, enumerated set is consistent with existing Sprout precedent rather than
ad hoc: spec §5.9 enumerates `Maybe`/`Result` as the only short-circuiting
families and states outright that the behaviour is not user-extensible. Widening
the set later (`Vec`) is compatible; narrowing it would not be.

Nothing the elaboration synthesizes needs dictionaries — `list_fold`,
`range_fold`, `list_reverse` are plain functions and `Cons`/`Nil` are
constructors. Class-method calls inside the element expression or guards are
ordinary user code and are resolved exactly as they would be outside a
comprehension.

#### D2.1 — Which files change, measured

Sprout carries two parallel node families: untyped `ast.*Expr` and typed
`typed_ast.T*`. A new expression needs an arm in every walker over each family it
reaches. Measured by grepping the most recently threaded node, `RecordUpdateExpr`
/ `TRecordUpdate`:

| Family | Files that match on it |
|---|---|
| untyped `ast.RecordUpdateExpr` | `ast`, `parser`, `bundler`, `desugar_ctx`, `driver`, `iface_codec`, `lint_rules`, `infer` |
| typed `typed_ast.TRecordUpdate` | `typed_ast`, `infer`, `dce`, `ast_to_ir`, `resolve`, `lowering`, `verify_dispatch`, `linear_check` |

**Comprehensions pay the untyped row and none of the typed row.** The untyped
eight are unavoidable — the node is parsed, bundled, linted and interface-encoded
before inference ever sees it. The typed row is zero because the comprehension is
gone before a typed tree containing one can exist.

That asymmetry is not a scheduling nicety, and an earlier version of this section
got it wrong in an instructive way. It claimed a post-inference pass would cost
only three typed-side files because later passes "never see" the node. Measured,
that was false: **Sprout's exhaustiveness check is type-driven, not
reachability-driven**, so adding a constructor to `TypedExpr` makes every
non-catch-all `match` over `TypedExpr` a static error however unreachable the
case is. Introducing one broke the build in `lowering`, `dce` (four sites),
`ast_to_ir` (three sites), `linear_check`, and `typed_ast.typed_expr_pos` — eight
files of stubs, one of which (`linear_check`) could not be a stub at all without
being unsound.

### D3 — A refutable generator pattern is rejected, not filtered

Against the 4/4 consensus in §3, and for the reason in §3.2. Refutability is
judged by the rule spec **§5.2.1** already defines for `let..else` — *against the
pattern's type, not its shape* (§5.2.2:578 refers back to it) — so a `wrap`, a
tuple, or a single-constructor record pattern is irrefutable and allowed. The
check happens in `infer`, which has both the pattern and the element type.

```sprout
[x for Just x in ms]
# rejected — see §7 for the diagnostic

list_filter_map(\m -> m, ms)      # the explicit form, already in the prelude
```

**Honest limitation.** The escape hatch is only *direct* for `Maybe`.
`list_filter_map` (`prelude.sprout:227`) has type `f: a -> Maybe b`, so for any
other refutable pattern the user must hand-write the projection:

```sprout
[x for Cons x _ in xss]                                    # rejected
list_filter_map(\l -> match l with
                      | Cons x _ -> Just(x)
                      | _        -> Nothing, xss)          # what they write instead
```

That is more than "call the existing function", and §7 gives it its own
diagnostic wording rather than pretending the two cases are alike. It is still
the *explicit* form — the drop is written down — which is the point of D3.

Erlang needed new syntax (`<:-`) to recover explicitness because its
comprehension had held the filtering role for 25 years; Sprout has not shipped
one and can decline the role. This errs in the recoverable direction: rejecting
now leaves room to add skipping, or a marked form, later; shipping silent
skipping can never be narrowed.

### D4 — Effects flow; order and multiplicity are specified

`list_fold` and `range_fold` are effect-polymorphic (`step: b -> a -> b !{e}`),
so an effectful element expression or guard typechecks and the comprehension
carries the effect. Forbidding it would mean a purity restriction nothing else
in the language has; but `list_each` remains the idiom for running an action per
element, and the style guide should say so.

Sprout is strict, so the following are observable and therefore normative:

- **Order** — depth-first, leftmost generator outermost, left to right, matching
  Haskell's "nested, depth-first evaluation of the generators" and Python's
  "nesting from left to right".
- **Multiplicity** — the first generator's source expression is evaluated
  **once**; every subsequent generator's source is evaluated **once per
  iteration of the generators to its left**, because it may depend on their
  binders. So in `[e for x in xs, y in f(x)]`, `f` runs once per element of
  `xs`. A caller who wants a constant inner source must hoist it into a `let`.

### D5 — Prelude dependency

The elaboration emits prelude names (`list_fold`, `range_fold`, `list_reverse`,
`Cons`, `Nil`), so a comprehension does not work in an importless file that gets
no prelude. This is **not a new rule**: `[…]` list literals already desugar to
`Cons`/`Nil` (`parser.sprout:1309-1313`) and already have this property.

### D6 — The archetypal guard needs a remainder, and `%` is not it

Sprout has **no `%` operator** — the lexer's operator table (`lexer.sprout:343`)
does not list it and the character is rejected. The first draft's flagship
example, `[i * i for i in 1..n if i % 2 == 0]`, therefore did not compile, and
every example here uses comparisons or a named call instead.

*Corrected 2026-09-05.* This decision previously also claimed no `rem`/`mod`
exists, and filed a prelude `rem` to `BACKLOG.md` as a gap. That was wrong, and
wrong in an avoidable way: it checked `prelude.sprout` and `math.sprout` but not
`stdlib/math/int.sprout`, a *different module*, which exports
`mod(value, modulus) -> Maybe Int` (Euclidean, `Nothing` for a non-positive
modulus). The archetypal guard is writable today — verified:

```sprout
import stdlib.math.int as mint

fn evens(n: Int) -> List Int = [i for i in 1..n if mint.mod(i, 2) == Just(0)]
#  evens(10) == [2, 4, 6, 8, 10]
```

What survives the correction is narrower and still worth recording: the guard
costs an import and a `== Just(0)` unwrap where other languages write
`i % 2 == 0`, because `mod` is total over a modulus that may be zero.
`examples/fizzbuzz.sprout:1` defines its own `rem` rather than importing this,
which suggests the discoverability problem is real even though the function is
not missing. No backlog item is filed for a prelude `rem`; if one is ever wanted
the argument is ergonomics, not absence.

### D7 — A comprehension rejects linear values

Because the elaboration produces real lambdas before `fn_linear_gate` runs (D2),
a comprehension is judged by the rules already in `lin_lambda`
(`linear_check.sprout:1106-1147`): a lambda may not take a linear parameter, and
may not capture a linear value. So a comprehension that binds a linear value
from its source, or consumes one from the enclosing scope in its element or
guard, is **rejected**.

That is the right default and it is not a workaround. An element expression runs
once per element — zero to n times, statically unknown — and linear discipline
requires exactly once, so consuming a linear value there could never be correct.
It is the same reasoning `lin_lambda_captures` already encodes for any lambda.

The rejection is conservative in one known way. A fold's step lambda takes the
accumulator as a parameter, so threading a linear value *through the accumulator*
would be a legitimate exactly-once pattern — the one shape where "n times" can
still be linear. Sprout has no answer for linear folds anywhere today, and this
design deliberately does not invent one: comprehensions inherit whatever the
language decides for lambdas later.

The *rejection* is inherited, but the *wording* is not. `lin_lambda`'s own
messages describe a lambda, and against a comprehension all three of their parts
were wrong: they named a construct absent from the source, they advised declaring
a callee parameter `once` when the callee is the elaboration's own `list_fold`
and so cannot be annotated at all, and for a non-variable generator pattern they
printed a generated binder — a real leak, `linear lambda parameter
'__cmp_val5_39_0'`. `linear_check` therefore recognizes a synthesized fold (any
parameter carrying `ast.comp_binder_prefix`) and words both rejections in terms
of the generator:

```
comprehension generator 'f' binds a linear value, which is not yet supported
  (a comprehension elaborates to a fold, and higher-order linearity is deferred:
  the step function runs once per element, so a linear binder cannot yet be
  tracked across iterations); consume the elements in an explicit recursive
  function instead

linear value 'f' cannot be used inside a comprehension (…so the compiler cannot
  yet prove 'f' is consumed exactly once); consume it before the comprehension
  and use the result inside
```

When the fold parameter is generated rather than the user's own binder, the
subject degrades to `this comprehension generator` instead of naming it.

Both suggested rewrites are *pinned by a running test*
(`tests/stdlib/test_comprehension_linear_advice.spr`), not merely believed to
work. That is deliberate: advice being impossible to follow was the original
defect, and a message is the one place a broken claim can survive indefinitely
because nothing executes it. The rejections themselves are fixtures —
`tests/conformance/type_error/comprehension_linear_{binder,capture,ctor_binder}`.

### D8 — A comprehension coerces to `Vec`, like a list literal

Spec §5.5.1 wraps a list literal in `vec_from_list(…)` when the expected type is
`Vec`. A comprehension now does the same:

```sprout
fn takes_vec(v: Vec Int) -> Int = vec_length(v)

takes_vec([1, 2, 3])                # already worked
takes_vec([x * x for x in 1..3])    # D8: now works too

fn squares(n: Int) -> Vec Int = [i * i for i in 1..n]
```

Before D8 these two `[…]`-shaped expressions behaved differently in the same
argument slot, and the second failed with a bare `Type mismatch: Vec vs List`
naming no remedy — while the comprehension's *source* side already emitted
`— convert with vec_to_list(…)` for the mirror-image case.

**Why this is inside the existing rule, not a widening of it.** The
literal-only boundary exists for a stated soundness reason
(`coercions-and-literals-v1-draft.md:148`): `desugar_ctx` runs before inference,
so it cannot tell a `List`-typed name from a `Vec`-typed one, and an
unconditional wrap would turn `f(vec_empty())` into `vec_from_list(vec)`. A
syntactic `Cons`/`Nil` head was described there as "the **one shape** provably a
`List`" — true when written, stale now: a comprehension is provably a `List` too
(§5, its value is always a `List`). D8 applies the existing rule to a form that
did not exist when the rule was drawn. It is one arm in
`desugar_ctx.desugar_ctx_leaf_i`.

**Costed before deciding, because the first estimate was backwards.** The
initial read was that a hint would be cheap and the coercion a commitment. It is
the other way round:

| | cost |
|---|---|
| Coercion | **1 line** of code, in the function that already implements the rule |
| Runtime | **Zero** over the explicit form — emits `vec_from_list(list_reverse(range_fold(…)))`, exactly the hand-written lowering |
| Risk | **Nil.** Fires only when the expected type name is `Vec` *and* the leaf is a comprehension — a combination that was always a hard error, so no compiling program can change meaning |
| Golden corpus | **Untouched** — `desugar_ctx` is not in the bundled smoke shape |
| A comprehension-specific *hint* instead | Invasive: `infer_call_resolve` receives `arg_types`, not expressions, and unifies the whole call as one arrow, so it cannot say which argument mismatched |

**Prior art, verified against the GHC user's guide.** Haskell's `OverloadedLists`
overloads seven notations — `[]`, `[x]`, `[x,y,z]` and the four arithmetic-sequence
forms — plus list patterns; comprehensions are **not** among them. Python makes
the container syntactic (`{x for …}` vs `[x for …]`) rather than converting. So
prior art does not support this extension, and it is taken on the local argument
instead: Sprout's mechanism is a fixed `List → Vec` conversion directed by an
expected type, not literal polymorphism over a class, and its own soundness
boundary already covers comprehensions.

**What it does not buy.** The intermediate list is still built — a comprehension
targeting a `Vec` allocates cons cells and then walks them. A direct-to-`Vec`
lowering remains deferred (`coercions-and-literals-v1-draft.md` §5.A). D8 is
sugar, not an optimisation.

Pinned by `tests/stdlib/test_comprehension_vec_ctx.spr` (argument position,
return position, through an `if`, and a plain literal in the same slot).

## 5. Semantics — the elaboration

Written below in surface syntax for readability. The real pass operates on the
typed tree with **compiler-generated binders** (D2), so the accumulator names
shown as `acc`/`acc2` are fresh and cannot capture or be captured by user names.

Single generator with a guard:

```sprout
[e for x in src if p]

# elaborates to (FOLD = list_fold | range_fold, per D2):
list_reverse(FOLD(\ (acc, x) -> if p then Cons(e, acc) else acc, Nil, src))
```

Multiple generators nest, threading one accumulator so that exactly one reverse
runs at the end:

```sprout
[e for x in xs if p, y in ys if q]

list_reverse(
  list_fold(\ (acc, x) ->
    if p then
      list_fold(\ (acc2, y) -> if q then Cons(e, acc2) else acc2, acc, ys)
    else acc,
  Nil, xs))
```

A non-variable irrefutable pattern (tuple, `wrap`, single-constructor record)
becomes a fresh binder plus a destructuring bind inside the step, since a
pattern cannot occupy a parameter position (D2).

**Costs, stated precisely.** For a k-element result from an n-element source:

| | traversals | cons cells |
|---|---|---|
| `filter \|> map` | 2 | 2k (intermediate + result) |
| this elaboration | 2 (source, then reverse) | 2k (accumulator + reversed result) |
| `flat_map`-nested, m generators | — | 2k **plus** one intermediate list per outer element |

So against the single-generator chain it is a wash on both counts; the wins are
the **O(1) stack** (`list_fold_go` `:200`, `range_fold_go` `:167`,
`list_reverse_go` `:528` are all tail-recursive, against `list_filter` `:218`
and `list_map_go` `:195` which are not) and the **absence of per-outer-element
concatenation** in the multi-generator case.

A guard on an outer generator skips the entire inner loop, which is both the
correct reading of "nesting from left to right" and the efficient one.

Hand-evaluated for `xs = [1, 2]`, `ys = [a, b]`, no guards:
`[(x, y) for x in xs, y in ys]` → `[(1,a), (1,b), (2,a), (2,b)]`.

## 6. Type-system impact

No new types, no new classes, no unification changes.

- Generator sources are inferred left to right; source *k* is inferred in an
  environment extended with the binders of generators *1…k-1*, which is what
  makes `[a * b for a in 1..3, b in 1..a]` well-typed.
- Each guard must be `Bool`.
- The comprehension's type is `List b`, where `b` is the element expression's
  type.
- Its effect row is the join of the sources', guards', and element expression's
  rows (D4).
- Each source's type must be **solved** by the end of inference and must be one
  of the closed set (D2). An unsolved source is an error, not a default — there
  is no container-polymorphic comprehension to preserve.

Each generator's source is inferred twice — once by the check phase, once inside
the synthesized tree (D2). That is a deliberate, compile-time-only cost, and it
is what buys the elaboration passing through the ordinary typechecker. It has no
semantic consequences: fresh type variables from the discarded pass are orphans
that never reach the surviving tree, effect reports are recorded per declaration
rather than per expression (`infer.sprout:8694`, `:9132`), and the source
expression is embedded once, so runtime evaluation multiplicity is D4's.

## 7. Error-message impact

Five new diagnostics, all positioned:

1. **Unsupported source** — the closed set, naming the conversion:
   ```
   3:20: ERROR: check: a comprehension generator ranges over a `List` or an
   `IntRange`; `items` is a `Vec Int` — convert with `vec_to_list(items)`
   ```
2. **Under-determined source** — the type never got solved:
   ```
   3:20: ERROR: check: cannot tell what `xs` ranges over; a comprehension
   generator needs a known `List` or `IntRange` type — annotate the parameter
   ```
3. **Refutable pattern** — the rejection, the counterexample that proves it, and
   the way to drop elements deliberately (D3):
   ```
   9:19: ERROR: check: a generator pattern must be irrefutable, and this one does
   not match `Nothing`. To drop non-matching elements, say so with
   list_filter_map before the comprehension
   ```
   *Revised 2026-09-05, after review.* Two things were wrong here. The draft
   specified **two** wordings, split on whether the element type is `Maybe`
   (which has a one-call `list_filter_map(\m -> m, ms)`) — one wording ships, and
   the split is not worth a type-dispatch branch inside a diagnostic. More
   seriously, the shipped message spliced in `exhaustiveness_check`'s **whole
   sentence**, so it read `Non-exhaustive match on Maybe Int — no branch matches
   Nothing`: it reported a `match` against source that contains none. The witness
   is now taken from `coverage_gap` directly, which returns the bare
   counterexample. The `.err` fixtures pin the full sentence rather than a
   five-word prefix, so this drift cannot recur unnoticed.
4. **Non-`Bool` guard** — the standard unification error, positioned at the
   guard rather than at the whole comprehension.
5. **Linear value** — two shapes, a generator that binds one and an enclosing
   binder consumed inside. `linear_check` produces these instead of its
   lambda-worded pair once it recognizes a synthesized fold; see D7 for the full
   text and for why inheriting the wording (unlike inheriting the rejection) was
   not acceptable.

Plus parse errors for a missing `in`, a comprehension with no generator, and a
guard before any generator:
```
1:12: ERROR: parse: a comprehension needs at least one generator
(`<pat> in <expr>`) before any `if` guard
```

The guard check is **positional, not first-only**: it runs at every generator
position, so `[x for y in ys, if p]` gets this message too rather than falling
through to a bare `Expected pattern`. Fixture:
`tests/conformance/parse_error/comprehension_guard_after_comma`.

Two of these wordings are **inherited, not invented**, and the implementation
must keep them that way so the fixtures stay stable:

- The missing-`in` error comes from the parser's existing
  `expected_keyword` helper (`parser.sprout:157-164`), which emits
  `"Expected keyword " ++ kw ++ " at " ++ pos`. So the generator's `in` must be
  consumed with `expected_keyword(tokens, i, "in")` rather than a bespoke
  message.
- The non-`Bool` guard error is the unifier's standard `Type mismatch: Int vs
  Bool`, identical to the existing `if_branch_mismatch` fixture. A guard is an
  ordinary `Bool`-unified position and needs no special-casing.

## 8. Compatibility and migration

- **Reserving `for` is the only breaking change**, and it breaks nothing in
  either repository today (D1).
- Everything else is additive. No existing program changes meaning.
- **The keyword change reaches the formatter and linter.**
  `stdlib/compiler/formatter.sprout` is token-based, so `for` moving from
  `TokenIdentKind` to `TokenKeywordKind` changes its spacing decisions; a
  multi-line comprehension also needs a defined layout. `just fmt-check` runs in
  CI, so this is a gate, not a polish item.
- Editing `stdlib/compiler/` makes this seed-gated: `just refresh-seed` runs
  **before** `just test` and before `just ir-golden-diff`, or those gates
  silently run the pre-edit binary.
- The lexer change means a two-step bootstrap may be required if the committed
  seed predates it (`docs/debugging.md` §2-Step Bootstrap Protocol).

## 9. Tests and gates

**Parser** — single generator; multiple generators; guard; several guards;
comprehension-vs-list-literal disambiguation; comprehension-vs-tail-pattern
(`[a, b | rest]` must still parse as a pattern); nested comprehension in both
element and source position; a lambda in the element expression; `let … in` as a
source; the three parse errors in §7.

**Types** — `List` source; `IntRange` source; `Vec` source rejected; unsolved
source rejected (`fn f(xs) = [x for x in xs]`); refutable pattern rejected, both
diagnostic variants; non-`Bool` guard rejected; dependent inner source
(`b in 1..a`); irrefutable non-variable patterns accepted (tuple, `wrap`,
single-constructor record).

**Linearity** (D7) — a generator binding a linear value; a linear value from the
enclosing scope consumed in the element; a non-variable generator pattern over a
linear element, which is the case that used to print a generated binder name. The
paired regression is that a genuine hand-written lambda still gets the *lambda*
wording (`linear_lambda_param`, `lambda_capture_still_rejected`), since the new
messages are selected by a name prefix and a mis-selection would be silent. A
positive suite runs both rewrites the messages suggest, so the advice is gated
rather than asserted.

**Hygiene** — `[acc + 1 for acc in xs]` and `let acc = 10 in [acc + x for x in xs]`
must both compile and give the right answer. These are the first draft's bugs;
they are regression tests, not nice-to-haves.

**Runtime** — result order for two generators (depth-first, leftmost outermost);
outer guard skipping an inner loop; inner-source evaluation multiplicity (D4),
observed with an effectful source; an empty `List` source; an empty range built
from *computed* bounds (a literal `5..1` is a compile error, not an empty range —
see D2); a descending source via `range_down`; and a range large enough to prove
the O(1) stack claim in §5.

**Gates** (Definition of Done, in order):

1. `just fmt` over new `.spr`/`.sprout` files, staged (#4) — and `fmt-check`
   passes given the new keyword (§8).
2. `just refresh-seed`, staging `bootstrap/compile_driver.ll` (#9) — **before**
   the two gates below.
3. `mise exec -- just test`, full, no filter (#5).
4. `just compile-examples-stage1` (#6).
5. Smoke shapes via `--emit-ir` (#7) and bundle smoke via `--phase bundle` (#8),
   both triggered by editing `stdlib/compiler/`.
6. `just run-example-canary` (#11), triggered because `refresh-seed` rewrites
   `bootstrap/compile_driver.ll`. Covered by `just ci-fast-gates`.
7. `just ir-golden-diff`, then `ir-golden-snapshot` + stage `tests/golden/ir/`
   (#12). A new `examples/` file is itself a golden-corpus change
   (`scripts/ir_golden_diff.sh:103` walks the directory); for a purely additive
   example the snapshot must write **only** the new file.
8. `just seed-fp-ack` as its own isolated step if the fixed point holds, then
   commit (#13).

**Downstream** — `uncharted-suns` was surveyed for the `for` keyword (D1, clean)
and, on 2026-09-05, for the §1 shapes. The result is in §1 "Downstream survey":
essentially no existing code is a comprehension candidate, so the feature's value
there is prospective rather than a retrofit.

## 10. Spec and docs

- `docs/spec-v0.md` — a new subsection under §5 covering the grammar (D1), the
  closed generator set and the solved-type requirement (D2), the
  irrefutable-pattern rule (D3), and evaluation order and multiplicity (D4).
  Normative. Cross-reference §5.2.1, whose refutability judgement D3 reuses, and
  §5.9, whose closed-family precedent D2 follows.
- `docs/spec-v0.md` §5.5.1 — retitled and rewritten for D8: the `Vec`-context
  lowering now covers comprehensions as well as list literals, and states the
  boundary as "provably a `List` before inference" rather than "literal-only".
- `docs/coercions-and-literals-v1-draft.md` — amended where it called a
  `Cons`/`Nil` head "the one shape provably a `List`" (D8), and two acceptance
  lines that said "non-literal" where they meant "a `List`-typed variable".
- `examples/comprehension_demo.sprout` — the runnable tour: range source,
  guards, destructuring, dependent multi-generators (Pythagorean triples),
  nesting, and the `Vec` coercion. Adding it is itself a golden-corpus change
  (`scripts/ir_golden_diff.sh` walks `examples/`).
- `docs/idiomatic-sprout.md` — when to reach for a comprehension versus `|>`
  with combinators, and `list_each` for effects rather than a discarded
  comprehension (D4).
- `README.md` — comprehension syntax under "Iteration Combinators", and a line in
  the experimental-slices list. There is no reserved-word list in `README.md`;
  the keyword list lives in **spec §2**, which this change also corrects — it
  listed 12 keywords against `is_keyword`'s 20, so `for` was added along with the
  8 that were already missing (`export`, `class`, `instance`, `do`, `in`,
  `extern`, `deriving`, and `for` itself).
- `BACKLOG.md` — replace V1 Roadmap Candidate 1 with a pointer here; record the
  deferred `let` qualifier, `Vec` sources, and zip generators from §2. **No**
  prelude `rem`/`mod` item: see the correction in D6 — `stdlib.math.int.mod`
  already exists.

## 11. Review history

### Second review (2026-09-05) — the elaboration site

Reviewed after inference landed, to decide between a post-inference pass over a
typed `TComprehension` node and elaborating inside inference. Findings:

- **`linear_check` runs inside inference**, at the declaration boundary
  (`fn_linear_gate`, `infer.sprout:8069-8075`, called from `:8697`/`:9175`) — not
  after it. A post-inference pass is therefore invisible to it, and the stub arm
  it forces (`LinOk(Nil, Nil)`) lets a double-consume compile. This was the
  deciding finding; it turned the choice from a risk-appetite question into a
  soundness one. See D2's "Why the elaboration must not be a later pass".
- **Nothing downstream would catch bad hand-built types.** `verify_dispatch`
  checks dictionary resolution only; `opt --passes=verify` passed on every
  compiles-clean-then-SIGSEGVs episode in `BACKLOG.md`; a new golden is
  snapshotted presumed-correct.
- **A fourth hygiene hazard** neither the first review nor this design had: a
  local shadowing a bare prelude name the elaboration emits
  (`let list_fold = 5 in […]`). A type error here, a silent miscompile under the
  rejected design. Now pinned by a conformance fixture.
- **The "post-solve" framing was never accurate.** Source kinds are resolved as
  the walk reaches each generator, left to right, in both designs.
- **Double inference is compile-time only** — confirmed against the effect and
  field-obligation machinery; D4's runtime multiplicity is unaffected.

### First review (2026-09-05) — the design draft

First draft reviewed adversarially 2026-09-05. Beyond the D2 revision described
at the head of this document, the review corrected:

- `%` is not a Sprout operator — the flagship example did not compile (now D6).
  This review also asserted that no `rem`/`mod` exists anywhere; that half was
  **itself wrong** and is corrected in D6. `stdlib/math/int.sprout` exports `mod`.
- The refutability rule is spec **§5.2.1**, not §5.2.2 (miscited throughout).
- "Allocates only the result's cons cells" was false by 2×, and "strictly better
  on stack and passes" was half right — passes are a wash (now §1, §5).
- "`|` is unavailable, forced not chosen" was wrong: `|` is free in expression
  position. The conclusion survives on weaker grounds (now D1).
- Inner-source evaluation multiplicity was unspecified and is observable (now
  D4).
- `range_to_list` does have one non-range-test call site (now §1).
- The DoD gate list omitted smoke shapes, bundle smoke, seed staging, the
  example canary, `fmt`, and the full test run; the formatter/linter consequence
  of a new keyword was missed entirely (now §8, §9).
- D3's escape hatch was presented as uniformly available; it is direct only for
  `Maybe` (now D3, and §7 diagnostic 4).
- Reserving `for` was verified in-repo but not downstream. Now verified in both
  (D1) — `uncharted-suns` is clean.

Checked and found sound: all cited `prelude.sprout` line numbers and their
tail/non-tail characterisations; the `Foldable` kind argument; the `lexer.sprout`
keyword list; `ir_golden_diff.sh:103`; `list_flat_map` having no call sites
outside the prelude; spec §5.9's closed-family precedent; the §5 desugar's
semantics proper (element order, guard scoping, accumulator threading, single
reverse); D5's pre-existing importless caveat; and the grammar under
single-token lookahead against nested comprehensions, tuple elements, `..` as a
source, a lambda in the element, `let … in` as a source, and guard termination.

This document is non-normative until implemented; `docs/spec-v0.md` is the
normative source once it lands.
