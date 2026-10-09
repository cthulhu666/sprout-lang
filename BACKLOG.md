# Sprout Backlog

Purpose: track progress toward a usable, general-purpose, functional-first language.

Legend:
- Priority: `P0` (critical), `P1` (important), `P2` (later)
- Status: `[ ]` todo, `[~]` in progress, `[x]` done

One backlog lives outside this file: `.claude/skills/sprout-review/BACKLOG.md`, because that
skill is meant to be liftable into another repository whole. It is held to the same shape rules
by `just backlog-shape`. Nothing else may split off without the same justification.

## Backlog

### 1) Language Core and Safety

> Closed items are not kept here. What landed is in git history, in the design doc each entry names,
> and in `docs/spec-v0.md`. An entry is a title plus what is broken, where, and why it matters —
> detail belongs in a `docs/<feature>-v0.md`.

**Effects**

- [ ] `P2` **A peer join still swallows an OPEN effect variable, and nothing rules it out.**
  `unify_join` passes `NoExpectation`, so `bound_role` answers `NoBound` and `arrow_effect_meet`
  has no reject path — at every depth, since `unify_tapp` keeps the polarity. The five remaining
  sites (`++`, numeric, comparison, equality, ctor result) are fine only because their arrows are
  concrete; an open variable one side can bind to pure would launder. Find a reaching program, or
  route those sites through a fresh result variable as `if` now does.
  `docs/effect-subsumption-v0.md` §6.5b.
- [ ] `P3` **A type argument is judged covariantly, which is wrong for a mutable container.**
  `Ref` should be invariant in its argument. No reaching program is known — four shapes that
  laundered before bounded effect variables now reject, but via a bound travelling through
  the shared tyvar, not via correct variance. `docs/effect-subsumption-v0.md` §6.4, §6.5a.
- [ ] `P2` **`Foldable`'s `step` slot should be effect-polymorphic; `cond` must not be.** The policy
  in `docs/effect-polymorphism-policy-v0.md` admits `!{e}` where the contract pins order and
  multiplicity — true of `step` (left fold), false of `cond` (law lets an instance re-ask it).
  13 signatures, zero measured breakage; lands after effect subsumption.
- [ ] `P2` **Top-level `let` initializers are not checked for purity.** Spec §6 states the rule
  normatively and nothing checks it — `let boom = print("x")` type-checks. `LetDecl` discards the
  initializer's inferred effect and `--phase effects` does not enumerate top-level `let`s, so the
  first step is extending the census, not writing the check (215 top-level `let`s in tree). Couples
  to the value restriction at the same line (`docs/fundamentals-code-review-handoff-2026-07-03.md`
  §W3/§W6).
- [ ] `P3` **A trailing effect annotation on a non-arrow type is discarded** — `Int !{IO}` has no
  effect slot. §7 rule 9 now records that it carries no meaning; rejecting it outright would be
  more honest. The unknown-label check already reaches this position (`Int !{NOPE}` is rejected),
  so what remains is deciding whether a *well-formed* label is an error here. Verified by
  execution; fixtures under
  `tests/conformance/{type_error,run,parse_error}/effect_paren_arrow_annotation*`.
- [ ] `P3` **Return position cannot spell "returns an effectful function."**
  `parser.parse_return_type_cont` splits `-> (Int -> Int) !{IO}` into return type `Int -> Int` plus
  FnDecl effect `IO`, i.e. "this function performs IO". Parameter and stored positions express it
  fine; return position has no spelling for it.
- [ ] `P3` **Two effect annotations on one arrow resolve last-one-wins, silently.**
  `f: (Int -> Int !{e}) !{IO}` type-checks with the written `!{e}` gone and no diagnostic, and `!{}`
  would downgrade an arrow to pure the same way. Rule 9 admits one annotation per arrow, so this is
  a rejection case; `types.attach_arrow_effect` has no error channel, which is why it is not one
  yet. **The reachable spelling is an annotated alias**, spec §5.6.2's own example shape: given
  `type alias Handler = Int -> Int !{IO}`, a use site `h: Handler !{e}` types as `Int -> Int !{e}`,
  so the alias's declared `!{IO}` is erased by an annotation that reads as merely adding
  polymorphism, and the overwritten one is not visible at the use site at all.
- [ ] `P3` **An effect annotation followed by a further `->` does not parse.** `(a -> b) !{IO} -> C`
  fails at the `->` because `parse_type_expr_cont` returns after consuming the annotation without
  looking for one. Enclosing it works and is correct, so this is a grammar wart, not a missing
  capability. Fixture: `effect_annotation_before_arrow.spr`.
- [ ] `P3` **The effect report is one wave, not a fixed point.** An inferred effect is never written
  back to the env, so a caller of a mis-declared function is not flagged until that function is
  annotated and the compile repeated — `--phase effects` counts are a lower bound.
- [ ] `P3` **Rule 9 is not checked on a class METHOD SIGNATURE** — no body, so no effect report is
  recorded. Any instance of the class is rejected, so only a class declared and never instantiated
  escapes. `docs/effect-enforcement-v0.md` §13.

**Types and inference**

- [ ] `P2` **Allow polymorphic recursion for a CONSTRAINED declaration.** A complete signature
  enables it only when the declaration has no constraints; `fn f(n: Nest a) -> Int where Eq a`
  reports the occurs check. This is a constraint-solver feature, not a different self-binding
  (measured): a self-call at `Nest (a, a)` needs `Eq (a, a)`, deduced against the instance
  environment, which Sprout has no step for. Pinned by
  `tests/conformance/type_error/polymorphic_recursion_constrained.spr`.
- [ ] `P1` **A class dictionary keyed on a deferred field read is never resolved.** A key lambda
  reading a field of a record declared later in the file compiles, then fails at runtime:
  `vec_sort_by(\l -> l.index, rows)` above `type Row = (index: Int, …)` panics "dispatched
  through an unresolved typeclass dictionary for Ord__" on the first comparison. The field
  obligation is discharged at the declaration boundary, after `Ord k` was injected with `k` still
  open. Declaring the type first, or naming the key function, avoids it. Found by
  `bind_census.census_lines`; a five-line repro is the sort above plus a `main`.
- [ ] `P2` **Numeric defaulting fires before a deferred field obligation is discharged**
  (`infer.check_arith`, `infer.sprout:2782`), so `Double` fields under arithmetic get a spurious
  `Int vs Double` error and valid code is rejected. Incomplete fix, not a regression — it replaced
  a silent Int-arithmetic miscompile. Fix: make defaulting yield to an undischarged obligation.
  `docs/type-system-review-2026-08-13.md`.
- [ ] `P2` **Extend strict type-name validation to `ClassDecl` method signatures and `InstanceDecl`
  constraint types.** `validate_decl` matches `| _ -> Nothing` there. The `FnDecl` half is **stale**
  (measured 2026-08-18): an unresolved uppercase name in a param/return annotation is already
  rejected by the bundler's `unresolved_in_types`, so that half is consolidation, not a missing
  check. Confirm each position empirically first. The lowercase case is a separate gap, filed in
  §9.
- [ ] `P3` **Make a top-level `let` visible above its own declaration.** Forward-referencing one is
  `Unknown variable` — `pre_scan_fn_decls` has no `LetDecl` arm. Never worked; filed as the
  additive feature it is. Bigger than it looks: pre-scanning means inferring the RHS, so the
  principled form makes a top-level `let` a binding-group member under the value restriction
  (Haskell Report §4.5.1). Until then `LetDecl` is a reordering barrier and `spec-v0.md` §7 rule
  16 says so.
- [ ] `P3` **`head_name_matches` suffix-matches the final dotted segment** (`infer.sprout:1917`), so
  `where Sh (Box a)` binds to another module's same-named type — two modules defining `Box` are
  indistinguishable and the scan takes whichever argument comes first. Fix: qualify the head at
  canonicalization time so the comparison is exact.
- [ ] `P3` **Full Maranget usefulness matrix for product exhaustiveness.**
  `(true,true)|(false,false)` on `(Bool,Bool)` is not yet rejected; sound to over-accept per spec
  §5.5.

**Bindings, patterns and surface syntax**

- [ ] `P2` **`try` + `Propagate`: migrate off the fallible `<-`.** `try` landed (spec §5.9.1,
  experimental). Open: a phase that lists propagating binds (step 1a), codemod A adding `try` in
  all four repos, the flip that makes `<-` effect-only with the discard rule, codemod B, and `let`
  purity (steps 2–5 of `docs/try-propagate-v0.md` §8).
- [~] `P2` **Binding-level type annotations `let x : T = e`.** Phase 1 (top-level `let`) landed
  2026-07-30. `docs/binding-annotations-v0.md`; spec §5.2 (experimental).
  - [ ] `P2` **Phase 2 — `let…in` and `where` annotations.** Those bindings are desugared and
    have no dedicated AST node to hang an annotation on, so it is materially more invasive than the
    `LetDecl` field. Specify separately (`docs/binding-annotations-v0.md` §5).
  - [ ] `P3` **do-block `let`/`<-` step annotations.** `DoLetStep` has `LetDecl`'s shape and could
    carry a `Maybe TypeExpr` the same way; deferred with Phase 2, which shares the surface.
  - [ ] `P2` **Anonymous `any C` introduction — `let row : List (any C) = …`.** Needs a
    type-directed rewrite boxing each element into a per-value dictionary, so it cannot ride the
    Phase-1 syntactic coercion. Belongs to the existentials arc (`docs/gadts-v0.md` §6).
- [ ] `P1` **A top-level `let`'s type annotation resolves only PRELUDE types.** `let x: mod.T = …`
  is rejected with "unknown type `mod.T` … add it to that module's import list" even where the
  module imports `mod` and a `fn` signature two lines away resolves that same name; a type declared
  in the SAME file fails identically, advising an import of itself. So spec §5.2's annotation cannot
  be written for a user type, which is most of them. That blocks the `fn` → `let` rewrite
  `lint/nullary-const-fn` recommends: carrying the return type across is what keeps a list-literal
  `Vec` from silently becoming a `List` (§5.5.1). Repro: `let favourite: Colour = Red` beneath the
  `type Colour` that declares it.
- [ ] `P2` **Ref sugar in do-notation:** `:=` for `ref_write`, `<~` for a ref-read bind step,
  `var x = expr` for `x <- ref_new(expr)`.
- [ ] `P2` **B1 — an inline multi-line `do`-block lambda as a call argument is a parse error.**
  `f(0, 4, \i -> do { _ <- g(i); h(i) })` fails with `Expected )`; single-expression lambdas parse.
  Forces every multi-statement iteration step to be lifted to a named function, which is the
  difference between a good and a maximal verbosity reduction at combinator call sites.
- [ ] `P3` **B3 — `;` as a statement separator in a `do` block lexes as an error.** Either
  implement it as newline-equivalent or drop it from the docs; the newline form works today.
- [ ] `P3` **`_` digit separator (`1_000_000`).** `_` is an identifier-start character, so `1_000`
  lexes as `1` then `_000`. A lexer decision of its own, deliberately excluded from radix literals.
- [ ] `P3` **Spec has no normative statement that prefix `-` is a unary operator.** `parse_unary`
  (`parser.sprout:1159`) implements it, and §6 describes overflow behaviour for "unary negation",
  but no section defines the grammar or precedence of prefix `-`/`!`. Found while wiring the
  §2 lexical carve-out (`docs/bigint-v0.md` §5.3) to a normative section and finding none to point
  to. `docs/spec-v0.md` §5 (Declarations and Expressions) is the likely home.
- [ ] `P3` **Sweep the driver staircases.** `analysis_service_driver.op_session_update` (Tier 1b
  pure-prefix) and `driver.run_file` (effectful head + pure tail). Neither is in the compiler's
  closure, so the self-host fixed point does not guard them — each needs its own behavioural test
  first, and `run_file`'s is disproportionate for a debug driver (consider leaving it).
- [ ] `P4` **`staircase-of-doom` lint false-positives on inverted alternatives chains.** The false
  positive is an ambiguous-polarity chain where both branches descend, so `classify` picks the
  binding side and the real descent lands in the `else`. Candidate fix: in `walk_chain`, do not
  treat a node as flattenable when the terminal branch's body `is_two_branch_match`. With the
  `run_file` companion above, these are the only reason two commits needed `--no-verify`.
  Seed-fp-ack, not a full reseed.
- [ ] `P4` **Effectful-`let..else` surface (B) — unify `do` and `let..in`.** Would let a `let..in`
  block hold `<-` bindings, at the cost of reclassifying `let..in` as sometimes-effectful. Revisit
  only if the two-construct split proves to be friction (`docs/effectful-let-else-v0.md` §5).

**Existentials / GADTs**

- [ ] `P4` **Existentials — `validate_ctor_where` only checks the FIRST constraint arg.**
  `where Convert a b` with `b` unbound by the `exists` prefix is accepted. Latent, since
  multi-parameter classes are unsupported, but the validator's stated contract is not upheld. Fix:
  validate every `TypeName` argument. Stages 0a and 0b are complete; analysis, staging and prior art
  are in `docs/gadts-v0.md` (non-normative), spec §5.6 (experimental).
- [ ] `P4` **Existentials — extend constraint-head class validation to instance/class positions.**
  A variable-first `where a ToString` is rejected on a `FnDecl`; the same swap on an `InstanceDecl`
  context-constraint or a `ClassDecl` superclass silently drops the constraint.
- [ ] `P4` **Existentials — ambiguous construction gives an opaque arity error.** `Bag([])` leaves
  the constraint's var free, so no witness is injected and it surfaces as
  `ctor application has wrong arity`. It fails loudly, but the message should be a located "cannot
  determine which `C` instance to pack". Unique to compound-field existentials.
- [ ] `P4` **Existentials — first-mentioning field wildcard-bound while a later field binds the
  var.** Both passes select the first field *mentioning* the constrained var, so `T _ x` seeds no
  given and fails with "No instance". Consistent across both passes and loud, so not a soundness
  hole. Fix: pick the first VAR-BOUND mentioning field, in both passes, in lockstep.
- [ ] `P4` **Existentials Stage 1 — index refinement (full GADTs) (XL).** Needs an OutsideIn-style
  local-equality solver, bidirectional checking and constraint-aware exhaustiveness. Out of scope
  for v0/v1 (`docs/scoped-type-variables-analysis-2026-07-26.md`).

**Operators, intrinsics and dispatch**

- [ ] `P2` **Operators as signatured functions (B1) — design landed, impl deferred.** Primitive
  operators have no type signature; they are hand-coded in the typechecker, which is what let the
  `! 3 → 2` miscompile exist. The soundness hole is closed by a stopgap; this is the architecture
  follow-on — declare them as prelude `extern fn`, desugar at parse time, intercept the name in
  `ast_to_ir`. Runtime cost zero. Open sub-decision: whether arithmetic becomes a `Num` class.
  `docs/operators-v0.md`.
- [ ] `P2` **The comparison operators are the only CLOSED operator family and silently default an
  unresolved operand to `Int`.** `check_compare` *unifies* against `Int`, then `Char`, then
  `Double`; `check_eq` and `++` fall back to a class. So `"a" < "b"` is unimplementable,
  `fn lt(x, y) = x < y` silently infers `Int`, and `where Ord a` is rejected as "Signature too
  general". **Not a contained fix** — the non-destructive version rejects code that compiles
  today. **Hard constraint:** any `Ord` fallback must keep the `Double` short-circuit, or
  `nan < 1.0` becomes true (`docs/eq-ord-double-v0.md`).
- [ ] `P2` **B2 — recursive instance-method dispatch fails instance resolution.** A class method
  calling itself from its own instance body reports `No instance of <Class> for <T>`. Not
  effect-related (the pure control fails identically) — it is the tyvar-identity / dict-resolution
  gap. Blocks unifying the iteration combinators under a generic `Foldable`; also the message a
  legitimate self-recursive instance method hits.
- [ ] `P3` **Reject a user declaration that shadows an intercepted compiler intrinsic.** A
  headerless file defining `fn to_double`/`fn print`/`fn double_to_bits` has its call sites silently
  lowered to the intrinsic, body ignored, no error at any stage. **Sharper second form:**
  `translate_call` consults `captures` *before* the intrinsic names but `param_known` *after*, so a
  captured variable named `bit_and` wins while a parameter of the same name is intercepted. Fix both
  together. `docs/bitwise-int-ops-v0.md` §8.1.
- [ ] `P3` **Names the parser writes are captured by a module's own declaration or a local.**
  List and dict literals, list patterns and `>>`/`<<` use bare `Cons`, `Nil`, `dict_empty`,
  `dict_set`, `rcompose`, `lcompose` (spec §3.1; `own_cons_captures_list_literal.spr`). Writing
  `prelude.Cons` instead breaks `no_prelude` files and `compiler.compile_source`, which type-checks
  without bundling. That path also neither resolves a user's `prelude.X` nor renames locals, so a
  local still captures templates there. `docs/prelude-name-identity-v0.md` §5.
- [ ] `P2` **A user class method named like a prelude function captures built-in syntax.** Method
  names stay bare after bundling, so a method `list_reverse` breaks comprehensions
  (`class_method_captures_comprehension.spr`), and a method `branch` captures an unfused `try`
  (`class_method_captures_try.spr`; §9 Q9). Same root as spec §3.1's wrapper-symbol limit.
- [ ] `P3` **Reassess the `print` design** — should compiled `print` dispatch through `ToString`
  everywhere, instead of the type-erased runtime renderer? The full redesign regresses the
  importless loud-fail, risks bootstrap (every `print` in `stdlib/compiler/` needs an instance in
  scope), and is a normative spec change (`print(true)` flips `1` → `"true"`). Needs its own
  design doc. Decision: do it, or formally bless the intrinsic + surgical-rewrite split as
  permanent.
- [ ] `P2` **Overloaded literals: replace both hand-written coercions with one class + a defaulting
  rule.** `StringTemplate → String` and `List`-literal → `Vec` are two hardcoded branches of a
  pre-inference pass (`desugar_ctx.sprout`, 619 lines), so a third literal target (`Set`, `Bytes`,
  a logging frame, a user container) needs a compiler edit rather than an instance. Both fire only
  on literal *syntax*, so the general feature is `FromList`/`FromTemplate` classes picked by the
  expected type — which needs typeclass defaulting Sprout does not have (`infer.sprout:9181`).
  Gated on one open call: whether a default that changes asymptotics (`List` vs `Vec`) may be
  silent. Design, prior art and decision gate: `docs/overloaded-literals-v0.md`.

**Modules and prelude**

- [ ] `P3` **An `instance` method is the one way past record opacity.** An instance introduces no
  name, so `infer.group_module` cannot attribute it to a module and does not gate it: any method
  body may read any abstract record's fields (declare a class, instance it for the type, read
  them). Attributing to the head type was tried and reverted — a prelude-headed instance names no
  module, so a module could not read its OWN record inside one — and closed nothing, since an
  instance *for* an abstract record resolves to that record's module anyway. The fix is an
  orphan-instance rule, or the writing module on `ast.InstanceDecl` (74 sites, 18 files).
  Recorded in `spec-v0.md` §5.6.4.
- [ ] `P3` **An unresolved name under a renamed alias gets no "declared here" note.** The note that
  names the module declaring an unexported symbol (`infer.unresolved_name_hint`) searches the
  environment by last component alone, so it cannot confirm the reference's alias names the module
  it found. It requires the alias to equal the module's last segment — the default alias — and
  stays silent otherwise, because `sealed.trim` finding `stdlib.string.trim` produced a note that
  contradicted itself. `import demo.dup_one as d` then `d.hidden_helper()` therefore gets the bare
  error. Fix: hand the bundler's per-module alias map down to inference, and key the search on it
  instead of on the last segment. Pinned by `test_unresolved_name_hint.spr`'s renamed-alias case.
