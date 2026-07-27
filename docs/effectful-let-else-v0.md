# Effectful-RHS `let..else` — refutable `<-` with an explicit `else`

**Status:** IMPLEMENTED (2026-07-27; surface **(A)** chosen — §5). The next
capability tier of the refutable-binding arc
(`docs/let-else-and-monadic-binding-plan.md`): it brings Tier 1b's residual-`else`
to the **effectful bind** (`<-`) inside a `do` block. Delivered as **pure
syntactic sugar** — a parse-time rewrite into constructs that already exist, with
no new typechecker/inference/effect/codegen rule.

---

## 1. Problem

The self-hosted compiler's inference code is full of the shape "run an effectful
step, bail on the error variant, thread the state forward":

```sprout
fn infer_range(..., lo: ast.Expr, hi: ast.Expr, pos) -> InferResult !{IO} =
  do
    lr <- infer_expr(state, env, subst, eff_subst, lo)
    match lr with
    | InferErr pos e -> InferErr(pos, e)
    | InferOk typed_lo s1 es1 _ ->
        do
          hr <- infer_expr(state, env, s1, es1, hi)
          match hr with
          | InferErr pos e -> InferErr(pos, e)
          | InferOk typed_hi s2 es2 _ ->
              match unifier.unify_types(s2, type_of(typed_lo), int) with
              | Err e -> InferErr(pos, "Range needs Int: " ++ e)
              | Ok s3 ->
                  match unifier.unify_types(s3, type_of(typed_hi), int) with
                  | Err e -> InferErr(pos, "Range needs Int: " ++ e)
                  | Ok s4 -> InferOk(TRange(typed_lo, typed_hi, ...), s4, ...)
```

Six levels of rightward drift, each `Err`/`InferErr` arm formatting its own
payload. Tier 1b (`else <pat> -> <h>`) is exactly the tool for the payload-carrying
arms — but it **cannot reach here**, for two independent reasons:

1. **`let..else` is pure-expression-position only.** The `infer_expr` steps are
   `!{IO}`; a pure `let` RHS is a type error (the Tier-1 rule). The effect must be
   sequenced with `<-`, which lives in `do`-statement grammar.
2. **`let..else` is unavailable inside a `do` block** — there, `let` is the
   effectful binding step. So even the *pure* `unify_types` sub-chain, nested in
   the `do`, can't use today's `let..else`.

The `staircase-of-doom` lint flags these sites but they are not Tier-1b-convertible;
this tier is what makes them flat.

## 2. Proposal

Allow a `do`-block bind to carry a **refutable pattern with an `else`**, reusing
Tier 1b's residual clause verbatim:

```
<pat> <- <e> else <fb>              -- constant else (short-circuit to a value)
<pat> <- <e> else <rpat> -> <h>     -- binding-else: <rpat> names the failing value
```

With it, `infer_range` becomes flat — and, because pure `=` bindings already work
in a `let` block, the pure and effectful steps can be read as one aligned sequence
(see §5 for the surface decision that enables this):

```sprout
fn infer_range(..., lo: ast.Expr, hi: ast.Expr, pos) -> InferResult !{IO} =
  do
    InferOk typed_lo s1 es1 _ <- infer_expr(state, env, subst, eff_subst, lo) else InferErr p e -> InferErr(p, e)
    InferOk typed_hi s2 es2 _ <- infer_expr(state, env, s1, es1, hi)          else InferErr p e -> InferErr(p, e)
    let Ok s3 = unifier.unify_types(s2, type_of(typed_lo), int) else Err e -> InferErr(pos, `Range needs Int: ${e}`)
        Ok s4 = unifier.unify_types(s3, type_of(typed_hi), int) else Err e -> InferErr(pos, `Range needs Int: ${e}`)
    in InferOk(TRange(typed_lo, typed_hi, ...), s4, ...)
```

## 3. Goals and non-goals

**Goals**
- A refutable `<-` bind with a constant or binding-`else`, inside `do` blocks.
- Pure sugar: front-end rewrite only, zero new typing/effect/codegen machinery.
- Works uniformly for `!{IO}` and for `do`-over-`Maybe`/`Result` (whatever `<-`
  already binds), because the sugar never inspects the effect.

