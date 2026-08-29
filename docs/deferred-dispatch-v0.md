# Deferred input-position dispatch (v0)

Status: **implemented.** The fix is the concrete-head branch of
`maybe_forward_input_dispatch` (`stdlib/compiler/infer.sprout`); the injection it
shares with the return-position path is `concrete_dispatch_call`.

## 1. Problem statement

Calling a local lambda whose body uses a typeclass method fails to compile, with a
diagnostic that states something false:

```sprout
fn main() -> Unit !{IO} =
  let g = \x -> to_string(x)
  in print(g(1))
```

```
3:26: ERROR: check: dispatch-verify: No instance of ToString for Int
```

`instance ToString Int` exists (`prelude.sprout`). The program is well-typed. The
message names a real class and a real type and asserts a relationship between them
that is not true.

### Confirmed behaviour map

Every row below was run against stage-1 at `39f7774a`+5 (`--phase check`).

| Shape | Result |
|---|---|
| `let g = \x -> to_string(x) in g(1)` | rejected |
| `let g = \ (x: Int, y: Int) -> to_string(x) in g(1, 2)` | rejected — **annotation does not help** |
| `let g = \x -> \y -> x == y in g(1)(2)` | rejected (`No instance of Eq for Int`) |
| `print((\x -> \y -> x == y)(1)(2))` | rejected — no `let` involved |
| `let g = \x -> to_string(x + n) in g(2)` (`n: Int`) | accepted |
| `list_map(\v -> to_string(v), [1, 2])` | accepted |
| `fn f(x: Int) -> String = to_string(x)` | accepted |
| `let g = \x -> x == 1 in g(2)` | accepted (`==` on `Int` never dispatches) |

The discriminator is **not** nesting, arity, `let`, or annotation. It is *when the
receiver's type becomes known*: if the lambda body pins it, the call compiles; if only
a later application pins it, the call is rejected. The annotated row is the clearest
statement of the defect — the author wrote `Int` and was told there is no instance for
`Int`.

Not class-specific: reproduced on `ToString` and `Eq`.

### Root cause

`let..in` is parse-time sugar for `match` (`parser.sprout:787-791`), so `g` is a
monomorphic pattern binding and the lambda's parameter type stays open until the
application unifies it.

