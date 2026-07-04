# Currying and Pipe Semantics — Decision (v1)

**Status:** Proposed / undecided. This is a decision-support document, not normative. It
frames a single coupled language decision and the mechanics needed to rule on it. Nothing
here is implemented; `docs/spec-v0.md` remains the source of truth for current behavior.

**Owner decision required:** is Sprout a *curried* or an *n-ary* language? Everything below
follows from that one answer.

---

## 1. Problem statement

Sprout's function-application model is currently **incoherent**: the type system is fully
curried while codegen is only partly so. Concretely:

- The type representation is curried — `TFunc(param, ret, eff)` — and application typing
  (`infer.infer_call_resolve`) threads currying correctly. `add3(1)(2)(3)` typechecks.
- Codegen handles saturated calls and argument-position partials, but **miscompiles**
  incremental application of a partial with two or more remaining arguments.

This split produced two defects and intersects one open pipe question:

- **Defect #1 (FIXED).** Functions whose declared return type is itself a function type were
  rejected (`Return type mismatch: Int -> Int vs Int`) because `check_fn_body` /
  `check_instance_method` computed the expected return via `ctor_result_type`, which strips
  *all* arrows. Fixed in `infer.sprout` via `fn_return_type` (strips exactly `n_params`
  arrows). Regression: `tests/stdlib/test_function_returning_function.spr`.
- **Defect #2 (OPEN).** Incremental application of a partial closure with ≥2 remaining args
  SIGSEGVs. `add3(1)(2)(3)` typechecks, but codegen builds an arity-2 partial closure
  (`__sprout_partial_N(env, a0, a1)`) and the next call site supplies a single argument,
  under-saturating it; the malformed call returns an `Int` that the following application
  reinterprets as a closure pointer (`inttoptr` + `load`) → crash. Saturating in one call
  (`feed_two(add3(1), 2, 3)`) works — only *incremental* application is affected.
- **Backlog item 12 (OPEN).** The `|>` operator has two modes: `x |> f` ≡ `f(x)`, but
  `x |> f(a, b)` desugars *at parse time* to `f(a, b, x)`. The multi-arg mode is a special
  parser case flagged as "not compositionally obvious."

The thesis of this document: **#2 and #12 are one decision, not two.** Choosing the currying
model determines the pipe's resolution. Fixing the crash falls out of whichever model is
chosen — the crash is a symptom of the incoherence, not an independent bug.

---

## 2. Goals and non-goals

**Goals**
- Choose a single, coherent function-application model and make the whole stack consistent
  with it (type system, codegen, pipe).
- Eliminate the Defect #2 crash as a consequence of that coherence — a clean error or a
  correct result, never a segfault.
