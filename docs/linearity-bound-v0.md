# A linearity bound on type variables (v0, 2026-10-20)

**Status:** design decided (§11). Implementation in four steps (§12); step 1, the syntax, is
done: `where linear v` parses and is rejected as not yet supported. Not normative until it lands in
[spec-v0.md](spec-v0.md) §5.8. Closes the type-variable holes in §1, three of which
[persistent-array-v0.md](persistent-array-v0.md) §5.3 lists as prerequisites.

## 1. Problem

The linear checker runs after inference, on fully substituted types, and checks each generic body
once with its type variables rigid. A rigid `a` is not a linear type, so the body may copy it or
drop it. A caller may then instantiate `a` at a linear type. Every probe below compiles and runs
on master (2026-10-20), with `type linear L = L Int` and `close` consuming an `L`:

| Program | Result |
|---|---|
| `fn dup(x: a) -> (a, a) = (x, x)`, then `dup(L(1))` | two owners, both closed |
| `fn konst(x: a, y: b) -> a = x`, then `konst(1, L(2))` | the `L` is dropped |
| `list_length([L(1), L(2)])` | both elements dropped |
| `fn box_peek(b: borrowing Box a) -> a`, at `Box L` | an owned `L` while `b` still holds it |
| `type Boxed = \| exists a. Boxed a where Taker a`, `Boxed(L(1))` | unpacked twice |
| `instance Taker (Box a) where Taker a` whose `take` calls `take(x)` twice, then `take(Box(L(1)))` | two owners, both closed |
| top-level `let dup = \x -> (x, x)`, then `dup(L(1))` | two owners, both closed |

The first, fourth and fifth are BACKLOG P1 entries. The second and third are the same cause from
the other side: a leak instead of a reuse. The last two reach the same gap through an instance
head's variable and through a top-level `let`, which is generalized. None of those four was filed.

Two over-strict rules come from the same gap. A bare type variable in non-final `do` position is
rejected as a possible discard (§5.8 **Discarded result**), even at `Int`. And a modifier on a
type-variable parameter (`borrowing a`) is rejected, because without a bound `borrowing Int` would
be an error while `borrowing a` at `Int` was not (BACKLOG P3 **Linearity bound on a type
parameter**).

## 2. Goals and non-goals

Goals:

- Reject each program in §1, at the call or pack that instantiates the variable.
- Keep every generic body that compiles today compiling, unchanged.
- Let a generic function take a linear value when it uses it exactly once, as `ident`, `after`
  and `chan_send` do today.
- Allow `borrowing a`, so a class can borrow its receiver.

Non-goals:

- Linearity as a property of types (containment virality, Option 2 of
  [linearity-virality-v0.md](linearity-virality-v0.md)). Containment keeps deciding which
  bindings are tracked.
- Higher-order linearity: a consuming linear lambda parameter stays rejected.
- Inferring the bound (§5, option B).

## 3. Prior art

Each row is from the language's own spec, proposal or official docs.

