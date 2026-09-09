# Sprout Backlog

Purpose: track progress toward a usable, general-purpose, functional-first language.

Legend:
- Priority: `P0` (critical), `P1` (important), `P2` (later)
- Status: `[ ]` todo, `[~]` in progress, `[x]` done

## Backlog

### 1) Language Core and Safety

> Closed items are not kept here. What landed is in git history, in the design doc each entry names,
> and in `docs/spec-v0.md`. An entry is a title plus what is broken, where, and why it matters —
> detail belongs in a `docs/<feature>-v0.md`.

**Effects**

- [ ] `P1` **A declared effect is not enforced once a function is passed as a VALUE.**
  `fn pure_map(xs: List Int) -> List Int = list_map(shout, xs)` runs IO and reports
  `declared pure, inferred pure`; an instance may also strengthen its class's effect, and a
  pure declaration may call an `!{e}` parameter. Four parts including unknown-label rejection
  below; migration cost measured zero on 127 in-tree + 199 downstream files.
  `docs/effect-subsumption-v0.md`.
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
- [ ] `P1` **An unknown effect label is accepted as a VARIABLE — `!{NOPE}` type-checks and
  launders IO.** Not inert, as this entry said until 2026-09-08: `!{NOPE}` becomes `$e30`, so it
  binds against anything and `list_map(sneak, xs)` runs IO under a pure signature. Reject a label
  that is neither `IO` nor a lowercase variable; decide `!{}` in the same change. **Lands with
  effect subsumption as its part 0** — that design's variable-exempt arm is unsound without it.
  `docs/effect-subsumption-v0.md` §6.0.
- [ ] `P3` **A trailing effect annotation on a non-arrow type is discarded** — `Int !{IO}` has no
  effect slot. §7 rule 9 now records that it carries no meaning; rejecting it outright would be
  more honest, and it touches the same constructor as the `!{NOPE}` check above, so **decide the two
  together**. Verified by execution; fixtures under
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

- [ ] `P1` **Int overflow policy (DEFERRED 2026-07-06).** `+`/`-`/`*` do silent two's-complement
  wrap (plain `add/sub/mul i64`, no `nsw`), contradicting the spec's arbitrary-precision `Int`
  intent. Option A (trap/panic, Swift/Rust-debug/Zig) vs Option B (Go: wrap, compile-error on
  literals only); author recommends A. **X4 must rule on RADIX literals explicitly** —
  `0xFFFFFFFFFFFFFFFF` is `-1` today, which is the useful reading for masks, so a uniform "must fit
  in `Int`" rule would reject the all-ones mask idiom. `docs/int-overflow-policy-decision.md`;
  `docs/bitwise-int-ops-v0.md` §5.6.
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
- [ ] `P3` **`head_name_matches` suffix-matches the final dotted segment** (`infer.sprout:1917`), so
  `where Sh (Box a)` binds to another module's same-named type — two modules defining `Box` are
  indistinguishable and the scan takes whichever argument comes first. Fix: qualify the head at
  canonicalization time so the comparison is exact.
- [ ] `P3` **`iface_codec` gained `#app:<Name>` with no iface version bump** (`:48`, `:533`) —
  still emits and accepts v6, where the precedent is that v3→v4 was bumped for exactly this. No
  live miscompile (`module_loader` does not consume ifaces), so it is a forward-compatibility gap.
  Fix: bump to v7 both sides, add a `#app:` case to `test_scheme_roundtrip.spr`.
- [ ] `P3` **Full Maranget usefulness matrix for product exhaustiveness.**
  `(true,true)|(false,false)` on `(Bool,Bool)` is not yet rejected; sound to over-accept per spec
  §5.5.

**Bindings, patterns and surface syntax**

- [~] `P2` **Refutable `let-else` + pure monadic binding.** Tier 1, 1b and the effectful-RHS
  (`do`-local) tier all landed; surface (A) chosen over widening `let..in`. **Remaining tiers:** 2
  — no-else propagate for `Result`/`Maybe` (a built-in `?`); 3 — general monad-generic
  propagate, entangled with effect-system design D2. `docs/let-else-and-monadic-binding-plan.md`;
  `docs/effectful-let-else-v0.md`.
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
- [ ] `P4` **Existentials — `check_existential_constraints` is a dead no-op hook**, body emptied
  and still called from `infer.sprout`. Wire a real check, or drop the call and keep the comment as
  the home marker.
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
- [ ] `P3` **Reassess the `print` design** — should compiled `print` dispatch through `ToString`
  everywhere, instead of the type-erased runtime renderer? The full redesign regresses the
  importless loud-fail, risks bootstrap (every `print` in `stdlib/compiler/` needs an instance in
  scope), and is a normative spec change (`print(true)` flips `1` → `"true"`). Needs its own
  design doc. Decision: do it, or formally bless the intrinsic + surgical-rewrite split as
  permanent.
- [ ] `P2` **Revisit string-interpolation type-directed dispatch (Mechanism A).** Phase 4 ships a
  syntactic coercion (`template_to_string` inserted at `String`-expected contexts). Evaluate an
  `IsTemplate` class once a third meaningful instance (`Bytes`, a logging frame, a tagged-template
  processor) lands and forces generality.

**Modules and prelude**

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
- [ ] `P3` **Phase B mutual-TCO: a member that is both self-tail-recursive and in a heterogeneous
  mutual cycle keeps its mutual edge as a plain call.** `mutual_tco_rewrite_fn` skips any fn
  carrying an `IRTcoEntry`. No miscompile — only the mutual edge builds a native frame per
  iteration. `docs/mutual-tco-phase-b-v0.md` §5a.
