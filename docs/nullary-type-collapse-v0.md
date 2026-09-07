# The nullary type collapse (v0)

Status: **BUG REPORT, design undecided.** Filed 2026-09-07. Every claim below was verified
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

## 8. The decision (open)

**Option A — give a nullary function a real arrow type**, `Unit -> T`. Fixes all three
symptoms uniformly, and the runtime already models arity 0
(`sprout_alloc_closure(size, arity)` accepts `0`; the closure header has an arity field).
Cost: ABI-visible, and it changes what every nullary declaration's type prints as.

**Option B — check the two positions instead.** Reject a non-arrow in callee position, and
reject a function reference where a non-function is expected. Cheaper and not ABI-visible,
but `() -> T` stays unspellable as a type, so "a thunk that performs IO" cannot be written
as a record field or a parameter — which is the shape §6.6 needs.

Undecided. The choice determines whether the effect-subsumption work can proceed as
check-only.

## 9. Tests — deferred deliberately, with the reason

`_test-reject` takes an xfail list of basenames (`justfile:761`); `type_error/` currently
passes an empty one. It self-heals: a listed fixture that starts matching reports
`UNEXPECTED MATCH (remove from xfail)` and the gate goes red. So a red fixture *can* be
quarantined here.

The blocker is that the match is `grep -qF` against the `.err` file (`justfile:798`), so a
fixture must commit to the diagnostic's wording — and which diagnostic is correct depends
on §8. A guessed string would never self-heal, which is worse than no fixture. File all
three with the design decision.

> `BACKLOG.md` claimed on 2026-08-16 that `tests/conformance/type_error/` "has no `XFAIL`
> manifest". That was false when written: the `xfail` parameter had existed since
> 2026-06-30 (`4393b064`). The claim was cited again in 2026-09-07 as a reason not to file
> repros, which is the cost of leaving a wrong line in place.
