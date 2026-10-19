# Non-escaping closures (`local` parameters) — design note v0

**Status:** implemented 2026-10-11 (approved after a review that changed §3, §6.3, §6.4, §6.5 and
§8). Supporting, not normative: the normative text is `docs/spec-v0.md` §5.8, beside `once`.

**Builds on:** `docs/one-shot-closures-v0.md` (`once`, M4.4a) and `docs/linear-borrowing-v0.md`
(`borrowing`, M4.4b).

---

## 1. Problem statement

A closure cannot capture a **borrowed** value, at any parameter. The rule is right for
`task_spawn`, whose closure runs after the call returns (one-shot-closures-v0 §6.3):

```
task_spawn(scope, \_ -> peek(conn))   # borrow captured
close(conn)                            # runs first; the join later runs the task
```

It is wrong for a callee that is done with the closure before it returns. Sprout cannot tell the
two apart, so it rejects both. Two shapes are blocked by this today.

**1. Bounding a block of work on a resource the caller holds.**

```
fn probe(conn: borrowing net.TcpConnection) -> TimeoutResult Bytes !{IO} =
  with_timeout(500, \_ -> handshake(conn))
```

Rejected: `borrowed value 'conn' captured by a lambda is not yet supported`. Issue #342 asks for
`with_timeout`'s body to be `once`, which would let the caller *move* `conn` in instead. That
compiles, and leaks: on `Expired` the body is force-dropped (`__await_deadline`), and with
`ms <= 0` it never runs, so the moved connection is never closed. spec-v0 §5.8 already says so —
"do not move a linear resource into a task in a scope that may be cancelled or timed out". `once`
turns today's compile error into a silent leak on the path `with_timeout` exists for.

What the caller wants is to **lend** `conn` for the duration of the call and close it afterwards,
on `Completed` and `Expired` alike. That is leak-free, and it needs the callee's promise that the
closure is finished or dropped by the time the call returns.

Per-operation deadlines already exist (`net.read_exact_by`, `write_all_by`, `read_avail_timeout`)
and cover a single I/O call — the Go model (§3). They do not bound a *sequence* of calls, or work
that is not I/O, as one unit.

**2. Effectful combinators over a held resource.**

```
list_each(\x -> write_all(conn, x), xs)
```

Rejected for the same reason. BACKLOG *Higher-order linearity (M4.4) — the general case* records
that this shape's borrow half "needs an escape notion". `list_each` calls `f` and never keeps it.

## 2. Goals and non-goals

**Goals**

1. A parameter may be declared `local`: the callee promises the closure does not outlive the call.
2. A lambda passed at a `local` parameter may capture **borrowed** values.
3. The promise is **checked** in the callee's body, as `once` is (§6.3).
4. `with_timeout(ms, \_ -> f(conn))` compiles with `conn` borrowed, and the caller keeps ownership.
5. Erased: no IR change, like `once` and `borrowing`.

**Non-goals**

- **Moving into `with_timeout`.** Its body stays non-`once`; a move would leak (§1).
- **Region or lifetime types.** No `Scope s` tag, no rank-2 types (§3, rejected).
- **Cancellation-time resource release.** A linear value a task holds when a cancel
  drops it still leaks (one-shot-closures-v0 §9). Unchanged here; lending sidesteps it.
- **Borrowed captures in spawned tasks.** `task_spawn`/`task_fork` stay non-`local`.

## 3. Prior-art survey

| Language | Mechanism | What it licenses | How the callee is held to it |
|---|---|---|---|
| **Swift** | closure params non-escaping by default (SE-0103); `@escaping` opts out | capture of anything; no lifetime question arises | compile error if a non-`@escaping` closure escapes |
| **OxCaml** | `local` parameter mode | passing stack-allocated (`local`) values, incl. closures over them | type error in the callee if the parameter escapes |
| **Rust** | `thread::scope` + `'scope` lifetime | scoped threads may borrow non-`'static` data | lifetimes; the spawn itself is `unsafe` inside std |
| **Haskell** | `runST :: (forall s. ST s a) -> a` | state confined to one `runST` | rank-2 type variable `s` |
| **Go** | deadlines on the connection | per-operation timeout, caller keeps the conn | n/a — no closure involved |
| **Kotlin** | `inline` function's lambda parameters | inlinable lambdas only called or passed on as inlinable arguments | compile error; `noinline` lambdas may be stored |
| **Kotlin** | `withTimeout` cancels the block | timeout over a block | resources released by `use`/`finally` on cancellation |
| **Trio** | `move_on_after` cancel scope | timeout over a `with` block | `Cancelled` raised at the next checkpoint |

