# The nullary type collapse (v0)

Status: **BUG REPORT, design decided (§9, Option A′, 2026-09-08), unimplemented.** Filed
2026-09-07. Every claim below was verified
by compiling and running on master `40808541` (tree `e73d8152`); the commits since are
docs-only, so no compiler source has moved under it.

A nullary function's type *is* its return type. `() -> T` collapses to `T`, so a function
value and a plain `T` are the same type. One absent type constructor produces three
symptoms, one of which is memory-unsafe.

## 1. The fact

For `fn io_thunk() -> Int !{IO}` and `fn io_unary(n: Int) -> Int !{IO}`, `--phase check`
prints:

```
main.io_thunk : Int !{IO}
main.io_unary : Int -> Int !{IO}
```

The unary function keeps an arrow and carries its effect on it. The nullary one has no
arrow: the effect lives on the `Scheme`, and the *type* is bare `Int`, indistinguishable
from any other `Int`.

Referencing the name confirms it. Passing each to `fn want_string(s: String) -> String`:

| expression | diagnostic |
|---|---|
| `want_string(io_thunk)` | `Type mismatch: String vs Int` |
| `want_string(io_unary)` | `Type mismatch: String vs Int -> Int !{IO}` |

## 2. Symptom 1 — effect laundering

```sprout
fn launder() -> Int =
  let t = io_thunk
  in t()
```

Compiles, links, runs, prints `io`. `--phase effects` reports
`main.launder: declared pure, inferred pure`.

The direct spelling `fn launder() -> Int = io_thunk()` **is** rejected by spec §7 rule 8,
so the hole is precisely the local or expression callee.

Two mechanisms in `infer` sit on top of the collapse and were filed separately as
"conservative" zero-arg effect losses. They are this bug: `call_effect_of` with
`argc <= 0` returns an unfreshened program-level `EffectVar`, and `infer_call_general`'s
`arrows_effect(t, argc)` returns `Pure` for `argc <= 0` with no scheme fallback. Neither
is conservative — accepting a program that runs IO under a pure signature is the rule-8
hole — and neither is separately fixable, because the effect they should read is not on
any arrow.

## 3. Symptom 2 — a function reference enters integer arithmetic

```sprout
fn take(x: Int) -> Int = x + 1

fn main() -> Unit !{IO} = print(int_to_string(take(io_thunk)))
```

Type-checks. Prints `35184372088841` — the closure handle plus one — and never prints
`io`. The emitted IR shows the checker and codegen disagreeing about what the expression
is, with nothing to reconcile them:

```llvm
define i64 @main.take(i64 %p$x) {
  %t$0 = add i64 0, 1
  %t$1 = add i64 %p$x, %t$0        ; arithmetic on whatever arrives
  ret i64 %t$1
}

define i64 @__sprout_user_main() {
  %t$0 = call i64 @sprout_alloc_closure(i64 8, i64 0)
  store ptr @__sprout_ir_eta_main.io_thunk_0, ptr %t$0$raw
  %t$1 = call i64 @main.take(i64 %t$0)   ; the closure handle, as an Int
  ...
}
```

The checker read `io_thunk` as `Int` and accepted it against `take`'s `Int` parameter.
Codegen read the same expression as a function reference and built a closure for it.

## 4. Symptom 3 — an integer enters callee position and is dereferenced as code

```sprout
fn launder(x: Int) -> Int = x()

fn main() -> Unit !{IO} = print(int_to_string(launder(42)))
```

Type-checks as `main.launder : Int -> Int`. Segfaults (exit 139). The IR jumps through
address 42:

```llvm
define i64 @main.launder(i64 %p$x) {
  call void @sprout_closure_arity_check(i64 %p$x, i64 0)
  %t$0$env_ptr = inttoptr i64 %p$x to ptr
  %t$0$code = load ptr, ptr %t$0$env_ptr
  %t$0 = call i64 (i64) %t$0$code(i64 %p$x)
  ret i64 %t$0
}
```

