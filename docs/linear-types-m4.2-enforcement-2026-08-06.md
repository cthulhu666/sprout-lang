# Linear-types M4.2 — consume-exactly-once enforcement (with M4.3 convergence merged)

**Status:** design record, pending implementation. Supersedes the M4.2/M4.3 split in
`docs/gc-rooting-model-c-plan-2026-06-02.md` (§Milestone 4) — the two are merged here per the
scope decision (Kuba, 2026-08-06): implement full per-path convergence in one milestone rather
than shipping a branch-free way-station.

Builds on M4.1 (`docs/linear-types-m4-scoping-2026-08-01.md`, merged PR #22): `type linear`
parses and records a `@linear:<TypeName>` sentinel in the scheme env. M4.1 records; M4.2
enforces.

## 1. Problem statement

A value of a `type linear` type must be **used exactly once** in a function body — not zero
times (a leaked resource), not twice (a use-after-consume). M4.1 accepts but does not enforce:
`tests/stdlib/test_linear_type_decl.spr` reads a linear record field twice and compiles. M4.2
makes that an error.

## 2. Goals / non-goals

**Goals**
- Enforce consume-exactly-once for **every** binder that can hold a linear value: function
  parameters, do-block `let`, **match-arm pattern variables** (a var pattern aliases the whole
  linear scrutinee; a constructor/tuple sub-pattern binds a linear field — the "viral" case),
  and **`<-` do-bind** variables. Across all control flow (`if`, `match`) with full
  **convergence** checking.
- Enforce it at **every body-bearing declaration**: `fn`, top-level `let`, and instance methods.
- Precise diagnostics: reuse (used twice), leak (used zero times), branch divergence (consumed
  on one path but not another).
- Keep linearity **out of type inference** — the analysis is a post-pass over the typed AST,
  not new machinery in `infer_expr`.

> **Correction (2026-08-06).** The first cut of this milestone tracked only function-param and
> do-`let` binders and ran only on `fn` bodies. An adversarial code review found that a linear
> value aliased through a match var-pattern (`let g = f in …`, which desugars to one), a do-bind,
> or a lambda param — or misused in a top-level `let`/instance-method body — silently bypassed
> the check. The scope above (all binders, all bodies) is the corrected, sound version. Binder
> types are recovered by structurally matching the pattern against the value type
> (`linear_check.pattern_linear_binders`). Lesson: test *aliased* misuse, not just direct.

**Non-goals (deferred, stated plainly)**
- **Higher-order linearity.** A linear-typed **lambda parameter** and a linear binding
  **captured by a lambda** are deferred (M4.4). A closure may be invoked 0..n times; tracking
  that is the known-hard higher-order case (Austral's "loop rule" analogue). M4.2 **loud-rejects**
  a linear binding captured by a lambda body, and a linear lambda parameter, with a
  "not yet supported" diagnostic — it never silently accepts.
- **Containment virality.** Austral makes a type linear if it *contains* a linear field. Sprout
  M4.1 marks linearity **per-declaration only** (the head type's `@linear:` marker). A record
  with a linear field is **not** automatically linear in M4.2. This is a deliberate
  simplification; virality is a separate future item.
- **`wrap linear`**, polymorphism-over-linear-types, and promotion of the spec section from
  experimental → normative.

## 3. Prior-art (primary-sourced)

The M4.1 doc's survey (Austral / Linear Haskell / Idris 2 / Clean / Rust) stands. The rule
M4.2 implements is **Austral's**, verified against the Austral spec
(<https://austral-lang.org/spec/spec.html>), quoted verbatim:

- Core: *"a value of a linear type must be used once and only once. Not can: must."*
- Branches: *"a variable of a linear type, defined outside an `if` statement, must be used
  either zero times in that statement, or exactly once in each branch."*
- Loops: *"a variable of a linear type, defined outside a loop, cannot appear in the body of
  the loop."* (Sprout has no loops; the analogue is closure capture — deferred, see §2.)

Sprout deviates from Austral in one deliberate way: **per-declaration linearity, not
containment virality** (§2). Everything else — exactly-once, branch convergence — is Austral's
discipline.

## 4. The algorithm: consumed-set analysis over the typed AST

The checker's env carries resolved types only on **use** sites (`typed_ast.TVar name t pos`),
**not** on binders (`TLambda`/`TFnDecl` params are raw `ast.Param`; pattern vars are raw
`ast.Pattern`). So the pass is **hybrid**:

1. **Collect** the linear bindings in scope — done during checking, where types are live, at the
   single `bind_local_scheme` funnel (`infer.sprout:813`), filtered to exclude the function's
   own self-name (`fn_body_env` self-binds through it) and desugar temporaries. Linearity is
   resolved against the **final** substitution (a param's scheme is a metavariable at bind time),
   via `type_head_str(t)` → `type_name_is_linear(head, env)`.
2. **Count** uses via a post-pass over the typed AST, modeled structurally on `dce.is_free`
   (`dce.sprout:101`). The pass computes, for each subexpression, the **set of linear bindings it
   consumes**.

**Composition rules** (this is the whole discipline):

- **Leaf** `TVar x`: `{x}` if `x` is a linear binding, else `{}`.
- **Field access** `TGetField base f`: the base of `p.x` is a `TVar p` in the typed AST
  (Sprout has no record patterns; `p.x` is `GetFieldExpr(VarExpr p, "x")` synthesized in
  inference). So `p.x` consumes `p` — reading a field *is* a use. **Consequence:** a linear
  record can be read one field or passed once, but `p.x + p.y` is a reuse error (there is no
  record-destructuring escape hatch — records have no pattern form). Linear **ADTs** are
  unaffected: `match f with | File n -> n` consumes `f` once via the scrutinee. This is a
  documented limitation (loud error, not a silent gap); borrowing / record patterns would lift
  it and are deferred. For a value meant to be read freely, don't declare it `linear`.
- **Sequential** children (`TCall` args, `TTuple`, `TBinary`, `TUnary`, `TRange`, `TRecord`,
  `TRecordUpdate`, `TGetField`, and successive `TDo` steps): consumed sets must be **disjoint**
  (a binding in two siblings = used twice → reuse error). Result = their union.
- **Alternative** children — the arms of `TIf c t e` and `TMatch scrut arms`: `c`/`scrut` compose
  **sequentially before** the arms; the arms must all consume the **identical** set of
  outer-scope linear bindings (**convergence**). Divergence (a binding consumed in one arm but
  not another) → error. Result = `consumed(c) ∪ consumed(arm)`.
- **Binding scope** (function body over its params; `TDo` tail over a do-`let`/`do`-bind var;
  match arm over its pattern-bound linear vars): each linear binding introduced must appear in
  the scope's consumed set **exactly once** — absent → leak error; the disjointness rule already
  precludes >1 on any single path.

Because "exactly once per path" holds within a path (disjointness gives ≤1; scope-membership
gives ≥1) and convergence makes all paths agree, the property is **consume-exactly-once on every
control-flow path** — Austral's rule.

**Pass ordering:** runs on the **pre-DCE** typed AST (DCE could drop a use and skew the count).
Invoked from the declaration-checking path after `check_fn_body` yields its `BodyResult`, before
any dead-code pass.

## 5. Syntax / semantics impact

No syntax change (M4.1 owns the surface). Semantics: a program that reused/leaked a linear value
and previously compiled now fails to type-check. New rejections only — no accepted program
changes meaning.

## 6. Type-system impact

None to inference/unification. The analysis is a separate post-pass reading resolved types off
the typed AST; `infer_expr`'s recursion is untouched (the parent plan's #1 flagged risk is
sidestepped, same principle as M4.1's sentinel).

## 7. Error-message impact

Three new diagnostics, each pointing at the offending position:
- **Reuse:** `linear value 'f' is used more than once (linear values must be used exactly once)`
  — at the second use.
- **Leak:** `linear value 'f' is never used (linear values must be used exactly once)` — at the
  binding site.
- **Branch divergence:** `linear value 'f' is used in some branches but not others (every branch
  must use it exactly once)` — at the `if`/`match`.
- **Deferred (loud):** `linear value 'f' captured by a lambda is not yet supported` /
  `linear lambda parameters are not yet supported`.

## 8. Compatibility / migration

Backward-compatible except for programs that were already misusing linear types — which only
exist behind the M4.1-experimental `type linear` syntax. The one in-tree case is
`test_linear_type_decl.spr` (§9).

## 9. Tests

- **Convert** `tests/stdlib/test_linear_type_decl.spr` — it currently *asserts* read-twice
  works; that behavior is now an error. Rewrite it to a correct single-use program (positive),
  and move the read-twice case to a negative fixture.
- **Conformance `type_error/`** pairs (`.spr` + `.err`): reuse (twice), leak (zero), branch
  divergence (`if c then use(f) else ()`).
- **Conformance `run/`** positive: branch convergence OK (`if c then use(f) else use(f)`),
  match-scrutinee use, single straight-line use.
- **Cross-module:** an imported `type linear` is still enforced (exercises the M4.1
  `iface_codec` round-trip reaching the post-pass env).
- **Coverage:** the touched files gain cases per the coverage-gap rule.

## 10. Docs / spec

- **`docs/spec-v0.md`:** add the linear-types section that M4.1 committed to but omitted —
  syntax (M4.1) + enforcement semantics (M4.2), marked **experimental**. This closes the M4.1
  spec-DoD gap.
- This doc is the M4.2 design record.

## 11. Implementation sequence (TDD-first)

1. Failing tests first (the converted `test_linear_type_decl` negative case + one reuse
   fixture), RED-verified against the M4.1 seed.
2. New module `stdlib/compiler/linear_check.sprout` (the consumed-set pass) — keeps it out of the
   already-large `infer.sprout`; mirrors `dce.sprout`'s standalone typed-AST-walk shape.
3. Collect hook at `bind_local_scheme` (or a thin wrapper) recording linear bindings; wire the
   pass into the decl-check path pre-DCE.
4. Diagnostics per §7.
5. Spec section (§10).
6. DoD (compiler-source ordering): fmt + lint → refresh-seed (delete stale stage binaries) →
   full `just test` → smoke/bundle → fixed-point → example canary.

## 12. Risk

The plan rates this High (M4.3 convergence "touches inference; expect iteration"). Mitigation:
the post-pass design keeps it *out* of inference, and the consumed-set algorithm is small and
well-understood (Austral ships it in ~600 lines total for a richer language). The likely
iteration is in the collect-hook filtering (self-name, desugar temporaries) and getting the
match-arm pattern-var scoping right.
