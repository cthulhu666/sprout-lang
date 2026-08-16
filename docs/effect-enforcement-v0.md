# Effect enforcement — v0

**Status: done.** Spec §7 rule 8 is **enforced** — a body that performs IO under a pure signature is
a compile error. Read §9–§11 for the current state; §1–§8 are the original measurement and remain
accurate as history, but their numbers are superseded.

Landed 2026-08-16, in order: the measurement instrument (§4), `panic` decided **pure** (§6), effect
variables quantified and bound by unification (§9), closure construction correctly attributed as pure
(§10), and enforcement (§11). Together those took the corpus from 12 reported gaps to **zero real
ones** before the flip, so enforcement cost **zero source annotations and rejected zero correct
programs**.

The sequencing was not incidental. Each of §9 and §10 was a prerequisite for the next, and flipping
enforcement at any earlier point would have rejected correct code: before §9, `list_each(print, xs)`
was invisible; before §10, every function that merely *built* an effectful closure reported as
effectful, which was six false positives. The instrument existed to find that out before committing,
and it did.

This document records what was broken, what was fixed to make the gap measurable, the numbers that
came out, and the decisions taken along the way.

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

**Named functions passed as values were a blind spot — closed 2026-08-16, see §9.**
`list_each(\x -> print(x), xs)` was caught; `list_each(print, xs)` was not. The effect crossed a
higher-order boundary only because `infer_lambda` reports the lambda *body's* effect as the effect of
the enclosing expression. That attribution is strictly wrong — building a closure performs no IO —
but it was the only mechanism available, because `unify_types` ignored the arrow's effect field and
never populated `eff_subst`, so a callee's `!{e}` instantiated to a fresh `EffectVar` that nothing
ever bound. Reporting the technically-correct pure there would have made `list_each`/`list_fold` —
the repo's own recommended effectful idiom — report clean, and those are precisely the boundary that
matters. Over-approximating kept them visible until effect unification landed.

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

## 6. Is `panic` an effect? Decided: no

`stdlib/prelude.sprout:1288` declares `extern fn panic(msg: String) -> a !{IO}`. That is why nine of
the twelve gaps exist, and it is a bigger question than the count suggests, because it decides the
shape of the migration rather than its size.

`panic` **diverges** — it never returns. Under the current declaration, any otherwise-pure function
with an internal-error fallback must be annotated `!{IO}`, which is both a large annotation burden
and arguably a false statement: the function is pure in every case where it produces a value.

The alternatives are (a) `panic` becomes pure, (b) `panic` gets its own effect distinct from `IO`,
or (c) the declaration stands and the fallbacks get annotated.

### 6.1 The argument for keeping `!{IO}`, stated first because it is real

`panic` **writes to stderr**. `runtime/sprout_runtime.c:3023` calls `tcp_fail`, which does
`fprintf(stderr, "runtime error: %s\n", msg)` and then `exit(1)` (`:5037`). Sprout's own §6 rule says
a builtin takes `!{IO}` "when evaluating the call may interact with runtime or external state such as
terminal IO". By the letter of that rule `panic` qualifies, and no amount of prior art overrides
Sprout's own normative text.

What the survey settles is whether that rule should be read that literally — because **every
language below aborts by printing a diagnostic too, and not one of them treats that as an effect.**

**And neither, it turns out, does Sprout.** Checking the "only such builtin" claim while writing
this up found the opposite of what was expected: every runtime abort goes through `tcp_fail` →
`fprintf(stderr, …)` → `exit(1)`, and there are **~187 such call sites** sitting behind builtins that
are declared **pure**. `vector_length : Vector a -> Int` aborts on a null vector. So do
`vector_get`, `str_len`, and most of the rest. Under the descriptor-touching reading of §6, all of
them are mis-annotated.

