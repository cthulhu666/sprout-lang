# `try` and `Propagate` — early return through a class (v0)

Status: **proposal**, not implemented. Nothing here is normative until `docs/spec-v0.md` carries
it. Drafted 2026-10-05.

## 1. Problem

Inside `do`, `x <- e` has two meanings, chosen by the *name* of `e`'s type (spec §5.9). On `Maybe`
or `Result` it unwraps and may end its block early. On anything else it binds the whole value. Five
costs follow.

1. **The reader cannot see which lines may end the block early.** A user wrote
   `r <- fs.write_text(...)` and then `match r with | Ok _ -> …`, expecting `r : Result`. The bind
   had unwrapped it to `Unit`, and the error named the `match`, not the bind.
2. **It was unsound.** The checker decided the mode at the bind, from the type known *then*.
   Codegen decided it again from the *final* type. When the head was filled in later they
   disagreed:

   ```sprout
   fn greet() -> Maybe String =
     do
       x <- pure("a")
       x
   ```

   This compiled and printed a pointer; the `Result` twin segfaulted. Phase 0 (§8) now rejects
   it; this design removes the cause.
3. **Only two types can do it.** A user type with two kinds of failure (`Got a | NotFound |
   Failed String`) cannot short-circuit. The checker tests the names `Maybe`/`Result`
   (`infer.decide_bind_mode`), and every later pass reads its `typed_ast.BindMode`.
4. **Errors land on the wrong line.** `x <- pure("a")` followed by `x ++ "b"` reports
   `` `++` needs matching Semigroup operands: $t3031 String vs String `` at the `++`.