Sources, verbatim:

- Swift, *The Swift Programming Language*, Closures, "Escaping Closures": "A closure is said to
  *escape* a function when the closure is passed as an argument to the function, but is called after
  the function returns. … you can write `@escaping` before the parameter's type to indicate that the
  closure is allowed to escape." Reference, Attributes: `@escaping` means "the value is allowed to
  outlive the lifetime of the call."
- Swift Evolution SE-0103, "Make non-escaping closures the default" (implemented, Swift 3.0): "noescape
  closures have also always been the preferred default, since they eliminate a class of retain cycle
  issues."
- Swift stdlib, `withoutActuallyEscaping(_:do:)` (`stdlib/public/core/Builtin.swift`): "Allows a
  nonescaping closure to temporarily be used as if it were allowed to escape." … "It is undefined
  behavior for the escapable closure to be stored, referenced, or executed after the function
  returns."
- OxCaml, `jane/doc/extensions/_02-stack-allocation/intro.md`: "A local parameter is a promise by a
  function not to let a particular argument escape the region where it belongs. In the body of f,
  you'll get a type error if x escapes". `reference.md`, Closures: "Higher-order functions should
  usually mark their function arguments as `local`, to allow local closures to be passed in."
- Rust, `std::thread::scope`: "scoped threads can borrow non-`'static` data, as the scope
  guarantees all threads will be joined at the end of the scope." `library/std/src/thread/scoped.rs`,
  `spawn_scoped`: `unsafe { spawn_unchecked(…) }`. `std::thread::spawn` requires
  `F: FnOnce() -> T + Send + 'static`.
- Haskell, `Control.Monad.ST`: `runST :: (forall s. ST s a) -> a`.
- Go, `net.Conn.SetDeadline`: "A deadline is an absolute time after which I/O operations fail instead
  of blocking. … will return an error that wraps os.ErrDeadlineExceeded."
- Kotlin, Inline functions: "Inlinable lambdas can only be called inside inline functions or passed as
  inlinable arguments"; `noinline` ones "can be manipulated in any way you like, including being
  stored in fields or passed around".
- Kotlin, kotlinx.coroutines "Cancellation and timeouts" (1.7.3): "The timeout event in
  `withTimeout` is asynchronous with respect to the code running in its block … Keep this in mind if
  you open or acquire some resource inside the block that needs closing or release outside of the
  block."
- Trio, reference-core: `move_on_after` creates a "cancel scope", "which contains all the code that
  runs inside the ``with`` block."

**Findings.**

1. **Swift and OxCaml put the promise on the parameter and check it in the callee.** That is the
   shape here, and the shape `once` already has. OxCaml's word is `local`; this note takes it.
2. **Kotlin and Trio time out a block in the caller's own task** and unwind with an exception, so
   `finally`/`use` releases resources. Sprout has no exceptions; `with_timeout` runs its body in a
   child task because dropping a task is the only unwinding the runtime has.
3. **Running the closure in another task needs one unchecked step, everywhere.** Rust's safe
   `thread::scope` spawns through `unsafe`; Swift's `withoutActuallyEscaping` makes an escape
   undefined behaviour rather than a type error. The safety argument is the join, not the type.
   Sprout's equivalent is §6.5.
4. **Region types (Haskell, Rust lifetimes) would check the join too**, and are rejected: a `Scope s`
   tag on every task API plus rank-2 `with_scope` is a type-system extension far larger than this
   problem, for one call site.
5. **Opt-in, not Swift's default.** SE-0103's reasons are retain cycles and optimisation, neither of
   which applies to erased modes under a tracing GC. A non-escaping default would mark every
   unannotated function parameter `local` and, compared invariantly, break every API that stores a
   closure, in all four repos, for a benefit confined to borrowed linear captures. `once` is opt-in
   for the same reason.

