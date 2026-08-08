# One-shot closures (`once` parameters) — design note v0

**Status:** implemented (2026-08-08). Supporting, not normative — the normative text is
`docs/spec-v0.md` §5.8.

**Milestone:** M4.4a — a deliberately narrow slice of the deferred M4.4 (higher-order linearity).

---

## 1. Problem statement

`stdlib/net.sprout` ships a complete linear socket API: `type linear TcpConnection`, four
`borrowing` I/O operations, and exactly one `consuming close`. It has **no consumer**.

`stdlib/http_server.sprout` is the only real server in the tree and uses none of it. Every
signature is `conn: Int`, it imports the raw `tcp_*` builtins directly, and `write_and_close`
(`:440`) hand-closes on both the `Ok` and the `Err` arm — discipline that a linear type exists
precisely to mechanize, maintained by hand instead.

One rule separates the two files. `serve_loop:476` and `serve_forever_loop:493` both read:

```
task_spawn(scope, \_ -> handle_connection(conn, handler))
```

and `linear_check.lin_lambda` rejects a linear value captured by a lambda. Reproduced against a
ten-line file mirroring that shape:

```
11:21: ERROR: check: linear value 'conn' captured by a lambda is not yet supported
       (higher-order linearity is deferred)
```

The rejection is correct under its own premise — a closure may run 0..n times and Sprout tracks no
call count — but the premise is stronger than this program needs. `task_spawn` hands its thunk to
the scheduler, which invokes it once; user code never holds it. The general problem is hard; *this*
shape is not.

## 2. Goals and non-goals

**Goals**

1. A parameter may be declared to receive a closure the callee invokes **at most once**.
2. A lambda passed at such a parameter may **move** linear captures into itself; each moved capture
   is consumed at the call site and must be consumed exactly once inside the body.
3. The property rides in the **type**, per M4.6 — no name-keyed side table.
4. `stdlib/http_server.sprout` compiles end-to-end against `net.TcpConnection` / `net.TcpListener`.
   This, not the feature, is the definition of done.
5. The annotation is **erased** — it reaches no IR pass, as `borrowing` does not.

**Non-goals**

- **General M4.4.** No call-count tracking, no multiplicity polymorphism, no escape analysis. A
  lambda at an unannotated parameter keeps today's rejection verbatim.