**Non-goals (explicit — do not bundle)**
- **No-`else` propagate** (`<pat> <- e` auto-reinjecting the failure). This is *not*
  sugar: it requires the compiler to *construct* the failure (re-wrap `Err`, or call
  a monad's `fail`/`return`), which is type-directed and, for a general monad,
  genuinely gated on the effect-system design. Tier 2/3 of the plan doc.
- **Widening `let..in` to hold `<-` bindings.** The pretty "mixed `=`/`<-` block in
  expression position" reclassifies `let..in` from *always pure* to *sometimes
  effectful* — a semantics change (§5). This tier stays inside `do`.
- **User-defined monads / a `Monad`-generic desugar.** Scoped to `!{IO}` and
  concrete result ADTs (`Result`, `Maybe`, `InferResult`, …) matched explicitly.

## 4. Prior art (surveyed against primary sources; the base rows are re-used from
`let-else-and-monadic-binding-plan.md` §3, verified 2026-07-07)

| Language | Construct | Relation to this proposal |
|----------|-----------|---------------------------|
| **Haskell** | refutable `p <- m` in `do` → `MonadFail.fail` (Report §3.14) | Same "refutable monadic bind," but the refuted case is **implicit** and collapses to one `fail` value. Our explicit per-binding `else` is strictly more expressive — you name the failure. |
| **OCaml** | `let* x = e in b` binding operators | The monadic-bind precedent; irrefutable, no failure branch. |
| **Rust** | `let PAT = e else { … }` (diverging); `e?` | Explicit `else`, but pure/imperative and the else must diverge; `?` is the separate propagate form. |

Reading: the exact fusion — *refutable effectful bind with an explicit, optionally
payload-binding `else` per binding* — was **not found** as a single feature in the
surveyed languages. It is "Rust's `let-else` brought inside `do`/`<-`," with
Haskell's refutable-bind made explicit instead of `MonadFail`-implicit.

## 5. Syntax and the one real design decision

The construct itself is settled; the open decision is **where it lives**, and it is
a genuine semantics fork:

- **(A) `do`-local (recommended).** A `do`-block bind gains an optional `else`
  clause. Purely additive to `do`-grammar; `let..in` stays *always pure expression
  position* (its current spec guarantee is untouched). Cost: the mixed pure/effectful
  block is written with `do` + an inner `let..in` for the pure tail (as in §2), not
  one flat `let` list.
- **(B) `let..in` gains `<-` bindings.** The prettiest surface (one flat list mixing
  `=` and `<-`), but a block becomes effectful iff it contains a `<-`, which
  **reclassifies `let..in`** and rewrites the spec rule "every RHS must be pure."
  Larger blast radius; still sugar mechanically, but a real semantics widening.

**Decided: (A)** (Kuba, 2026-07-27). Rationale: it keeps effects syntactically
explicit (a `do` always marks where effects happen; under (B) a `let..in` could be
silently effectful), preserves `let..in`'s "always pure expression" spec guarantee,
and is reversible — (B)'s unification of `do` and `let..in` can be layered on later
if a concrete need appears, but two merged constructs can't easily be un-merged. (B)
is recorded as a possible future unification, not part of this tier.

## 6. Semantics — desugaring

A refutable bind with continuation `<rest>` (the remaining `do` steps) desugars to
an ordinary irrefutable bind of a fresh temporary, followed by a `match`:

```
<pat> <- <e> else <fb>            ~>   __t <- <e>
<rest>                                  match __t with | <pat> -> <rest> | _    -> <fb>

<pat> <- <e> else <rpat> -> <h>   ~>   __t <- <e>
<rest>                                  match __t with | <pat> -> <rest> | <rpat> -> <h>
```

Both target constructs already exist: `DoBindStep Pattern Expr` (which already
carries a full pattern, not just a name) and `MatchExpr`. Nothing else is emitted.

- **Exhaustiveness self-enforces through W5**, exactly as in Tier 1b: the residual is
  spliced verbatim into arm 2; a residual leaving cases uncovered is a
  non-exhaustive-match error. A refutable `<-` *without* `else` remains a
  non-exhaustive-match error (unchanged behaviour).
- **Effect timing is identical to the hand-written staircase**: `<e>`'s effect is
  performed exactly once, by the `<-`, *before* the branch. The `match`/`else` is
  pure control flow afterwards.

## 7. Type-system impact — none

The sugar introduces no inference rule. `__t <- <e>` is typed by the existing `<-`
rule; the `match` by the existing match rule; both arms unify to the block's result
type; a pure `else` value sits in effectful position by the existing effect
subsumption. This is the crux of the "pure sugar" claim.

## 8. Does it interfere with effects beyond `IO`? — No.

