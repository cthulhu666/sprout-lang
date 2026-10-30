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
- [ ] `P3` **`RecordDecl` is still a `group_plan` barrier, for no remaining reason.** Records are
  registered before the walk now (`pre_scan_fn_decls`), so a `FnDecl` may cross one. Kept as
  a barrier because dropping it changes check order, and a record's index must still land in the
  plan exactly once. `docs/binding-group-inference-v0.md` §Barriers.
- [ ] `P3` **A `with` update on a receiver whose head is still a variable is rejected.** `let b =
  pure(3)` then `b with (val = 4)`, with `f` pinned to `Box` only by a later use, reports "no
  field `val` on `Unknown`": `record_head_name` turns an open head into the placeholder
  `"Unknown"`. A field READ of the same shape waits for the final substitution
  (`record_receiver`); an update has no such deferral. At least the message should not name a
  type that does not exist.
- [ ] `P3` **`head_name_matches` suffix-matches the final dotted segment** (`infer.sprout:1917`), so
  `where Sh (Box a)` binds to another module's same-named type — two modules defining `Box` are
  indistinguishable and the scan takes whichever argument comes first. Fix: qualify the head at
  canonicalization time so the comparison is exact.
- [ ] `P3` **Full Maranget usefulness matrix for product exhaustiveness.**
  `(true,true)|(false,false)` on `(Bool,Bool)` is not yet rejected; sound to over-accept per spec
  §5.5.

**Bindings, patterns and surface syntax**

- [ ] `P2` **`try` + `Propagate`: migrate off the fallible `<-`.** `try` landed (spec §5.9.1).
  Open: a phase that lists propagating binds (step 1a), codemod A adding `try` in
  all four repos, the flip that makes `<-` effect-only with the discard rule, codemod B, and `let`
  purity (steps 2–5 of `docs/try-propagate-v0.md` §8).
- [~] `P2` **Binding-level type annotations `let x : T = e`.** Phase 1 (top-level `let`) landed
  2026-07-30. `docs/binding-annotations-v0.md`; spec §5.2.
  - [ ] `P2` **Phase 2 — `let…in` and `where` annotations.** Those bindings are desugared and
    have no dedicated AST node to hang an annotation on, so it is materially more invasive than the
    `LetDecl` field. Specify separately (`docs/binding-annotations-v0.md` §5).
  - [ ] `P3` **do-block `let`/`<-` step annotations.** `DoLetStep` has `LetDecl`'s shape and could
    carry a `Maybe TypeExpr` the same way; deferred with Phase 2, which shares the surface.
  - [ ] `P2` **Anonymous `any C` introduction — `let row : List (any C) = …`.** Needs a
    type-directed rewrite boxing each element into a per-value dictionary, so it cannot ride the
    Phase-1 syntactic coercion. Belongs to the existentials arc (`docs/gadts-v0.md` §6).
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
  are in `docs/gadts-v0.md` (non-normative), spec §5.6.
- [ ] `P4` **Existentials — extend constraint-head class validation to instance/class positions.**
  A variable-first `where a ToString` is rejected on a `FnDecl`; the same swap on an `InstanceDecl`
  context-constraint or a `ClassDecl` superclass silently drops the constraint.
- [ ] `P4` **Existentials — ambiguous construction gives an opaque arity error.** `Bag([])` leaves
  the constraint's var free, so no witness is injected and it surfaces as
  `ctor application has wrong arity`. It fails loudly, but the message should be a located "cannot
  determine which `C` instance to pack". Unique to compound-field existentials.
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
- [ ] `P3` **Reject a user declaration that shadows an intercepted compiler intrinsic.** A
  headerless file defining `fn to_double`/`fn double_to_bits` has its call sites
  silently lowered to the intrinsic, body ignored, no error at any stage. A parameter or local with
  such a name is not affected: it wins. `docs/bitwise-int-ops-v0.md` §8.1.
- [ ] `P3` **Names the parser writes are captured by a module's own declaration or a local.**
  List and dict literals, list patterns and `>>`/`<<` use bare `Cons`, `Nil`, `dict_empty`,
  `dict_set`, `rcompose`, `lcompose` (spec §3.1; `own_cons_captures_list_literal.spr`). Writing
  `prelude.Cons` instead breaks `no_prelude` files and `compiler.compile_source`, which type-checks
  without bundling. That path also neither resolves a user's `prelude.X` nor renames locals, so a
  local still captures templates there. `docs/prelude-name-identity-v0.md` §5.
- [ ] `P3` **Register classes in `pre_scan_fn_decls`, retiring `group_plan`'s `AtModuleStart`.**
  A class is checked at its module's start because its methods once kept bare names and would
  have shadowed the prelude if registered earlier. Methods are now canonical
  (`docs/qualified-class-methods-v0.md` §7, stage 2), so that reason is gone. Moving it changes
  check order, so it is its own change with its own golden diff.
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
  Recorded in `spec-v0.md` §5.6.4. It also lets any module copy an abstract linear record's
  untracked field (`docs/linear-publish-v0.md` §5.3).
- [ ] `P3` **`LinVec`'s module is trusted, not checked, not to alias its `Vector`.** Inside
  `stdlib.linvec`, a raw `Vector` unwrapped from a borrowed `LinVec` can be returned, captured or
  stored, and one from an owned `LinVec` can be duplicated as `(raw, LinVec(raw))`. Both alias
  what `linvec.set` writes in place. Only a comment guards it. A rule for borrowed binders alone
  was declined: it leaves the owned case and `id(raw)` open, and Sprout has no `Copy` or `copy`
  to exempt harmless fields. Revisit with a mechanism that covers both cases
  (`docs/linear-publish-v0.md` §7).
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

- [ ] `P2` **Scalar-replacement follow-ups.** Tuple-CPR and intra-function SRA landed;
  `docs/scalar-replacement-v0.md` Appendix B. Two separable extensions remain: **SRA beyond
  do-blocks** (extend to pure `let..in` and across a Maybe/Result do-bind, threading the SRA map
  through `translate_do_bind_*` instead of resetting it); **heap-field tuples**
  (`IRCallUnboxed{2,3}` slots holding heap values must be rooted at the call site, and `op_heap_def`
  must report multiple slots).
- [ ] `P2` **A literal inside a `Maybe` pattern loses CPR, so every read allocates.**
  `ast_to_ir.is_simple_width2_arm` admits only a variable or `_` under the constructor, so
  `match mutvec_get(c, p) with | Just false -> …` builds a `Just` per call, while `| Just b -> …`
  calls the unboxed worker. A 1M sieve: ~400 ms against 7. With `SPROUT_GC_THRESHOLD=50000000` it
  is 29–48 ms, so most of it is the count-blind trigger rescanning the live array. `Just false`
  is how a sieve over `LinVec` reads (`docs/persistent-array-v0.md` §3.4). Fix: bind the field to a
  fresh variable in the worker arm and test the literal after.
- [ ] `P3` **A captureless method closure is heap-allocated at every call, so `where Eq a` costs.**
  Passing a known instance's method, e.g. `list_member(name, scope)` at `Eq String`, emits
  `sprout_alloc_closure` for `@__sprout_ir_eta___tc_Eq_String_eq_*` each call, though it captures
  nothing; 650 of the compiler's 894 closure sites capture nothing. On 2026-10-17's master the
  bundler on prelude `list_member` peaks 247–252 vs 219–230 MB self-compiling, so it keeps a local
  `str_list_member`; static constants (hand-patched seed) bring it to 227–236 MB. On #652's first
  commit the same patch recovered nothing, and on master as is it gains nothing, so the win is
  unstable. Fix: emit captureless closures as static constants, as string literals are.
- [ ] `P3` **The CPR worker set is not closed over tail calls, so a wrapper's worker can box.**
  `scan_workers` admits only callees some `match` scrutinises. A wrapper whose tail
  calls a function nothing else matches gets a worker, but `translate_tail_chain` finds no callee
  worker and falls to `translate_tail_catchall`: box, then repack. Seen in
  `lexer.tokenize_source_worker` (golden `11_compiler_bundle`), where it costs nothing, since
  `tokenize`'s own arms box too. Fix: add tail-call targets of workerized fns to the set, to a
  fixed point. Docs claim the workers compose; that holds only when the callee is matched.
- [ ] `P3` **A `Maybe` returned by a lambda is boxed: CPR does not reach a closure call.**
  `list_filter_map(\n -> bvec_index(n, len), ns)` allocates a `Just` per element, where direct
  recursion over `match bvec_index(n, len)` allocates none: BFS w = 1,000 runs 415 against 335 ms
  (`docs/persistent-array-v0.md` §5.7). Every `Maybe`-returning lambda passed to a combinator
  pays this. Fix candidates: specialise a combinator at a known lambda argument, or inline small
  combinators before the CPR router runs.
- [ ] `P3` **Argument CPR reaches only calls `translate_direct_call` lowers.** A consumer call as a
  `match` scrutinee (`match maybe_or_else(vec_get(i, v), fb) with`), in a worker's tail chain, or
  as a routed producer's argument goes through `translate_args`, so its argument is boxed; and
  `scan_workers` still emits an `@f_argw` for such a site, which nothing calls. Fix: a
  worker unboxed on both sides, `@f_argw_worker`. `docs/arg-cpr-v0.md` §2.
