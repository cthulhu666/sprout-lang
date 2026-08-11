# Typing the fallible `do` bind (v0)

**Status: APPROVED AND IMPLEMENTED (2026-08-11).** The rule is normative in `docs/spec-v0.md` §5.9.
Fixed the `P0` in `BACKLOG.md` §1 ("a `do` block's `Result` short-circuit is never type-checked
against the enclosing function's return type"). Supersedes the severity question left open in
`docs/fallible-bind-diagnostic-v0.md`, which is a lint proposal for a different problem and whose
measurement located this one.

**What changed from this proposal during implementation** — three things, all recorded in the
`P0`'s closing note in `BACKLOG.md`:

- §1.2(2) claimed wrong-family binds were plainly accepted. With any *concrete* tail they are not:
  one of two pre-existing checks already fires. The case is reachable only through a tail whose
  type is a fresh tyvar (`panic : a`), which slips past both — verified, and it produced a
  `Nothing` box matching neither `Ok` nor `Err`. That is also why §4's "implementation risk" turned
  out to be the load-bearing detail: the check unifies against a *constructed* `Result E ?a` /
  `Maybe ?a` rather than comparing head names, which closes the tyvar hole by construction.
- §4's step 3 (the linearity predicate) is **not** made redundant by steps 1–2, but nearly: the
  typing rule now rejects the `Unit`-block shape first. What step 3 still buys is a false-positive
  removal — the old block-keyed predicate wrongly rejected a *non-fallible* bind followed by a
  consume in a `Result`-typed block. Both directions are pinned by tests.
- §7's three tailored diagnostics: two initially did not fire, because their conflict only surfaces
  against the signature, after the block-level unification has already succeeded. Fixed the same day
  (`return_mismatch_body_err`): when `check_fn_body`'s unification fails and the body is a `do` block
  containing a fallible bind, it re-blames the bind. §4's claim that the declared return type "does
  not have to be threaded" held after all — `check_fn_body` already has both the typed body and the
  return type, so it can look for the cause rather than be told about it.

Also landed alongside: `just compile-bench`, because `bench/` was compiled by nothing and that is
where the `P0`'s linear leak lived (`BACKLOG.md` `P3`). And `just linux-smoke` was run for this work —
34 task-io scenarios on epoll+timerfd with `SPROUT_GC_HDRCHECK=1`, green. That is **pre-push latency,
not extra coverage**: CI is `ubuntu-latest` with `SPROUT_GC_HDRCHECK: "1"` and `ci-fast-gates` already
runs `task-io-smoke`, so it exercises the same recipe on the same OS under the same env — including the
`accept(2)` `Err` arm the four restructured `tcp_accept` fixtures gained. The only thing local
`linux-smoke` covers that CI does not is the **architecture**: the container is aarch64 and CI is
x86_64 (see the arm64 `P3` in `BACKLOG.md`).

Every claim below about another language was verified by running that language's compiler locally;
versions and verbatim diagnostics are in §3. Every claim about Sprout's current behaviour was
verified by compiling and running a probe, or read off emitted IR.

---

## 1. Problem statement

In a `do` block, `x <- e` where `e : Result E A` or `e : Maybe A` compiles to a short-circuit: on
`Err`/`Nothing` the enclosing **function** returns that failure. Nothing checks that the enclosing
function can carry it.

### 1.1 Root cause, exactly

`infer_do_steps` types a `do` block as **the type of its last step** (`infer.sprout:3584-3587`):

```sprout
| Nil -> InferOk(typed_ast.TDo(typed_do_step_list_reverse(acc_steps),
                               typed_ast.typed_expr_type(last_typed), pos), …)
```

and `check_fn_body` unifies exactly that against the declared return type (`infer.sprout:5518`):

```sprout
let Ok s2 = unifier.unify_types(s1, typed_ast.typed_expr_type(typed_body),
                                fn_return_type(inst_type, list_length(params))) else …
```

So the **tail** is checked and the **short-circuit path is not**. Both halves are observable:

| Probe | Result |
| --- | --- |
| `fn plain_tail(x) -> Result String Int` ending in a plain `Int` | correctly **rejected**: `Return type mismatch in main.plain_tail: Type mismatch: Int vs Result String Int` |
| `fn returns_int() -> Int` binding a `Maybe Int` | **accepted**; returns `35184372088840`, a heap pointer printed as an `Int` |

There is no auto-wrapping of a plain tail into `Ok`, and the tail unification is doing its job. The
defect is a single missing constraint, not a broken pipeline.

### 1.2 What goes wrong, measured

For `x <- e` with `e` fallible, codegen emits a fresh failure box in `do_short_*` and merges it with
the success value through a `phi` typed `i64`:

```llvm
do_short_3:                                        ; in `fn returns_int() -> Int !{IO}`
  %t$6 = call i64 @sprout_alloc_obj(i64 0, i64 0)  ; a Nothing box
  br label %do_done_3
do_done_3:
  %t$9 = phi i64 [%t$6, %do_short_3], [%t$8, %do_cont_3]
  ret i64 %t$9                                     ; a Nothing BOX or an Int, from `-> Int`
```

Four consequences, all measured, all compiling clean today:

1. **Non-fallible enclosing type.** `-> Int` returns the box as an `Int` (`35184372088840`); `-> String`
   returns a pointer *read as a CSTR*, printing arbitrary heap bytes as text.
2. **Wrong family.** A `Maybe` bind in a `Result`-returning function, or the reverse, is accepted.
3. **Wrong error type.** `fn mismatched() -> Result Int Int` binding a `Result String Int` compiles;
   `"boom"` arrives as `Err e` with `e : Int`.
4. **Exhaustiveness is defeated.** A `Maybe` short-circuit returned from `-> List Int` produces a
   value matching neither `Nil` nor `[h | t]`, so a match the compiler *proved total* under §5.5 dies
   with `runtime error: non-exhaustive match`.

### 1.3 The linearity facet is a wrong normative sentence, not an oversight

The spec already states a rule for the linear hazard (§5.8, linear-types bullet list):

> **A consume may not follow a fallible bind.** In a block whose type is `Maybe` or `Result`, a `<-`
> bind short-circuits (§11), so steps after it are conditional. […] **Effectful blocks (`!{IO}`) run
> every step and are unaffected.**

Both of the italicised parts are wrong, and together they switch the rule off exactly where it was
needed:

- **The condition is keyed on the block's type, not on the presence of a fallible bind.**
  `linear_check.sprout:465` implements the sentence literally —
  `lin_do(steps, block_short_circuits(dty), sc, env)` — where `dty` is the block's own (tail) type.
- **The `!{IO}` carve-out is false.** `!{IO}` and short-circuiting are orthogonal. A `!{IO}` block
  containing a fallible bind does *not* run every step.

`bench/http_worker_pool/{pool,spawn}_server.sprout` was the witness: `fn handle(conn: consuming
TcpConnection) -> Unit !{IO}` had block type `Unit`, so `sc_block = false`, so `conditional_consume`
returned `Nothing` at `linear_check.sprout:922` before looking at anything, and two fallible binds
before `close(conn)` raised nothing. A read timeout leaked the descriptor. Fixed at the six call
sites in commit `20a935e3`; the *checker* is still wrong.

Note the diagnostic and the rule already exist and are correctly worded
(`linear_check.sprout:269-270`). Only the predicate that enables them is wrong.

### 1.4 The rule has no normative section to amend

`§5.2.2` specifies the **refutable** `<pat> <- e else …` form. The plain `Maybe`/`Result`
auto-short-circuit is described only incidentally — the §5.8 bullet above, and the combinator tables
in §8.5. Its `(§11)` cross-reference is dangling: `§11` occurs once in the whole spec and no such
section exists (the spec ends at §10). So part of this work is to **write** the normative rule.

---

## 2. Goals and non-goals

**Goals**

- G1. Make the four shapes in §1.2 type errors.
- G2. Make the linearity rule of §1.3 fire on the presence of a fallible bind, regardless of the
  block's own type or effect row.
- G3. Land the migration with the ergonomic replacement available in the same change, so the 87
  affected sites have somewhere to go.
- G4. Give the rule a normative home in the spec, and fix the dangling `(§11)`.

**Non-goals**

- N1. **No change to `_ <-`'s meaning.** Whether a failure propagates must not depend on whether the
  success value was named. Measured: `_ <-` occurs in a `Result`-returning function exactly once in
  the tree (`parser.sprout:1346`, `_ <- validate_ctor_where(…)`, whose only purpose is to fail —
  legitimate and unaffected).
- N2. **No auto-wrapping** of a plain tail into `Ok`/`Just`. Today's compiler correctly rejects that
  (§1.1); adding it is a separate, larger design question.
- N3. No effect-system change. `merge_effects` (`P2`) stays as it is.
- N4. No new `Try`-style user-extensible protocol. `Maybe` and `Result` are the only two families
  `short_circuit_family` recognises and that stays true.

---

## 3. Prior-art survey

The question is: when a `do`-style bind (or `?`/`try`) can propagate a failure, must the enclosing
function's return type be able to carry it — and is a mismatch an **error** or a **warning**?

Every row was produced by running the compiler named. Diagnostics are verbatim.

| Language | Shape | Verdict |
| --- | --- | --- |
| Rust 1.75.0 | `?` on `Option` in `-> Result<i32, String>` | **error** E0277 — "the `?` operator can only be used on `Result`s, not `Option`s, in a function that returns `Result`" |
| Rust 1.75.0 | `?` in `-> i32` | **error** E0277 — "the `?` operator can only be used in a function that returns `Result` or `Option` (or another type that implements `FromResidual`)" |
| Rust 1.75.0 | `?` on `Result` in `-> Option<i32>` | **error** E0277 — "the `?` operator can only be used on `Option`s, not `Result`s, in a function that returns `Option`" |
| Rust 1.75.0 | `?` on `Result<_, String>` in `-> Result<i32, i32>` | **error** E0277 — "`?` couldn't convert the error to `i32`" |
| GHC 9.10.1 | a `Maybe` action in an `IO` `do` block | **error** GHC-83865 — "Couldn't match type 'Maybe' with 'IO'" |
| Swift 6.2.4 | `try` in a non-`throws` function | **error** — "errors thrown from here are not handled" |
| OCaml 5.1.0 | `let*` (option) block whose tail is `Ok v` | **error** — "This variant expression is expected to have type 'a option" |

Rust's mechanism corroborates the rule at the library level rather than only the diagnostic: the
implementor list for `core::ops::FromResidual` contains `impl<T> FromResidual<Option<Infallible>> for
Option<T>` and `impl<T, E, F> FromResidual<Result<Infallible, E>> for Result<T, F> where F: From<E>`,
and **no** `FromResidual<Option<Infallible>> for Result<T, E>`. Cross-family propagation is not
merely diagnosed, it is unimplementable.

**Consensus: 4 of 4 reject; none warns.** Two mechanisms:

- **Type-directed** (Haskell, OCaml). The bind operator's own type fixes the monad, so ordinary
  unification catches the mismatch and no dedicated rule is needed. Sprout cannot get this for free:
  its `do` is built-in and *overloaded* across `!{IO}` sequencing, `Maybe`, and `Result`, so the
  family is a property of each step's type rather than of the block's type constructor.
- **Rule-directed** (Rust, Swift). A dedicated rule plus a diagnostic tailored per mismatch shape.
  This is the shape Sprout needs, and Rust's four distinct messages are a ready-made template for §7.

**Escape hatch precedent.** Rust pairs `?` (propagate, needs a compatible return type) with
`.expect("msg")` (assert, legal anywhere, panics loudly). Verified: `fn e() -> i32 {
opt().expect("index in range") }` compiles and at runtime prints `index in range` and panics. That is
exactly the `mutvec_get` / `mutvec_at` pair Sprout already has (§8).

**Not surveyed.** Zig's `try` appears to impose the same requirement, but the reference section does
not state it verbatim and no local toolchain was available, so it is **unverified** and excluded from
the consensus count.

---

## 4. High-level implementation overview

The machinery is already present; one constraint is missing.

**Already there.** `short_circuit_family` (`infer.sprout:3517`) classifies a type as `Maybe`,
`Result`, or neither. A `family: Maybe String` accumulator is already threaded through
`infer_do_steps` (`:3579`). `do_family_update` (`:3540-3558`) already **rejects mixing** families
within one block, with a real diagnostic ("This do block started with `Result` bindings, but this step
returns `Maybe`…").

**Step 1 — carry the type, not just the name.** Widen the accumulator from `Maybe String` to
`Maybe (String, types.Type)` so the failure slot's type is available, not only its family name.

**Step 2 — one unification at block end.** In the `Nil` arm of `infer_do_steps`, when the family is
`Just`, unify the block's type against `Result E ?` (fresh success var, `E` from the recorded type) or
`Maybe ?`. Because `check_fn_body:5518` already unifies the block's type against the declared return
type, the constraint propagates outward **with no new threading and no change to `check_fn_body`**.
Nesting is handled for free: each block is constrained against its own type.

**Step 3 — fix the linearity predicate.** Replace `block_short_circuits(dty)` at
`linear_check.sprout:465` with "does any `TDoBindStep` in this block have a fallible RHS type". The
existing fixture `tests/conformance/type_error/borrow_after_fallible_bind` guards the current
behaviour; the bench shape from §1.3 becomes the new regression test.

Steps 1–2 arguably subsume step 3: post-rule, a block containing a fallible bind must itself be
fallible, so `block_short_circuits(dty)` becomes true whenever it matters. **Do step 3 anyway** — the
subsumption argument is subtle, and a direct predicate is cheaper to verify than to reason about.

**Sequencing.** Step 3 is independent of steps 1–2 and can land first with its own regression test,
which de-risks the larger change. Suggested order: 3, then 8 (the `mutmatrix_at` addition), then 1–2
with the migration.

**Implementation risk to check during the work.** The family is read off `apply_subst(subst,
step_type)`. If a step's type is still an unresolved `TVar` at that point, `short_circuit_family`
returns `Nothing` and the constraint is skipped, while `ast_to_ir` — which runs on the fully
substituted typed AST — may still emit a short-circuit. Any such window is a hole in the rule and
needs a test; it is the one part of this design I have not been able to rule out statically.

---

## 5. Syntax and semantics impact

No syntax change. No change to what accepted programs do.

Semantics gain a **static requirement** on an existing form. The three `do`-step forms and their
meanings are unchanged; the middle column is what the rule newly enforces:

| Form | Requires | Meaning |
| --- | --- | --- |
| `x <- e`, `e : Result E A` | enclosing block type `Result E B` | bind `x : A`; `Err` propagates |
| `x <- e`, `e : Maybe A` | enclosing block type `Maybe B` | bind `x : A`; `Nothing` propagates |
| `_ <- e` | as above (identical to `x <-`) | discard the value; the failure still propagates |
| `e` (bare statement) | nothing | run it, discard the whole `Maybe`/`Result`, **continue** |
| `x <- e`, `e` non-fallible | nothing | ordinary effectful bind |

The trap today is that `_ <- e` *reads* as the fourth row and *behaves* as the third. The rule makes
that difference visible instead of removing it (N1).

---

## 6. Type-system impact

One unification per `do` block that contains a fallible bind. No new type constructors, no new
constraint kind, no inference-power change: it constrains a type that was previously left free. The
success slot is a fresh variable, so a block whose tail legitimately narrows the success type is
unaffected.

Interaction worth stating: because the block's type is its tail's type and the tail is already
unified with the declared return type, the new constraint reaches the signature **through existing
unification**. A function with an inferred (unannotated) return type therefore gets the fallible type
inferred rather than reported — which is correct and matches Haskell.

---

## 7. Error-message impact

Three new diagnostics, modelled on Rust's practice of one tailored message per mismatch shape rather
than one generic message. Wording proposals:

- **Non-fallible enclosing type**
  > `x <- read_file(p)` propagates a `Result` failure, but `load` returns `String`, which cannot
  > carry an error. Use `let x = with_default(…)` or a total accessor to handle the failure here, or
  > change `load` to return `Result IoError String`.

- **Wrong family**
  > `v <- mutvec_get(w, i)` propagates `Nothing`, but `step` returns `Result String Int`. A `Maybe`
  > bind needs a `Maybe`-returning function; convert with `maybe_to_result`, or use `mutvec_at` if
  > the index is known to be in range.

- **Wrong error type**
  > `x <- parse(s)` fails with `String`, but `run` returns `Result Int Int`. Convert the error with
  > `map_error`.

Each names the binder, the callee, the enclosing function, both types, and a concrete next step. The
existing generic `Return type mismatch in <fn>: …` would technically fire once the constraint exists,
but it points at the tail rather than the bind, so it is the wrong message for this shape.

The **fallible-bind linearity** message (`linear_check.sprout:270`) needs no rewording — it is already
accurate. Only its trigger changes.

---

## 8. Compatibility and migration

Breaking for code that relies on the unsound behaviour. Measured blast radius, method as in
`docs/fallible-bind-diagnostic-v0.md` §3:

| Family | Sites | Migration |
| --- | --- | --- |
| `Result`, `_ <-` in a non-`Result` function | 21 | delete `_ <- ` — the bare statement already runs the effect, discards the `Result`, and continues (verified) |
| `Result`, `_ <-` in a `Result` function | 1 | none; legitimate (`parser.sprout:1346`) |
| `Maybe` | 87 | see below |
| `Result`, named binder in a non-`Result` function | 4 latent | `probe_ir -> String` in `test_unresolved_dict_poison.spr`; `accept_forever -> Int` in three `task_io_smoke` fixtures |

**The 87 `Maybe` sites are exactly two callees** — `mutvec_get` (68) and `mutmatrix_get` (19) — which
collapses the migration to a rename plus one small addition:

- **`mutvec_get` → `mutvec_at`.** Already exists and is exported (`mutable.sprout:37`). Routes through
  `vector_get_direct`, which **is** bounds-checked (`sprout_runtime.c:7312`,
  `tcp_fail("vector_get_direct: index out of bounds")`) — so "unchecked read" in its doc comment means
  "no `Maybe` box", not "no bounds check", and that comment should be reworded. Migration is a
  one-identifier rename that also **removes a `Maybe` allocation per read** in hot numeric loops
  (`examples/neural_network_train_xor.sprout`, `examples/digit_recognizer/`,
  `tests/stdlib/test_nn_xor_train.spr`). The prelude's own iteration combinators already use
  `vector_get_direct` directly; the examples and tests never followed.
- **`mutmatrix_get` → `mutmatrix_at`.** Does **not** exist — only `mutmatrix_at_or`. Needs ~4 lines
  mirroring `mutvec_at`. **This is the only new API in the proposal.**
- Callers who want a default rather than an assertion already have `vec_get_or`, `mutmatrix_at_or`,
  `maybe_with_default`, and `result_with_default`.

By return type, the 87 split into 57 `Unit` (the short-circuit is unobservable today, because the
returned box is discarded by convention) and 30 observable: 15 `-> Int`, 12 `-> Double`, 1 `-> String`,
and 2 `-> List (Int, Int, Int, Int)` in the shipped `examples/astar.sprout` — the
exhaustiveness-defeating shape of §1.2(4), latent only because the indices happen to be in range.

> **Correction to an earlier estimate.** I previously recorded the `Maybe` migration as needing a
> per-site semantic decision. That was wrong: with only two callees and `mutvec_at` already present,
> it is a mechanical rename for 68 of 87 sites and a small stdlib addition for the other 19.
> `BACKLOG.md` carries the same correction.

No deprecation window is proposed: `mutvec_get`/`mutmatrix_get` remain correct and useful in
`Maybe`-returning functions, so nothing is removed.

---

## 9. Tests

**Negative (must be rejected), as `tests/conformance/type_error/` fixtures**

1. `Result` bind in `-> Int`.
2. `Result` bind in `-> String` (the CSTR-misread shape).
3. `Maybe` bind in `-> Int`.
4. `Maybe` bind in `-> List Int` (the exhaustiveness shape).
5. `Maybe` bind in `-> Result E A`, and `Result` bind in `-> Maybe A` (wrong family, both directions).
6. `Result String Int` bind in `-> Result Int Int` (wrong error type).
7. Pure (non-`!{IO}`) version of #1, pinning that the rule is not effect-gated.
8. `_ <-` version of #1, pinning that `_ <-` is not an opt-out.
9. **Linearity:** `consuming` parameter, fallible bind, then `close`, in a `-> Unit !{IO}` function —
   the bench shape. Must be rejected by `after_fallible_msg`. This one can land ahead of the rest.
10. **Linearity, post-rule:** the same shape in a function that legitimately returns `Result`, where
    the typing rule permits the bind and only the linearity check can catch the skipped consume.

**Positive (must still be accepted), under `tests/stdlib/`** — note
`tests/conformance/run/` is executed by no recipe, per the `P1` W5 entry in `BACKLOG.md`:

11. `Result` bind in a matching `-> Result E A`, both paths exercised at runtime (the `result_tail`
    probe: `Ok 6` and `Err boom`).
12. `Maybe` bind in a matching `-> Maybe A`, both paths.
13. Bare `Result`-valued statement in a `-> Unit` and a `-> Int` function: runs, discards, continues.
14. Ordinary non-fallible `!{IO}` bind, unaffected.
15. A migrated `mutvec_at` / `mutmatrix_at` numeric kernel still produces its expected output.

**Gates.** Compiler-source changes ⇒ smoke shapes, bundle smoke, `just refresh-seed`, golden IR
(`just ir-golden-diff` — the `mutvec_get` → `mutvec_at` rename changes emitted IR and the diff must be
read before regenerating). Stdlib/runtime-adjacent ⇒ full `just test`,
`just compile-examples-stage1`, the example canary, and `just linux-smoke`.

---

## 10. Spec and docs

- **New normative subsection** for the fallible `<-` bind — the rule has no home today (§1.4). It
  should state the short-circuit, the return-type requirement, the three-forms table of §5, and that
  `Maybe` and `Result` are the only two families.
- **Fix §5.8's linear bullet**: drop "Effectful blocks (`!{IO}`) run every step and are unaffected"
  (false), and re-key the condition from "a block whose type is `Maybe` or `Result`" to "a block
  containing a fallible bind".
- **Fix the dangling `(§11)`** at `docs/spec-v0.md:870` to point at the new subsection.
- Reword `mutvec_at`'s "unchecked read" comment (`mutable.sprout:34-36`) — it is bounds-checked.
- `docs/idiomatic-sprout.md`: document `mutvec_at`/`mutmatrix_at` as the idiomatic
  known-in-range read, and the bare statement as the discard form.
- Close the `P0` in `BACKLOG.md`.

**Status:** the fallible-bind rule is proposed as **normative** for the stable core. Linear types are
themselves marked Experimental (§5.8), so the linearity-predicate fix inherits that status.

---

## 11. Decision requested

1. **The rule** (§5): `x <- (e : Result E A)` well-typed only where the block's type is `Result E B`;
   `x <- (e : Maybe A)` only where it is `Maybe B`. **Error, not warning** — 4 of 4 surveyed languages
   error, and a warning would leave memory-unsafe code compiling.
2. **The `Unit` case**: rejected, not permitted-with-a-warning. It is the invisible skip path that
   leaked the connection in §1.3, and it is 57 of the 87 `Maybe` sites.
3. **The one new builtin-adjacent addition**: `mutmatrix_at` in `stdlib/mutable.sprout`, pure Sprout
   mirroring the existing `mutvec_at`, no runtime change. Flagged explicitly per `AGENTS.md`
   §Collaboration Rule 6 even though it needs no new host builtin.