5. **Pure code has no propagation.** A pure function uses `do` only to get the short-circuit, and
   `let..in` has none (the plan's Tier 2 is still open).

## 2. Goals and non-goals

**Goals.**

- One meaning per syntax. `<-` means "an effect runs here". `try` means "this may end the block
  early".
- User types opt in through a class. The compiler tests no type names.
- Zero cost at a known instance: the same code as a hand-written `match` (§5 measures the bar).
- Works in `do` and in pure `let..in`.
- Error conversion without implicit conversions: replacing or wrapping a failure is an explicit
  `let..else` (§4.3), with room to add `try` suffixes for it later (§4.4).

**Non-goals.**

- Monad-generic `do` (`<-` as `flat_map`). `flat_map`'s function is pure
  (`prelude.sprout`, `class Monad`), so an `!{IO}` block could not continue after a bind. It
  would also end the block through a closure per bind.
- Exceptions. They are planned as an `exn` effect (`docs/effect-system-handlers-draft.md` §11).
  This design only reserves vocabulary for them (§4.7).
- Implicit error conversion (Rust's `From`).
- Zero cost in generic code (`where Propagate t`).
- `List` as a short-circuit type. Comprehensions cover it.

## 3. Prior art

Each row was checked against the language's own reference or docs.

| Language | Construct | What it does | Source |
|---|---|---|---|
| Haskell | `do { p <- e; … }` | Every `<-` is `>>=` of `Monad`: one meaning, any type | Haskell 2010 Report §3.14 (checked 2026-07-07, `docs/let-else-and-monadic-binding-plan.md` §3) |
| GHC | `QualifiedDo` | `M.do`: "The `x <- u` statement uses `(M.>>=)`" — the block picks the bind | GHC User's Guide, *Qualified do-notation* |
| F# | computation expressions | `builder-expr { cexper }`; "`let!` is defined by the `Bind(x, f)` member"; plain `let` stays plain | F# Language Reference, *Computation Expressions* |
| OCaml | binding operators | `let* x = e in b` ≡ `( let* ) e (fun x -> b)`, user-defined | OCaml Manual, *Binding operators* (checked 2026-07-07, plan §3) |
| Rust | `?` | `Ok(val)` evaluates to `val`; `Err(e)` "returns `Result::Err(From::from(e))`" | Rust Reference, *The try propagation expression* |
| Rust | `Try` trait | `branch` decides "whether the operator should produce a value (`ControlFlow::Continue`) or propagate a value back to the caller (`ControlFlow::Break`)"; unstable, `try_trait_v2` | `std::ops::Try` |
| Zig | `try` | `try a` is "Equivalent to: `a catch \|err\| return err`" | Zig Language Reference |
| Swift | `try` | Written "before a piece of code that calls a function … that can throw"; errors propagate "to the scope from which it's called"; handled by `do`-`catch` | *The Swift Programming Language*, Error Handling |
| Kotlin | `?: return` | `val parent = node.getParent() ?: return null` | Kotlin docs, *Null safety* |
| Koka | `try` | "Transform an exception effect to an `error` type" — here `try` **catches** | Koka `std/core/exn` |

Every language above marks the early-return point in the syntax: an operator (`?`), a keyword
(`try`), a bind form (`let!`, `let*`) or a block (`M.do`). None picks the meaning from the
operand's type name. Today's `<-` is the outlier.

`Propagate` is Rust's `Try` made an ordinary class. Rust keeps `Try` unstable, so a user type cannot
opt in on stable Rust; in Sprout any type can.

### Where the failure goes (§9 Q2)

Checked 2026-10-06 against each primary source.

**To the function** — languages with statements and a real `return`:

| Language | Rule | Source |
|---|---|---|
| Rust `?` | "the `Err` will be returned from the whole function as if we had used the `return` keyword"; "Async blocks act like a function boundary, much like closures" | *The Rust Programming Language* §9.2; Reference, *Block expressions* |
| Rust `try { }` | "A `try` block creates a new scope one can use the `?` operator in" — a block target, unstable | Unstable Book, `try_blocks` |
| Zig `try` | "Equivalent to: `a catch \|err\| return err`" | Zig Language Reference |
| Swift `try` | Propagates "to the scope from which it's called"; a `do`-`catch` intercepts it, and "If none of the `catch` clauses handle the error, the error propagates to the surrounding scope" | *The Swift Programming Language*, Error Handling |

**To the enclosing block** — expression languages, where the rest of the block becomes a function:

| Language | Translation | Source |
|---|---|---|
| Haskell `do` | `do {p <- e; stmts} = let ok p = do {stmts} … in e >>= ok` | Haskell 2010 Report §3.14 |
| Scala `for` | `for (p <- e; p' <- e'; …) yield e''` → `e.flatMap { case p => for (…) yield e'' }` | Scala 2.13 spec §6.19 |
| F# `let!` | `builder.Bind(expr, (fun pattern -> {{ cexpr }}))`, scoped to the expression's braces | F# Language Reference, *Computation Expressions* |
| OCaml `let*` | `( let* ) e1 (fun x -> e2)` | OCaml Manual, *Binding operators* |
| Gleam `use` | "turns all following expressions into an anonymous function" | Gleam v0.25 release notes |

Sprout has no `return`, a failing `<-` already ends only its block, and `let..else` desugars to a
`match` over the remaining steps, so it is in the second group. Both first-group languages with a block form added it deliberately (Rust `try`
blocks, Swift `do`-`catch`).

Gleam is the nearest precedent and cuts the other way on syntax. It had a block-scoped `try`
keyword from v0.9 and removed it in v0.27: "Now that we have `use` expressions, the less general
`try` expressions are redundant"; it prefers "fewer ways to do the same thing". Its replacement is
`use x <- result.try(e)`, a library function. This design keeps the keyword but backs it with a
class, as Rust backs `?` with `Try`.

## 4. Design

### 4.1 The class

```sprout
type Step r a = Continue a | Break r          # names: §9 Q3

class Propagate t
  fn branch(value: t a) -> Step (t b) a

instance Propagate Maybe
  fn branch(value: Maybe a) -> Step (Maybe b) a =
    match value with
    | Just v -> Continue(v)
    | Nothing -> Break(Nothing)

instance Propagate (Result e)
  fn branch(value: Result e a) -> Step (Result e b) a =
    match value with
    | Ok v -> Continue(v)
    | Err err -> Break(Err(err))
```

`branch` splits a `t a` into the value or the failure. The failure is re-typed to `t b` for any
`b`: it holds no `a`, so it fits whatever the enclosing block returns. `b` appears only in the
result, and the instance is still picked by the argument. This compiles and runs today, including
a user instance for `Fetch a = Got a | NotFound | Failed String`.

### 4.2 `try e`

`e : t a` with `Propagate t`; `try e : a`. In a binding position (§9 Q1):

```
let x = try e          →   match branch(e) with
<rest>                     | Continue x -> <rest>
                           | Break r -> r
```

In `do`, `x <- try e` runs `e`'s effect once and then branches, the same way an effectful
`let..else` step does (spec §5.2.2). What a bare `try e` statement means, and `try` as a block's
last step, are open (§9 Q11).

**Typing.** `r : t b` becomes the value of the enclosing block, so the block's type must unify with
`t b`. This one unification replaces §5.9's table: a `Maybe` in a `Result` block fails, and so
does `Result String _` in a `Result Int _` block. No special rule is needed.

**Where the failure goes.** It becomes the value of the enclosing `let..in` or `do` block. When that
block is the function body, the function returns it. A failure in a nested block ends only that
block, and a `do` inside a lambda is the lambda's own block. This is what `<-` does today; spec
§5.9 says so since Q2 was decided.

**An unknown `t`** is an ordinary class constraint. It resolves as late as any other, and an
unresolved one is the usual ambiguity error at the `try`. The mode is fixed by the syntax, so the
checker and codegen cannot disagree about it.

### 4.3 Replacing or wrapping a failure: `let..else`

`try` takes no suffix (§9 Q1). To replace or wrap a failure, match it with `let..else` (spec
§5.2.1), which already compiles to a `match` on the call:

```sprout
let Just decoded = b64.decode(text) else Err(BadRequest(name))                # replace
let Ok key = webauthn.public_key_from_spki(pk) else Err e -> Err(Rejected(e))   # wrap
```

Corpus, non-test code of the three repos, one-line forms: about 53 sites replace a failure and
about 7 wrap it, against about 600 that propagate it unchanged. For replacing, a `try e else fb`
suffix is the same length as `let..else` and means the same thing. Wrapping is where a suffix
would be shorter.

### 4.4 Later: `try e else fb` and `try e with f`

The suffixes can be added later without breaking anything, because two shapes are reserved now:

- **A `try` right-hand side takes no pattern `else`.** `let Just y = try e else fb` is an error,
  so a later `try … else` gives meaning only to code that was rejected. For `let..else` on the
  unwrapped value, parenthesise: `let Just y = (try e) else fb`.
- **No `with` directly after a `try` expression.** `try load_point() with (x = 1)` would otherwise
  parse as a record update of the unwrapped value. Parenthesise: `(try load_point()) with (x = 1)`.

If added, they would mean:

```
let x = try e else fb   →   match branch(e) with | Continue x -> <rest>
                                                 | Break _ -> fb
let x = try e with f    →   match branch(e) with | Continue x -> <rest>
                                                 | Break r -> map_failure(f, r)
```

`with` must map only the failure arm. The first draft defined it as `try map_failure(f, e)`, which
passes `e`'s result as an argument (boxed, §5) and rebuilds `Ok(v)` on every success. `MapFailure`
is a class over a two-argument constructor, which compiles today; `Maybe` has no error and cannot
be an instance. `try e else fb` would also let generic code (`where Propagate t`) replace a
failure, which `let..else` cannot, having no constructor to name. Add them when wrap sites become
common.

### 4.5 `<-` and `let` inside `do`

| Line | Effect? | May end the block early? |
|---|---|---|
| `let n = parse_count(s)` | no | no |
| `line <- read_line()` | yes | no |
| `row <- try pg_query(conn, sql)` | yes | yes |
| `let cfg = try parse_config(text)` | no | yes |

`x <- e` never unwraps. A `do`-`let` must be pure. Spec §5.2.2 already calls it "the pure local
bind", but the checker accepts an effectful right-hand side today (`let r = fs.write_text(...)`
compiles and runs). The same holds for `let..in`: spec §5.2.1 makes an effectful right-hand side an
error, yet `let r = fs.read_text(...) in …` compiles in an `!{IO}` function. Unlike a `do`-`let`, it
has no mechanical rewrite to `<-` (§9 Q15).

### 4.6 Generic code

`try` works under `where Propagate t`. It costs a dictionary call and a `Step` box (§5).

Generic code can propagate but cannot build a success: `Propagate` has no `pure`-like method, so a
`where Propagate t` function returning `t a` also needs `Applicative t` (§9 Q14).

### 4.7 Exceptions, later

An `exn` effect is planned as the abortive corner of effect handlers. Its "Universe B" lowers
`!{Exn}` to the same `Result` threading that `try` produces (`docs/effect-system-handlers-draft.md`
§11.3). The two should share that lowering.

Vocabulary: in Sprout `try` means *propagate*, as in Swift and Zig. Koka uses `try` to *catch*. The
exception handler is spelled `handle` (handlers draft §4.2) and must never be spelled `try`.

## 5. Performance

Setup: `-O2`, macOS arm64, 10M iterations of three fallible steps each, best of 3 runs. Heap objects
are counted with `SPROUT_DEBUG_ALLOC=1`.

| Variant | Time | Heap objects |
|---|---|---|
| A. today's `<-` on `Result` | 0.12 s | 10.0M |
| C. hand-written nested `match` | 0.12 s | 10.0M |
| B. `Propagate` as a plain class, no compiler support | 0.90 s | 70.0M |
| D. hand-written `match` on a user 3-constructor ADT | 0.13 s | 10.0M |

B costs two objects per `try`:

- The `Result` returned by the call is boxed. The unboxed worker return applies only to a call that
  is matched directly, not to one passed as an argument.
- `branch`'s worker builds a boxed `Continue(v)`, then unpacks it into registers to return it.

Devirtualisation already works: `try_devirt_concrete` (`lowering.sprout`) calls the `Result`
instance directly. LLVM `-O2` recovers nothing more. The compiler has no inliner or case-of-case
pass of its own.

**Known-instance fusion.** At a `try` whose instance is known after devirtualisation, and whose
`branch` body is one `match` with `Continue`/`Break` in its arms, inline `branch` and merge the two
`match`es:

```
match branch(check(i)) with | Continue a -> K | Break r -> r
→ match check(i) with | Ok a -> K | Err e -> Err(e)
```

That is variant C, which measured the same as today. The rule depends on the instance's shape, not
its name, so it covers user types too. Acceptance: a fused `try` chain emits the same IR as the
hand-written `match` (modulo SSA names), and no `sprout_alloc_obj` for `Step`.

Fusion needs the instance's `branch` body during lowering. `LowerCtx` (`lowering.sprout`) carries
only instance impl names, so step 1 adds a table of instance bodies from the typed program.

**What "zero cost" means here.** The same code as a hand-written `match`, on the success path:

- The unboxed worker return needs the scrutinee to be a direct call to a top-level function
  (`unboxed_maybe_match_target`, `ast_to_ir.sprout`). `try` on a variable, a field, a lambda or a
  closure call stays boxed, exactly as a hand-written `match` on it would.
- On failure the fused `Err e -> Err(e)` rebuilds the `Err`, as today's unboxed `<-` path already
  does. Only today's boxed path passes the original box on.

**Generic code** still pays the dictionary call and the box. `docs/fold-while-v0.md` §6 rejected a
`Continue`/`Done` type because "Sprout boxes every constructor", which on `any`/`all` would mean
one allocation per element of every fold. Here the box is confined to generic code that cannot
short-circuit at all today, and fusion removes it everywhere else.

**Today's fast paths:**

| Fast path | Decided by | Effect of this design |
|---|---|---|
| Unboxed worker return (`_worker`, tag + payload) | shape: at most one field per constructor | unchanged; already serves user ADTs (variant D) |
| `<-` short-circuit lowering | the bind's `BindMode` (phase 0) | removed; fusion replaces it |
| Unboxed C runtime reads | a fixed list of 8 externs | unchanged (runtime ABI) |
| Tuple scalar replacement | shape, tuples only | its `BindMode` test (`sra_rest_plain`) becomes a `try` test |

A constructor with two or more fields is boxed even in hand-written code. Widening unboxed returns
helps every ADT, but it is separate work.

## 6. Diagnostics

Proposed wording. Each is reported at the `try`, the `let` or the `<-`, never at a later use.

```
no instance        `try` needs a type that can fail, and `Int` has no `Propagate` instance.
wrong block type   this `try` returns a `Result String _` failure, but the block returns `Int`.
                   Handle it here with `let..else`, or make the block return `Result String _`.
Maybe in Result    ... plus: to turn `Nothing` into an error, write `let Just x = e else Err(...)`.
reserved else      `try` takes no `else` yet. For `let..else` on the value: `(try e) else …`.
reserved with      `with` after `try` is reserved. For a record update, write `(try e) with (…)`.
unknown t          the existing ambiguity error.
effectful do-let   this `let` runs an effect. Bind it with `<-`.          (step 5)
old fallible <-    `<-` no longer unwraps `Result`. Write `x <- try e`.   (step 3)
```

## 7. Interaction with `let..else`

They split the work. `try` propagates a failure unchanged; `let..else` replaces it, wraps it or
supplies a default. They overlap only on unchanged propagation: `let Just x = find(k) else Nothing`
and `let x = try find(k)` mean the same thing, and both stay legal.

- `let..else` matches **any refutable pattern** on any type (`let Cons h _ = xs else d`).
- `try` asks the **type** what success is, through `Propagate`, and needs no pattern.

The plan's Tier 2 proposed `let Ok x = e` with no `else` as the propagate form
(`docs/let-else-and-monadic-binding-plan.md` §2). `try` replaces that proposal, for two reasons.
A pattern binding without `else` looks like an ordinary binding, which is the invisible-mode
problem again. And today a refutable pattern without `else` is a non-exhaustive-match error;
turning that error into propagation would silently change the meaning of a mistake.

## 8. Compatibility and migration

Breaking: a fallible `<-` needs `try`, and an effectful `do`-`let` becomes `<-`.

**Size.** A rough count of fallible binds (callee name looked up against `fn` signatures, ±50%):
576 certain across sprout_lang, uncharted-suns and repbit, an estimated 600–900 in total.
uncharted-suns is mostly `Maybe`, repbit mostly `Result`. Effectful `do`-`let`s are not counted.
Exact counts need the compiler, not grep.

**Order (proposed; reviewed 2026-10-06 by three independent passes, pending §9 Q8).** Every step
must leave all three repos compiling with unchanged behaviour.

0. ~~Soundness fix.~~ Landed: `typed_ast.BindMode` on each bind, decided in
   `infer.decide_bind_mode` and read by every later pass; a head that becomes `Maybe`/`Result`
   after the bind is rejected (spec §5.9). The modes are also the list the codemod needs.
1. Add `Step`, `Propagate` and their instances, `try`, its two reserved shapes (§4.4) and fusion.
   Old fallible `<-` keeps working, except that a `<-` whose right-hand side is a `try` (after
   stripping parentheses) is always plain. Otherwise `x <- try e` with `e : Result E (Maybe A)`
   unwraps twice. The rule must reach every place that reads a bind's type, not only
   `decide_bind_mode`: `do_family_update` (`infer.sprout`) sets the block's family from the step's
   type for every `DoBindStep`, and the parser's synthetic `__t <- e` binds (`build_do_total`,
   `parser.sprout`) carry the user's right-hand side.
   1a. Tooling: a compiler phase that lists every bind whose `BindMode` propagates and every
   effectful `do`-`let`, with file, line and column. Only the type checker knows either, and no
   `--phase` reports them today.
