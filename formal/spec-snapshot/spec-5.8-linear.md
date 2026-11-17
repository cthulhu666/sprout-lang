### 5.8 Linear types

Design rationale and the deferred items live in
`docs/linear-types-m4-scoping-2026-08-01.md`,
`docs/linear-types-m4.2-enforcement-2026-08-06.md`,
`docs/linear-borrowing-v0.md` and `docs/one-shot-closures-v0.md`.

A type declaration may carry the contextual modifier `linear`, written between
`type` and the type name (mirroring `type alias`), or between `wrap` and the
name. It applies to ADT, record and wrap (§5.6.1) declarations:

```
type linear File = File Int
type linear Pos = (x: Int, y: Int)
wrap linear Fd = Int
```

`linear` is recognized only in those two positions; it remains an ordinary
identifier everywhere else (no reserved word).

**Semantics — consume exactly once.** A value of a linear type must be used
*exactly once* in a function body: not zero times (a leaked resource), and not
more than once (use-after-consume). "Use" means any reference to the binding,
including as the base of a field access (`p.x` uses `p`) — except a bare reference
in non-final `do`-statement position, which does nothing with the value and is
rejected as a discard (see **Discarded result** below). Enforcement covers every
binder that can hold a linear value — **function parameters**, **do-block `let`**,
**match-arm pattern variables** (a variable pattern aliases the whole linear
scrutinee; a constructor/tuple sub-pattern binds a linear field), and **`<-`
do-bind** variables — in `fn`, top-level `let`, and instance-method bodies, and is
checked on every control-flow path.

*Non-normative:* an owned linear value has no other reference, so an implementation may reuse
the memory of a consumed linear cell for a value of the same type it builds next. Reuse is not
observable (`docs/linear-reuse-v0.md`).

A **top-level `let` may not hold a linear value**, nor one that contains one: every
function can name a module binding, so no scope holds it to one use. A function
that makes one (`let mk = \(n: Int) -> File(n)`) holds nothing and is accepted.

A binder carries the obligation when its type **is** a linear type or **contains**
one — as a type argument, as a tuple component, or as a type the declaration
**stores in a field**. `let m = Just(File(1))` is bound by the rules below even
though `Maybe File` is not itself a linear type, and so is a user-declared
container (`Box File`), and so is a wrapper with no type parameter at all
(`type Crate = | Crate File`) — the test is structural, not a list of known
containers, and it does not depend on whether the linear type was written at the
use site or fixed by the declaration. A **function type is not descended**:
`Unit -> File` is a recipe for a resource rather than a resource, so binding one
and never calling it leaks nothing. Containment does not make the containing type
linear; it decides which *bindings* are tracked (see "Containment virality" under
Deferred, below). In particular `Crate` above is still not a linear type, so
`consuming Crate` remains an error while `let c = Crate(File(1))` must still be
used exactly once.

Field containment is transitive and terminates on a declaration that stores
itself or cycles through a sibling: `type Mid = | Mid File` and
`type Outer = | Outer Mid` both carry the obligation, naming `File`.

**Containment reads the declaration, not the type application.** Both halves of
the test ask what the declaration stores. A concrete field type counts because
the declaration stores it outright; a type argument counts only at a position the
declaration actually **stores** — one where the parameter appears in some
constructor field or record field type, outside an arrow. At a *phantom* position
the declaration holds no value of that type:

```
type Chan a = | Chan Int          # `a` is phantom: only an Int handle is stored

fn worker(ch: Chan File) -> Int   # `ch` stores no File; no obligation attaches
```

This is what lets a handle to a shared queue of linear values be passed around
freely while the values themselves stay tracked: `chan_recv(ch)` returns a
`Recv File`, whose declaration *does* store its parameter, so the binder that
receives it carries the obligation. The rule applies to **every** binder
uniformly — parameter, `let`, pattern variable and `<-` bind alike — so the
same value does not change status with the form that binds it. The test reads
one declaration deep, so a parameter stored inside another type that itself
drops it (`type Outer a = | Outer (Chan a)`) is still counted — over-strict,
never unsound.

**The limit this draws.** "Stores" is a statement about the *declaration*, not
about the program's runtime state. A `Chan File` is an Int handle into a queue
that really does hold `File` values, and `chan_send` discharges the obligation
of each value it moves in; after that, nothing relates the channel's own
liveness to what is still queued inside it. Abandoning a channel holding
unconsumed linear values is therefore **not** a diagnosed leak. Relating the
two needs a notion of runtime ownership that linearity as specified here does
not have, and the alternative — treating every phantom position as storing —
reports leaks for values a declaration provably does not hold.

