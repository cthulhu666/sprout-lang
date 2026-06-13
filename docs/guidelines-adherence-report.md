# Sprout Code Authoring Guidelines Adherence Report

Date: 2026-06-13

This report assesses `stdlib/prelude.sprout` and the self-hosted compiler
(`stdlib/compiler/*.sprout`) against `docs/guidelines.md` ("The six basics"),
and proposes additions to the guidelines based on retrospectives and external
references.

## Summary

Current adherence is partial.

The prelude is generally clean: data-last argument order, total `Maybe`/`Result`
return types on collection access, no boolean blindness in any public function.
The compiler is the source of most violations. The single largest gap is
guideline #5 (errors carry source locations) — only `TokenizeError` and
`ParseError` are spanned ADTs; every other error type in the pipeline
(`InferErr`, `CheckErr`, `LowerErr`, `BundleErr`, `TypedErr`, `Diagnostic`)
carries a bare `String`. Several construction sites have the position in scope
and discard it. Pipeline driver code reaches for `panic` whenever a fallible
extern returns `Err`, which is also a partiality regression.

The highest-risk gap is #5: lossy error types make the diagnostics pipeline
strictly weaker than the ADT-level information that already exists. The
pattern is systematic, not isolated.

## Guideline Assessment

### 1. Functional core, IO at edges

Status: partial.

Good:

- `stdlib/compiler/lowering.sprout` (1502 lines) is fully pure — 0 `!{IO}`
  annotations, demonstrating the discipline is achievable in a large pass.
- `stdlib/compiler/parser.sprout`, `lexer.sprout`, `types.sprout`, and
  `typed_ast.sprout` are pure or nearly so.
- Most prelude data structures and combinators are pure; IO is concentrated
  in extern declarations near the bottom of the file (~lines 928–1042).
- Drivers (`compile_driver.sprout`, `full_driver.sprout`, etc.) sit at the
  edges and carry IO as expected.

Gaps:

- `stdlib/compiler/codegen.sprout` carries 157 `!{IO}` annotations and
  `stdlib/compiler/infer.sprout` carries 57. Both use Ref-based state
  (`EmitterState`, `InferState`) which is IO-effectful in the current effect
  model. This is documented design (see `project_pure_unifier_decision`
  memory note) but it conflates internal mutable state with external IO.
- `checker.sprout:42` `check_program` returns `!{IO}` purely because it
  threads `unifier.new_state()` — semantically a State effect, not IO.
- `ast_to_ir.sprout` has 32 IO annotations for the same reason.

Impact:

This is conservative imprecision rather than discipline failure — IO escapes
because the language lacks State/Ref-as-non-IO effects, not because pure
transformations were carelessly tainted. Same finding as observability
guard rail #6. Will resolve once the effect system grows a way to
distinguish internal state mutation from external IO.

### 2. Total over partial

Status: partial.

Good:

- `vec_get`, `dict_get`, `vector_get` return `Maybe a` (prelude
  lines 157, 441, 992).
- `argv_get`, `env_get`, `str_char_at`, `str_char_at_byte`, `map_nth_key`,
  `map_nth_value` all return `Maybe`.
- `read_file`/`write_file` return `Result String String` / `Result String Unit`.
- All compiler-internal AST traversals return `Result E a` or a custom
  `XOk | XErr` ADT.

Gaps:

- `extern fn parse_int(s: String) -> Int` (prelude:955) panics on bad input
  (`runtime/sprout_runtime.c:1280` calls `tcp_fail`). This is a public
  partial function with no spelled-out `Maybe Int` variant.
- `extern fn panic(msg: String) -> a !{IO}` (prelude:933) is exposed,
  making the partiality escape hatch part of the stdlib surface.