Second-class values (Osvald et al., OOPSLA 2016) and Effekt's second-class blocks, likely the
closest prior art, are surveyed in [byte-buffer-v0.md](byte-buffer-v0.md) §4 and §5.5.

## 4. Syntax and semantics

`local` is a parameter mode in the same slot as `borrowing`/`consuming`/`once`, and like them a
contextual keyword — an ordinary identifier everywhere else. It combines with `once`:

```
export fn with_timeout(ms: Int, body: local Unit -> a !{IO}) -> TimeoutResult a !{IO}
export fn with_scope(body: once local Scope -> a !{IO}) -> a !{IO}
```

**Callee promise.** The closure does not outlive the call: it is not returned, stored, or passed
anywhere it could run after the call returns. It may be called any number of times (unless also
`once`).

**Caller licence.** A literal lambda at a `local` parameter may capture **borrowed** values: a
`borrowing` parameter, or an owned linear binding the lambda only reads. The borrow lasts for the
whole call. A lambda at a `once local` parameter may also move values in, exactly as at `once`.

## 5. Type-system impact

`local` rides on the function type, as `once` does (M4.6). `types.Ownership` gains `OwnLocal` and
`OwnOnceLocal`. Only these two combinations are meaningful — `local` is function-typed only, and
`borrowing`/`consuming` are linear-typed only — so a fourth and fifth case beat a second field on
`TFunc` (127 references in the compiler). Predicates `own_is_once` and `own_is_local` keep
`linear_check` reading one axis at a time. Compared invariantly at unification, like the other
modes.

`ast.ParamMode` gains `ModeLocal` and `ModeOnceLocal`; `IfaceFile` bumps its version.

## 6. Checking rules

All in `linear_check`, beside `lin_once_arg`.

### 6.1 A lambda at a `local` position

Walk the body as `lin_once_lambda_body` does, giving consumed captures `bc` and borrowed captures
`bb` (minus `bc`).

- **`bb`** — admitted, and recorded as borrows **at the call**.
- **`bc`** — admitted only at `once local` (a move, as at `once`); at plain `local` the closure may
  run many times, so a consumed capture is rejected, as today.
- **Whole-call borrow.** A name in `bb` may not be consumed by **any other argument of the same
  call**, in either order. `f(\_ -> peek(c), close(c))` evaluates `close` before `f` runs the
  closure. Argument-order `seq2` alone would accept the lambda-first order, so this is a new check
  over the call's arguments.

### 6.2 A non-lambda at a `local` position

Walked as an ordinary argument. A named function captures nothing.

### 6.3 The callee must keep its promise

A `local` parameter `p` may appear only as:

1. the callee of a call, `p(x)`;
2. an argument at another `local` (or `once local`) position;
3. a capture of a lambda that itself sits at a `local` position — that lambda cannot outlive the
   call either. Its body is checked by these same rules, and its **result counts as returned**.

Anything else is an error: returned, put in a constructor, record, tuple, `Ref` or `Vec`, passed at
an ordinary position, captured by an ordinary lambda, or bound with `let` (conservative — aliasing
is not tracked, and no stdlib use needs it).

Rule 3's last clause is load-bearing. Without it `p` escapes through the lambda's result:

```
fn id_thunk(f: local Unit -> F) -> F = f(())                       # rule 1
fn leak(p: local Unit -> Int) -> Unit -> Int = id_thunk(\_ -> p)  # p comes back out
```

This is a **dedicated escape walk** over the typed AST, not a reuse of `lin_consumed` as
`once_honesty` is: `lin_consumed` records `p(x)`, `return p` and `Cons(p, …)` as the same use, and
this rule must tell them apart. It reads each argument's slot from the callee's type spine
(`spine_own`), as `lin_call_args` does. It runs before `once_honesty`, which reports every error
as a reuse, so an escape is named as an escape. `once local` gets both.

The same walk checks `once` parameters, with the slot required to be `once` rather than `local`
(`docs/one-shot-closures-v0.md` §6.5). A `once local` parameter is checked for both, so it may be
passed on only at a `once local` position: a plain `local` callee may call it many times.