- [ ] `P3` **Phase B / Tier-2 CPR: `emit_repack_one` emits width-2 only.** A match-routed cycle
  member returning a ≥2-field-ctor ADT would drop fields. Unreachable today (worker routing is
  gated on max-ctor-arity ≤ 1), so latent. Widening needs `{tag, f0, f1}` and a `{i64,i64,i64}`
  sret — cf. `docs/archive/cpr-nested-product-unboxing-handoff-2026-06-28.md`.
  `docs/mutual-tco-phase-b-v0.md` §5b.
- [ ] `P3` **Phase B code-review follow-ups (2026-07-21).** Deferred, latent-or-cleanup; full
  disposition in `docs/mutual-tco-phase-b-v0.md` §12. #2 arity re-check on `pb_retarget_tail`; #3
  restore the per-edge all-i64 `params_match` gate; #4 broaden `pb_ret_unifiable` to `TTuple`
  (blocked: a tuple return has no `adt_index` entry); #5 confirm bare-vs-qualified callee names at
  the pre-lowering seam; #6 collapse the duplicated tail-position grammar walk; #7 replace the
  O(n²) SCC with one Tarjan pass; #8 delete the dead `pb_scc_of_rest`; #9 restore T19's exact-name
  assertion; #10 the vacuous `build_ret_i64` eligibility map.
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
- [ ] `P3` **GC hardening follow-ups from the phase-2 review.** HDRCHECK region-walk validation (8
  of 10 kinds have no layout check); `is_large` uniformization (~9 branch sites — do it before the
  generational step multiplies them); single ownership of the "keep ≥ 1 normal region" invariant;
  close the OBJ tag-write window by passing the tag into the alloc path.
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
  builtins). **Remaining:** rebuild `tcp_read_exact` over `tcp_wait` — it is not a soundness
  violator but has *no deadline*, which is why the C5 body-framing fix could not use it, so adding
  one is an API change and its own PR; migrate `tcp_write_all_timeout`, which re-arms its idle bound
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
- [ ] `P3` **`bytes_slice`'s extern declaration has misleading parameter names.** The prelude and
  `stdlib/bytes.sprout` declare `(b, from, to)`; the C implementation takes `(start, count)` and
  clamps `count`. Anyone computing `to` from the declaration silently gets a shorter slice. Rename
  to `start`/`count`. Trivial, but it touches the prelude, so it needs the seed-refresh path.

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
  `Dict String`. The two *framing* hazards are refused outright (differing `content-length`,
  repeated `host`), but `Cookie` legitimately arrives as several field lines (RFC 6265) and
  collapses to the last. Needs an all-values accessor beside `request_header` (Go
  `map[string][]string`; Rust `HeaderMap` multi-map).
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

### 2.5) Binary Data and Protocol Primitives

- [ ] `P2` **`bytes_builder_append` is O(n_left + n_right) per call**, copying full chunk arrays
  into a new flat array, so a `list_fold` over n strings costs O(n²). Switch to a tree/rope where
  append makes an internal node (O(1)) and `builder_build` traverses once. Also add `builder_str`
  and `builder_to_str` to skip the `Bytes` intermediary and the UTF-8 round-trip. These three
  unblock a pure-Sprout `string_join_suffix` over `list_fold` + builder (see §5).

### 3) JSON Support

- [ ] `P2` **Reimplement `json_stringify` in Sprout** once string/escaping primitives make that
  practical, keeping host builtins for the impossible or efficiency-critical.
- [ ] `P2` **An out-of-range literal reads as an infinity, so a conformant document can be READ but
  not re-written.** `parse_double` builds `mag * pow10(e)` and `pow10` overflows, so
  `parse("1e400")` gives `JsonFloat(+inf)` and `stringify` then refuses it. `1e-400` underflows
  silently to `0.0`. RFC 8259 §6 names `1E400` as an interoperability hazard and explicitly allows
  limiting range, so rejecting at parse is legitimate and has not been taken. This is the only way a
  non-finite Double can enter from ordinary JSON input rather than arithmetic. Design Change Process
  call between: reject as `Err`, keep the saturating `inf` and document it, or clamp to the largest
  finite Double — and whichever is chosen should settle the underflow-to-zero, the same question
  at the other end. No caller depends on today's behaviour.
- [ ] `P3` **Sub-1.0 magnitudes lose the low bit in `parse_double`.** `parse_mantissa` computes
  `iv + (fv / pow10(len(frac)))`, so a value is rounded twice and lands 1 ULP off: measured over 200
  pseudo-random mantissas per decade, 1e-9 → 85/200 fail and 1e-3 → 26/200, while 1e0…1e16 are
  0/200. The documented limit of a pure-Sprout reader. Closing it needs either a correctly-rounded
  decimal→binary algorithm in Sprout or a `strtod`-backed builtin — the latter needs approval
  under Builtin vs Stdlib rules 4–6, with the *correctness* argument doing the work, not
  performance.

### 4) Terminal UI Runtime

- [ ] `P1` **TUI M4 — the widget library (`stdlib/tui/widgets/`).** C1 landed 2026-09-07:
  `container`/`children`/`text`/`paint` ship `row`/`column`/`grid`, an opaque `Slot`, `label`/
  `static`/`spacer` and the four child traversals; `examples/tui_dashboard.sprout` went 284 → 157
  lines with no container of its own. `View.measure` now returns a `Measured` so a child can ask
  for "whatever is left". C3 below remains. Design: `docs/tui-widget-set-v0.md`.
