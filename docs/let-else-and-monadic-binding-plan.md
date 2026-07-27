# Refutable Binding — `let-else` fused with Monadic Propagation — Design Plan

**Status:** PLAN. Drafted 2026-07-07. **Tier 1 LANDED 2026-07-07** (§6);
**Tier 1b LANDED 2026-07-27** (§6b). One
binding construct delivered in capability tiers; the smallest tier is
self-contained and touches neither the monad machinery nor the effect system.

**Origin:** surfaced making a test helper (`first_fn_body_line`,
`tests/stdlib/compiler/test_parser.spr`) readable. A pure function that reaches
through several fallible/refutable layers, bailing differently at each, has no
flat form in Sprout today.

---

## 1. Problem

A pure function performing a **linear sequence of refutable destructuring steps,
each with its own failure outcome** is forced to *nest*, because Sprout's only
pure binding forms are `where` (irrefutable, no failure branch) and `match`
(refutable, but each success arm nests the remainder — a rightward staircase).
There is no `let … in` in pure functions (README §Not Yet Supported).

## 2. Key insight — one construct, not two

`let-else` (fail-with-constant) and monadic bind / `?` (fail-with-value) are **the
same construct at different points on one axis: what happens to the refuted case.**
A single binding form with an optional, optionally-binding `else` covers all of it:

```
let <success-pat> = <e>  [else [<residual-pat> ->] <handler>]
```

Everything desugars to one two-arm match with the happy path kept flat:

```
match <e> with
| <success-pat>  -> <continuation>
| <residual-pat> -> <handler>
```

The three behaviours we previously treated as separate features are just **modes
of the `else` clause**:

| You write | Mode | else-arm desugars to |
|-----------|------|----------------------|
| `let Ok x = e` *(no else)* | **propagate / monadic** | `\| Err err -> Err err` (re-inject residual) |
| `let Ok x = e else -1` | **fail-with-constant** (classic `let-else`) | `\| _ -> -1` |
| `let Ok x = e else Err err -> recover(err)` | **fail-with-value** (handle) | `\| Err err -> recover(err)` |

Making **"no else" mean propagate** puts the monad on the *default* path (the
common case — thread the error onward — is the shortest to write), with `else` as
the local-handling override. It also subsumes "how do I see the failure value?":
that's the binding-else mode, not a separate mechanism.

## 3. Prior art (verified against primary sources, 2026-07-07)