The rules:

- **Reuse** — a linear binding referenced more than once along a path is rejected.
- **Leak** — a linear binding referenced zero times in its scope is rejected.
- **Branch convergence** — in an `if`/`match`, a linear binding defined outside it
  must be used either zero times in the whole construct, or exactly once in *every*
  branch; using it in some branches but not others is rejected.
- **Discarded result** — a `do`-block statement in non-final position is rejected
  when its value is linear, *contains* a linear value as a type argument or tuple
  component (`Maybe File`), or has a bare type-variable type that may be linear. The
  three rules above are keyed on binders, so a
  linear value that is never bound carries no obligation for them to find
  unfulfilled: `do { task_fork(s, w); 7 }` would otherwise drop the handle in
  silence. The **final** statement is exempt — it is the block's result, so the
  obligation passes to the caller.

  Containment is checked here for a reason of its own: in a `Maybe`/`Result` block
  *every* statement has type `Maybe X`/`Result E X`, so a rule reading only the
  type's head could never fire in a short-circuiting block at all. It is the same
  containment the binder rules use, and one predicate answers for both — they
  disagreed until 2026-09-07, which made `Maybe File` a leak in statement position
  and not in binder position. A function's type variable counts only under `where linear`
  (**Type variables**, below): without the bound no caller can instantiate it at a
  linear type, so `do { x; 1 }` with `x: a` is accepted, and so is the same step at an
  instance head's variable without the instance's bound.
- **Wildcard** — a `_` pattern at an *owned* position is rejected when it can match a
  value that holds linear content (the containment test above). Use is syntactic, so
  `match f with | _ -> 0` references `f` exactly once and the binder rules see no leak;
  this rule closes it, for `match`, `let..in`, `let..else`, `<-` and `try` alike.
  A `_` nested in a constructor field or a tuple component is rejected iff that
  position's type contains a linear type (`Wrap _`, `(_, n)` over `(File, Int)`).
  A `_` that is a whole arm — also `let _ = e`, `_ <- e` and the implicit arm of
  `let..else` — is rejected iff the scrutinee's type contains a linear type, with one
  refinement: over a non-linear ADT it is rejected only if some constructor it can
  still match has a field whose type contains one. An earlier arm rules a constructor
  out when it names it with only irrefutable sub-patterns (a variable, `_`, unit, or
  a tuple of those); `| Filled v -> … | _ -> Blank` over `Slot File` is accepted.
  A linear type, a tuple, a record or a type variable gets no refinement. A `_` over a
  **borrowed** value consumes nothing and is accepted (`Pg _ session` on a
  `borrowing Pg`), as is a `_` over a field of non-linear type (`File _` over an
  `Int` field), which is how a linear type's own module consumes it.

A linear ADT is consumed once by matching it (`match f with | File n -> …`) or by
passing it to a function. A linear record is read via field access, which consumes
it — so an *owned* linear record supports reading a single field or being passed
once; reading two fields (`p.x + p.y`) is a reuse (records have no destructuring
pattern). A `borrowing` parameter lifts that restriction (see below). A field read
or a `with` update that would drop another linear field is an error: `p.a` when
`p.b` is linear, or `p with (a = v)` when the old `a` is linear. Declare a record with
several linear fields as a constructor type and `match` it, so every field is taken.
For a value meant to be read freely, do not declare it `linear`.

#### Publishing a linear type

A pattern binds each field at its own type, so a field whose type holds no linear
value is not tracked: it may be copied or dropped. Inside the declaring module
that is trusted code. Outside it, the constructor would let any module copy such
a field out of a linear value, or build one around a value it shares — a second
owner of a file descriptor, or of an array written in place.

So a linear type whose representation is **published** may only have fields whose
type **is** linear: a linear type, whose own declaration guards what it holds, or
a variable of its constructor's `where linear`, which is linear where a pattern
unpacks it. Containing a linear type is not enough: a pattern takes a tuple or a
`Maybe` apart and binds each part at its own type, so `(File, Vector Int)` would
hand out its `Vector`. Aliases are expanded first. Published means `export` with
`(..)` (§5.6.1, §5.6.4), or declared in the prelude, whose constructors are in
scope everywhere (§3.1). Every field of every constructor is checked, and the
error is reported at the declaration:

```sprout
export wrap linear LinVec a (..) = Vector a        # error: `Vector a` is not linear
export type linear W (..) = | W (File, Vector Int)   # error: not itself a linear type
export type linear Pair (..) = | Pair File File  # accepted: both fields are linear
export type Boxed (..) = | exists a. Boxed a where linear a   # accepted
export wrap linear LinVec a = Vector a             # accepted: abstract
```

The rule is stricter than it must be: `(File, File)` and `Maybe File` hold nothing
untracked, and are rejected too. The fix is the abstract form: drop `(..)` and
export functions that build and take the type apart. A linear type that is not
published is not checked.

**Records are covered by the rule, not by the guarantee.** An `instance` method
can read an abstract record's fields from any module (§5.6.4), so an abstract
linear record's untracked field is still reachable that way. Wraps and ADTs are
not affected: an instance cannot name an abstract constructor. Design:
`docs/linear-publish-v0.md`.

#### Type variables and `where linear`

A type variable a piece of code is generic over admits only types that hold no
linear value at a stored position — the containment test above. That covers a
function's and an extern's variables, a class method's own variables, a top-level
`let`'s generalized ones, and an existential's `exists` variables. Two kinds are
unbounded: a data declaration's parameters (a constructor stores its argument and
copies nothing, so `Maybe File` and `Ok(conn)` stay legal) and a class's own
variable (an instance at a linear type is concrete code, checked as such), which
takes no `where linear`, in the class header or in a method's `where`.

`where linear v` lifts the restriction on `v`, in the same `where` list as class
constraints; an `extern fn` takes a `where` that holds only these, and a redeclared extern
must repeat them. Each bound names one
type variable: `where linear Int`, `where linear g a` and a bare `where linear` are parse
errors. The body is then
checked as if `v` were linear: a binder of type `v`, or one storing a `v`, must be
used exactly once, and `borrowing v` is allowed. The non-linear types still fit.

```
fn ident(x: a) -> a where linear a = x       # ident(L(1)) and ident(1) both compile
fn dup(x: a) -> (a, a) = (x, x)              # dup(L(1)) is rejected
```

The restriction is checked where a variable is **instantiated** — a call, a value
reference such as `list_map(dup, xs)`, a constructor pack — once the declaration's
types are known; in a mutually recursive group, once the group's are. A variable
bounded by `where linear` counts as linear there, so it
cannot be passed on to code that has not opted in. In a group it counts as linear in
every member, so an unannotated member handed one cannot copy, drop or pass it on. A variable that occurs only at a
phantom position is not exempt: `detach(task: Task a)` discards a task whose result
nobody receives, so `detach` at a `Task File` is rejected.

```
`dup` takes `a` as a value it may copy or drop, and `main.L` is linear; if `dup` uses its `a` exactly once, add `where linear a` to it
```

The error names the variable as the callee's signature writes it. A callee whose
signature writes no type variable for it is told to annotate its parameters first, and
a top-level `let`, which takes no `where`, to become a `fn`, and a constructor to bound
its `exists` variable. Inside a bounded declaration, every linearity error prints a
bounded variable under its written name.

A class method may declare the bound on its own variables; each instance method
restates it under its own names, as it restates a class constraint, and its body is
held to it. `pure` (on `a`) and `branch` (on `a` and `b`) do, so a `Result e L`
block can end in `pure(l)` and use `try`.

An instance head takes the bound on its variables, `instance Taker (Box a) where
linear a, Taker a`, and each method body is checked with them linear. Without it, a
head variable admits no linear type, and the rule is checked at each dictionary: a
call or a method used as a value that selects a generic instance, through its
context and its superclasses, must not instantiate an unbounded head variable at a
type holding a linear value, and a bounded variable of the enclosing declaration
counts as linear. So `take(Box(L(1)))` is rejected when the `Box` instance has no
bound:

```
the `Taker` instance for `Box a` takes `a` as a value it may copy or drop, and `L` is linear; if the instance uses its `a` exactly once, add `where linear a` to its head
```