- [ ] `P2` **TUI `list_view` — items are fixed at construction.** A list is built from a
  `List String` and keeps it, so a filter box over one, or a list built from a model that updates,
  has no way to change what it holds; rebuilding the tree resets every widget's state, focus
  included. Per-item rendering is the same gap one level out — Brick's `renderList` takes
  `Bool -> e -> Widget n`, so an item can be any widget. Design: `docs/tui-list-view-v0.md` §8.
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
- [ ] `P2` **TUI M4 C3 — the larger widgets.** `tabs`, `tree`, `table`, `text_area`. `scroll_view`
  and the screen clip it needed landed as C3a (`docs/tui-scroll-view-v0.md`); the rest are still
  open. The `MutVec` editing primitives `text_area` needs landed 2026-09-09, so nothing gates it.
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
- [ ] `P2` **Codepoint-indexed `str_slice` is still O(source_len)**, walking the source per call, so
  per-token callers are quadratic in input size. The byte-indexed direction shipped
  (`str_slice_bytes`, `str_starts_with_at_byte`) and is now genuinely O(1) in `|s|`; what remains is
  either leaving `str_slice` as the codepoint-indexed convenience with its cost documented, or
  direction (b), a codepoint-to-byte index cache on String values. *(The shipped half is the lesson:
  both byte functions originally opened with `strlen(s)` to bounds-check, so the replacement was
  itself O(source_len) and every migrated scanner stayed quadratic — while three separate places,
  this item included, stated the intended complexity. Nothing measured it. It is now guarded by a
  cost test rather than a comment.)*
- [ ] `P2` **B4 — `list_length` is unreliable on complex element ADTs.**
  `examples/digit_recognizer/recognizer.sprout` hand-writes two monomorphic length helpers purely
  because of it. Root-cause and fix so those can be deleted; add a regression over a `List` of a
  field-bearing ADT and of a tuple. Likely the same dispatch/monomorphization family as B2.
- [ ] `P2` **Retire five private re-implementations of `split`.** `stdlib.string.split` landed
  2026-09-05; the five are `template.split_on`, `url.split_amp`, `ast_to_ir.split_on_comma`,
  `http_server.split_header_lines` and `prelude.split_on_char`. A mechanical sweep with two things
  to check: the new one is O(bytes × |sep|) where the `split_once`-recursion is quadratic, so at
  least two sites get *faster*; and it **keeps** empty segments, so a caller relying on its private
  version dropping them needs a `filter` (`path.split` is the worked example).
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

### 6) Modules and Packaging

