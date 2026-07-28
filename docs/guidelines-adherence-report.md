# Sprout Code Authoring Guidelines Adherence Report

Date: 2026-07-28

This report re-assesses `stdlib/prelude.sprout` and the self-hosted compiler
(`stdlib/compiler/*.sprout`) against `docs/guidelines.md` ("The six basics",
now seven with `wrap`), and scrutinizes the guidelines themselves for
additions and removals.

It supersedes the 2026-06-13 snapshot (`docs/archive/guidelines-adherence-report.md`),
which predates guideline #7 (`wrap`) and the six weeks of compiler work since.
Findings below were verified at **construction and call sites**, not at type
definitions — a `SourcePos` field on an error ADT is necessary but not
sufficient evidence of "errors carry a source location from inception."

## What changed since 2026-06-13

- **#5 (spans) — the previous headline shortfall — is substantially fixed.**
  `InferErr`, `CheckErr`, `TypedErr`, `DiagError`, `DiagWarning` all gained a
  `source.SourcePos` field *and thread a real position at construction*. The
  report's "most damning" case — `lex_err_diag`/`parse_err_diag` discarding the
  position via `_` — now reads `TokenizeError msg pos -> DiagError(pos, …)`.
  54 of 60 `InferErr` construction sites thread a real position; the 6 residual
  `dummy_pos()` uses are confined to pattern inference (see #5).
- **#2 (total): `parse_int` no longer panics** — it returns `Maybe Int`
  (`prelude.sprout:1348`). The footgun the report flagged is gone.
- **#7 (`wrap`) is a new guideline** (post-dates the last report) and is
  assessed here for the first time.
- **Structural churn:** `codegen.sprout` is gone; IR emission now lives in
  `ast_to_ir.sprout`, `ir_lowering.sprout`, `ir_pipeline.sprout`,
  `ir_rooting.sprout`, `sprout_ir.sprout`, and `verify_dispatch.sprout`;
  ~15 new modules landed
  (`resolve`, `dce`, `deriving`, `lint_rules`, `field_kinds`, `type_kind`,
  `iface_codec`, `desugar_ctx`, …). All `codegen.sprout:NNNN` line references
  in the prior report are stale.

Two of the previous report's recommended next steps (#1 span-every-error and
#2 kill `parse_int` partiality) have largely landed. The persistent gaps are
the two structural ones: phase-distinct ASTs (#3/#4) and data-last compiler
entry points (#6).

## Guideline Assessment

### 1. Functional core, IO at edges

Status: partial (unchanged).

The finding is the same as last time and the same as observability guard-rail
#6: IO in `infer.sprout` (`InferState`) and the IR-emit modules
(`EmitterState`) is conservative imprecision, not discipline failure — the
effect system still lacks a State/Ref effect distinct from IO, so Ref-threading
passes carry `!{IO}`. `lowering.sprout` remains fully pure (0 `!{IO}`),
proving the discipline scales. Resolves only when the effect system grows a
non-IO state effect.

### 2. Total over partial

Status: partial (improved).

Good:

- **`parse_int` now returns `Maybe Int`** (`prelude.sprout:1348`) — the prior
  report's headline #2 footgun is fixed.
- `vec_get`, `dict_get`, `argv_get`, `env_get`, `str_char_at`, etc. still
  return `Maybe`; `read_file`/`write_file` still return `Result`.

Gaps (unchanged):

- `extern fn panic(msg: String) -> a !{IO}` (`prelude.sprout:1287`) is still
  exported — the partiality escape hatch remains part of the stdlib surface.
- Driver code still `panic`s on `read_file`/`write_file` `Err` even though both
  return `Result`: `bundler.sprout:425,504`, `compile_driver.sprout:50,234`,
  `fmt_driver.sprout:30,45,56,64`, `analysis_service_driver.sprout:802,814`.
  Files routinely fail to read; these are not unreachable-by-invariant, so they
  still violate the spirit of #2.

Impact: the prelude footgun is closed; the *driver* pattern of unwrapping
`Result` via `panic` is unchanged and is now the sole remaining #2 gap.

### 3. Make illegal states unrepresentable

Status: partial (unchanged).

Good (unchanged): `Effect`, `Pattern`, `Expr`, `Decl`, `Type`, `Scheme`, and
the per-pass `*Result` sum types remain well-encoded. New modules
(`field_kinds`, `type_kind`) continue the ADT-first pattern.

Gaps (unchanged):

- The guideline's headline example — phase-distinct ASTs
  (`RawExpr → ResolvedExpr → TypedExpr`) — is **still not implemented**.
  `qualify_expr(ctx, scope, expr: ast.Expr) -> ast.Expr` (`bundler.sprout:894`)
  produces the same type it consumes. A new `resolve.sprout` module exists but
  did not introduce a phase-distinct resolved type. See #4.
- `is_function_scheme(s) -> Bool` and `is_polymorphic_scheme(s) -> Bool`
  (`analysis_service_driver.sprout:820,825`) are still exported boolean-blind
  predicates. A `SchemeShape = MonoValue | PolyValue | FnValue` classifier
  would be more honest.
- Bool-flag threading persists in private helpers (formatter, the IR-emit
  modules). Lower priority per the guideline (private, not public API).

### 4. Parse, don't validate

Status: partial (unchanged at the qualify boundary; strong at the typed-IR
boundary).

Good: the `ast.Expr → typed_ast.TypedExpr` boundary remains exemplary — types
attach at every node, so lowering and IR-emit consume a representation that can
only exist if typed. **New: `typecheck_expr` (`infer.sprout:493`) now folds the
mandatory `apply_subst_typed_expr` step in before returning**, with `infer_expr`
demoted to a private `fn` (see v1-candidate #1 below — this is that rule adopted
as practice).

Gap (unchanged): qualify remains "validation, not parsing" — `ast.Expr ->
ast.Expr` re-resolves names without a structural type change. Pairs with #3.

### 5. Errors carry a source location from inception

Status: substantially compliant (was: not compliant — the largest single flip
since the last report).

Good:

- `TokenizeError`/`ParseError` remain construction-time-spanned.
- `InferErr`, `CheckErr`, `TypedErr` now carry `source.SourcePos`
  (`infer.sprout:85`, `checker.sprout:21`, `typed_ast.sprout:154`);
  `Diagnostic` is now `DiagError source.SourcePos String | DiagWarning
  source.SourcePos String` (`compiler.sprout:30–31`).
- **The construction sites thread real positions.** `lex_err_diag`/
  `parse_err_diag` (`compiler.sprout:60,64`) now pass the error's own `pos`;
  the `check` fold passes `CheckErr pos msg -> DiagError(pos, …)`
  (`compiler.sprout:82`). 54 of 60 `InferErr` sites thread `pos`/`cpos`/`upos` —
  including chained unifier errors (`Just (msg, upos) -> InferErr(upos, msg)`,
  `infer.sprout:2663`), which previously string-wrapped and dropped the span.

Remaining gaps (the residual, now-minority, spanless surface):

- **`BundleErr String` and `LowerErr String` are still bare** (`bundler.sprout:16`,
  `lowering.sprout:14`). They fold into `DiagError(no_pos(), …)` /
  `TypedErr(no_pos(), …)` at six sites (`compiler.sprout:149,159,222,262,299,335`),
  so the bundler and lowering passes remain fully spanless. These are the two
  error types to span next.
- `dummy_pos()` survives at ~15 *error*-carrying sites, clustered in
  pattern inference (`infer.sprout:2724,2768,2794,2825,2837,2854`), the
  no-args `CallErr` path (824,827,831), and instance-method/return-mismatch
  paths (3456,3459,3462,3846,4512,4790). Triage: the ~5 non-error `dummy_pos()`
  uses (synthesized `TUnit` nodes at 451,2742,2744,2770,2817) are *legitimate*
  — those nodes have no source position. The error sites are not.
- Minor: two names for the same `SourcePos(0,0,0)` sentinel — `no_pos()`
  (compiler) and `dummy_pos()` (infer/lowering/bundler). Consolidating to one
  named `synthesized_pos()`/`no_pos()` would make the "legitimately positionless"
  intent explicit and greppable.

Impact: the systematic discard pattern is gone. What remains is (a) two whole
passes (bundle, lower) whose error type never had a span, and (b) a pattern-
inference tail where the `pos` is often genuinely not in scope. Both are
tractable and bounded.

### 6. Data-last argument order in public APIs

Status: partial (unchanged).

The prelude remains the model citizen (collection last, everywhere). Compiler
pipeline entry points still invert it — `lower_program(prog, env)`,
`check_program(prog)`, `bundle_file(path, stdlib_root)`, and the IR-emit
entry points put the data first. This is the same ergonomic loss at the seams
the last report noted: a `bundle |> check |> lower |> emit` pipeline would need
argument flipping. Either flip to data-last or ratify the current order as a
deliberate, documented deviation (recommended — see scrutiny below).

### 7. Use `wrap` for semantic distinctions on shared representations

Status: adopted as a coherent name/identity taxonomy; the one real gap is
AST-*phase* distinction, not name distinction (first assessment; guideline
post-dates the last report).

Good — 11 `wrap` declarations, and they are *used* at extraction seams, not
merely declared:

- **A name taxonomy in `source.sprout`** — `FilePath`, `StdlibRoot`,
  `ModuleName`, `RawName`, `QualifiedName`, `GlobalName` (`source.sprout:20–51`,
  all `export wrap … = String`). This is the guideline's extraction-seam
  heuristic applied precisely: `qualified_name(module_name: source.ModuleName,
  name: source.RawName) -> source.QualifiedName` (`bundler.sprout:152`) makes the
  raw-vs-qualified swap a compile error at the exact boundary where names are
  qualified (9 call sites in `bundler.sprout`); `GlobalName` threads through IR
  init-globals synthesis (`ast_to_ir.sprout:6710–6842`).
- **Type identity in `types.sprout`** — `wrap TypeId = String` /
  `wrap TyVarId = String` (`types.sprout:41,47`).
- **Env / tyvar distinctions in `infer.sprout`** — `wrap GlobalEnv = Dict
  types.Scheme` (`infer.sprout:30`) and the retro-anchored `ProgVarName` /
  `FreshTVarName` pair (`infer.sprout:68,69`).

Assessment:

- Adoption is healthy where it matters — the naming seams, which are the exact
  places a `String`-swap bug is silent and expensive. The guideline's
  cost-benefit heuristic ("wrap at the extraction seam, keep internals raw") is
  being followed, not ignored.
- The one revealing gap: `wrap` reached **name granularity** (`RawName` vs
  `QualifiedName`) but **not AST-phase granularity**. `qualify_expr` is still
  `ast.Expr -> ast.Expr` (#3/#4) even though the *names inside* it are wrapped.
  That is a defensible line — a phase-distinct AST is an ADT-level change, not a
  one-field `wrap`, and belongs to #3/#4, not #7. So the residual work is a #3/#4
  design decision, not a #7 adoption shortfall.
- **Stale worked example in the guideline itself** — see scrutiny #R1 below:
  `wrap BodyEnv` no longer exists (only `GlobalEnv` does), and the `@fwd:`/
  `@eta_fwd:` distinction it cites was withdrawn.

Impact: `wrap` is the healthiest of the three "new-ish" areas — it landed, it is
used at the right seams, and it validates its own placement heuristic. The
guideline text needs one factual fix (R1); the rule itself needs no change.

## Recommended Next Steps (code)

1. **Span `BundleErr` and `LowerErr`.** These two are the last fully-spanless
   error types; the six `no_pos()` fold sites in `compiler.sprout` are the
   mechanical fix once the payloads carry a position.
2. **Thread `pos` through pattern inference and instance-method error paths**
   (the ~15 `dummy_pos()` error sites), and consolidate the two
   `SourcePos(0,0,0)` helpers into one named `no_pos()` reserved for
   genuinely-positionless synthesized nodes.
3. **Surface driver read/write failures as `Result`** instead of `panic`
   (10 sites across four drivers). Unchanged from last report.
4. **Introduce a phase-distinct resolved AST** (or route it through the new
   `resolve.sprout` module) to close the #3/#4 exemplar gap and prevent
   double-qualification.
5. **(no `wrap`-widening action)** — the naming seams are already wrapped
   (`source.sprout` taxonomy). The one residual is a phase-distinct AST, tracked
   under #3/#4 (next step #4), not as a #7 gap.
6. **Classify schemes with a `SchemeShape` ADT** instead of the two exported
   boolean predicates; audit remaining Bool-flag threading in the IR-emit
   modules.

---

## Guidelines Scrutiny — Add / Remove

The task's second half: does `docs/guidelines.md` itself need changes? Findings
are split into **factual corrections** (the code moved; the doc is now wrong —
safe to fix directly) and **rule changes** (additions/removals that alter what
the guidelines *require* — these route through the AGENTS.md Design Change
Process and are proposed here, not applied unilaterally).

### Factual corrections (stale text — fix directly)

- **R1. `wrap BodyEnv` worked example is doubly stale** (`guidelines.md:118`).
  `wrap BodyEnv = Dict types.Scheme` no longer exists — only `GlobalEnv` does.
  Worse, the example's stated purpose ("enforce the `@fwd:` vs `@eta_fwd:` scope
  distinction") references a marker family that was **withdrawn** during the
  scheme-constraints consolidation. Replace the bullet with a live retro-anchored
  example (`ProgVarName`/`FreshTVarName` is already cited in the next bullet and
  is real) or drop it.
- **R2. `codegen.sprout` no longer exists.** Any example or cross-reference
  pointing at it (the guideline body currently doesn't, but the deferred-items
  and future edits will) should target the split modules.

### Deferred-to-v1 items — status

- **R3 (corrected/withdrawn). Smart constructors — trigger has NOT fired; item
  stays deferred.** An earlier draft claimed the v0-feasible "assertion-builder"
  half had landed via `typecheck_expr`. That conflates two different mechanisms.
  The smart-constructor item is about *validating construction* (route all
  `T(...)` through a `mk_t` that asserts a field co-invariant) and its stated
  trigger is a *private-constructor language feature* — which has not landed
  (no spec support; verified). Its former exemplar `FnSig`/`mk_fn_sig` no longer
  exists (the `codegen.sprout` split removed `FnSig`). What actually landed is
  `typecheck_expr`, which is the **post-condition wrapper** pattern (fold a
  mandatory next stage into a producer) — a distinct rule, promoted as A1 below.
  The deferred smart-constructor item is untouched.

### Rule additions (promote — proposals for approval)

The prior report proposed seven v1 candidates. Re-checked against six weeks of
landings, three now have *code uptake* and should be promoted from proposal to
guideline; the rest hold their prior priority.

- **A1 — Type-encoded post-conditions for staged operations (was v1-candidate #1).**
  Now demonstrably adopted: `typecheck_expr`/`infer_expr` is the live instance of
  exactly this rule. **Promote to a numbered basic.** Without it, nothing
  normative stops a future refactor from re-exposing `infer_expr` and
  reintroducing the SIGBUS class the split prevented. **Priority: high.**
- **A2 — Env-gated trace hook per pipeline pass (was v1-candidate #4).**
  Now has real uptake: `SPROUT_TRACE_DISPATCH` is live (the only trace hook so
  far, alongside `SPROUT_TIME_PHASES`). One uptake is enough to ratify the
  pattern as expected-of-new-passes rather than aspirational. **Priority: medium**
  (was high; downgraded because only one pass has adopted it — promote the rule,
  but it is a "new passes ship with a hook" forward rule, not a backfill mandate).
- **A3 — Document non-obvious invariants at the definition site (was v1-candidate #2).**
  No code mechanism can enforce this; it remains the highest-value *documentation*
  rule and the `feedback_document_abi_invariants` lesson keeps recurring.
  **Promote. Priority: high.**
- **Hold (still valid, no new evidence):** no-silent-fallback-in-helpers (#3),
  promote-guard-rails-#2/#3 (#5), complexity-comment-on-prelude-exports (#6).
  Keep as v1 candidates; none regressed, none gained decisive new uptake.

### Rule removals / simplifications (proposals)

- **D1 — Withdrawn.** See R3 (corrected): the smart-constructor deferred item's
  trigger (a private-constructor language feature) has not fired, so there is
  nothing to retire. `typecheck_expr` is A1, not this item.
- **D2 — Resolved by audience-tagging (see Outcome).** The #6 "data-first
  compiler entry points" tension was an *audience* confusion, not a deviation:
  #6 is a [Library] public-API ergonomics rule, and the compiler's never-`|>`-
  chained pipeline entry points are not public APIs. Tagging #6 [Library] and
  adding a Scope note makes the rule cleanly not-applicable to them, so the
  "eternal partial" is retired without either flipping code or blessing a
  deviation.

### What was considered and left unchanged

- The six/seven basics themselves need **no removals** — every one still earns
  its place, and the two with the largest prior gaps (#5, #2) improved *because*
  they were called out, which is evidence the framework works.
- `wrap` (#7) needs no rule change beyond R1 — the guideline text is sound; the
  gap is adoption, not specification.

## Overall Rating

Materially improved since 2026-06-13. The dominant prior shortfall (#5, spans)
flipped from "not compliant" to "substantially compliant" with real
construction-site discipline, and the #2 prelude footgun is closed. The
framework is doing its job: the two guidelines that were loudest in the last
report are the two that improved most.

The remaining gaps are structural and stable across audits: phase-distinct ASTs
(#3/#4) and data-last compiler entry points (#6) — both are design decisions
awaiting a call, not discipline failures. `wrap` (#7) is both specified well and
adopted well — 11 declarations used at the naming seams, validating its own
extraction-seam heuristic; its one factual-staleness bug (R1) is in the doc, not
the code. The guidelines doc has three stale/landed items (R1–R3) worth fixing
now and three proposals (A1–A3) worth promoting on the evidence of the last six
weeks.

## Outcome (applied 2026-07-28)

Walked through the scrutiny items with the maintainer; `docs/guidelines.md` was
updated in the same session:

- **R1** — applied: the stale `wrap BodyEnv` worked-example was deleted (the type
  is gone and it cited the withdrawn `@fwd:`/`@eta_fwd:` markers).
- **R2** — no-op: guidelines.md never referenced `codegen.sprout`.
- **R3 / D1** — withdrawn (see corrected entries): the smart-constructor deferred
  item's trigger has not fired; left untouched.
- **A1** — added as the "internal corollary" sub-point under basic #4
  (type-encoded post-conditions; `typecheck_expr`/`infer_expr`).
- **A2** — added as a one-line cross-reference to `docs/observability-guard-rails.md`
  in the intro (env-gated `SPROUT_TRACE_<pass>` hook per pass).
- **A3** — added as a dedicated "Documenting load-bearing invariants" subsection.
- **D2** — resolved via audience-tagging (below).

**Structural change — audience tags.** Rather than split into separate
compiler/stdlib/user-code documents, each rule now carries an audience tag —
**[Universal]**, **[Library]**, or **[Compiler]** — defined in a new "Audience
tags" section. This dissolves the recurring #6 tension (it is [Library]-only) and
clarifies that ignoring a rule outside your audience is not a deviation. User-code
idiom/formatting remains owned by `docs/idiomatic-sprout.md` and
`docs/style-guide-v0.md`.

**Follow-up noted:** `scripts/guidelines_reminder.sh` still says "six basics"
(there are seven, now audience-tagged); the reminder hook text should be
refreshed separately.