The prelude's `ToString` instances over `Maybe`, `Result` and tuples, and `Propagate
(Result e)`, take the bound. Its `Eq` and `Ord` instances and those over `List` do
not: each drops a value on some path, so they are rejected at a linear type.

A constructor takes the bound on its `exists` variables, `type Boxed = | exists a.
Boxed a where linear a, Taker a`, and its type is then linear whatever a value holds:
`Boxed(1)` is used exactly once too. A value a pattern unpacks from a bounded variable
is linear in its arm, so `match b with | Boxed x -> take(x) + take(x)` is rejected, and
so is passing `x` on to code that has not opted in:

```
`dup` takes `b` as a value it may copy or drop, and `Boxed`'s `a` is linear under its `where linear a`
```

Without the bound, the pack is rejected:

```
`Boxed` takes `a` as a value it may copy or drop, and `main.L` is linear; add `where linear a` to the constructor, which makes its type linear
```

#### Borrowing (Milestone 4.5)

A resource is often **acquire → use N times → release once**; a socket is read and
written repeatedly, then closed. Strict use-exactly-once cannot express that at
all — the first use would consume it. A **borrow** is a use that does *not* claim
the value's one consuming use.

A parameter whose type is linear may carry an ownership modifier between the `:`
and the type:

```
export fn write(conn: borrowing TcpConnection, b: Bytes) -> Result … !{IO} = …
export fn close(conn: consuming TcpConnection)          -> Unit       !{IO} = …