That reframes the whole question. `panic` is not a builtin asking for an exception — it is the only
builtin whose *sole purpose* is to abort, and consequently the only one anybody thought to annotate
`!{IO}` for aborting. It was the **inconsistency**, not the special case, and the decision below
removes it rather than carving anything out.

### 6.2 Prior-art survey

Every row verified against the language's own reference or standard-library documentation. The
question asked of each: *does the possibility of an unrecoverable abort appear in a function's
type?*

| language | recoverable failure | abort / panic | in the abort's type? |
|---|---|---|---|
| **Koka** — full effect system | `exn` effect | `exn` effect | **Yes, but not `io`.** `alias pure = <exn,div>`; `alias io = <exn,io-noexn>`. `exn` is a distinct effect *contained in* both, and `total = <>` excludes it. |
| **Haskell** | `IO` / `Either` | `error :: HasCallStack => [Char] -> a` | **No.** Not in `IO`; returns a bare `a`, callable from pure code. |
| **Rust** | `Result<T, E>` in the signature | `panic!` | **No.** Not part of a function's type; the Reference calls a panic "a response to an error condition that is typically not expected to be recoverable". |
| **Java** | checked exceptions, declared in `throws` | `Error`, `RuntimeException` | **No.** "The unchecked exception classes … are exempted from compile-time checking." |
| **Swift** | `throws` | `fatalError(…) -> Never` | **No.** Not a throwing function; callers need no `throws`. |
| **Zig** | error union `ErrorSet!T` in the signature | `@panic`, `unreachable` | **No.** Outside the error-union system; does not change the return type. |

Two things are unanimous across six languages that otherwise disagree about almost everything:

1. **Abort is never the same thing as I/O.** Not one language folds it into its I/O or side-effect
   channel. Koka is the only one that tracks it at all, and tracks it as a *separate, weaker* effect
   that is explicitly inside its definition of `pure`.
2. **The split is recoverable-vs-not, not observable-vs-not.** Java, Swift and Zig each track
   *recoverable* failure in the signature with real rigour, and each deliberately exempts the
   abort — despite every one of those aborts also printing a diagnostic on the way out.

Java's specification gives the rationale in normative text, and it describes Sprout's nine gaps
exactly:

> Error classes are exempted because they can occur at many points in the program and recovery from
> them is difficult or impossible. A program declaring such exceptions would be cluttered,
> pointlessly.

### 6.3 Why the stderr write does not settle it

The reason the survey is unanimous despite every abort writing a diagnostic is that **an effect is
worth tracking because a continuation can observe it.** `print(x)` matters in a pure function because
the program keeps running and something downstream can tell. `panic` has no continuation: the write
is the last thing that happens before `exit(1)`, and no Sprout expression can observe it. Nothing a
caller does is different for having called a function that might panic, because if it panics the
caller does not run.

That is also why `panic`'s return type is `a` — the same bottom-shaped signature as Haskell's `error
:: [Char] -> a` and Swift's `-> Never`. The type already says "this does not come back". Adding
`!{IO}` on top says "and the rest of your program is impure because of it", which is the part that
is not true.

### 6.4 Decision — `panic` is pure (2026-08-16)

**Adopted: option (a).** `stdlib/prelude.sprout` now declares `extern fn panic(msg: String) -> a`
with no effect. The normative statement is in `docs/spec-v0.md` §6, which also amends the
builtin-effect rule that previously said otherwise; the justification is in
`runtime/APPROVED_BUILTINS`; and `tests/effects/canaries.spr` pins it via `unreachable_arm`, so a
drift back to `!{IO}` fails `just effect-report-smoke` rather than quietly re-inflating the gap count.

Two things were **not** adopted along with it, deliberately:

- **This does not generalise to "diverging calls are pure".** An infinite loop is not an abort. A
  Sprout function that never returns but keeps running can still perform observable I/O, and its
  effects are tracked as usual. The exemption is for termination of the whole *program*, which is
  why it covers exactly one builtin.