Dictionary selection for a class method happens in `maybe_rewrite_class_method_call`
(`infer.sprout:7386`), reached both during inference and again from the post-pass
`resolve_dispatch_typed_expr` (`infer.sprout:7417`). The post-pass **does** run over
every `fn` body (`infer.sprout:8337`, with the declaration's final substitution `s2`),
every instance method (`:8816`), and top-level `let` (`:6918`), and it **does** descend
into `TLambda` (`:7444`). So the machinery is all present and reaches the call.

It declines on purpose. In `maybe_forward_input_dispatch` (`infer.sprout:7309-7315`):

```sprout
# A concrete head is inference's business, not this pass's: if it resolved
# concretely and still has no dict, that is a missing instance, which
# verify_dispatch reports against the call's position.
Nothing = concrete_type_str(resolved_t) else typed_ast.TCall(fn_, args, t, pos)
```

The input-position path repairs only the *forward* (still-polymorphic) case and bails
out whenever the resolved head is concrete, on the stated assumption that "concrete head
+ no dict" can only mean a missing instance. That assumption holds when input dispatch
really did resolve eagerly during inference. It fails exactly when the argument is a
lambda parameter pinned by a later application: eager inference could not resolve it,
the post-pass refuses to, and the call reaches `verify_dispatch` dictionary-less.

`verify_dispatch.check_missing` (`verify_dispatch.sprout:307-315`) then sees a
constraint variable pinned to a concrete type with no dict, applies the same assumption,
and prints `No instance of C for T`. It is reporting a symptom faithfully; the false
statement originates upstream.

The **return**-position path, twenty lines above in the same function family
(`infer.sprout:7275-7282`), already does the right thing for a concrete head: it injects
a `TDict` carrying the concrete constraint head and `EvUnresolved`.

## 2. Goals and non-goals

**Goals**
- The programs in the "rejected" rows above compile.
- Genuine missing instances are still rejected, with a located, accurate message.
- No new syntax, no signature changes, no new builtin.

**Non-goals**
- `Eq Double` / `Ord Double` and the min/max-on-`Double` question — settled
  separately on 2026-08-29, `docs/eq-ord-double-v0.md`.
- The `<`/`Ord` operator split and `check_compare`'s silent `Int` defaulting — a
  separate, larger item.
- Generalizing local bindings (let-polymorphism). This change makes monomorphic local
  lambdas dispatch correctly; it does not make them polymorphic.

## 3. Prior art

Deliberately omitted, and the omission is the judgment call: the Design Change Process
requires a survey when the decision is a choice among established external alternatives.
This is not. The change makes two paths *inside this compiler* consistent — the
input-position path adopts the concrete-head branch the return-position path already
runs. If the fix is later widened into "when should instance selection happen relative
to unification", that question does deserve a survey and should get its own document.

## 4. Implementation overview

One branch, in `maybe_forward_input_dispatch`:

- Keep the existing guard that the call carries no dict
  (`let Nil = collect_all_tdicts(args)`). This change never revises a decision made
  during inference; it only fills a slot left empty.
- Keep the forward (type-variable head) path exactly as is.
- **Change**: when `dispatch_type_for_vars` yields a *concrete* head, stop bailing out.
  Inject the `TDict` the same way `try_rewrite_for_return_dispatch` does — constraint
  head from the resolved type, `EvUnresolved` evidence.

**The change has a second half, found by a failing test rather than by design.** Removing
the bail-out exposed that the rewriter is not scope-aware. `maybe_rewrite_class_method_call`
decides a callee is a class method by looking up `@class:<name>` in the *global* env, which
cannot see a local shadow — a parameter, lambda parameter, `let`, do-bind or match binding
that happens to share a method's name. `tests/stdlib/test_local_shadows_class_method.spr`
covers exactly this, and its first case is
`fn via_param(append: Int -> Int -> Int, x: Int) -> Int = append(x, x)`, which the naive fix
rewrote into a `Semigroup Int` dispatch and rejected with
`No instance of Semigroup for Int` — a class the code never mentions.

Scope-awareness had always lived in `verify_dispatch`, which threads a `locals` set and
skips shadowed callees. The rewriter reached the same calls and never needed it *only
because it declined every concrete head*: the bail-out was load-bearing for a reason
unrelated to its stated rationale. So the post-pass now threads a `bound: Set` through
`resolve_dispatch_typed_expr` and its helpers — extended at lambdas, match branches and
do-steps, seeded from the declaration's own parameters — and skips the rewrite for a
callee that names a local. It reuses `infer.sprout`'s existing `bind_pattern` /
`bind_params`, so no helper is duplicated and no module dependency is added.

Scoping is precise rather than a blanket suppression: the same test asserts that the real
method still dispatches elsewhere in the file and that a shadow does not leak out of a
nested scope.

No instance table is needed in `infer`. Injected dicts carry `EvUnresolved`, and
`resolve.sprout` owns instance existence (`instance_exists`, `:143`) and already emits
`No instance of C for T` (`:137-138`). `infer.sprout` does not import `resolve.sprout`
and would not need to.

So a genuinely missing instance is still an error; it is reported by the phase that
actually knows the instance table, instead of being inferred from an empty dictionary
slot by a pass that does not.

## 5. Syntax and semantics impact

No syntax change. Programs that were rejected now compile; no accepted program changes
meaning. The dictionary a fixed call receives is the one the declaration's final
substitution names, which is the only instance that could have been correct.

## 6. Type-system impact

None. Inference, generalization and the value restriction are untouched. Only the
placement of an already-determined dictionary changes.

## 7. Error-message impact

This was the part that needed testing rather than assertion, and it was measured rather
than predicted. Before, a missing instance in this position was reported by
`verify_dispatch`; now it is reported by `resolve.sprout`. Measured on
`tests/conformance/type_error/missing_instance_in_applied_lambda.spr`:

```
before:  15:25: ERROR: check: dispatch-verify: No instance of Eq for Double
after:   15:25: ERROR: check: No instance of Eq for Double
```

The **source position is unchanged** and the wording is unchanged; only the
`dispatch-verify:` phase prefix is dropped, which is correct — the report now comes from
the phase that actually owns the instance table rather than from one inferring it from
an empty dictionary slot. No `.err` fixture in `tests/conformance/` matched on that
prefix, so nothing depended on it.

## 8. Tests

Written and confirmed RED before implementation, per Definition of Ready #3.

Positive (must compile and run):
1. `let g = \x -> to_string(x) in print(g(1))` — the minimal repro.
2. The annotated variant, `\ (x: Int, y: Int) -> ...`.
3. The curried variant, `\x -> \y -> x == y` applied as `g(1)(2)`.
4. A non-prelude user class, to show the fix is not `ToString`/`Eq`-specific.

Negative (must STILL be rejected, with a located, accurate message):
5. A local type declared without `deriving`, used under `==` in an applied lambda.
   Guards against the fix degrading into "inject a dict and hope", which would turn a
   compile error into a link error. The surrounding code comments
   (`verify_dispatch.sprout:322-326`) call out that failure mode explicitly.

   > **Corrected 2026-08-29.** This fixture originally used `Double`, on the grounds
   > that the prelude had no `Eq Double`. `Eq Double` landed two days later
   > (`docs/eq-ord-double-v0.md`) and the file started compiling — a negative test
   > anchored on a *gap* stops testing anything the moment the gap closes, silently.
   > It is now anchored on a user type with no instance, which no prelude change can
   > close.

Regression:
7. The working rows of the §1 table, so the eager path is not disturbed.

## 9. Blast radius

Zero currently-failing sites, by construction: the corpus and the downstream repo both
compile today, so nothing in either tree hits this. It is a latent trap, not a live
breakage.

It is a *near* trap, though. The corpus has 30 let-bound lambdas and
`uncharted-suns` has 12; at least one of the downstream ones
(`tests/game/test_digest_tags.spr:25`) calls `to_string` inside such a lambda and
compiles only because another call in the same body pins the type. Removing that other
call would trip the bug.

## 10. Gates

Compiler-source change, so Definition of Done items 7-12 all apply:

- `fmt`/`lint` on the compiler sources **before** the reseed, and no edits during it.
- **`just refresh-seed` FIRST** (delete the stale stage-1 binary first), *then*
  `just ir-golden-diff`. Reversing these makes the golden gate compare the old compiler
  against itself and report a meaningless `0 differences`.
- Golden IR does **not** move: `60 files, 0 differences`. This design doc originally
  predicted the opposite ("calls that previously reached codegen with no dictionary now
  carry one"), and that prediction was wrong for a reason worth keeping: the only calls
  that gain a dictionary are calls that previously **failed to compile**, and a file that
  fails to compile is not in the golden corpus. The change is invisible to every program
  that already worked — which is the strongest available statement that it does not
  disturb the eager path.

  Because a stale seed produces an identical `0 differences`, that green is only
  meaningful paired with its other half, per the AGENTS.md deletion proof: after
  `refresh-seed`, `git diff bootstrap/compile_driver.ll` was **non-empty** (fingerprint
  `e41d65ef…` → `88f2b623…`, 320 insertions / 288 deletions), so the edit demonstrably
  reached the binary that produced the goldens. Neither half is evidence alone.
- Smoke shapes, bundle smoke, `compile-examples-stage1`, `run-example-canary`, and the
  full `just test`.

## 11. Risks

- The comments around the injection site warn about grabbing an unrelated argument's
  instance (the `vec_sort` `Ord a` → `Ord (Vec a)` soundness hole). This change does not
  use that heuristic: the head comes from `dispatch_type_for_vars`, computed from the
  method type, the arguments and the final substitution.
- **This risk was real and fired.** "If some accepted-today program relies on the empty
  slot being filled later by a different mechanism, injecting here could conflict" —
  local shadowing was that mechanism, and `test_local_shadows_class_method.spr` caught it
  in the full suite after every targeted test was green. The `collect_all_tdicts(args) ==
  Nil` guard did not bound it, because the conflict was not about an existing dict; it
  was about the callee not being a class method at all. See §4.

  Worth keeping as a lesson rather than just a fixed bug: the targeted tests all passed
  because they were written from the model of the defect, and the model was incomplete.
  The suite-wide run is what found the missing half.