2. Codemod A, on sprout_lang, then uncharted-suns and repbit: add `try` to every listed `<-`.
   `x <- try e` means what the old `x <- e` did. The codemod must parenthesise an operand that is
   not an application (§9 Q12; uncharted-suns has about 105 lines `<- if`/`match`/`do`/`let`),
   map a synthetic `__t <-` back to the user's line, and refuse a pattern-`else` bind on a fallible
   right-hand side (`Just x <- e else …`): its faithful rewrite is the reserved shape, and today
   it unwraps twice (`e = Just(Just(2))` binds `x = 2`). A grep of the three repos finds no such
   site. Running it on `stdlib/compiler/` needs step 1 landed and reseeded first.
3. Flip: `<-` never unwraps. A `<-` that would have unwrapped without `try` gets a migration
   diagnostic (§9 Q8). `Ok x <- e else …` on a `Result`, a type error today because the synthetic
   bind unwraps, becomes legal; that change is intended.
4. Codemod B: rewrite every effectful `do`-`let` to `<-`. Behaviour is preserved because `<-` is
   now plain; before step 3 it is not (`let _ = fs.write_text(bad, …)` continues, `_ <-` stops the
   block). IR is not preserved: a tuple bound by a `do`-`let` gets scalar replacement
   (`sra_core_eligible`, `ast_to_ir.sprout`) and a plain `<-` does not. Extend it to plain `<-`
   first, or accept the diff.