- Resolve the `|>` multi-arg semantics (backlog #12) in the same stroke.
- Preserve pipe ergonomics and keep faith with Sprout's beginner-friendly identity.

**Non-goals**
- Changing the data-last argument convention (Guideline #6). It is correct under *both*
  models and is the hinge that keeps pipe *meaning* stable regardless of choice.
- Touching the effect system, records, or any unrelated surface.
- Implementing anything in this pass. No proof-of-concept. This document exists to make the
  decision rulable on mechanics rather than intuition.

---

## 3. Evidence grounding the decision

Gathered empirically before framing the options:

1. **The type system is already fully curried and internally consistent.** `TFunc(param, ret)`
   is curried; partial application types correctly. Going n-ary means *un-building* part of a
   working type system, not extending an unfinished one.
2. **The self-hosted compiler uses zero partial application.** An audit of the full compiler
   IR (3043 `define` blocks) found **no** `__sprout_partial_N` wrapper definitions — every
   "partial" symbol is the compiler's own code that *emits* partials, or a string literal. The
   compiler's higher-order style is 229 explicit lambdas (`\x -> ...`) plus ~444 eta /
   function-as-value wrappers (bare function references, zero args pre-applied) — both of which
   work identically under either model. Where a curried-language programmer would write
   `map(add(1), xs)`, the compiler consistently writes `map(\x -> add(1, x), xs)`.
   *Caveat:* this is partly survivorship — partial application was broken/fragile, so code
   routed around it. It proves n-ary is *sufficient* for real Sprout code; it says nothing
   about whether currying would be *valued* if it worked.
3. **The pipe does not depend on partial application.** `x |> f(a, b)` desugars at parse time
   to a *saturated* call `f(a, b, x)`. Sprout deliberately achieved pipe-friendliness via
   data-last + parser sugar rather than currying — the single most common motivation for
   currying in ML-family languages was engineered around.
4. **The two closest precedents split deliberately.** Elm is fully curried and beginner-first
   (bets on good diagnostics). Roc (Elm-lineage, same audience) rejected currying outright in
   favor of n-ary functions and call-site arity errors. There is no consensus answer for a
   beginner-friendly typed FP language.

---

## 4. The decision: two coherent packages

Each package resolves currying **and** the pipe in one consistent design.

| | **Package A — Curried** | **Package C-a — N-ary** |
|---|---|---|
| Functions | `f(a)` on an n-arity fn yields a partial; saturated calls stay direct (fast); under-saturation builds a partial | No partials; function types arity-aware; under-application is a compile error |
| `x \|> f(a, b)` | Ordinary application of the partial `f(a, b)` to `x`; **delete the special multi-arg parser mode**; operator collapses to one rule `x \|> g` ≡ `g(x)` | Stays parser sugar (`f(a, b, x)` at parse time); two-mode shape inherent; resolved only syntactically |
| Backlog #12 | Resolved by unification | Stays a standalone syntactic question |
| Pipe *meaning* today | Unchanged (data-last makes `(f(a,b))(x)` = `f(a,b,x)`) | Unchanged |
| Ergonomic ceiling | `andMap`/`<*>`, `compose`, `flip`, point-free | `map2`/`map3` + explicit lambdas |
| Beginner errors | "forgot an arg" → confusing function value downstream (needs diagnostic) | "forgot an arg" → clean arity error at the call site |
| Main cost | GC-rooting of intermediate partials + closure-ABI change | Type-system rework to arity-aware; currying gone permanently |
| Migration risk | Low (nothing uses partials) | Low (compiler proves lambdas suffice); removes argument-position partials |
| Precedent | Elm, OCaml, GHC | Roc, Scala (methods) |

**Coupling insight.** Package A collapses two open design surfaces (currying *and* pipe #12)
into one resolved answer. Package C-a leaves two surfaces standing — arity-aware functions
*and* a parser-sugar pipe whose two-mode shape must be separately ruled on. Fewer independent
design surfaces is itself an architectural argument for A, visible only once the decisions are
coupled.

---

## 5. Syntax and semantics impact

**Package A.** No surface syntax changes. `f(a, b)` stays multi-arg-application sugar for
curried application; `f(a)` becomes a legal expression producing a partial. The pipe's
multi-arg parser special-case is *removed* — `x |> f(a, b)` becomes plain application, meaning
unchanged. Over-application (`(f_that_returns_a_fn)(x)(y)`) becomes well-defined.

**Package C-a.** `f(args)` must be saturating. `f(a)` on a 2-arg `f` becomes a static error.
Argument-position partials that work today (`apply3(add(5))`) are removed. The pipe keeps its
parse-time desugaring; backlog #12 is then resolved by one of: keep-as-is, Elixir value-first
(`x |> f` ≡ `f(x, ...)`), or require explicit lambdas for the multi-arg case.

---

## 6. Type-system impact

**Package A.** *None to the type representation* — it is already curried. Work is confined to
codegen/runtime (Section 8). Application typing already curries correctly.

**Package C-a.** The type system must become **arity-aware**: distinguish an n-ary function
`(a, b) -> c` from a curried `a -> b -> c` so the checker can reject under-application with a
precise message. This is the larger type-system change of the two and contends directly with
the curried `TFunc` representation — likely a distinct n-ary function type constructor, or an
arity tag on `TFunc`, plus application-site saturation checks.

---

## 7. Error-message impact

**Package A.** Under-application is not an error — it yields a function value. The risk is a
beginner who forgot an argument gets a downstream type mismatch far from the mistake. This must
be mitigated with a first-class diagnostic (a *deliverable*, not an afterthought): when an
expression's inferred type is a function and the surrounding context expected a non-function,
emit "this expression has type `X -> Y` — did you mean to apply it to another argument?".

**Package C-a.** Native win: under-application is "this function needs N arguments, you gave M"
at the exact call site — the friendlier error, and a reason Roc chose this model.

---

## 8. Package A implementation sketch (closure ABI + GC rooting) — the tiebreaker

The costs are symmetric enough that the decision hinges on one question: **is GC-rooting the
intermediate partials tractable given Sprout's rooting discipline?** This section answers it at
the design level. (No PoC in this pass; a rooting-stress PoC is the recommended first
implementation step if A is chosen.)

### 8.1 Model: eval/apply

Adopt the eval/apply convention (OCaml `caml_apply`, GHC PAP objects), not push/enter. Rationale:
saturated calls — the overwhelming common case, and the *only* case Sprout emits today — stay a
direct call with no arity check and no allocation. The generic machinery is reached only on
genuine under-/over-application.

### 8.2 Closure representation

Today: a closure env is a heap block with the code pointer at slot 0 and captures at slots 1..n;
arity is *implicit* in the wrapper's LLVM signature, and the apply site simply calls with
whatever arguments it has (the root cause of Defect #2).

Proposed: the closure carries an explicit **remaining-arity** field (a header word alongside the
code pointer). The generic apply site can then compare supplied-args `k` against remaining-arity
`a`.

### 8.3 Apply protocol (generic apply of a closure value to `k` args)

- **`k == a` (saturate):** direct call `code(env, args…)`. Statically-known-arity *direct*
  calls skip the check entirely and remain today's direct LLVM call — the fast path is
  untouched.
- **`k < a` (under-apply):** allocate a new closure capturing the existing captures plus the `k`
  new args, code pointer set to a partial trampoline, remaining-arity `a - k`. Return it. This
  is the only *new allocation site*.
- **`k > a` (over-apply):** saturate with the first `a` args, obtain the result (which must be a
  function), then recurse: apply the result to the remaining `k - a` args.

### 8.4 GC-rooting analysis (the actual risk)

The new allocation in the `k < a` branch is a GC-triggering site on the *generic apply* path.
Rooting obligations:

- Before allocating the new env, every live value copied into it — the `k` incoming args and the
  source closure's captures — must be rooted so a collection triggered during allocation cannot
  reclaim them. This follows the existing discipline (root-once stack coalescing, PR #108;
  type-aware rooting per `compiler-internals.md`): push roots for the live set, allocate, copy,
  pop.
- The over-apply branch allocates nothing itself, but the intermediate result closure must be
  rooted across the recursive apply.
- The partial trampolines must root their captured slots before calling through — the existing
  `__sprout_partial_N` wrappers already demonstrate this pattern in emitted IR.

**Why the risk is more contained than "new allocations on a hot path" suggests:** the fast
(saturating) path allocates nothing and is unchanged. All new rooting obligations live on the
*under-/over-application* path, which is *cold* — the compiler exercises it zero times, and most
user code will too (`map2`-style saturated calls dominate). So the new rooting surface is real
but confined to a rarely-taken branch, which is favorable for correctness: the surface is small
and can be targeted directly.

**Mandatory validation if A is implemented:** the `SPROUT_GC_STRESS=1` oracle (`just
test-stress`) must be made to *force* the under-/over-application paths (default runs will not
hit them). A rooting bug here is a silent use-after-free — the worst failure mode for Sprout's
safety pitch — so the stress oracle covering these paths is a release gate, not a nicety. This is
why the recommended first implementation step is a rooting-stress PoC, not feature code.

### 8.5 Bootstrap sequencing

The closure ABI (arity field) is a coordinated runtime + codegen + seed change — the same
catch-22 class as builtin removal. Suggested PR sequence:

1. **Safety first:** add the arity field and make under-saturation a *clean panic* (not a build
   of a partial). This kills the Defect #2 segfault immediately and ships value on its own. It is
   the first half of A *and* a valid stopping point if the decision slips.
2. **Complete to A:** replace the panic with the build-partial branch. Full currying lands. The
   pipe's multi-arg special-case is removed in the same PR.

---

## 9. Package C-a implementation sketch

- **Types:** introduce arity-awareness (distinct n-ary function type or arity-tagged `TFunc`);
  application-site saturation check; precise arity-mismatch diagnostic.
- **Codegen:** the closure still needs an arity field so under-saturation is caught cleanly
  rather than miscompiled — i.e., C-a *also* pays for the arity field and the clean-panic step
  (Section 8.5, PR 1). The difference from A is purely that C-a *stops* there and additionally
  forbids the construct at compile time, whereas A continues to the build-partial branch.
- **Pipe:** unchanged mechanically; separately choose the #12 sub-option.

Note the consequence: **PR 1 of Section 8.5 (arity field + clean panic) is common to both
packages.** It kills the segfault regardless of the eventual A/C-a choice, and is therefore
safe to schedule *before* the decision is final.

---

## 10. Compatibility / migration

- **Package A:** pipe *meaning* is unchanged (data-last). No existing code breaks. New surface
  (partials) is additive.
- **Package C-a:** removes argument-position partials that currently work (`apply3(add(5))`).
  The compiler uses zero partials, but `examples/` and `tests/` have not been audited. **Pre-
  implementation action for C-a:** run the same `__sprout_partial_N` wrapper-count audit over
  `examples/` and `tests/` IR to bound the blast radius.

---

## 11. Recommendation

Lean **Package A (curried)**, held with one explicit contingency:

1. The type system is already curried and consistent; C-a partially un-builds it.
2. A retires backlog #12 for free and lifts the ergonomic ceiling, while data-last keeps the
   pipe's meaning identical to today.
3. Migration risk is low either way, so "the compiler needs no currying" does not favor C-a as
   much as it first appears — it means *neither* choice breaks existing code.

**What flips the recommendation to C-a:** if, on closer scoping, GC-rooting the partial-
allocation path (Section 8.4) looks genuinely hard to get provably right, then C-a's zero new
runtime risk plus call-site arity errors becomes the safer, on-brand choice, with Roc as proof
it suits this audience. The tiebreaker is rooting feasibility, not ergonomics.

---

## 12. Decision needed

1. **Curried (A) or n-ary (C-a)?** This is the ruling everything else follows from.
2. If **C-a:** which backlog #12 resolution — keep-as-is, Elixir value-first, or explicit-lambda?
3. Regardless of 1: schedule the common **arity-field + clean-panic** step (Section 8.5 PR 1 /
   Section 9) to kill the Defect #2 segfault now, since it is shared by both packages.

---

## 13. Tests to add on implementation

- **Common (PR 1):** the Defect #2 reproducer (`add3(1)(2)(3)`) yields a clean panic/error, not
  a segfault; `feed_two(add3(1), 2, 3)` still returns 6; GC-stress coverage of the new apply
  paths.
- **Package A:** incremental application (`add3(1)(2)(3)` → 6), over-application, partials stored
  in data structures then applied later, partials surviving a collection (stress), the pipe
  unification (`x |> f(a, b)` ≡ `(f(a,b))(x)`), and the "did you forget an argument?" diagnostic.
- **Package C-a:** under-application is a precise arity error (success/failure checker tests);
  the removed argument-position-partial forms are rejected with good messages.

---

## 14. Spec/docs updates on decision

- `docs/spec-v0.md`: normative statement of the chosen application model and (for A) the unified
  pipe rule or (for C-a) the arity rule + chosen #12 resolution.
- `docs/guidelines.md`: Guideline #6 (data-last) stays; add guidance on partials-vs-lambdas (A)
  or the no-partials idiom (C-a).
- `README.md` §Not Yet Supported: update once the crash is fixed and the model is chosen.
- `docs/backlog.md`: close items 9 and 12 with a pointer here.