conn <- connect(host, port)
_    <- write(conn, request)   # borrows; conn stays live and still owes its use
line <- read(conn, 128)        # borrows again
close(conn)                    # the single consuming use
```

- `borrowing` — a non-consuming use. The consume obligation stays with the
  **caller**, so the parameter may be used zero or many times inside the function
  and must **not** be consumed there.
- `consuming` — the explicit spelling of the default. An unmodified linear
  parameter is already consuming, so this is redundant, but it documents the
  contract at an API boundary. No existing program changes meaning.

- `once` — see **One-shot closure parameters** below. It sits in the same slot but
  constrains a different thing: not how the parameter is taken, but how often the
  callee may invoke it.

- `local` — see **Non-escaping closure parameters** below.

All four are **contextual keywords**: `borrowing`, `consuming`, `once` and `local` remain
ordinary identifiers everywhere else, including as type names (`x: borrowing` is
the type named `borrowing`, since no type follows the word).

**Where a use is a borrow.** A reference to a linear binding is a borrow when it
sits at a `borrowing` parameter position, or when the binding it names is itself a
`borrowing` parameter and the reference merely *reads through* it — as the base of
a field access or as a `match` scrutinee. Every other reference consumes. So
`p.x + p.y` is legal for `p: borrowing Pos` and remains a reuse for an owned `p`.

**Rules.**

- **Leak is unchanged and strict** — a borrow never discharges the once-only
  obligation, so an omitted `close` is still rejected. That is the point of the
  feature.
- **Use after consume** — a borrow may not follow the consume along a path
  (`close(conn); write(conn, …)` is rejected). Borrow-then-consume is the normal
  order and is fine. Evaluation order is the one fixed in §6.
- **A borrow lasts the whole call.** A value passed at a `borrowing` position may
  not be consumed by another argument of the same call: with
  `f(b: borrowing File, c: File)`, `f(x, x)` is rejected, since `f` could read `b`
  after consuming `c`. Borrowing one value at two positions is fine.
- **Branch convergence** applies to consumes only; arms need not agree on borrows.
- **Borrowed contents** — destructuring a borrowed value binds every field that is
  or holds a linear value as borrowed, so they cannot be consumed out from under
  the owner. For the same reason a **field that is or holds a linear value may not
  be read out of a borrowed value** (`w.inner` where `w: borrowing Wrap` and
  `inner` is `File`, `Maybe File` or `(File, Int)`): the result would be an owned
  value its real owner will still release. A field that holds no linear value
  reads freely.
- **A consume may not follow a fallible bind.** A `<-` bind whose right-hand side is
  `Maybe` or `Result` short-circuits (§5.9), so the steps after it are conditional.
  Consuming a binding from outside the block there would be skipped on the failure
  path and the value leaked, so it is rejected; consume before the first fallible
  `<-`, or branch explicitly with `match`. The condition is the **bind's** type, not
  the block's, and `!{IO}` does not exempt it: an effect row and a short-circuit are
  orthogonal, so an `!{IO}` block containing a fallible bind does *not* run every
  step. A block whose own type is `Maybe`/`Result` but whose binds are all
  non-fallible is likewise unaffected — nothing in it can end the block early.
  The same holds after a `try` (§5.9.1), and the error names the `try`.
- A `borrowing` parameter may not be consumed or returned.
- An argument at a `borrowing` position must be a **variable reference**; a
  freshly-built value that is or holds a linear value there would never be
  consumed.
- The modifiers are **erased**: they reach no IR pass and emitted code is
  byte-identical with and without them.

**Ownership is part of the function type (Milestone 4.6).** `(borrowing T) -> U`
and `T -> U` are **different types**, and the ownership is carried on the arrow
alongside the effect row. A `borrowing` function may therefore be bound, passed
and called as a value, and class and instance methods may carry modifiers — the
mode is readable wherever the type is.

The two conventions are **invariant**: neither substitutes for the other, in
either direction. Supplying a consuming function where a borrowing one is expected
means the value is released by a call whose caller still owns it (double consume);
supplying a borrowing one where consuming is expected discharges the caller's
obligation through a call that releases nothing (leak). A mismatch is a type
error. One between `borrowing` and consuming is decided once types are known
(below), so it is reported at the declaration that holds it.

**On a type holding no linear value the two coincide.** The modes are erased, and
such a type has nothing to own, so `(borrowing Int) -> Int` is the type
`Int -> Int`. The rule is decided once the parameter's type is known, not when the
two arrows meet: `apply(peek_it, 40)` with `peek_it: (borrowing a) -> Int` and
`apply(f: b -> Int, x: b)` compiles, since `b` is `Int`; with `where linear b` on
`apply`, `apply(peek_it, L(1))` is a mismatch. A `where linear` variable counts as
linear. This is the `borrowing` axis
only: `once` and `local` must still match exactly. A function type inside an
instance method's signature, in a parameter or the return type, is compared with
the class's before any type is known to be linear, so there its modes must match
exactly.

Two consequences follow:

- **An instance method's modifiers must match its class declaration's.** A call
  dispatches through the class signature, so an instance that borrows where the
  class consumes would leak, and one that consumes where the class borrows would
  release a value its caller still owns. Compared as *ownership*, not as written
  text: `consuming` on the instance against an unmodified class parameter agrees,
  since both consume, and on a parameter type holding no linear value any two of
  them agree (`fn peek(r: Int)` implements `fn peek(r: borrowing a)` at `Int`). A
  class method's signature takes the modifier rules a function's does, except that
  the class's own variable may be borrowed, since an instance may put a linear type
  there.
- **An UNMARKED arrow type means consuming.** Passing a borrowing function to a
  parameter annotated `(T) -> U` is an ownership mismatch. To accept one, mark
  the arrow's parameter: `(borrowing T) -> U`. The modifier is written inside the
  parentheses, which are then the parameter's own rather than a grouping, and the
  reading is taken only when an `->` follows the closing paren — `borrowing`,
  `consuming` and `once` are ordinary identifiers everywhere else, so `(borrowing a)`
  with no arrow after it is an ordinary type application, exactly as `x: borrowing`
  is the type named `borrowing`.

  The same rules apply as on a declared parameter, and are enforced where the
  modifier is WRITTEN rather than at some later use: a modifier on a non-linear or
  type-variable parameter is rejected, in a return type as well as a parameter
  type, and at any depth — inside a type argument (`List ((borrowing a) -> Int)`)
  or a tuple component, not only on the outermost arrow. `consuming` is checked
  too, although it denotes the same ownership an unmarked arrow has: the rule is
  about what the author wrote, not about what the type system kept.

  This is what makes the with-resource combinator expressible:

  ```
  fn with_file(path: String, work: (borrowing File) -> Int) -> Int
  ```

  `work` borrows, so `with_file` still owns the `File` and is the one that closes
  it — the release cannot be forgotten by a caller.

  **A lambda may fill such a slot.** A lambda parameter whose mode is unwritten
  takes the slot's **`borrowing`**, so `with_file(p, \c -> peek(c))` and the
  `_`-hole spelling `with_file(p, scaled(_, 5))` both mean a borrowing parameter.
  A **written** mode is never overridden and must match. The push-down is a
  checking rule, so it reaches a lambda written *at* the argument; one bound by a
  `let` first must spell the mode.

  Only `borrowing` is taken this way. **`once` is never inferred for a lambda
  parameter**: it is a promise about how often the parameter is invoked, and a
  promise must be written — so an unannotated lambda whose own parameter sits at
  a `once` slot stays an ownership mismatch. Written
  (`\(w: once Unit -> Int) -> w(())`), it is checked as a declared function's is
  (below): at most one run per path, and no escape to a slot without the promise.
  This is about the lambda's *own* parameter, not the slot the lambda itself
  fills: passing an unannotated lambda **to** a `once` parameter is the one-shot
  move below, and compiles.

  This is the one case in which a linear lambda parameter is accepted — **a
  lambda may take a linear parameter iff it borrows it.** A borrow carries no
  consume obligation, so the 0..n run count that defers the rest of higher-order
  linearity is silent about it. The body is held to the rule any `borrowing`
  parameter is held to: it may read the value, not release it, and returning it
  counts as releasing it. What the lambda **captures** is unaffected — a captured
  linear value, owned or borrowed, is still rejected.

**A modifier on a non-linear parameter is an error**, as is one on a
type-variable parameter without `where linear` (reported distinctly): an unbounded
variable is never linear, so the modifier would mean nothing. Under the bound
`borrowing a` is allowed. Swift SE-0427 draws the same line, making generic
parameters `Copyable` by default and requiring `<T: ~Copyable>` to opt out.
`extern fn` signatures are checked the same way even though they have no body. This diverges deliberately from Swift, whose
`borrowing`/`consuming` apply to any parameter because under ARC they still change
retain/release traffic. Sprout has a tracing GC and erases the modifiers, so on a
non-linear parameter they would carry no meaning at all; rejecting them keeps them
from reading as enforcement that is not there.

**One-shot closure parameters (`once`, Milestone 4.4a).** A parameter may be
declared `once`:

```
export fn task_spawn(scope: Scope, work: once Unit -> Unit !{IO}) -> Unit !{IO} = …
```

This is a promise by the **callee**: it invokes `work` **at most once**, and does
not store or return it. The promise is **checked, not assumed** — a function, or
a lambda, declaring `once p` may use `p` at most once along any path, counted per
control-flow path, so `if c then p(x) else p(x)` is one invocation and is legal
while `p(x) + p(x)` is not. Zero uses are legal: the bound is from above, so one
path may invoke and another not (`if c then p(x) else 0`, or an invocation after
a fallible `<-`).

Every use of `p` other than a call must hand it to a position that makes the
same promise. `p` may be passed at a `once` (or `once local`) position, or
captured by a lambda that itself sits at one, whose result then counts as
returned. Returning `p`, storing it, binding it with `let`, or passing it at an
ordinary or plain `local` position is an error, because the receiver could
invoke it again.

`once` is only meaningful on a **function-typed** parameter; elsewhere it is an
error. In particular it is rejected on a **type-variable** parameter, and for a
reason of its own — not the universe argument that rejects `borrowing a`. The
licence `once` grants rests on the callee being able to *invoke* the parameter,
which it cannot do when the type is not known to be a function, while the value
would remain freely returnable and duplicable. `once a` is therefore strictly
worse than a concrete parameter whose promise is merely wrong.

The promise licenses one thing at the **caller**: a lambda passed at a `once`
position may **move** linear values into itself. A moved capture is consumed *at
the call*, so it is discharged there and any later use is a reuse:

```
conn <- accept(listener)
task_spawn(scope, \_ -> handle(conn))   # conn is MOVED into the closure
                                         # any use of conn after this is rejected
