# `try` and `Propagate` — propagation through a class (v0)

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
`match` over the remaining steps, so it is in the second group. Both first-group languages with a
block form added it deliberately (Rust `try` blocks, Swift `do`-`catch`).

Gleam is the nearest precedent and cuts the other way on syntax. It had a block-scoped `try`
keyword from v0.9 and removed it in v0.27: "Now that we have `use` expressions, the less general
`try` expressions are redundant"; it prefers "fewer ways to do the same thing". Its replacement is
`use x <- result.try(e)`, a library function. This design keeps the keyword but backs it with a
class, as Rust backs `?` with `Try`.

### Discarding a failure (§9 Q8)

Checked 2026-10-06 against each primary source. Each flags a result dropped in silence and has a
written-out discard.

| Language | Flags | Written-out discard | Source |
|---|---|---|---|
| Rust | an expression statement of a `#[must_use]` type, such as `Result` (lint `unused_must_use`) | `let _ = f();` | Reference, *Diagnostic attributes* |
| Swift | a call whose result is unused, unless the function is `@discardableResult` | — | *The Swift Programming Language*, Attributes |
| Haskell | a `do` statement whose result is not bound (`-Wunused-do-bind`, in `-Wall`) | `_ <- e` | GHC User's Guide, Warnings |
| OCaml | a non-`unit` expression left of `;` (warning 10, on by default) | `ignore (f x)` | OCaml Manual, Warnings; `Stdlib.ignore` |

Sprout cannot use the wildcard bind, as Rust and Haskell do: `_ <- e` passes the failure on today,
so making it the discard would change old code silently. It takes OCaml's `ignore`.

## 4. Design

### 4.1 The class