### 6.4 `local` only on a function-typed parameter

Rejected elsewhere, including on a type variable, for the reason `once a` is: the licence rests on
the callee being unable to keep the closure, and a value of unknown type can be returned.

**Never on a lambda's own parameter.** §6.3 runs only over declared functions (`fn_linear_gate`),
so a lambda's promise would be checked by nobody:

```
fn run(g: (local (Unit -> Int)) -> (Unit -> Int), c: borrowing Conn) -> Unit -> Int =
  g(\_ -> peek(c))
run(\w -> w, conn)     # w escapes; the borrow outlives the call
```

So `local` is never pushed down onto an unannotated lambda parameter (`infer.mode_or_slot` seeds
only `borrowing`, and stays that way), and `local` written on a lambda parameter is rejected. With
nothing seeded, `\w -> w` at that slot is an ownership mismatch, as it is for `once` today
(`type_error/lambda_slot_once_not_pushed_down`).

### 6.5 The trust boundary: extern parameter modes

An `extern fn`'s body is C, so its parameter modes cannot be checked. They are trusted, as its
types and effects already are. One extern carries `local`:

```
extern fn __task_fork(scope_id: Int, work: once local Unit -> a !{IO}) -> Int !{IO}
```

Its `once` is a claim about the C side (a task runs at most once), as `__scope_spawn`'s `once` is.
Its `local` is different: the scheduler does keep the closure, so the claim holds only because of
how the extern is called. This is the Sprout counterpart of Rust's `unsafe
{ spawn_unchecked(…) }`, so only the module that declares it may use it. **An extern whose
signature writes `local` anywhere is reachable only from its declaring module.** That covers:

- a parameter;
- an arrow inside a parameter or return type;
- any type the signature names, followed across modules, with a guard for recursive types:
  aliases, and the fields of sum types, records and `wrap`s.

A returned `(local X) -> Y`, bare or inside a constructor, hands the same unchecked licence to
whoever calls it. The bundler decides on the qualified signature, so type names are already
canonical there. `prelude.X` goes through the same check as a bare `X`. This is the one exception
to spec §Externs, which makes every other extern reachable by bare name from any importer. Without
it, any module importing `stdlib.task` could lend a borrowed value to a task that outlives the
owner's release. The bundler marks a reference from another module, and inference reports it (the
`@canonical:` mechanism).

A module may redeclare an extern itself and then reach it, since the trust claim is then its own.
That is safe only because several declarations of one extern must agree on the full signature.
Inference keeps the first declaration's scheme, so a disagreeing redeclaration would otherwise be
typed with someone else's `local`. Both uses are in `stdlib/task.sprout`:

- `task_fork(scope, work: once Unit -> a)` passes a **non-local** closure. It cannot capture a
  borrow (§6.1 applies only at `local` positions), so the stronger declaration changes nothing.
- `run_with_deadline` passes `with_timeout`'s `local` body. It is sound because the closure never
  runs after `with_timeout` returns:
  - the caller is parked in `__await_deadline` from the fork until the child finishes or is
    dropped, so no caller code runs while the closure might;
  - `Completed`: the child finished. `Expired`: `__await_deadline` force-dropped it before returning
    (`tests/task_io_smoke/timeout_io_drop.spr` pins the drop, fd deregistration included, for
    a body parked in the poller);
  - `ms <= 0`: never forked;
  - a nested `with_timeout`, or a cancel of an OUTER scope while the caller is parked here, aborts
    loudly: `force_drop_task` refuses a task whose `deadline_child` is set. While the owner instead
    sits in the ready queue (the poll-batch case), `__scope_cancel` does not drop it at all;
  - `with_scope`'s unconditional `__scope_join` follows in every case.

A comment at the extern states the obligation for any future caller. `__scope_spawn` stays
non-local.

**Obligation on the deferred tree-cancel cascade.** Once a cancel may drop a deadline owner instead
of aborting, it must drop the child first: otherwise the child runs against a value lent from a
frame that is gone. BACKLOG: *Tree-cancel cascade must drop a deadline's child before its owner*.

## 7. Error-message impact

- Borrowed capture at a non-`local` position — today's message, with a door added:
  `"borrowed value 'conn' captured by a lambda is not yet supported (…); if the callee does not keep
  this closure past the call, declare that parameter `local`"`.