- [ ] `P1` **Package/dependency conventions for third-party modules.** Direction recorded
  (non-normative, per-phase approval pending) in `docs/packaging-v0.md`: strict compile-time
  coherence with the orphan rule extended across packages, single-version selection with a loud
  resolver, incompatible majors as distinct explicit package identity (Go `/v2`-style), git sourcing
  reusing the `.iface`/`.bc` cache, graph-wide compiler-version unification. Package-qualified
  identity is the spine — it generalizes module-qualified type identity and dissolves the "dotted
  non-`stdlib.` import resolves to `Nothing`" gap as its degenerate single-package case, subsuming
  the `examples.*` item below. Phased plan in §10, semantics before mechanics.
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
  no-space case exists in a *type* position, then add a formatter regression.
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
  id, so the contract covers AST rules only. **(b)** `just lint` is permanently red — 10 findings
  across 4 files, two violating deliberately because the raw form *is* the test subject. **(c)** Put
  `lint` in `ci-fast-gates`; it is pre-commit only today, which is why the red set drifted
  unwatched. Needs a `## Lint` section in `docs/style-guide-v0.md`.
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
- [~] `P2` **Formatter/linter beyond the baseline.** Four AST lint rules shipped
  (`staircase-of-doom`, `redundant-vec-from-list`, `list-shape-pattern`, `list-prefix-pattern`) on
  top of `formatter.sprout`'s text-based Style checks. **Remaining roadmap** from
  `docs/idiomatic-sprout.md`: "Match the producing call directly" (a `let`/do-bind immediately
  followed by a match on that single otherwise-unused variable) and "Collapse a trivial `do` block".
  The rest of that doc is design-level or too fuzzy for a reliable syntactic check. **Also open:** a
  config file for per-rule enable/disable (not justified at four rules), and autocorrect (needs an
  AST-aware rewriter; today's formatter is a line-based text transform).

**Gates and diagnostics**

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
- [ ] `P3` **`docs/int-overflow-policy-decision.md` §2 cites
  `stdlib/compiler/codegen.sprout:2106`**, deleted 2026-07-12. That doc is a live decision document
  — spec §8.4 points readers at it — so its ground-truth section should re-verify against
  `ir_lowering.sprout`. The finding (plain `add/sub/mul i64`, no `nsw`) is still correct; only the
  citation rotted.
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

- [~] `P0` **Dictionary-passing lowering.** Hidden-method-parameter lowering supports constrained
  polymorphic helpers via forwarding and monomorphizes concrete call sites to specialized wrappers.
  Confirmed working: a class method in value position at a concrete type, and a method value whose
  dictionary comes from the caller's constraint. **The blocker is at the syntax layer before the
  representation layer:** a method-level constraint inside a class is a parse error (`Expected }`)
  and there is no `forall` surface at all. Same root cause as `traverse`/`sequence` below — one
  surface change unblocks both; sequence them together.
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
- [ ] `P2` **Instance overlap is not enforced against env-supplied instances on the REPL /
  analysis-service path.** Confirmed by probe: a session redeclaring a *prelude* instance
  (`instance ToString Int`) is ACCEPTED on the REPL path and rejected on the compile path, while a
  duplicate within the session's own decls is rejected on both. `check_program_with_env` passes
  imports and the prelude as env schemes so their instances arrive as `@inst:` markers, while
  `check_overlapping_instances` scans `decls`, which holds only the session's own source. **Open
  question that sets the severity:** which instance wins at dispatch — if the session's, shadowing
  is the de facto semantics and needs specifying; if the prelude's, the session instance is silently
  dead and wants rejecting. **Decide before implementing:** reject for consistency with
  `--phase check`, or define session-level instance shadowing in the spec. Today is neither.
- [ ] `P2` **`Validation` type + error-accumulating `Applicative`** — the killer app (form-style
  validation collecting *all* errors). Needs its own type (`Valid a | Invalid e`) because a type
  admits one `Applicative` and `Result`'s is fail-fast; the instance requires `Semigroup e`.
- [ ] `P2` **`traverse` / `sequence`**, deferred with `Validation`. A `Traversable` *class* is
  blocked — the parser rejects a method-level `where Applicative f` constraint. Ship them first as
  free functions (`list_traverse`/`list_sequence`, `where Applicative f`, structure hardcoded —
  the `concat_map` pattern, verified to compile and run); the class needs the parser surface above.
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
  `wrap Age = Int deriving (Num, Ord, ToString)` generating instances that unwrap → delegate →
  rewrap, **while `Age` stays distinct from `Int`**. This is Haskell's GeneralizedNewtypeDeriving,
  verified to preserve distinctness — NOT a coercion (auto-wrap destroys mistake-prevention,
  auto-unwrap loses the wrap type; both are a transparent alias). Scope: operators with all-wrapped
  operands (`age1 + age2 : Age`, `name1 ++ name2 : Name`). `++` falls out for free since it already
  desugars to `Semigroup.append` — route the witness to the lifted instance, not the `String`
  peephole. Out of scope: mixed `age + 1` with a bare literal (needs numeric-literal polymorphism).
  `docs/coercions-and-literals-v1-draft.md` Case B; requires `spec-v0.md:343-344`'s "wrap cannot
  derive" line to change.
- [ ] `P2` **Investigate qualified imported-constructor access** (low confidence).
  `import stdlib.foo as f` then `f.MkCtor(x)` gave `Unknown variable: f.MkCtor` for a parametric
  ADT, while the *type* `f.Box` and functions `f.mk_box` resolved fine and a non-parametric ADT's
  constructor resolved. Confirm whether qualified constructor access is intended syntax at all
  before treating it as a bug.
- [ ] `P2` **Deriving/specialization follow-ups** once the core class system is stable.
- [ ] `P3` **`Alternative` class + generic `or_else`** — deferred until a *second* lawful instance
  exists (e.g. a parser combinator type); with only `Maybe` it is single-instance ceremony, and
  List's lawful instance (`++`) is already `Semigroup`.
- [ ] `P3` **Monad-generic `do` + built-in `?` propagation** — wire `Monad` into `do`/`<-`
  (currently desugarer-special-cased for `Maybe`/`Result`) and add the Tier-2/3 propagate form. This
  is the rung that flattens the `staircase-of-doom` cascades.
  `docs/let-else-and-monadic-binding-plan.md`.
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

## Design Roadmap

> Forward-looking design and soundness priorities, and the V1 roadmap. The sections above are the
> engineering execution log.

### Fundamentals-review residuals

The 2026-07-03 adversarial review's campaign (W1–W11, decisions D1–D5) is otherwise landed; full
findings and probe programs are in `docs/fundamentals-code-review-handoff-2026-07-03.md`, and the
effect half is in `docs/effect-enforcement-v0.md`. Read the handoff doc's §2 for *why* the effect
deferral happened, not for current behaviour. Still open:

- [ ] `P2` **W7's `INT_MIN / -1` operator guard**, coupled to the int-overflow policy decision in
  §1.
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
  inference, call resolve, the four `typecheck_decls` validators). `typecheck_decl`'s `BodyLenient`
  internal-invariant arm intentionally stays `no_pos()` — unreachable by construction.

### Runtime and performance roadmap

- [ ] `P2` **Make tight Sprout string-processing loops competitive with host builtins**, so moderate
  stdin/text workloads do not need dedicated host helpers to be practical. Investigate the native
  overhead in recursive stdlib string loops such as `string_lines` over stdin-loaded text — tail-
  recursive loop lowering, call/closure overhead, primitive boxing, string/vector iteration — and
  add stable benchmarks for `string_lines`, `trim` and AoC-style stdin parsing so wins are
  measurable. Target: `string_lines` over a `day5input`-style workload in low single-digit seconds.
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
- [ ] `P2` **`stdlib.path` — the typed half.** `stdlib.fs.path` landed 2026-09-05 with the pure
  String-based ops (`join`, `basename`, `dirname`, `extension`, `stem`, `split`, `normalize`,
  `relative_to`, `is_absolute`), so what remains from `docs/stdlib-path-v1-draft.md` is the typed
  surface: zero-cost `File`/`Dir` wraps, smart constructors `file_checked`/`dir_checked` rejecting
  empty and NUL, migrating `read_file`/`write_file`/`*_exists`/`dir_list` to take them, and retiring
  the compiler's `FilePath`/`StdlibRoot` into `path.File`/`path.Dir`. Constraints: POSIX-only, no
  absolute-vs-relative type distinction, no eager normalization, no symlink resolution, no
  byte-level paths. The POSIX-only constraint is unblocked by §10's **Milestone B**, not A —
  Win32 accepts forward slashes, so paths stay non-load-bearing until the compiler runs there.