- Compiler/REPL driver code routinely panics on `Err` from `read_file`,
  although `read_file` explicitly returns `Result`:
    - `bundler.sprout:418` `panic("read_file: " ++ path ++ ": " ++ msg)`
    - `bundler.sprout:489` `panic("read_file: prelude: " ++ msg)`
    - `compile_driver.sprout:54`, `compile_driver.sprout:267`
    - `fmt_driver.sprout:18`, `fmt_driver.sprout:33`
    - `analysis_service_driver.sprout:499`, `analysis_service_driver.sprout:511`
    - `repl.sprout:198`
  These are not "unreachable-by-invariant" — files routinely fail to
  read — so the panics violate the spirit of #2 even though they are in
  driver code.

Impact:

The prelude's panic surface is small but real: `parse_int` is a footgun and
the project's own driver code demonstrates a recurring pattern of unwrapping
`Result` via `panic` rather than threading the error to the caller. Both
are fixable: introduce `try_parse_int : String -> Maybe Int` (or `Result`)
and require drivers to surface read-errors as diagnostics.

### 3. Make illegal states unrepresentable

Status: partial.

Good:

- `types.Effect = EffectPure | EffectIO | EffectRow (List String) | EffectVar String`
  encodes the effect-row taxonomy as an ADT rather than a boolean.
- `Visibility`-style coding is used throughout the AST: `Pattern`, `Expr`,
  `Decl`, `TypedExpr`, `Type`, `Scheme`, `BodyResult`, `CallSig`, etc.
- `InferResult / CheckResult / BundleResult / LowerResult / TypedResult /
  CompileResult` give each pass a sum type rather than `(Bool, String)`.
- `CurBlk` in codegen (`NoBlk | InBlk Int`) avoids null-tagged ints.

Gaps:

- The guideline's headline example — phase-distinct ASTs
  (`RawExpr → ResolvedExpr → TypedExpr`) — is **not** implemented. The
  qualify pass in `bundler.sprout:873` has signature
  `qualify_expr(ctx, scope, expr: ast.Expr) -> ast.Expr`, so "qualified"
  and "raw" are the same type. A consumer cannot tell from the type
  whether a `Decl` has been qualified. This is exactly the convention-only
  invariant the guideline warns against.
- `analysis_service_driver.sprout:517,522` exports public predicates
  `is_function_scheme(s: Scheme) -> Bool` and
  `is_polymorphic_scheme(s: Scheme) -> Bool`. These are boolean-blindness
  in a public API; a `SchemeShape = MonoValue | PolyValue | FnValue` ADT
  with one inspection would be clearer.
- Boolean parameters are common in private helpers:
  `formatter.sprout` threads `has_equals_ahead: Bool` through ~6 functions;
  `compile_driver.sprout:201,292` and `codegen.sprout` use `debug_mode_: Bool`.
  Per the guideline these are not strictly public APIs, but the pattern
  spreads (codegen alone passes the same `Bool` through dozens of helpers).
- `Diagnostic = DiagError String | DiagWarning String` (compiler.sprout:24)
  uses the constructor tag as the only distinction; the payload structure
  is identical, suggesting a `Severity` field on a unified `Diagnostic`
  ADT would be more honest.

Impact:

The encoding of single-pass invariants is good, but cross-pass invariants
(post-qualify, post-lower) remain conventions enforced by the call graph
rather than the type system. The exemplar `RawExpr/ResolvedExpr/TypedExpr`
gap is the most consequential.

### 4. Parse, don't validate

Status: partial.

Good:

- The parser-to-typed-AST boundary is exemplary: `ast.Expr → typed_ast.TypedExpr`
  attaches `types.Type` at every node, so downstream passes (lowering,
  codegen) consume a representation that can only exist if typed.
- `typed_ast.TypedExpr` accessors (`typed_expr_type`, `typed_expr_pos`)
  guarantee callers cannot construct a "typed expression" without a type.
- `TokenizeError` / `ParseError` are construction-time-spanned ADTs at the
  earliest boundaries.

Gaps:

- Qualify is the canonical case of "validation, not parsing":
  `qualify_expr: ast.Expr -> ast.Expr` re-resolves names but produces the
  same type, so the boundary is not preserved structurally. Pairs with #3.
- `analysis_service_driver.sprout:91–105` parses JSON fields with
  validators that return `Result String String`/`Result String Int`. The
  shape is right, but the eventual aggregate is still a loosely-typed
  `json.Json` rather than a typed request ADT.
