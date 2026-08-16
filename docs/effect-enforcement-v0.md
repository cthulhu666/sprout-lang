# Effect enforcement — v0

**Status:** measurement landed 2026-08-16; enforcement **not** started and blocked on one decision
(§6). `docs/spec-v0.md` §7 rules 8, 9 and 11 state the intended discipline and its enforcement note
states that the v0 checker does not apply them. This document records what was actually broken, what
was fixed to make the gap measurable, and the number that came out.

Nothing here changes what any program compiles to. Effects are erased before codegen.

## 1. Problem

An effect annotation in Sprout is documentation, not a checked contract. `fn shout(s: String) ->
Unit = print(s)` compiles today. The spec has said so since the enforcement note was added, and the
standing plan was recorded as "wire up `unify_effects`" — the function exists, is exported, and has
no call site.

**That framing described a change that does not exist.** There is no missing call site. What was
missing was the entire *producer* half of the pipeline: nothing ever computed an effect for a body,
so there was nothing for a call site to check. Measured before any fix, over one real compile: 316
declarations, **zero** inferred `IO` — including `main`, whose body is literally `print(…)`.

The consumer half, by contrast, was complete and correct. `merge_effects` was already threaded
through calls, arguments, `if`, match arms and `do` steps. It had simply never had anything but
`EffectPure` to merge.

## 2. Why it stayed invisible

Three properties conspired, and it is worth naming them because they are the reason this survived
so long rather than being caught by a test:

1. **Unification ignores the effect.** `unifier.unify_types` matches `(TFunc p1 r1 _ o1, TFunc p2 r2
   _ o2)` — the effect field is discarded. So a wrong effect could never cause a false rejection.
2. **Codegen erases effects.** Golden IR is identical with the field populated or not.
3. **The two representations disagreed silently.** A declared effect was written to `Scheme`'s
   effect field and the type was built with the arrows hardcoded `EffectPure`. Nothing compared
   them, and `types.scheme_effects` had no reader in inference at all — four readers repo-wide, all
   in `types.sprout`, being one free-variable computation and two printers.

There was no configuration of the code in which the missing half produced a visible symptom.

## 3. What was broken, precisely

Six sites, in pipeline order. The three "producer" ones are why nothing had an effect; the rest is
what would have dropped it anyway.

| # | site | was |
|---|---|---|
| 1 | `infer.type_from_ast` (`TypeArrow`) | discarded the arrow's effect — which the **parser had already recorded** |
| 2 | `infer.build_fn_type_modes` | hardcoded `types.EffectPure` on every declared arrow |
| 3 | `infer.infer_lambda` | same builder, so lambda types were pure too |
| 4 | `unifier.instantiate` | returns `scheme_type` only; the Scheme's effect never reached a use site |
| 5 | `infer.infer_call_var` | passed a literal `types.EffectPure` as the callee effect of **every named call** |
| 6 | `infer.check_fn_body` | bound `InferOk typed_body0 s1 _ _`, discarding the body's inferred effect |

Site 5 alone is sufficient to explain the zero: every named call reported a pure callee, so
`merge_effects` only ever merged pure with pure and every body bottomed out at `EffectPure`.

Site 1 is the one worth dwelling on. `parser.make_type_arrow` hoists a trailing annotation onto the
arrow it is building, so `a -> b -> C !{IO}` has always put `!{IO}` on the **innermost** arrow and
stored it in the AST. Inference threw it away one step later. A parameter written
`f: a -> Unit !{e}` typed as a plain `a -> Unit`.

### The arrow question was never open

An earlier note in `BACKLOG.md` framed the fix to site 2 as needing a language decision: *for a
curried `fn f(a, b) -> C !{IO}`, which arrow carries the effect? Partial application makes the
choice observable.* **Both halves of that are wrong.** Sprout is n-ary; §5.3 states an application
is never a partial application, and `_` placeholders desugar to ordinary lambdas at parse time. And
the parser had already made the choice. `build_fn_type_modes` now places the declared effect on the
innermost arrow, matching what a hand-written signature produces.

A **zero-parameter** function has no arrow to carry anything, so the declared effect stays on the
`Scheme` as well; that is where `fn main() -> Unit !{IO}` keeps its `!{IO}`.
`types.scheme_to_string` prints whichever of the two is load-bearing, so an effectful function does
not render as `String -> Unit !{IO} !{IO}`.

## 4. What shipped: `--phase effects`

A report. It never rejects anything.

```
$ compile_driver --phase effects stdlib tests/effects/canaries.spr
effect GAP shout: declared pure, inferred !{IO}
effect ok  loud: declared !{IO}, inferred !{IO}
effect ok  over_declared: declared !{IO}, inferred pure
effect ok  instance describe(Quiet): declared pure, inferred pure
effect GAP instance describe(Noisy): declared pure, inferred !{IO}
effect-summary: 11 declarations, 4 gaps (declared pure, inferred !{IO}), 1 unresolved effect vars
```

A **gap** is an under-declaration only. Over-declaring is not a gap and is never reported as one: a
pure body under an `!{IO}` signature is exactly how `mutvec_len` says *my result is not a function
of my argument alone* (`docs/growable-mutvec-v0.md`), and enforcement will accept it. The rule is
subsumption — inferred ⊑ declared — not unification. `unify_effects` is the wrong operation for this
and always was: it returns `Ok` for both `(Pure, IO)` and `(IO, Pure)`, so calling it at the
declaration boundary would be a literal no-op.