- [ ] `P3` **Expand native ADT lowering.** `docs/native-adt-lowering-v1.md`. The `Nothing` singleton
  and immediate-match optimization for direct constructor-producing scrutinees landed; planned:
  broader constructor forwarding, whole-scrutinee binding, and specialized representations for tiny
  ADTs.
- [ ] `P3` **`wrap` ergonomics follow-ups.** Parameter-level destructuring (`fn f(Foo x) -> …`
  desugaring to a match, useful for all single-constructor types); an auto-generated zero-cost
  accessor; a named-field variant `wrap Foo { inner: T }`; and `opaque type` for Scala 3-style
  module-boundary transparency, distinct from the shipped `export type Name` export-opacity.
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

- [ ] `P1` **`unifier.apply_full_subst` does not terminate on a cyclic substitution.** Found via the
  LSP: `sproutd` at 99.7% CPU for 15 minutes with RSS flat at 2.4 MB, the stack cycling
  `instantiate_with_vars → apply_full_subst → apply_full_subst → …`. Flat RSS with unbounded
  time means it cycles a fixed structure — consistent with a binding `α := … α …` that an
  occurs check should make impossible, though that is **not proven**. Trigger, bisected: two
  `task_fork`s whose forked function calls any *imported-module* function, so `Task` is a red
  herring and imported-scheme instantiation is the common factor. Repro and trigger table:
  `docs/module-surface-authority-v0.md` §7.1. Unreachable from editors now, so latent. **Any fix
  needs a time-bounded harness** — an in-process `.spr` test would hang `just test`, not fail it.