- Callee breaks the promise:
  `"`local` parameter 'body' may outlive the call: <returned | stored | passed to a non-local
  parameter | captured by a closure that may outlive the call>. Declaring it `local` promises callers
  it does not, and that promise is what lets them lend it borrowed values"`.
- Whole-call borrow: `"'conn' is lent to a `local` closure for the whole call and cannot be consumed
  in another argument of it"`.
- `local` on a non-function parameter: mirrors the `once` message.
- An extern whose signature writes `local`, reached from another module (§6.5): it names the
  declaring module and says the promise is kept only by that module's callers.
- An extern declared twice with different signatures: names it and shows both signatures.
- A `once` parameter handed on where it could run again: `"`once` parameter 'w' may run more than
  once: <returned or stored | passed to a parameter that does not promise `once` | captured by a
  closure that may run more than once>"`, plus why the promise matters.
- Mode mismatches name the axis that differs: `once`, `local`, or both. That holds at unification
  and between a class method and its instance.

## 8. Compatibility and migration

Additive: `local` is contextual, unmarked parameters check as before, and the change only accepts
more programs at `local` positions.

- `stdlib/task.sprout`: `with_scope.body` becomes `once local`; `with_timeout.body` and
  `run_with_deadline.body` become `local`; `run_with_deadline` calls `__task_fork` directly;
  `__task_fork.work` becomes `once local` and `__scope_spawn.work` becomes `once`. `with_scope` keeps `once`, so existing moves into it still
  compile.
- **Bootstrap.** If no file in `compile_driver`'s closure writes `local`, the committed seed parses
  everything and no 2-step bootstrap is needed. `stdlib/task.sprout` is outside that closure — to be
  re-verified at implementation time.
- **Golden IR.** The parser gains the string `"local"`, which shifts the compiler-bundle golden's
  string-constant indices, as `once` did. Read the diff, do not just regenerate it.
- **#342.** Resolved by this: `with_timeout` takes a lending closure, and the issue's moving
  version is answered by §1.

**Follow-up (separate change).** Mark the prelude's effectful combinators' function arguments
`local` — `list_each`, `list_fold`, `list_map`, the `vec_` family — closing the borrow half of the
BACKLOG M4.4 entry. Its design must decide `Functor.fmap`: left plain, `map(\x -> peek(c) + x, xs)`
is rejected where `list_map` accepts it; made `local` in the class, it forbids any instance whose
`fmap` keeps the function (a function functor). No such instance exists today. A comprehension
reading a borrowed value becomes legal there too, since comprehensions elaborate to folds. The `_`
placeholder form, `list_each(\x -> write(c, x), _)`, desugars to an ordinary lambda and stays
rejected. The prelude is in the compiler's closure, so this one needs the 2-step
bootstrap, and is kept out of the first change for that reason alone.

## 9. Tests

TDD, each red before the change.

- `tests/stdlib/test_local_closures.spr`: borrowed capture at a `local` position; called many times;
  `once local` with a move; a `local` parameter forwarded to another `local` position; captured by a
  lambda at a `local` position.
- `tests/stdlib/test_task_timeout.spr`: `with_timeout` over a borrowed `Token`, `Completed` and
  `Expired`; the caller releases it after both.
- `tests/task_io_smoke/`: a borrowed `TcpConnection` read inside `with_timeout` against a silent
  peer — `Expired`, then the caller closes the connection.