```

- Each moved value must be consumed **exactly once inside the closure body**, on
  every path. The body is checked by the ordinary rules, so branch convergence and
  reuse detection apply unchanged within it. Reading the moved value before
  consuming it inside the same closure is a normal borrow-then-consume and is
  allowed — `\_ -> do { write(c, x); close(c) }` is the intended shape.
- A value the closure only **borrows** — reads without consuming — may not be
  captured at a `once` parameter alone: the closure may run after the owner has
  consumed the value. Declaring the parameter `local` (below) supplies the
  missing non-escaping promise.
- A **consuming** linear lambda parameter is still rejected: `once` bounds how
  often the closure runs, not what may be handed to it on each run. A
  **borrowing** one is accepted, here as at any other position (§5.8).
- `once` is **erased**, like the ownership modifiers, and is part of the function
  type, so it is compared invariantly at unification.

**Non-escaping closure parameters (`local`).** A parameter may be declared
`local`, alone or as `once local`; the mode is function-typed only:

```
export fn with_timeout(ms: Int, body: local Unit -> a !{IO}) -> TimeoutResult a !{IO} = …
export fn with_scope(body: once local Scope -> a !{IO}) -> a !{IO} = …
```

- **Callee promise, checked.** The closure does not outlive the call. In the
  callee the parameter may only be called, passed at another `local` (or
  `once local`) position, or captured by a lambda that itself sits at a `local`
  position, whose result then counts as returned. Returning it, storing it,
  binding it with `let`, passing it at an ordinary position or capturing it in
  an ordinary lambda is an error. The closure may be called any number of times
  unless it is also `once`. A `once local` parameter must satisfy both rules, so
  it may be passed on only at a `once local` position.
- **Caller licence.** A lambda at a `local` position may capture **borrowed**
  values: a `borrowing` parameter, or an owned linear binding it only reads. The
  borrow lasts for the whole call, so the value may not be consumed by another
  argument of that call. Moves into the closure are accepted only at
  `once local`, as at `once`; at plain `local` the closure may run many times.
- **Restrictions.** `local` is rejected on a non-function parameter and on a
  type-variable parameter, for the reason given for `once`. It is never accepted
  on a lambda's own parameter and never inferred onto one, because the callee
  check runs only over declared functions.
- `local` is **erased** and is part of the function type, so it is compared
  invariantly at unification.
- **In the standard library.** The concrete combinators (the prelude's `range_*`, `list_*`,
  `vec_*`, `result_*`, `when_ok`, `when_error`; `stdlib.mutable`'s `mutvec_*` and
  `mutmatrix_*`; `stdlib.linvec`'s `linvec.accum`; `stdlib.list`'s `map_adjacent`), the
  `Foldable` methods and the generic functions over them (`fold`, `fold_while`, `any`,
  `all`, `find`, `find_map`, `count`, `fold_indexed`, `min_by`, `max_by`), `Zip`'s
  method with `zip_with`, and `stdlib.sort`'s `Sort` method with `sort_by`, take their
  function `local`, so a lambda passed to them may read a borrowed value, comprehensions
  included. `Functor`, `Filterable`, `Applicative`, `Monad`
  and `Traversable`, and `map`, `filter`, `filter_map`, `partition`, `map2`..`map5`,
  `and_then`, `traverse`, `concat_map`, do not: an instance for a function-shaped type (a
  parser, state or reader monad, a lazy functor) keeps the function, and `local` in the class
  would forbid it. A `Foldable` returns the final accumulator, so it has no use for keeping
  `step`; a `Zip` instance applies `g` while it builds the result, so neither does it. A `_` hole
  inside the function argument (`list_fold(step(c, _, _), 0, xs)`) is a lambda at the `local`
  slot and lends; a `_` for the data argument (`xs |> list_each(f, _)`) is an ordinary lambda
  around the whole call and does not, so use the direct call there. Because `local` is part of
  the function type, two kinds of program that compiled before are now rejected: a user `instance
  Foldable` whose `fold_values` or `fold_while_values` function parameter lacks `local` (write
  `local`), and a combinator passed as a value where a plain function type is expected, such
  as `list_fold` for a `(b -> a -> b) -> b -> List a -> b` parameter (wrap it in a lambda).
- An `extern fn`'s modes cannot be checked and are trusted. An extern whose
  signature writes `local` is therefore reachable only from its declaring module
  (§Externs). See `docs/local-closures-v0.md` §6.5.

**At most once, not exactly once.** The type system bounds invocations from above
only — matching Rust's `FnOnce` and OxCaml's `once`, both of which are also
at-most-once. Leak-freedom for a moved value therefore also depends on the
callee's runtime contract that the closure *does* run: for `stdlib.task` that is
`with_scope`'s unconditional join.

**Leak-freedom holds absent scope cancellation.** Cancelling a scope force-drops
its tasks that are parked (on I/O, `task_sleep`, a channel or `select`); a task
that has not started yet still runs. Every `with_timeout` expiry drops its body the
same way. A dropped task never finishes its closure, so a linear value it holds at
that park, whether moved in or created inside the task, is never released — while
the program still type-checks as consume-exactly-once clean. Sprout has no
destructors, so closing this requires release-on-drop in the scheduler rather than
a typing rule. Until it exists, do not cancel a scope whose tasks hold a linear
resource across a park, and do not let a `with_timeout` body create one it holds
across a park. Lending is safe: a `with_timeout` body is a `local` closure, so the
caller keeps a borrowed value and releases it after either outcome.

**Deferred (rejected with a diagnostic, never silently accepted):**

- Higher-order linearity beyond the `once` case — a linear binding captured by a
  lambda at an **unannotated** parameter, and **consuming** linear lambda
  parameters, are not yet supported, nor is capturing a **borrowed** value
  at a parameter that is not `local`. The prelude's concrete `list_*`, `vec_*`, `range_*`
  and `result_*` combinators and the `Foldable` functions take `local`, so
  `list_each(\x -> write(conn, x), xs)` lends `conn`; the class methods `map`, `filter`,
  `and_then` and `traverse` do not. Two shapes are no longer deferred: the
  spawn-a-handler server, a move into a one-shot closure, so `stdlib.http_server` runs on
  the linear socket API throughout; and the with-resource combinator, whose work function
  takes the resource `borrowing` (§5.8).
- Containment virality **as a property of types** — linearity is still
  *per-declaration*: a record that merely contains a linear field is not itself
  linear (contrast Austral), so `Maybe File` is not a linear type. What containment
  decides is which **bindings** carry the use-exactly-once obligation — every
  binder, parameters included (above). Making linearity itself containment-computed
  would further reach parameter *modes*, `borrowing` filters and field reads, and
  stays deferred. It no longer waits on a linearity bound for type parameters:
  the case that motivated that (`Chan a`) is a phantom parameter, which containment
  now declines to descend on its own. See `docs/linearity-virality-v0.md`.

#### `LinVec`: a pure vector with in-place writes

`stdlib.linvec` declares `export wrap linear LinVec a = Vector a`, abstract: only its module
builds or takes one apart. A set, push or pop consumes the vector and returns it, and linearity
guarantees no one holds the old one, so the change cannot be observed and the signature is pure. A
`LinVec` is a `Vector` at runtime (§5.6.1), so a set allocates nothing. They are written qualified
here, as after `import stdlib.linvec as linvec`. `linvec.set`, `linvec.push` and `linvec.pop` wrap
the module's private functions `linvec_set`, `linvec_push` and `linvec_truncate`. Each calls an
`!{IO}` writer (`vector_mutset`, `vector_push`, `vector_truncate`) inside `unsafe_pure` (§6).

```
linvec.new(n: Int, x: a) -> LinVec a                     # n copies of x
linvec.get(i: Int, v: borrowing LinVec a) -> Maybe a     # Nothing out of range
linvec.set(i: Int, x: a, v: LinVec a) -> LinVec a        # traps out of range
linvec.length(v: borrowing LinVec a) -> Int
linvec.empty() -> LinVec a                               # a fresh vector of length 0
linvec.from_list(xs: List a) -> LinVec a                 # a fresh vector of the list's elements
linvec.from_vec(v: Vec a) -> LinVec a                    # a fresh copy; `v` is unchanged
linvec.push(x: a, v: LinVec a) -> LinVec a               # amortised O(1)
linvec.pop(v: LinVec a) -> (Maybe a, LinVec a)           # the last element and the rest
linvec.freeze(v: LinVec a) -> Vec a                      # O(1); the vector's life ends
linvec.drop(v: LinVec a) -> Unit
linvec.scatter(is: List Int, x: a, v: LinVec a) -> LinVec a
linvec.accum(f: local a -> b -> a, pairs: List (Int, b), v: LinVec a) -> LinVec a
```

- `linvec.set` out of range, and `linvec.scatter` or `linvec.accum` with an index out of range,
  stop the program with a runtime error, as `mutvec_set` does. `linvec.get` out of range is
  `Nothing`, as `vec_get` is.
- `linvec.push` appends, doubling the storage when it is full. `linvec.pop` removes the last
  element and returns it with the rest; on an empty vector it returns `(Nothing, v)`, the vector
  unchanged. The capacity never shrinks.
- `linvec.scatter` sets `x` at each index in order; `linvec.accum` sets `f(old, y)` for each
  `(i, y)` in order, so a repeated index folds.
- Every path must consume the vector: return it, pass it on, or end it with `linvec.freeze` or
  `linvec.drop`. Otherwise the leak rule (above) fires. A set
  inside a lambda is rejected: a lambda may not consume a linear parameter. Use explicit
  recursion or the bulk operations. A lambda may READ a `LinVec` it captures inside a
  combinator that takes `local`: `list_fold(\ (n, c) -> … linvec.get(c, seen) …, 0, cells)`.
- `LinVec (LinVec a)` is rejected where it is built, by the bound on `linvec.new`'s `a`.

Design and measurements: `docs/persistent-array-v0.md` §5.3.