Sprout's effect representation is already a **row**, not a single flag:
`Effect = EffectPure | EffectIO | EffectRow (List String) | EffectVar String`
(`stdlib/compiler/types.sprout`). Only `EffectIO` is materialized today, but
`EffectRow labels` ("arbitrary named labels, future use") and `EffectVar` (open
variables, `!{e}`) are built in, and functions carry a row (`TFunc _ _ Effect`).

The sugar is **effect-agnostic by construction**:

1. It **never names or constructs an effect**. It writes no `IO`. It desugars to
   `<-` (the effect-row-*polymorphic* bind) + a value `match`.
2. Whatever row `<e>` carries — `!{IO}`, a future `!{State, Log}`, or a polymorphic
   `!{e}` — the `<-` propagates it into the enclosing `do`/function row exactly as an
   ordinary bind does. Adding effect labels changes `<-`'s typing (if at all); the
   sugar inherits that for free and needs no revision.
3. The `else` handler yields a *value* in the same effect context; subsumption of a
   pure value into any row is unchanged by the row's size.

The **only** place effect-sensitivity could leak in is the **no-`else` propagate**
variant (§3 non-goals), which must re-inject a failure into the effect/monad
context — and that is precisely why it is deferred to the effect-system design, and
excluded here. This tier commits to nothing about the effect algebra, so it neither
helps nor hinders adding effects beyond `IO`.

## 9. Error-message impact

Inherited: a refutable `<-` residual that isn't exhaustive produces W5's generic
"Non-exhaustive match" (with a source position), the same wording Tier 1/1b rely on.
No bespoke diagnostic is required for v0; a tailored "this `<-` needs an `else`"
message is optional polish.

## 10. Compatibility / migration

Purely additive. Today a refutable `<-` pattern is already accepted syntactically
(`DoBindStep` holds a `Pattern`) but yields a non-exhaustive match; adding the
optional `else` arm makes such binds *usable* without changing any existing program.
No file needs migration; the ~4 do-block staircases in `infer.sprout` (and the
9 `test_ir_codegen_*` compile-pipeline fixtures) become convertible follow-ons.

## 11. Implementation (as built — parse-time desugar, `stdlib/compiler/parser.sprout`)

`do` is threaded as a `DoExpr (List DoStep)` node through inference/lowering/codegen
(not desugared early), and — decisively — the compiler *already* emits and handles
the target shape (nested `do { __t <- e; match __t | pat -> do{rest} | err -> h }`,
which is what the hand-written staircase is). So the whole feature is a **parse-time
rewrite**, touching no downstream pass:

1. **Parse** (`parse_do_step_sub`): both `<pat> <- <e>` and `let <pat> = <e>` accept
   an optional `else` clause (reusing Tier 1b's `parse_let_else_clause` — residual
   pattern or constant, disambiguated on `->`, detected positionally after the value
   expr). A do-`let` now parses a *pattern*; a bare `VarPattern` with no `else`
   stays the existing `DoLetStep` path byte-for-byte, and a refutable pattern with no
   `else` is a clean parse error. Refutable steps are returned as a `DoStepRefutable`.
2. **Desugar** (`collect_do_steps` / `build_do_refutable`): on a refutable step, the
   *remaining* steps are collected and spliced into arm 1 as a nested `do`; an
   effectful bind runs the value into a fresh temp (`__do<line>_<col>`) then matches
   it, a pure `let` matches the value directly. Each generated `MatchExpr` takes the
   **bind/`else` source position**, so the staircase lint's `is_written_as_match`
   sniff never re-flags generated code. A refutable bind with **no following step**
   is a parse error (an empty continuation would silently infer `DoExpr Nil = Unit`).
3. **Lint**: no rule change needed — Tier 1b's `staircase-of-doom` message already
   points payload chains at binding-else, which now covers do-block sites too; a
   regression fixture (`already_do_binding_else`) asserts new-syntax code yields zero
   staircase findings.

## 12. Tests / spec / docs

- Behavioural (`tests/stdlib/`): refutable `<-` with constant else, with binding-else
  (payload recovery), chained short-circuit across effectful binds, and a mixed
  `<-`/pure-`let` block; over both `!{IO}` and `do`-over-`Result`.
- Conformance `type_error`: a non-exhaustive residual on a `<-` bind → "Non-exhaustive
  match".
- Spec: extend §5.2.1 (or a new §5.2.2) with the `do`-local refutable-bind form and
  its desugaring; mark experimental. Update `let-else-and-monadic-binding-plan.md`
  (this is the concrete design for the effectful slice of that plan).
- README "Not Yet Supported" note updated as the gap closes.