### Sprout-IR / Model-C codegen

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
- [ ] `P3` **`type_driver.sprout` and `lower_driver.sprout` are orphaned executables carrying a
  fixed defect — wire them up or delete them.** Both still report errors via `print` (stdout), the
  exact defect fixed in `compile_driver.sprout`; they were left alone deliberately, since nothing
  builds them and fixing an unbuildable binary is an unverifiable change. Their original purpose was
  parity diagnosis against a compiler that no longer exists, so the likely answer is **delete**,
  plus the stale `checker.sprout:62` comment. If they are wanted as diagnostic entry points, note
  `compile_driver --phase check`/`--phase lower` already do what they do. `gate-audit`'s assertion B
  guards orphaned `scripts/*.sh`, not orphaned `.sprout` executables — arguably a third direction
  worth adding.
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
  binding captured by a lambda and any linear lambda parameter, because a closure may run 0..n times
  and its call count is untracked; M4.5 borrowing did not lift this and extends the rejection to
  borrowed values, since whether a captured borrow is sound depends on whether the closure escapes
  and outlives the consume — a distinction Sprout does not have. Known-hard: Linear Haskell
  shipped it incomplete. The move-into-a-one-shot-closure slice landed as M4.4a. Left: a linear
  value captured at an UNANNOTATED parameter (the true 0..n case, including
  `list_each(xs, \x -> write(conn, x))`, whose borrow half needs an escape notion); linear lambda
  *parameters* (`\c -> close(c)`, needing the lambda's own parameter types to carry ownership); and
  a linear `Scope`, which is both at once.
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
- [ ] `P2` **`borrowing` inside arrow-type syntax.** `fn apply(g: (borrowing File) -> Int, f: File)`
  cannot be written — arrow types have no ownership slot, so an annotated arrow means *consuming*.
  **Real and not blocked by M4.4:** a function-typed *parameter* over a linear value typechecks
  today (an early M4.6 draft claimed otherwise and was wrong). Deferred for cost: a parser change
  (hence the 2-step bootstrap), an ownership field on `ast.TypeExpr`'s arrow with its own fan-out,
  plus formatter and TypeExpr-codec work — and mixing a parser change into a type-system change is
  what Collaboration Rule 2 warns against. Purely additive; it reuses M4.6's `types.Ownership`.
  Fixture: `tests/conformance/type_error/borrow_fn_as_value`.
- [ ] `P2` **The over-strict effect-bind fallback now has a concrete consumer.** `x <- e` where
  `e : Container Linear !{IO}` types `x` as the payload, so a non-linear container of a linear is
  conservatively rejected — which forces `ch <- chan_new(s, cap)` to be written as a threaded
  parameter in `bench/http_worker_pool/pool_server.sprout`. Verified that this, **not** linearity
  propagating from a type argument, is the whole obstacle: `Chan Res` used twice as a parameter
  typechecks, `List Res` twice typechecks, and `borrowing Holder Res` is rejected as "only allowed
  on a parameter of a linear type". Fixing the fallback removes a real shape constraint from stdlib
  code.
- [~] `P3` **Containment virality — binder half LANDED 2026-09-07; type half still open.**
  Decision (Kuba): **Option 1** — containment decides which *bindings* carry the obligation, while
  linearity stays per-declaration as a property of *types*, so a record containing a linear field is
  still not itself linear (contrast Austral) and a `Maybe File` *parameter* is still not a linear
  parameter. **Still open — Option 2, full virality**, with linearity computed by containment
  everywhere, reaching parameter modes, borrowing filters and field reads. Blocked on a linearity
  bound for type parameters, and **to be decided jointly with the effect-bind item above, which
  wants the opposite answer for parameters.** §6 of `docs/linearity-virality-v0.md` has the two
  questions it opens (a type variable's universe; declaration-level recursion needing a visited set)
  that the binder scope avoids.
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
- [ ] `P2` **Decide whether `import M (T)` brings `T`'s constructors into scope.**
  `select_named_pairs` matches names exactly, so a selective import of a type does not import its
  constructors; the bundler, by inlining, behaves as if it does. Three stdlib modules were relying
  on the permissive behaviour and failed on the env path — their import lists have since been
  completed, so this is a semantics ruling, not a live break. Options: require explicit constructor
  listing (status quo on the env path), make `T` imply its constructors, or add an explicit `T(..)`
  form — Haskell spells the permissive case that way precisely because `T` alone does not imply
  it. The ruling belongs in spec §visibility/exports; the two front ends disagree until then.
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
- [ ] `P2` **Delete `module_loader.build_import_pairs*` and the orphan
  `type_driver`/`lower_driver`.** Those two driver modules are unreferenced and are the only
  remaining callers of the retired scheme-environment path, each carrying its own copy.
  `build_import_pairs_with_roots` is marked RETIRED in-file. `load_prelude_pairs` stays either way
  — `check_bundled` uses it for the ambient-prelude case. (Same two modules as the
  orphaned-executables item under Compiler Internals.)
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

- [ ] `P1` **Inline GC root push/pop, or enable LTO.** After the type-aware rooting fix, root
  push/pop is still ~44% of N-queens CPU. The remaining pushes are genuine heap pointers, so type
  filtering cannot help further; the per-push cost is the **function-call boundary** between LLVM IR
  and the C runtime (~50 cycles of caller-save spill + branch + writes). **(A) inline as IR** — a
  slim i64-only root stack in the runtime, with codegen emitting the 3–4 instructions inline;
  SCAN/PTR roots keep the current machinery. **(B) enable LTO** — `-flto` on both the runtime and
  the emitted `.ll`, and hope LLVM inlines across the boundary. B is the cheaper test (a one-line
  recipe change); A is the canonical fix. Verify B first. Expected 2–3× on top of the landed
  rooting work.
- [ ] `P2` **GC trigger is object-count-blind, not byte-aware.** `sprout_gc_maybe_collect_threshold`
  fires on `g_managed_heap_count >= g_gc_threshold`, and the count increments by 1 per managed
  object regardless of size — a `VectorVal`'s backing array is a plain `malloc`, invisible to the
  trigger. Many-small over-collects, few-but-large under-collects, amplified by the `adapt_factor`
  default of 3.0. First measured instance 2026-09-06 and the gap is ~100,000×: one function holding
  a 1,600-element `Vec Int` literal peaks at 3,188 MB RSS to produce 767 KB of output.
  `docs/gc-generational-v0.md` §11 has the measurements and two results worth not re-deriving —
  `SPROUT_GC_THRESHOLD` cannot investigate this and its flat readings will mislead you, and a
  count-based cap is not the fix because it trades the quadratic memory for a livelock (which is
  also a ready-made reproducer).
- [ ] `P2` **`ir_lowering` assembles IR text with `++` in a recursion at all three nesting levels**
  (`lower_ops`, `lower_blocks`, `lower_fns`, and the same shape in `sprout_ir.print_*`). Each of n
  frames concatenates onto the entire remaining tail, so emitting a block of n ops copies O(n ×
  total) bytes. `docs/string-building-v0.md` §6 prohibits exactly this and `string.join` shows the
  sanctioned form; the fix is mechanical and local. §11 there has the regime analysis — why this
  does not contradict the "string concatenation was the wrong target" correction, and why fixing it
  hides the byte-blind GC trigger rather than closing it. Whether the fix collapses the curve is
  unmeasured.
- [ ] `P2` **The freelists are still wiped and rebuilt from *all* regions every sweep** — a
  prerequisite for the nursery, since a minor collection that marks only young objects but rebuilds
  the whole heap's freelist is not proportional to the young set. Making them generation-scoped
  (stop wiping; remove/re-add only the swept regions' entries) is the natural next increment, and
  the per-region touched-class bookkeeping it needs already exists
  (`fl_region_commit`/`fl_region_rollback`).
- [ ] `P2` **Skip re-pushing already-rooted function parameters.** Codegen re-pushes arguments at
  every call site even when the argument is a `TVar` resolving to a parameter already rooted in the
  caller's frame — the recursive `queens(…)` re-roots three vectors it already holds. Pure
  codegen fix in `emit_args_with_roots`. Expected 20–40% on top of the rooting work, and
  multiplicative with the inlining item above.
- [ ] `P2` **True/False/Nil singletons.** Mirror the existing `Nothing` singleton in `sprout_make0`.
  Each `vec_set(col, true, cols)` and `false` literal currently allocates a fresh ADT object;
  singletons make each a constant pointer, eliminating ~16M of the 33M allocations per N=12 run.
  Expected 10–15%; small runtime change.
- [ ] `P2` **Bump-allocated nursery with no per-object metadata** — the canonical generational GC,
  and distinct from the older split-the-node-list draft, which keeps per-object `ManagedNode` and so
  cannot reduce per-allocation cost. Objects are identified by address-range membership and
  allocation is `arena_top += size` (~5 cycles vs ~50 for malloc + register). Survivors are copied
  to the old gen and gain full metadata on minor GC. **Only worth pursuing after the push/pop
  inlining lands** — push/pop dominates today, while the bump allocator helps the malloc/free
  family at ~10% of CPU. See also the generational-step entry in §1, whose measurements re-scope it
  as compiler-only.
- [ ] `P3` **HAMT persistent vector for `vec_set`** — O(n) → O(log n). **Deferred:** at N≤14
  vectors are 12–27 elements (a single HAMT leaf), so path-copying is the same work as the current
  copy, and `vector_set` is 1.1% of CPU.

**Server and scheduler**

- [ ] `P2` **`serve` is a client-driven memory exposure.** ~1.5 MiB of stack per concurrent
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
- [ ] `P3` **48 compiler comments cite `codegen.sprout`, deleted 2026-07-12 in `5f29b9da`.** Spread
  over `ast_to_ir` (31), `ir_lowering` (7), `sprout_ir` (6), `type_kind` and `field_kinds` (2 each),
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
- [ ] `P3` **DCE keeps an unreachable stdlib function in one corpus file.**
  `examples__sentry_api.sprout.ll` gained `@stdlib.string.split` and `@…split_go` (~175 lines)
  though nothing calls them; the other 13 corpus files importing `stdlib.string` prune both.
  Harmless (dead IR, not wrong IR), filed because a per-file inconsistency in DCE grows. **Two
  plausible explanations, both REFUTED by probe** — recorded so nobody re-derives them:
  *name-prefix reachability* (a probe calling only `split_once` emits no `split`; the correlation
  was perfect and the mechanism still wrong) and *the import set* (a probe with the same four
  imports also emits no `split`). The trigger is something in the example's **body** — bisect
  that, not its header.

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
- [ ] `P2` **A local named `empty` — any zero-argument, return-dispatched class method — is
  still rejected.** `fn via(empty: Int -> Int) -> Int = empty(7)` fails with
  `No instance of Monoid for Int` while the same shape named `mk` compiles. Note the **absent
  `dispatch-verify:` prefix**: this is rejected during dispatch *resolution*, a different pass from
  the local-shadowing fix that closed the `append`/`to_string`/`compare`/`pure` cases, and it is not
  a regression. `Monoid.empty` takes no arguments, so its dispatch is driven entirely by the
  expected return type — the arm a local shadow must be excluded from. Wanted: the scope check the
  verifier has, applied wherever resolution decides a bare name is a class method.
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
  unwritable over `Char`. **The value is already there:** `sprout_runtime.c:6441` says outright that
  a Char IS its codepoint as an immediate i64, so `char_codepoint : Char -> Int` is the same
  identity function with the arrow reversed and needs no new runtime capability. Workaround in use:
  `stdlib/unicode` decodes UTF-8 to `List Int` itself and keys every entry point on `Int`; where a
  numeric value must come out of a character, the house idiom is an index into an alphabet string,
  which works for 62 known characters and does not generalise. Blocks a `Char`-shaped public API for
  `stdlib/unicode` and `stdlib/tui`'s per-cell width lookups.
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
- [ ] `P2` **A large list/`Vec` literal is not a usable way to ship a data table.** A literal of N
  `Int`s lowers to ~11 IR lines per element and costs O(N²) compiler memory: 800 → 825 MB, 2,000
  → 4,631 MB. Past that it stops compiling at all — N=6,000 fails immediately with
  `GC root pool exhausted` (a fixed `RootNode g_root_pool[131072]`). The ceiling's location between
  2,000 and 6,000 is established; the ~22-roots-per-element accounting is inferred, not verified.
  **Workaround that works today: ship the table as a STRING literal and decode it at startup** —
  the same 2,000 entries cost 49 MB and 218 IR lines, emitted IR being constant-size whatever the
  table. Lexing is still superlinear in literal length (80 KB source → 1.9 GB), so chunk across
  several few-KB literals with a compact encoding. Fixing the `ir_lowering` quadratic moves the
  memory curve but not the root-pool ceiling — a separate fixed limit, and both must go.
- [ ] `P2` **Allow a layout `do` block inside call parentheses** — an inline multi-statement
  effectful lambda as a call argument. `range_fold(\ (s, k) -> do <newline> stmt1 …, seed, r)`
  fails with "Expected )"; today the lambda must be `let`-bound and passed by name. A probe shows it
  affects all argument positions, single- as well as multi-statement, so it is not a
  non-final-argument issue. Root cause: the do-step layout scanner ends a block only on EOF or a
  dedent, never on a bracket, and `update_bracket_depth` clamps close-brackets at zero, so a `)`
  closing an enclosing `(` is invisible and the block over-consumes to EOF. Fix is the standard
  layout rule (cf. Haskell's parse-error rule): let depth go negative, ending the step and the block
  when a closer takes it below 0, and on a depth-0 `,`. Guard existing do-blocks against
  regressions.
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
- [ ] `P3` **One-line `let … in` is rejected everywhere, and the diagnostic blames the next
  declaration.** `fn f(n) = let x = n + 1 in x + 10` fails with `Expected pattern` pointing at the
  *following* top-level declaration. Spec §5.2.1 requires `in` dedented to the `let` column, so the
  rejection is conformant — but the one-line form is the canonical ML spelling and the error names
  a line the author did not write. Root cause: `parse_let_block`'s binding-end scan is line-based,
  so a same-line `in` cannot terminate a binding slice. **(a)** Fix the diagnostic to point at the
  `in` — cheap, worth doing alone. **(b)** Accept the form: needs a spec change and a scan
  stopping at a `let`-balanced `in`, and that balancing miscounts when a binding's RHS holds a `do`
  block with a `let` *statement*, which swallows the real terminator.
- [ ] `P3` **`parse_do_let_bindings` never reads its `binding_col`, so a misaligned binding is
  silently absorbed.** The split between bindings falls out of wherever `parse_expr` happens to
  stop, so a third binding at a column that is neither the binding nor the block column is accepted.
  Both the function's own comment and spec §5.2.1a assert an alignment rule that nothing enforces.
  Which way out is right is a language call: enforce the column and reject (a tightening, needs a
  corpus sweep), or drop the claim and soften the spec to say bindings split at the end of each
  right-hand side.
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
- [ ] `P3` **Five implementations of "which names does this pattern bind?"** —
  `ast_to_ir.pattern_names`, `dce.pat_binds`, `linear_check.pattern_all_binders`,
  `linear_check.pattern_linear_binders`, `verify_dispatch.pattern_bound_names`. All five are
  exhaustive, so a new variant is a compile error at every one rather than a silent wrong answer —
  but that is five sites to update in lockstep, and the return shapes differ (`List String`, `Bool`,
  and a type-directed variant consulting `types.Type`), so a shared helper is not a mechanical
  extraction. Worth doing when a pattern variant is next added, which is when the cost is actually
  paid. Not urgent: exhaustiveness makes divergence loud, which is the property that matters.
- [ ] `P3` **`looks_like_do_step_start` duplicates `parse_expr`'s notion of "starts an expression",
  by hand** — eighteen `tok_is_*` disjuncts maintained in parallel with what `parse_expr` accepts,
  with nothing to detect divergence. Each divergence has cost a PR (a float literal and a prefix `!`
  could not begin a do step; neither could a `let … in`). Wanted: a test that derives one from the
  other — for every token kind the lexer can emit, assert the two agree on a minimal expression
  starting with it. **Feasibility unverified**: some tokens are expression-legal only in context, so
  the test needs a way to avoid false failures, and that design question is the actual work.
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

- [ ] `P2` **The apt LLVM install has no retry and no cache, and it hung CI for 24 min.** The `test`
  and `lsp` jobs run a bare `sudo apt-get update && sudo apt-get install -y llvm clang …` with no
  retry, no timeout and no package cache; on one run that step sat 24 minutes against 24 seconds on
  the run 43 minutes earlier. It cannot fail fast — GitHub's default job timeout is 6 hours, so a
  stalled mirror burns the whole budget rather than erroring, and from the checks list it looks like
  a long test run. And the workflow caches the deterministic thing and not the fragile one: the
  reproducible bootstrap (23 s) is cached, the networked third-party fetch is not. Recommended first
  increment is a retry loop plus `timeout-minutes`; measure before assuming an apt cache helps.
- [ ] `P2` **Straggler heavy bundlers still run on every PR.** The compiler-suite directory gate
  misses the ~10 `tests/stdlib/test_ir_*` suites that also bundle the whole compiler (one is 222k IR
  lines / ~17 s emit) but live in flat `tests/stdlib/`. Move them under `tests/stdlib/compiler/`, or
  gate by an explicit file list. Also open: LPT (largest-first) dispatch in
  `_test-stdlib`/`_compile-examples`/`ci-fast-gates` so a 50 s pole stops stranding idle lanes, and
  folding the serial `verify-bootstrap-fixed-point` (~23 s) into the `ci-fast-gates` fan-out.
  Re-measure first: the 846 s compiler-suite figure predates the quadratic-`strlen` fix, and all the
  older wall-times came from a self-hosted GCE worker, not today's GitHub-hosted `ubuntu-latest`.
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

- [~] `P1` **A `where`-constrained function used as a first-class VALUE.** Fixed everywhere the
  dictionary is readable at the mention, by rewriting a bare mention into the eta-lambda the
  programmer could have written (`eta_expand_constrained_arg`) in `infer_arg_slots`; the five
  remaining value positions then closed with a single resolver fix. **Residual: the reporter's
  original failure has never been reproduced.** They reported the unresolved-dict poison thunk's
  "please report" message; ten probe shapes here reached the arity panic or ran correctly and none
  emitted a poison, `Double` included. That matters because the poison-sink entry records that no
  source-level RED INVOKES a poison — a real one falsifies that and is the more severe bug. **Get
  the triggering expression from the reporter first**; a poison-reaching shape needs a producer
  guard.
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
- [ ] `P3` **`extern fn str_slice(s: String, from: Int, to: Int)` misnames its third parameter.** It
  is a **length**, not an end index — the runtime signature is `(s, start, length)` and the
  prelude documents it inline, but the declaration says `to`. Reading the declaration rather than
  the comment produces a slice wrong by exactly `start` characters, silent whenever the caller
  compares the result against something (it cost a session once). Rename to `len`. A prelude edit,
  so it needs its own reseed cycle even though no IR changes.
- [ ] `P3` **Write the ADT-vs-record concretization invariant into `docs/compiler-internals.md`.**
  An ADT constructor-application node is born concretely typed (`Box Int`), while a
  record-construction node carries `Box $a` with the binding living only in the substitution — so
  any dispatch or resolution site reading the raw node type instead of applying the resolved
  substitution silently works for ADTs and breaks for records. Three sites hit this; the invariant
  is "resolve constraint/dispatch argument types via `apply_subst(s3, …)`, not `typed_expr_type`
  alone", and a written rule stops the next one at review time.