- [ ] `P3` **A recursive consumer's own loop boxes its argument.** In
  `count(n, m) = match m with | Just x -> count(n + 1, step(x)) | …` the self-call stays a call to
  the boxed `count`, so each iteration builds a `Just`: routing it to `@count_argw` would hide the
  loop from `tco_rewrite`, which keys on the callee's name. Only calls from outside the group
  route. Fix: self-TCO on `@f_argw`, then route the group's own calls. `docs/arg-cpr-v0.md` §2.
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
- [ ] `P3` **The may-trigger-GC fixpoint is quadratic in call depth.**
  `ir_rooting.fixpoint_iterate` is Jacobi (a round reads only the last round's map), so a fact
  moves one call per round: a chain whose last link allocates costs 745k / 955k / 1.39M per link
  as it grows, 0.2% of self-compile allocation (2026-10-05). Fix: walk
  `scc.sccs_in_dependency_order` callees first, as mutual TCO Phase A now does. Phase A is linear,
  so `scripts/scc_cost_gate.sh` no longer needs its fixtures to alternate arity to dodge it.
- [ ] `P3` **Noreturn paths in `ir_rooting` stop at the block that calls `panic`.** The may-trigger
  summary skips such a block (`ops_never_return`) but nothing reaches further. Open: (1) a block
  whose every successor is a noreturn block never returns either, so `br` to a panic arm
  qualifies; (2) a Sprout fn whose every path panics is itself noreturn, so its callers' blocks
  qualify; (3) `rewrite_ops` still roots values across triggers inside the panic block, which is
  dead weight there but needed for the call's own arguments, so only the unused ones can go.
  Each needs the `panic` premises re-checked, and (2) a fixpoint over the call graph.
- [ ] `P3` **A `Double` constructor field is rooted though it holds raw bits.** `type_kind` calls
  `Double` a scalar, but `type_kind.scalar_field_code` gives it no code, so its field encodes `"p"`
  and every `IRGetField` of it is rooted. Safe (the GC ignores a non-address root), only wasted
  pushes. Fix: a scalar code for `Double`; it changes the ctor metadata strings, so golden IR moves
  and `sprout_debug_adt` must decode the new byte.
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
- [ ] `P3` **`IRTScalar` witness `tuple_slot` is a free constant, unchecked in `type_kind`.**
  `type_kind.tuple_slot` is safe only because `ast_to_ir.scalar_tuple_width` checked every slot
  first; a new unboxing path can use it without that check. Fix: `type_kind` takes the field types
  and returns the witness, and `scalar_tuple_width` carries it to `emit_tuple_worker_call`,
  `translate_unboxed_tuple_match` and `translate_tail_tuple_repack`. `ctor_tag` has no premise and
  stays a constant.
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
- [ ] `P3` **Runtime-function facts are hand-written, unchecked name lists.**
  `is_nonallocating_read`, `is_nonallocating_unboxed_read`, `is_noreturn_extern`,
  `unboxed_maybe_extern_name`, `safe_io_externs` and five `op_triggers_gc` arms are claims about C
  bodies that nothing checks, and short: rooting trusts 13 runtime functions where 45 cannot
  trigger GC. Trusting the rest measured ~10% on `json.parse`, nothing on the compiler. Plan: one
  hand-edited facts module, and a check that derives the facts from the runtime's LLVM IR and
  fails on any entry the C does not support, per platform. Rejected: generating the module.
  `docs/runtime-facts-v0.md`.
- [ ] `P3` **GC hardening follow-ups from the phase-2 review.** HDRCHECK per-kind layout checks —
  the kind-independent region-walk validation landed 2026-09-26 (boundaries must agree with the
  slotmap) and VECTOR got an aux-vs-`->data` check with it, leaving 7 of 10 kinds with none;
  `is_large` uniformization (~9 branch sites — do it before the generational step multiplies them);
  single ownership of the "keep ≥ 1 normal region" invariant; close the OBJ tag-write window by
  passing the tag into the alloc path.
- [ ] `P3` **Sweep pass fusion** — pass 3 re-walks every region calling `slot_bytes` per live
  slot; fusible into pass 1 if freelist entries from released regions are handled. Only worth it if
  gc-profile shows sweep dominated by the extra pass.

**Strings and data representation**

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
- [ ] `P2` **A second `type T` in one module is accepted silently.** `type T = | A` then
  `type T = | B` passes `--phase check` with no error, while a second `fn f` is rejected with
  "is defined more than once in this module" (`infer.check_duplicate_module_values`). Which `T`
  the rest of the module sees is not something a user can know. That check covers `fn`, `let`
  and class-method names only; extend it to type, alias and class declarations, each in its own
  namespace.
- [ ] `P2` **A private prelude function is callable from user code.** `list_length` is declared
  without `export` in the prelude, yet `list_length([1, 2, 3])` compiles in a user module. Spec
  §Imports rejects listing an unexported name, so the prelude skips the export rule every other
  module obeys, and each private helper is public API by accident. Found by the LeetCode survey
  (`docs/leetcode-survey-2026-10-27.md` §3).
- [ ] `P2` **`match u with | () -> …` fails with no source location.** `ast_to_ir` returns
  "UnitPattern in match not yet supported" with no position, against guideline §5, so the user
  has nothing to search for. Either lower a unit pattern (it always matches) or reject it in the
  checker with a located error.
- [ ] `P3` **No `instance Ord Char`, though `<` accepts `Char`.** The spec lets the comparison
  operators take `Char`, but the prelude's `Ord` instances skip it, so `sort` on a `Vec Char`
  does not compile. Sorting a word's letters goes through codepoints instead. Add the instance.
- [ ] `P3` **No total Int remainder.** There is no `%`, and `stdlib.math.int.mod` returns
  `Maybe`, so `i - (i / m) * m` is the idiom (LeetCode survey: 3 solutions plus every test-data
  generator). Decide between an operator, behaving as `/` does at zero, and a total named function.

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
  **Caveats:** `ir_rooting`'s noreturn-block exemption assumes nothing resumes after a panic and
  must go with this, as must the `never_returns` facts (`docs/runtime-facts-v0.md` §8). GC
  type-aware rooting must be settled on unwound frames, and a task that panics mid-mutation of
  shared state can leave it inconsistent — the safe contract is terminate + notify, never
  resume-in-place.
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
- [ ] `P3` **`net.listen_on` binds IPv4 only; IPv6 is not supported yet.** An IPv6 literal is
  `Err TcpInvalidArgument` saying so (`tcp_listen` in `runtime/sprout_runtime.c`). Deferred, not
  refused: nothing needs it yet. The open decision is `IPV6_V6ONLY` for `::`: off (dual-stack) by
  default on macOS, the `net.ipv6.bindv6only` sysctl on Linux, so the runtime must set it. 1 keeps
  "what you write is what you bind"; 0 makes one `::` listener serve IPv4 too. Adding IPv6 later
  breaks no caller.

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
- [ ] `P2` **`Dict` order is relied on but never promised; split out `SortedDict`.** Every
  `dict_keys`/`dict_entries`/`set_to_list` returns ascending keys only because the runtime is a
  tree; 62 compiler calls read it, unmarked. Proposal: `SortedDict`/`SortedSet` promise the order,
  `Dict`/`Set` do not, and a scrambled-order test mode finds callers that rely on it. Stage 1 (add
  the types) is safe now; stage 2 (migrate) goes with `Dict k v`, since a hash `Dict` alone would
  save ~4–5% of a self-compile (`docs/dict-representation-v0.md` §6). `docs/sorted-dict-v0.md`.
- [ ] `P3` **No immutable `Matrix a`.** Only `MutMatrix` exists (`stdlib.mutable`), and it is
  `!{IO}` throughout, so pure code holds a grid as `List (List a)`, which can be ragged:
  `list.transpose` must skip missing cells and so does not round-trip. Wanted: a module
  `stdlib.matrix` with an abstract `Matrix a` (rows, cols, flat `Vec a`), `from_rows` returning
  `Maybe`, a total `transpose` that round-trips, and conversion to and from `MutMatrix`. Trigger: a
  second grid caller beyond LeetCode p0054.
- [ ] `P2` **Prelude O(n²) audit.** Several helpers are quadratic via naive list-append recursion
  and mostly undocumented: `ToString` for `List`/`Vec`/`Dict`, `list_dedup`,
  `Semigroup (Dict v)`, and several `vec_*` (`map`/`filter`/`filter_map`/`reverse`/`slice`).
  Document true complexity inline or fix to linear.
  Findings and probes: `docs/fundamentals-code-review-handoff-2026-07-03.md`.
  `list_builder_*` fixes the list-append half: O(1) per add, one reverse at the end.
- [ ] `P3` **Three stdlib loops build a `Vec` with `vec_append`, O(n²).** `vec_append` copies its
  vector (O(n), `prelude.sprout`), and each loop calls it once per element:
  `template.parse_seq` (via `dispatch_step`), `compiler.map_diagnostics` and
  `repl.qualify_names`. The last two are an index loop doing `vec_map`; the first wants a list or
  `ListBuilder` accumulator. Inputs are small today (template nodes, diagnostics, module names).
- [ ] `P3` **`Dict` and `Set` box their map, so every update allocates a wrapper.** Both are
  one-constructor `type`s over the runtime `Map` (`prelude.sprout`), so `dict_set`, `set_insert`
  and `set_remove` each allocate a 12-byte box (`sprout_alloc_obj(12, 1)`) around the new tree,
  and every read unwraps one. `wrap Dict v = Map v` and `wrap Set = Map Unit` would make them the
  tree itself (`ListBuilder` is already a `wrap`). Measure on a self-compile before and after.

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
- [ ] `P2` **Retire `prelude.split_on_char`, a private re-implementation of `split`.** It predates
  `stdlib.string.split`. Check two things: the shared one is O(bytes × |sep|) where the
  `split_once`-recursion is quadratic; and it **keeps** empty segments, so a caller relying on the
  private version dropping them needs a `filter` (`path.split` is the worked example).
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
- [ ] `P3` **Deferred non-goal from `docs/min-max-combinators-v0.md` §2**, waiting on a caller:
  comparator-taking `min_with`/`max_with` — there is no `sort_with` either, so comparator forms
  should arrive as a set.
- [ ] `P3` **Deferred non-goals from `docs/collection-combinators-v0.md` §7 and
  `docs/filterable-v0.md` §7.** (a) `length`/`is_empty` over `Foldable` — a fold-derived version
  is O(n) *even on `Vec`*, so free functions would put two spellings of one question at different
  complexities in one prelude; doing it right needs class methods with per-instance overrides, which
  Sprout cannot soften with default bodies (`parse_class_body` collects signatures only), so both
  become mandatory for every future instance. Note `list_length` is private, so List has no public
  length and `count(\_ -> true, xs)` is the O(n) stand-in. (c) `position`/`index_of`
  (List-shaped, not `Foldable`-derivable). (d) a `Set` `Filterable`, gated on `Set` getting
  `Functor`/`Foldable` (`Dict` has `Filterable`). (e) An effectful predicate (`a -> Bool !{e}`)
  needs `Filterable` to state `pred`'s call order, not B2 (`docs/effect-polymorphism-policy-v0.md`).
- [ ] `P3` **No `NonEmpty a`, though `docs/guidelines.md` §3 names it as the illegal-states
  exemplar.** Four in-tree sites pay for the gap: two panics in `infer.build_comp_level` and
  `infer.comp_fold_name` (a comprehension always has ≥1 generator, so `ComprehensionExpr` should
  carry one), and two dead arms in `tui.text.cluster_width`/`cluster_is`, whose own comment says
  "each a non-empty list of codepoints". uncharted-suns pays two more panics (`game.sim.slot0` and
  `game.sim.zone0`, the slot and zone rosters) plus `grimward.gear.slot_of`, pinned by
  `test_gear.spr` rather than by the type. Design: `docs/non-empty-v0.md` (proposal); the list
  toolkit's `scanl`/`group_by` signatures wait on it.
- [ ] `P3` **Primitive sort builtins for `Int`/`Double` vectors — needs approval first.** A C radix
  sort runs at ~2 ns/element against `mutvec_sort`'s ~190 (n=440). Safe for the collector, and
  `stdlib.sort` could call it on its copy. Open: how generic code
  reaches it — separate `_ints` names, or specialisation. Deferred until a caller needs it
  (`docs/sorting-v0.md` §Options).
- [ ] `P3` **Top-k selection without a full sort.** uncharted's `loam/lights.sprout` `reaching`
  sorts every light in range, then takes `keep`, per chunk and per mesh every frame. A
  `take_smallest_by(k, key, xs)`-style helper keeps k items in O(n log k). Grep the spec for the
  name first. `docs/laziness-v0.md` §5.3.
- [ ] `P2` **List toolkit, rest: `scanl`, LeetCode migration.**
  Frequent gaps in the survey (`docs/leetcode-survey-2026-10-27.md` §2). Design:
  `docs/list-toolkit-v0.md` §9 steps 3–4; `scanl` waits on `NonEmpty`. `enumerate` deferred to
  laziness.
- [ ] `P2` **`Dict`/`Set` take only `String` keys.** They sit on runtime externs
  (`map_get(m, key: String)`), so Int keys cost an `int_to_string` per access and `dict_entries`
  orders `"10"` before `"9"`; 6 of 31 survey problems paid for it. A pure-Sprout
  `Map k v where Ord k` needs no builtin; measure it against the C map first. The counting helpers
  (`dict_update`, `dict_get_or`, `frequencies`, `group_by`) belong with it.
- [ ] `P3` **No trapping `Vec` index read.** Index-heavy code writes `vec_get_or(i, 0, v)` with a
  meaningless fallback (7 of 31 survey problems). `mutvec_at` traps on a bad index; a `vec_at`
  would mirror it. Needs a ruling against guideline §2: is an out-of-range index our bug or caller
  input?
- [ ] `P3` **No fast pure table whose entries read earlier entries.** Coin Change's DP built by a
  `vec_append` fold is quadratic (4.7 s at 1e5); only `LinVec` with explicit recursion is fast,
  and `range_fold` has no `where linear b`, so it cannot drive one. Options: add the bound to
  `range_fold`, or a `vec_build(n, f)` builder.

### 6) Modules and Packaging

- [ ] `P1` **Package/dependency conventions for third-party modules.** Direction recorded
  (non-normative, per-phase approval pending) in `docs/packaging-v0.md`: strict compile-time
  coherence with the orphan rule extended across packages, single-version selection with a loud
  resolver, incompatible majors as distinct explicit package identity (Go `/v2`-style), git sourcing
  reusing the `.iface`/`.bc` cache, graph-wide compiler-version unification. Package-qualified
  identity is the spine — it generalizes module-qualified type identity and dissolves the "dotted
  non-`stdlib.` import resolves to `Nothing`" gap as its degenerate single-package case, subsuming
  the `examples.*` item below. Phased plan in §10, semantics before mechanics.
- [ ] `P2` **Module resolution guesses one path and never probes.**
  `module_loader.resolve_module_path` is pure, so it consults only the FIRST package root, and the
  prefixless form stops at depth 1: `import math` works, `import math.int` is read as a
  package-root path (it now fails with a `did you mean stdlib.math.int?` hint). A missing file is
  an error at the import line, so the guess is no longer silent. Fix: return candidate paths, probe
  them in the `!{IO}` loader, and hand the chosen path to `apply_one_import` so the two agree.
  Open: does the stdlib root outrank package roots?
- [ ] `P3` **A malformed import line is dropped without a word.** `import` with no module name, or
  `import X as` with no alias, makes `module_loader.parse_import_line` return `Nothing`;
  `collect_imports_from_lines` skips it and `strip_headers` hides the line from the parser, so
  nothing reports it. The walker knows the line's position, so it can become a load issue.
- [ ] `P2` **Dedup `extern fn` declarations in the bundler.** The typed AST reaching
  `ast_to_ir.translate_program` holds the same `TExternFnDecl` once per importing module. The IR
  path defends with a `seen: Set` in `lower_extern_decls_loop`; the fix belongs in `bundler.sprout`
  — collapse same-name `ExternFnDecl` nodes into one canonical decl, checking signatures match
  across importers.
- [ ] `P2` **The stage-1 module loader does not resolve `examples.*` imports** (only `stdlib.*`), so
  a multi-file example that imports a sibling fails to link, and library-style example modules with
  no `fn main` fail at the entry point. Both are in `XFAIL_EXAMPLES`; the fix needs a design
  decision on cross-example module resolution (subsumed by packaging above).
- [ ] `P2` **An error inside an imported file has no position.** `issue_pos_for_entry` blanks any
  pos outside the entry file, so an import or parse error in a dependency reports line 0 and names
  the file only in the message text. An `ImportSpec` now carries its line; the entry's own import
  errors point at it. Needs a diagnostic that can name a file other than the one being checked.
- [ ] `P3` **`stdlib.collections` is a 7-line module exporting one function**, and after the import
  diagnostics deleted the stale entries no import line in the tree binds anything from it — its
  `vec_singleton` is a duplicate of the prelude's. Decide: grow it into the real collections surface
  and move the prelude's `vec_*` there, or fold it away. **Do not simply delete it** — the
  duplicate name is load-bearing as a fixture: `module_loader.sprout:303` picks `own_pairs` by
  declared-name selection rather than key-exclusion *specifically* because of it, it is the only
  in-repo case exercising that path, and `test_compiler.spr:123-142` is an explicit regression for a
  bug that has already happened.

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
- [ ] `P3` **Fold a file's lines with an early stop.** `stdlib.fs` reads only whole files, so
  uncharted's starmap reads a ~600 KB tile and splits all of it to find one row, and starcat keeps
  folding after `node_cap`. Wanted: a `fold_while`-shaped line fold whose effect row sits on the
  fold. Open: chunked reads, or the whole file read first — that decides most of the value.
  `docs/laziness-v0.md` §5.2, §6.4.

### 7) Tooling and Developer UX

**Formatter and linter**

- [ ] `P3` **`fmt` drops the space between two adjacent parenthesized type atoms.**
  `Widget s (s -> s) (s -> String)` becomes `… (s -> s)(s -> String)`, which reads as application
  rather than two fields. Same family as the landed `[` fix: `needs_space_word_or_op` has no case
  for `)` followed by `(`, so the `is_word_like` fallback returns false. Confirm no legitimate
  no-space case exists in a *type* position, then add a formatter regression. A word before one
  loses it too: `type alias Forwarded = Dict (List T)` becomes `Dict(List T)` (resolve.sprout).
- [ ] `P3` **`fmt` drops the space between a keyword and a lambda.** `if b then \x -> x + 1 else
  \x -> x + 2` becomes `if b then\x -> x + 1 else\x -> x + 2`. It still parses and compiles, so it
  is cosmetic, but no one writes it, and `tests/closure_arity_smoke/over_apply_static.spr` moved its
  lambdas into separate functions to avoid it. Acceptance: a keyword before `\` keeps its space.
- [ ] `P3` **`fmt` inserts a space into chained application when the argument starts with an
  uppercase identifier.** `pick_color()(Red)` becomes `pick_color() (Red)` while `pick_int()(4)` and
  `labeller_for(1)(2)` are untouched — discriminated by probe, so the trigger is the leading
  identifier's CASE, not the type or literalness. Cosmetic, but it is inconsistent output from the
  tool `fmt-check` gates on. Acceptance: `f()(X)` and `f()(x)` format identically.
- [ ] `P3` **Formatter inconsistency on nested constructor spacing.** A constructor call whose
  argument starts with an uppercase identifier gains a space when it is itself a call argument:
  `b(Just(Green))` becomes `b(Just (Green))`, while a bare `Just(Green)` and `b(Just('z'))` are
  untouched. Not column-driven. Likely the same trigger as the chained-application entry above.
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
- [ ] `P3` **`nullary-const-fn` skips every lowercase-name body; narrow it once suppression lands.**
  `lint_rules.takes_type_from_context` exists because a `let` annotation could not name a
  non-prelude type; it now can, so `let pick: Colour -> String = label` compiles. The skip is
  also coarse: plain value reads (`fn v0() -> v.Version = v.origin`) pass it too. Dropping it takes
  `just lint` from 0 to 36 findings: 1 real (`analysis_service_driver.sprout`
  `sentinel_name`), 35 fixtures testing the nullary-`fn` shape itself. Blocked on the pragma
  above. Then narrow it, rewrite the real site, and mark the fixtures
  `# lint: allow(nullary-const-fn)`.
- [ ] `P3` **Lint a redundant to-string conversion inside a template slot.** A `${…}` slot already
  calls `ToString`, so `${int_to_string(n)}` and `${to_string(x)}` convert twice; `${n}` prints the
  same text. 102 sites today across `examples/`, `stdlib/`, `ide/` and `tests/` (47
  `int_to_string`, 54 `to_string`, 1 `double_to_string`). The linter is untyped, so derive the
  names from the prelude's `instance ToString T` bodies that only call `f(value)`, as
  `docs/lint-rules-v0.md` §5 derives patterns, and skip a name the module shadows. `to_string`
  itself is always redundant there. Rewriting a site changes golden IR, not output.
- [~] `P2` **Formatter/linter beyond the baseline.** Eight AST lint rules shipped
  (`staircase-of-doom`, `redundant-vec-from-list`, `list-shape-pattern`, `list-prefix-pattern`,
  `multi-line-lambda-arg`, `deprecated-brace-body`, `nullary-const-fn`, `hand-rolled-combinator`) on
  top of `formatter.sprout`'s text-based Style checks. **Remaining roadmap** from
  `docs/idiomatic-sprout.md`: "Match the producing call directly" (a `let`/do-bind immediately
  followed by a match on that single otherwise-unused variable) and "Collapse a trivial `do` block".
  The rest of that doc is design-level or too fuzzy for a reliable syntactic check. **Also open:**
  autocorrect (needs an AST-aware rewriter; today's formatter is a line-based text transform) and
  the config file, designed in `docs/lint-rules-v0.md` §8.
- [ ] `P3` **`hand-rolled-combinator` still declines every combinator over a call.** Guard 4
  (`docs/lint-rules-v0.md` §7) was right while a call's `Maybe` was boxed to reach any combinator.
  Since argument CPR (`docs/arg-cpr-v0.md`), `maybe_with_default`, `result_with_default`, `is_just`
  and `result_from_maybe` over a top-level call build no wrapper (bench/vec_box: 2.1 ns against
  1.9 for the `match`), so the rule can suggest them there. `map`, `and_then` and a lambda's
  result still box. A syntactic check cannot tell a top-level callee from a closure call, so the
  widened rule needs the local-binding environment §7 already describes.
- [ ] `P3` **A nested `match` on a `Cons`-bound tail is not linted.** `match xs with | Cons h t ->
  match t with …` is what `[a, b | rest]` exists to flatten, and neither `list-shape-pattern` (which
  needs a chain ending in a literal `Nil`) nor `list-prefix-pattern` (a wildcard tail) covers it.

**Gates and diagnostics**

- [ ] `P3` **14 stdlib modules have no section in `docs/stdlib-reference.md`.** `args`, `bits`,
  `chan`, `http_middleware`, `log`, `mutable`, `process`, `repl`, `rng`, `stamped`, `task`,
  `template`, `test`, `version` — listed as `UNDOCUMENTED` in
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
- [ ] `P3` **`just test-file` cannot compile a test that imports `testsupport.*`.** `_test-file`
  runs `--emit-ir` without `--package-root`, so the import does not resolve and the file fails to
  compile; `just test` passes it. Seven IR suites now import `testsupport.ir_fixtures` (e.g.
  `tests/stdlib/test_ir_rooting.spr`), so none of them runs through `test-file`. Fix: pass
  `--package-root "{{justfile_directory()}}"` there as the other emit-ir recipes do, and drop the
  caveat from AGENTS.md §How to run tests.
- [ ] `P3` **IR tests — the lex→parse→check→lower→compile helper is pasted into 13 suites.**
  `translate_source` is defined in 8 of the `tests/stdlib/test_ir_codegen_*.spr` files and 5 under
  `tests/stdlib/compiler/` (`test_ir_*.spr`, `test_scalar_global_no_root.spr`); 7 of them also
  define a local substring `count`. A change to the pipeline is made 13 times. Move it to
  `testsupport/ir_fixtures.sprout` as `ir_of_source`, flatten it with `let..else`, and import it.
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
  `pos_for_entry` the way `find_one_ctor_clash` does (#425). Found while fixing #422. A check
  error at an imported declaration has the same gap (`app_linear_publish_bad.spr` gives `5:8`).
- [ ] `P2` **REPL SIGSEGV on a tuple that nests let-bound tuple variables.**
  `let t1 = (1,3,"foo",true)` then `let t2 = (t1, t1)`, then evaluating `t2` gives
  `SIGSEGV (no current function set)`. Flat tuples are fine. **Not a codegen bug** — the
  equivalent compiled program renders correctly under both paths — so the fault is in the REPL's
  per-expression eval path (`analysis_service_driver.op_eval_expr_in_source`), most likely in the
  program it builds over prior session let-bindings (`compiler.compile_question_ir`). Next step:
  compile the session source with `fn main() -> Unit !{IO} = print(to_string(t2))` standalone.
- [ ] `P3` **REPL completion reads modules relative to the cwd.** `repl.module_path` builds
  `stdlib/<m>.sprout` or `a/b/c.sprout` from the current directory, so a REPL started outside the
  repo root panics on the first TAB (`prelude_completion_names`: "read_file: prelude"), while the
  other two reads (`globals_of_module`, `dotted_completion_candidates`) silently offer nothing.
  Fix: resolve through the same stdlib root and `--package-root` the compiler session uses.
- [ ] `P3` **A CRLF file reports every line number doubled.** `source.starts_line` says both `\r`
  and `\n` start a line, so an error on line 3 of a CRLF file is reported at 5:11. Columns are
  right. Fix: `\r` starts a line only when no `\n` follows it, which needs one byte of lookahead.
- [~] `P2` **Dispose of the obsolete negative corpora.** Entrypoint validation landed
  (`validate_entrypoint`): a defined `main` must be zero-arg, `Unit`/`Int`-returning and concretely
  `!{IO}`, gated via `test-executable-errors`. **Remaining:** the "missing main" check
  (`executable_error/missing_main`, xfailed) cannot be a type-check error — at check time the
  compiler cannot tell a library check from an executable build, and main-less library files are
  legitimate; it needs an explicit executable-vs-library compile mode threaded from
  `--emit-ir`/codegen, where `has_user_main` is already computed. Also decide separately whether to
  delete `runtime_error/main_arity_mismatch` (a mislabeled duplicate) and `parity_*` (byte-parity
  against the retired reference, no golden).

**Developer setup**

- [ ] `P3` **`setup-dev.sh` stops on x86_64 distros without clang 22.** It installs the distro
  clang, and on x86_64 clang <= 21 lowers `__int128` as `{ i64, i64 }`, so LLVM discovery and
  `bootstrap-from-seed` refuse it (`scripts/check_clang_cpr.sh`). Ubuntu 24.04 ships 18. Setup
  stops with a pointer to apt.llvm.org instead of adding that repository itself. Revisit when
  Ubuntu's default clang reaches 22: then the distro install works and this entry dies.

### 7.5) Type Classes

- [ ] `P2` **Default method bodies.** A class method is a signature only (spec §8.5), so adding a
  method breaks every user instance and a derivable one (`Foldable.fold_values`,
  `Filterable.filter_values`) is still mandatory. A sort on `Ord` and `length` over `Foldable`
  were both declined over this. Proposed: lift each default to
  one `where C t` function in the class's module, an omitted method forwards to it, a cycle of
  taken defaults is rejected at the instance. Awaiting approval: `docs/default-methods-v0.md`.
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
  `where Boxed (Tagged k), Boxed (Tagged j)` gets two hidden slots now —
  `typed_ast.dict_slot_key` names the arguments — but `infer.check_indistinct_constraints` still
  rejects it (spec "Two constraints of one class must not differ only in their arguments"). What
  is left: the eta fallback that takes the only slot carrying a method
  (`lowering.find_forwarded_method_any`) must pick by key.
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
  The two `tests/conformance/emit_error/ambiguous_forwarded_*.spr` shapes pass `--phase check`
  and then fail at emit with `ast_to_ir: unbound variable '__eta_unresolved_<Class>_<method>'` —
  a compiler-internal sentinel, no line, no column. So the LSP and every check-phase gate accept
  the program and the user learns at build time. Both are unresolvable identity (see below).
  Ambiguity detection belongs where constraints are known, not at slot-selection time. (The
  cross-class shape is now rejected at its declaration, `docs/qualified-class-methods-v0.md`.)
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
- [ ] `P3` **`to_string` does not quote strings inside a container.** `to_string(["", "a"])`
  prints `[, a]` and `to_string([[""]])` prints `[[]]`, so distinct values print alike.
  `ToString String` is the identity and `ToString (List a)` joins its elements' `to_string`; a
  container needs a quoting form for string elements. Found by the LeetCode survey.

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
  to the mechanism, not a handler defect: `scheme_of_expr_in_source` types the expression after
  the buffer's declarations, at **module scope**, where a local is not in scope, so the
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

- [ ] `P2` **Unbox small fixed-shape numeric records** (`Vec3 {x,y,z}` as 3 raw f64s, not a heap
  pointer; `stdlib.linalg.vec3`'s `Vec3` is the first target) — the ergonomic-and-fast path for
  individual small vectors, additive on top of the tested flat-buffer foundation.
- [ ] `P2` **Native `Float` (f32) type + `Vector Float` unboxed path.** Doubles-everywhere is
  correct today; float32 earns its keep only when Sprout owns bulk GPU-bound buffers. Decide by
  measured buffer/upload cost.
- [ ] `P3` **Remove the dead C `json_parse` tree-parser.** Extract the shared low-level helpers
  (`sprout_json_skip_ws`/`_parse_string`/`_parse_hex4`) still used by the by-key extractor, then
  delete `json_parse`/`_value`/`_array`/`_object`/`_number`/`_ok_result`/`_err_result`/`_reverse_*`
  and drop `json_parse` from `APPROVED_BUILTINS` and the `stdlib$json$*` ctors from
  `SPROUT_RUNTIME_CTORS`.
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
- [ ] `P2` **The CPR `_unboxed` externs have no Windows x64 ABI.** Emitted IR declares
  `i128 @X_unboxed` and gets it back in rax/rdx. MSVC returns a 16-byte struct through `sret`,
  clang returns `__int128` in XMM0 there, and `cl.exe` has no `__int128`, so no C form of
  `SproutUnboxed2` matches: a call returns garbage. Windows ARM64 under clang agrees (`i128`).
  Fix before W3 links the runtime: an out-pointer declare on Windows x64, or no CPR externs
  there. `docs/compiler-internals.md` §CPR extern ABI.
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
- [ ] `P3` **Tail recursion modulo constructor (TRMC).** A recursion that rebuilds a node around
  its own result (`Node(h, insert(rest))`) keeps a stack frame and two GC roots per node, even
  when reuse writes the node in place. A* under a FIFO tie-break walks 1.25M open-set nodes per
  run: the recursive insert took 13,300 µs/run, the same insert as two tail-recursive loops (walk
  onto an accumulator, then unwind) 4,600 µs, Go 1,510 (M1, 2026-10-24).
  `examples/astar_linvec.sprout` uses the loops. TRMC would compile the recursive form to one loop
  that writes the `next` field: one pass, and the natural form stops costing 2.9×.
- [ ] `P3` **Span the remaining `no_pos()` error sites: codegen / IR-emit errors (`IrLinesErr`).**
  Everything else is spanned (diagnostics, infer/typed/check/body errors, bundle errors, pattern
  inference, call resolve, the four `typecheck_decls` validators).

### Runtime and performance roadmap

- [ ] `P2` **A* benchmark ports break ties on equal `f` differently.** Sprout and Haskell pop
  the newer entry first, Go/JS/Ruby the older, Python the smaller `g`, Java whatever its heap
  yields. On this grid the rule changes the search 21× (Go: 198 nodes against 4,178), so the
  cross-language rows in `bench/astar/README.md` compare different searches. Fix: one rule in
  all 7 ports. Preferred shape (2026-10-24): a binary heap keyed on `(f, insertion order)`
  everywhere, so the rule is explicit and the benchmark measures A*, not a sorted-list scan.
- [ ] `P3` **Native-Sprout runtime, Phase 0: add the galaxy frame to the baseline.**
  `bench/native_runtime/bench.sh` (results: `bench/results-2026-10-18-native-runtime.md`) covers
  the sort pair, self-compile, nqueens, `gc_roots` and `http_log_middleware`, but not the
  `uncharted-suns` galaxy frame, which lives in another repo. Per-candidate Phase 3 port benches
  wait for Phase 2. Plan: `docs/native-sprout-runtime-v0.md`.
- [ ] `P3` **User externs can declare a C function pure.** Declaring an extern is unchecked trust:
  a module may state any signature when the canonical declaration is not in its build. A user file
  that declared `vector_mutset` returning `Unit` had its call deleted by DLE. `unsafe_pure` is
  gated by module name (`docs/unsafe-primitives-v0.md` §4.2); this older hole is unchanged. User
  code declares externs for real (uncharted-suns `loam/gfx.sprout`, sprout-sqlite's FFI), so a gate
  must tell FFI from a false signature: a checked marker, or an effect floor for user externs.
  Open: which, and what happens to the declarations that exist.
- [ ] `P2` **Make tight Sprout string-processing loops competitive with host builtins**, so moderate
  stdin/text workloads do not need dedicated host helpers to be practical. Investigate the native
  overhead in recursive stdlib string loops such as `string_lines` over stdin-loaded text — tail-
  recursive loop lowering, call/closure overhead, primitive boxing, string/vector iteration — and
  add stable benchmarks for `string_lines`, `trim` and AoC-style stdin parsing so wins are
  measurable. Target: `string_lines` over a `day5input`-style workload in low single-digit seconds.
- [ ] `P3` **Capturing closures at `local` slots allocate, and every apply re-checks arity.** A
  lambda passed to a `local` parameter cannot escape, so its env could live in the caller's frame,
  and an `IRApplyClosure` whose arity is known could skip `sprout_closure_arity_check`. Both were
  hand-applied to `bench/gc_roots`: stack envs gained ~0, and deleting the check outright gained
  ~2%, although it shows as ~9% of top-of-stack samples (`docs/static-closures-v0.md` §7). Worth
  revisiting only if inlining lets LLVM see through the env, which the out-of-line check blocks.
- [ ] `P3` **One eta wrapper per reference site.** Each use of a top-level function as a value
  mints its own `__sprout_ir_eta_<fn>_<n>` wrapper (`ast_to_ir`, the eta-expansion branch of
  `translate_expr`), so a function passed in ten places is ten identical functions and, since
  static closures, ten constants. One wrapper per `(function, arity)` would do. Code size only.
- [ ] `P2` **Stronger server-side runtime models — multi-reactor as the next target.** Native TCP
  handle-slot reuse and the `stdlib.http_server` helper layer are the groundwork. Chunked reads are
  filed in §2.
- [ ] `P2` **Specialise generic functions at concrete types.** A `where Ord a` function takes the
  `Ord` and `Eq` dictionaries and makes an indirect `compare` per comparison, so `mutvec_sort` on
  `Int` runs ~2× slower than a hand-specialised copy. Specialisation would also let `sort` at
  `Int` reach a primitive sort under its own name. Numbers:
  `docs/sorting-v0.md`. Related: §5 B4, §7.5 "Deriving/specialization follow-ups", and the
  "specialize per instantiated head" lever in the withdrawn bare-tyvar CPR entry. Proposal:
  `docs/specialisation-v0.md`.
- [ ] `P3` **Specialise context-instance bodies, so tuple keys compare directly.** `Ord (a, b, c,
  d, e) where Ord a, …` reaches each field through a context dictionary, and
  `docs/specialisation-v0.md` §4 *Limits* does not copy instance bodies. A heap keyed on a 5-tuple
  of `Int` makes ~2.5× the indirect calls of one keyed on a type that derives `Ord` (19.2M vs 7.8M
  `closure_apply` per 100 A* runs) and runs ~2.5× slower (`docs/linheap-v0.md` §5). After
  *Specialise generic functions at concrete types*: copying an instance at concrete context types
  would make the tuple key as cheap as the derived one.
- [ ] `P2` **Calls to Sprout functions root their operands, though the callee roots what it
  needs.** `op_exposes_operands` (`ir_rooting.sprout`) roots every call's heap operands, for
  runtime builtins that allocate before reading them; for a call to a Sprout function the pushes
  look redundant, and they survive LLVM inlining, so small wrappers cost real time. Narrowing the
  rule needs a written GC-safety argument first, since a missing root corrupts the heap.
  Evidence: `docs/sorting-v0.md` §Where the time goes, item 2, and option F.
  The root also keeps a persistent array's whole history alive: `docs/persistent-array-v0.md` §3.
  Premise test: `tests/stdlib/test_gc_phi_of_params.spr` (a direct call's operand root covers a
  callee phi of parameters; a closure call and a mutual tail call have no such root).
- [ ] `P3` **`ir_verify` classifies heap values with `ir_rooting`'s own functions.**
  `verify_rooting` builds its heap set with `ir_rooting.compute_heap_origin`, so a wrong
  classification in the pass passes the verifier too: a phi of parameters was missed by both.
  Fix: an independent classifier in `ir_verify`, for example a fixpoint over phis seeded with the
  parameter kinds. Limit noted in `docs/gates.md` §Root verifier, "Does not check".
- [ ] `P3` **A missing frame pop has no executable oracle.** `ir_rooting` pops the frame at every
  exit; a miss leaves a scan root pointing into a green stack that `sprout_ctx_destroy` freed.
  Deleting every exit pop leaves `tests/stdlib/test_frame_slot_rooting.spr` green, plain and under
  `SPROUT_GC_STRESS=1`: the freed stack stays mapped, and a stale word is looked up and ignored.
  Only `ir_verify` (`just verify-roots`) and the unit counts catch it. A run-time check needs
  AddressSanitizer on a built program, or a task stack the runtime unmaps on destroy.
- [ ] `P3` **LLVM does not inline the sort pair's `insert` into `insertion`.** The Int merge
  sort is 1.26× C at n=440, against Phase 2's 1.2× aim, and the insertion phase over 16-element
  blocks carries most of it. `insert` costs 325 against a threshold of 225, so each element pays
  a call; forced inline, the bench reaches 1.14×. The bounds checks block it: without them it
  inlines and the bench reaches 1.06× (the overflow checks alone change nothing). Fix in the
  compiler (an inline hint for small TCO-loop functions), in the accessors (fewer fail-call
  sites), or by BCE stage 2 (below; stage 1 leaves `insert` entered once per element). Generic
  `sort_insert` is never inlined: its comparator call roots a slot, a dynamic alloca in the loop.
  `docs/native-sprout-runtime-v0.md` §Phase 2.
- [ ] `P3` **Sink a shared store and tail self-call out of both `if` arms.**
  `if c then do { set(d, k, a); f(x1) } else do { set(d, k, b); f(x2) }` stays a branch: each
  arm's inlined bounds checks and `+ 1` overflow check make it several blocks, so LLVM will not
  if-convert it. Written as one `set` and one `f` with `if c then … else …` arguments, it
  becomes selects (`sort_merge`, 1.92× → 1.37× C on random input). A lowering in
  `ast_to_ir` could do that rewrite when both arms end in the same effect and tail call and
  their differing arguments are pure. `docs/native-sprout-runtime-v0.md` §Phase 2.
- [ ] `P3` **Compile caching for the stdlib test runner** so repeated runs of unchanged test files
  skip the IR-emit + clang step. Shares an invalidation strategy with incremental build caching
  below.
- [ ] `P3` **Forward `str_char_at` loops are quadratic.** `str_char_at` and
  `str_char_at_unboxed` walk from byte 0 on every call, so a loop over `i` is O(n^2). A runtime
  cursor cache (2-4 entries: string, byte offset, char index) would make forward loops linear.
  It must also cache the codepoint count, since many loops guard with `str_len`. Rules: clear
  an entry on both sweep free paths (region slots and large objects), per entry, like
  `sprout_gc_invalidate_singleton`; write the cache before the boxed path allocates; and state
  "a CSTR is freed only by the sweep" next to the non-moving GC note in
  `docs/compiler-internals.md`. `parse_int`/`parse_double` already use a byte cursor.

### V1 roadmap candidates

- [ ] `P2` **Integer ranges: range patterns, half-open forms, diagnostics.** Spec §8.3 is the
  contract for `IntRange`, `a..b` and the `range*` helpers. Open: whether range patterns or
  half-open forms should exist at all, and sharper diagnostics.
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
- [ ] `P3` **Tier-2 CPR: unify the two worker-emission shells.** The active streaming path and the
  test-only batch path duplicate the same collect+index+emit+merge loop with different accumulator
  plumbing; only the shell diverges, so **IR-shape tests exercise a different shell than production
  ships**. Extract one loop. Same-area efficiency: `worker_source_for` re-scans all decls per callee
  (O(N·D) — build a name→decl `Dict` once), and `build_adt_ctor_index_go` computes
  `adt_ctor_entries` twice per TypeDecl.
- [ ] `P3` **`gate-audit` does not catch orphaned `.sprout` executables.** Its assertion B guards
  orphaned `scripts/*.sh` only. Seven unreferenced compiler drivers accumulated unnoticed until a
  review deleted them (2026-10-09).
- [ ] `P3` **An out-of-range `Double` literal fails in clang, not in the checker.** A literal above
  the largest `Double` (`1` and 400 zeros, then `.0`), or nonzero below the smallest subnormal,
  passes the front end into the IR text; clang then stops with `floating-point constant overflowed
  type` (or `underflowed`) at an IR line, with no Sprout position. Spec §4.1 rejects such a
  literal at compile time; the check belongs in the parser or checker, at the literal's line:col.
- [ ] `P3` **No scientific-notation Double literal in the lexer.** `1.5e3` is a parse error, so a
  large or tiny power of two is writable only as a long digit run, which is why
  `stdlib/math.sprout`'s power-of-two ladder is built from module-level `let`s and `sq()` calls.
  Adding `e`/`E` exponents would let the ladder be literals. Decide hex-float (`0x1.8p3`) at the
  same time; it is the only exact-and-readable spelling for a power of two. **Read the item below
  before assuming the const path is a speedup.**
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
- [ ] `P3` **Dense `Vector Double` loops get neither SIMD nor Polly, even after BCE.** In a
  `$fast` clone the vectorizer stops on the overflow-check early exit, then on "cannot identify
  array bounds"; Polly rejects every region on `IntToPtr`, because a vector handle is an `i64`
  cast at each use. Next: hand-apply a header-load hoist with TBAA, a hoisted cast, and `ptr`-
  and `double`-typed IR to `mm_j$fast`, and time each against C. `docs/llvm-value-types-v0.md`,
  `bench/llvm_remarks/remarks.sh`.

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
  `docs/linear-borrowing-v0.md` §20), and the escape notion for the borrow half (`local`,
  `docs/local-closures-v0.md`), with the prelude combinators marked `local` (design §8). Left: a
  linear value MOVED into a closure at an UNANNOTATED parameter (the true 0..n case); CONSUMING
  linear lambda parameters (`\c -> close(c)`); a linear `Scope`, both at once.
- [ ] `P3` **Compile-time check that `Scope`/`Chan` do not outlive `with_scope`.** Today the
  scheduler's handle table aborts a handle used after its scope closed; no type rejects the
  program. Candidates, none chosen: a containment bound on `with_scope`'s result type (misses a
  handle held by a closure or an outer-scope task); a linear `Scope`/`Chan` (breaks channels shared
  by several tasks); region or rank-2 typing (needs the region on closure and effect types). The
  runtime check stays the guarantee until one lands. See
  `docs/concurrency-channels-design-2026-07-16.md` §4 and the M4.4 entry above (a linear `Scope`).
- [ ] `P3` **Linearity-bound retro: deferred items.** From
  `docs/linearity-bound-retro-2026-10-20.md` §5–6: C2, one group-boundary check deciding bound
  obligations and marks under the final substitution; T3, a `/sprout-review` pass that writes
  linear misuse programs and runs them on master and head; X3, check the spec's `sprout` blocks
  that mention `linear`, expected-error where the text says so. Also park bound obligations
  unconditionally: drop the whole gate in `park_bound_obligations` (retro §3.3 measured it free).
- [ ] `P3` **A `let`-bound lambda gets no expected type, so it must spell its ownership mode.**
  M4.4b pushes a slot's ownership onto an unwritten lambda parameter mode, but only where the
  lambda is written at the call argument — `let work = \c -> peek(c) in with_file(n, work)` still
  fails with an ownership mismatch, while the annotated `\(c: borrowing File) -> …` compiles. The
  fix is to propagate an expected type into a `let` right-hand side generally, which is wider than
  linearity. Limit recorded at `type_error/lambda_borrow_mode_needs_call_position`.
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
- [ ] `P3` **A deferred mode mismatch is reported at the declaration, not the expression.**
  Arrows differing in `borrowing` unify and wait in the substitution under `@modes`
  (`unifier.defer_modes`) until the parameter's type is known; the pair carries no position, so the
  error points at the declaration head. Carrying one means a typed channel out of every unify site
  (`docs/linearity-bound-v0.md` §6.6).
- [ ] `P3` **An arrow's mode inside an instance method's signature must match exactly.**
  `infer.sig_against_class` compares instance and class signatures, parameters and return type,
  before any type is marked linear, so `f: Int -> Int` cannot implement `f: (borrowing a) -> Int`
  at `Int` (`type_error/linear_mode_instance_return_arrow`). Needs the linear types and bounds at
  that check (§6.6).
- [ ] `P3` **Linear-record ergonomics for OWNED records.** M4.5 lifted this only for `borrowing`
  parameters: `p.x + p.y` is legal for `p: borrowing Pos` and remains a reuse for an owned `p`. The
  field-read borrow is keyed on the binding's mode, not on `TGetField` syntax, deliberately —
  making every field read a borrow breaks `fn get_x(p: Pos) = p.x`, because a field read *is* an
  owned record's only consume, and relaxing the leak rule to compensate would stop an unclosed
  socket being a leak. The owned case needs a real consuming exit: a `RecordPattern`. Blocked on
  that.
- [ ] `P3` **Linear values held by a force-dropped task are never released.** `__scope_cancel`
  and a `with_timeout` expiry drop tasks parked on I/O, a sleep, a channel or `select`; a dropped
  task never finishes, so what it holds at the park (moved in or created inside) is never closed,
  though the program type-checks. Unstarted tasks are not dropped and run normally.
  `test_task_cancel.spr` pins today's behaviour. Rust survives the same at-most-once bound because
  a dropped `FnOnce` still runs `Drop`; Sprout has no destructors. A runtime leak on the
  experimental cancellation path, not a checker hole. Fix: release-on-drop in the scheduler, not
  the type system; spec §5.8 states the limit.
- [ ] `P3` **`with_timeout` cannot stop a body that only yields.** `__await_deadline` sets the
  timed-out reason and drops the body only when it is parked; a body in the run queue is
  re-queued, so a loop on `task_yield` runs past the deadline and `task_cancelled()` never turns
  true (1 ms timeout: 2,000,000 steps, `Completed`). Setting the reason on that path would let a
  cooperative body see it and return. `TimedOut` is unobservable for the same reason.
- [ ] `P3` **Tree-cancel cascade must drop a deadline's child before its owner.** A cancel of an
  outer scope while a task is parked in `with_timeout` aborts loudly today (`force_drop_task`
  refuses a task with `deadline_child`). Once a cancel may drop that owner instead, it must drop
  the child first: `with_timeout`'s `local` body may borrow a value lent from the owner's frame,
  and a surviving child would run against a frame that is gone (`docs/local-closures-v0.md` §6.5).
- [ ] `P3` **A linear `TcpConnection` costs an allocation and a GC root per connection; a
  `wrap linear` would not.** Migrating `http_server.sprout` from raw `Int` handles turned
  `pop_roots(1)` into `pop_roots(2)` in the read loop: a connection became a boxed one-field ADT
  rooted across the recursive call. `wrap linear` now exists (`docs/linear-wrap-v0.md`), so
  `TcpConnection`, `TcpListener` and `Task a` could each drop the box. **Measure first** — a
  server doing per-connection syscalls may not notice one allocation.
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
  match. Proposed resolution: no `List` `head` at all — `head`/`tail`/`last` exist only, total,
  on `NonEmpty`, and a list uses `let [x | rest] = xs else …` (`docs/non-empty-v0.md` §4.2).
- [ ] `P3` **`&`/`&mut` (shared-XOR-mutable) split.** v0 ships borrow-vs-consume only; the
  read-vs-write refinement is a later increment. `docs/linear-borrowing-v0.md` §2, §13.
- [ ] `P3` **Linear-bound error names a module function unqualified.** `linvec.new(2, linvec.new(2,
  0))` reports `` `new` takes `a` as a value it may copy or drop, … `` (`linvec_nested.err`): the
  user wrote `linvec.new`, and a bare `new` names no one function once modules use short names.
  The message is built in `resolve.sprout` and `infer.sprout` from the bare name; print it as the
  call site wrote it.
- [ ] `P3` **"never used" for a linear value that is only borrowed.** `let v = linvec.new(5, "x")
  in linvec.length(v)` reports "linear value 'v' is never used", but `v` is used: `length` borrows
  it, and nothing consumes it. The wording sends a reader looking for a typo.
  `linear_check.leak_msg` has one message for both; when the binder was borrowed, say "borrowed but
  never consumed".

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
  `docs/constructor-namespacing-v0.md` has the prior-art survey and the cheaper fallback; the
  restriction is now checker-enforced.
- [ ] `P2` **Duplicate type names in one module are accepted.** `type A = | X Int` declared twice
  with different constructors compiles and runs; the later declaration silently wins. Only a clash
  of constructor names is rejected (`check_duplicate_module_values`), so this is caught only when
  the two declarations also share one. Check type names beside value names.

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
- [ ] `P3` **The REPL cannot show a linear result.** `linvec.new(5, "x")` at the prompt fails with
  "No instance of ToString for stdlib.linvec.LinVec"; the user has to wrap it in `linvec.freeze`.
  Options: a `ToString` instance per linear type that renders and then consumes, or the REPL
  rendering a linear result through a borrow and dropping it. At least, name `freeze` in the error.
- [ ] `P3` **`:t` refuses a constrained function.** `:t linheap.pop` fails with "the expression
  needs a class instance that its type `LinHeap a -> (Maybe a, LinHeap a)` leaves open, and Sprout
  does not infer `where` clauses": the ambiguity check in `infer.sprout` runs on a `:t` operand,
  though `:t` needs only the type. It should print the scheme with its `where Ord a`.

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
- [ ] `P2` **Inline `Bytes` reads the way B1 inlines `Vector Double`.** `bytes_get_unboxed` is a
  C call per byte. B1 (`IRVecGetD`) covers only `Vector Double`, because generic wrappers erase
  other element types; `Bytes` holds only bytes, so no specialisation is needed. Hand-inlined
  in a base64 loop: 6.5× C → 2.6×. Phase 3.3 of `docs/native-sprout-runtime-v0.md` needs it.
  `docs/byte-buffer-v0.md` §3.
- [ ] `P2` **Alias tags (TBAA) on inlined runtime-object access.** Every Sprout value is an `i64`,
  so LLVM assumes an element store may hit a vector or `Bytes` header and reloads `len` and
  `data` after it. Tags that keep headers, elements and bytes apart: 2.6× → 1.6× C on base64.
  B1's `IRVecGetD`/`IRVecSetD` loops have the same reloads. `docs/byte-buffer-v0.md` §3.
- [ ] `P3` **A top-level literal table is opaque to LLVM.** A top-level `let` is a mutable global
  set at startup, so LLVM sees neither its size nor its contents and keeps a bounds check on a
  masked index (`& 63`). As a constant array: base64 2.08× → 1.65× C, and 1.25× with shift-based
  packing. Array data, unlike the Double-literal entry under Compiler Internals Follow-Ups.
  `docs/byte-buffer-v0.md` §3.1.
- [ ] `P3` **BCE stage 2: a `$fast` clone calls its callee's `$fast` directly.** `insertion$fast`
  still calls `insert`'s entry once per element, so the test runs per element and LLVM does
  not inline `insert$fast`; stage 1 moved the sort's Int rows by 3-4%, against the 1.07-1.09×
  of the by-hand version. When the caller's own entry test implies the callee's, call the
  callee's `$fast`. `docs/bounds-check-elimination-v0.md` §6 and §10.
- [ ] `P3` **BCE stage 3: step-0-or-1 induction variables (the merge).** `merge`/`sort_merge`
  advance `i` or `j` by 0 or 1 with `i + j` stepping by exactly 1, so the pass sees a varying
  parameter and leaves them checked. Also `Bytes` reads (no length change possible).
  `docs/bounds-check-elimination-v0.md` §6 and §10.
- [ ] `P3` **`sort_insert` and `sort_shift` are one loop written as two functions.** Mutual
  recursion hides it from BCE, which needs a self-tail-call. Merging `sort_shift` into
  `sort_insert`, as the bench's `insert` is written, makes the generic insertion loop versioned.
  `stdlib/mutable.sprout`, `docs/bounds-check-elimination-v0.md` §10.
- [ ] `P2` **GC trigger is object-count-blind, not byte-aware.** `sprout_gc_maybe_collect_threshold`
  fires on `g_managed_heap_count >= g_gc_threshold`, +1 per object whatever its size, so many-small
  over-collects and few-but-large under-collects (`adapt_factor` 3.0 amplifies it). 2026-09-06, a
  ~100,000× gap: a 1,600-element `Vec Int` literal peaks at 3,188 MB RSS for 767 KB of output.
  Vectors ≤508 elements carry elements inline, so those bytes ARE counted (2026-09-25); a longer or
  push-grown buffer is an invisible `malloc`. Cheap repro: `test_http_param_all_cost.spr` against
  pre-#622 `http_server` peaks at ~3 GB RSS (2^15 pairs; 2^16 hit 16 GB) on ~n²/2 words of copies.
  `docs/gc-generational-v0.md` §11 has the measurements and three approaches already measured and
  rejected — do not re-derive them. Also over-collects one big live array: 868 re-scans, 1.2 s of a
  1.4 s `MutVec` BFS (`docs/persistent-array-v0.md` §3.2).
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
- [ ] `P2` **nqueens allocates one `Vec` wrapper per `vec_set` — 16.7M per N=12 run**, which is its
  whole object-allocation count. Measured while landing nullary interning, which moved nqueens by 7
  objects and so disproved this entry's predecessor: it blamed `true`/`false` literals, but `Bool`
  lowers to a native `i1` (`br i1` in the emitted IR, no ctor), and tag 10 arity 1 is `Vec`. The
  cost is the persistent vector's copy-on-write wrapper, so the lever is the pure-array entry
  below or unboxing, not interning.
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
- [ ] `P2` **Branded indices for `LinVec`, so a write cannot be out of range.** Today
  `linvec.set`, `linvec.scatter` and `linvec.accum` trap on a bad index. An existential brand
  `s` on `Len s`, `Idx s` and `BVec s a`, with `Idx s` made only by a check against `Len s`,
  makes every write total in plain Sprout: no compiler change, prototype run. Measured on BFS and
  LCS: no cost when checked at use. Open: the module itself; `BVec` must expose no operation
  that changes the length.
  `docs/persistent-array-v0.md` §5.7.
- [ ] `P3` **`LinVec Double` writes do not inline.** `ast_to_ir.recognize_vector_op` and
  `bce.direct_shape` key on `vector_mutset` over a static `Vector Double`. `linvec_set` calls it on
  a polymorphic `raw` inside `unsafe_pure`, so a `LinVec Double` write stays a call with its bounds
  check, where a `MutVec Double` write inlines (B1) and loses the check (BCE). Measure first: the
  sieve and BFS run within 1.2-1.6x of `MutVec` without it. Versioning over `LinVec` writes breaks
  BCE's premise (`bce.sprout`, above `safe_io_externs`). `docs/persistent-array-v0.md` §5.3.
- [ ] `P3` **A persistent trie array, `PVec`.** `LinVec` has no old versions, so storing, sharing
  or branching on an array needs a 32-way trie in plain Sprout: no builtin, 3-21x slower than a
  Baker array on the kernels, fully persistent. `docs/persistent-array-v0.md` §5.2 and §7 step 2.
- [ ] `P3` **In-place reuse for a linear record's `with` update.** `p with (x = v)` on an owned
  linear record allocates; `docs/linear-reuse-v0.md` reuses only from a `match` arm. The case is
  narrow today: `with` cannot read its own base (`p with (x = p.x + 1)` uses `p` twice) or
  replace a linear field (spec §5.8). Same `IRReuseCtor`, minted at a `TRecordUpdate` whose base
  is an owned linear parameter. Worth it once `with` may read its base. Doc §2.
- [ ] `P3` **In-place reuse across types.** `docs/linear-reuse-v0.md` pairs a rebuilt constructor
  with the matched cell only when both belong to one type and have the same physical field count.
  Lean's relaxed pass also reuses a cell for another type of the same size. The lowering is the
  same header rewrite; only the type test in `reuse_take` goes. No measured program needs it.
  Doc §2.
- [ ] `P3` **In-place reuse of a non-parameter scrutinee.** A token is minted only when the
  scrutinee is an owned parameter's own cell (`TxEnv.owned` holds parameter SSA names). A
  `let`-bound or pattern-bound owned linear value, or a call result, is as dead after its match
  and could be reused, but needs its own ownership fact in `TxEnv`. Doc §2.
- [ ] `P3` **A linear ordered map in Sprout, `LinMap k v`.** `Dict`/`Set` are the runtime's
  persistent AVL tree: `String` keys only, a traced key per node, O(log n) new nodes per insert.
  A balanced linear tree in plain Sprout would take any `Ord` key, free its keys, and
  insert in place (`docs/linear-reuse-v0.md` §1.1: 25M → 632k objects for 1M inserts, unbalanced).
  Blocked on the entry above: a rotation matches a child taken from a field, which mints no token
  today, so every rebalance would allocate. One owner only, so it adds to `Dict`, never replaces it.

**Server and scheduler**

- [ ] `P2` **A green-task switch costs ~4 syscalls on macOS.** libc `swapcontext` runs
  `getcontext` → `populate_signal_stack_context` (`sigprocmask`, `getrlimit`, `sigaltstack`), then
  `setcontext` (`sigprocmask`). 10.7% of server CPU on keep-alive `/health` (`sample`, M3 Pro).
  Linux glibc not measured. A switch that saves only callee-saved registers would drop all four.
- [ ] `P3` **`http_server` costs ~4 µs/request more than a hand-written keep-alive loop** (17.2 vs
  13.3 µs server CPU on `/health`, after header rendering stopped allocating). What is left is
  request parsing and the GC it drives; no single function stands out.

- [ ] `P2` **`serve` is a client-driven memory exposure.** ~1.4 MiB of stack per concurrent
  connection, fully resident because `makecontext` zeroes it: 40 `wrk` connections hold 130–237
  MB, and the shape scales with client-chosen concurrency. Sprout copied Go's
  goroutine-per-connection model while paying **512× Go's per-task cost** (1 MiB vs
  `stackMin = 2048`) — and cannot follow Go's answer of growing small stacks, because growth needs
  `copystack`'s pointer adjustment and Sprout's rooting is non-moving by design. Independent of
  which pooling design lands. Each task also holds two kernel mappings (stack + guard page), so
  Linux's default `vm.max_map_count` caps live connections near 32k.
  `docs/green-task-pool-v0.md` §3.1.
- [ ] `P3` **The accept loop is the next server bottleneck.** Worker count is irrelevant from w=2 to
  w=256, so once task creation leaves the per-request path the single accept task is the limit. Any
  further HTTP-server work should start there, not at the handler.
- [ ] `P3` **Idle keep-alive connections hold a pool worker each.** `serve_pooled` cannot wait on a
  socket and its channel at once, so a worker waiting for a connection's next request serves nothing
  else, and the pool caps idle holders at ¾ of its workers and closes while a connection is queued.
  Under more clients than workers that turns keep-alive mostly off. The fix is one task holding
  every idle socket and handing readable ones to the workers (Apache's event MPM, Puma's reactor),
  which needs a runtime primitive: a wait over many handles, or readable-then-send. A builtin; ask
  first.
  `docs/http-keep-alive-v0.md` §6.
- [ ] `P3` **Closing replies close without a lingering close.** `tcp_close` is a bare `close()`.
  A reply that closes while request bytes are still unread can make the kernel answer with a RST
  that discards it before the client reads it (RFC 9112 §9.6): a 413, 431 or 501, and any reply that
  closes a connection the client is pipelining on (HTTP/1.0, a handler's close, capacity). Go
  half-closes and waits 500 ms. Needs `tcp_shutdown_write`, a builtin; ask first.
  `docs/http-keep-alive-v0.md` §6.
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

### Prelude-helper cleanup arc (#602, #603) — review residuals

Cleanups the 2026-10-09/10 reviews of #606, #613 and #617 reported and left. Confirm each before
acting.

- [ ] `P3` **TUI: the reply tuple and one loop are still spelled out after #606.** `app.sprout`'s
  `Step` and `Update` aliases are private, so `ide/app.sprout` (7 sites), the `tui_files` and
  `tui_dashboard` examples write `(widget.Widget Msg, app.Flow, Asks)`: export them.
  `ide/app.sprout`'s `bar_handler`, `refilled` and `keys_on_event` write `widget.Handled` by hand.
- [ ] `P3` **TUI: a private reverse-onto, a misplaced `first_row`, a duplicate assert.**
  `text.reverse_onto` copies the prelude's private `list_reverse_go`, and `no_prelude_core.sprout`
  and `children.sprout:64` do the same job: export a `list_reverse_onto`. `list_view` and `tree`
  import text_area's `viewport` module only for `first_row`; move it lower. `test.record` is
  `assert_true` with its arguments reordered.
- [ ] `P3` **`ast_to_ir`: the ADT ctor index is rebuilt per pass.** `build_adt_ctor_index` runs in
  `new_prog_env` and again in `tier2_augment_fns` and twice in `ir_pipeline`, on the same inputs.
  Store it in `ProgEnv`; `emit_worker_for` and the worker streams then drop their `adt_index` param.
- [ ] `P3` **`ast_to_ir`: bind-then-one-arm-match leftovers.** `translate_tail_*` and
  `translate_call` bind a result, then destructure it with a one-arm `match` (`er`, `br`, `r` — the
  last shadowed by `| Just r`); a tuple `<-` says it. Other one-arm matches only name `fresh_name`s.

### Compiler / Stdlib Misc

**Codegen and IR correctness**

- [ ] `P3` **46 compiler comments cite `codegen.sprout`, deleted 2026-07-12 in `5f29b9da`.** Spread
  over `ast_to_ir` (31), `ir_lowering` (7), `sprout_ir` (6), `type_kind` and `field_kinds` (1 each),
  mostly as "mirrors codegen.sprout:<fn>" provenance notes whose target cannot be opened. They read
  as live cross-references and send a reader looking for a file that is two months gone. Either
  re-anchor each to the surviving definition or drop the citation; decide once and sweep.
- [ ] `P3` **`ast_to_ir.sra_escape_ok_go` builds a free-variable Set per do-step to test one
  name.** Up to two full `compute_free_vars` Sets per step, only to ask whether `name` occurs. An
  early-exit "does `e` mention `name`" walk does the same job. Same family as #599.
- [ ] `P3` **Four passes hand-write the same `TypedExpr` rebuild.** `dce.elim_expr` and
  `bce.map_children` are plain child maps; `lowering.lower_expr` and
  `infer.resolve_dispatch_typed_expr` are too, apart from a few arms (`TCall`, `TLambda` scope,
  `TMethodRef`). A pure `typed_ast.map_children(g, e)` beside `traverse_children` would hold the
  rebuild once, as `resolve` now does through the `Result` form. Keep each pass's variant list
  exhaustive (guidelines §2a) and measure closures: `g` is one per node.

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
- [ ] `P3` **Dictionary pairings still compare classes by their last segment.** Dispatch keys
  keep the full class now, but `infer.take_dict_for_class`, `infer.dict_refines`,
  `infer.where_slot_error` and `verify_dispatch.take_by_class` match a dict to its constraint on
  the short name, so one call or `where` needing both `main.Eq` and `prelude.Eq` can pair them
  crosswise. `take_dict_for_class` cites an unqualified field entry (`Tag`) no bundled path was
  found to produce; prove that with a test, then compare in full.
- [ ] `P3` **Ten intrinsics get a `declare` for a symbol the runtime does not define** —
  `@to_double`, `@double_to_bits`, `@double_from_bits` and the seven `@bit_*`. Nothing calls them
  any more and LLVM does not require an unreferenced declare to resolve, so this is inert today.
  **It is worth closing because it is the mechanism that made the eta bug expensive to find:**
  `eprint` is on `is_hardcoded_intrinsic`'s list so its declare is suppressed and a
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
  `docs/gates.md` §Compiling one long block. A big STRING literal compiles in linear time
  (80 KB: 0.13 s), so shipping the table as a STRING literal decoded at startup remains the
  smaller-IR option: emitted IR is constant-size whatever the table.
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
- [ ] `P2` **A long `match`, or a `do` block of many `if`s, compiles in superlinear time.** A
  function whose body is one `match` of 100 / 200 / 400 arms allocates 2.5M / 3.0M / 4.7M objects
  (arena 2.7× per doubling at the top), tail-recursive or not, so it is not TCO. A `do` block of
  150 vs 300 `if` steps takes 0.86 s vs 3.46 s. Liveness (`ir_rooting.compute_live_out`) is ruled
  out: indexing it moved 600 of 14.4M objects. Found by the #599 fixes (2026-10-12); may share a
  cause with the chained-`let` entry above. Neither cost gate has a branching fixture.
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
- [~] `P2` **`instance Eq Double` / `instance Ord Double` in the prelude.** *(The originally filed
  silent-no-op `assert_eq` is GONE — the same program is now rejected at compile time with a
  located `No instance of Eq for Double`, most likely by the superclass/unresolved-dict work that
  turned null-filled dicts into compile errors.)* What remains is the stdlib gap: `Double` cannot be
  used with any `where Eq a`/`where Ord a` function — `assert_eq`, `check_eq`, `list_contains`,
  sorting. The workaround explains why it went unnoticed: `assert_true(state, label, x == y)` works
  because `==` on `Double` is the builtin operator path, which never consults the class. See the
  `Ord Double` entry in §1, which carries the decided IEEE semantics.

**Stdlib and prelude**

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
- [ ] `P2` **`fmt` rewrites a `#` inside a backtick template as if it began a comment**, padding it
  to two spaces: `` print(`a #${x} b`) `` becomes `` print(`a  #${x} b`) ``, and `` `a # b` ``
  becomes `` `a  # b` ``. That changes what the program prints, and `fmt` reports the file
  formatted. Double-quoted strings are left alone. Found when `just fmt` rewrote a task-io fixture.
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
  `test-*` shards and `lsp` run `scripts/ci_install_llvm.sh` (apt.llvm.org plus Ubuntu's mirrors)
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
  sessions running the gate at once fail to bind (seen 2026-10-04). The primitives now exist:
  `listen_local(0)` and `net.local_port`. What remains is moving each fixture to port 0 and
  passing the bound port to its client tasks. Parallel fixtures inside the gate need this too.
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
  for at least the Applicative shape. A binding nested in a container is now gated
  (`class_var_type`); what still reaches the scan is a call where no argument binds the class
  variable. Rebuild the repro first — `show2(x: a, y: b)` with two `to_string`s passes.
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
- [ ] `P3` **Local `let` bindings are not generalized.** A `let`-bound helper has one monomorphic
  type, so `let g = \z -> z in (g(1), g("a"))` is a type error. Next to a `match` on an
  existential ([spec §5.6, Existential constructors](docs/spec-v0.md#existential-constructors))
  that means a helper let-bound outside the arm cannot be applied to the unpacked value, since
  its parameter type would become the hidden type; the program is now rejected and the helper
  must move into the arm or to the top level. Fix: generalize a `let` whose initializer is a
  syntactic value, with the value restriction.
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