The runtime guard does not help. `sprout_closure_arity_check`
(`runtime/sprout_runtime.c:1556`) rejects `payload == NULL` and then reads a header off
the raw handle, so every non-null garbage value is dereferenced by the check itself.

This is reachable from ordinary well-typed source. Sprout has no unsafe construct to blame.

## 5. The control — it is exactly arity 0

The same shape one arity up is correctly rejected:

```sprout
fn launder(x: Int, n: Int) -> Int = x(n)
```

```
ERROR: check: Call type mismatch: Type mismatch: Int vs Int -> $t2553
```

At arity ≥ 1 the checker builds an expected arrow and unifies against it. At arity 0
there is no arrow to build — the expected type is just the result type, and every `Int`
satisfies it.

## 6. Already half-known

`BACKLOG.md`'s 2026-08-18 hover fix states the representation fact exactly: *"a nullary
function has no arrow to carry the effect."* Hover, `:type` and the analysis service
reported every nullary effectful function as pure. That was closed at the display layer —
a bare name now answers with the `Scheme` the checked env already holds — which was right
for hover and left the type-level collapse untouched.

Symptoms 2 and 3 are new here. Neither is an effect bug, and no effect check reaches them.

## 7. Why this blocks effect subsumption

`docs/effect-subsumption-v0.md` (branch `effect-arrow-subsumption-detector`, pushed, not
merged) describes four boundaries at which a declared effect escapes. Parts 1–3 all ask
"do these two effects agree?". A missing type constructor is not a disagreement, which is
why that document's instrumented compiler — which rejects *every* concrete pure/IO arrow
meet — reports **zero errors** on symptom 1.

Its part 4 (§6.6) proposes *"read the arrow's effect at `argc <= 0`"*. That is not
implementable: at arity 0 there is no arrow to read. Two consequences for that design:

- §6.6 should be dropped from it and made to depend on this document, leaving parts 1–3
  to land on their own measured zero migration cost.
- Its §7 promise of "check-only, no IR change" does not survive either resolution below.

## 8. Prior art

Each row was read from the cited reference.

| language | a zero-parameter function's type | collapses to the result type? |
|---|---|---|
| OCaml | `unit -> t` — `Stdlib.read_line : unit -> string` | no |
| Standard ML | `unit -> t` — `TIMER.startCPUTimer : unit -> cpu_timer` | no |
| Rust | `fn() -> T`; the parameter list in `BareFunctionType` is optional | no |
| Swift | `() -> T`; `function-type → (parameter-clause?) -> type` | no |
| Scala | `=> T` for a parameterless *method*; `() => T` = `Function0[T]` for the value | no |
| Haskell | a binding with no arguments has the type of its value | **yes** |

**The one language that collapses is the one that is lazy.** In Haskell a nullary function
and its value are operationally the same thing, so there is nothing to tell apart — lambda
abstraction requires `n ≥ 1` parameters and no zero-argument arrow exists. Sprout is
strict, which is what makes the same collapse unsafe here: the thunk and the `Int` it
returns are different values, and §3 shows the checker and codegen each picking a
different one.

**Scala is the closest case to Sprout's syntax and still does not collapse.** It permits
`def a: Int` with no parameter list — the spelling Sprout has — and types it `=> Int`, not
`Int`. The spec is explicit that "method types do not exist as types of values": naming the
method converts it to `() => Int`. So admitting the parameterless *declaration* does not
require collapsing the *type*. Scala pays for that syntax with a second type former rather
than with an absent one.

Rust and Swift take a third shape worth naming: a genuine empty parameter list (`fn()`,
`()`) rather than a unit parameter. That is a real alternative to Option A — `() -> T` as
its own arrow rather than sugar for `Unit -> T` — and it leaves nullary call sites passing
nothing.

**Option B has no precedent here.** No surveyed language leaves the zero-argument function
type unspellable and compensates with position checks. Haskell manages without the type
because laziness removes the need for it, not because it guards the two positions.