- `lowering.lower_program -> LowerResult(TypedProgram)` (which preserves
  types) is positive movement that should be the model for other passes.

Impact:

The typed-IR boundary at codegen is well-built; the qualified-AST boundary
is structurally absent. The result is that mid-pipeline assumptions
("this is qualified") are not encoded.

### 5. Errors carry a source location from inception

Status: not compliant.

Good:

- `token.TokenizeError(String, SourcePos)` (token.sprout:21) — spanned.
- `parser.ParseError(String, SourcePos)` (parser.sprout:11) — spanned.
  Construction helpers (`make_error`, `expected_keyword`,
  `expected_symbol`, `expected_ident` in parser.sprout:104–132) attach
  `cur_pos(tokens, i)` at construction.
- AST/TypedAST nodes carry `source.SourcePos` on every expression and
  declaration, so positional context for errors is *available* throughout
  the pipeline (see `ast.sprout:49–67`, `typed_ast.sprout:32–50`).

Gaps:

This is a systematic violation. Every downstream error type drops the
location even though the source ADT had one:

- `infer.InferResult` (infer.sprout:12–14): `InferErr String`.
  `infer.sprout:391` builds `InferErr("Unknown variable: " ++ name)`
  inside `infer_var` whose `pos: source.SourcePos` parameter is in scope.
  `infer.sprout:478` does the same inside `infer_call` with `pos` and
  `cpos` both available.
- `infer.sprout:428,455` chain error messages by string-concatenation —
  the original `unifier.unify` error string is wrapped without any span.
- `infer.sprout:1554,1573,1581,1596,1611` (`infer_binop_*`) build
  `InferErr(op ++ " needs Int: " ++ e)` without the `pos` parameter that
  is already in the function signature.
- `checker.CheckResult` (checker.sprout:18–20): `CheckErr String`.
- `typed_ast.TypedResult` (typed_ast.sprout:115–117): `TypedErr String`.
- `bundler.BundleResult` (bundler.sprout:14–16): `BundleErr String`.
  `bundler.sprout:1061,1064` wrap the inner err_msg with literal prefixes
  rather than carrying the position of the offending decl.
- `lowering.LowerResult` (lowering.sprout:12–14): `LowerErr String`.
- `compiler.Diagnostic` (compiler.sprout:24–26):
  `DiagError String | DiagWarning String`. Most damning:
  `compiler.sprout:67–73`:
    ```
    fn lex_err_diag(e: token.TokenizeError) -> Diagnostic =
      match e with
      | token.TokenizeError msg _ -> DiagError("lex: " ++ msg)

    fn parse_err_diag(e: parser.ParseError) -> Diagnostic =
      match e with
      | parser.ParseError msg _ -> DiagError("parse: " ++ msg)
    ```
  These functions receive a positioned error and *deliberately discard the
  position* via `_`. This is the most concrete instance of the guideline
  violation; the fix is mechanical.
- `bundler.sprout:447,452,496,501` stringify lex/parse errors with the
  file path prefixed but the SourcePos discarded.
- `ast_to_ir.sprout:243,646,673–689,1078,1288,1304,1507–1671` and
  `unifier.sprout` errors are all bare strings.

Internal generated nodes use `dummy_pos()` (`SourcePos(0,0,0)`) which makes
downstream error attribution impossible:

- `infer.sprout:324` `dummy_pos` helper + uses at 327, 1818, 1820, 1846, 1893.
- `lowering.sprout:121` `dummy_pos` helper + ~10 uses at 476–1109.
- `bundler.sprout:150` `dummy_pos` + use at 1065.

Impact:

This is the headline finding. The compiler has *more* positional information
at the construction site than the error ADT can carry, and the codebase shows
a recurring pattern of explicitly discarding it. Errors formatted for users
contain a substring like "Unknown variable: foo" with no line/column attached,
even though every call site knows the line and column. Fixing this requires
adding `SourcePos` (or `List SourcePos`) to every `*Err`/`Diagnostic` ADT
and propagating it at construction.