| Language | Construct | Verified facts | Source |
|----------|-----------|----------------|--------|
| **Rust** | `let PAT = e else { … }`; `e?` | let-else's else **must diverge** and **does not bind** the residual; `?` is the *separate* propagate form. The fusion says these are one axis. | Rust Reference, *Statements* |
| **Swift** | `guard let x = e else { … }` | else must exit scope; bindings live after — secondary (JS page didn't render; re-verify at sign-off) | Swift Language Guide, *Statements* |
| **Haskell** | `do { p <- e; … }` | refutable bind → `MonadFail.fail`; all failures collapse to one value | Haskell 2010 Report §3.14 |
| **OCaml** | `let* x = e in b` | user-defined; `≡ ( let* ) e (fun x -> b)` — this **is** our no-else propagate mode (failure handled by the user's `bind`) | OCaml Manual, *Binding operators* |

Reading: Rust spells propagate (`?`) and handle (`let-else`) as *two* syntaxes;
OCaml's `let*` is exactly our no-else mode. The fusion unifies them.

## 4. Delivery in tiers

The three `else` modes split cleanly by **how much the compiler must know**, which
gives a natural, independently-shippable staging:

| Tier | Adds | Machinery | Coupling |
|------|------|-----------|----------|
| **1** | `else <expr>` (constant) — refutable bind, else **required** | parser + desugar-to-`match` + refutability (reuse W5) | none |
| **1b** ✓ | `else <pat> -> <expr>` (bind the residual / handle) — **LANDED** | + one match-arm in the else | none |
| 2 | no-else **propagate** for `Result`/`Maybe`, structurally (a built-in `?`) | + residual re-injection for two known types | none |
| 3 | no-else propagate via a `Monad`/`Bind` class (user-defined) | + typeclass, monad-generic desugar | **effect-system design (D2)** |

Tiers 1–2 deliver the whole flat-railway ergonomic **without touching the monad or
effect design**. Tier 3's generality is the only part gated on the effect-system
pass; it may not be wanted in v0 at all (open question §7).

**Tier-2 prerequisite (stdlib gap).** The structural propagate desugar for `Maybe`
needs the residual-handling combinators, but `Maybe` currently exposes only `fmap` —
it is missing `and_then`/`map`/`with_default`, which `Result` already has in full.
Close this `Maybe` gap in `stdlib/prelude.sprout` before (or as part of) Tier 2;
otherwise the two-known-types desugar has no uniform surface to lower onto.

**Decided (so it isn't relitigated):** the `else` never *implicitly* binds the
residual — you get the failure value only by writing the explicit binding-else
(1b). Follows Rust/Swift.

## 5. Semantics (all tiers)

Sprout is expression-based, so — unlike Rust's imperative "else must diverge" — the
`else` here **is an expression that becomes the value of the binding sequence** on
failure. `let p = e else fb` with continuation `k` is *exactly*:
```
match e with | p -> k | _ -> fb
```
Type rule: `fb` and `k` unify to the result type. The RHS type is **arbitrary** —
refutability is a property of pattern-vs-type, so `Result` steps and bare-ADT steps
(`FnDecl …`) compose uniformly; no wrapper needed.

**Refutability (self-enforced via W5).** Because each binding desugars to a
`match`, W5 enforces the rule with no dedicated code: a refutable pattern without
`else` becomes a non-exhaustive match (**error**), and an `else` on an irrefutable
pattern makes the wildcard arm unreachable (**error**). Both carry a source
position; only the wording is W5's generic message.

## 6. Tier 1 — LANDED as the block form (2026-07-07)

Shipped as the sequential **block** form (§7.1): one `let`, a layout-aligned
binding list, one dedented `in`, then the body. Example:
```sprout
let x        = trim s
    Just n   = parse x else -1
    m        = n * 2
in m + 1
```

Implemented **entirely in the parser** — `parse_let_else_expr` /
`parse_let_block` / `parse_let_binding_step` / `parse_let_binding_sub` /
`build_let_binding_match` in `stdlib/compiler/parser.sprout`, reusing
`scan_do_step_end` for the layout binding-list; `in` is a keyword. Each binding
desugars to a `match` (two-arm with wildcard `else`, or single-arm without), so
**no checker change was needed** — and, verified empirically, the whole
refutability matrix enforces itself through W5: a refutable pattern without `else`
is a non-exhaustive-match error, and an `else` on an irrefutable pattern is an
unreachable-branch error (messages are W5's generic wording; bespoke text is
optional polish). Spec §5.2.1 rewritten; README's "Not Yet Supported" `let … in`
entry removed. Tests: `tests/stdlib/test_let_else.spr` (10 cases: refutable+else,
chained distinct sentinels, irrefutable-no-else, mixed, short-circuit) +
`tests/conformance/type_error/let_{refutable_missing_else,irrefutable_redundant_else}`.

**Finalized design (2026-07-07, refined with Kuba — all decisions recorded):**
1. **Separator/layout** — bindings are newline-aligned at a common column under
   `let`; `in`, dedented to the `let` column, closes the block. (Not `and`/`;` —
   `and` would wrongly imply simultaneous/recursive bindings.)
2. **`else` rule** — a *refutable* pattern **requires** `else`; an *irrefutable*
   pattern **with** `else` is an **error** (dead branch); an irrefutable pattern
   without `else` is the plain `where`-complement binding. Refutability is
   type-dependent, so this is enforced in the **checker** (reusing the W5
   exhaustiveness engine), not the parser.
3. **Purity** — every RHS must be pure; an effectful RHS is a type error (use
   `do`/`<-`). This deliberately avoids cementing effect semantics ahead of the
   deferred effect-system design (D2).
4. **`where` coexistence** — combinable; `where` is the **outer** scope. `where`
   bindings are visible in a `let … in` RHS and body; `let … in` bindings are
   **not** visible in `where`.
5. **Position** — a full expression, usable anywhere (reuses the `do`
   layout-expression machinery; the explicit `in` bounds the block mid-expression).
6. **Shadowing** — allowed, consistent with existing Sprout scoping; sequential,
   so a binding's RHS sees the previous meaning of any name it rebinds.

Trivial defaults (will apply unless flagged): at least one binding is required
(`let in body` is an error); the `else` expressions and the body must unify to the
block's result type.


**Delivered — ML-style `let … in`.** `let <pat> = <rhs> else <fb> in <body>`
desugars to `match <rhs> with | <pat> -> <body> | _ -> <fb>` **entirely in the
parser** (`parse_let_else_expr` + `build_let_else_match` in
`stdlib/compiler/parser.sprout`, dispatched from `parse_expr` on a leading `let`);
`body` is parsed recursively so `let … in let … in <expr>` sequences chain. `in`
is now a keyword (`lexer.sprout`; verified no identifier collisions). This extends
the roadmapped `let x = e in body` form (README §Not Yet Supported) rather than
inventing a parallel layout syntax — so no layout scanner is needed. No new AST
node; inference, exhaustiveness, and codegen inherit unchanged. `else` is
**mandatory** (the wildcard arm makes every desugared match exhaustive, so no
refutability analysis is needed yet). Spec §5.2.1 added. Tests:
`tests/stdlib/test_let_else.spr` (single/chained bindings, distinct sentinels, over
`List` and `Result`). Limitation: pure expression position only — inside a `do`
block, `let` remains the effectful variable-binding step.

**Original scope note:** constant else only; no residual binding, no propagate.

**Surface (shipped — block form):**
```sprout
fn first_fn_body_line(src: String) -> Int =
  let Ok toks                 = tokenize(src)        else -1
      Ok prog                 = parse_program(toks)  else -2
      Program (Cons d _) _    = prog                 else -3
      FnDecl _ _ _ _ _ body _ = d                    else -5
      CallExpr _ _ pos        = body                 else -4
  in  position_line(pos)
```

## 6b. Tier 1b — LANDED as binding-else (2026-07-27)

Adds the residual-binding `else` mode: `<pat> = <e> else <rpat> -> <handler>`,
where `<rpat>` is a **full pattern** matched against the refuted value and spliced
verbatim into the second arm:

```
<pat> = <e> else <rpat> -> <h>   →   match <e> with | <pat> -> <rest> | <rpat> -> <h>
```

So `let Ok x = e else Err msg -> fmt(msg)` names the failing payload, and a bare
variable (`else other -> …`) binds the whole scrutinee. **No failure constructor
is ever injected** — that keeps the feature parser-only and uniform with the
bare-ADT bindings Tier 1 already supports (there is no canonical failure ctor for
`FnDecl … = d else …`), and it resolves the row-3 contradiction in §2 (the
residual is written in full, not grown by the compiler).

Implemented **entirely in the parser** (`parse_let_else_clause` /
`parse_let_else_after_pat` / `parse_let_else_constant`, threading a
`Maybe ast.Pattern` residual through `parse_let_binding_sub` /
`build_let_binding_match` in `stdlib/compiler/parser.sprout`). Constant vs
binding-else is disambiguated by speculatively parsing a pattern after `else` and
committing to binding-else only if a `->` follows; a pattern that fails to parse,
or one not followed by `->`, falls back to the existing constant-`else` path
(`parse_expr`), so every previously-valid program parses identically. No AST node,
no checker/inference/codegen change — **exhaustiveness self-enforces through W5**:
the residual is checked like any match arm, so a residual that leaves cases
uncovered is a non-exhaustive-match error (`tests/conformance/type_error/
let_binding_else_nonexhaustive_residual`). Tests: `tests/stdlib/test_let_else.spr`
(payload recovery, `Nothing` residual, chained short-circuit, bare-var-binds-whole-
scrutinee). The `staircase-of-doom` lint (`stdlib/compiler/lint_rules.sprout`) now
points payload-carrying chains at binding-else instead of a soft "restructure"
nudge. Spec §5.2.1 updated; `docs/idiomatic-sprout.md` gained a binding-else
example. **Follow-on (not in this change):** sweep the ~4 real compiler staircases
(`analysis_service_driver.op_session_update`, `driver.run_file`, and constraint
cascades in `infer.sprout`) onto binding-else — filed in `BACKLOG.md`. Purity is
unchanged (RHS pure; the body after `in` may be a `do` block, so a pure validation
gate can guard an effectful action). Effectful-RHS let..else remains a later tier.

## 7. Open decisions

1. **Binding syntax** — RESOLVED (2026-07-07, revised): a single **sequential
   `let … in` block** — one `let`, a layout-aligned list of bindings, one `in`,
   then the body. Any binding is `<pat> = <e>` (irrefutable) or
   `<pat> = <e> else <fb>` (refutable, `else` supplies the short-circuit value).
   Bindings are sequential (each in scope for later bindings + body); **recursion
   is delegated to `where`** (a `let rec` is a possible additive follow-up).
   Complements the postfix `where` with a prefix form (OCaml's `let`/`let rec`/
   `let*` split is the precedent). The initial per-binding `let … else … in` shape
   (one `in` per binding) is superseded by this block shape — same desugaring,
   different surface. `in` stays a keyword.
2. **Tier 3 at all in v0?** — is "let-else + built-in `?` for Result/Maybe"
   (Tiers 1–2) enough, leaving user-defined monads out of v0?
3. **`else foo` vs `else foo -> h` disambiguation** (Tier 1b) — RESOLVED &
   LANDED: split on the presence of `->` after a speculatively-parsed pattern;
   no `->` (or a pattern that doesn't parse) is the constant `else`. See §6b.
4. **Tier 3 effect coupling** — relationship to effect rows; gated on the deferred
   effect-system design (D2).

## 8. Tests / spec / docs impact

- Parser tests: let-else binding sequences (nested constructor patterns already
  parse — verified).
- Typechecker success/failure: missing-else on refutable, redundant-else on
  irrefutable, fallback/continuation type-mismatch.
- Conformance run-tests: desugaring == the explicit `match` staircase on samples.
- Spec: new § for the binding form; update README §Not Yet Supported as the
  `let … in` gap closes.