- **Linear lambda *parameters*.** `\c -> close(c)` with `c` linear stays rejected. It is a separate
  sub-problem (it needs the lambda's own parameter types to carry modes) and http_server does not
  need it.
- **Captured *borrows*.** Rejected under `once` too — see §6.3. This is not an oversight; it is the
  same call Rust makes.
- **Closing the cancellation hole** (§9). Out of scope, documented rather than fixed.
- **Linear `Scope`**, `&`/`&mut`, arrow-type `borrowing` syntax — unchanged, still filed.

## 3. Prior-art survey

Every claim below is quoted from the language's own reference or standard-library documentation.

| Language | Spelling | Bound it gives | Captured borrows |
|---|---|---|---|
| **Rust** | `FnOnce` (trait) | at most once | forbidden at `thread::spawn` via `'static` |
| **OxCaml** | `once` (mode) | at most once | n/a (uniqueness axis) |
| **Linear Haskell** | `%1 ->` (arrow multiplicity) | exactly once, on the *argument* | n/a (no borrowing) |

**Rust** is the closest analogue, and `std::thread::spawn` is the closest single signature —
it is `task_spawn` with a different scheduler:

```rust
pub fn spawn<F, T>(f: F) -> JoinHandle<T>
where F: FnOnce() -> T + Send + 'static, T: Send + 'static,
```

`FnOnce` is documented as: *"Instances of `FnOnce` can be called, but might not be callable
multiple times. Because of this, if the only thing known about a type is that it implements
`FnOnce`, it can only be called once."* A closure gets it *"automatically … [when it] might consume
captured variables"*, and `move` *"gives ownership of captured values to the thread."* The `'static`
bound is what forbids captured borrows.

**OxCaml** reaches the same rule from the uniqueness side: *"if the closure was invoked more often,
it could not use the value uniquely each time. Such closures are at mode `once`"*, and *"When a
`unique` value is consumed in a closure, this closure can be invoked at most once."*

**Linear Haskell** puts multiplicity on the arrow — *"A function `f` is linear if: when its result
is consumed exactly once, then its argument is consumed exactly once"* — which annotates the
argument rather than the closure's call count. Its own guide is the best argument for taking a
narrow slice: the extension is *"currently considered experimental, expect bugs, warts, and bad
error messages; everything down to the syntax is subject to change"*, and among its listed
limitations, *"There is currently no support for multiplicity annotations on function arguments."*
The full generalisation is unfinished in the system that has pursued it longest.

**Swift SE-0377** remains the reference for M4.5/M4.6's parameter conventions and is unchanged here.

**The convergent finding, and it decides §9:** every production system gives **at most once**, not
exactly once. Rust can afford the weaker bound because a never-called `FnOnce` still runs `Drop` on
its captures. Sprout has no destructors, so at-most-once alone does not give leak-freedom — the
missing half has to come from the callee's runtime contract, not from the type.

## 4. Syntax and semantics

`once` joins `borrowing` and `consuming` in the existing parameter-modifier slot:

```
export fn task_spawn(scope: Scope, work: once Unit -> Unit !{IO}) -> Unit !{IO}
```

It is **contextual**, not reserved, and reuses `parser.param_mode_at`'s existing disambiguation
guard verbatim: the modifier reading requires a type atom to follow, so `x: once` still names a type
called `once`. Deliberately *not* arrow-type syntax (`(borrowing File) -> Int`, still deferred) —
`once` describes how the *parameter* is received, exactly like `borrowing`, so the parameter slot is
where it belongs, and this change needs no arrow-type grammar.

**Meaning.** `once p: F` is a promise by the callee: it will invoke `p` **at most once**, and will
not store or return it. It licenses the caller to move linear values into the closure it passes.

**Not a runtime check.** Like `borrowing`, `once` is erased before lowering. It is a contract the
callee's author asserts; the compiler checks callers against it, not the callee's honesty. Sprout's
three `once` positions (`task_spawn`, `task_fork`, `with_scope`) are all thin wrappers over
scheduler builtins where the invocation is the runtime's, not Sprout's.

## 5. Type-system impact

`types.Ownership` gains a third case:

```
export type Ownership (..) = | OwnConsume | OwnBorrow | OwnOnce
```

`Ownership` sits on `TFunc` and describes how *that node's parameter* is taken, so this needs **no
new `TFunc` field** and therefore none of M4.6's ~85-site fan-out — only the handful of `match`es on
`Ownership` itself (`ownership_eq`, `ownership_is_borrow`, `ownership_prefix`, `encode_ownership`,
`ownership_from_str`). This is the payoff from M4.6: the slot already exists and already survives
instantiation, substitution, generalisation and the interface codec.

Unification stays **invariant** (M4.6, per SE-0377): `OwnOnce` unifies only with `OwnOnce`. Call
sites are unaffected because `infer.build_fn_type_like` copies ownership from the callee's spine, so
`task_spawn(scope, my_named_thunk)` unifies `OwnOnce` against `OwnOnce`.

**Naming caveat, stated rather than hidden.** `Ownership` is a slight stretch for a call-count
bound. The alternative is renaming the type (`ParamConv`), which is mechanical but churns every
M4.6 site and the wire format three weeks after they landed. Recommendation: keep `Ownership`,
document the widened meaning as "how the parameter is received" in `types.sprout`.

## 6. Checking rules

All three live in `linear_check`. `lin_arg`'s `is_borrow: Bool` becomes the `Ownership` value, and
`spine_is_borrow` is joined by a `spine_own`.

### 6.1 A lambda at a `once` position

Check the body exactly as `lin_lambda_body` does today, obtaining its consumed set `bc` and borrowed
set `bb`. Then, instead of `lin_lambda_captures`'s blanket rejection:

- **`bc` (moved captures)** — propagate as the *call's* consumed set. The move happens at the call.
- **`bb` (borrowed captures)** — reject (§6.3).

Everything else falls out of machinery that already exists, which is the main evidence the scope is
right:

- a capture consumed **twice** inside the body is caught by the body walk's own `seq2`;
- a capture merely **read** inside the body lands in `bb` and is rejected;
- a capture **not consumed** in the body contributes nothing to `bc`, so the value stays outstanding
  at the caller and the caller's existing leak check fires;
- **double-move** across two calls, and use-after-move, are caught by the caller's consumed set.

### 6.2 A non-lambda at a `once` position

Walked as an ordinary consuming argument. A named function captures nothing, so there is nothing to
license.

**Known conservative edge**, verified rather than assumed: only a *literal* lambda at the call gets
the licence, so binding one first and passing it by name —

```
let g = \_ -> release(f)
run_once(g)
```

— is rejected. Not by this rule, though: `g`'s initializer is a lambda in an unannotated context, so
it is refused at its own binding site before it ever reaches the `once` position. That is sound, and
it errs the safe way (a rejection, not a silent acceptance). Lifting it means propagating one-shot-
ness backwards to a binding, which is inference this milestone does not attempt.

### 6.3 Captured borrows stay rejected

A borrow captured by a spawned closure is unsound and the counterexample is short:

```
task_spawn(scope, \_ -> peek(conn))   # borrow captured
close(conn)                            # consume runs first
                                       # the join later runs the task — use after close
```

`with_scope` joins *after* the body, so the closure can observe a value the body already consumed.
Sprout has no escaping/non-escaping or lifetime distinction to rule this out. Rust draws exactly
this line with `'static` on `thread::spawn`. Moves are safe because the value is gone from the
caller; borrows are not.

### 6.4 `once` is only meaningful on a function-typed parameter

Rejected elsewhere, mirroring "`borrowing` only on a linear type" — and for the same reason, that a
modifier which silently means nothing is worse than one that is refused.

## 7. Error-message impact

- Borrowed capture at a `once` parameter:
  `"borrowed value 'conn' cannot be captured by a closure passed to a `once` parameter: the closure may run after the value is consumed. Move it instead — pass ownership in and consume it inside the closure."`
- `once` on a non-function parameter:
  `"`once` applies only to a function-typed parameter (parameter 'n'): it bounds how many times the callee may invoke it"`
- The **existing** capture rejection gains a pointer, so the wall becomes a door with a sign on it:
  `"… captured by a lambda is not yet supported (higher-order linearity is deferred; if the callee invokes this closure at most once, declare that parameter `once`)"`

## 8. Compatibility and migration

Purely additive. `once` is contextual, so no existing identifier breaks; unannotated parameters keep
`OwnConsume` and check exactly as before; the lift only *accepts* more.

**Correction to an earlier draft of this note.** Goal 5 originally read "zero IR change
(`just ir-golden-diff` clean)". That was wrong, and the golden gate caught it. `once` *is* erased —
proved directly in `tests/stdlib/compiler/test_borrow_erasure.spr`, where two sources differing in
exactly the `once` token emit byte-identical IR and no `once` token survives into the output — but
the golden corpus still moved, in five files and for two reasons, both read before regenerating:

1. **`repl_hosted.sprout`** — the parser gained the string literal `"once"`, and this is the one
   golden program that bundles the compiler itself. `@.str.228` becomes `"once"` and every later
   constant shifts one index with identical content. No emitted instruction changed.
2. **`http_echo_server` / `http_web_server` / `tcp_echo_once` / `tcp_echo_server`** — the new
   `net.read_avail` definition, plus a real codegen change in `http_server`'s read loop:
   `pop_roots(i64 1)` became `pop_roots(i64 2)`. That is the migration, not the modifier. A
   connection used to be an unboxed `Int` handle and is now a boxed `TcpConnection`, so it is a
   GC-managed value that must be rooted across the recursive call.

The second is worth stating plainly rather than burying: **adopting the linear socket type costs an
allocation per connection and one more GC root in the read loop.** That is the price of the
compile-time release guarantee here, and it is not inherent — `TcpConnection` is a single-field ADT
that `wrap` would unbox if `wrap` and `linear` composed. Filed in `BACKLOG.md`.

The lesson for the gate itself: `ir-golden-diff` could not have proved `once`'s erasure in this
change, because the only corpus programs that exercise `once` are the same ones whose types
migrated. That is exactly why the one-token byte-identity test exists alongside it.

- `IfaceFile` **v5 → v6** (`ast.ParamMode` and `types.Ownership` both gain a case).
- **No 2-step bootstrap**, provided no file under `stdlib/compiler/` uses `once` — the committed
  seed must parse the compiler sources, and a new ADT case plus a one-line `param_mode_of_text` arm
  is ordinary Sprout the old seed already handles. To be re-verified at implementation time, not
  assumed.
- `stdlib/task.sprout`: `once` on `task_spawn.work`, `task_fork.work`, `with_scope.body`.
- `stdlib/http_server.sprout`: migrated to `net.TcpConnection` / `net.TcpListener`, and the rest of
  the file needed no new rules — `read_request` and its four helpers became `borrowing`;
  `write_and_close` borrows then consumes on both arms (M4.3 convergence); `serve_loop`'s recursive
  call is itself the consume on the looping branch and `close_listener` is the consume on the base
  branch. `serve_forever_loop` never closes its listener and yet is *not* a leak, because its tail
  call consumes it — no `net.forget` sibling to `task.detach` was required.
- `stdlib/net.sprout`: added `read_avail` (`borrowing`), the unknown-length counterpart to
  `read_exact` — an HTTP header block ends at a delimiter, not a byte count. A Sprout wrapper over
  the existing `tcp_read_avail` extern, so **not** a new builtin.
- `tests/task_io_smoke/concurrent_read.spr`: the other file the backlog listed as gated, migrated
  too. Each socket is moved into the task that owns it, so `close` moved out of `main` and into
  `reader`/`writer`; the listener, previously never closed at all, now is.

## 9. Known limitation: at most once, not exactly once

The type system will give **at-most-once**, matching Rust and OxCaml. Leak-freedom needs
exactly-once, and the missing half comes from the runtime contract: `stdlib/task.sprout:79`'s
`with_scope` binds its body's result with `let` rather than `<-` specifically so `__scope_join` is
unconditional, so every spawned task runs before the scope closes.

**That contract has one hole, and it is pre-existing.** `runtime/sprout_scheduler.c:671` —
`__scope_cancel` walks parked tasks and force-drops them, freeing their roots, and a spawned task
does not start until the current task yields. A cancelled task's closure can therefore run zero
times, and a moved-in connection is never closed.

This is a *runtime* leak on the experimental cancellation path (L0.5), not a soundness hole in the
checker, and it is already true today of the raw `Int` handles http_server uses — adopting linear
types does not introduce it. The honest statement for the spec is therefore: **leak-freedom for
values moved into a `once` closure holds absent scope cancellation.** Filed in `BACKLOG.md` rather
than fixed here; the fix belongs with cancellation-time resource release, not with this change.

## 10. Tests

TDD — each RED against the pre-change tree first.

**Positive** (`tests/stdlib/test_once_closures.spr`)
- move a linear value into a `once` closure that consumes it; no leak, no reuse;
- the same at a nested/second parameter position, guarding the ownership-spine walk;
- a *named* function passed at a `once` parameter (nothing captured);
- a non-linear capture at a `once` parameter, unaffected.

**Negative** (`tests/conformance/type_error/`)
- `once_capture_borrow` — §6.3's counterexample;
- `once_capture_unconsumed` — moved in, never consumed inside the body → leak still reported;
- `once_capture_double_move` — the same value moved into two closures;
- `once_use_after_move` — moved in, then used at the caller;
- `once_on_non_function` — §6.4;
- `lambda_capture_still_rejected` — an *unannotated* parameter still refuses the capture, with the
  new pointer in the message. This is the test that proves the lift did not become a blanket lift.

**Executable** — `tests/task_io_smoke/echo_roundtrip.spr`, `concurrent_read.spr`,
`http_conn_error_survives.spr` still round-trip against the migrated `http_server`.

**Unchanged, must still pass** — every `borrow_*` / `linear_*` fixture, `test_linear_borrowing.spr`,
`test_linear_borrow_in_type.spr`, `test_borrow_erasure.spr`, `test_linear_cross_module.spr`.

## 11. Spec/docs

- `docs/spec-v0.md` §5.8 — `once` as a parameter modifier, the at-most-once contract, the move
  licence, the borrow exclusion, and §9's cancellation caveat. Normative.
- This note — rationale, prior art, non-goals. Supporting, non-normative.
- `BACKLOG.md` — narrow M4.4a recorded against the M4.4 entry (which stays open for the general
  case: linear lambda parameters, captured borrows, call-count tracking); new entry for the
  cancellation-path leak.