5. Enforce `do`-`let` purity (and `let..in`, per §9 Q15).

Downstream CI builds against sprout-lang master. Codemod A must be merged in uncharted-suns and
repbit before step 3 lands, and codemod B before step 5. uncharted-suns has about 8 live
worktrees; each branch needs codemod A before it rebases past step 3.

## 9. Open questions

- **Q1. Where can `try` appear?** **Decided (2026-10-06):** at the head of a binding's right-hand
  side (`let x = try e`, `x <- try e`) or as a bare statement in `do`, lowering to `match`. Its
  operand is an application; anything else is parenthesised (`try (a |> f)`). No `else` or `with`
  suffix: failures are replaced or wrapped with `let..else` (§4.3), and two shapes are reserved so
  the suffixes can be added later (§4.4). The suffixes were weighed against a corpus count and
  against their grammar cost: `else` and `with` already mean two things each. Rust, Swift and Zig
  allow `try` in any expression, which needs a real early return in codegen.
- **Q2. Where does the failure go?** **Decided (2026-10-06): the enclosing block**, which is what
  `<-` does today. Prior art in §3. Spec §5.9 said "returns from the enclosing function"; it now
  says the block.
- **Q3. Names.** Lines using the word, comment lines excluded: `Step` 52 in sprout_lang and 41 in
  uncharted-suns; `Continue` 52 (constructors in `stdlib/repl.sprout` and `stdlib/tui/app.sprout`);
  `branch` only as a local binding; `Break`, `Propagate`, `MapFailure`, `map_failure` 0. A clash
  does not block a name: a module's own type or constructor shadows the prelude's, and the
  prelude's uses keep working (checked with a local `IntRange`, and a local `Just` beside a `Maybe`
  `<-`). Proposed: keep `Step`/`Continue`/`Break`, the shape of Rust's `ControlFlow`. Open: how a
  module with its own `Step` names the prelude's, to write an instance by hand.
