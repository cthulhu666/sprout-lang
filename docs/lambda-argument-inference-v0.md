# Two-Pass Argument Inference for Lambda Arguments (v0)

Status: **normative** — implemented in `stdlib/compiler/infer.sprout`, pinned by
`tests/stdlib/test_lambda_param_field_access.spr` and
`tests/stdlib/test_lambda_arg_constrained_dispatch.spr`. Spec: §5.3, §7 rules 3–4.

## 1. Problem statement

A call inferred every argument in source order and only then unified the result
against the callee's type. A lambda argument therefore had its parameters typed
as unconstrained variables while its body was inferred — even when a *later*
argument would have fixed them.

`list_fold(step, seed, xs)` is the shape that makes this bite: the accumulator
type comes from `seed` and the element type from `xs`, both of which are inferred
*after* `step`.

```sprout
type Hold = (volume_m3: Double, max_load_t: Double)
type CargoBay = (name: String, volume_m3: Double, max_load_t: Double)

list_fold(\(acc, b) -> Hold(volume_m3 = acc.volume_m3 + b.volume_m3,
                            max_load_t = acc.max_load_t + b.max_load_t),
          no_hold, l.bays)
```

Two distinct failures followed from the one cause:

1. **Spurious rejection.** `get_field_from_resolved` cannot resolve a field on an
   unresolved receiver, so it falls back to a fresh variable. `check_arith` then
   tries `Int` before `Double`, locking the operand type to `Int`, and the
   enclosing record construction is rejected with
   `Record field type mismatch: Type mismatch: Int vs Double` — a message that
   names neither the lambda nor the field.
2. **Undefined symbol in the emitted IR.** A class method called on a lambda
   parameter had no resolved receiver type to dispatch on, so codegen emitted a
   direct call to the *class method* rather than an instance:
   `error: use of undefined value '@to_string'`. This reached the LLVM verifier,
   not the type checker. A backtick template counts — `` `${acc}` `` calls
   `to_string` — so `list_fold(\(acc, s) -> `${acc}|${s}`, "", xs)` did not compile.

Neither is a miscompile: `ast_to_ir` resolves record field offsets from the
constructor table, not from the node's inferred type, so a wrong-typed receiver
cannot silently read the wrong field. Both failures are loud.

## 2. Goals and non-goals

**Goals**

- A lambda argument's parameter types are known before its body is inferred,
  whenever any argument of the same call determines them.
- No change to argument *evaluation* order, only to checking order.
- Existing diagnostics keep their wording; a genuine argument/callee mismatch is
  still reported by `infer_call_resolve`.

**Non-goals**

- Honouring a lambda parameter's *annotation* (see §7).
- Higher-rank or impredicative instantiation. Slots are resolved by ordinary
  unification; nothing here inspects polymorphic argument types.
- Tuple elements. `infer_tuple` has no callee to resolve slots against and keeps
  the single-pass `infer_args_acc`.

## 3. Prior-art survey

Ordering argument checking so that lambdas are visited last is the mainstream
choice, not an invention.

| Language | Rule | Source |
|---|---|---|
| Rust | `check_argument_types` iterates `for check_closures in [false, true]` and `continue`s when `is_closure != check_closures`, so non-closure arguments are checked first | `compiler/rustc_hir_typeck/src/fn_ctxt/checks.rs` |
| C# | Second-phase type inference repeatedly processes arguments whose *output* types contain unfixed type variables but whose *input* types do not — the implicitly-typed lambda case | ECMA-334 §12.6.3.3, §12.6.3.7 |
| Kotlin | Lambda bodies are analysed only after overload candidates are fixed and the other arguments' constraints are in the constraint system | Kotlin spec, "Statements with lambda literals" |

The divergence among them is in *how much* is deferred (C# and Kotlin iterate to a
fixed point over a constraint system; Rust makes a single extra pass). Sprout takes
Rust's shape: one pass for non-lambdas, one for lambdas. It is the least machinery
that covers the `fold`/`map` shapes the language actually leans on, and it degrades
to the previous behaviour rather than failing when a slot stays unresolved.

## 4. Implementation