```sprout
type ControlFlow r a = Continue a | Break r   # names: §9 Q3

class Propagate t
  fn branch(value: t a) -> ControlFlow (t b) a

instance Propagate Maybe
  fn branch(value: Maybe a) -> ControlFlow (Maybe b) a =
    match value with
    | Just v -> Continue(v)
    | Nothing -> Break(Nothing)

instance Propagate (Result e)
  fn branch(value: Result e a) -> ControlFlow (Result e b) a =
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
`let..else` step does (spec §5.2.2). A bare `try e` statement, not last, passes a failure on and
drops the success value: it means `_ <- try e`, or `let _ = try e` when `e` is pure. A success
value that is itself fallible is caught by the discard rule (§4.5). `try` as a block's last step is
an error, since the block ends there anyway: write `e` without `try` (§9 Q11).

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

Corpus, non-test code of the four repos, one-line forms: about 83 sites replace a failure (30 of
them in sprout-pg) and about 7 wrap it, against about 600 that propagate it unchanged (sprout-pg
not counted). For replacing, a `try e else fb` suffix is the same length as `let..else` and means
the same thing. Wrapping is where a suffix would be shorter.

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

`x <- e` never unwraps. A `do`-`let` must be pure, and a `<-` must not be (§9 Q4): a right-hand
side known to be pure is an error ("this has no effect; write `let x = …`"). An unknown effect
(`!{e}`) may be an effect, so it takes `<-`. The two rules give every right-hand side exactly one
binder, and share one purity check, looking through `try`. Spec §5.2.2 already calls a `do`-`let`
"the pure local bind", but the checker accepts an effectful right-hand side today
(`let r = fs.write_text(...)` compiles and runs). The same holds for `let..in`: spec §5.2.1 makes
an effectful right-hand side an error, yet `let r = fs.read_text(...) in …` compiles in an `!{IO}`
function. So does a `where` binding, the same construct (§5.1), and a top-level `let`, which spec
§5.2 says must be pure ("Not yet enforced"). Step 5 enforces all four (§9 Q15).

**Discarding a failure** (§9 Q8). A non-final `do` statement, or a `_ <-` bind, whose value's type
has a `Propagate` instance is an error. Write `try e` to pass the failure on, or `ignore(e)` to drop
it; `ignore(x: a) -> Unit` is a pure prelude function. This extends spec §5.8's *Discarded result*
rule from linear values to fallible ones. A named `x <- e` stays legal and binds the whole value.

### 4.6 Generic code

`try` works under `where Propagate t`. It costs a dictionary call and a `ControlFlow` box (§5).

Generic code can propagate but cannot build a success: `Propagate` has no `pure`-like method, so a
`where Propagate t` function returning `t a` also asks for `Applicative t` (§9 Q14). A type with
both instances must satisfy `branch(pure(x)) == Continue(x)`.

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
hand-written `match` (modulo SSA names), and no `sprout_alloc_obj` for `ControlFlow`.

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
| Tuple scalar replacement | shape, tuples only | unchanged: a `try` in the rest of the block disables it, as a fallible `<-` does today (`sra_rest_plain`) |

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
last-step try      the block ends here anyway, so `try` does nothing. Write `e` without `try`.
effectful let      this `let` runs an effect. Bind it with `<-` in a `do` block.  (step 5)
pure <-            this has no effect. Write `let x = …`.                  (step 3)
discarded failure  this drops a `Result` failure in silence. Write `try e` to pass it on,
                   or `ignore(e)` to drop it.                          (step 3)
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
uncharted-suns is mostly `Maybe`, repbit mostly `Result`. sprout-pg adds up to 100 `<-` lines, not
yet split by type. Effectful `do`-`let`s are not counted. Exact counts need the compiler, not grep.

**Order (proposed; reviewed 2026-10-06 by three independent passes).** Every step must leave all
four repos compiling with unchanged behaviour.

0. ~~Soundness fix.~~ Landed: `typed_ast.BindMode` on each bind, decided in
   `infer.decide_bind_mode` and read by every later pass; a head that becomes `Maybe`/`Result`
   after the bind is rejected (spec §5.9). The modes are also the list the codemod needs.
   0b. Move `let..else` and `do` pattern binds from the parser into inference, as Q9 does for
   `try`; decided 2026-10-06. First commit landed: the parser emits `ast.LetBindExpr` and
   `ast.DoPatStep`, and inference rewrites them via `ast.let_bind_match` and `ast.do_pat_steps`.
   A refactor: the rewrite emits the same `match`, so the acceptance test is an unchanged seed
   fixed point and golden IR. It builds the machinery `try` uses under that test. Once in
   inference, the synthetic `__t <- e` can be marked plain, which the parser cannot do; that
   changes behaviour, so it belongs to step 1, not 0b (today `Just x <- g() else Nothing` with
   `g : Maybe (Maybe Int)` binds `x = 2`). Second commit landed: a fallback whose type differs
   from the body's is reported at the `else` value, naming both types, instead of as "Match branch
   type mismatch", and a trailing pattern or `else` binding gets the same inference error as a
   trailing `let`. Third commit landed: a multi-binding `do`-`let` statement takes patterns and
   `else` (§9 Q13).
1. Add `ControlFlow`, `Propagate` and their instances, `try`, its two reserved shapes (§4.4)
   and fusion, as separate PRs in that order. The first landed: `ControlFlow`, `Propagate` and
   the `Maybe` and `Result e` instances, in the prelude (spec §8.5). A local or top-level
   `branch` cannot capture `try`'s rewrite (Q9). `ignore` moves to step 2, its first user;
   `tests/stdlib/test_linear_borrowing.spr` defines its own `ignore`, to be renamed then. Old
   fallible `<-` keeps working, except that a `<-` whose right-hand side
   is a `try` (after stripping parentheses) is always plain. Otherwise `x <- try e` with
   `e : Result E (Maybe A)` unwraps twice. The rule must reach every place that reads a bind's
   type, not only `decide_bind_mode`: `do_family_update` (`infer.sprout`) sets the block's family
   from the step's type for every `DoBindStep`, and the synthetic `__t <- e` binds
   (`ast.do_pat_steps`) carry the user's right-hand side.
   1a. Tooling: a compiler phase that lists every bind whose `BindMode` propagates (and whether
   its right-hand side is pure), every non-final `do` statement with a fallible value, and every
   effectful `do`-`let`, with file, line and column. Only the type checker knows any of them, and
   no `--phase` reports them today.
2. Codemod A, on all four repos and every live worktree branch: add `try` to every listed `<-`,
   writing `let x = try e` where the right-hand side is pure (§9 Q4), and wrap every listed
   fallible statement in `ignore(…)`, which this step adds to the prelude. `x <- try e` means
   what the old `x <- e` did. The codemod must parenthesise an operand that is not a postfix
   expression (§9 Q12; 12 sites in the four repos, non-test), map a synthetic `__t <-` back
   to the user's line, and refuse a pattern-`else` bind on a fallible right-hand side
   (`Just x <- e else …`): its faithful rewrite is the reserved shape, and today it unwraps twice
   (`e = Just(Just(2))` binds `x = 2`). A grep of the four repos finds no such site. Running
   it on `stdlib/compiler/` needs step 1 landed and reseeded first.
3. Flip: `<-` never unwraps. The discard rule and the pure-`<-` error (§4.5) land with it, so a
   stale `_ <- e` is an error, not a silent discard. `Ok x <- e else …` on a `Result`, a type
   error today because the synthetic bind unwraps, becomes legal; that change is intended.
4. Codemod B: rewrite every effectful `do`-`let` to `<-`, and every effectful `let..in` and
   `where` binding to a `do` block with `<-` (`do` is an expression), except that `let _ = e`
   with a fallible `e` becomes `ignore(e)`, since `_ <- e` is now an error. Behaviour is preserved
   because `<-` is now plain; before step 3 it is not (`let _ = fs.write_text(bad, …)` continues,
   `_ <-` stops the block). IR is not preserved: a tuple bound by a `do`-`let` gets scalar
   replacement (`sra_core_eligible`, `ast_to_ir.sprout`) and a plain `<-` does not. Extend it to
   plain `<-` first, so the rewrite costs nothing.
5. Enforce purity for every `let` form: `do`-`let`, `let..in`, `where` and top-level `let`
   (§9 Q15). An effectful top-level `let` has no rewrite to `<-` and is fixed by hand, usually by
   making it a function.

All Sprout code is in these four repos, so each step migrates all of them together; there is no
compatibility window for outside code. The steps stay separate for other reasons: the compiler's
own source has hundreds of `<-` lines, so the seed must know `try` before that source uses it; and
codemod A is checked alone, since under the old rules it must change no test result, and its
`try` half no golden IR. Downstream CI builds against sprout-lang master, so codemod A must be
merged downstream before step 3 lands, and codemod B before step 5. A branch that rebases past
step 3 without codemod A fails on every fallible `_ <- e` or statement; a named `x <- e` whose `x`
type-checks either way (`show(x)`) is caught only by tests.

## 9. Open questions

- **Q1. Where can `try` appear?** **Decided (2026-10-06):** at the head of a binding's right-hand
  side (`let x = try e`, `x <- try e`) or as a bare statement in `do`, lowering to `match`. Its
  operand is a postfix expression (Q12); anything else is parenthesised (`try (a |> f)`). No
  `else` or `with` suffix: failures are replaced or wrapped with `let..else` (§4.3), and two shapes
  are reserved so the suffixes can be added later (§4.4). The suffixes were weighed against a
  corpus count and against their grammar cost: `else` and `with` already mean two things each.
  Rust, Swift and Zig allow `try` in any expression, which needs a real early return in codegen.
- **Q2. Where does the failure go?** **Decided (2026-10-06): the enclosing block**, which is what
  `<-` does today. Prior art in §3. Spec §5.9 said "returns from the enclosing function"; it now
  says the block.
- **Q3. Names.** **Decided (2026-10-06): `ControlFlow` with `Continue` and `Break`**, Rust's
  names, in Rust's parameter order (failure first). Declared in the four repos, tests included:
  `Step` as a type in 6 files (`ide/editor` and uncharted-suns `chess/attack` export one) and as
  a constructor in 1; `Continue` as a constructor in `stdlib/repl.sprout` and
  `stdlib/tui/app.sprout`; `ControlFlow`, `Break`, `Propagate` nowhere. A clash breaks nothing: a
  module's own name shadows the prelude's, and so does a selectively imported one (checked: an
  imported `IntRange` beside `1..4`). With Q9's requirement, `try` is unaffected too. A
  shadowing module writes the prelude's as `prelude.Continue` (spec §3.1).
- **Q4. `<-` with a pure right-hand side.** **Decided (2026-10-06): an error** (§4.5), from
  step 3. A lint cannot do it: Sprout's lint never consults types or effects
  (`docs/lint-rules-v0.md`). A warning would be the compiler's first (`DiagWarning` exists, nothing
  constructs one) and fails nothing. Swift makes a redundant `try`/`await` a warning
  (`no_throw_in_try`, `no_async_in_await` in `DiagnosticsSema.def`); hlint suggests "Use let" for
  `x <- return y`. A callee that drops its effect breaks each `x <- callee()`, but effects are
  declared in the signature, so that is a signature change like any other.
- **Q5. A binding `else` for `try`** (`try e else Err x -> …`). Moot after Q1: `try` takes no
  `else`, and `let..else` already has the binding form.
- **Q6. Generic-code cost.** Specialisation would remove it. Out of scope.
- **Q7. Linear types.** Spec §5.8 forbids a consume after a fallible bind. `linear_check` keys that
  on the bind's `BindMode`; it must key on `try` instead, in all three forms: `lin_do_let`
  (`linear_check.sprout`) has no fallible flag today, and a pure `let..in` reaches the checker as a
  parse-time `match`, with a different message from `after_fallible_msg`. After Q9 the rule comes
  from branch convergence (spec §5.8): a consume after a `try` sits in the `Continue` arm only.
  What remains is the message, which must name the `try`, not a `match`.

Raised by the 2026-10-06 review; all must be decided before step 1:

- **Q8. The step-3 diagnostic.** **Decided (2026-10-06): no migration diagnostic; a permanent
  discard rule instead** (§4.5). A fallible `_ <- e` or non-final statement is an error; `ignore(e)`
  drops a failure on purpose, `try e` passes it on, and a named `x <- e` binds the whole value. A
  temporary error would guard only its window, and a branch rebased after it would change in
  silence; the rule catches a stale `_ <- e` at any time. Prior art in §3. The first proposal, an
  error on every `<-` that would have unwrapped, forbade binding a whole `Result`.
- **Q9. Representation.** **Decided (2026-10-06): an untyped `try` node, checked and rewritten
  inside inference**, as list comprehensions are (`docs/list-comprehensions-v0.md` §D2). The check
  raises §6's diagnostics with the user's `try` in hand; the rewrite is
  `match branch(e) with | Continue x -> rest | Break r -> r`, positioned at the `try`, and inference
  types it as ordinary code. A rewrite after inference is unsound, since `linear_check` runs during
  inference. A typed node through to lowering doubles the passes touched (about 8 typed-side files)
  and needs its own linear rule. A parse-time rewrite reports errors about a `match` the user never
  wrote. Requirement: the rewrite's references to `branch`, `Continue` and `Break` must reach the
  prelude's, never a user's. Met by `docs/prelude-name-identity-v0.md` except for class methods:
  after bundling a module's own names are qualified and a local that shares a prelude name is
  renamed, but a user class method named `branch` stays bare and would capture the rewrite (spec
  §3.1's limits; BACKLOG).
- **Q10. A failure swallowed by a discarded step.** A non-last `do` step whose value is discarded
  loses its failure: `let Just v = mx else Nothing` / `in Just(v)` as a step, then `Just(99)`,
  returns `Just 99` for `mx = Nothing`. That follows from Q2. **Decided by Q8:** the step is a
  discarded fallible statement, so it is an error.
- **Q11. A bare `try e` statement, and `try` as the last step.** **Decided (2026-10-06):** a
  bare non-final `try e` is allowed (§4.2); after Q8 it replaces today's `_ <- e`. `try` as the
  last step is rejected with its own message, not the trailing-binding one; the rare flatten
  (`e : Maybe (Maybe A)`) is `let x = try e`, then `x`. Rust allows `f()?;`, and clippy's
  `needless_question_mark` flags `Some(x?)` in return position: "There's no reason to use `?` to
  short-circuit when execution of the body will end there anyway." Zig requires `_ = try f();`
  unless the success type is `void` ("Expressions of type void are the only ones whose value can
  be ignored"); Sprout discards a non-fallible value freely today, and keeps that.
- **Q12. The operand.** **Decided (2026-10-06): a postfix expression** — a variable, a field
  access, a call (qualified or a constructor's) or a parenthesised expression. An infix
  expression, a pipe, `if`/`match`/`do`/`let` or a lambda needs parentheses, and the error says so.
  Q1 said "an application", which rejected `try mx`. Prior art splits on `try a + b`: Swift's `try`
  "applies to the whole infix expression", Zig's binds like `!x`/`-x`, tighter than `+`, and Rust's
  `?` is postfix. Swift's `try` changes no value; Sprout's unwraps, so the two readings differ in
  type, and parentheses remove the question. Corpus, `<-` right-hand sides in the four repos,
  non-test: 4 variables (`mx`, `rx`), 1 pipe, 11 `if`/`match`/`do`/`let`/lambda, of 2617.
- **Q13. Positions.** **Decided (2026-10-06).** A top-level `let` rejects `try`: no block or
  function is there to end. A `where` binding accepts it, and a failure becomes the function's
  value: spec §5.1 makes `where` and `let … in` "the same binding construct", and `where` bindings
  run before the body. Left of a `try`, any pattern a plain `let` accepts; a refutable one without
  `else` gets the usual non-exhaustive error, and with `else` it is the reserved shape (§4.4). A
  `try` binding may sit in a multi-binding group. Spec §5.2.1a made an `else` binding stand alone
  in a `do`-`let` statement only because the parser's rewrite nested the remaining steps in a
  `match` arm; `let..in` groups already allowed it. Step 0b's third commit lifted that limit.
- **Q14. Building a success in generic code.** **Decided (2026-10-06): ask for `Applicative t`
  beside `Propagate t`** (§4.6); `Propagate` keeps one method. Rust's `Try` (unstable,
  `try_trait_v2`) has `from_output`, with the law `Try::from_output(x).branch() -->
  ControlFlow::Continue(x)`, because Rust has no `Applicative`. A superclass would make every type
  that uses `try` define `map`, `pure` and `map2`; a `from_output` method would duplicate `pure`.
- **Q15. Step 5's scope.** **Decided (2026-10-06): every `let` form**, as the spec already says:
  `do`-`let` (§5.2.2), `let..in` (§5.2.1), `where` (§5.1, the same construct) and top-level `let`
  (§5.2; §6 relies on imports running no effects). Q4's split, known pure to `let` and anything
  else to `<-`, only holds if no `let` can hide an effect. The review said `let..in` has no
  mechanical rewrite; it has one, since `do` is an expression (`ast.DoExpr`). Only a top-level
  `let` has none.

## 10. Tests

- Parser: `try e` in each binding position; `try` elsewhere rejected; each postfix operand
  accepted (variable, field, call, constructor, parenthesised); an unparenthesised infix, `|>`,
  `if`, `match`, `do`, `let` or lambda operand rejected; both reserved shapes rejected, and their
  parenthesised forms accepted.
- Typechecker, accepted: `Maybe`, `Result`, a user instance, generic `where Propagate t`, nested
  blocks; generic `where Propagate t, Applicative t` building a success with `pure`.
- Law: `branch(pure(x)) == Continue(x)` for `Maybe` and `Result e`.
- Typechecker, rejected: no instance; wrong family; wrong error type; unknown `t`; at step 3, a
  `<-` whose right-hand side is pure, with and without `try` (an `!{e}` one is accepted); and, at
  step 5, an effectful `do`-`let`, `let..in`, `where` binding or top-level `let`.
- Runtime: both paths of every form, plus the nested-block semantics of Q2.
- Q11: a bare `try e` passes a failure on and continues on success, effectful and pure; `try` as
  the last step is rejected with its own message.
- Step 1: `x <- try e` with `e : Result E (Maybe A)` binds a `Maybe A` in a `Result` block.
- Codemod A: a pattern-`else` bind on a fallible right-hand side is refused, not rewritten.
- Discard rule (Q8): a fallible `_ <- e` and a fallible non-final statement are rejected, in pure
  and effectful blocks; a named `x <- e` and `ignore(e)` are accepted; the final statement is
  exempt. `ignore` on a linear value does not drop it in silence.
- Codegen: a fused `try` chain matches the hand-written IR and allocates no `ControlFlow`.
- Capture (Q9): `try` works in a module that defines its own `Continue`, `Break` and `branch`.
- Positions (Q13): `try` in a `where` binding returns the failure from the function; in a
  top-level `let` it is rejected; in a multi-binding group, a failure skips the later bindings.
- Linear: a consume after a `try` is rejected (Q7).
- The phase-0 regression fixtures keep passing.

## 11. Spec and docs impact

- Spec §5.9: replaced by `try`, experimental at step 1 and normative at step 3. Its "discard form"
  (a bare fallible statement continues) becomes `ignore(e)`.
- Spec §5.8, *Discarded result*: extended to values with a `Propagate` instance (step 3).
- `try` becomes a hard keyword. No identifier `try` exists in the four repos (strings and comments
  excluded). Places that list keywords or step starts: `lexer.is_keyword` and spec §2;
  `layout.starts_step` and spec §5.2.1a's step-start list; the formatter's
  `is_call_like_pp_kw`, else `try (x)` is reformatted to `try(x)`; the IntelliJ plugin's lexer
  keyword list and its test.
- Spec §5.1 (`where`), §5.2.1 and §5.2.2: `try` in binding right-hand sides; `do`-`let` purity
  enforced. Their "monadic propagation remains planned" notes point here.
- Spec, prelude classes section: `Propagate`, `ControlFlow`. The note that "a built-in `?`
  propagation form" is future work becomes `try`.
- `docs/idiomatic-sprout.md`: `try` idioms, and pure `do` blocks become `let..in`.
- `docs/let-else-and-monadic-binding-plan.md`: Tier 2 is this document. Tier 3 (monad-generic
  propagation) is not pursued.
- `README.md`: none until step 1 lands.