- **Sprout still has no `exn` effect.** Koka's answer remains the better long-term shape, and
  adopting purity here does not close that door — it just declines to open it in v0.

It matches five of the six languages surveyed. Option (b) — Koka's answer, a
separate `exn`-like effect — is the most principled but is not affordable in v0: Sprout's effect
system has exactly one concrete label, `!{IO}`, and no `EffectRow` semantics (§7 says v0 has no mixed
or open rows), so adding a second label is an effect-system design change rather than a declaration
change. It is the right long-term shape and belongs with the same work that makes `!{e}` solvable.

Option (c) — leave it, annotate the nine — is the one option with no prior art behind it, and it
buys a signature that is misleading in the direction that matters: it would make nine functions that
are pure in every returning case advertise themselves as impure.

**Consequence for the migration — re-measured after the change, not predicted.** The census in §5
was run again with `panic` pure, same 124 files, same 4337 declarations:

| declared → inferred | before | after |
|---|---:|---:|
| pure → pure | 3617 | 3626 |
| `!{IO}` → `!{IO}` | 672 | 671 |
| `!{IO}` → pure (over-declared) | 13 | 14 |
| **pure → `!{IO}` (gap)** | **12** | **3** |

The three survivors are exactly the lambda-construction over-approximations named in §4 —
`log.stderr_logger`, `http_middleware.with_logging`, `http_web_server.routes` — and not one is a
real finding. **Enforcing rule 8 now costs zero source annotations.**

It should still not ship until `!{e}` is solvable (§7). That was always the real gate; the `panic`
question was never the expensive one, only the loudest.

**The normative edit that came with it.** §6.1's rule said a builtin is `!{IO}` when the call "may
interact with runtime or external state such as terminal IO". Taken literally that makes `panic`
effectful, so the rule itself was amended rather than quietly excepted: it now reads "…**in a way the
rest of the program can observe**", with the observability test stated as normative and the abort
case spelled out. For every builtin but `panic` the two readings agree, which is why the change is a
clarification of intent rather than a reclassification of anything else.