### 6. Data-last argument order in public APIs

Status: partial.

Good:

- The prelude is the model citizen. All collection operations consistently
  place the collection last: `vec_get(index, vec)`,
  `vec_set(index, value, vec)`, `vec_map(f, vec)`, `vec_filter(pred, vec)`,
  `vec_fold(f, init, vec)`, `vec_slice(start, count, vec)`,
  `dict_get(key, dict)`, `dict_set(key, value, dict)`,
  `dict_remove(key, dict)`, `list_map(f, xs)`, `list_fold(step, init, xs)`,
  `filter(pred, xs)`, `result_map(f, r)`, `result_and_then(f, r)`,
  `set_insert(item, s)`, `set_member(item, s)`, `set_remove(target, xs)`.
- Pipe-style integration: prelude functions like `pipe_apply`, `rcompose`,
  `lcompose`, `result_pipe` are explicitly built around the convention.

Gaps:

- Compiler pipeline entry points invert the convention by putting the
  receiver (the program) **first** rather than last:
  - `codegen.sprout:4067` `compile_to_ir(prog, env)` — data first.
  - `codegen.sprout:4081`
    `compile_to_ir_lines(prog, env, debug_mode_, source_file_)` —
    data first, auxiliary last (and trailing `Bool` param compounds #3).
  - `lowering.sprout:28` `lower_program(prog, env)` — data first.
  - `checker.sprout:42,48,62` `check_program(prog)`,
    `check_program_with_env(prog, extra)`,
    `typecheck_typed(prog, extra)` — data first.
  - `bundler.sprout:1068,1124` `bundle_file(path, stdlib_root)`,
    `dump_qualify_file(path, stdlib_root)` — path is the data,
    `stdlib_root` is the auxiliary; order is inverted.
- Parser helpers `parse_X(tokens: Vec, i: Int)` (parser.sprout:155, 255,
  486, 1447) place tokens first. Defensible because `i` is a cursor paired
  with `tokens`; consistency across the file is high. Lower priority.

Impact:

Prelude callers can write idiomatic pipelines:
`vec |> vec_filter(pred) |> vec_map(f) |> vec_fold(step, init)`. Compiler
pipeline callers cannot — a `|>` pipeline of
`bundle |> check |> lower |> codegen` would need `(env)`-style flipping.
This is a real ergonomic loss at the seams that future drivers will care
about.

## Recent Positive Movement

- `typed_ast.TypedExpr` and `lower_program` both preserve types now — the
  observability report's #5 (type survival into typed core) is closer to
  green, and #4 (parse-don't-validate at the typed-IR boundary) benefits.
- `lowering.sprout` is fully pure (0 `!{IO}`) — proof point that large
  passes can avoid IO contamination.
- `Effect` ADT is well-encoded; `BodyResult / TypedDeclResult /
  InstanceMethodResult` use sum types instead of nullables.

## Recommended Next Steps

1. **Span every error ADT.** Replace `*Err String` payloads with
   `*Err (List SourcePos) String` (or a typed `ErrorKind` ADT plus span).
   Start with `Diagnostic`, then `InferErr`, then percolate to
   `CheckErr / BundleErr / LowerErr`. The conversion functions
   `lex_err_diag`/`parse_err_diag` in `compiler.sprout:67–73` and the
   `InferErr` construction sites in `infer.sprout` are the highest-leverage
   targets — they already have the position in scope.

2. **Eliminate `parse_int`'s partiality.** Add
   `extern fn try_parse_int(s: String) -> Maybe Int` (or `Result`), make
   `parse_int` a deprecated alias, migrate call sites in the compiler and
   stdlib, then remove the panicking variant.

3. **Replace driver `panic(read_file …)` calls with proper error
   surfacing.** Six drivers panic on read failures even though
   `read_file` returns `Result`. Convert each to return its own
   driver-level `Result` and let `main` print and exit non-zero.

4. **Introduce a `ResolvedProgram` phase-distinct type.** Either a fresh
   `resolved_ast` module or a phantom-param `Program QualState`. This
   directly closes the guideline's named #3/#4 exemplar gap and prevents
   double-qualification bugs.

5. **Rework compiler pipeline entry points to data-last.** Either flip
   `compile_to_ir(env, prog)` / `lower_program(env, prog)` /
   `check_program_with_env(extra, prog)`, or accept the current order as
   a deliberate deviation and document the rationale in the guidelines.

6. **Audit boolean parameters in formatter and codegen.** Where the
   same `Bool` flag threads through ≥3 helpers (e.g.
   `has_equals_ahead`, `debug_mode_`), replace with a named ADT.
   `is_function_scheme`/`is_polymorphic_scheme` are exported and should
   become a single classifier returning a `SchemeShape` ADT.

## Overall Rating

The codebase upholds the guidelines' spirit in its data model and in the
prelude, but falls short on systematic propagation through the compiler
pipeline. Guideline #5 (errors carry source locations) is the dominant
shortfall: positions are computed at every site and discarded at the next
type boundary. Guideline #6 (data-last) is observed by the prelude and
inverted by the compiler. Guidelines #1, #3, and #4 are partial: the typed
IR has improved, but qualify- and lower-time invariants remain
convention-only.

The prelude is the strongest area; the compiler pipeline's error types
and entry-point signatures are the weakest.

---

## Proposed Guidelines Additions (v1 Candidates)

The six basics in `docs/guidelines.md` have held up well, but recurring patterns
in session retrospectives (`retros/`) and the long-standing memory entries point
to several authoring rules that would have prevented concrete bugs. Each
proposal below cites a specific retrospective paragraph; brainstorm items
without a retro anchor were intentionally cut. Priorities reflect how often
the same shape of bug recurred, not how hard the rule is to state.

---

### 1. Type-encoded post-conditions for staged operations

**Motivation.** `retros/2026-05-30-1802.md` documents a SIGBUS where every
caller of `infer_expr` is expected to remember to call
`apply_subst_typed_expr(s1, typed_expr)` on the returned `InferOk` before using
the result. The `LetDecl` branch forgot, and the failure mode was a corrupted
typed AST that only crashed in the REPL. There is no compiler or type-level
mechanism preventing the omission — it is convention written nowhere.
This is `parse-don't-validate` (basic #4) applied to *internal* APIs: when a
returned value is only safe after a follow-up step, fold the step into the
function and return the post-condition type.

**Proposed rule.** When a function returns a value that requires a fixed
post-processing step before it is safe to consume, perform that step inside
the function and return the post-condition type. If the unprocessed value is
also needed elsewhere, expose two functions with distinct names — never a
single function whose return type cannot tell the two states apart.

**Examples.** `stdlib/compiler/infer.sprout` `infer_expr` should be private and
a public `typecheck_expr` should apply the substitution before returning (the
fix proposed in the retro). `stdlib/compiler/codegen.sprout` `FnSig` is the
mirror case: the constructor accepts `(name, param_lls, ret_ll, _)` with no
guarantee that `list_length(param_lls) == arity(name)`, and the CPR retro
(`retros/2026-05-28-cpr-bootstrap-fix.md`) shows what happens when callers
construct it directly.

**Priority.** High.

---

### 2. Document non-obvious invariants at the definition site

**Motivation.** `retros/2026-05-29-constrained-dict-injection-cascade.md` is
the canonical case: `resolve_obligation` has a four-step priority cascade
(scenarios A/B/C/D) where reordering the steps silently breaks scenario B
even when the test suite for scenarios A and C still passes. A prior session
reordered the cascade with no comment warning that the order was load-bearing.
`retros/2026-05-30-0037.md` documents the same shape for the `@fwd:` vs
`@eta_fwd:` key-prefix convention — the per-function vs global-env split was
load-bearing across two passes but written nowhere. Memory entry
`feedback_document_abi_invariants` already records this lesson for the runtime
ABI; it deserves to be promoted to a coding guideline.

**Proposed rule.** Any structural or ordering invariant that is not visible
from a single function body — priority cascades, paired-list length invariants,
key-prefix conventions, ABI bit-layout choices — must be documented in a
header comment at the definition site. The comment enumerates the scenarios
with explicit "do not reorder without verifying X, Y, Z" language. Git history
is not a substitute; future agents do not read it.

**Examples.** The cascade comment proposed in the constrained-dict retro is
the template:
```
# RESOLUTION CASCADE — do not reorder without verifying all three scenarios:
# A. Container-wrapped (Eq (Maybe a)): scan_ptf_for_prog_var ...
# B. Polymorphic forwarded (Foldable f in join->fold): resolve_via_fwd ...
# C. Concrete direct (Eq Int for assert_eq): scan_prog_to_fresh_for_instance
# D. Final fallback: resolve_one_constraint_tdict
```

**Priority.** High.

---

### 3. No silent fallback in shared helpers

**Motivation.** `retros/2026-05-28-cpr-bootstrap-fix.md` traces three of the
four bootstrap-breaking bugs to `extract_param_lls_from_type` returning `Nil`
on an input shape (`TVar`, or a constrained scheme with hidden dict args) that
it could not handle. Callers consumed the empty list as if it were valid,
producing LLVM functions with zero parameters and store-into-i64 instructions
that LLVM interpreted as `store i64 0`. Basic #2 says stdlib does not expose
partial functions, but it reads as a public-API rule; the CPR retro shows
internal helpers leaking the same failure mode — silent degradation is worse
than crash because the bad value propagates undetected.

**Proposed rule.** Internal helpers may not return a degraded sentinel (empty
list, default-zero, `TConst("?")`) for inputs outside their contract. Either
narrow the input type so the case is unrepresentable, return `Maybe`/`Result`
so callers must handle the gap, or panic with a useful message naming the
helper and the unhandled shape. Silent fallback in a helper used across the
codebase is a latent regression generator.

**Examples.** The fix pattern already present in `emit_lambda_expr` is the
template: extract types, check `list_length(raw) == n_params_`, and either
proceed or fall back explicitly with the failure visible at the call site
(`stdlib/compiler/codegen.sprout`).

**Priority.** High.

---

### 4. Every pipeline pass ships with an env-gated trace hook

**Motivation.** `retros/2026-05-29-constrained-dict-injection-cascade.md`
notes that most of two sessions' investigation time was spent mentally tracing
"which cascade path fired for which constrained call." A
`--trace-dict-injection` flag emitting one line per call (`[dict] fold
Foldable f -> fwd: f`) would have collapsed hours into seconds.
`retros/2026-06-09-1436.md` repeats the lesson for codegen: adding sub-phase
timing required a full seed refresh cycle just to answer "is `emit_all_fns`
the cost?". Memory entry `feedback_tooling_during_investigation` records this
as a meta-principle. Observability guard rail #2 (`docs/observability-guard-rails.md`)
already requires explicit named passes; this guideline extends it to require
explicit *instrumentation*.

**Proposed rule.** A pipeline pass without an env-gated tracing hook is
incomplete. New passes ship with a guarded `if env_get("SPROUT_TRACE_<pass>")
...` line emitting structured one-line-per-event diagnostics. The cost is
trivial; the payoff is the first time anyone has to debug the pass without
rebuilding the seed.

**Examples.** `SPROUT_TIME_PHASES` is the existing template (`compile_driver.sprout`);
extend the same pattern per new pass — e.g. `SPROUT_TRACE_DICT_INJECTION`
in `resolve_obligation`, `SPROUT_TRACE_ETA` in `try_eta_in_class`.

**Priority.** High.

---

### 5. Promote observability guard rails #2 and #3 into the coding guidelines

**Motivation.** `docs/observability-guard-rails.md` items #2 (pipeline stages
are named typed explicit functions) and #3 (effectful capabilities are passed
explicitly, not captured globally) are written as aspirational design
constraints, but they describe day-to-day authoring discipline that applies
every time someone adds a new compiler pass or threads a logger.
`retros/2026-06-09-1436.md`'s timing-instrumentation pain shows what happens
when capabilities (here, a clock/log sink) are not explicit arguments —
adding them retroactively required hoisting let-bindings, rebuilding the seed,
and reverting a batch-mode design. Memory entry
`project_observability_guard_rails` flags both rules as load-bearing.