## 9. The decision (open)

**Option A — give a nullary function a real arrow type**, `Unit -> T`. Fixes all three
symptoms uniformly, and the runtime already models arity 0
(`sprout_alloc_closure(size, arity)` accepts `0`; the closure header has an arity field).
Cost: ABI-visible, and it changes what every nullary declaration's type prints as.

> **`Unit -> T` is already taken.** `fn takes_unit(u: Unit) -> Int` checks as
> `main.takes_unit : Unit -> Int` today (verified 2026-09-08). Option A would give
> `fn takes_nothing() -> Int` that same type, merging two declarations that are distinct
> in the surface syntax. A′ does not, because Sprout already *writes* the empty parameter
> list — `fn f()`, not `fn f(u: Unit)` — so `() -> T` is the type that matches what the
> declaration says.

**Option A′ — an arrow with an empty parameter list**, `() -> T`, distinct from `Unit -> T`.
The Rust/Swift shape. Same fix as A and the same ABI visibility, but a nullary call site
keeps passing nothing rather than a unit value, and `f()` stays the call spelling instead
of becoming `f(())`. Costs a second arrow arity in `types` that A does not.

**Option B — check the two positions instead.** Reject a non-arrow in callee position, and
reject a function reference where a non-function is expected. Cheaper and not ABI-visible,
but `() -> T` stays unspellable as a type, so "a thunk that performs IO" cannot be written
as a record field or a parameter — which is the shape §6.6 needs. §8 found no language
that resolves this the way B proposes.

**Decided 2026-09-08 — Option A′.** A asks the type system to describe a parameter the
programmer did not write: the declaration syntax is an empty parameter list, not a `Unit`
binder, and `Unit -> T` already belongs to `fn f(u: Unit) -> T`. The notation is free —
`()` is a parse error in type position today (`let u: () = ()` → `Expected type at 3:9`),
so `() -> T` collides with nothing.

Measured cost, so it is not relitigated as a surprise:

- `types.Type`'s arrow is `TFunc Type Type Effect Ownership` — curried and binary, with no
  way to say "no parameter". A′ needs a new constructor, not a new field. A sentinel
  parameter type would be the magic-value spelling of the same change.
- **73 `TFunc` match arms** across 12 files must each decide what they do at arity 0:
  `infer` 23, `types` 12, `linear_check` 12, `lowering` 11, `unifier` 4, `resolve` 4, and
  one or two each in `verify_dispatch`, `typed_ast`, `iface_codec`, `dce`, `ast_to_ir`,
  `analysis_service_driver`.
- The collapse itself is two lines: `build_fn_type` (`infer.sprout:790`) and
  `build_fn_type_modes` (`:812`) both return `ret` unchanged at `Nil`.
- `.iface` serialises types, so the format version goes 6 → 7
  (`iface_codec.decode_iface_version`) and CI's iface cache purges.

This also settles §10: the diagnostics can now be committed to, so the three fixtures
deferred there can be written.

## 10. Tests — deferred deliberately, with the reason

`_test-reject` takes an xfail list of basenames (`justfile:761`); `type_error/` currently
passes an empty one. It self-heals: a listed fixture that starts matching reports
`UNEXPECTED MATCH (remove from xfail)` and the gate goes red. So a red fixture *can* be
quarantined here.

The blocker is that the match is `grep -qF` against the `.err` file (`justfile:798`), so a
fixture must commit to the diagnostic's wording — and which diagnostic is correct depends
on §9. A guessed string would never self-heal, which is worse than no fixture. File all
three with the design decision.

> `BACKLOG.md` claimed on 2026-08-16 that `tests/conformance/type_error/` "has no `XFAIL`
> manifest". That was false when written: the `xfail` parameter had existed since
> 2026-06-30 (`4393b064`). The claim was cited again in 2026-09-07 as a reason not to file
> repros, which is the cost of leaving a wrong line in place.