- [ ] `P2` **`--package-root` takes exactly one root, so a two-package app cannot be built.**
  An app importing its OWN modules and a library's has no second root to name. First hit by
  `repbit` (`app.*` plus `sprout-postgres`'s `postgres.*`); it symlinks the dependency into its own
  root — resolves correctly, but puts a build dependency in the source tree. **The plumbing is
  already there:** `extra_roots: List String` threads driver → `bundler` → `module_loader`. Only
  two places are single-root: `main`'s arg pattern (a literal `"--package-root", dir` pair), and
  `try_extra_roots`, which matches `[root | _]` and drops the tail. **The real work is the
  existence check** — it returns `root ++ "/" ++ path` unconditionally, so first-match-wins has
  nothing to fall through ON, and a wrong root still resolves silently. A name two roots both
  provide should be reported, not picked. Stopgap ahead of `docs/packaging-v0.md`.

- [ ] `P2` **Move `stdlib.compiler` to a dedicated tooling/compiler namespace** once the non-stdlib
  tooling-package model is settled.
- [ ] `P3` **Reconsider the prelude-bundling default (polarity + trigger).** The prelude is bundled
  iff some module has a non-empty `module` header, so it is opt-IN by accident of syntax and "do I
  get the stdlib?" is answered by an orthogonal question.
  `fn main() -> Unit !{IO} = print((1, true))` gets no stdlib and a hard link error for a reason
  invisible in the source. Proposed: implicit by default, with shadowing or a `no_prelude` pragma
  for the blank-slate cases. Design-doc-level.
- [ ] `P3` **No test-only visibility, so a test oracle must be `export`ed and ships in every
  consumer's IR.** `stdlib/math.sprout`'s four `*_strided` accuracy oracles cost
  `10_double_math.spr` ~7% of its IR. Not fixable by moving them into the test (they need private
  helpers, and duplicating those makes the oracle worse). Investigated and shelved, no decision
  taken: `docs/test-visibility-v0.md` — §9-10 has the unresolved comparison (Go's additive
  `export_test.go` shape vs the mirrored tree). The real gap is that Sprout has no build-mode
  file-selection stage.

**Codegen: TCO, CPR and scalar replacement**

- [ ] `P2` **Lift the i64-only TCO restriction.** `ast_to_ir.sprout:6427` gates TCO on
  `ret_ty == "i64"`, so a `Bool`- or tuple-returning self-tail-recursive function builds one native
  frame per call and overflows at depth. Extend the return-type predicate to `i1` and small structs,
  add the back-edge store coercions, add deep-recursion regressions for both shapes.
- [ ] `P2` **Scalar-replacement follow-ups.** Tuple-CPR and intra-function SRA landed;
  `docs/scalar-replacement-v0.md` Appendix B. Two separable extensions remain: **SRA beyond
  do-blocks** (extend to pure `let..in` and across a Maybe/Result do-bind, threading the SRA map
  through `translate_do_bind_*` instead of resetting it); **heap-field tuples**
  (`IRCallUnboxed{2,3}` slots holding heap values must be rooted at the call site, and `op_heap_def`
  must report multiple slots).
- [ ] `P2` **The CPR peephole unboxes a `Maybe` that a `match` consumes, and any wrapper that
  returns one, but not one passed as an argument — so an escaping `Maybe` costs 12–90x.**
  `match vec_get(i, v) with` lowers to a two-word `@vec_get_worker` and allocates nothing, and
  the workers compose through a user-defined wrapper; `maybe_with_default(0, vec_get(i, v))`
  builds the `Just` and costs **+17.5 ns/read** at every size and **+110–135 ns/read** with 100k
  live — the size dependence is collection cost, not allocation
  (`bench/results-2026-09-28-vec-box-tax.md`, harness `bench/vec_box/`). Extending it through a
  known non-escaping combinator, of which `maybe_with_default` is essentially the whole
  population, removes the gap and the hand-taught rule in `docs/idiomatic-sprout.md`.
- [ ] `P3` **Phase B mutual-TCO: a member that is both self-tail-recursive and in a heterogeneous
  mutual cycle keeps its mutual edge as a plain call.** `mutual_tco_rewrite_fn` skips any fn
  carrying an `IRTcoEntry`. No miscompile, but the mutual edge builds a native frame per
  iteration, so a long input overflows the stack: `json`'s string decoder died at 2^15 escapes
  until it was rewritten as one self-recursive loop. `docs/mutual-tco-phase-b-v0.md` §5a.
- [ ] `P3` **Phase B / Tier-2 CPR: `emit_repack_one` emits width-2 only.** A match-routed cycle
  member returning a ≥2-field-ctor ADT would drop fields. Unreachable today (worker routing is
  gated on max-ctor-arity ≤ 1), so latent. Widening needs `{tag, f0, f1}` and a `{i64,i64,i64}`
  sret — cf. `docs/archive/cpr-nested-product-unboxing-handoff-2026-06-28.md`.
  `docs/mutual-tco-phase-b-v0.md` §5b.
- [ ] `P3` **Phase B code-review follow-ups (2026-07-21).** Deferred, latent-or-cleanup; full
  disposition in `docs/mutual-tco-phase-b-v0.md` §12. #2 arity re-check on `pb_retarget_tail`; #3
  restore the per-edge all-i64 `params_match` gate; #4 broaden `pb_ret_unifiable` to `TTuple`
  (blocked: a tuple return has no `adt_index` entry); #5 confirm bare-vs-qualified callee names at
  the pre-lowering seam; #6 collapse the duplicated tail-position grammar walk; #9 restore T19's
  exact-name assertion; #10 the vacuous `build_ret_i64` eligibility map.
- [ ] `P3` **Mutual TCO Phase A and the may-trigger-GC fixpoint are quadratic in call depth.**
  `ast_to_ir.mutual_filter_targets` calls `mutual_reaches(g, f)` per same-arity tail callee:
  per-link arena bytes 387k / 486k / 700k at 100 -> 800 links. `ir_rooting.fixpoint_iterate` is
  Jacobi (a round reads only the last round's map), so a fact moves one call per round: a chain
  whose last link allocates costs 745k / 955k / 1.39M per link. 1.1% and 0.2% of self-compile
  allocation (2026-10-05). `scripts/scc_cost_gate.sh` alternates arity to dodge Phase A. Fix for
  both: one `scc.sccs_in_dependency_order` pass — Phase A compares components, the fixpoint walks
  them callees first — then drop the alternation from the gate's fixtures.
- [ ] `P3` **The lexer is a third of the compiler's self-compile allocation.** 35% sits under a
  `lexer.` frame in a stage-2 self-compile (2.72 GB, 2026-10-05): mostly `source.next` (11.4%)
  and `source.decode_char_at` / `decode_codepoint_at` (6.1% each), then
  `try_multi_char_symbol_worker` and `advance_position`. It is paid per character, so every compile
  pays it. Profile per token first. The unverified superlinear-lexing report in the list-literal
  root-pool entry is the same code: confirm or drop it in that pass.
- [ ] `P3` **Two `@fwd:` marker lookups still walk the whole type environment.**
  `infer.forwarded_tdict_for_tyvar` (after a direct-key miss) and `resolve_via_fwd_for_prog_var`
  turn `dict_entries(env)` into a list to find `@fwd:*:<class>` markers: 2.9% and 1.0% of
  self-compile allocation (2026-10-05). `dict_entries_with_prefix("@fwd:", env)` narrows each to
  the `@fwd:` run; the class is a key suffix, so going further needs a re-keyed marker.
- [ ] `P3` **Single traversal for the alloc-summary pre-pass vs the streaming emit.**
  `ir_pipeline.summarize_*` hand-duplicates the structure of `stream_*`; only the per-fn leaf action
  differs. Degrades safely (a missing summary entry over-roots), so this is drift, not soundness.
  Fix: one higher-order traversal both passes consume. Also: the new call-op classifiers use `_`
  catch-alls where `op_triggers_gc` is exhaustive.
- [ ] `P3` **Populate `IRGetTupleField`'s `IRType` kind for `VarPattern`-bound scalar tuple
  fields.** The op is kind-aware and the literal-pattern sites emit `IRTScalar`; the common `(x, y)`
  case still emits `IRTUnknown` because `bind_tuple_items` has no element-type info. Needs threading
  the scrutinee tuple's types from `translate_match`. Type-reclassification, ~0.2%-class win — do
  only if a measured hot path shows over-rooting.
- [ ] `P3` **Named `RuntimeLet` record for the runtime-let representation.** `classify_let_decls_ir`
  returns an anonymous positional pair. Blocked on records maturing in compiler code. Broader
  follow-on: adopt `GlobalName` at the other global-name sites so the wrap/unwrap seam shrinks.
- [ ] `P2` **`SPROUT_OPT_OFF` cannot reach the one pass known to do work.** The M0 switchboard sits
  at the `compiler.sprout` seam, but `dce.elim_unreachable` — which drops the ~98% of declarations a
  program cannot reach — runs inside `ir_pipeline.sprout:338`, below it. So the harness A/Bs `dle`,
  which removes 0 nodes from every real program (`bench/results-2026-09-11-opt.md`), and cannot A/B
  the pass whose numbers would be large. Add a `reach` pass name and thread an `OptConfig` into
  `compile_program_streaming` (two callers). `docs/opt-passes-v0.md` §M0.
- [ ] `P3` **No LICM over self-recursion, and LLVM cannot supply it.** The shadow stack makes every
  allocating function look `memory(readwrite)` to `FunctionAttrs`
  (`tests/golden/ir/examples__aoc_2025_day_1.sprout.ll:393-396`) and there is no LTO, so LLVM's
  LICM never touches a call. Sprout-side, loop-invariant means *an argument passed unchanged at
  every recursive call site*, hoisted via a worker/wrapper split — which can LOSE, since a longer
  live range holds more GC roots across more triggers. CSE (the sibling pass) was measured and
  declined: its sites were real but ran a handful of times each. Gate this on an A/B of a
  hand-applied hoist, not on a count of invariant parameters. `docs/opt-passes-v0.md` §M2.
- [ ] `P3` **The width-3 `sret` extern ABI is documented but no longer exists.** The sret call
  path lived in `codegen.sprout`, deleted with the direct backend (`5f29b9da`, 2026-07-12);
  `ir_lowering` declares only width-2 `_unboxed` externs. Nothing calls
  `native_set_to_list_unboxed` in `runtime/sprout_runtime.c`, yet `docs/compiler-internals.md`
  §CPR extern ABI and the status note of `docs/unboxed-adt-returns-v1-draft.md` describe the path
  as live. Delete the function and correct both docs.

**GC and runtime**

- [ ] `P2` **GC generational step** — bump-region nursery over the phase-2 regions, minor
  collections, write barrier, age/remembered bits already reserved. **Measured 2026-08-09, and it
  re-scoped the item:** the nursery ceiling is 97% for the compiler but 13-48% elsewhere, and
  outside the compiler GC is not the bottleneck at all (`SPROUT_GC_DISABLE=1` makes nqueens
  *slower*). Build it as a compiler/self-hosting optimisation or not at all. Barrier surface is
  `ref_write` + `vector_mutset` + `vector_push` and must be TYPED, not unconditional; non-moving
  caps the churn workloads regardless (Immix §5.3); the remembered set must be per-domain-shaped
  from day one. `docs/gc-generational-v0.md`.
- [ ] `P2` **GC-safety tooling follow-ups.** (5) Single source of truth for operand protection —
  `op_exposes_operands` is a hand-maintained list that can drift from runtime reality. (6) A
  GC-safety linter at the IR-rooting layer, modelling "allocates-then-stores ⇒ heap operands must
  be rooted". (7) Collision-proof root-slot naming (a reserved `%root.N` namespace, so an `idx_map`
  gap cannot collide with a body temp). (8) Output-parity gating: `scripts/ir_runtime_parity.sh` is
  gone with the direct backend, so `tests/golden/runtime/*.out` is the only output guard left and no
  gate runs it — a wrong-but-exit-0 render still passes CI.
- [ ] `P2` **C exhaustiveness for heap-kind dispatch** — make `SproutHeapKind` complete, switch on
  the enum with no `default:` arms, add `-Werror=switch` to the runtime clang lines. Converts silent
  wrong-dispatch after a new heap kind into a build break.
  `docs/gc-phase2-retro-handoff-2026-07-05.md` §1.
- [ ] `P3` **Compiler-inferred non-allocation (Koka-style) instead of a hardcoded list.**
  `is_nonallocating_read` is an unchecked assertion; manual annotation is explicitly not what is
  wanted. Target: interprocedural non-allocation inference as a fixpoint over the call graph. **Do
  first, ships alone:** a CI/DoD checker for the irreducible leaf C-extern facts (each tagged
  extern's body invokes no `sprout_make*`/`sprout_alloc*`/`sprout_gc_maybe_collect_threshold`),
  which the inference bottoms out on. Rejected: `@noalloc` annotations, return-type shape, `!{IO}`.
- [ ] `P3` **GC hardening follow-ups from the phase-2 review.** HDRCHECK per-kind layout checks —
  the kind-independent region-walk validation landed 2026-09-26 (boundaries must agree with the
  slotmap) and VECTOR got an aux-vs-`->data` check with it, leaving 7 of 10 kinds with none;
  `is_large` uniformization (~9 branch sites — do it before the generational step multiplies them);
  single ownership of the "keep ≥ 1 normal region" invariant; close the OBJ tag-write window by
  passing the tag into the alloc path.
- [ ] `P3` **Region allocator polish** — consider address-mask region lookup (1 MiB-aligned
  regions) to replace the binary search, if the profile shows it.
- [ ] `P3` **Sweep pass fusion** — pass 3 re-walks every region calling `slot_bytes` per live
  slot; fusible into pass 1 if freelist entries from released regions are handled. Only worth it if
  gc-profile shows sweep dominated by the extra pass.
- [ ] `P3` **Measure `str_eq`'s length fast-reject** now that membership is a region binary-search:
  two lookups guard every strcmp. Profile whether a short-string threshold or identifier interning
  is the better shape.

**Strings and data representation**

- [ ] `P3` **Static string literals: emit a length-prefix header word in the data segment** so all
  strings get O(1) length via a uniform header read. Codegen change → seed refresh.
- [ ] `P2` **`print` writes a string literal inside an ADT as a number.** `print(Err("boom"))`
  prints `Err(4373942584)`, while `print(Just(int_to_string(42) ++ "x"))` prints `Just(42x)`.
  `print_inline_value` in `runtime/sprout_runtime.c` renders a word as text only when
  `sprout_heap_lookup` finds a `CSTR` block; a literal lives in the data segment, so it falls
  through to `%lld`. A bare `print("s")` is fine: the compiler picks `print_str` from the type.
  The literal-header entry above would make literals recognisable at runtime.
- [ ] `P3` **Embedded-NUL string semantics.** `String` silently truncates at the first interior NUL
  (e.g. binary-ish `proc_run` output). Decide at spec level: keep "no interior NULs" and enforce
  loudly at ingestion boundaries, or move to length-delimited semantics.
- [ ] `P2` **Bit-packed record types + sized unsigned ints.** `packed type` with named bit-fields
  composing with `wrap`, plus `U8`…`U64`. The bitwise half landed as `stdlib.bits`
  (`docs/bitwise-int-ops-v0.md`); what remains needs a sub-word representation decision. Motivated
  by the GC header word (~14 consumers). Note the bitwise half already unblocks a pure-Sprout
  SHA-256 core; it does not bring the GC header within reach from Sprout.

**Double math**

- [ ] `P3` **`Double` `min`/`max`/`sign`.** Unblocked by `Ord Double` (2026-08-29). C's
  `fmin`/`fmax` return the non-NaN operand when exactly one is NaN, which differs from the prelude's
  `Ord Double` (NaN greatest, so a `compare`-based `max` propagates it). Prior art is split —
  C99/Rust discard, OCaml propagates. Pick one and say which in the doc comment.
- [ ] `P3` **Remaining elementary functions**, in rough demand order: `sinh`/`cosh`/`tanh`,
  `expm1`/`log1p`, `hypot`, `sigmoid`. None blocks anything; add on first real caller. Expect the
  endpoint-check class of bug `asin`/`acos` hit — small relative error does not imply in-range
  when the true value sits on a boundary. `docs/math-transcendental-v0.md` §15.
- [ ] `P3` **Export `is_finite` / `is_infinite` from `stdlib.math`.** The predicate now exists twice
  — `stdlib/json.sprout` defined its own rather than import `stdlib.math`, because per-program
  bundling makes that import cost +21% IR on a json-only program. Worth doing for third-party
  callers; revisit for `json` only if the prelude grows a finiteness predicate.
- [ ] `P3` **Euclidean `mod` vs C `fmod` naming across the two modules.** `stdlib.math.int.mod` is
  Euclidean; every mainstream float remainder is truncated. Either implement a Euclidean `Double`
  `mod` so the name means one thing, or name the truncated one distinctly and document why.
- [ ] `P2` **No way to ask for wrapping arithmetic, so hash code has to fake it.** `rng_hash2`
  genuinely wants mod-2^64 multiplication — it is a hash, and the residue is the answer. Since
  Stage 1 made `*` trap, it was rewritten to reduce every input first, and the first attempt
  reduced only the seed, leaving a coordinate past ~1.25e11 aborting the process
  (`docs/bigint-arc-retro-2026-09-25.md` §7.3). `bit_shl` got a discarding exemption by fiat;
  multiplication got none. `wrapping_mul`/`wrapping_add`, or a `Wrapping` wrap type, would let
  such code say what it means. `docs/int-overflow-policy-decision.md` §5 already anticipates these
  "if a measured hot loop needs them" — this is the correctness argument instead, which is the one
  Builtin vs Stdlib rule 6 asks for.
- [ ] `P3` **No hex float literal (`0x1p63`), so exact Double constants are written as products.**
  `tests/stdlib/test_parse_double.spr` spells 2^63 as `9007199254740992.0 * 1024.0` with a comment
  explaining why that is exact, because there is no way to write the bit pattern directly. C, Rust
  and Java all have the form; it is a lexer addition with no type-system impact, and it makes
  bit-exact float tests say what they mean. Motivated by the bigint arc's test code, where every
  exactness assertion needed a hand-built reference.
- [ ] `P3` **No wide multiply (`mul_hi`, or a 128-bit intermediate), which blocks two things.**
  `stdlib/crypto/p256.sprout` uses ten 26-bit limbs *specifically* so products fit i64 — that
  representation is a workaround for this gap, not a preference. It is also what stops a
  correctly-rounded `parse_double` being written in Sprout, which is why that entry currently
  reads "or a `strtod`-backed builtin". One primitive unblocks both. Prior art: Rust
  `u64::widening_mul`, C `__int128`, Go `bits.Mul64`.

### 2) Networking and HTTP Client

**Scheduler and poller**

- [ ] `P2` **The epoll poller collapses two interests on one fd; kqueue does not.**
  `sprout_poll_add` promises a per-`(fd, interest)` registration and only kqueue delivers one; the
  epoll backend keys by fd, so a second add silently replaces both mask and `data.ptr` and the first
  task is never woken. `sprout_poll_remove` ignores `interest` and deletes the whole fd. Latent —
  the stdlib keeps one task per socket — and becomes a Linux-only silent hang the moment someone
  writes a duplex protocol. **Fix (needs a call):** a per-fd owner table that loud-fails on a second
  concurrent registration, so the violation is identical on both backends. Making epoll genuinely
  per-interest is more code and buys a capability nothing asks for yet.
- [ ] `P2` **The pump only polls when the ready queue is empty, so one busy task starves all I/O.**
  `pump_loop` reaches `sprout_poll_wait` only on `rq_pop() == NULL`, so a task looping on
  `task_yield` — the documented cooperative idiom — means no fd readiness or timer is ever
  observed: `task_sleep` never wakes, deadlines never fire. Already worked around locally rather
  than fixed (`tcp_accept`'s EMFILE comment rejects a yield back-off for exactly this). **Fix (needs
  a call):** poll with a zero timeout on some cadence even when the queue is non-empty; the cadence
  is the only design question. Pairs with the timerfd-free backend below, which needs a
  timeout-driven wait anyway.
- [ ] `P2` **A timed read costs a timerfd per park on Linux.** `sprout_poll_add_timer` calls
  `timerfd_create` per registration, doubling the per-connection fd cost. The fatal half is fixed
  (arming failure is reported, not `sprout_fail`). **Remaining: the timerfd-free backend** — one
  shared timerfd plus a deadline heap, or the `epoll_wait`/`kevent` timeout argument driven by a
  min-heap. Makes `task_sleep` descriptor-free and deletes the `Task.park_timer_dead` exactly-once
  dance, which exists only because a timerfd close must happen exactly once. Rewrites timer
  semantics for `task_sleep`/`with_timeout`/`select` across both backends at once; kqueue
  (`EVFILT_TIMER`, no descriptor) cannot exercise the regression locally.
- [ ] `P3` **A listener readiness primitive was scoped 2026-08-11 and DEFERRED — do not build it
  as one small builtin.** It needs *two*, a non-parking accept as well, since `tcp_accept` parks
  indefinitely on EAGAIN by design and "wait then accept" is therefore not bounded. Its motivating
  case, backing off under descriptor exhaustion, is already solved in C, and its value is gated on
  the timerfd-free backend above anyway. Build it when a concrete caller must give up on accepting
  — a supervisor rebinding a listener, a drain-then-rebind reload — both builtins together with
  that caller.
- [ ] `P3` **A force-dropped handler still leaks its connection.** `force_drop_task` tears down
  poller registrations but `park_close_fd` is -1 for a handle-table-owned conn, so the fd is never
  closed and `g_conn_used[conn]` stays 1; repeated cancel-and-restart cycles exhaust the 2048-entry
  table. **The obvious fix is worse than the leak** — clearing `g_conn_used` makes the slot
  immediately reallocatable, so a surviving Sprout `TcpConnection` naming it denotes a different
  peer: a cross-connection integrity bug in place of a bounded silent leak. The real fix is the
  finding's second option, scope-owned connection handles so join-time reclaim covers value and slot
  together. A design task, not a patch. Cheap safe subset if it ever bites: free the parked 64 KiB
  `recv` buffer on drop, which needs no ownership reasoning.

**tcp_* surface reduction**

- [ ] `P2` **Finish decomposing the timed `tcp_*` builtins into readiness + transfer.** The design
  separates *readiness* (`tcp_wait`, parks, moves no data) from *transfer* (`tcp_read_some`/
  `tcp_write_some`, never park, report `Err TcpWouldBlock`), with retry-and-deadline loops written
  once in `stdlib/net.sprout`. Both primitives exist; the read half landed 2026-08-11 (net −4
  builtins). **Remaining:** retire the `tcp_read_exact` builtin — `read_exact_by` now provides the
  deadlined read in Sprout, so what is left is reimplementing the undeadlined `read_exact` over it
  and deleting the builtin; migrate `tcp_write_all_timeout`, which re-arms its idle bound
  inside C so a caller can never impose a total bound (`write_all_by` shows the shape); and retire
  `tcp_write_string`, blocked on the full-duplex item below. Delete each twin's `APPROVED_BUILTINS`
  entry as it goes.
- [ ] `P2` **Full-duplex on one socket is inexpressible, and the restriction is CONSERVATIVE rather
  than fundamental.** Two tasks borrowing one connection (one reading, one writing — a proxy,
  WebSocket, HTTP/2) is rejected because a `once` closure could be stored and run later. That reason
  does not hold inside `with_scope`, whose join is unconditional: structured concurrency already
  enforces the lifetime discipline the checker does not model. **Two candidate fixes:** teach the
  checker that a borrow captured by a `task_spawn` closure is bounded by the scope's join, or add a
  `split` returning two linear halves with disjoint operations (Rust's `TcpStream::split`, the more
  explicit option). Not solved by a raw-`Int` escape hatch — that discards consume-exactly-once
  instead of splitting it. This is the sole blocker on retiring the raw-handle `tcp_*` family.
**HTTP server**

- [ ] `P1` **Decode `Transfer-Encoding: chunked` request bodies.** Refused with 501 today, which is
  conformant (RFC 9112 §6.1) but a real gap — any client that cannot know its body length up
  front uses chunked. **Scope:** chunk-size line parsing (`<hex>[;ext]\r\n`), the read loop in
  `read_remaining_body`, `max_body_bytes` against the RUNNING total, the trailer section consumed,
  and CL+TE still refused as the smuggling shape (RFC 9112 §6.3). **Depends on** streaming below
  — chunked has no `Content-Length`, so the size-bounded buffer cannot express it, and the two
  land together.
- [ ] `P2` **Stream request bodies instead of buffering them.** Buffering forfeits O(chunk) memory,
  backpressure, incremental proxying and mid-body abort, and cannot express chunked at all. The
  `Bytes` work is a prerequisite, not a substitute: streaming replaces the accumulator, not the
  element type, and `request_body_bytes` becomes the collect-it-all convenience every streaming API
  also offers. **Design constraint:** the occupancy bound currently assumes the handler runs after
  the whole body is read; streaming makes them concurrent and changes that math.
- [ ] `P2` **List-valued request headers.** `parse_header_lines` folds repeats last-wins into a
  `Dict String`, so a comma-list header (`accept`, `forwarded`, `via`) collapses to the last.
  Three fields are already handled by name in `fold_repeat`: `host` and a differing
  `content-length` are refused as framing hazards, and `cookie` is joined with `"; "` per RFC 9113
  §8.2.3. That is three special cases where a general all-values accessor would be one rule —
  which is the argument for this entry, not against it. Needs an accessor beside `request_header`
  (Go `map[string][]string`; Rust `HeaderMap` multi-map).
- [ ] `P3` **A `render` error reaches the client as a bare 500, its reason dropped.**
  `render_response_or_fallback` in `stdlib/http_server.sprout` answers any `Err` from `render`
  with a fixed "internal server error", and `http_server` has no log, so the handler's body and
  headers and the `HttpServerError` saying why are all lost. Only a status outside `stdlib.http`'s
  table reaches it today. Wants a way to report it, e.g. a log hook on `ServerConfig`.
  `docs/http-request-params-v0.md` §9.
- [ ] `P2` **Request-param convenience layer.** A merged `param`/`param_all` bag over query+form
  (query-first, matching Werkzeug's `CombinedMultiDict([args, form])`), plus first-wins
  `Dict String` projections `query_params`/`form_params`/`params`. All over the existing `_pairs`
  accessors. `docs/http-request-params-v0.md` §7.
- [ ] `P2` **Path / route params (`/users/:id`).** Needs `Route` to move from exact
  `route_path == path` to pattern matching, a segment-capturing matcher, captures threaded into
  dispatch, then a `path_param(name, req)` accessor. `params` is overloaded across frameworks
  (Sinatra = merged bag, Express = path only) — reserve `path_param` for this.
  `docs/http-request-params-v0.md` §7.
- [ ] `P2` **Template engine phase 2 — filter pipeline + `loop.index`.** The parser already
  special-cases `{{ path | safe }}`, so generalizing to a `{{ path | filter }}` chain
  (`upper`/`lower`/`default`/`length`) is the natural next step; plus `loop.index` in `{% for %}`
  and a web-server example rendering a page from a template. `docs/template-engine-v0.md`.
  - [ ] `P3` **Template engine phase 3 — inheritance/includes.** `{% extends %}`/`{% block %}`/
    `{% include %}` composition, if demand warrants once phase 2 ships.
- [ ] `P3` **`stdlib/log` follow-ups (deferred from v0):** (a) escape/quote field values containing
  spaces or `=` in the line formatter; (b) a JSON-lines formatter variant (the sink/format split
  makes this a new formatter, not an API change); (c) graduate `format_iso8601` + a
  `wall_now_micros` re-export into a dedicated `stdlib/time.sprout` once a second consumer appears.
  `docs/logging-v0.md` §9–§10.

**Runtime and diagnostics**

- [ ] `P2` **Task-boundary panic isolation ("let it crash" at task granularity).** A panic in any
  green task reaches the process-fatal path, so one bad request kills the whole server. Give the
  scheduler a per-task panic boundary: unwind to the spawning `with_scope`/`task_spawn` frame, mark
  the task failed, reclaim it, let siblings continue, and let a supervisor respond (500 + loud log).
  Buildable without type-system work and it de-risks the eventual `Exn`-effect unwinding.
  **Caveats:** GC type-aware rooting must be settled on unwound frames, and a task that panics
  mid-mutation of shared state can leave it inconsistent — the safe contract is terminate +
  notify, never resume-in-place.
- [ ] `P2` **A bare `<-` bind of a `Result` in a `Unit`-returning function silently discards the
  `Err`.** Defensible do-notation semantics, but it converts a newly-recoverable API into a silently
  swallowing one and is invisible at the call site. **Designed, awaiting a call on severity:**
  `docs/fallible-bind-diagnostic-v0.md`. Of 1539 `<-` binds, 21 discarded ones use `_ <-`
  (deliberate, and GHC's own opt-out) and 5 use a named binder, of which 1 was production code and
  is fixed. Survey is unanimous — warning, never an error, with an underscore opt-out (Rust
  `unused_must_use`, GHC `-Wunused-do-bind`, Swift SE-0047). Recommended as a **driver-side lint
  pass**, not an `infer` change: `CompileResult` has no channel for a warning on a *successful*
  compile. Remaining blockers are the two decisions (warn vs error; whether CI gates on it).
- [ ] `P3` **Three R2 producers still mint Strings that can violate the `String` invariant:**
  `term_read_line`, `env_get`, `argv_get`. None has a cheap NUL repro any more (the OS NUL-delimits
  env and argv; `term_read_line` truncates at the NUL — data loss, not an inconsistent header),
  but all three can still mint invalid UTF-8, which `SPROUT_GC_HDRCHECK` is blind to because it
  compares `aux` against `strlen` and a bad lead byte leaves those equal. `docs/debugging.md`
  records the asymmetry.
- [ ] `P2` **`http_request`'s `timeout_ms` does not bound name resolution.** The deadline starts
  before `http_resolve`, but `async_resolve` parks on its pipe with `scheduler_park_on_unowned_fd`,
  which has no timeout, so a 30 s lookup under `timeout_ms = 1000` returns ~29 s late
  (documented as a limit in `docs/builtins-reference.md`). **Fix:** park with the remaining
  budget (`scheduler_park_on_unowned_fd_timeout`) and abandon the lookup on expiry the way
  force-drop already does (`docs/async-dns-v0.md` §6), so no thread or fd leaks.

### 2.5) Binary Data and Protocol Primitives

- [ ] `P2` **`bytes_builder_append` is O(n_left + n_right) per call**, copying full chunk arrays
  into a new flat array, so a `list_fold` over n strings costs O(n²). Switch to a tree/rope where
  append makes an internal node (O(1)) and `builder_build` traverses once. Also add `builder_str`
  and `builder_to_str` to skip the `Bytes` intermediary and the UTF-8 round-trip. These three
  unblock a pure-Sprout `string_join_suffix` over `list_fold` + builder (see §5).
  Until then, `bytes.builder_concat` (pairwise merge, O(k log k)) is the workaround every chunked
  reader uses; `bigint.builder_of_mag` and `hex` still hand-roll the same halving.

- [ ] `P2` **`bytes.singleton` and `bytes.builder_byte` are partial**, trapping via `sprout_fail`
  on a value outside 0..255 (`bytes_singleton`, `bytes_builder_byte`). `docs/guidelines.md` §2 makes
  totality a hard mandate for [Library] code, so two stdlib exports currently break it. The sibling
  `builder_u16_be`/`builder_u32_be` take the other branch and wrap silently, so the module is also
  inconsistent with itself about an out-of-range byte. Decide one answer for all four — `Maybe
  Bytes`, a documented wrap, or a smart constructor over a `Byte` wrap — since changing the two
  trapping signatures is a breaking API change. Found while annotating complexity for the `Eq Bytes`
  instance; the traps are pre-existing and undocumented until that change.

### 3) JSON Support

- [ ] `P1` **`json.parse` has no nesting limit, so 32 KiB of `[` kills the process.** `p_value`
  recurses once per level; 2^14 nested arrays overflow the main stack (measured, 2026-10-09), and a
  green task's stack is smaller. Reachable from any HTTP or LSP body. Needs a depth cap that returns
  `Err` — a Design Change Process call on the limit and whether callers can raise it (serde_json and
  Go's `encoding/json` both cap by default).
- [ ] `P2` **Reimplement `json_stringify` in Sprout** once string/escaping primitives make that
  practical, keeping host builtins for the impossible or efficiency-critical.
- [ ] `P2` **An out-of-range literal reads as an infinity, so a conformant document can be READ but
  not re-written.** `parse_double` saturates rather than rejecting, so `parse("1e400")` gives
  `JsonFloat(+inf)` and `stringify` then refuses it. `1e-400` underflows silently to `0.0`. Not
  confined to out-of-range literals: a value within a bit of either threshold goes the same way, and
  `1.7976931348623158e308` rounds to DBL_MAX but reads as `+inf`. RFC 8259 §6 names `1E400` an
  interoperability hazard and explicitly allows limiting range, so rejecting at parse is legitimate
  and has not been taken. This is the only way a non-finite Double can enter from ordinary JSON
  input rather than arithmetic. Design Change Process call between: reject as `Err`, keep the
  saturating `inf` and document it, or clamp to the largest finite Double — and whichever is chosen
  should settle the underflow-to-zero, the same question at the other end.
- [ ] `P3` **`parse_double` is up to 5 ULP off past 15 significant digits.** Exact at or below
  that (significand under 2^53, scale exact); past it the `Int -> Double` conversion and `10^k`
  each round, and a result landing subnormal rounds a second time on the split divisor. Bound is
  `|parsed - exact| <= 5 ULP` over 10^-322..10^308, held by
  `tests/stdlib/test_parse_double_differential.spr` (`just parse-double-sweep` for the wide run),
  and tight — the sweep reaches it. Closing the last bits needs a two-double (hi/lo) power-of-ten
  table with Dekker products, a correctly-rounded decimal→binary algorithm, or a `strtod`-backed
  builtin — the last needs approval under Builtin vs Stdlib rules 4–6, with the *correctness*
  argument doing the work, not performance. It would also settle the two threshold tips above.

### 4) Terminal UI Runtime

- [ ] `P1` **TUI M4 — the widget library (`stdlib/tui/widgets/`).** C1 landed 2026-09-07:
  `container`/`children`/`text`/`paint` ship `row`/`column`/`grid`, an opaque `Slot`, `label`/
  `static`/`spacer` and the four child traversals; `examples/tui_dashboard.sprout` went 284 → 157
  lines with no container of its own. `View.measure` now returns a `Measured` so a child can ask
  for "whatever is left". C3 below remains. Design: `docs/tui-widget-set-v0.md`.
- [ ] `P2` **Swapping a `scroll_view`'s child kills the keyboard for a focused descendant.**
  `on_content` builds the new child with `has_focus = false` while `Ring.at` still names its id,
  and `ring_route` declines an outside `ToFocus` (`focus.sprout:157`), so only a user Tab restores
  input. Unfixable in the widget: focus lives in the ring above it and `has_focus` sits inside an
  existential it cannot read. `docs/tui-focus-v0.md` §4.6 already allows this state; what is new
  is reaching it without the author doing anything wrong. Needs a focus-model answer — the ring
  re-asserting after a subtree changes, or an application-addressable focus command (§4.7 forbids
  one today). Pinned in `tests/stdlib/test_tui_scroll_view.spr`. Design:
  `docs/tui-content-update-v0.md` §9.4.
- [ ] `P2` **TUI `list_view` and `tree` — per-item rendering.** An item is a `String`; brick's
  `renderList` takes `Bool -> e -> Widget n`, so an item can be any widget. Deliberately not taken
  with the content-update work, which has landed. One entry for both widgets, not two — the
  answer is the same shape. Design: `docs/tui-list-view-v0.md` §8, `docs/tui-tree-v0.md` §8.
- [ ] `P3` **TUI `tree` — sibling labels must be unique.** A node is named by its path of labels
  (`docs/tui-tree-v0.md` §4.1), so two siblings sharing one are indistinguishable: the walk takes
  the first, and because the open set is keyed by path, opening one opens both. A filesystem
  cannot produce this, which is why `ide/filetree` is safe, but a general caller can. The lift is
  `tui-tree-widget`'s caller-chosen identifier. Pinned in `tests/stdlib/test_tui_tree.spr`.
- [ ] `P3` **TUI `input` — word motion, selection and the chord family.** `ctrl-w`/`ctrl-u`,
  ctrl-arrows, a selection anchor beside the caret, and the terminal clipboard. Deliberately not
  claimed by C2b: every one is a binding an application may want, and a field that took them would
  give no way to opt out. Same file, one known limitation: deleting the cluster BETWEEN two that
  would themselves combine leaves them as two, because only insertion re-segments. Brick's zipper
  behaves the same way; reaching it means deleting from between a base character and its mark.
  Design: `docs/tui-input-v0.md` §9.
- [ ] `P2` **TUI focus — click-to-focus needs a retained hit-test tree.** Containers discard the
  solved region list after painting, so nothing can answer "what is under the pointer",
  `focus_ring` is keyboard-only, and a `list_view` row cannot be clicked either.
  Design: `docs/tui-focus-v0.md` §9.
- [ ] `P3` **TUI focus — terminal cursor placement, and focus trapping.** `app.run` hides the
  cursor for the whole session, so a focused `input` paints its own caret cell; Brick instead
  feeds `appChooseCursor` from the ring. Trapping — a widget that consumes Tab itself — is the
  opt-in §4.5 declines to make implicit. Both are cosmetic until an application asks.
  Design: `docs/tui-focus-v0.md` §9.
- [ ] `P2` **TUI M4 C3 — `table`, the last of the larger widgets.** `scroll_view` landed as C3a,
  `text_area` as C3b, `tree` as C3c and `tabs` as C3d (`docs/tui-tabs-v0.md`).
- [ ] `P3` **TUI `tabs` — what v0 left out.** No way to retitle a tab, so the IDE cannot mark a
  dirty file in the bar; a bar wider than its region is cut at the edge rather than scrolled to
  keep the shown title in view; no mouse; no reordering. Each is additive: retitling is one more
  `Change` arm. Design: `docs/tui-tabs-v0.md` §2.
- [ ] `P3` **TUI tests — the screen-dump helpers are pasted into eight suites.** `row_text` is in
  `tests/stdlib/test_tui_{input,list_view,focus,scroll_view,tree,text_area,tabs}.spr` and
  `tests/ide/test_ide_editor.spr`; `rows_from` in four of them. A change to how a screen reads back
  is made eight times. Move them to a `testsupport/` module; `just test` passes `--package-root`.
- [ ] `P3` **TUI — a tick repaints the whole tree even when nothing changed.** `App.tick_ms` is both
  the input read deadline and the tick period, and the deadline is load-bearing: it is what resolves
  a held ESC into a key. So an application wanting no animation still repaints twice a second, and
  `diff_to_ansi` then emits nothing for it. Measured at 8.5 ms per idle frame once §3.4's ASCII fast
  path reached `grapheme`; before that it was 22% of a core with a source file on screen. Separating
  the two — keep the deadline, emit a tick only when asked — needs somewhere in `App` to ask.
  Design: `docs/tui-core-v0.md` §3.2.
- [ ] `P3` **TUI `viewport` — `render` builds the whole document every frame.** `visible` calls
  `buffer_lines`, which is `list_append(list_reverse(above), Cons(line, below))`, then throws all
  but `region_rows` of it away; the unfocused branch does the same. `above`/`below` are already
  the two halves the window needs, so O(region rows) is reachable. The accessor's shape was open
  pending the IDE pane; it now wants a window AND a line count, for a gutter as wide as its
  largest number (`ide/editor.sprout`), so both `text_area` and the pane pay it twice per frame.
  Harmless for a commit composer, not for a source file. Design: `docs/tui-text-area-v0.md` §4.6.
- [ ] `P2` **TUI `text_area` — an application can send a caret in but never read one out.**
  `on_content` takes a `Maybe buffer.Caret`, and the only outbound is `on_change: String -> m`
  (`text_area.sprout:231`), so nothing hands a `Caret` back. `docs/tui-content-update-v0.md` §9.3
  motivates the payload with an IDE restoring a cursor into a file it reopens — that round trip is
  not performable today: the app has no `Buffer` to call `buffer_caret` on, and mirroring the text
  through `buffer_open` puts the caret at the end. So the app must invent two `Int`s, which is also
  why wrapping `Caret`'s axes was tried and reverted — it guarded a value the app can never hold.
  Wants a caret readout; shape depends on whether `on_change` widens or a second handler appears.
- [ ] `P2` **TUI `text_area` — what C3b left out.** Soft wrap (needs height-for-width, which
  `Measured` cannot express); selection and clipboard; a way to ASK for an undo — `buffer` holds
  the history now (`docs/tui-undo-v0.md`), and the widget binds no chord and takes no prism for it,
  so only the IDE pane can walk it; word motion and the chord family;
  tab-stop expansion on paste, which today blanks a tab to one space and loses the indentation of
  pasted code; a jump-to-line control, whose intended shape is an opts-supplied prism rather than
  a `Delivery` arm; and peeking away from the caret, which the derived window gives up. Each is
  additive over the shipped surface. Design: `docs/tui-text-area-v0.md` §4.6, §4.10, §10.
- [ ] `P2` **TUI `scroll_view` — overshooting either end stores dead presses.** Clamping to the far
  end needs the viewport, and a handler is pure and gets no region, so `FromTop n` grows past the
  bottom and `FromBottom k` past the top; the stored presses must be spent before the window moves
  back. `Home` and `End` land exactly, so recovery is one key. Root cause is the pure-handler
  contract — the same wall `ListOpts.page` hit. Design: `docs/tui-scroll-view-v0.md` §4.8.
- [ ] `P3` **TUI screen — a wide pair cut by a narrower clip cannot be repaired.** `repair` writes
  through `set_raw`, so it is dropped outside the clip: a two-column cluster painted under a wide
  clip and then cut between its halves by a narrower one keeps its live half, and the terminal
  draws it two columns wide over a cell that now belongs to someone else. Unreachable with today's
  widgets — nothing paints a wide cluster and then narrows across it — but `screen_clipped` is
  public. Noted on `repair` in `stdlib/tui/screen.sprout`.
- [ ] `P3` **TUI `scroll_view` — no scrollbars, and no auto-scroll to a focused child.** Nothing
  indicates that content continues past the viewport; an indicator needs a style vocabulary and
  touches the `Ambiguous`-width question below. Brick's `visible` — scroll until the focused child
  shows — needs the child's solved position, so it waits on the retained hit-test tree the
  click-to-focus entry above describes. Design: `docs/tui-scroll-view-v0.md` §8.
- [ ] `P3` **`just tui-resize-probe` is opt-in, and one resize property stays uncovered.** Every arm
  of `app.run` is verified now, the `TermResized` one by that probe — but it depends on
  `script(1)` and on process timing and has no CI track record, so it is in no aggregate gate (green
  10/10 on macOS, 3/3 on Linux); wiring it into `ci-fast-gates` is a later call. Still uncovered,
  and **not worth a builtin**: that a *changed* size is adopted. `term_read_avail` answers
  `TermResized` from a flag its SIGWINCH handler sets and never measures a size, so the probe
  exercises the arm without one; asserting adoption needs `TIOCSWINSZ` on the pty master, which
  `script(1)` owns and does not expose.
- [ ] `P2` **The kitty keyboard protocol.** Would remove the lone-ESC ambiguity outright (the spec
  states the problem exactly) and deliver meta/super/hyper and key-release events, which the legacy
  encoding cannot express — the reason `event.Mods` has exactly three flags. Costs a capability
  negotiation, and the legacy decoder stays as the fallback, so it is additive.
- [ ] `P3` **Mouse reporting mode is decoded but never enabled.** M2 decodes SGR (1006); nothing
  writes the escape that asks for the reports, which belongs with the app loop that would turn it on
  — enabling it without a consumer just corrupts the input stream. Two smaller gaps in the same
  area: the modifier bits in the button value are masked off because `Event` carries no modifiers on
  a mouse report, and only SGR is decoded, not normal-mode tracking (which is why `MouseRelease` can
  carry a button at all).
- [ ] `P3` **A terminal may disagree with UAX #11 about a character's width.** `screen.sprout`
  computes width from the Unicode tables, the best a program can do unilaterally; a terminal that
  disagrees — emoji ZWJ sequences, Ambiguous-width under a CJK locale — renders at an
  unpredicted width and every later cursor move drifts. Any real fix is a negotiation with the
  terminal. Recovery today is a full repaint, which `screen_resize` provides.
- [ ] `P3` **A constructor with a LINEAR field cannot be used as a function value**, and the
  rejection names a synthesized parameter: `apply(Box, Tok(5))` gives
  `linear lambda parameter '__eta_x0' is not yet supported`. The underlying restriction is the
  deferred higher-order-linearity work, not new; what is new is that spec §5.3 now documents the
  bare spelling as idiomatic, so the wart is reachable from code the spec blesses. Two separable
  pieces: (a) name the constructor rather than the eta parameter, a `linear_check` change worth
  doing on its own; (b) support a linear parameter in a synthesized eta lambda, the deferred
  feature.

### 4.5) The IDE (`ide/`)

Its own section because `ide/` lifts out of this repo whole, as `loam/` did. Design:
`docs/ide-v0.md`.

- [ ] `P2` **IDE — one pane, no splits, no tabs, no palette.** `ide/app.sprout` wires exactly one
  editor beside the tree, so a second file replaces the first and an unsaved edit goes with it.
  `ide/pane.sprout` and `ide/palette.sprout` from the plan are unwritten; the `tabs` widget they
  want has landed. The pane first needs each editor's id in `ed.Opening`/`ed.Saving`, a rule for
  Enter on a directory and for closing a dirty tab (`docs/tui-tabs-v0.md` §5). A palette also needs
  somewhere to type a path, which makes "save a scratch buffer" reachable. Design: `docs/ide-v0.md`
  §9.
- [ ] `P2` **IDE — reopening a file forgets where the caret was.** `ed.EditorOpts.on_content`
  carries a stamped body and no caret, so every open starts at line 1. The payload is not the
  blocker — §4's "an application can send a caret in but never read one out" is: the pane would
  have to announce a `Caret` on the way out for anything to send back, and nothing reads one.
  Fix that entry first; this is its first real consumer. Design: `docs/ide-v0.md` §5.
- [ ] `P3` **IDE — an open that never completes says nothing.** A body dropped as stale (the user
  typed during the read) is claimed and discarded silently, so the file simply does not open and
  nothing says why. `on_note` exists now and a failed write uses it, but `stamped.fresh` hides the
  payload of a reply it has judged stale, so the pane cannot name the file it dropped. Wants a note
  that does not need the payload, or `at_least`. Design: `docs/ide-v0.md` §5.2.
- [ ] `P3` **IDE — no local history under undo.** The history dies with the process, so a file
  closed and reopened has nothing behind it — and autosave has already written the visited file.
  Every editor surveyed keeps a second floor: Emacs autosaves to `#foo#` and leaves the visited
  file alone, Vim has `'undofile'`, and VS Code and JetBrains both ship a local history ON by
  default — the camp Sprout's autosave puts it in. Wants durable state, somewhere to put it, a
  retention policy and a way to browse it, which is why it is a feature and not undo's second half.
  Survey and the call: `docs/tui-undo-v0.md` §3.2, §9.
- [ ] `P3` **`app.step_to` recurses forever when `update` re-sends the message it is given.**
  `delivered` (`app.sprout:126`) answers an unclaimed delivery with `apply(update, [msg], w)`, so
  an `update` arm whose handling of `msg` is `step_to(update, w, id, msg)` — the obvious spelling
  of "ask that widget" — loops until the stack goes. `ide/app.sprout` uses `widget.deliver`
  directly and handles `Nothing` itself to avoid it. Either document the constraint at `step_to`
  or give it a form that cannot fall back to the sender.

### 5) Data Structures and Collections

- [ ] `P1` **Polymorphic-keyed dicts (`Dict k v` instead of today's `String`-keyed `Dict v`).**
  Needs (a) a `Hash a` class — pick the method signature (`-> Int` vs `-> Bytes`) and algorithm
  (FNV-1a, Murmur3, wyhash) with runtime primitives for the primitives; (b) a runtime hashmap
  reshape, either a callback hash or coercing keys to a canonical `Bytes`; (c) migration of every
  `Dict` site plus the `Eq`/`ToString`/`dict_keys`/`dict_values`/`dict_entries` surface. Today's
  `Dict` forces callers to pre-stringify, which contradicts typeclass-based design. Unblocks
  `deriving (Hash)`.
- [ ] `P2` **Prelude O(n²) audit.** Several helpers are quadratic via naive list-append recursion
  and mostly undocumented: `ToString` for `List`/`Vec`/`Dict`, `mconcat`, `list_dedup`,
  `Semigroup (Dict v)`, and several `vec_*` (`map`/`filter`/`filter_map`/`reverse`/`slice`).
  `vec_sort_by`'s doc comment claims O(n log n) and rebuilds O(n²). Document true complexity inline
  or fix to linear. Findings and probes: `docs/fundamentals-code-review-handoff-2026-07-03.md`.
  `list_builder_*` fixes the list-append half: O(1) per add, one reverse at the end.
  Also `set_remove`: it reinserts every element into a fresh set where an AVL delete is already
  available — `NativeSet` IS the `Map` BST with value 0, and `map_remove` calls `bst_remove_node`.
  O(n) allocations instead of O(log n), and it needs a `native_set_remove` extern, so ASK FIRST.

- [ ] `P2` **A `Dict` key is interned permanently, so computed keys leak for the process's life.**
  `map_set` routes every key through `intern_string`, which mallocs outside the arena and never
  frees; `sprout_heap_lookup` returns NULL for those buffers so the collector skips them. That is
  deliberate — it makes `BSTNode.key` a raw `const char*` the GC need not trace, and gives every
  String an O(1) header length — but it means a server keyed on request ids grows the table without
  bound. Now VISIBLE, not fixed: the report carries `intern=`/`intern_bytes=` and the census an
  `offheap:` line (`tests/c_runtime/intern_table_report.c` pins that it counts distinct keys).
  Bounding it means making the key a traced handle, which is a runtime representation change and
  needs a design round. Measure a long-running server first — the galaxy client's 75,640 keys are
  boot-loaded and bounded, which is the easy case.
- [ ] `P2` **`unicode.lookup` allocates a closure per table search.** `find_tag(count, chunk, cp)`
  takes its chunk accessor as a `Int -> String` parameter, and passing `gcb_chunk` allocates a
  closure at every call. Measured with `SPROUT_DEBUG_ALLOC` over a segmentation probe: 82,000
  closures for 27,200 characters — one per lookup, three tables deep — falling to 200 once the ASCII
  fast path skipped the searches. The fast path hid it for ASCII; non-Latin text still pays. Options
  are a non-higher-order entry point per table, or making a static function argument not allocate.
  `sample` never showed this — it flattened into `str_slice`; only the per-kind counters named it.
- [ ] `P3` **Cost gates cover four workloads; a parse and the stdlib hot paths are unguarded.**
  `render-cost-gate` budgets a TUI frame, `test_byte_offset_cost.spr` pins one complexity claim,
  `rooting-cost-gate` prices a compile of one long block and `scc-cost-gate` one of many functions.
  Bytes are now REPORTED but barely budgeted: `arena_bytes=` and `offarena_bytes=` split the two
  allocators, and only `arena_bytes` has a ceiling, only in the two compile gates — in
  `rooting-cost-gate` it separates the two concatenation forms 5.9× against no signal from any
  count. Nothing budgets `offarena_bytes` (no `Bytes`/`Builder` fixture exists to set one from)
  and `render-cost-gate` budgets neither. A `cost-golden` over 4–5 fixed workloads on shared
  counters is the shape that covers all of them at once. Shapes: `docs/gates.md` §Render cost.
- [ ] `P3` **Mid-string `str_slice` is O(start)**, so a scanner whose offset advances is still
  quadratic. Prefix slicing no longer is — `str_slice` walks to `start + count` and stops, making
  `slice(s, 0, k)` independent of `|s|`. Closing the rest needs a codepoint-to-byte cache on String
  values, or byte-indexed callers. Measured shapes: `tests/stdlib/test_slice_cost.spr`.
- [ ] `P2` **B4 — `list_length` is unreliable on complex element ADTs.**
  `examples/digit_recognizer/recognizer.sprout` hand-writes two monomorphic length helpers purely
  because of it. Root-cause and fix so those can be deleted; add a regression over a `List` of a
  field-bearing ADT and of a tuple. Likely the same dispatch/monomorphization family as B2.
- [ ] `P2` **Retire two private re-implementations of `split`.** `ast_to_ir.split_on_comma` and
  `prelude.split_on_char` predate `stdlib.string.split`. Check two things: the shared one is
  O(bytes × |sep|) where the `split_once`-recursion is quadratic; and it **keeps** empty segments,
  so a caller relying on its private version dropping them needs a `filter` (`path.split` is the
  worked example).
- [ ] `P2` **Generalize `string_join_newlines` to `string_join_suffix(suffix, lines)`**, then
  reimplement in pure Sprout over `list_fold` + builder once the §2.5 builder work lands, and
  remove the C builtin. The builtin was a 2026-05-11 workaround for a 204K-deep right-fold that
  spiked 2.6 GB during stage-2 self-compile.
- [ ] `P2` **Tail-spread syntax for list-literal expressions (`[a, b | tail]`).** Patterns already
  support it; expressions do not, so `Cons(h, Cons(sep, acc))` — prepending a fixed number of
  elements onto an existing list, at ~8 sites across stdlib and the compiler — cannot be written
  as a literal, unlike its `Nil`-terminated siblings. New expression syntax, so it needs its own
  Design Change Process pass (prior art: Haskell has no literal cons-spread; OCaml `::`; Elm `::`).
- [ ] `P2` **Format-agnostic serialization (serde-style Serializer/Deserializer visitor split).**
  The `Serialize`/`Deserialize` classes shipped on `feat/deriving` were reverted 2026-06-10 for
  conflating polymorphism with format choice — the names promised format-agnostic dispatch and the
  implementation hardcoded S-expressions. Target: a user-facing `Serialize a` taking a
  format-specific `Serializer`, with format implementations behind visitor traits. Prerequisites:
  the `Serializer`/`Deserializer` classes (likely higher-kinded or rank-2), stream-vs-tree
  intermediate, error accumulation. Defer until a concrete use case beyond iface materializes.
- [~] `P2` **Vector utility combinators** (e.g. `vec_sum_by`; `vec_max_subsequence_by_count` is a
  maybe/later).
- [ ] `P3` **Growable `MutVec` — the deferred operations**, listed so the omissions do not read as
  oversights. (a) `mutvec_with_capacity` / `mutvec_reserve` — skip the regrowth when the size is
  known, for stores where the doubling reallocations *are* the cost; needs `vector_reserve`. (b) The
  open question: an ECS whose `world_new(cap)` fixes every column's capacity only benefits if
  `world_spawn` can grow columns it does not own. Shrinking landed 2026-09-09
  (`docs/growable-mutvec-v0.md` §Shrinking).
- [ ] `P3` **No `fst` / `snd` in the prelude.** `\ (a, b) -> …` is a **two-parameter** lambda in
  Sprout, not tuple destructuring, so `count(\ (_, e) -> …, entries)` fails with
  `Bool vs Entry -> Bool` and the workaround is a named function with a tuple pattern per
  projection. Tuples are otherwise first-class, so the accessors are the gap, not the type. Decide
  alongside it whether a destructuring lambda is wanted — note the syntax currently *parses* and
  means something else, so that would be a change of meaning rather than new syntax.
- [ ] `P3` **B5 — a half-open `[0, n)` iteration helper. DEMOTED TO SUGAR** —
  `docs/ranges-v0.md` made a backwards range empty, so `range_up(0, n - 1)` is correct at `n == 0`
  and the correctness half is gone. What remains is whether `[0, n)` deserves a name; two in-tree
  definitions of `fn upto(n) = range_up(0, n - 1)` are the evidence. **If added, do NOT call it
  `upto`** — one character from `range_up`, different arity and meaning; `count_each`/`count_fold`
  avoid the collision. Every in-tree `upto` result is consumed immediately by
  `range_fold`/`range_each`, so the combinator pair covers all real usage and an exclusive-range
  *constructor* is the weaker option.
- [ ] `P3` **Deferred non-goals from `docs/min-max-combinators-v0.md` §2**, both waiting on a
  caller. (a) Comparator-taking `min_with`/`max_with` — there is no `sort_with` either, so
  comparator forms should arrive as a set. (b) A generic two-argument `min`/`max` over `Ord` —
  `stdlib.math.int` already has `Int`-specific ones, so this is a naming-collision question first (a
  prelude-global `min` would be shadowed by `import stdlib.math.int (min)`, and shadowing a prelude
  name misdirects the error).
- [ ] `P3` **Deferred non-goals from `docs/collection-combinators-v0.md` §7 and
  `docs/filterable-v0.md` §7.** (a) `length`/`is_empty` over `Foldable` — a fold-derived version
  is O(n) *even on `Vec`*, so free functions would put two spellings of one question at different
  complexities in one prelude; doing it right needs class methods with per-instance overrides, which
  Sprout cannot soften with default bodies (`parse_class_body` collects signatures only), so both
  become mandatory for every future instance. Note `list_length` is private, so List has no public
  length and `count(\_ -> true, xs)` is the O(n) stand-in. (c) `position`/`index_of`, and
  `take`/`drop`/`zip` (List-shaped, not `Foldable`-derivable). (d) `Dict`/`Set` instances, gated on
  those types getting `Functor`/`Foldable`. (e) An effectful predicate (`a -> Bool !{e}`) needs
  `Filterable` to state `pred`'s call order, not B2 (`docs/effect-polymorphism-policy-v0.md`).
- [ ] `P3` **No `NonEmpty a`, though `docs/guidelines.md` §3 names it as the illegal-states
  exemplar.** Four in-tree sites pay for the gap: two panics in `infer.build_comp_level` and
  `infer.comp_fold_name` (a comprehension always has ≥1 generator, so `ComprehensionExpr` should
  carry one), and two dead arms in `tui.text.cluster_width`/`cluster_is`, whose own comment says
  "each a non-empty list of codepoints". uncharted-suns pays two more panics (`game/sim.sprout:348`
  and `:370`, the slot and zone rosters) plus `grimward.gear.slot_of`, pinned by `test_gear.spr`
  rather than by the type. Scope it as `NE a (List a)` with total accessors — the cons shape
  enforces itself, a `NonEmptyVec` would need representation opacity — shipped for ordinary modules
  (spec §5.6.4) but NOT for the prelude, where this type would live (§6), and without the total
  `head`/`minimum` the arms go but the `Maybe`s stay. Ergonomics gate on `[a, b | tail]` above.

### 6) Modules and Packaging

- [ ] `P1` **Package/dependency conventions for third-party modules.** Direction recorded
  (non-normative, per-phase approval pending) in `docs/packaging-v0.md`: strict compile-time
  coherence with the orphan rule extended across packages, single-version selection with a loud
  resolver, incompatible majors as distinct explicit package identity (Go `/v2`-style), git sourcing
  reusing the `.iface`/`.bc` cache, graph-wide compiler-version unification. Package-qualified
  identity is the spine — it generalizes module-qualified type identity and dissolves the "dotted
  non-`stdlib.` import resolves to `Nothing`" gap as its degenerate single-package case, subsuming
  the `examples.*` item below. Phased plan in §10, semantics before mechanics.
- [ ] `P1` **An import resolving nowhere is silent; one whose file is missing panics.**
  `module_loader.resolve_module_path` is pure and guesses a path with no existence check, so a name
  it cannot place is dropped with no diagnostic (`module_loader.sprout:416`, `bundler.sprout:1036`
  and `:1048`), while one it places on a missing file `panic`s the read
  (`module_loader.sprout:419`) and kills the REPL's analysis session. `import math.intza` answers
  `ok` and binds nothing; `import foobar` ends the session. The same guess caps the prefixless form
  at depth 1: `import math` works end to end, `import math.int` cannot, a dotted name being read as
  a package-root path. Fix: return candidate paths and probe them in the already-`!{IO}` loader.
  Open: does the stdlib root outrank package roots? `docs/repl-env-type-vocabulary-v0.md` §4.2
  calls the silent drop deliberate and must be revisited with it.
- [ ] `P2` **Dedup `extern fn` declarations in the bundler.** The typed AST reaching
  `ast_to_ir.translate_program` holds the same `TExternFnDecl` once per importing module. The IR
  path defends with a `seen: Set` in `lower_extern_decls_loop`; the fix belongs in `bundler.sprout`
  — collapse same-name `ExternFnDecl` nodes into one canonical decl, checking signatures match
  across importers.
- [ ] `P2` **The stage-1 module loader does not resolve `examples.*` imports** (only `stdlib.*`), so
  a multi-file example that imports a sibling fails to link, and library-style example modules with
  no `fn main` fail at the entry point. Both are in `XFAIL_EXAMPLES`; the fix needs a design
  decision on cross-example module resolution (subsumed by packaging above).
- [ ] `P2` **Import diagnostics carry no source position.** `ImportSpec` has no `SourcePos`, so the
  two checks in `bundler.make_bundle_or_err` report `no_pos()` and name the file in the message
  text. Threading a pos through `parse_import_line` reaches four consumers and would let the LSP
  squiggle the offending name. Wants both halves: positions from an *imported* file do not render
  either (`issue_pos_for_entry` blanks any pos outside the entry file).
- [ ] `P3` **`stdlib.collections` is a 7-line module exporting one function**, and after the import
  diagnostics deleted the stale entries no import line in the tree binds anything from it — its
  `vec_singleton` is a duplicate of the prelude's. Decide: grow it into the real collections surface
  and move the prelude's `vec_*` there, or fold it away. **Do not simply delete it** — the
  duplicate name is load-bearing as a fixture: `module_loader.sprout:303` picks `own_pairs` by
  declared-name selection rather than key-exclusion *specifically* because of it, it is the only
  in-repo case exercising that path, and `test_compiler.spr:123-142` is an explicit regression for a
  bug that has already happened.
- [ ] `P3` **`export extern fn` is dead syntax and the tree has 8 of them** (7 in
  `stdlib/net.sprout`, 1 in `stdlib/task.sprout`). An extern never enters a module's exported set
  and `export` on one is a parsed-and-discarded no-op, so each reads as a visibility decision that
  does nothing — the same defect class as the stale import names the diagnostics now reject.
  Decide between rejecting it at parse time (loud, consistent with the import rules), deleting the
  keyword at the 8 sites (silent, and the next author writes it again), or a lint rule.

### 6.5) OS and Process Primitives

- [ ] `P2` **Relocate `read_file`/`write_file`/`env_get`/`argv_get` out of the prelude.** They are
  globally available but belong in a namespaced module; `stdlib.fs` settled where the namespace goes
  (`list_dir`/`path_join`/`path_exists`/`delete_file` already landed there, so they are off this
  list). Migration touches every stdlib and example caller, needs a stage-0/stage-1 rebuild, and
  needs a call on whether to keep prelude re-exports. Also the home for `make_temp_file`.
- [ ] `P2` **`proc_shell(cmd: String) -> ProcResult !{IO}`** in `stdlib.process`, when a concrete
  use case requires pipes, redirects or glob. `proc_run` covers all current needs. Implementation is
  `argv = ["/bin/sh", "-c", cmd]` forwarded to `sprout_proc_run_impl`.
- [ ] `P3` **A declared `Spec` layer over `stdlib.args`** (fuller argparse flavor): declare expected
  args (name, kind, default, required, help) for `Result`-typed validation of
  unknown/missing/bad-int args and an auto-generated `--help`. The bag-of-args core covers the
  boot-config need without it.

### 7) Tooling and Developer UX

**Formatter and linter**

- [ ] `P2` **`just fmt` inserts a space after every prefix `!`**, and a second before a call's
  argument list: `!f(x)` → `! f (x)`, `!flag` → `! flag`. Unary minus is unaffected, so it is
  specific to `!`. Output is idempotent, lint-clean and parses identically — a readability defect
  only — but `!` is the only boolean negation Sprout has and the docs present `!x` as the idiom,
  which `just fmt` then rewrites into a form nobody would write by hand. In argument position the
  preceding comma loses its space too. Mechanism: `is_call_like_pp_other` (`formatter.sprout`) does
  not recognise `!` as a prev-prev token after which `ident(` is a call, so the `(`-spacing fallback
  fires; the postfix-`.` and `..` cases have an explicit arm there and `!` wants the same, plus
  `needs_space_curr_bang`'s counterpart for the space *after* `!`. It already costs something —
  `test_ir_codegen_cpr_maybe_externs.spr` had to write `if … then false else true` instead.
- [ ] `P3` **`fmt` drops the space between two adjacent parenthesized type atoms.**
  `Widget s (s -> s) (s -> String)` becomes `… (s -> s)(s -> String)`, which reads as application
  rather than two fields. Same family as the landed `[` fix: `needs_space_word_or_op` has no case
  for `)` followed by `(`, so the `is_word_like` fallback returns false. Confirm no legitimate
  no-space case exists in a *type* position, then add a formatter regression. A word before one
  loses it too: `type alias Forwarded = Dict (List T)` becomes `Dict(List T)` (resolve.sprout).
- [ ] `P3` **`fmt` inserts a space into chained application when the argument starts with an
  uppercase identifier.** `pick_color()(Red)` becomes `pick_color() (Red)` while `pick_int()(4)` and
  `labeller_for(1)(2)` are untouched — discriminated by probe, so the trigger is the leading
  identifier's CASE, not the type or literalness. Cosmetic, but it is inconsistent output from the
  tool `fmt-check` gates on. Acceptance: `f()(X)` and `f()(x)` format identically.
- [ ] `P3` **Formatter inconsistency on nested constructor spacing.** `Just(Just(x))` sometimes gets
  an inner space and sometimes not, within one file — possibly column-budget driven. Needs a
  fixture with multiple `Just(Just(x))` at different indentation depths.
- [ ] `P2` **Lint suppression, then wire `lint` into CI.** Three things that must ship together,
  since green-but-unenforced returns to red. **(a) The pragma**, direction chosen 2026-08-12: a
  same-line trailing `# lint: allow(<rule>)` with the rule name mandatory — Rust requires it while
  ESLint/clang-tidy/golangci-lint permit a blanket form that would silently swallow unrelated
  findings. Implement it as a post-parse filter in `lint_rules.lint_ast`, **not** `fmt_driver`, so a
  future editor surface cannot disagree with the gate; `formatter.lint_source` issues carry no rule
  id, so the contract covers AST rules only. **(b)** `just lint` is down to **12** findings, all
  `unparsed` on `parse_error/*.spr` fixtures that exist not to parse — the combinator sites were
  swept (`docs/lint-rules-v0.md` §8), so those 12 are the only thing left between `lint` and a gate.
  **(c)** Put `lint` in `ci-fast-gates`; pre-commit only. Needs a style-guide `## Lint`.
- [ ] `P2` **Per-line lint suppression is the follow-on that is bigger than it looks.** It must scan
  every line, not just the header, so a `#` inside a multi-line backtick template can false-match
  — the hazard `formatter.lint_spans` exists to handle, and the class of bug fixed in `7d1171f`.
  And a line-number anchor cannot serve the `unparsed` case at all: inserting the directive shifts
  the position it anchors to, and `parse_error/missing_else.spr` reports at 3:1 in a 2-line file.
- [ ] `P3` **Report a lint suppression that no longer suppresses anything.** Biome and staticcheck
  both flag an unused suppression and rustc reaches the same end with `#[expect]`; without it these
  directives accumulate unreviewed. Needs the unfiltered finding set alongside the filtered one.
  Rust's `#[expect]` is the strictly better fit for the two deliberate files — it turns the
  suppression into a second assertion that the construct is still present — held back only to
  avoid shipping two mechanisms at once.
- [~] `P2` **Formatter/linter beyond the baseline.** Eight AST lint rules shipped
  (`staircase-of-doom`, `redundant-vec-from-list`, `list-shape-pattern`, `list-prefix-pattern`,
  `multi-line-lambda-arg`, `deprecated-brace-body`, `nullary-const-fn`, `hand-rolled-combinator`) on
  top of `formatter.sprout`'s text-based Style checks. **Remaining roadmap** from
  `docs/idiomatic-sprout.md`: "Match the producing call directly" (a `let`/do-bind immediately
  followed by a match on that single otherwise-unused variable) and "Collapse a trivial `do` block".
  The rest of that doc is design-level or too fuzzy for a reliable syntactic check. **Also open:**
  autocorrect (needs an AST-aware rewriter; today's formatter is a line-based text transform) and
  the config file, designed in `docs/lint-rules-v0.md` §8.
- [ ] `P3` **Nothing lints the pessimised spelling: an eliminator applied to a call.**
  `maybe_with_default(0, dict_get(k, d))` boxes a wrapper the `match` form keeps unboxed — 70–90x at
  100k live (`bench/results-2026-09-28-vec-box-tax.md`). `hand-rolled-combinator` stopped suggesting
  it (`docs/lint-rules-v0.md` §7.4), but nothing flags the sites already written that way: 35
  single-line `maybe_with_default`/`result_with_default` calls over a call argument, 12 outside
  `tests/` (`cse_census.sprout:115`, `tui/buffer.sprout:234`/`:248`, `tui/text.sprout:97`,
  `ide/document.sprout:42`, `examples/aoc_2025_day_5.sprout:20`, 4 more). Two caveats: a syntactic
  check cannot tell a top-level callee from a closure call, and `bench/vec_box/` uses the shape on
  purpose, so suppression ships first. Moot if the CPR-reach `P2` above lands — decide that first.
- [ ] `P3` **A nested `match` on a `Cons`-bound tail is not linted.** `match xs with | Cons h t ->
  match t with …` is what `[a, b | rest]` exists to flatten, and neither `list-shape-pattern` (which
  needs a chain ending in a literal `Nil`) nor `list-prefix-pattern` (a wildcard tail) covers it.

**Gates and diagnostics**

- [ ] `P3` **15 stdlib modules have no section in `docs/stdlib-reference.md`.** `args`, `bits`,
  `chan`, `http_middleware`, `linalg`, `log`, `mutable`, `process`, `repl`, `rng`, `stamped`,
  `task`, `template`, `test`, `version` — listed as `UNDOCUMENTED` in
  `scripts/stdlib_reference_gate.sh`, which fails once one gains a section until it leaves the
  list. Several (`task`, `chan`) have design docs but no API reference a user would find.
- [ ] `P2` **`just gate-audit` derives "CI runs task X" by grepping the workflow's COMMENTS.** The
  pattern matches anywhere in `ci.yml`, so prose invents requirements: a task named only in a
  comment counts as CI-run, and the English word "just" manufactures a task name. **Not a
  one-liner** — piping through `sed 's/#.*//'` makes assertion A correct and immediately breaks
  assertion C, because `just test` appears in ci.yml *only inside a comment* (CI actually invokes
  the split `test-stdlib-core-stage1`/`test-stdlib-compiler-stage1` plus `ci-fast-gates`). So the
  audit's current green rests on a comment match. Decide whether CI should invoke `just test` by
  name, or whether `test-package-resolution`/`test-stdlib-stage1` belong in `GATE_ONLY_EXCLUDE`,
  then strip comments.
- [ ] `P2` **`just ir-golden-diff` truncates each file's diff at 40 lines**, so DoD #12's "read the
  diff before regenerating" silently shows a prefix, cut marked only by a bare `---` that reads as
  ordinary diff punctuation. Caught 2026-08-15: the tool displayed through one symbol and silently
  dropped four others, noticed only by reconciling the reported set against what had been deleted.
  **The reliable review is `git diff tests/golden/ir/` AFTER snapshotting**, complete by
  construction — consider making that the documented workflow, raising the cap, or at minimum
  printing "(truncated, N more lines)".
- [ ] `P3` **`just test-file` reports a false green for tests that need `SPROUT_STDLIB_ROOT`.**
  REPL/analysis-service tests guard on it and no-op without it, printing `0 passed, 0 failed`
  followed by `SUITE PASSED` — an apparent pass that executed no assertions. Only the full
  `just test` runner sets the variable, so the trap is specific to fast iteration, which is exactly
  when a green is trusted. Fix: set it in `_test-file`, or make the guard fail loudly. A skip that
  reports success is the worst of the three options.
- [ ] `P2` **Robust suite pass/fail signalling + stdout flush.** The runner derives exit status by
  grepping stdout for `^SUITE FAILED`, which couples the machine gate to a human string, false-trips
  on a fixture printing it as data, and blocks golden-stdout tests from sharing the stream. Replace
  with a real nonzero exit via the existing `panic` extern. Separately, `print` has no
  `fflush`/`setvbuf`, so output is lost on a crash under capture; add
  `setvbuf(stdout, NULL, _IOLBF, 0)` at startup. The runtime edit needs approval per the
  builtin/runtime rules.
- [ ] `P1` **Stack-overflow diagnostic v2 — map native backtrace frames to Sprout source
  locations.** v1 catches the overflow on an alternate signal stack and panics with a native
  backtrace naming the recursing function; v2 turns symbol+offset into `file:line`. Options: (a) a
  per-thread shadow call stack pushed/popped in codegen prologues (precise, per-call cost —
  measure first); (b) emit DWARF line tables from codegen and symbolize post-hoc (no runtime cost,
  larger change; the principled end state); (c) a lighter `__sprout_set_current_loc` breadcrumb at
  statement granularity. Keep v1's named-frame backtrace as the fallback. Extend
  `just stack-overflow-smoke` to assert a `file:line`.
- [ ] `P3` **`--emit-ir --debug` is inert at the driver.** Both arms dispatch to
  `run_batch(..., "ir-typed", ...)` identically — the flag only shifts the argv start index and
  nothing reads it; the DWARF `just build-debug` produces comes entirely from `clang -g -O0`. Either
  emit Sprout source-level DWARF from the typed IR pipeline, or drop the flag so the CLI stops
  advertising a capability it does not have.
- [ ] `P3` **Wire `opt` optimization passes into the build pipeline** — add
  `opt --passes=mem2reg,instcombine,simplifycfg` before the clang step in `_build-stage`. Decouples
  Sprout's IR quality from clang's optimizer settings and makes it easier to inspect what survives.
- [ ] `P2` **`double_to_string` and `stdlib.math` have no differential harness.**
  `tests/stdlib/test_parse_double_differential.spr` is the shape — random inputs against an exact
  bigint oracle, a ULP bound per case, `--runs`/`--seed` knobs and a `just` recipe for the sweep —
  and it found three `parse_double` defects the suite had passed for months. `double_to_string`
  wants the round-trip (`parse_double(double_to_string(x)) == x` over random bit patterns);
  `stdlib.math`'s elementary functions want their documented ULP bounds held to a number, since
  `docs/math-transcendental-v0.md` states them and nothing checks them.
- [ ] `P2` **No gate reads prose, so a doc can claim its own feature is unimplemented.** Three
  places still said bigint-v0 Stage 1 had not shipped a month after it did — the policy doc's
  title (`DECIDED, UNIMPLEMENTED`), a `stdlib/bits.sprout` comment, and the stage heading — and
  nothing flagged any of them (`docs/bigint-arc-retro-2026-09-25.md` §4). A cheap 80%: fail when a
  doc's title says `UNIMPLEMENTED`/`pending` while its own `**Status:**` line says `DECIDED`, and
  list `stdlib/*.sprout` comments containing "still open"/"not yet" that cite a doc now marked
  decided. Syntactic and false-positive-prone, so it wants an explicit ack path like `seed-fp-ack`
  rather than a hard block.
- [ ] `P3` **The review ledger is per-worktree, so it cannot answer its own question.** It lives
  under `$GIT_DIR/claude-review/`, which for a worktree is `.git/worktrees/<name>/`, so a branch
  reviewed from another worktree reads as never reviewed. The bigint arc's four earlier review
  rounds are recorded nowhere machine-readable; only the two run from this worktree survive. Move
  it to `$(git rev-parse --git-common-dir)/claude-review/` — two sites in
  `scripts/review_ledger.sh`: `ledger_path` for the TSV, `ledger_sibling` for the findings and raw
  files — and keep the per-worktree column that already distinguishes rows.
- [ ] `P3` **`ir-golden-diff` catches new typeclass dictionary wrappers but does not say so.**
  Writing a two-arm `Maybe` decision with `and_then` pulled `Monad`'s whole superclass chain
  (Applicative + Functor eta wrappers) into every consumer of `stdlib.json`; the IR diff showed it
  and no test could. That is a second use for the gate beyond regression detection, currently
  reachable only by reading 60 files of diff by hand. Have the report name added/removed
  `__sprout_ir_eta___tc_*` definitions on their own line.

**Modules, prelude and bootstrap edges**

- [ ] `P3` **`import stdlib.prelude` silently doubles the prelude in the bundle.** The prelude has
  no `module` header, so `any_has_module_name` is false for a file whose only import is
  `stdlib.prelude` and the explicit import supplies the single copy — but add *any* import of a
  module-bearing file and the auto-prepend switches on, giving two copies of every prelude instance
  and `Overlapping instances for Semigroup`. No such import remains in the repo, so it is latent.
  Worth a lint that rejects `import stdlib.prelude` outright; there is no case where it is correct.
- [ ] `P3` **A `no_prelude` file whose top-level name matches a C runtime symbol dies with
  `duplicate symbol` at link, having type-checked clean.** `fn str_len(s: String) -> Int = 99`
  passes `--phase check` (the local define shadows the extern, the documented `lower_extern_decls`
  behaviour) then fails against `runtime/sprout_runtime.c`. A normal file is immune — its entry is
  qualified. Pre-existing, verified against builds either side of the `no_prelude` floor with
  byte-identical outcomes: the hazard is the bare namespace a `no_prelude` file already has, and it
  applies to any top-level name colliding with a runtime symbol.
- [ ] `P2` **An `import` after `no_prelude` is silently dropped.** `no_prelude` then
  `import demo.tokbar (bar_value)` fails as `Unknown variable: bar_value`; the reverse order works.
  `module_loader.collect_imports_from_lines` stops at the first line that is not `module`, `import`,
  blank or a comment, so `no_prelude` ends the import scan. Spec §3.1 calls `no_prelude` a header
  line like `import`, with no order among them. Found while fixing #422.
- [ ] `P3` **A module sees a `no_prelude` entry's types bare, unimported.** A module imported by a
  `no_prelude` entry that declares `type Token` can write `fn tok_value(t: Token)` with no import,
  and `Token` resolves to the entry's type. The entry keeps the empty module name, so its types are
  canonical-bare, and `bundler.qualify_type_name` falls through to the bare name. A library then
  compiles or not depending on who imports it. Found while fixing #422.
- [ ] `P3` **A constructor name may contain a dot.** The lexer reads `json.JsonEncode` as one ident
  and the parser accepts it as a constructor name, so `type Doc = | json.JsonEncode | Other`
  compiles, and `json.JsonEncode` in an expression then means this local constructor, not a member
  of the `json` import. The #422 check rejects it only when the name resolves to a type. Reject a
  dotted name in `parse_type_constructor_def`.
- [ ] `P3` **An unknown type in an imported module is reported at that module's line:col.** The
  file is not named, so the CLI and the LSP read the position against the entry: `fn unk(x: Nope)`
  on line 4 of `demo.unkdep` gives `4:8: ERROR: bundle: unknown type …` for a 3-line entry.
  `bundler.validate_type_names` runs on qualified decls, which carry no path, so it cannot apply
  `pos_for_entry` the way `find_one_ctor_clash` does (#425). Found while fixing #422.
- [ ] `P2` **REPL SIGSEGV on a tuple that nests let-bound tuple variables.**
  `let t1 = (1,3,"foo",true)` then `let t2 = (t1, t1)`, then evaluating `t2` gives
  `SIGSEGV (no current function set)`. Flat tuples are fine. **Not a codegen bug** — the
  equivalent compiled program renders correctly under both paths — so the fault is in the REPL's
  per-expression eval path (`analysis_service_driver.op_eval_expr_in_source`), most likely in how it
  synthesizes the throwaway program referencing prior session let-bindings. Next step: dump the
  synthesized source for `t2` and compile it standalone.
- [~] `P2` **Dispose of the obsolete negative corpora.** Entrypoint validation landed
  (`validate_entrypoint`): a defined `main` must be zero-arg, `Unit`/`Int`-returning and concretely
  `!{IO}`, gated via `test-executable-errors`. **Remaining:** the "missing main" check
  (`executable_error/missing_main`, xfailed) cannot be a type-check error — at check time the
  compiler cannot tell a library check from an executable build, and main-less library files are
  legitimate; it needs an explicit executable-vs-library compile mode threaded from
  `--emit-ir`/codegen, where `has_user_main` is already computed. Also decide separately whether to
  delete `runtime_error/main_arity_mismatch` (a mislabeled duplicate) and `parity_*` (byte-parity
  against the retired reference, no golden).

### 7.5) Type Classes

- [ ] `P3` **A constraint materialises its whole superclass chain, so idiomatic combinators cost
  more than the code they replace.** `and_then` carries `where Monad m`, and `Monad m where
  Applicative m where Functor f`, so one call in `stdlib/json.sprout` emitted four eta wrappers
  (`Monad.flat_map`, `Applicative.pure`/`map2`, `Functor.fmap`) into every consumer of the module,
  where the `map` it replaced needed one. It was written back as a plain `match`, which is the
  wrong trade to have to make — `docs/idiomatic-sprout.md` recommends the combinator. The prelude
  already records the precedent that this bloat is real (an import adding 62 unused wrapper bodies
  grew a consumer ~12%). Drop superclass slots no call site reaches, or devirtualise the chain
  when the instance is statically known.
- [ ] `P3` **Adding a stdlib instance for a builtin type breaks downstream duplicates, unrecorded.**
  A module with its own `instance Eq Bytes` stopped compiling at 2c3924f7 — `Overlapping instances
  for Eq`, pointed at the user's declaration, not at the stdlib that now also supplies one. It
  fires transitively, since `stdlib.net`/`http_client`/`http_server` all pull in `stdlib.bytes`.
  `docs/eq-ord-double-v0.md` §Compatibility is the precedent: it names the duplicate-instance break
  as the one way this lands and records the check against `uncharted-suns`. That check was run for
  `Eq Bytes` (clean) but written down nowhere. Make it a step: every new instance on a builtin type
  records the downstream check and its result, and the diagnostic should name the competing module.

- [ ] `P2` **Two same-class constraints differing only in their arguments are still rejected.**
  `where Boxed (Tagged k), Boxed (Tagged j)` gets two hidden slots now — `ast.dict_slot_key` names
  the arguments — but `infer.check_indistinct_constraints` still rejects it (spec "Two constraints
  of one class must not differ only in their arguments"). What is left: the eta fallback that
  takes the only slot carrying a method (`lowering.find_forwarded_method_any`) must pick by key.
  Then drop the rule and its spec paragraph, and turn `type_error/same_class_heads_share_dict_slot`
  and `tuple_heads_share_dict_slot` into run fixtures.
- [ ] `P2` **A class method's `.iface` scheme quantifies fewer binders than the live
  registration.** `iface_codec.method_scheme` quantifies the CLASS parameters only, so a
  method-level constraint head is keyed by source NAME, while `infer.register_class_method_over`
  quantifies the method's own variables too and keys by position (`#pos:k`). A decoded ClassInfo
  cannot resolve those dictionaries the way the locally-declared class does. Dead today —
  `decode_iface_file` serves only `--check-iface` — and live as soon as precompiled modules load
  interfaces into an env. The fix is one shared binder list and one shared token function across
  the two modules, which is why it is not a two-line patch.
- [~] `P1` **`__unresolved_*` dictionary sentinel leak.** The user-facing symptom (a SIGSEGV when a
  nested constrained dictionary is unsatisfiable) is fixed at check time by `resolve.sprout`, which
  rejects such programs before codegen. The sentinel *mechanics* — a single resolution path that
  would make the null-fill structurally unreachable — remain parked as M3b; see
  `docs/dict-resolution-north-star-plan-2026-06-30.md` for the sentinel-flow map and why M4/M5/M3b
  were parked.
- [ ] `P1` **An ambiguous class-method reference is diagnosed at codegen, with no source location.**
  The three `tests/conformance/emit_error/ambiguous_forwarded_*.spr` shapes pass `--phase check`
  and then fail at emit with `ast_to_ir: unbound variable '__eta_unresolved_<Class>_<method>'` —
  a compiler-internal sentinel, no line, no column. So the LSP and every check-phase gate accept
  the program and the user learns at build time. Two of the shapes are unresolvable identity
  (see below); the cross-class one is genuinely ambiguous source and wants a real
  "ambiguous method reference: `blank` is declared by both `Blank` and `Sizer`" at check time,
  with a way to say which — Sprout has no qualified `Blank::blank` form. Ambiguity detection
  belongs where constraints are known, not at slot-selection time.
- [ ] `P2` **An eta'd class method under two same-class constraints is rejected, not resolved.**
  The occurrence's type is a fresh tvar that no `@eta_fwd` marker names, so lowering cannot tell
  which constraint it belongs to; it now declines instead of taking the first slot, which was
  right only when the occurrence belonged to the first constraint and otherwise ran one
  instance's code at another's type. Both probes are pinned in `tests/conformance/emit_error/`.
  The identity gap is upstream: the site's tvar is unified with the constraint's during
  inference but neither the node type nor `final_subst` records it (verified — forcing
  `commit_fn_decl`'s substitution unconditionally does not relate them). Same root cause as the
  M3b entry below, and it wants the same canonicalization; until then the shape is a hard error.
- [ ] `P2` **Complete the M3b eta→single-authority collapse (blocked on tyvar canonicalization).**
  Lowering's `try_eta_in_class`/`try_eta_forwarded_without_class` remain a second resolution
  authority for one shape: a polymorphic (type-variable-head) forwarded value-position class method.
  `resolve.method_ref_evidence` emits `EvUnresolved` for the non-concrete head; making it emit
  `EvForward` produces a key that misses `ctx_fwd`'s source-name key — the name-vs-generalized
  divergence. Durable fix is canonicalizing tyvar identity in the dict resolver, then deleting
  `try_eta_*` and adding the deferred `TFunc` gate and marker-miss test. The canonical-identity work
  landed a positional fix in `infer.sprout`; this is the one place in the subsystem it did not
  reach, since it lives on the lowering side.
- [ ] `P2` **Widen the `@inst:` key to full-head matching (GHC `FlexibleInstances` position).**
  Would make `instance C (List Int)` and `instance C (List Bool)` both legal *and* sound, lifting
  the distinct-type-variables restriction on instance heads. Exact-string widening does not work
  (`instance Eq (Maybe a)` keys `Maybe a` while a call site at `Maybe Int` misses), so it means
  unifying against stored heads with a most-specific-wins rule — instance-specificity *semantics*,
  so Design Change Process. Blast radius: 13 `@inst:` sites in `infer.sprout`, 2 in
  `resolve.sprout`, both key writers, lowering's parallel `instance_table`, plus seed and golden IR.
  GHC pairs the relaxation with full-head matching; they arrive together.
- [ ] `P2` **`Validation` type + error-accumulating `Applicative`** — the killer app (form-style
  validation collecting *all* errors). Needs its own type (`Valid a | Invalid e`) because a type
  admits one `Applicative` and `Result`'s is fail-fast; the instance requires `Semigroup e`.
- [ ] `P2` **`Traversable` for `Result e`, and the effectful-mapping ergonomics.**
  The class, `traverse`, `sequence` and the `Vec` instance landed (spec §8.5); `Result e` has none.
  **The ergonomic gap is the reason to finish it:** `list_map` stays pure by design
  (`docs/effect-polymorphism-policy-v0.md` §5: order unpinned), so mapping an `!{IO}` function is
  spelled `list_reverse(list_fold(\ (acc, x) -> Cons(f(x), acc), Nil, xs))` — correct and ordered,
  but clumsy. `traverse` pins its order by contract and is the admissible fix; widening `list_map`
  is not. Still deferred with `Validation`.
- [ ] `P2` **Nested return-type dispatch: `map2(g, pure(x), pure(y))` miscodegens when `f` is fixed
  only by context.** When an Applicative method's argument is itself return-type-dispatched and the
  concrete `f` comes only from the surrounding context, codegen emits an undefined `@map2` → link
  error. The checker types it fine; only instance *selection* fails to propagate from the result
  anchor through `pure`'s own dispatch into `map2`'s argument. Workaround is a typed intermediate.
  Fix: propagate the resolved result-type instance to nested return-dispatched arguments.
- [ ] `P2` **Route `<`/`<=`/`>`/`>=` on ADTs through `Ord` dispatch.** A first attempt was REVERTED
  2026-06-13: replacing the load-bearing implicit Int/Char unification with pure `apply_subst`
  inspection left polymorphic TVar operands unresolved and cascaded into unrelated inference (a
  partial application typed as `String` instead of `Int -> String`, SIGSEGV at runtime). Eager
  binding breaks polymorphic `Ord`; lazy dispatch breaks partial-app inference — the two
  requirements are at odds at the operator site. **Redesign: a POST-PASS analogous to
  `resolve_dispatch_typed_expr`** that rewrites the comparisons after substitution is finalized,
  when the type is either concrete or genuinely polymorphic via `where Ord a`. See also the
  closed-operator-family entry in §1, which constrains any fallback to keep the `Double`
  short-circuit.
- [ ] `P2` **Revisit `compare`'s return type.** It returns `Int` with a sign convention, following
  OCaml/C/Java; Sprout's design family (Haskell, Elm, PureScript, Rust) uses
  `type Ordering = LT | EQ | GT`. `Int` admits ~4B nonsensical values for a 3-state outcome and
  `compare(x, y) == 1` is a silent bug that compiles; the language already has `Maybe`/`Result`
  explicitly to avoid magic-number conventions, so this is self-inconsistent signalling. **Profile
  nullary-ADT-ctor return cost before deciding** — that is the one empirical input, and (d) is
  currently handwaved. Migration touches the class declaration, the deriving emitter, the four
  `ord_*` helpers, every instance, and every call site doing `compare(...) < 0`. Options: keep
  `Int`, migrate hard, or `compare_int` + a new `compare`.
- [ ] `P2` **`wrap` instance lifting — reuse the base type's instances as the wrap type.**
  `wrap Age = Int deriving (Num)` generating instances that unwrap → delegate → rewrap, **while
  `Age` stays distinct from `Int`**. This is Haskell's GeneralizedNewtypeDeriving, verified to
  preserve distinctness — NOT a coercion (auto-wrap destroys mistake-prevention, auto-unwrap loses
  the wrap type; both are a transparent alias). Scope: operators with all-wrapped operands
  (`age1 + age2 : Age`, `name1 ++ name2 : Name`). `++` falls out for free since it already
  desugars to `Semigroup.append` — route the witness to the lifted instance, not the `String`
  peephole. Out of scope: mixed `age + 1` with a bare literal (needs numeric-literal polymorphism);
  `Eq`/`Ord`/`ToString`, which now derive **structurally** (`docs/deriving-wrap-v0.md`) — lifting
  must not silently re-render them. `docs/coercions-and-literals-v1-draft.md` Case B.
- [ ] `P3` **The stored test is one declaration deep, so an index threaded through another
  indexed type is still constrained.** `ast.type_expr_stores` is syntactic: `u` in a field of
  type `Tagged u` counts as stored even where `Tagged` discards it, so `type Outer u (..)
  deriving (Eq) = | Outer (Tagged u)` fails with `No instance of Eq for main.Metres` although
  nothing of that type is stored. Reaching further means resolving `Tagged`, and `deriving`
  runs in the bundler with no types; the same conservatism bounds `linear_check`'s `@phantom:`
  marker, which shares the predicate, so any fix moves both. Pinned by
  `tests/conformance/type_error/deriving_phantom_nested_still_constrained.spr`; the hand-written
  `instance` is the workaround (`docs/wrap-type-params-v0.md` §Limits).
- [ ] `P2` **Investigate qualified imported-constructor access** (low confidence).
  `import stdlib.foo as f` then `f.MkCtor(x)` gave `Unknown variable: f.MkCtor` for a parametric
  ADT, while the *type* `f.Box` and functions `f.mk_box` resolved fine and a non-parametric ADT's
  constructor resolved. Confirm whether qualified constructor access is intended syntax at all
  before treating it as a bug.
- [ ] `P2` **Deriving/specialization follow-ups** once the core class system is stable.
- [ ] `P3` **`deriving (ToString)` in the prelude depends on declaration order.** On a prelude
  type declared above `instance Semigroup String`, it fails with "No Semigroup instance for String
  in instance method to_string"; below it, it works. `deriving (Eq)` works anywhere. Instance
  resolution should not depend on order. Until fixed, prelude types hand-write `ToString`
  (`ControlFlow` does).
- [ ] `P3` **`Alternative` class + generic `or_else`** — deferred until a *second* lawful instance
  exists (e.g. a parser combinator type); with only `Maybe` it is single-instance ceremony, and
  List's lawful instance (`++`) is already `Semigroup`.
- [ ] `P3` **`Sliceable` class over `String`/`Bytes`** — declined, revisit if a generic *algorithm*
  over sliceable input appears. Probed working; declined because the index unit diverges (5
  codepoints vs 5 bytes, silently valid both ways), dispatch costs 3 GC root pushes and an indirect
  call against 1 and a direct one, and data-last `vec_slice` bars `Vec` as a third instance.
- [ ] `P3` **Monad-generic `do`** — a bind through `Monad.flat_map` for pure monads (parsers,
  State). Propagation is `try`'s job (`docs/try-propagate-v0.md`), and there `<-` becomes
  effect-only, so this needs its own bind syntax. `flat_map`'s function is pure, so no IO after a
  bind. No user yet.
- [ ] `P3` **`ZipList` newtype** — the pairwise `Applicative` for lists, distinct from the prelude
  instance's cartesian product. `wrap ZipList a = List a` with its own `map2`/`pure` (`pure`'s
  infinite/repeat semantics need a bounded variant).
- [ ] `P3` **Adoption sweep for `and_then` — largely net-negative; treat as closed.** Two measured
  traps. `maybe_or_else` is *strict*, so sites with a real lookup in the `Nothing` arm change
  meaning. And `and_then(\x -> body_capturing_locals, scrut)` allocates a **heap closure per call**,
  which the original `| Just x -> …` arm does not — so it only wins for point-free arms passing
  a static function pointer. The win-set after that filter is ~4 sites, not worth a compiler reseed.
  **Do not convert capturing-lambda arms, and never on hot paths.**

### 7.6) Editor integration — LSP server and the JetBrains plugin

- [ ] `P3` **The IntelliJ lexer's keyword set lags `lexer.is_keyword`.** `SproutLexer.KEYWORDS`
  says "Exactly `is_keyword` … Keep in lockstep" but lacks `for` and `try`, so neither highlights
  as a keyword. Nothing checks the claim: add both, and a gate that diffs the two lists so the next
  keyword cannot drift the same way.
- [ ] `P1` **Wire the remaining three LSP features whose compiler API already exists:** formatting
  (`formatter.format_source`), document symbols (`symbol_inventory_in_source`), completion
  (`complete_in_state` — REPL-line-shaped, so it wants the document line up to the cursor). Each
  goes in with its own capability; `lsp-smoke` asserts *advertised ⇒ answers* and skips absent
  ones, so the gate arms itself as they land. **Check each API for the hover trap:** an `analysis_*`
  name in `stdlib/compiler.sprout` is a builtin that crosses the analysis-service fork and loses
  package roots, while the in-process compiler modules keep them.
- [ ] `P1` **The LSP re-checks cold on a `didChange` of an imported module.** `LspState` now holds a
  session-long `bundler.LoadEnv` and every re-check reuses it (0.30s → 0.04s warm), but the memo
  does not notice an imported module changing. What remains is the invalidation policy — which is
  why it was never folded into the feature work.
- [ ] `P2` **Hover answers only for top-level names; a local or a parameter returns null.** Inherent
  to the mechanism, not a handler defect: `scheme_of_expr_in_source` appends
  `let __repl_source_value = <expr>` at **module scope**, where a local is not in scope, so the
  typecheck fails and hover declines rather than guessing. Fixing it needs a position-aware lookup
  — the type of the name *at that point* — i.e. the typed AST, the same capability
  `language-server-roadmap.md` §5.1's span refactor is about. Worth pairing rather than doing
  twice. The user-visible cost is larger than it sounds: parameters and `let`-bound locals are most
  of what a reader points at.
- [ ] `P2` **Diagnostic ranges are zero-width.** `lsp_driver.diag_range` sets `end == start`, so
  clients get a caret rather than an underlined token. Widening to the offending token's extent is
  small; full multi-token spans need the §5.1 span refactor.
- [ ] `P2` **`try_extra_roots` consults only the first package root, and checks nothing.**
  `module_loader.sprout:200` matches `[root | _]` and returns `<root>/<dotted-as-path>.sprout`
  unconditionally — no existence check, no attempt at the rest — so a project needing two
  package roots silently resolves everything against the first, and the plugin's
  `pathSeparator`-joined multi-root setting is single-root in practice. A known deferral (the
  in-file comment says "pure — no filesystem existence check") that is now user-visible through a
  settings field implying otherwise. Fixing it needs IO in the resolver; until then the plugin's
  settings comment should not promise multi-root.
- [ ] `P2` **The plugin cannot auto-detect a toolchain for a project that is not a Sprout
  checkout.** `SproutSettings.detectFrom` walks up for `build/sproutd` **and** `stdlib/` together;
  `uncharted-suns` — Sprout's only real user — has neither within six levels, so it can never
  auto-configure. Options: honour `SPROUT_ROOT`, remember the last working toolchain across
  projects, or keep making the unconfigured state persistent (the banner landed; see the re-collect
  item below).
- [ ] `P2` **The LSP registration test is inert on the default build platform.** IntelliJ IDEA
  Ultimate carries the LSP *API* but declares no `com.intellij.modules.lsp` module (checked in
  2024.2.5 and 2025.1.7.2), and our optional `<depends>` keys on that module, so the descriptor
  cannot load and `SproutLspRegistrationTest` reports itself inert. `SPROUT_IDE_HOME` pointed at
  RubyMine arms it, but CI has no such IDE. Options: find the smallest downloadable IDE declaring
  the module, or check the module list of the verifier's IDEs directly.
- [ ] `P3` **A rendered type shows module-qualified names for anything declared in the buffer.**
  `:type make_box` for a source-local `type Box a` answers `forall a. a -> $entry.Box a`, and
  `app.session.Box` in the REPL. Diagnostics do not have this problem — every `Diagnostic` goes
  through `compiler.diag_error`, which applies `source.strip_entry_names`; a rendered scheme has no
  equivalent boundary. **`strip_entry_names` alone is not the fix:** it addresses `$entry.` and not
  `app.session.`, which is what a REPL user actually sees. Wants a display-name rule that knows
  which module the reader is in. The same qualification leaks into `where`-clause rendering.
- [ ] `P3` **Surface effects more ambiently than hover.** Prior art, verified 2026-08-18: **there is
  no established convention for marking purity in an IDE.** LSP 3.17's ten semantic token modifiers
  include none for effects (`async` is nearest); Haskell draws no such distinction
  (`hls-semantic-tokens-plugin` emits token types only, modifiers `[future]`); rust-analyzer shows
  the extension path with custom `unsafe`/`consuming` modifiers; Flix — effect-typed, closest
  analogue — puts effects in hover text, which is what Sprout now does. If we want more, **inlay
  hints** beat colour: effects are polymorphic (`!{e}`) and a binary pure/effectful colour cannot
  express a variable. Unverified and worth checking first: whether the JetBrains LSP client can map
  custom semantic token modifiers to text attributes at all.
- [ ] `P3` **The unconfigured-plugin banner does not re-collect when the toolchain appears.**
  `EditorNotifications.updateAllNotifications` is called only from `SproutConfigurable.apply()`, so
  building `build/sproutd` with a project open leaves the banner up until some other editor event
  forces re-collection. Harmless (stale, never wrong in the other direction); the natural fix is a
  VFS listener on the resolved sproutd path, which is more machinery than the annoyance justifies
  today.
- [ ] `P3` **LSP4IJ for Community-edition IntelliJ IDEs.** The JetBrains LSP API is unavailable in
  open-source IDEA builds and Android Studio, so the plugin's LSP layer is paid-IDE only
  (highlighting works everywhere). LSP4IJ (Red Hat, EPL-2.0, 2024.2+) would close that at the cost
  of a third-party runtime dependency.

### 7.7) Codebase-memory-mcp indexing for Sprout

> Plan of record: `docs/tree-sitter-sprout-support.md`.

- [ ] `P2` **`codebase-memory-mcp` cannot index Sprout, and the half-built fix has been unmerged
  since April.** CBM's project for this repo holds 1 node and 0 edges, so `search_graph`,
  `get_architecture` and `trace_call_path` are useless on Sprout and on `uncharted-suns`. The C-side
  wiring exists on two unmerged branches of the fork `cthulhu666/codebase-memory-mcp`
  (`codex/sprout-support` tip `94d30b0`, clean feature commit `434926e`;
  `codex/sprout-index-persistence-fix` `70f55b0`). It did not stall on Sprout but on a CBM *core*
  defect recorded in `94d30b0` — the direct page writer produces inconsistent on-disk graphs for
  mixed projects. The fork has never fetched `upstream/main`, so the first question is whether that
  is fixed there. Scope decided: full expression coverage in one pass, since without `CALLS` edges
  the index offers little over grep.
- [ ] `P2` **`tree-sitter-sprout/` is ungated and has drifted from the language.** No `justfile`
  recipe runs `tree-sitter` at all, so nothing has ever checked the grammar against real source: 14
  verified divergences, including records (`( f: T )` vs the grammar's `{ }`) and field access
  (postfix `e.f` vs `get e f`), with `extern` (152 declarations), `deriving` (84 clauses), `wrap`,
  `linear`, `(..)`, `|>`, effect rows, `let..in`/`let..else`, `with (…)` and `++ >> << .. %`
  absent outright. **Build the measuring instrument first** — `scripts/ts_parse_coverage.sh` over
  the whole corpus plus a `just tree-sitter-test` recipe — because tree-sitter degrades to `ERROR`
  nodes silently, so without a corpus-wide rate you cannot tell 95% coverage from 40%.
  `sproutd --analysis-service` is a ready-made differential oracle: diff
  `symbol_locations_in_source` against the `queries/tags.scm` captures per file.
- [ ] `P3` **`.spr` was never registered with CBM.** The April branch added only `.sprout` to
  `EXT_TABLE`, missing **726 of 892** Sprout files — the entire test corpus. No collision (CBM has
  no `.spr*` entry). Also needs a `cbm_is_test_file()` case, since `.spr` files are non-importable
  entrypoint scripts rather than library modules.
- [ ] `P3` **CBM's `TEST(sprout_basics)` asserts on syntax Sprout does not have** —
  `type Person = { name: String }`, matching the grammar's wrong record rule rather than the
  language. A test written against a buggy grammar passes and locks the bug in; rewrite to
  `( name: String )` alongside the grammar fix.

### 9) Language/stdlib primitives surfaced by graphics

> The graphics/game engine and galaxy game were extracted to the **uncharted-suns** repo
> (2026-07-30); their roadmap lives there. These are the pure language/stdlib items graphics work
> surfaced.

- [ ] `P2` **A LOWERCASE type name in a parameter/return annotation is silently a fresh type
  VARIABLE, so a typo'd type is accepted and the error lands somewhere else entirely.** The
  uppercase case is already rejected at the annotation by the bundler's `unresolved_in_types` —
  measured, do not re-implement it. Open is lowercase: `fn dot(a: vec3, b: vec3) -> Double` with
  `Vec3` declared right above compiles clean, then field reads off the tyvar park deferred
  obligations, numeric defaulting pins them to `Int`, and the failure surfaces as
  `Return type mismatch: Int vs Double`, ~1700 lines from the annotation in the reporter's tree.
  Same mechanism as the numeric-defaulting item in §1. **Suggested rule:** reject or warn on a
  lowercase annotation matching a declared type case-insensitively; a blanket "type variables must
  be declared" rule is the Rust answer, and much larger.
- [ ] `P2` **An UPPERCASE name in a type declaration's parameter list becomes a type VARIABLE and
  shadows the type it names.** `wrap Metres Int = Int` means `wrap Metres a = a` and compiles — a
  value built that way holds a String, confirmed by running it. `wrap W String = List String` makes
  `W Int` a `List Int`, the real `String` unreachable inside the declaration. Spec §5.6 is explicit
  that a parameter is "a lowercase type variable", so this is an implementation divergence, not a
  design choice. One shared helper is responsible — `collect_ident_list` in `parser.sprout` matches
  any identifier — so `type`, record, `class`, `wrap` and `type alias` all inherit it and one case
  test fixes all five. Narrower than the lowercase-annotation item above: rejecting a leading
  uppercase in a parameter list needs no name resolution. Zero occurrences in `stdlib/`, `ide/`,
  `examples/` or `tests/` over every form and parameter position, so this breaks nothing here.

- [ ] `P2` **A `wrap` type in a user-defined function's annotation does not canonicalize across
  modules.** `fn f(v: linalg.Vec3)` in user code sees `linalg.Vec3` as distinct from the value's
  `stdlib.linalg.Vec3` (Call type mismatch). Values flow fine into the defining module's own
  functions, so stdlib APIs work; only user-written helpers over imported wrap types break. Likely
  in the module-qualified-type-identity machinery.
- [ ] `P2` **Unbox small fixed-shape numeric records** (`Vec3 {x,y,z}` as 3 raw f64s, not a heap
  pointer) — the ergonomic-and-fast path for individual small vectors, additive on top of the
  tested flat-buffer foundation.
- [ ] `P2` **Native `Float` (f32) type + `Vector Float` unboxed path.** Doubles-everywhere is
  correct today; float32 earns its keep only when Sprout owns bulk GPU-bound buffers. Decide by
  measured buffer/upload cost.
- [ ] `P3` **Remove the dead C `json_parse` tree-parser.** Extract the shared low-level helpers
  (`sprout_json_skip_ws`/`_parse_string`/`_parse_hex4`) still used by the by-key extractor, then
  delete `json_parse`/`_value`/`_array`/`_object`/`_number`/`_ok_result`/`_err_result`/`_reverse_*`
  and drop `json_parse` from `APPROVED_BUILTINS`.
- [ ] `P3` **`deriving (Enum)` breadth** — `values`/`enum_values`, optionally
  `succ`/`pred`/`min_bound`/`max_bound`. `values : List a` is the most-requested (Java `values()`,
  Kotlin `entries`, C# `Enum.GetValues`, Scala 3 `values`) and rides the same return-type-dispatch
  path as `from_ordinal`, so no new design work. **A consumer exists out-of-tree** (uncharted-suns):
  without it, code needing every variant must list constructors by hand, which rots silently the
  moment one is added — the failure `values` exists to prevent, and one an exhaustiveness checker
  cannot catch because a hand-written list is not a `match`. Left at `P3` per the report; raise on a
  second consumer.

### 10) Windows port and cross-platform runtime

> Design, milestone breakdown and the verified prior-art survey: `docs/windows-port-v0.md`. Items
> below are the execution units; the doc is the source of truth for *why* each is shaped the way it
> is.

- [ ] `P1` **Windows Milestone A — compile *to* Windows.** Umbrella, **parked after W2
  (2026-08-16) deliberately** — W0a/W0b/W1/W2 and the `windows` CI job are all on master, every
  gate green, no branch outstanding. Remaining: W3 (Winsock, files, the `VirtualAlloc` arena,
  threads, console), the async-DNS `pipe()` swap no Windows readiness poller can watch, W4 (crash
  diagnostics) and W5 (first `.exe`, linked and *run* under MSVC). Driver: uncharted-suns ships on
  Steam and links this repo's `runtime/*.c`. Scope is the runtime only — codegen already
  cross-compiles to clean Win64 COFF, gated by `just windows-ir-gate`. `docs/windows-port-v0.md` is
  the authority: §5.1 is the resume point, §6 the measured W3 surface inventory, §4.3.1 the
  `WSAPoll` decision with the AFD/wepoll and IOCP re-open triggers.
- [ ] `P2` **POSIX `<regex.h>` has no MSVC equivalent.** One use, `regex_compile_ere`, via
  `regcomp`/`REG_EXTENDED`. Filed separately from W3 because stubbing it removes a
  **language-visible** feature rather than an internal capability — vendor a small ERE
  implementation vs. narrow the feature, decided on its own terms. **DECISION PENDING (owner) — W3
  should not start without it**; `sprout_runtime.c` stops on this include at line 7, so it is also
  W3's first ordering constraint.
- [ ] `P2` **`proc_run` on Windows via `CreateProcess`.** `fork`/`execvp`/`pipe` (3/3/7 occurrences)
  → `CreateProcess` + anonymous pipes, preserving the deadlock-safe separate stdout/stderr
  capture. Not needed by the shipped game so not a Milestone A blocker; it *is* needed by Milestone
  B and the game's offline bake. Note `analysis_service_driver.sprout:751` shells out via
  `["sh", "-c", …]` and there is no `sh` on Windows, so the driver needs its own fix beyond the
  builtin. **DECISION PENDING (owner):** implement, or ship a loud stub? A stub is a legitimate
  answer, but that is a product call.
- [ ] `P3` **Milestone B — run the *compiler* on Windows.** Everything outside the runtime: the
  `justfile` and `scripts/*.sh` are bash, the bootstrap-seed flow assumes a POSIX shell, `mise`
  provisions the toolchain. Realistically MSYS2 or WSL rather than a native port. Strictly
  downstream of Milestone A — Sprout is self-hosted, so a Windows-native `sproutc` is itself a
  program whose runtime must already be ported. Also gates the `stdlib.path` V1 entry's Windows
  separator/drive-letter work.
- [ ] `P3` **An indexed `Vec` is far slower than an unboxed constructor, and the accessor is not
  why.** A ten-limb read+rebuild costs **19.7 ns** on a constructor of `Int` fields against
  **250 ns** on `Vec` (`docs/bigint-v0.md` §9 Stage 4) — the reason `stdlib/crypto/p256.sprout`
  abandoned `Vec`. This entry used to blame `vec_get`'s `Just` box and propose an unboxed
  accessor; that was built, measured at no gain (2 ns/read either way) and dropped. Correctly, but
  not for the recorded reason: both arms were already CPR-unboxed, so neither had a box to remove
  (`bench/results-2026-09-28-vec-box-tax.md`). The real gap is representation — a scalar
  constructor is unboxed outright
  (`type_is_non_heap_scalar`) while a `Vec` is a heap object behind a call — and no accessor
  closes it. Reopen only with a measurement that indicts something specific.
- [ ] `P3` **`bigint.from_string` is still quadratic in the digit count.** The codepoint walk is
  gone — the scan is over bytes — but `mul_small(acc, radix)` per digit is O(limbs), so the
  parse stays Θ(n²): 39 / 99 / 422 ms at 2000 / 4000 / 8000 decimal digits. Peeling seven digits
  at a time into an `Int` and folding them in with one `mul_small` by 10^7 would divide the
  bigint operations by seven without changing the shape; subquadratic needs divide-and-conquer.
  Nothing reaches this at the 64-hex-digit widths crypto callers use.
- [ ] `P3` **An ECDSA P-256 verify spends 21% of itself in one scalar inversion.** The field is
  now `Felem` (Montgomery, 26-bit limbs) and a verify is 5.4 ms, but `modular.mod_inv` mod *n* is
  still `BigInt` at **1.11 ms** (`docs/bigint-v0.md` §9 Stage 4). A second Montgomery field for
  the group order would remove most of it — `scripts/gen_p256_felem.py` already emits everything
  but the constants, so it is largely a parameter change. Shamir's trick on the two scalar
  multiplications is a further ~1/3 and is independent. P3 rather than P2: 5.4 ms is no longer a
  deployment problem, and the 484-vector Wycheproof suite makes either change safe to attempt.

## Design Roadmap

> Forward-looking design and soundness priorities, and the V1 roadmap. The sections above are the
> engineering execution log.

### Fundamentals-review residuals

The 2026-07-03 adversarial review's campaign (W1–W11, decisions D1–D5) is otherwise landed; full
findings and probe programs are in `docs/fundamentals-code-review-handoff-2026-07-03.md`, and the
effect half is in `docs/effect-enforcement-v0.md`. Read the handoff doc's §2 for *why* the effect
deferral happened, not for current behaviour. Still open:

- [ ] `P3` **W9 remainder**, and **T11**, which is gated on the iface arc.
- [ ] `P3` **`resolve`/`lowering`'s `is_type_var_name` lacks the dot-guard `infer.is_lowercase_name`
  has** — a dotted name is never a type variable. Latent hardening left over from the
  module-qualified-type-identity work (`docs/module-qualified-type-identity-design-2026-07-10.md`).

### Compiler and codegen roadmap

- [ ] `P3` **Split the field-kinds `'s'` byte into `'s'` (String, heap) and `'c'` (Char, scalar)**
  in `stdlib/compiler/field_kinds.sprout` so Char fields stop being conservatively over-rooted. A
  single-file edit now the encoder is consolidated. Trigger when Char field rooting becomes
  measurable, or on the next cleanup pass.
- [ ] `P3` **Expression-side list-literal sweep.** Rewrite multi-element `Cons(a, Cons(b, …))`
  *constructions* in `ast_to_ir.sprout`, `compiler.sprout` and similar to `[a, b]` literals, in
  batches with a seed refresh per batch. `docs/style-guide-v0.md` §8 has the policy on what to
  rewrite versus leave (sugar wins for 2+ heads; single-head constructions and `| Cons x rest ->`
  arms stay long-form).
- [ ] `P3` **Arity-aware types (currying Part 3 / Approach A).** The landed n-ary arity check
  enforces only at direct call sites; under-application *through a function-typed value* is not
  caught, because `TFunc` is curried and carries no arity. A gives the type system a real n-ary
  arrow (or an arity tag on `TFunc`) so arity flows through unification and generalization. Large
  type-system change, and A deletes the current checker pass. Likely deferrable indefinitely —
  most residual cases already surface as function-vs-expected mismatches.
  `docs/currying-and-pipe-decision-v1.md`.
- [ ] `P3` **Operator sections (`_ * 2`, `10 - _`), deferred to v2.** The parser represents
  operators as a distinct `BinaryExpr` node, so sections need a second small handler beside
  `desugar_placeholder_call`.
- [ ] `P3` **Span the remaining `no_pos()` error sites: codegen / IR-emit errors (`IrLinesErr`).**
  Everything else is spanned (diagnostics, infer/typed/check/body errors, bundle errors, pattern
  inference, call resolve, the four `typecheck_decls` validators).

### Runtime and performance roadmap

- [ ] `P2` **Make tight Sprout string-processing loops competitive with host builtins**, so moderate
  stdin/text workloads do not need dedicated host helpers to be practical. Investigate the native
  overhead in recursive stdlib string loops such as `string_lines` over stdin-loaded text — tail-
  recursive loop lowering, call/closure overhead, primitive boxing, string/vector iteration — and
  add stable benchmarks for `string_lines`, `trim` and AoC-style stdin parsing so wins are
  measurable. Target: `string_lines` over a `day5input`-style workload in low single-digit seconds.
- [ ] `P2` **Non-escaping combinator closures allocate, and every apply re-checks arity.** In
  `bench/gc_roots` — records in a list, nested `list_filter`/`list_fold`/`any`/`max_by` — the lambda
  handed to each combinator is heap-allocated per call although it cannot escape the callee, and
  `sprout_closure_arity_check` alone is ~10% of top-of-stack samples (`sprout_alloc_closure` a
  further 3%). Two independent levers: escape analysis to stack-allocate or inline a closure whose
  only use is the call it is passed to, and specialising an `IRApplyClosure` whose arity is
  statically known so the check is dropped. The closure allocation also feeds the collection rate,
  so it is upstream of the GC entries in §1. Profile: `bench/results-2026-09-20-gc-root-stack.md`.
- [ ] `P2` **Stronger server-side runtime models — multi-reactor as the next target.** Native TCP
  handle-slot reuse and the `stdlib.http_server` helper layer are the groundwork. Keep-alive and
  chunked reads are filed in §2.
- [ ] `P3` **Compile caching for the stdlib test runner** so repeated runs of unchanged test files
  skip the IR-emit + clang step. Shares an invalidation strategy with incremental build caching
  below.

### V1 roadmap candidates

- [ ] `P2` **Finalize the integer-ranges v1 contract.** The implemented scope is `IntRange`, `a..b`
  inclusive syntax, ascending/descending unit step, and the `range*` helper surface. Remaining: the
  normative contract, sharper diagnostics, and a decision on whether range patterns or half-open
  forms should exist at all.
- [ ] `P2` **Records — unknown field access and missing-field construction bypass the
  typechecker.** `c.nope` and a record literal with fields omitted are rejected only at `ast_to_ir`,
  **with no source position**, and preceded by a misleading
  `warning: alloc-summary pre-pass failed …` because the pre-pass treats a user type error as an
  infrastructure failure. Reproduces same-module too, so it is a general records-diagnostics gap.
  Contrast the two paths that get it right — `with`-unknown-field and a wrong field-value type
  both give positioned `check` errors. **The rejection is correct in every case; only the phase and
  the diagnostic are wrong.** Four negative probes exist and belong in
  `tests/conformance/type_error/`.
- [ ] `P2` **A record literal written above its `type` declaration is rejected.** In one file,
  `fn mk() -> Lp = Lp(a = 1, b = 2)` followed by `type Lp = (a: Int, b: Int)` fails with
  `check: Unknown record type or field: Lp.a`; declaring `Lp` first compiles. Field access above
  the declaration is fine — only the named-field literal fails. Spec §5.6.2 says a type is visible
  to declarations written above it. Hit in `stdlib/tui/app.sprout`, whose `Loop` had to move up.
  Wants a `tests/conformance/run/` fixture.
- [ ] `P2` **DECISION NEEDED — derived `ToString`: qualified or bare name?** `to_string` on a
  `deriving (ToString)` type prints the declaring module's qualified name when imported and the bare
  name when declared in the entry file, compounding per nesting level. Not a spec violation — §12
  wants valid source and the qualified form compiles. The defect is the **inconsistency**, an
  artifact of `bundler.sprout:1126` running `expand_deriving_decls` on already-qualified decls, so
  extracting a type from the entry file into a module silently changes program output. Prior art,
  verified: Haskell's derived `Show` is unqualified though it promises `Read`/`Show` are inverses;
  Rust's `Debug` is bare; Java records disclaim parsing. Stripping to the last dot-segment is a
  helper call in two `deriving.sprout` emitters; qualifying everywhere needs a module identity an
  entry file does not have. **Recommendation: strip.** Seed-gated.
- [ ] `P3` **Records PR3 — parser tests** for record-vs-tuple, record-vs-call and shadowing, plus
  the §8 error-message fixtures.
- [ ] `P2` **Algebraic effect handlers (phase 1: one-shot linear handlers).**
  `docs/effect-system-handlers-draft.md`. Motivation: eliminate explicit `TestState` threading and
  establish the infrastructure richer patterns (async, generators, capability injection) build on.
  Scope: `effect` declarations, `handle`/`with`, implicit perform, multi-label effect rows, one-shot
  linear codegen via handler-record passing — no heap continuations or setjmp. Constraints:
  one-shot only, no open effect row polymorphism, no constrained effect operations, and the old
  `TestState` API stays alongside.
- [ ] `P2` **A source-level debugger for compiled user programs.** `docs/debugger-v1-draft.md`. Emit
  LLVM DWARF from codegen behind `--debug`, then use `lldb`/`gdb` as the UI — every `TypedExpr`
  already carries a `SourcePos`. M1: DWARF emission + a 4th IR section + the flag wired through,
  delivering `b file.spr:N`/`n`/`s`/`bt` for user-module functions. M2: `field_kinds` on
  `SproutCtorMeta` + an ADT pretty-printer under `tools/`. M3: `just build-debug`/`debug-run`
  recipes and a docs section. Strictly opt-in; M1 scopes `!dbg` to user modules to avoid misleading
  attribution in multi-file bundles. Overlaps the narrower stack-overflow-diagnostic-v2 item in §7.
- [ ] `P3` **`stdlib.fs.path` — the typed half, and whether it earns its cost.** What remains from
  `docs/stdlib-path-v1-draft.md` is `File`/`Dir` wraps exported without `(..)`, with `path.file`/
  `path.dir` as the only way in; then migrating `read_text`/`write_text`/`is_file`/`is_dir`/
  `list_dir` and retiring the compiler's `FilePath`/`StdlibRoot` into them. Construction is
  **total** — `PathErr` was deleted 2026-09-13 once both its cases proved to be non-problems (the
  runtime already rejects an empty path; an embedded NUL is unrepresentable), which also closed the
  `PathErr`/`IoErr` seam. So the migration is mechanical but large: ~90 call sites plus 27 golden
  lines. Dropped to P3 because the remaining value is intent-marking, not validation, and the one
  place that threads a path already has wraps — see the draft's open question 4 before starting.
- [ ] `P3` **Expand native ADT lowering.** `docs/native-adt-lowering-v1.md`. The `Nothing` singleton
  and immediate-match optimization for direct constructor-producing scrutinees landed; planned:
  broader constructor forwarding, whole-scrutinee binding, and specialized representations for tiny
  ADTs.
- [ ] `P3` **`wrap` ergonomics follow-ups.** Parameter-level destructuring (`fn f(Foo x) -> …`
  desugaring to a match, useful for all single-constructor types); an auto-generated zero-cost
  accessor; a named-field variant `wrap Foo { inner: T }`; and `opaque type` for Scala 3-style
  module-boundary transparency, distinct from the shipped `export type Name` export-opacity.
  Also: a wrap pattern's inner arg must be a binder, so `| Line 0 ->` and `| Line (a, b) ->` are
  rejected by `bind_wrap_inner_arg` at emit time rather than compiled — the payload has to be
  bound first and matched separately. Lifting it means the recursive tag/field descent that
  function avoids, which for a wrap is a plain identity bind plus the inner pattern's own test.
- [ ] `P3` **Scoped type variables — deferred, no current demand.**
  `docs/scoped-type-variables-analysis-2026-07-26.md`. The feature does not apply today: implicit HM
  quantification means no lexical binder, and there are no local type annotations for a body `:: a`
  to reuse. An audit found zero code in `stdlib/` that needs to pin a return-polymorphic type inside
  a body and cannot. Hard prerequisite: local type annotations. Realistic trigger: a binary
  serialization/codec library (`decode : Bytes -> Maybe a` consumed in isolation).
- [ ] `P3` **Keep the external-protocol-client prerequisites stable.** The byte-building/parsing,
  TCP, crypto and generic SCRAM surfaces must stay usable by a separate repository implementing
  protocol-specific auth and wire flows — no protocol-specific client here, host-side builtins
  minimal, generic helpers preferred.

### Self-hosting follow-ups

- [ ] `P2` **Incremental build caching / invalidation.** No incremental compilation or module-graph
  invalidation yet. Shares a strategy with the stdlib-test-runner compile cache above.
- [ ] `P3` **Release-trust policy for self-built artifacts.** No release process defined for
  trusting binaries produced by the self-hosted compiler.
- [ ] `P3` **Stage-3 fixed point in CI.** Verified locally; CI does not cache or produce the stage-2
  binary to automate it. Likely already satisfied in spirit by `just verify-bootstrap-fixed-point`
  — confirm coverage before scheduling separate work.
- [ ] `P3` **Formal minimum-language-subset spec (docs-only).** The self-hosted compiler uses the
  full language surface, so no restricted bootstrap subset was ever needed or written.

### Standing guard rails, not work items

- **Observability constraints** (`docs/observability-guard-rails.md`). Logging, debugging, profiling
  and introspection for the self-hosted compiler are unscheduled, but the six constraints — source
  locations first-class, explicit typed passes, explicit capability passing, no premature pass
  fusion, type survival into typed core, accurate effect annotations — are active guard rails on
  all self-hosted compiler code so those features stay practical to add.
- **The unifier stays `Ref`-based.** A pure state-threading unifier was considered and decided
  against, for performance.

## Compiler Internals Follow-Ups

### Sprout-IR / Model-C codegen

- [ ] `P1` **The golden corpus covers the compiler's FRONT END only — a typechecker edit moves no
  golden.** `tests/smoke_shapes/11_compiler_bundle.spr` is the one entry bundling compiler source,
  and its header claimed it pulls `stdlib.compiler.*` transitively. It does not: the golden holds
  `ast`, `lexer`, `parser`, `source`, `token`, `types` and the session facade, and nothing from
  `infer`, `lowering`, `resolve`, `ast_to_ir`, `ir_lowering`, `bundler`, `dce`, `deriving`,
  `iface_codec` or `verify_dispatch`. Verified by refactoring `infer.sprout` and getting
  `ir-golden-diff: 0 differences`. So DoD #12 gives no codegen coverage for the back half of the
  pipeline — the largest and most delicate part. Fix: a shape that reaches the typed pipeline
  (importing `stdlib.compiler.infer` directly), or a second bundle shape per phase.

- [ ] `P2` **Tuple-return CPR does not fire on a SELF-RECURSIVE call**, so a recursive
  tuple-returning reduction boxes once per step. CPR fires on the outer call; on the function's own
  recursive edge the worker calls the BOXED wrapper, which allocates, then reloads and repacks —
  one heap tuple plus an unpack/repack per step, and the self-tail-call TCO is lost. Measured A/B in
  one binary, bit-identical results: `ln` 4.3 ns/call accumulator-threaded vs 12.4 ns/call
  tuple-returning (2.8×), ~29.5 ns/call on a cold heap, the difference being GC. Re-runnable at
  `bench/math_transcendental/accumulator_vs_tuple_bench.sprout`, pinned by
  `test_tuple_return_cpr.spr` whose "KNOWN GAP" assertion flips when fixed. Likely fix: chain a
  self-recursive tuple return to `@<self>_worker` in `translate_tail_unboxed` — the TCO caveat is
  the thing to resolve, since here the self-call IS the tail call.
- [ ] `P2` **Capturing IIFE returning String fails to translate.**
  `fn f(n: Int) -> String = (\x -> int_to_string(x + n))(7)` fails in `translate_program` even
  though every component (capturing lambda, IIFE, `int_to_string` GC trigger, String return) works
  individually; the obvious `is_string_type` rejection in `translate_direct_call` was removed and it
  still fails at a subtler step — suspect the outer fn's return-type handling or type tracking in
  `translate_lambda_lifted`'s body result. Int-returning capturing IIFEs pass, so it is specific to
  String returns from lifted bodies. Test fixtures T7 and T21 are deferred on it.
- [ ] `P2` **Top-level `let` binding resolution in lambda bodies.** `build_top_level_fn_set`
  includes `TLetDecl` names so `compute_free_vars` excludes them, but `translate_expr`'s TVar arm
  has no handler and falls through to `Err("unbound variable")`. Three options: (a) emit each
  `TLetDecl` as a 0-arg `@<name>()` and call it at use sites — semantically wrong for non-pure
  bodies, which would run per reference; (b) inline-translate the body at each use site — clean
  for pure literals, duplicated work otherwise; (c) emit it as an LLVM global with a one-time init
  — cleanest, most machinery. T20 in `test_ir_codegen_closures.spr` is deferred on the decision.
- [ ] `P2` **Extract `main_shim`'s `register_ctor` calls into `@_sprout_ir_module_init()`.** The
  shim is no longer a passive `ret i32 0`; it always emits an `@main` depending on runtime
  registration. **Latent risk:** a future bundle-smoke or test harness wrapping the lowered module
  with its own host `main` hits `duplicate symbol _main` — the trivial old shim could be
  weak-attributed away, this one cannot. Fix: extract the registration into its own function and
  either call it from `main_shim` or attach `__attribute__((constructor))`.
- [ ] `P3` **The emitted LLVM `declare`s carry no attributes, so every allocation is opaque to the
  optimizer.** The only annotated extern in the whole seed is `@sprout_abort_match() noreturn`;
  `@sprout_alloc_obj` and every other runtime import are bare, so LLVM models each allocation as
  may-read-write-all-memory and may-not-return — blocking dead-allocation elimination, load
  forwarding across allocations, and heap-to-stack promotion. Adding
  `allocsize`/`willreturn`/`noalias`/ memory-effect attributes in `ir_header` would help every boxed
  path. **Not a free lever:** stack promotion is entangled with precise-GC rooting — a rooted
  pointer escapes into the shadow stack and an unscanned stack copy violates the scan invariant —
  so establish the safe subset first, most likely excluding anything authorizing promotion or
  elimination of a rooted allocation.
- [ ] `P3` **CPR for bare-type-variable results — the filed design is WITHDRAWN, do not re-open it
  as filed.** Requested as "restore CPR for generics" on the assumption the §1 ABI bugfix switched
  CPR off for generics. It had not: the gate declines *bare* type variables only, and a generic
  declared `-> Maybe b` still workerizes with the constructor fully fused. The residual gap cannot
  pay — by parametricity a function declared `-> a` cannot construct its result, and CPR's win
  comes only from fusing a tail constructor into the unboxed return, so the bare-tyvar population is
  structurally the one with nothing to fuse. The conservative gate is permanently correct, not
  scaffolding to remove. Still on the table: inline small generics before the CPR router runs;
  specialize per instantiated head; or attack allocation cost via the allocator-attributes item
  above. Re-file under whichever is chosen.
- [ ] `P3` **Tier-2 CPR: bare-name `adt_index` collision.** `build_adt_ctor_index` keys by the bare
  type name and every reader uses bare `type_head_name`, so two modules declaring width-2 types with
  the same bare name collide last-write-wins; a catch-all repack then uses the wrong `(tag, arity)`
  and can emit `IRGetField(box, 0)` on a genuinely nullary ctor — reading adjacent heap into a
  rooted `val`, i.e. silent corruption, not a loud abort. A real fix is qualified type identity, not
  `adt_index` alone. Also: the qualified key it inserts is currently DEAD — delete it or make the
  reader disambiguate via it.
- [ ] `P3` **Tier-2 CPR: unify the two worker-emission shells.** The active streaming path and the
  test-only batch path duplicate the same collect+index+emit+merge loop with different accumulator
  plumbing; only the shell diverges, so **IR-shape tests exercise a different shell than production
  ships**. Extract one loop. Same-area efficiency: `worker_source_for` re-scans all decls per callee
  (O(N·D) — build a name→decl `Dict` once), and `build_adt_ctor_index_go` computes
  `adt_ctor_entries` twice per TypeDecl.
- [ ] `P3` **`gate-audit` does not catch orphaned `.sprout` executables.** Its assertion B guards
  orphaned `scripts/*.sh` only. Seven unreferenced compiler drivers accumulated unnoticed until a
  review deleted them (2026-10-09).
- [ ] `P3` **No scientific-notation Double literal in the lexer.** `1.5e3` is a parse error, as is
  any integer part above 2^63. So 2^64 / 2^512 / 2^-512 are **not writable as literals at all**,
  which is why `stdlib/math.sprout`'s power-of-two ladder is built from module-level `let`s and
  `sq()` calls — a forced shape, not a style choice. Adding `e`/`E` exponents would let the ladder
  be literals. Decide hex-float (`0x1.8p3`) at the same time; it is the only exact-and-readable
  spelling for a power of two. **Read the item below before assuming the const path is a speedup.**
- [ ] `P3` **Do NOT re-attempt const-folding Double-literal globals as a performance fix —
  measured, reverted 2026-08-06.** The mechanism is real (a module-level `let` is a *mutable* global
  whose address escapes to the root registrar, so LLVM cannot fold it), and routing `TFloat`
  literals to the `GlobalConst` path worked — `adrp` 65→26, `ldr` 86→41 — but **lost
  overall**: `mov`+`movk` went 181→358, because the i64-uniform value ABI makes LLVM see an
  *integer* constant, and an arbitrary 64-bit immediate on arm64 costs 4 instructions against
  `adrp`+`ldr`'s 2. Paired wall clock put `ln` at ratio 1.12, slower. Dead ends: LLVM removed
  floating-point constexprs; and folding in the compiler needs an exactly round-tripping decimal,
  which `double_to_string` cannot print. **If revisited**, emit the global as `double`-typed so LLVM
  applies its FP cost model.
- [ ] `P3` **Re-run the B3 SIMD checkpoint now that B1-Double has landed.** The last checkpoint
  (against B2 only) found zero vector-lane ops anywhere in the digit recognizer's row kernels, with
  `-Rpass-analysis=loop-vectorize` naming the blocker as "call instruction cannot be vectorized" —
  a negative result gated entirely on B1's per-element `vector_get_direct`/`vector_mutset` calls not
  yet being inlined, which B1-Double removes. Re-disassemble the three row kernels at `-O2`/`-O3`.
  If still blocked, the next suspect is B1's bounds-check panic branch (the post-B1 checkpoint
  already saw the blocker shift to "Incorrect number of successors from early exiting block"), which
  would need hoisting. Note `row_dot_go` is a reduction and cannot SIMD-reassociate without
  perturbing the golden training output — only the independent-write kernels can vectorize
  bit-identically. `docs/phase-d-numeric-fastpath-design-2026-07-11.md` §B3.

### Linear types (Model C Milestone 4) follow-ups

M4.1–M4.6 plus M4.4a (one-shot closures) have landed. Design and the full rule sets:
`docs/linear-types-m4-scoping-2026-08-01.md`, `docs/linear-types-m4.2-enforcement-2026-08-06.md`,
`docs/linear-borrowing-v0.md`, `docs/one-shot-closures-v0.md`, `docs/linearity-virality-v0.md`;
normative text in `docs/spec-v0.md` §5.8. Deferred, in the order they matter:

- [ ] `P2` **Higher-order linearity (M4.4) — the general case.** M4.2 loud-rejects a linear
  binding captured by a lambda, because a closure may run 0..n times and its call count is
  untracked; M4.5 borrowing did not lift this and extends the rejection to borrowed values, since
  whether a captured borrow is sound depends on whether the closure escapes and outlives the
  consume — a distinction Sprout does not have. Known-hard: Linear Haskell shipped it incomplete.
  Landed: the move-into-a-one-shot-closure slice (M4.4a), and borrowing lambda parameters (M4.4b,
  `docs/linear-borrowing-v0.md` §20). Left: a linear value captured at an UNANNOTATED parameter
  (the true 0..n case, `list_each(xs, \x -> write(conn, x))`, whose borrow half needs an escape
  notion); CONSUMING linear lambda parameters (`\c -> close(c)`); a linear `Scope`, both at once.
- [ ] `P1` **A lambda's `once` promise is never checked, so a moved-in linear value can be released
  twice.** `once_honesty` is what makes `once` a promise rather than a decoration, and it runs from
  `check_fn_linear` over a DECLARED parameter's written mode. A lambda reaches neither, so
  `runner(\(w: once Unit -> Int) -> w(()) + w(()), n)` at a `once` slot compiles, and a value M4.4a
  moved into the closure `runner` passes is released twice — verified running, on master and since.
  M4.4b keeps the hole closed for the common shape by pushing only `borrowing` down, so an
  unannotated lambda at a `once` slot is still a mismatch; the WRITTEN spelling above is the way in.
  Fix: run `once_honesty` over lambda bodies, keyed on the parameter's ownership rather than its
  written mode. Regression to flip: `type_error/lambda_slot_once_not_pushed_down` has the shape.
- [ ] `P3` **A `let`-bound lambda gets no expected type, so it must spell its ownership mode.**
  M4.4b pushes a slot's ownership onto an unwritten lambda parameter mode, but only where the
  lambda is written at the call argument — `let work = \c -> peek(c) in with_file(n, work)` still
  fails with an ownership mismatch, while the annotated `\(c: borrowing File) -> …` compiles. The
  fix is to propagate an expected type into a `let` right-hand side generally, which is wider than
  linearity. Limit recorded at `type_error/lambda_borrow_mode_needs_call_position`.
- [ ] `P2` **Decide whether a wildcard pattern over a linear value is a consume or a leak.** A
  *semantics* question, not a bug. Three shapes are accepted today: a linear parameter dropped by a
  wildcard arm; the same over an unbound linear scrutinee; and `match w with | Wrap _ -> 0` dropping
  a linear **field**. **Position A (ships today):** "use" is syntactic per spec §5.8 — the
  scrutinee IS referenced and the third never names the field; this is the inherent "a consume need
  not do anything useful" limit, which Rust shares minus `Drop`. **Position B:** a consume should
  mean destructured *or* passed on, making all three leaks, at the cost of a rule saying which
  patterns count. A's cost: `type linear Wrap = Wrap TcpConnection` plus `Wrap _` silently leaks an
  fd and looks deliberate. Survey Rust's `let _ =` vs `let _x =` and Austral's linear-field rules
  first.
- [ ] `P3` **The effect-bind fallback still types `x <- e` as the payload.** `do_bind_type` cannot
  see the monad kind, so for `e : Container Linear !{IO}` it strips a type argument and tracks the
  payload rather than the container. The shape that made this `P2` is gone: a phantom position is no
  longer stripped, so `ch <- chan_new(s, cap)` compiles and `http_server`/`pool_server` are written
  that way. What is left is a diagnostic split between binder forms — leaking `b <- mk()` at
  `Box Res` reports "linear value 'b' is never used", while the same value as a parameter reports
  "its type `Box Res` contains the linear type `Res`". Same accept/reject answer, worse message.
  Fixing it needs the post-pass to tell a monadic bind from an effectful one.
- [~] `P3` **Containment virality — binder half LANDED (concrete fields included); type half open.**
  Decision (Kuba): **Option 1** — containment decides which *bindings* carry the obligation, while
  linearity stays per-declaration as a property of *types*, so a record containing a linear field is
  still not itself linear (contrast Austral). Every binder is covered, and containment reads what a
  declaration **stores**: a concrete field (`type Crate = | Crate File`) and a stored type argument
  both count, a phantom position (`type Chan a = | Chan Int`) does not, and the walk is transitive
  with a visited set. **Still open — Option 2, full virality**, with linearity itself
  containment-computed, reaching parameter modes, `borrowing` filters and field reads; `consuming
  Crate` is still an error. What is left is §6 of `docs/linearity-virality-v0.md` — a type
  variable's universe.
- [ ] `P3` **Linearity bound on a type parameter (enabler for `borrowing a`).** A modifier on a
  type-variable parameter stays rejected, which also blocks the receiver-borrowing class shape
  `class Peekable a { fn peek(r: borrowing a) -> Int }`, so M4.6's method lift reaches only a
  method's *concrete* linear parameters. Not a representation limit — ownership sits in the type
  and survives instantiation — but a *universe* limit: without a bound on `a`, `borrowing Int` is
  an error while `borrowing a` instantiated at `Int` silently is not, the tell that this is
  polymorphism over linear types (an explicit M4.2 non-goal). Prior art, both verified: Swift
  SE-0427 makes generic parameters `Copyable` by default, `<T: ~Copyable>` opting out; Austral
  annotates every type parameter with a universe. Scope: pick a spelling, thread the bound through
  class/fn type parameters, enforce at instantiation, then delete the `type_is_tyvar` rejection.
- [ ] `P3` **Linear-record ergonomics for OWNED records.** M4.5 lifted this only for `borrowing`
  parameters: `p.x + p.y` is legal for `p: borrowing Pos` and remains a reuse for an owned `p`. The
  field-read borrow is keyed on the binding's mode, not on `TGetField` syntax, deliberately —
  making every field read a borrow breaks `fn get_x(p: Pos) = p.x`, because a field read *is* an
  owned record's only consume, and relaxing the leak rule to compensate would stop an unclosed
  socket being a leak. The owned case needs a real consuming exit: a `RecordPattern`. Blocked on
  that.
- [ ] `P3` **Resources moved into a cancelled task's closure are never released.** M4.4a's guarantee
  is **at most once** — as are Rust's `FnOnce` and OxCaml's `once`, both verified. Rust affords
  the weaker bound because a never-called `FnOnce` still runs `Drop`; Sprout has no destructors, so
  leak-freedom for a moved value depends on the callee's runtime contract that the closure *does*
  run. `with_scope` binds its body with `let` rather than `<-` precisely so `__scope_join` is
  unconditional — except on the cancellation path, where `__scope_cancel` force-drops parked tasks
  and a spawned task has not started yet, so its closure runs zero times and a moved-in socket is
  never closed. A *runtime* leak on the experimental cancellation path, not a checker hole, and
  equally true of the raw `Int` handles that preceded M4.4a. Fix belongs with cancellation-time
  resource release (a drop hook on force-drop), not the type system; spec §5.8 states the limit.
- [ ] `P3` **A linear `TcpConnection` costs an allocation and a GC root per connection; `wrap` +
  `linear` would not.** Migrating `http_server.sprout` from raw `Int` handles turned `pop_roots(1)`
  into `pop_roots(2)` in the read loop, because a connection went from an unboxed integer to a boxed
  single-constructor ADT rooted across the recursive call. Correct, and the price of the guarantee
  — but not *inherent*: `type linear TcpConnection = | TcpConnection Int` is exactly the
  single-field shape `wrap` already unboxes. Scope: find out whether the restriction is deliberate
  or simply unimplemented (`ast.WrapDecl` vs `ast.Linearity`), then lift it or record why it cannot
  be. **Measure before assuming it matters** — a server doing per-connection syscalls will not
  notice one allocation; this is a "no hidden cost" argument, not a demonstrated bottleneck.
- [ ] `P3` **Cross-module linear-reject conformance coverage.** The `type_error` harness invokes
  `--phase check` without `--package-root`, so a cross-module *misuse* of an imported `type linear`
  cannot be a conformance fixture. Enforcement is verified manually and by the positive
  `test_linear_cross_module.spr`; add a package-root-aware reject harness, or extend `_test-reject`
  with an optional `--package-root`.
- [ ] `P3` **Rename `types.Ownership` → `types.ParamConv`.** With `OwnOnce` the type carries two
  different things: how the parameter is *taken* (`OwnConsume`/`OwnBorrow`) and a bound on how often
  the callee may *invoke* it. "Ownership" is a misnomer for the second; the accurate term is a
  parameter *convention*, Swift SE-0377's own word — so `ParamConv` with
  `ConvConsume`/`ConvBorrow`/`ConvOnce`. Pure rename, but it touches the wire tag names, so it costs
  an `IfaceFile` version bump — batch it with the next change that needs one.
- [ ] `P3` **Export the current `.iface` version as a constant.** Every bump (three so far) costs a
  sweep through tests that pin the literal. That is wanted in `test_iface_file_roundtrip.spr`, where
  the version gate *is* the subject; it is pure friction in `test_iface_extraction.spr`, which only
  needs *a* decodable iface. Add `iface_codec.current_iface_version` and use it where the version is
  incidental — it removes a recurring false failure that trains the reader to bump-and-move-on,
  which is the habit that would let a real decode regression through.
- [ ] `P3` **Prelude has no `head` / safe list-destructure.** No `List a -> Maybe a` exists at all,
  so every "look at the first element without committing to a non-empty list" site open-codes the
  match. Add `head`, and consider `tail`/`uncons` alongside it. The prelude is bundled into the
  compiler, so any change forces a full reseed — check the wider tree for open-coded instances
  before settling the name.
- [ ] `P3` **`&`/`&mut` (shared-XOR-mutable) split.** v0 ships borrow-vs-consume only; the
  read-vs-write refinement is a later increment. `docs/linear-borrowing-v0.md` §2, §13.

### Linear-typed Sprout-IR (Model C Milestone 5) — DEFERRED (2026-08-07)

M5 (make the IR's heap types linear so GC rooting is a type-checker theorem) is **deferred**. Full
analysis and rationale: `docs/linear-ir-m5-feasibility-2026-08-07.md`. Short version: M5 as planned
is not executable against the IR that was actually built — there is no `Heap τ`/`Rooted τ` type,
only a coarse `IRType` tag plus the `ir_rooting` dataflow pass, and the M4 checker is over
`TypedExpr`, not `IROp`. A replay of the four historical GC-UAF bugs found they are all
classification-completeness or sub-op alloc-ordering bugs, never the "forgot to root a
correctly-classified value" class that linearity catches for free. The rooting invariant stays
enforced by `ir_rooting` plus its exhaustive no-catch-all op classification.

- [ ] `P2` **IR classification-consistency verifier (the M5 "Option 2").** A greenfield
  `stdlib/compiler/ir_verify.sprout` wired into `compile_program_streaming` after
  `ir_rooting.insert_roots`, run as a CI/debug gate. **Not linear types** — it targets the same
  bug class via classification *consistency*, the safety-critical half for GC rooting being coverage
  rather than no-reuse. **Family 1:** for every heap-producing op whose kind derives from a type,
  re-derive the expected kind from an independent structural source (`type_kind` for `IRCall`
  returns, `field_kinds` for `IRGetField`/`IRLoadEnvSlot`/`IRGetTupleField`) and assert the two
  agree, treating `IRTUnknown` as either-acceptable so there are no false positives; this catches
  the historical `IRCall`-wrong-kind shape without touching the translator. **Family 2, deferred:**
  re-verify the post-rooting IR, needing an independent liveness derivation or it is circular.

### Native REPL & Analysis Service

- [ ] `P1` **Implement `complete_in_state`** (tab completion) in `analysis_service_driver.sprout`;
  it is the last stub, returning "not yet implemented". Approach: reuse the `type_of_in_source`
  machinery and filter by prefix over visible names from imports plus declared names.
- [ ] `P1` **`sprout_tag: null pointer` crash in native REPL block-mode.** Three block-mode tests
  exit 250 (mixed submissions run sequentially; multi-line class declaration; multi-line function
  declaration). The crash is in the REPL binary itself, not the analysis service — likely a null
  GC allocation in the block-mode parser path for multi-line input.
- [ ] `P2` **A rendered effect variable leaks its generated name.** `list_fold` hovers as
  `(a -> b -> a !{$e14}) -> … !{$e14}` though the prelude declares `!{e}`. `types.effect_suffix`
  prints `EffectVar name` verbatim and no rename reaches it — `build_var_rename` covers type
  variables only. Same defect class as the binder renaming fixed 2026-09-07, different machinery:
  effect vars live in `Scheme`'s `effect_vars` and have no display pass. Fix is the mirror of
  `rename_generated` over that list, with letters from a separate sequence so a type var and an
  effect var never collide.
- [ ] `P2` **Canonical `<module>.<Type>` identity on the env path.** Two modules' same-named types
  collapse to one identity inside an importer. **Scope is bigger than it looks, and this was
  measured:** every `@`-marker family on the env path is keyed by SHORT type name — `@linear:`,
  `@inst:`, `@class:`, `@type:` — so qualifying types makes those lookups MISS silently. An
  implementation attempt reached 22/27 stdlib modules with new interaction classes still surfacing
  (it broke `@linear:`, and would have broken instance dispatch for imported types, which a
  module-load probe does not exercise), versus 26/27 for the alias-stripping fix that landed. Doing
  it properly means moving every marker family to canonical keys, or stripping to short names at
  every lookup — a change to dispatch and linearity needing its own design doc and PR.
  `docs/repl-env-type-vocabulary-v0.md` §11.1a.
- [ ] `P2` **Two types in one module cannot share a constructor name, and the workaround is
  invisible.** 394 of 585 constructors (67%) carry a prefix or suffix shared by every sibling — a
  namespace spelled by hand. `types.Type` is `TVar TConst TApp …`; `infer.sprout` has fourteen
  result types each inventing its own `Ok` (`InferOk`, `CallOk`, `GroupOk`, …). Module
  qualification cannot help: the collision is inside one module. Counting collisions finds 1.2%
  and measures only that the constraint was obeyed. Motivation is name pressure AND IDE
  discoverability, so the target is namespacing under the type — which makes leading-dot
  inference blocking, not optional, or use sites get longer than today's.
  `docs/constructor-namespacing-v0.md` has the prior-art survey and the cheaper fallback.

- [ ] `P2` **`(..)` is unenforced for everything the prelude declares.** The prelude has no module
  header, so `bundler.ParsedModule` carries `module_name = ""` and its names are injected
  unqualified into every module — never passing through the `(..)`-filtered export maps. `Dict` is
  declared without the marker and any file still destructures and forges it (`match d with | Dict
  raw -> raw`, `Dict(map_set(…))` both check clean, from a headerless entry *and* a named module),
  which is the `steal_raw`/`forge` program from
  `docs/archive/collections-facade-soundness-analysis-2026-07-12.md` §3B, still reproducing. Not a
  simple fix: the exemption is load-bearing for `Maybe`/`List`/`Result`, whose constructors every
  file must be able to write. Only `Vec`/`Dict`/`Set` want sealing, so the prelude needs per-name
  export filtering it does not have.

- [ ] `P2` **An import list entry starting with a non-word character silently empties the whole
  list.** `read_word` returns "" on a leading `(`, and `parse_selective_names_acc`
  (`module_loader.sprout:103`) reads that as end-of-list — one arm serves both "hit the closing
  paren" and "hit junk". `import demo.cap ((..), parse_int)` scans to `[]`, so
  `first_unbound_name(Nil)` is `Nothing`, no check fires anywhere, and the bare call reaches the
  PRELUDE's homonym: the probe exits 7 where `demo.cap.parse_int` returns 42. Identical failure to
  the `T(..)` drop fixed in #314, one step earlier in the same scan. Fix: at `("", i2)` branch on
  what stopped it — `)`/end-of-string ends the list, `(` captures the group as a bogus name so the
  export check reports it, `,` is an empty entry and wants its own ruling. Nothing in-tree is
  affected; `(..)` read as "import everything" is the plausible way in.

- [ ] `P1` **A library adding a variant breaks dependents that opted out of exhaustiveness.**
  Naming a type in an import list imports its constructors (spec §3 *Imports*), and the clash
  check is EAGER — it fires at the import line whether the name is used or not. Adding `| Box Int`
  to a `(..)` type then breaks any dependent that also selectively imports another type publishing
  a `Box`, though it writes neither `Box` nor the new variant. Exhaustive matchers break anyway,
  loudly; the point is that match-breakage is opt-out-able with a wildcard arm and import-breakage
  is not, since no syntax imports a type without its constructors. Verified: a wildcard dependent
  compiles, the library gains one arm, the untouched dependent is rejected, and deleting one
  import line makes it compile again. `docs/constructor-namespacing-v0.md` §7.2 has the repro,
  §7.7 the cheap fix (narrow the eager check to LISTED names, Haskell 2010 §5.5.2), §7.4 the rest.
- [ ] `P2` **`load_module` silently swallows a genuine `CheckErr`.** `module_loader.sprout:366`
  turns a module that fails to check into an empty pair list, so a broken `import` reports `ok` and
  every name from it reads as `Unknown variable` one command later — the swallow that turned a
  one-line diagnostic into a multi-hour investigation. Must distinguish *intentionally skipped*
  (`module_name_to_path → Nothing`, stays silent, the builtin-env path depends on it) from *found
  on disk but failed to check*. Threading a `Result` reaches 12 call sites across 5 driver modules.
  **Trap:** `load_module` caches `Nil` *before* loading to break import cycles, and the
  `ModuleCache` is shared across every session op — a naive fix reports the error once, then
  serves the cached `Nil` forever.
- [ ] `P2` **The LSP overlays only the entry document.** `LoadEnv` can carry every open dirty
  buffer, which is what makes checking an unsaved multi-file edit correct, but
  `check_and_push_diagnostics` overlays just the document being checked — so an unsaved edit in a
  second tab is invisible and the check reads that file from disk. Feed `lsp_documents` into the
  overlay.
- [ ] `P2` **Standing guard: every top-level stdlib module loads cleanly through `load_module`.**
  Blocked when asked for (four modules red); the alias fix took it to 26/27, so it is landable.
  Would have caught all eleven modules of the type-vocabulary bug the day they broke. Land it with
  the one remaining red module (`stdlib.repl`, failing inside the unswept `stdlib/compiler/`
  subtree) or with an explicit known-red list so it cannot silently rot.
- [ ] `P2` **Analysis-service env isolation:** `SPROUT_GC_THRESHOLD` and `SPROUT_GC_ADAPT_RATIO`
  must not propagate to the `analysis_service_bin` subprocess. The GC stress test sets
  `GC_THRESHOLD=1` on the program binary, the program spawns the service with the same env, and the
  service then collects on every allocation (~15 min). Strip `SPROUT_GC_*` before launching, or use
  a wrapper. The test is skipped until fixed.
- [ ] `P2` **`symbol_locations_in_source` omits constructor locations.** `collect_decl_locations`
  emits an entry for `TypeDecl name` but not its constructors; `collect_decl_names` already walks
  them via `ctor_names`, so add the parallel emission. One test is skipped until fixed.
- [ ] `P2` **In-Sprout tree-walking interpreter for REPL eval (the long-term approach).** Replace
  compile-and-run for `eval_expr_in_source` with an in-process interpreter over `typed_ast.TExpr`.
  The compiler already owns parser, typechecker and lowering; the interpreter is the remaining
  output mode. Buys zero subprocess overhead, in-process session state, `instances_in_source` as a
  live type-env query, and shared state for completion and diagnostics. Mirrors GHCi — same
  typechecker for REPL and compiled code, only the execution backend differs. Target:
  `stdlib/compiler/eval.sprout`.
- [ ] `P3` **`repl.stdlib_module_completion_names` is still a literal.** Deliberate: there is no
  directory-listing primitive, so enumerating at runtime means `sh -c ls` per keypress — a
  subprocess, and one that silently offers nothing on the Windows port. Staleness is caught by
  `test_repl_module_list.spr` instead. Revisit if a `read_dir` primitive lands (own approval
  needed).
- [ ] `P3` **REPL display: a zero-param `fn` shows `<value: Vec a>` instead of `<fn: Vec a>`.**
  `fn vec_empty() -> Vec a` has scheme type `forall a. Vec a` with no `TFunc` wrapper, so
  `is_function_scheme` is false. Technically correct from the type system's view; users expect
  `<fn:` because they wrote `fn`. Options: flag the declaration form on the scheme, use `<fn:` for
  all unprintable polymorphic values, or a third spelling like `<thunk:`.
- [ ] `P3` *(far future)* **LLVM MCJIT/ORC JIT for REPL eval.** Session module resident in the JIT
  dylib, new expressions linked on the fly at native speed with no fork. Requires embedding LLVM as
  a library rather than shelling out to `--emit-ir` + clang. Precedent: cling, clang-repl,
  `lli --jit`. Revisit after the in-Sprout interpreter works.

### GC & Runtime Performance

> The measured picture as of 2026-08-09: on the self-hosted compiler, GC is ~59% of compile time,
> split mark 42% / sweep-pass-1 32% / freelist rebuild 27% (since removed). **This is a
> compiler-only finding** — the same instrument puts the nursery ceiling at 13% on a real HTTP
> server, and `SPROUT_GC_DISABLE=1` makes nqueens *faster*. Full data: `docs/gc-generational-v0.md`.
>
> **Three measurement traps, all paid for once already.** `just gc-profile` over-reports GC by
> ~2.3× (its hot counters fire per heap-lookup / mark-edge / sweep-visit); this machine's absolute
> timings drift ~1.6× between sessions, so only interleaved same-session A/B is meaningful; and
> `region_find`/`sprout_heap_lookup` are `static` and fully inlined at `-O2`, so **no profiler can
> attribute to them** — size them by sensitivity probes instead.

- [ ] `P2` **DECISION: what consumers should depend on for whole-program linking.**
  `uncharted-suns` now calls `$SPROUT_ROOT/scripts/link_whole_program.sh` directly (its PR #387,
  −20.7% on perft-4). Provisional by agreement: it replaced a worse coupling, since those recipes
  compiled `runtime/*.c` themselves. The permanent shape is undecided. (a) Publish
  `build/runtime.bc` as a declared artifact and let consumers link it — Zig's cached-libc
  pattern. (b) Give the driver a `--link` mode so it owns the link as `rustc` does; also ends the
  `-framework` flags duplicated into every consumer, but puts clang-spawning inside the
  self-hosted compiler. (c) Ship the runtime as a shared library — Swift's choice, and it
  forecloses this optimisation entirely. `docs/cross-tu-inlining-v0.md` §5.3.

- [ ] `P2` **Whole-program linking is measured on macOS arm64 only.** Linux x86_64/aarch64 and
  the release workflow need their own run. Binary size grew 6.6% on `bench/gc_roots` and 21% on
  perft, so it is workload-dependent and unmeasured elsewhere. Not a candidate for the
  416-binary test path — a binary that runs once cannot repay any extra link.
  `docs/cross-tu-inlining-v0.md` §5.2.
- [ ] `P2` **GC trigger is object-count-blind, not byte-aware.** `sprout_gc_maybe_collect_threshold`
  fires on `g_managed_heap_count >= g_gc_threshold`, and the count increments by 1 per managed
  object regardless of size, so many-small over-collects and few-but-large under-collects,
  amplified by the `adapt_factor` default of 3.0. First measured instance 2026-09-06 and the gap is
  ~100,000×: one function holding a 1,600-element `Vec Int` literal peaks at 3,188 MB RSS to produce
  767 KB of output. Vectors up to 508 elements now carry their elements inline, so those bytes ARE
  counted (2026-09-25); a longer or push-grown one's buffer is still an invisible `malloc`.
  `docs/gc-generational-v0.md` §11 has the measurements and three approaches already measured and
  rejected — do not re-derive them.
- [ ] `P2` **`ir_lowering` assembles IR text with `++` in a recursion at all three nesting levels**
  (`lower_ops`, `lower_blocks`, `lower_fns`, and the same shape in `sprout_ir.print_*`). Each of n
  frames concatenates onto the entire remaining tail, so emitting a block of n ops copies O(n ×
  total) bytes. `docs/string-building-v0.md` §6 prohibits exactly this and `string.join` shows the
  sanctioned form; the fix is mechanical and local. §11 there has the regime analysis — why this
  does not contradict the "string concatenation was the wrong target" correction, and why fixing it
  hides the byte-blind GC trigger rather than closing it. Whether the fix collapses the curve is
  unmeasured.
- [ ] `P2` **Vector element bytes moved from `malloc` to the arena's fixed size classes, and
  nothing measures what that retains.** A small `Vec`'s elements now live in its own slot, so a
  200-element vector holds a class-102 slot that only another ~200-element vector can reuse, where
  before it was a `malloc` block libc could coalesce and hand back at any size. `sprout_gc_sweep`
  returns memory to the OS only when a whole 1 MiB region has no live and no poison slot, so a
  phase-structured workload (many 200-element vectors, then many 40-element ones) keeps the
  class-102 slots for the rest of the run. Commit 4535204c measured peak RSS *down* on two shapes,
  so this is a suspected counter-pressure, not a known regression — but no instrument reports
  retained-by-class arena bytes, so neither direction can be checked today. Prior art and a field
  set to copy (GHC ships this census): `docs/gc-size-classes-v0.md` §6.
- [ ] `P3` **`opt --passes=verify` can abort in teardown, and `_test-stdlib` reports it as IR
  INVALID.** Seen once on 2026-09-26, homebrew LLVM 23.1.1 on macOS arm64: verification succeeded,
  then `Module::~Module` -> `BasicBlock::eraseFromParent` aborted in libsystem_malloc. The harness
  appends the crash dump under "IR INVALID (opt --passes=verify)", so a tool flake reads as a
  compiler bug. Not reproducible on demand — the same `.ll` verified clean three times and
  `ir-golden-diff` showed 0 differences across 66 files. Worth distinguishing a verifier *finding*
  (opt prints an error and exits 1) from a *crash* (signal, no finding) at the three call sites, so
  the next one says "opt crashed" and, ideally, retries once.
- [ ] `P2` **The class freelists are exact-fit, so a reclaimed remainder usually goes unused.**
  `g_freelist` is indexed by `slot_bytes/16` and `sprout_gc_alloc_block` pops only that class, so a
  free 4064-byte slot is invisible to the 4080-byte request beside it and to every 32-byte one.
  So `sprout_vec_release_inline_tail` mostly makes bytes *reclaimable*, not reused; pages return
  to the OS only when a whole 1 MiB region empties; and the trigger's footprint floor grows ~6× in
  RSS when phases take turns across classes (`docs/gc-trigger-v0.md` §6.2.1). Options and prior
  art: `docs/gc-size-classes-v0.md` — re-carve a larger free slot for a class-k request (the split
  writes that header today); coalesce adjacent FREE slots in the sweep walk, which needs a
  `slotmap_clear` or HDRCHECK's "no start bit inside a step" assert fires; or make regions
  single-class, as five of six comparable heaps do. Unmeasured; needs the entry above's instrument.
- [ ] `P2` **The freelists are still wiped and rebuilt from *all* regions every sweep** — a
  prerequisite for the nursery, since a minor collection that marks only young objects but rebuilds
  the whole heap's freelist is not proportional to the young set. Making them generation-scoped
  (stop wiping; remove/re-add only the swept regions' entries) is the natural next increment, and
  the per-region touched-class bookkeeping it needs already exists
  (`fl_region_commit`/`fl_region_rollback`).
- [ ] `P1` **The galaxy game spends 7.7 ms of a 17.1 ms frame in GC, and 97% of its live set is
  one map.** Measured 2026-09-27, `docs/gc-generational-v0.md` §13.6: `game/app.sprout` in the
  uncharted-suns repo holds 77,653 live objects, of which `map=75,640`, collecting about every 17
  frames at p50 7,746 µs against that repo's 58 fps baseline. Pause tracks total slots and is
  floored by the live set (§13.2–3). **The "shrink the map" route was taken and backfired:** #407
  reports p50 7,713 → 797 µs but *total* GC 207 → 888 µs/frame — the trigger re-based onto the
  smaller live set while the swept footprint did not follow. This entry named only pause; the two
  are traded. Untried: move the map off the managed heap. Open: make the sweep proportional to
  something other than total slots (generation-scoped freelists are the prerequisite). The trigger
  half landed as the footprint floor (`docs/gc-trigger-v0.md` §9): GC per allocation 12–16× lower.
- [ ] `P3` **`http_log_middleware` overflows in `wall_loop`.** The bench sums `time.wall_micros()`
  (~1.76e15) over 1,000,000 iterations in `bench/http_log_middleware/`, so the fifth of its six
  phases traps on Int overflow (since 61fd1617, 2026-09-23) and `fmt_loop` never runs. Every GC
  figure taken from it since then covers five phases: `bench/results-2026-09-30-gc-floor.md`,
  `bench/results-2026-10-04-gc-trigger-b.md`. Fix: accumulate something bounded, then re-read
  those notes' rows.
- [ ] `P3` **The GC cycle timer measures elapsed time with a non-monotonic clock.**
  `sprout_gc_collect_with_reason` brackets the collection with `sprout_now_micros`
  (`gettimeofday`/`CLOCK_REALTIME`), while that function's neighbour documents the rule it breaks:
  "must not be used for elapsed-time measurement (use `time_now_micros` for that)". Those two calls
  are its only elapsed-time uses, so the fix is one call site. It removes NTP and clock-change
  artifacts from `SPROUT_DEBUG_GC`'s `elapsed_us`, and does **not** explain the unattributable
  pause tail filed below — both clocks count descheduled time.
- [ ] `P3` **A program cannot place a collection in its own frame — `sprout_gc_collect` is
  `static`.** A game with a 17.1 ms budget and a 7.7 ms pause would rather take it after present
  than wherever the allocation threshold lands, and OCaml exposes exactly this as
  `Gc.major_slice n`. Useful before any incremental work, since it costs nothing and needs no
  barrier. **Requires approval and is NOT approved**: exposing it is a new builtin (AGENTS.md
  "Builtin vs Stdlib" 4–6), justified on the impossible-in-Sprout ground rather than performance.
  Design, prior art and the reason incremental collection is *not* recommended yet:
  `docs/gc-frame-budget-v0.md`, which also splits the game's pause ~65% mark / ~35% sweep and
  notes that a resumable sweep needs the generation-scoped freelists entry above.
- [ ] `P3` **The GC pause tail is unattributable — `SPROUT_DEBUG_GC` cannot separate collector
  work from machine noise.** `docs/gc-generational-v0.md` §13.4: http_log_middleware's slowest
  collections ran 3.1–8.5 ms against a 33 µs median with identical `live`, `swept`, `marked` and
  region counts, and the same binary showed max 182 µs in one run and 8,516 µs in another. So no
  pause claim past p99 can be made from this instrument, which matters the moment anyone asks
  whether a frame was dropped. Needs a per-phase timer inside `sprout_gc_collect` (mark / sweep
  pass 1 / pass 2 / region release) and a quiet machine; the per-cycle `elapsed_us` already logged
  is the aggregate and cannot be decomposed after the fact.
- [ ] `P2` **Skip re-pushing already-rooted function parameters.** The rooting pass roots every
  heap-typed value live across a trigger, with no notion that one already owns a slot in the same
  frame — the recursive `queens(…)` re-roots three vectors it already holds. The fix belongs in
  `stdlib/compiler/ir_rooting.sprout`'s liveness dataflow; the `emit_args_with_roots` this entry
  used to name died with `codegen.sprout` and survives only in `docs/archive/`. **Worth ~20% of the
  whole-program-linked nqueens binary** — measured 2026-09-25 by neutralising all rooting (208 →
  165 ms at N=12, GC off both sides). An earlier correction here claimed the win was confined to
  separately-linked builds because the helpers inline away: wrong, inlining removes the call, not
  the alloca, the store and the shadow-stack bump.
- [ ] `P2` **nqueens allocates one `Vec` wrapper per `vec_set` — 16.7M per N=12 run**, which is its
  whole object-allocation count. Measured while landing nullary interning, which moved nqueens by 7
  objects and so disproved this entry's predecessor: it blamed `true`/`false` literals, but `Bool`
  lowers to a native `i1` (`br i1` in the emitted IR, no ctor), and tag 10 arity 1 is `Vec`. The
  cost is the persistent vector's copy-on-write wrapper, so the lever is the HAMT entry below or
  unboxing, not interning.
- [ ] `P2` **Bump-allocated nursery with no per-object metadata** — the canonical generational GC,
  and distinct from the older split-the-node-list draft, which keeps per-object `ManagedNode` and so
  cannot reduce per-allocation cost. Objects are identified by address-range membership and
  allocation is `arena_top += size` (~5 cycles vs ~50 for malloc + register). Survivors are copied
  to the old gen and gain full metadata on minor GC. **Its gate is met and its payoff understated:**
  whole-program linking landed, so push/pop no longer dominates, and the malloc/free family plus
  `madvise` was 23% of nqueens CPU, not ~10% (2026-09-25). Inlining small vectors' elements took
  the largest single contributor out of that share — nqueens now allocates 33.4M objects, not 50.1M
  — so re-measure before pricing this. See also the generational-step entry in §1, whose
  measurements re-scope it as compiler-only.
- [ ] `P3` **HAMT persistent vector for `vec_set`** — O(n) → O(log n). **Deferred:** at N≤14
  vectors are 12–27 elements (a single HAMT leaf), so path-copying is the same work as the current
  copy, and `vector_set` is 1.1% of CPU.

**Server and scheduler**

- [ ] `P2` **`serve` is a client-driven memory exposure.** ~1.4 MiB of stack per concurrent
  connection, fully resident because `makecontext` zeroes it: 40 `wrk` connections hold 130–237
  MB, and the shape scales with client-chosen concurrency. Sprout copied Go's
  goroutine-per-connection model while paying **512× Go's per-task cost** (1 MiB vs
  `stackMin = 2048`) — and cannot follow Go's answer of growing small stacks, because growth needs
  `copystack`'s pointer adjustment and Sprout's rooting is non-moving by design. Independent of
  which pooling design lands. `docs/green-task-pool-v0.md` §3.1.
- [ ] `P2` **A panicking handler kills its pool worker, and Sprout cannot catch it.** With
  spawn-per-connection a panic killed one connection; in a pool it permanently removes capacity, and
  a server that loses all workers stops answering with nothing logged. Ordinary client misbehaviour
  is already contained — `handle_connection` consumes the connection on every path — so this is
  strictly about a `panic` in user handler code. Both honest fixes are language-level: catchable
  failures, or a supervisor that observes worker exit and respawns. Same item as task-boundary panic
  isolation in §2.
- [ ] `P2` **HTTP keep-alive: without it, no high-throughput measurement here is server-bound.**
  `Connection: close` means one TCP connection per request; this machine has 16,384 ephemeral ports,
  so a 4 s run at ~32k req/s opens ~131k connections — 8× the range — and the client stalls on
  `TIME_WAIT` recycling. That, not the server, is the worker-pool benchmark's p99 tail: drained and
  kept inside the port range the same server measures **p50 = 35 µs / p99 = 243 µs**, versus 536
  µs / 66 ms on a long run. A real feature gap besides.
- [ ] `P3` **The accept loop is the next server bottleneck.** Worker count is irrelevant from w=2 to
  w=256, so once task creation leaves the per-request path the single accept task is the limit. Any
  further HTTP-server work should start there, not at the handler.
- [ ] `P3` **Make `SPROUT_TASK_STACK_BYTES` an env knob** (a compile-time `#define` today). Worth
  3× on the HTTP server for a recursion-depth tradeoff the deployment should own. The 1 MiB default
  has never been measured against real handler depth — measure that too.
- [ ] `P3` **`chan_select` allocates per call** — `malloc(n * sizeof(Chan*))` on every call plus
  `malloc(n * sizeof(SelectWaiter))` on the parking path, so a select loop pays the first every
  iteration. Unmeasured. Candidate second consumer for a runtime object pool, alongside `Chan` and
  `Scope`.
- [ ] `P3` **No idle/read timeout for the HTTP client, only the total one.** `timeout_ms` is a total
  deadline, right for the common case but unable to express "fail if the peer stalls for N seconds"
  on a long transfer. Both reference APIs offer both knobs (reqwest `timeout` + `read_timeout`; Go
  `Client.Timeout` + `Transport.ResponseHeaderTimeout`). Additive — it can wait for a caller that
  actually streams.
- [ ] `P3` **`listen(fd, 16)` — a 16-deep accept backlog**, well below every convention
  (`SOMAXCONN` 128 on macOS, nginx 511). **Downgraded from "sole cause of the p99 tail": measured
  not to matter here** — raising it to `SOMAXCONN` left p99 unchanged over 3 interleaved rounds.
  Hardening, not a fix, recorded because it was a plausible diagnosis that measurement killed. No
  hermetic regression is available: a too-small backlog manifests as an unbounded park, since the
  kernel drops the SYN and no error reaches either side.

### Prelude-helper cleanup arc (#602, #603) — review residuals

Cleanups the 2026-10-09 reviews of #606 and #607 reported and left. Unverified by design: confirm
each before acting. The compiler ones each cost a reseed, so land them as one PR.

- [ ] `P3` **Compiler: hand copies of `ast` and prelude helpers survive #607.** `infer.sprout:7440`
  `validate_ctor`, `validate_record_field`, `alias_uses_in_ctor`, `alias_uses_in_field` and
  `ast_to_ir.sprout:539` `record_field_types` only unwrap field types: use
  `ast.ctor_field_type_exprs` / `ast.record_field_type_exprs`, as `bundler.sprout` does.
  `ast_to_ir.sprout:1923` `find_capture_type_list` and `_fields` are hand-written `find_map`.
  `linear_check.sprout` `luse_member`, `luse_remove(_all)`, `unconsumed_binder`, `first_shared` and
  `first_consumed_borrow` are hand-written `any` / `list_filter` / `find`, about 30 lines.
- [ ] `P3` **Compiler: copy-paste and a quadratic fold left after #607.** `lint_rules`
  `list_shape_findings` and `list_prefix_findings` differ in rule id, message and terminator, and
  each walks the AST again. `analysis_service_driver`'s `source_type_of`, `source_instances` and
  `source_eval` repeat one three-step order, and its JSON is 23 nested `JsonObjectCons` chains where
  `lsp_driver` uses `json.object_from_pairs`. `infer.merge_effect_labels` folds `list_add_unique`
  (quadratic). `compound_tdict` now builds every unresolved TDict: rename it `unresolved_tdict`.
- [ ] `P3` **TUI: the reply tuple and one loop are still spelled out after #606.** `app.sprout`'s
  `Step` and `Update` aliases are private, so `ide/app.sprout` (7 sites), the `tui_files` and
  `tui_dashboard` examples write `(widget.Widget Msg, app.Flow, Asks)`: export them.
  `ide/app.sprout`'s `bar_handler`, `refilled` and `keys_on_event` write `widget.Handled` by hand.
  `grapheme.build` and `text.split_clusters` are one take/drop loop over `cluster_sizes`, walking
  each prefix twice; a `list_split_at` (#608) does it in one pass.
- [ ] `P3` **TUI: a private reverse-onto, a misplaced `first_row`, a duplicate assert.**
  `text.reverse_onto` copies the prelude's private `list_reverse_go`, and `no_prelude_core.sprout`
  and `children.sprout:64` do the same job: export a `list_reverse_onto`. `list_view` and `tree`
  import text_area's `viewport` module only for `first_row`; move it lower. `test.record` is
  `assert_true` with its arguments reordered.

### Compiler / Stdlib Misc

**Codegen and IR correctness**

- [ ] `P3` **`ast_to_ir` headers contradict the code beneath them, and one helper is dead.** The
  Bool/Unit codegen restrictions were lifted; the comments announcing them were not.
  `translate_lambda`'s header still reads "Rejects: Bool-returning lambda … deferred to a follow-up
  PR" (`ast_to_ir.sprout:2105`) sixteen lines above "Bool-return guard removed", and
  `is_supported_arg_type`'s still promises the ctor guard "keeps its own deferred restrictions on
  Bool and Unit" (`:812`) ten lines above "Bool is ACCEPTED" / "Unit is ACCEPTED".
  `find_bool_capture` (`:2072`) has no caller left. ~15 lines to fix, but a compiler-source change,
  so it costs a full reseed plus the golden-IR gate.
- [ ] `P3` **46 compiler comments cite `codegen.sprout`, deleted 2026-07-12 in `5f29b9da`.** Spread
  over `ast_to_ir` (31), `ir_lowering` (7), `sprout_ir` (6), `type_kind` and `field_kinds` (1 each),
  mostly as "mirrors codegen.sprout:<fn>" provenance notes whose target cannot be opened. They read
  as live cross-references and send a reader looking for a file that is two months gone. Either
  re-anchor each to the surviving definition or drop the citation; decide once and sweep.

- [~] `P1` **Arity mismatch through a function-typed VALUE is a clean runtime error, not a working
  call.** Both halves of the miscompile are closed — direct calls check arity in both directions,
  and every closure carries its parameter count in the GC header's aux field with
  `sprout_closure_arity_check` guarding each `IRApplyClosure` (gate: `just closure-arity-smoke`).
  **Still open, and it is a language call:** the guard *rejects* a mismatch, it does not make one
  work, so `h(1)(2)` for a two-parameter `h` errors at runtime and the type `Int -> Int -> Int`
  still advertises a currying the ABI does not implement. Closing it is either §8.3 generic apply,
  which reverses C-b's landed decision that under-application is an error, or Package C-a's
  arity-aware types, which makes both mismatches *compile* errors.
  `docs/currying-and-pipe-decision-v1.md`.
- [ ] `P2` **A redefined typeclass collides in the class-method wrapper symbol.** A file declaring
  `class Eq a` when the prelude also declares one emits two `@__cm_Eq_eq` definitions and the IR is
  rejected. Module qualification is threaded *most* of the way — the dictionary parameter is
  correctly `__tc_demo.Eq_0_eq` — and stops at the method wrapper, mangled from class and method
  names only. Presumably include the class's module in the `__cm_` mangling, but the
  dictionary-passing lowering reads these names in several places, so trace before costing. Affected
  fixtures: `conformance/run/instance_check.spr`, `type_classes.spr`.
- [ ] `P2` **Do notation resolves the monad family by unqualified name, so a user-defined `Maybe` is
  not bindable with `<-`.** `x <- safe_div(a, b)` returning the file's own `Maybe Int` reports
  `Type mismatch: Int vs demo.Maybe Int` — the bind machinery matches the family on the bare name,
  so a qualified `demo.Maybe`/`$entry.Maybe` is not recognised as the same family. Same class of gap
  as the item above (qualification is not uniform across resolutions that key on names) and worth
  fixing together. Affected fixtures: `conformance/run/codegen_do_bind.spr`,
  `test_ir_codegen_do_bind_strip.spr`. *(Both were surfaced by making the prelude unconditional and
  are **pre-existing** — each reproduces with an ordinary `module demo` header. The files that hit
  them were previously exempt only because they received no prelude at all.)*
- [ ] `P3` **Ten intrinsics get a `declare` for a symbol the runtime does not define** —
  `@to_double`, `@double_to_bits`, `@double_from_bits` and the seven `@bit_*`. Nothing calls them
  any more and LLVM does not require an unreferenced declare to resolve, so this is inert today.
  **It is worth closing because it is the mechanism that made the eta bug expensive to find:**
  `print`/`eprint` are on `is_hardcoded_intrinsic`'s list so their declare is suppressed and a
  dangling reference is caught by `opt --passes=verify`, which every IR gate runs; the other ten
  keep the declare, LLVM believes the promise, and the lie survives to the **linker**, which only
  `run-example-canary` (5 files) and `test-conformance-run` reach. Adding the ten to
  `is_hardcoded_intrinsic` moves any future regression one stage earlier, into a tool that runs
  everywhere. Costs a reseed and a golden cycle.
**Types and inference**

- [ ] `P2` **Top-level `let` annotations sharing a type-variable NAME share one variable across the
  whole module.** `build_type_var_dict` seeds each lowercase name as one variable *per name*, not
  per declaration, and the substitution threads across declarations, so one binding's annotation
  narrows another's. Loudly (`let p1: Maybe a = Just(1)` then `let p2: Maybe a = Just("x")` →
  mismatch) and silently, which is worse: `let bump: a -> a = \x -> x + 1` narrows `a` to `Int`, so
  a following `let ident: a -> a = \x -> x` checks clean as `Int -> Int` and `ident("hello")` fails
  at an innocent call site. Each declaration's annotation variables should be freshened per
  declaration. This is why `docs/lambda-parameter-annotations-v0.md` §2.1 rejects type variables in
  lambda annotations rather than reusing `let_annotation_type` — the proposal must not inherit it.
- [ ] `P2` **Honour lambda parameter type annotations in inference.** `ast.Param` carries the
  annotation and `infer.sprout` discards it. Two-pass argument inference masks this in *argument*
  position — the callee's parameter slot supplies the type — but nowhere else, so ``let wrap_it
  = \(s: String) -> `<${s}>` `` still fails with `use of undefined value '@to_string'`. Fix needs
  the enclosing declaration's `local_vars` threaded into `infer_lambda_expected` before calling
  `type_from_ast`, or `\(x: a) -> …` inside a `where`-constrained function turns `a` into a rigid
  `TConst` instead of binding the declaration's variable. Spec §5.3 carries a "not yet enforced"
  note. Seed-gated; needs success *and* failure typecheck tests.
- [ ] `P2` **Audit HM inference for latent typeclass-constraint / accumulator-type interference.**
  The original `fold_indexed` failure was two *syntactic* barriers, not an HM bug — no tuple
  destructuring in lambda params, and a nested `match` in call-arg position — and the natural
  `(Int, b)` accumulator works once both are sidestepped. The open question is whether
  OutsideIn-style gather-wanted / solve-wanted separation is needed for more complex
  constraint+accumulator patterns. If a regression surfaces, audit `check_call_constraint` and
  `inject_constrained_fn_dicts`.
- [ ] `P3` **`x <- pure(e)` in a do-block leaves `x` unusable: the Applicative is never pinned.**
  `s <- pure("abc")` then `s ++ "!"` reports
  ``` `++` needs matching Semigroup operands: Type mismatch: $t2553 String vs String ``` —
  `pure : a -> f a` with nothing constraining `f`, so the bind yields `$t String`. The surrounding
  `do` is already committed to one Applicative, so this looks like the block failing to unify its
  steps' functor with `pure`'s — **that reading is inferred from the error, not confirmed in
  `infer`**. The message names the *operator*, not the binding, so it points at the wrong line.
  Workaround: pass the value as a parameter, or annotate.
- [ ] `P3` **A class method is not reachable through a module alias.** `import m as m` then
  `m.method(x)` fails with `Unknown variable: m.method`; only `import m (method)` works.
  `bundler.add_class_to_symbols` files method names in `method_names` and never in `exported_vals`,
  and the alias value table is populated from `sym_exported_vals` alone. Latent until now because no
  module outside `stdlib/json.sprout` ever called an imported class's method. Fix is to register
  method names in the alias table — **settle first whether a method SHOULD be alias-qualifiable**,
  given that dispatch is by instance rather than by module.

**Language surface gaps**

- [ ] `P2` **There is no `Char -> Int`.** The prelude declares `char_from_codepoint : Int -> Char`
  and nothing going the other way, so a `Char` can be compared and printed but never *measured* —
  any character-property API (width, category, grapheme class, `is_digit` on a non-ASCII digit) is
  unwritable over `Char`. **The value is already there:** `char_from_codepoint` (`sprout_runtime.c`)
  says outright that a Char IS its codepoint as an immediate i64, so `char_codepoint : Char -> Int`
  is the same identity function with the arrow reversed and needs no new runtime capability.
  Workaround in use: `stdlib/unicode` decodes UTF-8 to `List Int` itself and keys every entry point
  on `Int`; where a numeric value must come out of a character, the house idiom is an index into an
  alphabet string, which works for 62 known characters and does not generalise. Blocks a
  `Char`-shaped public API for `stdlib/unicode` and `stdlib/tui`'s per-cell width lookups.
- [ ] `P2` **A String containing U+0000 is silently corrupt: the length header and the bytes
  disagree.** Sprout Strings are NUL-terminated C strings with a length in the heap header, and
  `string_from_char(char_from_codepoint(0))` writes a header saying 1 byte over content whose
  `strlen` is 0. The NUL is *dropped*, not truncated at — `"A" ++ NUL ++ "B"` has byte length 2
  and both letters survive. `SPROUT_GC_HDRCHECK=1` turns it from silent into an abort
  (`HDRCHECK: str_byte_len aux=1 strlen=0`), confirming an invariant violation rather than a
  documented limitation. Options: reject U+0000 in `char_to_str`/`string_from_char` with a located
  panic, or make the header the sole authority on length and stop calling `strlen` — wider than
  this warrants until something needs embedded NULs. Consequence: `unicode.cluster_sizes` is the
  primary API and `graphemes` is documented lossy for U+0000.
- [ ] `P2` **A large list/`Vec` literal hits a fixed root-pool ceiling.** N=6,000 `Int`s in one
  literal fails with `GC root pool exhausted` (a fixed `RootNode g_root_pool[131072]`) — in
  `--phase check`, so this is the COMPILER's own root stack recursing over the literal, not emitted
  roots, and 22 × 6,000 ≈ 132,000 matches the pool exactly. The O(N²) compiler memory that used to
  dominate is fixed (800 → 48 MB, 2,000 → 179 MB, from 825 MB / 4,631 MB): see
  `docs/gates.md` §Compiling one long block. Lexing is reported superlinear in literal length
  (80 KB → 1.9 GB) on a string-literal source; still unverified. It is not the list-literal
  growth once blamed on it: `--phase bundle` is flat to 1,920 elements. Shipping the table as a
  STRING literal decoded at startup remains the smaller-IR option: emitted IR is constant-size
  whatever the table.
- [ ] `P2` **A long `do` block hits the same root-pool ceiling.** One `do` block of 4,400 plain
  `print(...)` steps fails with `GC root pool exhausted`; 4,200 compiles. Cost is linear below it
  (27 / 42 / 69 MB at 600 / 1,200 / 2,400 steps).
  `--phase check` passes, so the crash is after type checking. `translate_do` in
  `stdlib/compiler/ast_to_ir.sprout` recurses once per step (not a tail call), and 131,072 /
  ~4,300 ≈ 30 roots per frame. That fits recursion depth, but nobody has traced it yet. The step
  kind does not matter: interleaved `let`/`print` dies at the same count.
- [ ] `P2` **Chained `let` in a `do` block compiles in quadratic time.** `let xN = x(N-1) + 1` × N
  in one block: 0.7 s / 56 MB at 600, 2.4 s / 153 MB at 1,200, 9.6 s / 552 MB at 2,400. The same
  count of independent lets (`let xN = N + 1`) takes 0.2 / 0.24 / 0.39 s, so the chaining drives
  it; their RSS still grows 2.5× per doubling. `--phase check` is flat, so it is past type checking.
  Cause not found; `sra_core_eligible(name, e, rest, …)` reads all of `rest` at each `let`, so check
  it first. Neither cost gate covers it: both fixtures are flat blocks with no bindings.
- [ ] `P2` **Add a `module prelude` header to `prelude.sprout`** so all its symbols get an
  `@prelude.` prefix in emitted IR, eliminating future POSIX/libc symbol collisions — the `pipe`
  → `pipe_apply` rename is the tactical fix, this is the principled one. Requires a
  stage-0/stage-1 rebuild, since the bundler and checker both derive the canonical qualified name
  from the module header.
- [ ] `P2` **Generalize `stdin_read_bytes` to `io_read_bytes(fd, n)`** once a `File`/`Fd`
  abstraction exists. `stdin_read_bytes` is a thin `fread` wrapper justified by LSP Content-Length
  framing; a proper descriptor read subsumes it and enables pipes, sockets and files without extra
  builtins. Candidate design: `stdin_fd()`/`stdout_fd()`/`stderr_fd()` constants plus
  `io_read_bytes(fd, n)`.
- [ ] `P3` **No exported `Int` bound constants, so every caller open-codes them.** There is no
  `int_max`/`int_min` in the stdlib: `math.sprout` keeps a **private**
  `min_int() = 0 - 9223372036854775807 - 1` and nothing exposes the maximum. The awkward spelling is
  not stylistic — **the lexer cannot represent the INT_MIN literal**, which is exactly the
  argument for defining it once rather than asking every caller to know the trick. Proposal: export
  `int_max` and `int_min` from `stdlib.math.int`, have the private `min_int()` and the prelude's
  `int_is_min` use them, and note in the spec that the negative bound is spelled `0 - int_max - 1`
  until the lexer accepts the literal.
- [ ] `P3` **Document `Double` normatively in `spec-v0.md`.** It landed 2026-07-06 but the spec only
  acknowledges it as an experimental extension plus a `ToString` row — there is no normative
  section defining literals, the concrete-only same-type arithmetic rule (no implicit `Int`/`Double`
  coercion), comparison, or `to_double`. Add one once the type graduates from experimental.
- [~] `P2` **`instance Eq Double` / `instance Ord Double` in the prelude.** *(The originally filed
  silent-no-op `assert_eq` is GONE — the same program is now rejected at compile time with a
  located `No instance of Eq for Double`, most likely by the superclass/unresolved-dict work that
  turned null-filled dicts into compile errors.)* What remains is the stdlib gap: `Double` cannot be
  used with any `where Eq a`/`where Ord a` function — `assert_eq`, `check_eq`, `list_contains`,
  sorting. The workaround explains why it went unnoticed: `assert_true(state, label, x == y)` works
  because `==` on `Double` is the builtin operator path, which never consults the class. See the
  `Ord Double` entry in §1, which carries the decided IEEE semantics.

**Stdlib and prelude**

- [ ] `P3` **`range_to_vec` is O(n²)** — `prelude.sprout:203` folds via copying `vec_append`,
  already acknowledged in `test_vec_sort_stacksafe.spr`. Wants a doc note on the function, or a
  `vec_from_list` single-allocation rewrite mirroring the one at `prelude.sprout:314`.
- [ ] `P3` **Replace per-arity `ToString` tuple instances with a generic approach.** Arities 2–5
  are explicit declarations; a variadic form needs either a type-level natural-number index over
  tuple arities, a deriving mechanism generating instances to a compiler-defined max, or
  language-level variadic typeclass support. The current approach is pragmatic — 6-tuples are
  uncommon — so if 6+ is needed before variadic support lands, add the instance explicitly.
- [ ] `P3` **`examples/digit_recognizer/recognizer.sprout:256` looks like an off-by-one.** It uses
  `range_each(epoch_step, range(0, total_epochs))`, which is inclusive and runs `total_epochs + 1`
  epochs, while every other loop in the file goes through `upto(n) = range(0, n - 1)`. Confirm
  intent before changing — an extra epoch is not observably wrong output, which is why it has
  survived.

**Tooling, diagnostics and cleanup**

- [ ] `P3` **The rule for which `do` steps start a `try` is written four times.**
  `ast.elaborate_steps_head`, `ast.elab_steps`, `infer.infer_do_steps` and `infer.steps_tail` each
  match the same step shapes. `steps_tail` could walk `elaborate_steps_head`'s output instead; the
  other three need their `DoPatStep` arms for exhaustiveness, so one `ast.elaborate_step` helper
  did not remove them. A fifth copy would drift silently.
- [ ] `P3` **`try` positions reach `linear_check` as fake `@try:` env entries.** `infer` writes a
  Unit-typed name per `try` into the scheme env; every `match` in `linear_check.sprout` builds the
  string key and looks it up, and the answer is threaded through five functions. Pass a set of
  positions instead, or look up only on the error path.
- [ ] `P4` **`infer.branch_mismatch`'s `TryBinding` arm is unreachable.** `arm_context` turns
  every `TryBinding` into a `TryArm` before `branch_mismatch` sees it. Drop the arm, or make the
  types say so.
- [ ] `P4` **`line:col` formatting has four private copies.** `parser.pos_str`,
  `lowering.pos_loc`, and two inline in `infer.sprout`'s `try` messages. Export one helper from
  `source.sprout` and call it from all four.
- [ ] `P3` **The fallible-bind error-type hint names `map_error`, which does not exist.** The
  diagnostic in `infer.sprout` ("Convert the error at the bind with `map_error`") and spec-v0.md
  §5.9 both name it; the prelude has `result_map_error`. Pinned by
  `tests/conformance/type_error/fallible_bind_error_type_mismatch.err`. Rename in all three;
  the diagnostic is compiler source, so it needs a reseed.
- [ ] `P3` **`fmt` skips normalization on any line containing a backtick template.** Two adjacent
  lines of the same construct format differently: `\(s: String) -> s` is rewritten to
  `\ (s: String)` while `\(s: String) -> ` + a template is left alone — and `fmt` reports both
  `ok`, so the formatter has two canonical forms for one construct depending on whether a template
  appears anywhere on the line. Not corruption; the residue of the line-based `format_source` design
  whose multi-line-template bug was fixed in `7d1171f`. Consequence: a corpus mixes `\(` and `\ (`
  and `fmt --check` accepts both, so the inconsistency cannot be gated away.
- [ ] `P3` **`unresolved_in_types` names the LAST unknown type in a list, not the first.** The
  recursion is tail-first and prefers the tail's result, so `type Foo (..) = | C Bad1 Bad2` reports
  `Bad2` — inconsistent with `unresolved_in_type`'s `TypeApply`/`TypeArrow` arms and with
  `unresolved_in_params`, which are head-first, so which name the diagnostic picks depends on the
  syntactic position. Affects constructor argument lists, tuple types and constraint argument lists.
  A one-line flip, wanted with a fixture pinning that the *first* unknown is named.
- [ ] `P3` **The negative-literal shift-count rejection has no conformance fixture.** Spec §1633
  states it normatively and `ast_to_ir` implements it, but `tests/conformance/` has zero hits. **The
  gap is structural:** the categories are keyed to phases — `type_error/` is `--phase check`,
  `parse_error/` the parse phase, `executable_error/` `validate_entrypoint` — and **there is no
  category for an `ast_to_ir`-phase rejection**. Either add one, or move the rejection into the
  check phase where a harness already exists (`docs/ranges-v0.md` §7 chose the latter rather than
  reproduce this gap).
- [ ] `P3` **Audit every other consumer of the effect's DUAL bookkeeping.** A declared effect is
  recorded twice — on the innermost arrow *and* on the `Scheme` — because a zero-parameter
  function has no arrow to hold one. Any pass that reconstructs a type without carrying the `Scheme`
  therefore drops the effect, **silently and only for nullary functions**. Not hypothetical: it is
  exactly how hover, `:type` and the analysis service came to report every `main` in the tree as
  pure (fixed 2026-08-18). The fix was local to one caller; the *class* was not audited. Wanted:
  find the other places that read a type back out after inference or re-generalisation and check
  each for the nullary case specifically — a parameterised function cannot expose the bug, so an
  audit testing only the obvious shape finds nothing. Removing the duplication outright is the
  deeper fix and a bigger call.
- [ ] `P3` **Decide the fate of `translate_append_operands`' String/List peepholes**, kept as a
  fallback on 2026-08-23 without evidence they can ever fire. Investigating why `03_strconcat`
  cannot go `no_prelude` found three lowering paths for `++`, two of them dead; one was deleted
  (provably unreachable — `infer.check_binary` routes `++` to `append_via_semigroup`, which
  returns a `TCall` and never builds a `TBinary`). Whether keeping the other two was right is the
  open question.
- [ ] `P3` **Decide when to delete the deprecated brace form of `class`/`instance` bodies.** The
  layout form is idiomatic, the corpus is migrated, the brace form is deprecated, and
  `lint_rules.deprecated-brace-body` reports every occurrence — and since the pre-commit hook
  fails on any lint finding, new brace code cannot be committed. So the deprecation is already
  enforced and the question is only *when the parser support goes*. Prior art is split on ever
  removing it: Haskell keeps both permanently (layout is *defined* as brace insertion and the two
  can be freely mixed), Scala 3 keeps optional braces, PureScript documents layout only.
  **Prerequisite before deleting:** the lint rule is currently the only thing pointing users at the
  fix, so removal must land the parse error's message carrying the same guidance.
- [ ] `P3` **Three answers to "which names does this pattern bind?" remain** —
  `ast.pattern_names` is the exported one and six copies now delegate to it, but `dce.bind_pat`
  and `infer.bind_pattern` fold into an accumulator `Set` rather than returning a list, and
  `linear_check.pattern_linear_binders` is type-directed: it consults `types.Type` to report only
  the binders carrying obligations. The two `Set` ones want `ast.pattern_names_into(pat, acc)`,
  to drop the intermediate list `set_from_list(ast.pattern_names(p))` allocates per match arm in
  free-var computation; the type-directed one asks a different question and stays. All three
  are exhaustive, so a new `Pattern` variant is a compile error at each — hence P3.
- [ ] `P3` **`layout.starts_step` duplicates the parser's notion of "starts an expression",
  by hand** — a token-kind list maintained in parallel with what `parse_primary` accepts,
  with nothing to detect divergence. Each divergence has cost a PR (a float literal and a prefix `!`
  could not begin a do step; neither could a `let … in`). Wanted: a test that derives one from the
  other — for every token kind the lexer can emit, assert the two agree on a minimal expression
  starting with it. **Feasibility unverified**: some tokens are expression-legal only in context, so
  the test needs a way to avoid false failures, and that design question is the actual work.
- [ ] `P2` **A mid-line `let` after a `do` step silently becomes a top-level `let`.**
  `layout.item_keyword` starts a declaration at a `let` right after an operand on its line, so
  `do` / `print("a") let y = 2` closes the `do` and makes `y` a module global. Before the layout
  pass this was "a do block takes one step per line"; with a step after it, the error now lands
  on that next line as "Expected declaration". Contradicts spec §5.2.1a. Fix: count such a `let`
  as a declaration only when no `do`/`let`/`where` block is open (`fn one() = 1 let two = 2`
  must keep working — `test_layout_blocks.spr` pins it).
- [ ] `P3` **`layout.awaited` sees only the top frame**, so an `else` left of the `do` holding
  its `if` fails ("Expected keyword else") when the then-branch opened a block: `if c then do` /
  … / `else`, or a multi-line `match` then-branch. The dedent closes that block and drops the
  `FThen` marker. Spec §2.1 states the any-column rule without this exception. Fix: a line-start
  `else` left of every block above the innermost `FThen` closes them and keeps the marker. The
  old parser rejected this shape too.
- [ ] `P3` **Type-driven-design gaps from the "parse, don't validate" audit.** Adherence is strong
  overall; these remain. *Compiler, seed-gated:* `Token TokenKind String pos` lets kind and payload
  disagree, unlike the `Expr`/`Pattern` ADTs where each variant carries its own typed payload;
  operators are raw `String` in the AST, where a closed `BinOp`/`UnOp` would make bogus ones
  unrepresentable; `is_function_scheme`/`is_polymorphic_scheme -> Bool` are boolean-blind where a
  `SchemeShape` is honest; scalar-ness is recomputed downstream by string-matching the type name
  instead of riding on the `Type` ADT. *Stdlib:* `NodeInterp (Vec String) Bool` in `template.sprout`
  → `Escaping = Safe | Escaped`, the smallest diff of the set. `Ord.compare`'s `Int` sentinel is
  the highest-value gap and is tracked separately in §7.5.
- [ ] `P3` **Re-add `SPROUT_TIME_PHASES` per-phase compile timing on the typed path.** The
  direct-codegen retirement deleted the machinery that emitted the
  `[phase] bundle=… check=… lower=…` stderr line; it was only ever wired to the now-gone path,
  so nothing regressed, but the diagnostic is useful. Wrap `time_now_micros()` around
  `compile_program_streaming` and the recheck phases in a timed variant, gated on the same env var.
- [ ] `P3` **Implement Sprout source-level DWARF on the typed path.** `--emit-ir --debug` is a
  no-op: the direct backend was the only path that honoured it, and `just build-debug` still
  produces an lldb-loadable binary via clang `-g -O0`, just without Sprout source attribution.
  Net-new work in `ast_to_ir`/`ir_lowering` emitting `!DILocation`/`!DISubprogram` keyed to source
  positions. **Merges with the `--debug` debugger item under V1 roadmap candidates.**
- [ ] `P4` **Collapse `--use-ir-codegen` onto `--emit-ir`.** With `--use-direct-codegen` gone it is
  a pure synonym — both route to `run_file_use_ir_codegen` — surviving only because justfile
  recipes call it. Repoint those recipes and drop the alias arm from `compile_driver.main`.

### CI / Build Performance

- [ ] `P2` **The apt LLVM install has no retry and no cache, and it hung CI for 24 min.** The
  `test-*` shards and `lsp` run a bare `sudo apt-get update && sudo apt-get install -y llvm clang …`
  with no retry and no package cache; on one run that step sat 24 minutes against 24 seconds on the
  run 43 minutes earlier. A 5-min `timeout-minutes` now makes a stall fail rather than burn the
  6-hour job default, but a failed fetch is still a red run, now on three shards instead of one.
  The workflow caches the deterministic thing and not the fragile one: the reproducible bootstrap
  (23 s) is cached, the networked third-party fetch is not. Next increment is a retry loop; measure
  before assuming an apt cache helps.
- [ ] `P2` **Straggler heavy bundlers still run on every PR.** The compiler-suite directory gate
  misses the ~10 `tests/stdlib/test_ir_*` suites that also bundle the whole compiler (one is 222k IR
  lines / ~17 s emit) but live in flat `tests/stdlib/`. Move them under `tests/stdlib/compiler/`, or
  gate by an explicit file list.
- [ ] `P3` **`test-gates` is the slowest CI job; six gates make most of it.** Since the core
  suite split, run time is `test-gates` (270–454 s) or the compiler jobs (~285 s). On a fast
  runner: `task-io-smoke` 116 s, `type-errors` 82, `conformance-run` 72, `rooting-cost` 54,
  `ir-golden-diff` 52, `overflow-smoke` 48 (each gate prints its seconds). Profile the top ones
  before splitting the job again: more shards means more runner draws, and one slow draw sets
  the run. Runner speed varies 1.7–2× per gate between runs minutes apart, so compare per-gate
  times across several runs, never one run's total.
- [ ] `P3` **`task-io-smoke` mostly waits, and holds a fan-out slot while it does.** Its 44
  fixtures run one after another: alone it takes ~123 s wall for ~40 s CPU (locally), since the
  fixtures wait on timers and deadlines. It starts first in `ci-fast-gates`, so today it overlaps
  the other gates; once they shrink below it, it sets the step's floor. Running fixtures in
  parallel needs a per-fixture `out.ll`/`bin` path (all share one today).
- [ ] `P3` **`task-io-smoke` fixtures bind fixed ports, so two worktrees running it collide.**
  Each fixture listens on a hard-coded port (e.g. `read_exact_deadline.spr` on 28982). Two
  sessions running the gate at once fail with `tcp_listen: bind failed`, which reads as a parking
  hang (seen 2026-10-04). Fix wants port 0 plus a way to read the bound port, which `listen_local`
  lacks (#335/#336). Parallel fixtures inside the gate would need this too.
- [ ] `P3` **CI has no arm64 Linux job, and `linux-smoke`'s value is latency, not OS coverage.**
  `linux-smoke` adds no OS coverage — `ci.yml` is `ubuntu-latest` with the same env and
  `ci-fast-gates` already contains `task-io-smoke`. What it covers is the *architecture*: its
  container is the host's (aarch64 on an Apple-silicon Mac), CI is x86_64. So the scheduler,
  epoll/timerfd and GC are smoke-tested on arm64 Linux only on a contributor's Mac, by an opt-in
  gate, while `release.yml` builds `sprout-linux-aarch64` on an `ubuntu-24.04-arm` runner and never
  runs `task-io-smoke` on it. That runner is free on public repos, so add an arm64 job to `ci.yml`,
  or at minimum run `task-io-smoke` in `release.yml` before uploading. No QEMU needed.
- [ ] `P3` **Flatten the pre-existing `staircase-of-doom` sites** exposed by the records PRs and
  committed around with `--no-verify`: `infer.sprout`'s `resolve_obligation` family, `infer_range`
  and `typecheck_fn_decl` body; two `lowering.sprout` sites; one in `driver.sprout`. **These are not
  `let..else` sugar swaps** — `infer_range`/`typecheck_fn_decl` thread `InferResult`/`Result`
  inside effectful `do` blocks and every failure arm binds and uses the error, which `let..else`
  (pure body, unwrap-or-constant-default) cannot express. The behaviour-preserving fix is
  **helper-function extraction**, the existing `infer_if → infer_if_checked → infer_if_merge`
  pattern — which adds a call boundary, so it changes the IR and needs a real `refresh-seed`, not
  a fingerprint-only ack. Its own commit; verify via the full suite, since inference is heavily
  exercised.

#### Runtime-invariant confidence tooling

- [ ] `P2` **Run the Sprout suites against a sanitized compiler.** The C *unit* tests already run
  sanitized (`tests/c_runtime/run.sh` builds with `-fsanitize=address,undefined`, gated as
  `c-runtime-test` in `gate` and `ci-fast-gates`, with CI installing `libclang-rt-dev` so the link
  cannot silently fall back). What is open is the other half: `just build-stage2-asan` only *builds*
  a sanitized stage-2 and is not in `gate`. Add a recipe running a representative test + canary set
  against the instrumented runtime, then a CI step. A stray `payload-8` read on a bare string, or
  any UB the HDRCHECK strlen-assert misses, would then surface as a located C stack trace instead of
  a silent wrong value. **Re-measure before deciding nightly-vs-PR:** the old sizing constraint
  (ASan ~2× slower, 2–3× fatter, on a memory-tight worker at capacity 2) was measured against a
  self-hosted box that no longer exists.
- [ ] `P2` **Compare each `extern fn`'s C definition against the emitted `declare`.** The
  duplicate-declaration half is gated — `scripts/check_extern_signatures.sh` enforces one C symbol
  to one `extern fn` — but it never reads `runtime/*.c`, so the type comparison is untouched and
  is the whole of what remains. Second silent extern-ABI mismatch found by accident:
  `_Bool`-vs-`i64` Bool returns, after CPR width-3 needing `sret` (`native_set_to_list` returned
  `Nil` for months). Both were invisible to `opt --passes=verify` and to linking, because LLVM only
  sees the `declare`. Derive each expected C signature from its `extern fn` and fail on a mismatch,
  wired into `ci-fast-gates`; longer term generate the C prototypes from the declarations. That also
  closes the reverse hole: `check_approved_builtins.sh` greps `long long <name>(`, so a builtin
  returning anything else is invisible to `APPROVED_BUILTINS`.
- [ ] `P2` **Both seed checks hang off a commit, so a rebase replays a stale seed unseen.**
  `scripts/seed_gate.sh` matches the command text for a literal `git … commit`, and
  `.githooks/pre-commit` is a git `pre-commit` hook — which `git rebase` does not run for replayed
  commits (verified empirically 2026-09-20). Rebase is exactly when the seed goes stale: master's
  compiler sources moved, and the replayed commit still carries the seed built against the old
  base. Observed on PR #317 — a stale seed was pushed and only CI's
  `just verify-bootstrap-fixed-point` caught it, one round-trip later. A `pre-push` hook is the fix:
  it fires however the push is invoked, it is the moment the damage actually escapes, and
  `core.hooksPath` already points at `.githooks/`. `docs/gates.md` §Bootstrap seed has the detail.
- [ ] `P3` **The seed gate's path regex cannot see a nested stdlib module, and that is luck, not
  design.** `scripts/seed_gate.sh:46` matches `^stdlib/[^/]+\.sprout$` and
  `^stdlib/compiler/[^/]+\.sprout$`, so `stdlib/fs/path.sprout`, `stdlib/math/*.sprout` and
  `stdlib/unicode/*.sprout` are invisible to it. Harmless TODAY only because no nested module
  contributes code to `bootstrap/compile_driver.ll` — `stdlib.fs` imports `stdlib.fs.path` and the
  compiler imports `stdlib.fs`, but nothing calls into it, so DCE drops it (verified: no
  `fs.path` symbol in the seed). The moment the compiler uses one, edits to it stop being gated and
  the staleness surfaces a round-trip later in CI. Widen to `^stdlib/.*\.sprout$`, or derive the
  set from what the bundler actually pulls.
- [ ] `P3` **Transactional bootstrap (never destroy the last-good stage-1).** A failed bootstrap can
  delete the only working stage-1 binary, leaving no way forward but the committed seed. Bootstrap
  should stage the new binary to a temp path, verify it (fixed point + a smoke) before swapping, and
  keep the previous one as an easy-rollback `.last-good`. Turns a bricked local checkout into a
  one-command restore. Lower leverage than the gates above because it protects the *developer loop*,
  not the correctness of landed code.

### Dispatch Soundness & Diagnostics

> Motivated by the `vec_sort_by` projection-sort crash (PR #176) — a dictionary mis-resolved to
> the element type instead of the projected key type, a silent SIGSEGV rather than a compile error.
> Full analysis: `docs/retro-dict-dispatch-soundness-2026-07-13.md`. The four architectural items
> (core verifier, dispatch trace, loud heuristic, canonical identity) are landed; what follows is
> the residue, ordered by leverage.

- [ ] `P3` **A compound constraint with no instance for its head fails at codegen.** A caller
  that neither declares `where C (T a)` nor can build it (the class has no `T` instance) gets
  `under-application ... reached codegen` instead of a diagnostic: `infer.compound_head_tdict`
  returns Nothing. A method call on such a constraint is already rejected, so this is rare.

- [~] `P1` **A `where`-constrained function used as a first-class VALUE.** Fixed everywhere the
  dictionary is readable at the mention, by rewriting a bare mention into the eta-lambda the
  programmer could have written (`eta_expand_constrained_arg`) in `infer_arg_slots`; the five
  remaining value positions then closed with a single resolver fix. **Residual: the reporter's
  original failure has never been reproduced.** They reported the unresolved-dict poison thunk's
  "please report" message; ten probe shapes here reached the arity panic or ran correctly and none
  emitted a poison, `Double` included. Source shapes DO reach it: #423 (an inner dictionary at a
  variable unification bound away, now checked in `resolve`) and an instance missing a method (now
  rejected at the instance). **Get the triggering expression from the reporter first** (the
  message names its line and column); any other poison-reaching shape needs a producer guard.
- [~] `P1` **Core verifier for dictionary passing — phase 2b (IR-level) pending.** Phases 1 and 2a
  are landed: `verify_dispatch.sprout` re-derives each constraint variable's type from the callee's
  SOURCE signature, genuinely independent of the resolver, and rejects a call whose injected `TDict`
  head disagrees. What phase 1 skips rather than verifies: forwarded/polymorphic dicts inside a
  generic function, and the `++`/`mconcat` **lowering-discard** case where the resolved dict is
  correct but dropped during IR emission, which a post-resolve pass structurally cannot see. Phase
  2b is for that second one — correlate the threaded dict argument in the lowered IR against the
  constraint's resolved head, `translate_append_operands` being the historical discard site.
  Residual gap in phase 1: class-method return-type dispatch via `TMethodRef` is uncovered, the
  signature table being `TFnDecl`-based while `check_call` keys `TVar` callees.
- [ ] `P2` **The remaining ungated scan can still pick the wrong marker.** When
  `check_instance_for_marker` cannot identify which argument carries the class variable it calls
  `check_instance_fwd` with `Nothing`, keeping the class-only scan and its first-in-dict-order
  defect. Repro: two constraints `ToString a, ToString b` rendered with `to_string` — both calls
  lower to `a`'s dict, so the `b` value renders through `a`'s instance, and
  `SPROUT_TRACE_DISPATCH=1` shows the caller resolving all four correctly, so the mis-selection is
  inside the callee. Gating that site too breaks the prelude's `map4`, leaving the scan load-bearing
  for at least the Applicative shape. Closing it needs the post-pass repair to cover the
  no-class-var-arg case — `dispatch_type_for_vars` finds a class variable nested in a container,
  `class_var_arg_or_fallback` cannot — then Applicative re-checked against it.
- [ ] `P2` **The uncovered-dictionary diagnostic prints a compiler-internal placeholder and gives
  advice that cannot be followed.** For an unannotated parameter it says "add
  `where ToString _unann_n`" — and `_unann_<param>` is a synthesized placeholder the compiler's
  own comment describes as "not written by the programmer", so the suggested edit is unwritable, and
  no rename would help: with the parameter unannotated there is no type variable to constrain. **The
  real remedy is to annotate the parameter, which creates the variable, and constrain that**
  (verified to compile and dispatch at two types). The rejection is correct; only the message is
  wrong. Fix: when the subject variable carries the `_unann_` prefix, strip it and advise annotating
  *parameter `n`*. Needs its own `type_error` fixture whose message substring differs from both
  existing `uncovered_dict_*` fixtures, since `_test-reject` matches by substring.
- [ ] `P3` **A `where` constraint on an applied type variable does not work.**
  `fn g(x: f a, y: f a) -> Bool where Eq (f a) = Just(x) == Just(y)` stops at codegen with
  "internal error: under-application of 'g' reached codegen (arity 3, got 2)", and `= x == y`
  under the same `where` is rejected as an ambiguous `eq`. So a context needed at `f a` has no
  remedy but a concrete type, which is what the #423 context check now tells the user. Fix:
  support `where C (f a)` end to end (hidden slot, call-site injection, dispatch), or reject it
  at the declaration with a located message instead of an internal error.
- [ ] `P3` **An instance signature rejected for effect direction gets the generic message.**
  `sig_against_class` reports every rejection with `sig_mismatch_msg`, which never names the
  effect. Class `ap(k: t, g: Int -> Int !{IO}) -> Int -> Int`, instance
  `ap(k: Box, g: q) -> q = g` is rightly rejected (the IO callback returns as pure), but the
  message prints `(Box, q) -> q`, which §8.5 allows as more general. Fix: when `arrows_flow`
  fails, say which arrow lets IO into a pure slot. Review run 1791122795-32761.
- [ ] `P3` **A deferred constraint at an applied variable (`$f Int`) gets no slot.**
  `resolve_precise_head` gives a bare variable a hole (`hole_tdict`) but nothing to an applied
  one, so the next same-class dict shifts into its slot: `let g = \z -> both(ident(z), 5) in
  g(xs)`, with `ident(x: f a) -> f a` and `both where ToString a, ToString b`, is rejected
  ("`ToString a` resolved to `ToString Int`"). Master too. Fix: a hole there as well.
- [ ] `P3` **A deferred constraint whose variable becomes a function type panics at run time.**
  `(\z -> same(z, z))(\n -> n + 1)` with `same where Eq a` compiles to the poison thunk; spec
  §8.5 says it is rejected with "No instance of C for a function type". Neither
  `constraint_var_dict` nor the forward fills the hole, and `resolve` skips a `_` head. Master
  printed `true`. A class method's dispatch variable (`(\g -> to_string(g))(\n -> n + 1)`) is
  rejected, but as "ambiguous type variable … Annotate the expression"; master said "No instance
  of C for a function type" or printed garbage. Fix: report a hole whose variable resolved to a
  function type.
- [ ] `P2` **A lambda let-bound outside an arm, applied to its existential, panics at run time.**
  `let g = \z -> two(z, 9) in match s with | Shown v -> g(v)` compiles to the poison thunk: the
  post-pass settles `g`'s body under the declaration's env, and the arm's given
  (`@fwd:<skolem>`) is seeded per branch only. Master printed a wrong answer. The skolem escapes
  its arm through `g`'s type, so this should be a compile error. Defining `g` inside the arm works.
- [ ] `P3` **In a recursive group, a parameter another member pins is called generic.** In
  `fn a_fn(n: Int, x) = … Just(x) == Just(x) … b_fn(n - 1, x)`, `b_fn`'s `y == 3` makes `x` an
  `Int`, yet `resolve` asks for an annotation and a `where`. Each member's post-pass and
  placeholder naming read its own substitution (`infer.sprout`, per-member `s2`), not the
  group's. Declaring `b_fn` first compiles. Master compiled it to the poison thunk.
- [ ] `P3` **§8.5's instance rules skip a class with more than one parameter, unstated.**
  `own_class_sigs` keeps only one-parameter classes, so an instance of `class Show2 t u` is neither
  seeded nor signature-checked. Its bare `b` in `instance Show2 (Box a) Int where ToString a` gets
  the `fn` remedy ("write that type out … add a `where`"), and with `b: Box a` the call fails as
  "No instance of Show2". The spec states neither limit. Fix: reject a multi-parameter class at its
  declaration, or say in §8.5 what holds for one.
- [ ] `P3` **A `where` headed by a type alias is keyed by the alias.** `type alias M a = Maybe
  a` with `fn f(y: M a, z: M a) -> Bool where Eq (M a) = y == z` is rejected ("add `where Eq
  a`"): `resolve.add_eff_keys` keys it `Eq_M` from the written head while the use site looks up
  `Eq_Maybe`. An alias below the head (`Eq (List (Opt a))`) works. Master failed with an
  internal under-application error. Fix: expand the head before building the key.
- [ ] `P2` **Wire in the dead `assert_resolved_typed_expr` soundness pass.** `infer.sprout` has a
  pass flagging free TVars in the final typed AST that is **never called**. **Investigate first
  whether it catches this class:** the record dispatch bugs poisoned the *injected dict evidence*
  while the final node type may already be concretized after the declaration-boundary `apply_subst`,
  so a node-type-only check may miss them. If confirmed — or extended to check each injected
  `TDict`'s constraint head against its resolved argument type, which overlaps phase 2b above —
  wire it behind a debug/CI flag so this class fails at compile time rather than at the runtime
  poison backstop.
- [ ] `P2` **Pattern-variable names share the fresh-tyvar namespace.** Pattern-bound variable names
  and the inferencer's fresh `t0`/`t1`/… names are drawn from the same namespace with no collision
  guard, so a pattern binding whose name collides with a fresh tyvar could shadow, or be shadowed
  by, the wrong entity during unification. A static finding from the fundamentals review, **not yet
  exercised at runtime**. Needs a minimal repro to confirm reachability, then either a namespace
  separator (reserve a prefix for fresh tyvars, distinct from any user-writable name) or a rename
  pass before pattern binding.
- [ ] `P3` **A bare class-method mention with nothing to fix its instance reports a compiler
  internal.** `fn pick() = to_string` emits
  `ast_to_ir: unbound variable '__eta_unresolved_ToString_to_string'` with no source position,
  preceded by `warning: alloc-summary pre-pass failed`. Loud rather than silent, so cosmetic in
  severity, but the text names an internal symbol and points at no line — while every sibling
  shape produces a proper located diagnostic (`fn f(x: a) = to_string(x)` reports the
  ambiguous-type-variable error; the constrained-user-function equivalents go through the
  uncovered-dictionary check). The eta-expansion path should reject with the same ambiguity message
  the applied form uses, at the mention's position.
- [ ] `P3` **Records are invisible to CPR.** `build_adt_ctor_index_go` matches `ast.TypeDecl` and
  drops `ast.RecordDecl` into its skip arm, so a record type gets no `adt_index` entry and a
  function returning a record is never given an unboxed worker however scalar its fields. Costs
  nothing measured today — the motivating hot path emitted no worker before *or* after. Worth
  fixing because the asymmetry is invisible at the call site and silently *caps* an optimization
  rather than breaking anything, and because "the result type has no ADT ctors" has now bitten from
  two type forms (`docs/scalar-replacement-v0.md` Stage 1 special-cased tuple results for the same
  reason). A record is a single-constructor product in the same `CtorInfo` table, so indexing
  `RecordDecl` alongside `TypeDecl` is plausibly the whole fix; check the tuple-shaped catch-all
  hazard first and gate on `just ir-golden-diff`.
- [ ] `P3` **String templates lower to more allocations than the `++` chain they replace; make the
  choice moot.** A backtick template allocates n+2 objects against `++`'s n−1 at every size, but
  buys back linear copying, so it loses below a ~1–2 KB result and wins above ~3 KB. Filed rather
  than fixed: not a measured bottleneck, and the real payoff is deleting the guidance burden so
  "choose on readability" becomes the whole rule. `docs/string-building-v0.md` §10 is the authority
  and holds the measurements, the lowering options (§10.3 Variant 2, type-directed on provable
  bounds, is the recommended one) and the blockers in §10.5 — a prior-art survey of how
  C#/Java/Kotlin/Scala choose an interpolation lowering, and builtin approval for the runtime
  option.
- [ ] `P3` **Write the ADT-vs-record concretization invariant into `docs/compiler-internals.md`.**
  An ADT constructor-application node is born concretely typed (`Box Int`), while a
  record-construction node carries `Box $a` with the binding living only in the substitution — so
  any dispatch or resolution site reading the raw node type instead of applying the resolved
  substitution silently works for ADTs and breaks for records. Three sites hit this; the invariant
  is "resolve constraint/dispatch argument types via `apply_subst(s3, …)`, not `typed_expr_type`
  alone", and a written rule stops the next one at review time.