**Proposed rule.** Cross-reference rules #2 and #3 from
`docs/observability-guard-rails.md` into `guidelines.md`: every new pass is
a named top-level function with explicit typed input and output; every
effectful capability (log sink, debug hook, clock) is passed as an explicit
function-value argument rather than read from global state. Restate the rule
in one sentence; defer to the guard-rails doc for rationale.

**Examples.** `check`, `lower`, `emit_program` in `stdlib/compiler/` already
follow this; new passes inherit the pattern.

**Priority.** Medium.

---

### 6. Complexity comment on every new prelude export

**Motivation.** Memory entries `feedback_prelude_perf_docs` and
`project_str_slice_codepoint_cost` document the cost of missing complexity
annotations: `str_slice` is O(source) because it walks the codepoint index,
and replacing a per-char accumulator with `walk + str_slice` in a hot lexer
loop silently produced O(n^2) behavior. The convention `# O(...)` is already
established on existing exports in `stdlib/prelude.sprout` but is not enforced
on new code, so it drifts.

**Proposed rule.** Every new `export fn` and every typeclass instance method
in `stdlib/prelude.sprout` carries a `# O(...)` comment matching the existing
convention. Document the dominant operand explicitly when it is not obvious
(`# O(len(haystack))`, not `# O(n)`). For typeclass methods, document
complexity per instance; the class signature alone is insufficient.