Sources: [Koka `std/core`](https://koka-lang.github.io/koka/doc/std_core.html) ·
[Haskell `base` Prelude](https://hackage-content.haskell.org/package/base-4.22.0.0/docs/Prelude.html) ·
[The Rust Reference — Panic](https://doc.rust-lang.org/reference/panic.html) ·
[JLS SE21 §11](https://docs.oracle.com/javase/specs/jls/se21/html/jls-11.html) ·
[Swift `fatalError`](https://developer.apple.com/documentation/swift/fatalerror(_:file:line:)) ·
[Zig Language Reference](https://ziglang.org/documentation/master/)

## 7. What is not done

Two of the four items here were done on 2026-08-16; see §9. What remains:

- Enforcement itself. The comparison site is `infer.check_fn_body`, which now holds both values and
  records them; turning the record into a `BodyErr` is the flip. **Blocked on correcting
  `infer_lambda`'s attribution** — see §9.4, which is now the only thing standing in front of it.
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

## 9. Effect unification (2026-08-16)

Effect variables are now quantified, freshened per instantiation, and **bound by unification**. This
closes the §4 blind spot and removes the second bullet of §7.

### 9.1 What was actually missing

Not "wire up `unify_effects`" — there was no missing call site. `unify_effects`, `apply_effect_subst`,
`build_effect_repl` and a fully-threaded `eff_subst` in every `InferResult` all existed and were all
correct. Two things were absent, and each alone made the other useless:

1. **Nothing quantified an effect variable.** Every declaration site passed `Nil` for `Scheme`'s
   effect-var field (`infer.scheme_from_fn_parts_inner`, the constructor and record-field scheme
   builders, `iface_codec.method_scheme`). `build_effect_repl` freshens only the variables a scheme
   *names*, so an omitted one survived instantiation verbatim and **every `!{e}` in the program was
   one shared variable**. `types.ftv_effect` could not have found them anyway: it reads a bare
   `Effect`, which for a scheme is only its top-level effect, and the interesting variables live in
   parameter arrows. `types.ftv_effect_type` walks the type for them.
2. **`unify_applied` discarded the arrow's effect field**, matching `TFunc p r _ o` on both sides. So
   even a freshened variable met the other arrow's effect and nothing happened.

### 9.2 Three decisions worth recording

**Arrow-position effect unification binds but never rejects** (`unifier.unify_arrow_effects`). Until
this change, populating arrow effects provably could not reject a program, because the field was
discarded. The moment it is read, every imprecision anywhere in inference becomes a candidate compile
error on correct code — and Sprout's rule is *subsumption*, so two arrows whose effects differ are
not thereby a type error (over-declaring is legal, §7 of the spec). Swallowing `unify_effects`' `Err`
preserves the old guarantee: turning effect inference on can make the report wrong, never the
accept/reject decision.

**`build_fn_type_like` inherits each arrow's effect from the callee** rather than hardcoding
`EffectPure`, exactly as it already inherited ownership via `template_own`. A hardcoded `Pure` is an
assertion the call site is in no position to make; unified against `list_each`'s innermost `!{e}` it
binds `e := Pure` and manufactures a pure reading. This one would not have shown up in testing —
`unify_tfunc` unifies the parameter first, so an IO argument binds `e` before the bogus meeting,
which is then absorbed by the conservative arm.

**`unify_types` returns one `Unified` value carrying both substitutions**, not two results. Several
callers unify speculatively and drop the result on failure; a separately-returned effect substitution
would leak a failed attempt's bindings into the real one. Bundling them makes a discarded unification
discard both halves, with no per-call-site discipline required. Sites behind a result type that
carries only a type substitution opt out through `unifier.no_effect_subst()` — greppable, and sound
because totality (above) means the effect substitution can never change whether unification succeeds.

### 9.2a The second AST walk, and why the canaries could not see it

`iface_codec` carries its own copy of the AST-to-type walk, and it had diverged from `infer`'s three
ways: `!{IO}` normalised to `EffectRow(["IO"])` rather than `EffectIO` (two spellings of one value,
which `unify_effects` has no rule relating); `typeexpr_to_type_with_vars` discarded a `TypeArrow`'s
effect labels; and `params_to_func_type` built every arrow pure. All three are now shared or aligned.

**This was latent, not observed.** Nothing type-checks against a decoded interface file:
`decode_iface_file`'s only consumer is `--check-iface`, which counts entries for well-formedness. The
wrong arrows were written to disk and read by nobody, and no program was ever misjudged. It is
recorded here because the day a consumer appears is the worst possible day to discover the two walks
disagree — and because of how the divergence survived a gate that was built to catch exactly this
class of thing.

`canaries.spr` deliberately has **no module header**, which keeps its report to a handful of lines
instead of hundreds. That also means it never produces an interface file, so every assertion in
`effect-report-smoke` was structurally blind to this path — green, and blind. The fix is a second
fixture that does have a header (`tests/effects/iface_effects.sprout`) with three assertions against
the *encoded* form, which is the only place this walk's output is visible. The before/after is
concrete:

| | before | after |
|---|---|---|
| `emit`'s inner arrow | `(EffectPure)` | `(EffectIO)` |
| `emit`'s scheme effect | `(EffectRow (IO))` | `(EffectIO)` |
| `run_with`'s quantified effect vars | `()` | `(e)` |
| `run_with`'s arrows | all `(EffectPure)` | `(EffectVar e)` |

The general lesson is the one §4 already records in a different form: a fixture chosen to keep output
small is a fixture that has excluded something, and what it excluded is invisible in a green run.

### 9.3 Measurement

Identical corpus, 696 files, 5859 unique declarations, before and after:

| | before | after |
|---|---:|---:|
| unresolved effect variables | 38 | **8** |
| gaps (declared pure, inferred `!{IO}`) | 10 | 11 |

The 8 survivors are the effect-polymorphic functions themselves — `list_each`, `list_fold`,
`range_each`, `range_fold` and their `_go` workers — where an unbound variable is what `!{e}` *means*.
The single new gap is `each_named`, the canary for the closed blind spot. No previously-reported gap
disappeared.

### 9.4 The remaining blocker for enforcement — cleared, see §10

Six real gaps survive (the other five are the canary fixture's deliberate breakage), and **all six are
the same false positive**: a pure function that builds a closure whose body does IO.

| declaration | shape |
|---|---|
| `stdlib.log.stderr_logger` | `logger_with_sink(min, \line -> eprint(line))` |
| `stdlib.http_middleware.with_logging` | body is `\req -> do …`; returns a function |
| `examples.http_web_server.routes` | a `Vec Route` of IO-performing handlers |
| `main.get_at` | `vector_get_direct(v, _)` — the `_` desugars to a lambda |
| `capture_logger` (×2 test files) | `logger_with_sink(Info, \line -> …)` |

Every one is `infer_lambda` reporting the lambda body's effect as the enclosing expression's — the
§4 over-approximation. **Enforcing today would reject six correct programs.**

Correcting it is now safe, and was not before: the attribution was the *only* path by which an effect
crossed a higher-order boundary, so removing it would previously have left `list_each`/`list_fold`
unchecked. The arrow now carries the effect and unification reads it, so the correct attribution
(constructing a closure is pure; the effect lives in its arrow) loses nothing. That is the next
change, and enforcement follows it.

## 10. Closure construction is pure (2026-08-16)

`infer_lambda_expected` returned the lambda **body's** effect as the effect of the lambda
*expression*. It now returns `EffectPure`; the body's effect goes only where it belongs, on the
lambda's arrow. Constructing a closure performs no effect — the effect fires when something calls it.

That lie was load-bearing until §9 landed, for the reason §9's own change removed: the arrow was a
dead end, so over-approximating here was the only path by which an effect crossed a higher-order
boundary. It is now redundant as well as wrong.

### 10.1 It cost six false positives, which is what blocked enforcement

Every function that *builds* an effectful closure reported as effectful:

```sprout
fn routes(list_tmpl: Template, form_tmpl: Template, store: Ref Store) -> Vec Route =
  [ Route("GET", "/users", \req -> handle_list(list_tmpl, store, req)), … ]
```

Evaluating `routes(…)` allocates nine `Route` records and returns them. No handler runs; nothing is
read or written. The declaration is honest and the inference was wrong. `Route`'s field type is
already `HttpRequest -> HttpServerResponse !{IO}` (`http_server.sprout:687`) and the lambda's arrow
already matched it, so the effect was recorded **twice** — correctly on the arrow, incorrectly on the
enclosing expression — and only the second reached the declaration boundary.

### 10.2 The half that is easy to miss

Removing the over-approximation alone makes directly-applied lambdas go quiet, because
`infer_call_general` never read the callee's arrow effect. Two effects meet at a call and both
belong in it: evaluating the callee *expression* (computing which function to call may itself do IO)
and *invoking* the result. Only the first was counted. `infer_call_var` had always merged both —
`call_effect_of` is its equivalent of the second — so this arm was the asymmetric one, and it went
unnoticed because a non-name callee is usually a lambda applied on the spot, whose effect arrived
through the over-approximation by accident.

The canaries pin all three ways an effect can reach a call site, and they are what stop a
"simplification" that turns closure construction pure across the board:

| canary | path | verdict |
|---|---|---|
| `make_shouter` | builds a closure, never calls it | must **not** be flagged |
| `apply_now` | `(\x -> print(x))(s)` — non-name callee | must be flagged |
| `via_local` | `let f = \x -> … in f(s)` — name callee | must be flagged |
| `each_named` | `list_each(print, xs)` — through `!{e}` | must be flagged |

### 10.3 Measurement: the corpus is clean

Same 696-file corpus:

| | before §9 | after §9 | after §10 |
|---|---:|---:|---:|
| real gaps (excluding the canary fixture) | 5 | 6 | **0** |
| unresolved effect variables | 38 | 8 | 8 |

Every gap the report now emits is a canary that is *supposed* to be broken. **Enforcing spec §7 rule
8 costs zero source annotations and rejects zero correct programs** — which is what the whole
instrument was built to find out.

## 11. Enforcement (2026-08-16)

Spec §7 rule 8 is enforced. `fn shout(s: String) -> Unit = print(s)` no longer compiles.

```
14:1: ERROR: check: `main.shout` performs IO but is declared pure
  — add `!{IO}` after its return type (spec-v0.md §7 rule 8)
```

Accepted, deliberately: over-declaring (`fn f(n: Int) -> Int !{IO} = n + 1`), and an unresolved
effect variable. The rule is subsumption, and where the checker does not know it accepts — every
imprecision in effect inference must fail towards accepting a program.

### 11.1 A post-pass, not a boundary error

`check_fn_body` and `check_instance_method` still only *record*. `checker.typecheck_typed` scans the
collected reports afterwards and rejects. Two things follow, and both were the point:

**Every gap is reported in one compile.** Raising the error at the declaration boundary stops
inference at the first offender, so a codebase adopting enforcement would receive its gaps one
compile at a time. The post-pass names them all:

```
16:1: ERROR: check: `shout` performs IO but is declared pure — add `!{IO}` …
  6 more in this program:
    calls_loud
    each_lambda
    …
```

**`--phase effects` survives enforcement.** It calls `typecheck_typed_with_effects` directly and so
never reaches the enforcing wrapper — the census instrument keeps enumerating, which matters most
precisely when enforcement is new and somebody needs the whole list to migrate against. An earlier
draft of this change would have killed the phase at its first gap; that was the wrong trade, since
the phase's entire purpose is the census.

Both paths call `unifier.effect_report_is_gap`. That predicate had a copy in `compile_driver`; it now
has one definition, because a report that disagrees with the checker is worse than no report.

### 11.2 Two ordering details that change results

The record moved to **after** the return-type unify, and resolves with *that* unification's effect
substitution rather than the body's. A return type can bind an effect variable the body left open —
a body returning a closure meets the declared arrow there and nowhere earlier — so resolving too
early reports raw variables where an effect is in fact known. Recording after also means a
declaration with both a type error and an effect gap reports the type error, which is the right
priority.

### 11.3 Coverage

| fixture | pins |
|---|---|
| `type_error/effect_pure_body_does_io.spr` | the `fn` boundary rejects |
| `type_error/effect_pure_instance_method_does_io.spr` | the `instance` boundary rejects |
| `run/effect_over_declared_ok.spr` | over-declaring still **compiles and runs** |
| `tests/effects/canaries.spr` (via `--phase effects`) | the report still enumerates, and its GAP/ok verdicts |

The instance fixture is separate on purpose. `infer` has two sites that bind a body's effect against
a declaration, and the first version of this census instrumented only the `fn` one — leaving all 83
instance methods out of the count, silently. Enforcement inherits that hazard exactly: a flip applied
to one boundary passes every `fn` test while exempting every instance method in the program.

`effect_over_declared_ok` guards the opposite failure: a checker that *unified* the two effects
instead of subsuming them would reject legal code, and the `type_error` fixtures alone would not
notice, since they only pin the direction that must fail.

### 11.4 Cost

Zero source annotations. Zero correct programs rejected. Full suite green, 51/51 examples,
`ir-golden-diff` 0 differences, and the compiler bootstraps itself under enforcement.