- **Q4. `<-` with a pure right-hand side.** Allowed, error, or lint? Proposed: allowed, linted.
  Decide before step 2: codemod A writes `x <- try pure_fn()` into every pure `Maybe` `do` block,
  and lint is a CI gate. A lint must look through `try`.
- **Q5. A binding `else` for `try`** (`try e else Err x -> …`). Moot after Q1: `try` takes no
  `else`, and `let..else` already has the binding form.
- **Q6. Generic-code cost.** Specialisation would remove it. Out of scope.
- **Q7. Linear types.** Spec §5.8 forbids a consume after a fallible bind. `linear_check` keys that
  on the bind's `BindMode`; it must key on `try` instead, in all three forms: `lin_do_let`
  (`linear_check.sprout`) has no fallible flag today, and a pure `let..in` reaches the checker as a
  parse-time `match`, with a different message from `after_fallible_msg`. Depends on Q9.

Raised by the 2026-10-06 review; all must be decided before step 1:

- **Q8. The step-3 diagnostic.** As first proposed it is a permanent error, which forbids binding a
  whole `Result` (`r <- fs.write_text(…)`, the case §1 opens with) and contradicts §4.5. Make it
  temporary or a lint, with a later step that removes it.
- **Q9. Representation.** A typed `try` node, or a parse-time rewrite to `match branch(e)`. It
  decides diagnostic positions, Q7, the tuple scalar-replacement test and fusion. A rewrite to the
  bare names `branch`/`Continue`/`Break` can be captured by a user's own definitions
  (`stdlib/repl.sprout` defines a `Continue`), so the lowering needs names a module cannot shadow.