`tests/effects/canaries.spr` is the instrument's calibration rig, gated by `just
effect-report-smoke`. It asserts on **both** halves — the cases that must be flagged and the cases
that must not — because a report that flags everything is as useless as one that flags nothing, and
because the predecessor instrument's failure mode was silence that read as good news.

### Two limitations, both deliberate

**One wave, not a fixed point.** A caller resolves its callee through the callee's env scheme, which
carries the *declared* effect; nothing writes an inferred effect back. So a mis-declared `helper` is
flagged, but the callers it taints are not — they surface only on a later run, after `helper` is
annotated. **Every number below is a lower bound on the first wave.**

**Named functions passed as values are a blind spot.** `list_each(\x -> print(x), xs)` is caught;
`list_each(print, xs)` is not. The effect crosses a higher-order boundary today only because
`infer_lambda` reports the lambda *body's* effect as the effect of the enclosing expression. That
attribution is strictly wrong — building a closure performs no IO — but it is the only mechanism
available, because `unify_types` ignores the arrow's effect field and never populates `eff_subst`,
so a callee's `!{e}` instantiates to a fresh `EffectVar` that nothing ever binds. Reporting the
technically-correct pure there would make `list_each`/`list_fold` — the repo's own recommended
effectful idiom — report clean, and those are precisely the boundary that matters. Over-approximating
keeps them visible until effect unification is threaded through `unify_types`.

## 5. The measurement

`stdlib/`, `stdlib/compiler/`, `stdlib/math/`, `examples/` — all 124 files, none excluded, run with
`--package-root .` as `just compile-examples-stage1` does.

| declared → inferred | count |
|---|---:|
| pure → pure | 3617 |
| `!{IO}` → `!{IO}` | 672 |
| pure → `!{e}` | 15 |
| `!{IO}` → pure (over-declared, allowed) | 13 |
| **pure → `!{IO}` (gap)** | **12** |
| `!{e}` → `!{e}` | 8 |
| **total** | **4337** |

672 bodies inferring `!{IO}` is the evidence that propagation works; 12 gaps is the headline.

Both declaration boundaries are covered. `infer.sprout` has *two* sites that discard a body's
inferred effect — the `fn` one and the `instance` one — and recording only the first would have left
every instance method out of the census, silently and in the flattering direction. 83 instance
methods are included, disambiguated by their head (two instances of one class share a method name);
all 83 are clean. `just effect-report-smoke` pins both sites so this cannot regress unnoticed.

The total is **approximate to within a few**: a declaration in the *entry* file is reported
unqualified while the same file seen as an import is reported qualified, so two same-named
entry-file declarations in different files collide under dedup. `is_free` is a real instance of this
(`dce.sprout` and `examples/nqueens.sprout`). It does not move the gap count, whose twelve entries
were each read against their source.

**And not one of the twelve is a function that quietly performs IO.**

| cause | count | examples |
|---|---:|---|
| `panic` in an internal-error fallback | 9 | `dce.is_pure_expr`, `ast_to_ir.compute_free_vars`, `lowering.consume_evidence`, `parser.int_from_lexed` |
| lambda-construction over-approximation (§4) | 3 | `log.stderr_logger`, `http_middleware.with_logging`, `http_web_server.routes` |

The nine are all the same shape — an exhaustiveness arm that cannot be reached:

```sprout
| typed_ast.TMethodRef _ _ _ _ _ -> panic("dce.is_pure_expr: TMethodRef survived lowering (internal error)")
```

## 6. The open decision: is `panic` an effect?

`stdlib/prelude.sprout:1288` declares `extern fn panic(msg: String) -> a !{IO}`. That is why nine of
the twelve gaps exist, and it is a bigger question than the count suggests, because it decides the
shape of the migration rather than its size.

`panic` **diverges** — it never returns. Under the current declaration, any otherwise-pure function
with an internal-error fallback must be annotated `!{IO}`, which is both a large annotation burden
and arguably a false statement: the function is pure in every case where it produces a value.

The alternatives are (a) `panic` becomes pure, (b) `panic` gets its own effect distinct from `IO`,
or (c) the declaration stands and the fallbacks get annotated. This is a semantics choice with
established prior art on all three sides, so per the Design Change Process it wants a proper
verified survey before a call — that survey has **not** been done and is not asserted here.

**Until it is settled, the honest reading of the measurement is: enforcing rule 8 costs three
annotations plus whatever the `panic` decision implies.**

## 7. What is not done

- Enforcement itself. The comparison site is `infer.check_fn_body`, which now holds both values and
  records them; turning the record into a `BodyErr` is the flip.
- Effect-variable solving. `!{e}` is written in `list_each`/`list_fold`/`list_map` signatures in the
  prelude, is never quantified (`scheme_effect_vars` is `Nil` for every declaration) and is never
  bound. Enforcement cannot ship Pure/IO-only: the prelude depends on the polymorphic case on day
  one.
- Writing an inferred effect back to the env, which is what would turn the report from one wave into
  a fixed point.
- The conformance fixture for the first case enforcement must reject
  (`fn shout(s: String) -> Unit = print(s)`). It cannot land before the implementation:
  `tests/conformance/type_error/` has no `XFAIL` manifest — only `tests/conformance/run/` does.

## 8. Fixed in passing

`merge_effects` dropped one variable side (`| _ -> a`), the latent bug recorded in `BACKLOG.md`. It
was unreachable while every callee reported pure; wiring the callee effect through `infer_call_var`
makes it live, so it is fixed here rather than left as a known-wrong input to the numbers above. Two
distinct effect variables now merge into an `EffectRow` instead of one silently winning.

An unrecognised `--phase` argument used to fall through to the default source check and exit 0, so
`--phase efects` ran something else and looked like it had worked. It is now an error. A gate that
asserts on a phase's output is only as good as the driver's willingness to admit the phase does not
exist.