- Rejections in `tests/conformance/type_error/`: `local_param_returned`, `local_param_stored`,
  `local_param_escapes_via_ordinary_slot`, `local_param_captured_by_ordinary_lambda`,
  `local_capture_moved_not_once`, `local_borrow_consumed_in_other_arg`, `local_on_non_function`,
  `local_on_type_variable`, `local_param_escapes_via_local_lambda_result`,
  `lambda_param_local_rejected`, `lambda_slot_local_not_pushed_down`, and
  `borrow_capture_at_ordinary_slot` (today's rule, still in force).
- Private extern (§6.5): `private_local_extern_reached_from_another_module`,
  `private_local_extern_named_without_borrow`. `stdlib.task`'s own calls are the passing case.
  In `tests/conformance/package_resolution/`, `local` in a return type, in a parameter type, via an
  alias, via an alias imported two hops away, and in a sum-type, record or `wrap` field; a
  recursive type without `local` stays public. `prelude.X`:
  `tests/stdlib/compiler/test_bundle_prelude_private_extern.spr`.
- Redeclared externs: `extern_redeclared_signature_differs`,
  `extern_task_fork_redeclared_without_local`; passing: `tests/stdlib/test_extern_redeclare.spr`
  and `app_extern_redeclare_renamed_vars` (type and effect variables renamed).
- Handing a mode on (§6.3): `once_local_forwarded_to_plain_local_slot`,
  `once_param_forwarded_to_plain_slot`, `once_param_returned`, `once_param_let_bound_and_reused`,
  `once_param_in_tuple`, `once_param_captured_by_ordinary_lambda`,
  `once_local_relayed_through_plain_local_slot`. The passing forwards are in
  `test_once_closures.spr` and `test_local_closures.spr`.
- Mismatch messages: `once_local_mismatch_names_once`, `once_vs_local_mismatch_names_both`,
  `local_instance_omits_class_local`, `once_local_instance_drops_local`,
  `once_local_instance_drops_both`.
- Erasure: one-token byte-identity case in `tests/stdlib/compiler/test_borrow_erasure.spr`.

## 10. Spec/docs

- `docs/spec-v0.md` §5.8: a **Non-escaping closure parameters (`local`)** paragraph after `once`;
  the leak paragraph gains "lend instead of move" for `with_timeout`.
- `docs/one-shot-closures-v0.md` §6.3: point at this note for the borrow case.
- `docs/idiomatic-sprout.md` §"Let the compiler enforce release it exactly once": the
  `with_timeout` lending shape, and how to write your own lending combinator.
- `BACKLOG.md`: the M4.4 entry's "escape notion" line points here until the prelude follow-up lands.
- `examples/linear_resources.sprout`: every parameter mode on one resource, offline.

## 11. Where `local` fits

`local` belongs on a callback the callee calls, or hands on, and is done with before it returns:
"run this inside my call".

**Choosing a mode.** Pick by what the caller's lambda must capture:

| callback parameter | callee may | caller's lambda may also |
|---|---|---|
| plain | keep it, call it any number of times | — (unrestricted values only) |
| `once` | keep it, call it at most once | move a linear value in |
| `local` | call it any number of times, not keep it | borrow a linear value |
| `once local` | call it at most once, not keep it | move in and borrow |

**In use.** `stdlib.task`: `with_timeout` (`local`) and `with_scope` (`once local`).

**Your own combinators.** A retry loop, a timing wrapper, a "run in a transaction" helper: each
calls its body and returns, so marking the body `local` lets callers lend it a borrowed resource.
The callee check (§6.3) holds the combinator to it. `examples/linear_resources.sprout` has one.

**Planned: the prelude combinators** (§8). Until `list_each` and the rest take `local`, iterate in
a recursive function that takes the resource `borrowing`.

**Where it does not fit.**

- A callback the callee keeps: an HTTP handler, middleware (`http_middleware.with_logging` returns
  a closure holding `handler`), `task_spawn`/`task_fork` (the work runs after the call returns).
  Marking any of these `local` is a compile error in the function that keeps it (§6.3); an
  extern is trusted instead (§6.5).
- A plain value: `local` is function-typed only (§6.4); `borrowing` already lends a linear value.
- A class method whose instances may keep the function: `local` in the class binds every instance
  (the `Functor.fmap` question, §8).

**Not a speed feature yet.** Modes are erased (§2), so a closure at a `local` slot is
heap-allocated like any other. OxCaml's `local` exists mainly to stack-allocate. A `local` slot
gives Sprout the same fact without escape analysis (BACKLOG *Non-escaping combinator closures
allocate*), but using it is a separate design: it touches rooting and codegen, a `with_timeout`
body runs in a child task, and externs are trusted rather than checked (§6.5).