- **Q10. A failure swallowed by a discarded step.** A non-last `do` step whose value is discarded
  loses its failure: `let Just v = mx else Nothing` / `in Just(v)` as a step, then `Just(99)`,
  returns `Just 99` for `mx = Nothing`. That follows from Q2. It is rare with `let..else`; with
  `try` in pure code it will not be. Needs a diagnostic.
- **Q11. A bare `try e` statement, and `try` as the last step.** §4.2 first said a bare statement
  means `let _ = try e`, but a `do`-`let` is to be pure; `_ <- try e` fits. As the last step it is
  rejected by the trailing-binding rule with a message about a `let` or `<-` the user never wrote.
- **Q12. The operand.** "An application" rejects `try r` and `try p.field`. Alternative: any postfix
  expression (variable, field, call, parenthesised); only an infix expression or a pipe needs
  parentheses. Also decides what codemod A parenthesises.
- **Q13. Positions.** A `try` binding inside a multi-binding `let` (spec §5.2.1a forces an
  `else`-carrying binding to stand alone, and `try` has the same shape); a top-level `let` or a
  `where` binding, which have no block and must be rejected; which patterns may sit left of a `try`.
- **Q14. Building a success in generic code.** Require `Applicative t` beside `Propagate t`
  (§4.6), or give `Propagate` a method for it, as Rust's `Try` has `from_output`.