`infer_call_args` (`stdlib/compiler/infer.sprout`) replaces `infer_args_acc` for
call arguments only:

1. **Pass 1** — `infer_arg_slots` infers each non-lambda argument in source order,
   threading the substitution. Each lambda is held back as
   `ArgLambda params body v pos`, where `v` is a fresh variable standing in for
   its parameter slot.
2. **Push-down** — skipped outright when no slot is an `ArgLambda`
   (`has_lambda_slot`), since it would then unify exactly what
   `infer_call_resolve` unifies moments later. Otherwise
   `push_down_arg_slots` unifies the callee's type against
   `build_fn_type(slot_types, fresh_ret)`. On failure it returns the substitution
   unchanged: a genuine mismatch is left for `infer_call_resolve` so the existing
   `Call type mismatch` wording is preserved.
3. **Pass 2** — `infer_lambda_slots` walks the slots in source order and infers
   each held-back lambda via `infer_lambda_expected`, whose `expected` argument is
   the substituted slot. `seed_from_arrow` walks the expected arrow spine
   alongside the parameters and seeds them **with the slot's own variables, not
   copies**, so a slot resolved later still propagates into the body.

Source order is preserved for both the resulting `typed_args` list and the effect
merge — `merge_effects` keeps its left operand when neither side is `EffectPure`
or `EffectIO`, so a reordered merge could otherwise pick a different effect
variable.

Where the callee type cannot be peeled — a bare type-variable callee, a parameter
count mismatch — `seed_from_arrow` leaves the remaining parameters as fresh
variables, which is exactly the pre-change behaviour.

## 5. Compatibility

The change only makes argument types *more* resolved at the point a lambda body
is inferred, and a call with **no** lambda argument skips the slot resolution
entirely (`has_lambda_slot`), threading the same substitution and the same typed
arguments as before. So the blast radius is calls that pass a lambda. No gate in
the repo regressed — see §6.

The one behavioural risk is dispatch: `check_call_constraint` branches on how
resolved an argument's head type is when choosing between forwarding an in-scope
dictionary and devirtualizing a concrete instance. `tests/stdlib/` had **no** test
combining a `where C a` constraint with a lambda argument, so
`test_lambda_arg_constrained_dispatch.spr` was added to cover both paths.

## 6. Tests

- `tests/stdlib/test_lambda_param_field_access.spr` — field access on lambda
  parameters, with `Hold`/`CargoBay` sharing field *names* while differing in
  layout and in the type at index 0, so a wrongly-resolved receiver cannot pass
  unnoticed. Covers the seed-fixed slot, the element-fixed slot, a field read as
  the whole lambda body, and a field read that fixes the arithmetic type.
- `tests/stdlib/test_lambda_arg_constrained_dispatch.spr` — dictionary forwarding
  through calls that also take a lambda: forwarded dictionary, devirtualized
  concrete instance, nested container instance, and a fold whose seed and list fix
  different type variables of the same call.

## 7. Follow-up

> **Superseded by [`lambda-parameter-annotations-v0.md`](./lambda-parameter-annotations-v0.md).**
> Two claims below are stale. The example's failure is now a located Sprout
> diagnostic (`dispatch-verify: ambiguous type variable in to_string`), not the
> `clang` link error quoted; and the same shape *inside* a function does not fail
> at all, because the use site solves the parameter. The gap is narrower than this
> section implies and is primarily a **checking** gap, not an inference gap.

Lambda parameter annotations are parsed into `ast.Param String (Maybe TypeExpr)`
and then discarded: `make_fresh_param_types` allocates a fresh variable per
parameter without consulting the annotation, and `extend_env_with_params` drops
it. In argument position the slot now supplies the type, so the gap is masked;
elsewhere it is not:

```sprout
let wrap_it = \(s: String) -> `<${s}>`   # error: use of undefined value '@to_string'
```

Fixing it needs the enclosing declaration's type-variable environment threaded
into `infer_lambda` — otherwise `\(x: a) -> …` inside a `where`-constrained
function would turn `a` into a rigid `TConst` rather than the declaration's type
variable. That is a separate change; filed in `BACKLOG.md`.