| Language | Unannotated type parameter | Bound | Containers | Existentials |
|---|---|---|---|---|
| Swift ([SE-0427](https://github.com/swiftlang/swift-evolution/blob/main/proposals/0427-noncopyable-generics.md)) | `Copyable` by default | `T: ~Copyable` suppresses the default, so any type may be substituted | a type with a `~Copyable` field is `~Copyable`; copyable again only by a written conditional conformance | `any P` is copyable; `any P & ~Copyable` opts out |
| Rust ([Reference](https://doc.rust-lang.org/reference/special-types-and-traits.html)) | moves; may drop (affine) | `T: Copy`, `T: Clone` | `Copy` only if every field is | `dyn Trait` is never copied |
| Austral ([spec](https://austral-lang.org/spec/spec.html) §Type Parameters) | no default | `Free`, `Linear`, or `Type`, which is treated as linear | a type holding a `Linear` or `Type` parameter is linear | none |
| Mojo ([AnyType](https://mojolang.org/docs/std/traits/anytype/AnyType)) | no default; a bound is required | `AnyType` is linear; `Copyable`, `Deinitable` relax it | implicitly `Deinitable` when every field is | not supported |
| Clean ([report 2.2](https://clean.cs.ru.nl/download/doc/CleanLangRep.2.2.pdf) ch. 9) | non-unique; uniqueness may be dropped, so `dup` is sound | attribute variables `u:a` | uniqueness propagates outwards | 1.3 did not propagate through existential fields; 2.x makes the box unique |
| OxCaml ([modes](https://oxcaml.org/documentation/modes/intro/)) | legacy `many`/`aliased` modes, chosen for compatibility | kinds such as `('a : value mod many)` | modes are deep | an existential counts as any value |
| Linear Haskell ([GHC guide](https://ghc.gitlab.haskell.org/ghc/doc/users_guide/exts/linear_types.html)) | linearity is on arrows, not types | `%1 ->` | fields linear by default | not documented |

Two camps. Swift and OxCaml keep the old meaning of an unannotated variable and add a written
relaxation, which is what lets existing code keep its meaning (SE-0427 §Source Compatibility).
Austral and Mojo have no default, and the weakest bound is fully linear. Swift tried inferring
when a container may be copied and dropped it as "more confusing than helpful" (SE-0427
§Alternatives).

## 4. What the corpus instantiates at a linear type

A survey of the four repos (sprout_lang, uncharted-suns, repbit, sprout-pg), 2026-10-20. A data
constructor (`Ok(conn)`, `Just(c)`) is not counted: its variables are the declaration's
parameters, which §6.1 leaves unbounded. What remains is generic *code* run at a linear type:

| Function | At | Where |
|---|---|---|
| `chan_send`, `chan_recv`, `chan_new`, `chan_close` | `TcpConnection`, `SqliteDb`, `PoolSlot` | `http_server`, `bench/http_worker_pool`, `twelve_dollar_server`, sprout-pg `pool` |
| `panic(msg) -> a` | `TcpListener`, `Link` | `testsupport/listen`, bench servers, uncharted-suns `app`, tests |
| `after(effect, value: a) -> a` | `Result PgError PgConnection` | sprout-pg `connection`, `pool` |
| `pure` (`Applicative`) | `L` in a `Result` block | probe; sound |
| `branch` (`Propagate`) | the value under `try` | `try` desugars to a `branch` call (`ast.sprout`) |

Each uses its value at most once (`chan_new` and `chan_close` hold none, §6.3), so each takes
the opt-in and nothing downstream changes. There is no instance at a linear type outside one
rejection test, and no existential packs one. No real code maps, folds or measures a list of
linear values; only tests do.

`chan_select`, `task_fork`, `task_await`, `with_scope` and `with_timeout` also pass an `a` that a
linear value could fill, but nothing instantiates them at one, so they stay unannotated until
something does. `Task` is itself `type linear` with a phantom `a`.

## 5. Options

**At the caller.** Tell people not to pass linear values to generic code. Nothing enforces it,
and `list_length` is in the prelude.

**In the checker** — three ways to say what an unannotated `a` may be:

- **A. Non-linear by default, opt in to linear** (Swift, OxCaml). An unannotated variable admits
  only types that hold no linear value, so every body that compiles today stays sound. A
  function that uses its `a` exactly once writes `where linear a`, and its body is then checked
  as if `a` were linear. Breaks only calls that instantiate an unannotated variable at a linear
  type: the unsound programs in §1, and the sound uses in §4, which add the opt-in.
- **B. Inferred.** Compute per variable whether the body copies or drops it, and carry the result
  in the scheme like a class constraint. Nothing to write, but the bound is in no signature, so an
  error at a call names a rule the reader cannot see, and the result depends on every function
  the body calls, so a change in one moves errors elsewhere. Swift tried inference for the
  container case and dropped it. Rejected.
- **C. Linear by default, opt in to copy and drop** (Austral's `Type`, Mojo's `AnyType`). Sound,
  but `list_length`, `konst` and every generic function that ignores or repeats a value
  would need a written bound, which is most of the prelude. Rejected.

**In the representation.** Making linearity a property of types (containment virality) does not
help: the generic body still sees `a`, not the type it will become.

## 6. Design (option A)

### 6.1 Which variables carry the bound

Every variable a piece of code is generic over: a function's, a class method's own (`a` in
`pure(x: a) -> f a`), an instance head's (`a` in `instance ToString (Maybe a)`), an existential's,
and a top-level `let`'s. A `let` inside a body is not generalized, so it has none. A top-level
`let` has no `where`, so its variables always take the default; a generic value that must accept
a linear type is written as a `fn`. A data declaration's parameters do not: a constructor stores
its argument and copies nothing, and containment already tracks the binder that holds it. So
`Maybe L`, `Result e L` and `Chan L` stay legal types, and `Ok(conn)` stays legal.

A class's own variable carries no bound. Classes have no default method bodies (spec §8.5), so an
instance at a linear type is concrete code, checked as such, and generic code that calls the
class sees an unannotated `a`, which a linear type cannot reach.

This is Rust's and Mojo's rule, not Swift's. Rust's `Self` is "an implicit type parameter"
constrained "as usual" (Reference, items.traits.self-param), so any type may implement a trait and
a default body cannot copy `Self` unless the trait requires `Copy`. A Mojo default body "can't call
methods that aren't part of the trait's contract". Swift makes protocols `Copyable` unless they
write `~Copyable` (SE-0427 §Protocols), because its protocol extensions already copy `Self`. If
Sprout adds default method bodies, they are checked as Rust's are: as if the class's variable may
be linear.

### 6.2 Spelling

`where linear a`, in the existing `where` list beside class constraints:

```
export fn after(effect: Unit !{IO}, value: a) -> a !{IO} where linear a = value
export fn chan_send(ch: Chan a, value: a) -> Unit !{IO} where linear a = …
type Boxed = | exists a. Boxed a where linear a, Taker a
```

An `extern fn` takes a `where` too, holding only linear bounds: it has no dictionary parameter
for a class constraint, but `panic` and the `__chan_*` primitives are externs whose `a` must
admit a linear type. A class's own variable takes none (§6.1), so `class C a where linear a`
is a parse error.

`linear` is already a contextual word (`type linear`). It names the case being admitted; the
non-linear types still fit, as `~Copyable` still admits copyable types in Swift. The reading
"`a` must be linear" would be useless, since no function needs to refuse `Int`. No sigil.

### 6.3 At an instantiation

Where an unbounded variable is instantiated — a call, a method dispatch, a pack — its type must
hold no linear value at a stored position. That is the containment test the binders use (§5.8),
so `Chan L` passes (phantom) and `Maybe L` does not. A type variable that carries `where linear`
counts as linear here, so an opted-in function cannot pass its `a` on to one that has not opted
in.

The bound is checked whether the variable occurs at a stored position or not. `fn f(ch: Chan a)`
can call `chan_recv(ch)` and drop the `Recv a` it gets, so a variable under a phantom parameter
still reaches values. And the phantom-only functions are where a value hides behind a handle:
`detach(task: Task a)` discards a task whose result nobody will receive, so it stays unannotated
and `detach` at a `Task L` is rejected. `chan_new` and `chan_close` take the opt-in, `chan_close`
accepting §5.8's stated limit that closing a channel with queued linear values is not diagnosed.

### 6.4 In a body

Under `where linear a`, a binder whose type is `a`, or holds `a` at a stored position, is tracked
like a linear binder: leak, reuse, branch convergence and discard all apply, and `borrowing a` is
accepted. An existential declared `where linear a` makes its constructor's field linear, so every
`Boxed` binder is tracked.

Without the bound, `a` is known to hold nothing linear, so the bare-type-variable discard rule is
dropped for it. `fn stmt(x: a) -> Int = do { x; 1 }` compiles.

### 6.5 Instances

A class method that declares `where linear a` holds every instance to it: each instance body is
checked with `a` linear. `pure` takes the bound on `a`; `branch(value: t a) -> ControlFlow (t b) a`
takes it on both `a` and `b`, since in a `Result E L` block the failure arm's `b` is `L` too.
Every prelude instance passes (`Cons(x, Nil)`, `Just(x)`, `Ok(x)`, one `match`). `map2` cannot
take it: over `List` it pairs each element with every element of the other list, so it repeats
them. `flat_map` passes each element
to a function, and a function that consumes a linear argument is higher-order linearity, still
deferred.

The price of bounding `pure`: every future instance is held to it. A state or parser applicative
cannot write `pure(x) = P(\s -> (x, s))`, because capturing `x` in a lambda is that same deferred
higher-order linearity. No repo defines such an instance today.

### 6.6 A mode on a non-linear type

Modes are erased, and on a non-linear type they mean nothing (§5.8's reason for refusing
`borrowing Int`). So once types are known, `borrowing` and consuming are the same mode on a
parameter whose type holds no linear value, and `(borrowing Int) -> Int` is the type `Int -> Int`.
This is the borrow axis only; `once` and `local` constrain function-typed parameters and stay
exact.

```
class Peekable a
  fn peek(r: borrowing a) -> Int

instance Peekable L        fn peek(r: borrowing L) -> Int = …   # must match, as today
instance Peekable Int      fn peek(r: Int) -> Int = r           # Int has nothing to own
use(peek)                  # use: (Int -> Int) -> Int — accepted
```

The unifier no longer fails at once when two arrows differ in mode. It records the pair, and the
check runs after substitution, when the parameter's type is known: on a linear type a mismatch is
an error, as today; on a non-linear type the modes coincide. Comparing earlier would make the
answer depend on inference order. Swift has the same rule: a protocol requirement "may still be
satisfied by an implementation that uses different conventions for parameters of copyable types",
function values convert between conventions, and for noncopyable types "the convention must match
exactly" (SE-0377 §Ownership convention conversions). OxCaml's mode crossing ignores modes on
immediates.

Two fixes ride along. A class declaration's method signatures are not mode-checked today, so
`borrowing` on a concrete non-linear parameter there is accepted; they get the check functions
and externs have. And the instance-vs-class mismatch has one wording for both directions, so
`fn peek(r: Int)` against `borrowing a` reports "the instance only borrows where the class
consumes", the reverse of what happened.

### 6.7 Where the check runs

Two places, each where the facts it needs already are:

- **References, in inference.** Where inference instantiates a generic name — a call, a value
  reference, a constructor — it records, for each unbounded variable, the fresh type variable
  that stands for it, beside the name and position. Once the declaration's substitution is final
  it applies §6.3 to each. This covers partial application and a generic function passed as a
  value (`list_map(dup, xs)`): each mention is instantiated. It runs in inference rather than
  in `linear_check` because only inference resolves names: `linear_check` sees the global
  environment, so a local that shadows a generic global would be matched against the wrong
  scheme. Field access defers its check the same way (`field_obligations`). A class method's
  own class variable is exempt, as is a constructor's data parameter; an `exists` variable is
  one absent from the constructor's result type.
- **Dictionaries, in evidence resolution.** A generic instance's variables never appear in the
  method's scheme: `take(Box(L(1)))` is `take` at the class's own (unbounded) variable, and
  `a := L` exists only in the dictionary, `Taker (Box L)` built from `Taker L`. Which instance
  a dictionary comes from is decided only in `resolve.sprout`, after inference, and that pass
  holds the instance heads. So the check walks each dictionary there, nested ones included,
  matches the instance head against the constraint's type and applies §6.3 to the instance's
  variables. A forwarded dictionary needs nothing: the forwarding function's own variable is
  already bounded.

Dispatch does not change. Inference changes only in §6.6's mode comparison.

## 7. Impact

- **Syntax:** `linear <var>` in a `where` list, which an `extern fn` now also takes (§6.2).
- **Now rejected:** each program in §1, and a call that instantiates an unannotated variable at a
  linear type in sound code that has not opted in. The error names the function, the variable and
  the fix:
  ```
  `ident` takes `a` as a value it may copy or drop, and `L` is linear.
  If `ident` uses its `a` exactly once, add `where linear a` to it.
  ```
- **Now accepted:** a bare type variable discarded in statement position (§6.4); `borrowing a`
  under the bound, which closes the P3 entry; an instance at a non-linear type of a class that
  borrows its variable, and such a function passed as a value (§6.6).
- **Migration:** §4's functions take the opt-in in `stdlib/`. Downstream repos change nothing.
  Tests that call their own generic function at a linear type change; the count comes from the
  first implementation run, not from this survey.
- **`:type` and hover** print the bound like a constraint: `a -> a where linear a`.
- **IR:** none. The bound is erased, like the modifiers.

## 8. Tests

- Rejection, one per §1 row, each confirmed to run on the commit before.
- Pass-through: an opted-in function that passes its `a` to an unannotated one is rejected.
- Opt-in bodies: `where linear a` with a reuse, a leak and a drop are each rejected; with one use,
  accepted at `Int` and at `L`.
- Phantom: `chan_send` at `TcpConnection` compiles; a phantom-only function without the opt-in is
  still rejected (§6.3).
- Instances: a `where linear a` method whose instance copies `a` is rejected.
- Dictionaries: `take(Box(Box(L(1))))` through two generic instances is rejected; with
  `where linear a` on both instances and one use each, accepted.
- Values: `list_map(dup, xs)` and `dup(_)` at a linear type are rejected.
- `try` in a `Result E L` block compiles once `branch` opts in.
- Modes (§6.6): `instance Peekable Int` with `r: Int` compiles and `use(peek)` at `Int -> Int`
  compiles; at `L` a consuming instance of a borrowing class is still rejected, with the right
  wording; `borrowing Int` in a class method signature is rejected.
- Existential: `Boxed(L(1))` rejected without the bound; with it, `unbox(b) + unbox(b)` rejected.
- The discard rule: `do { x; 1 }` at an unannotated `a` compiles.

## 9. Spec and docs

Spec §5.8: the bound, §6.3, §6.4 and §6.6, replacing the conservative discard paragraph and the
type-variable modifier rejection, adding non-linear types to "compared as ownership", and
removing the Deferred bullet. `idiomatic-sprout.md`: when to write `where linear a`.
`persistent-array-v0.md` §5.3: the three prerequisites point here. BACKLOG: the three P1 entries
and the P3 entry are deleted on landing.

## 10. Open questions

1. ~~The default: A, B or C (§5)?~~ A (§11).
2. ~~The spelling: `where linear a`, or another word?~~ `where linear a` (§11).
3. ~~Should a class's own variable stay unbounded (§6.1)?~~ Yes (§11).
4. ~~Should a variable that occurs only at phantom positions be exempt?~~ No (§11).
5. ~~What does a mode mean on a parameter whose type turns out non-linear?~~ Nothing (§6.6, §11).

## 11. Decision record

Decided with Kuba, one question at a time.

1. **Default: A**, 2026-10-20. Existing bodies stay sound and unchanged, and the bound is in the
   signature. Accepted cost: a sound but unannotated generic rejects linear callers until its
   author opts in. B was not annotation-free (externs and class methods have no body to infer
   from) and moves errors into code the caller cannot see; C bounds most of the prelude.
2. **Spelling: `where linear a`**, 2026-10-20. `where Linear a` reads as a class with instances
   and a dictionary, which it is not; `where owned a` would give "owned" a second meaning beside
   the consuming mode.
3. **A class's own variable is unbounded**, 2026-10-20. Rust, Mojo and Clean let a plain class
   have such instances; only Swift requires the opt-in, to protect protocol extensions that already
   copy `Self`. Sprout classes have no default bodies, so an opt-in would protect nothing.
4. **No phantom exemption**, 2026-10-20. Exempting a phantom-only variable as implicitly linear
   would accept `detach(Task L)`, a leak, with nothing in `detach`'s text saying so, and would flip
   a function's bound when another declaration starts storing its parameter. Cost: `where linear
   a` on `chan_new` and `chan_close`.
5. **A mode on a non-linear type is no mode**, 2026-10-20, as a type rule (§6.6). Instance-only
   normalisation would leave `use(peek)` at `Int` rejected; writing `borrowing Int` in instances
   puts in code what §5.8 refuses as fake enforcement; deferring loses the receiver-borrowing
   class. Swift SE-0377 is the same rule.

6. **Where the check runs**, 2026-10-20, approved with the plan in §12: references in
   inference and dictionaries in evidence resolution (§6.7), not both in `linear_check`, which
   sees neither local names nor instance heads. The bound is carried in its own AST field, not
   as a constraint named `linear`, so that no class-constraint consumer can mistake it for a
   class (guidelines #2a, #3, #4).

An outside review (2026-10-20) checked decisions 1–4 together and found them consistent. It
added the dictionary walk (§6.7), the instance-head and top-level `let` holes (§1), `branch`'s
second bound and `pure`'s price (§6.5), and the class-signature mode gap (§6.6).

## 12. Implementation

1. **Syntax.** The parser moves `linear v` out of each `where` list into its own field on
   `FnDecl`, `ExternFnDecl`, `TypeConstructor`, `ClassMethodSig`, `InstanceDecl` and
   `InstanceMethodImpl`, so no class-constraint consumer sees it. Inference rejects any bound as
   not yet supported.
2. **The bound at references and in bodies** (§6.3, §6.4, §6.7's first walk), the stdlib opt-ins
   of §4, and the spec. Closes §1's rows 1–5 and 7.
3. **Dictionaries and existentials** (§6.5, §6.7's second walk): row 6, a bound on an instance
   head, and an existential unpacked under a bound.
4. **Modes** (§6.6).