**Examples.** `string_concat_many` (`stdlib/prelude.sprout`) is the existing
template. `str_slice` is the cautionary tale — its O(source) cost was not
documented at the call site, and a hot-path rewrite picked it as a "cleaner"
alternative.

**Priority.** Medium.

---

### 7. Smart constructors via assertion when private constructors are unavailable

**Motivation.** The current "Deferred to v1" list parks smart constructors
behind the trigger "a private-constructor language feature." But the evidence
from `retros/2026-05-28-cpr-bootstrap-fix.md` is that the cost of *not* having
them is already being paid — `FnSig(name, param_lls, ret_ll, ret_sig)` is
constructed at three sites with mismatched `list_length(param_lls)` versus the
actual function arity, and no language feature is needed to make a canonical
`mk_fn_sig` function that asserts the invariant.

**Proposed rule.** When a record/ADT has a non-obvious co-invariant between
its fields, expose a single canonical builder (e.g. `mk_fn_sig`) that asserts
the invariant and route all construction through it. Document the raw
constructor as "internal — use `mk_*` instead." This is the v0-feasible half
of the deferred smart-constructor item; the v1 language feature only adds
*enforcement* of a convention that should already be followed.

**Examples.** `FnSig` in `stdlib/compiler/codegen.sprout` is the immediate
candidate. `InferOk` in `stdlib/compiler/infer.sprout` is the second — the
substitution-applied invariant from proposal #1 is enforced by routing
construction through `typecheck_expr` instead of `InferOk` directly.

**Priority.** Medium.

---

## What was considered and cut (v1 candidates)

- **"Search all call sites before changing a shared helper"** (CPR retro) —
  workflow discipline, not code authoring. Belongs in AGENTS.md if anywhere.
- **"Run compile-examples before just test"** (CPR retro) — DoD ordering,
  already partially captured in memory `feedback_examples_dod`.
- **Test consolidation for cascade scenarios** (constrained-dict retro) —
  testing strategy; the AGENTS.md TDD discipline already covers it.
- **Haskell lessons #1–#13** — most are language-design ammunition (lazy,
  String-as-list, partial Prelude). Basic #2 already absorbs lesson #5;
  lesson #12 is covered by the "explicit is default" memory. No new
  code-authoring content beyond what the basics already say.
- **Naming convention for partial wrappers** — current "Deferred to v1"
  trigger ("first stdlib case where a partial and total sibling both exist")
  has not been hit. Leave deferred.