- **Q15. Step 5's scope.** Whether purity is also enforced for `let..in` (§4.5), which has no
  mechanical rewrite.

## 10. Tests

- Parser: `try e` in each binding position; `try` elsewhere rejected; an unparenthesised `|>`
  operand rejected; both reserved shapes rejected, and their parenthesised forms accepted.
- Typechecker, accepted: `Maybe`, `Result`, a user instance, generic `where Propagate t`, nested
  blocks.
- Typechecker, rejected: no instance; wrong family; wrong error type; unknown `t`; and, at steps 3
  and 5, a fallible `<-` and an effectful `do`-`let`.
- Runtime: both paths of every form, plus the nested-block semantics of Q2.
- Step 1: `x <- try e` with `e : Result E (Maybe A)` binds a `Maybe A` in a `Result` block.
- Codemod A: a pattern-`else` bind on a fallible right-hand side is refused, not rewritten.
- Codegen: a fused `try` chain matches the hand-written IR and allocates no `Step`.
- Linear: a consume after a `try` is rejected (Q7).
- The phase-0 regression fixtures keep passing.

## 11. Spec and docs impact

- Spec §5.9: replaced by `try`, experimental at step 1 and normative at step 3.
- `try` becomes a hard keyword. No identifier `try` exists in the three repos (strings and comments
  excluded). Places that list keywords or step starts: `lexer.is_keyword` and spec §2;
  `parser.looks_like_do_step_start` and spec §5.2.1a's step-start list; the formatter's
  `is_call_like_pp_kw`, else `try (x)` is reformatted to `try(x)`; the IntelliJ plugin's lexer
  keyword list and its test.
- Spec §5.2.1 and §5.2.2: `try` in binding right-hand sides; `do`-`let` purity enforced. Their
  "monadic propagation remains planned" notes point here.
- Spec, prelude classes section: `Propagate`, `Step`. The note that "a built-in `?`
  propagation form" is future work becomes `try`.
- `docs/idiomatic-sprout.md`: `try` idioms, and pure `do` blocks become `let..in`.
- `docs/let-else-and-monadic-binding-plan.md`: Tier 2 is this document. Tier 3 (monad-generic
  propagation) is not pursued.
- `README.md`: none until step 1 lands.
