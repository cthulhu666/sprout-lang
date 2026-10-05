# `try` and `Propagate` — early return through a class (v0)

Status: **proposal**, not implemented. Nothing here is normative until `docs/spec-v0.md` carries
it. Drafted 2026-10-05.

## 1. Problem

Inside `do`, `x <- e` has two meanings, chosen by the *name* of `e`'s type (spec §5.9). On `Maybe`
or `Result` it unwraps and may return early. On anything else it binds the whole value. Five
costs follow.

1. **The reader cannot see which lines may return early.** A user wrote
   `r <- fs.write_text(...)` and then `match r with | Ok _ -> …`, expecting `r : Result`. The bind
   had unwrapped it to `Unit`, and the error named the `match`, not the bind.
2. **It is unsound.** The checker decides the mode at the bind, from the type known *then*
   (`do_unwrap_type`, `stdlib/compiler/infer.sprout`). Codegen decides it again from the *final*
   type (`ast_to_ir.sprout`, the `TDoBindStep` arm). When the head is filled in later they
   disagree:

   ```sprout
   fn greet() -> Maybe String =
     do
       x <- pure("a")
       x
   ```

   This compiles and prints a pointer. The `Result` twin segfaults. A separate fix (phase 0, §8)
   closes the hole; this design removes the cause.
3. **Only two types can do it.** A user type with two kinds of failure (`Got a | NotFound |
   Failed String`) cannot short-circuit. The compiler tests the names `Maybe`/`Result` in five
   places: infer, codegen, two optimisation gates, and `linear_check` (`bind_short_circuits`).
4. **Errors land on the wrong line.** `x <- pure("a")` followed by `x ++ "b"` reports
   `` `++` needs matching Semigroup operands: $t3031 String vs String `` at the `++`.
5. **Pure code has no propagation.** A pure function uses `do` only to get the short-circuit, and
   `let..in` has none (the plan's Tier 2 is still open).

## 2. Goals and non-goals

**Goals.**

- One meaning per syntax. `<-` means "an effect runs here". `try` means "this may return early".
- User types opt in through a class. The compiler tests no type names.
- Zero cost at a known instance: the same code as a hand-written `match` (§5 measures the bar).
- Works in `do` and in pure `let..in`.
- Error conversion without implicit conversions: `else` replaces the failure, `with` wraps it.

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

## 4. Design

### 4.1 The class

```sprout
type Step r a = Continue a | Break r          # names open, §9 Q3

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
`let..else` step does (spec §5.2.2). A bare `try e` statement in `do` means `let _ = try e`.

**Typing.** `r : t b` becomes the value of the enclosing block, so the block's type must unify with
`t b`. This one unification replaces §5.9's table: a `Maybe` in a `Result` block fails, and so
does `Result String _` in a `Result Int _` block. No special rule is needed.

**Where the failure goes.** It becomes the value of the enclosing `let..in` or `do` block. When that
block is the function body, the function returns it. This is what `<-` does today. A probe with a
fallible bind in a nested `do` showed the failure ending only the inner block, though §5.9 says it
"returns from the enclosing function" (§9 Q2).

**An unknown `t`** is an ordinary class constraint. It resolves as late as any other, and an
unresolved one is the usual ambiguity error at the `try`. The mode is fixed by the syntax, so the
checker and codegen cannot disagree about it.

### 4.3 `try e else fb` — replace the failure

```
let x = try e else fb   →   match branch(e) with | Continue x -> <rest> | Break _ -> fb
```

`fb` has the block's type and is evaluated only on failure. It covers the two shapes `with`
cannot: discarding the cause, and crossing from `Maybe` into `Result`:

```sprout
let text = try bytes.to_string(raw) else Err(BadClientData("json"))
let decoded = try b64.decode(text) else Err(BadRequest(name))
```

### 4.4 `try e with f` — wrap the failure

`try e with f` is `try map_failure(f, e)`:

```sprout
class MapFailure p
  fn map_failure(f: e1 -> e2, value: p e1 a) -> p e2 a

instance MapFailure Result
  fn map_failure(f: e1 -> e2, value: Result e1 a) -> Result e2 a =
    match value with
    | Ok v -> Ok(v)
    | Err err -> Err(f(err))
```

A class over a two-argument constructor compiles and runs today. `Maybe` has no error to convert
and cannot be an instance, so `with` on a `Maybe` is a type error whose message points to `else`.
A user type with one error slot can add an instance.

### 4.5 `<-` and `let` inside `do`

| Line | Effect? | May return early? |
|---|---|---|
| `let n = parse_count(s)` | no | no |
| `line <- read_line()` | yes | no |
| `row <- try pg_query(conn, sql)` | yes | yes |
| `let cfg = try parse_config(text)` | no | yes |

`x <- e` never unwraps. A `do`-`let` must be pure. Spec §5.2.2 already calls it "the pure local
bind", but the checker accepts an effectful right-hand side today (`let r = fs.write_text(...)`
compiles and runs).

### 4.6 Generic code

`try` works under `where Propagate t`. It costs a dictionary call and a `Step` box (§5).

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

**Generic code** still pays the dictionary call and the box. `docs/fold-while-v0.md` §6 rejected a
`Continue`/`Done` type because "Sprout boxes every constructor", which on `any`/`all` would mean
one allocation per element of every fold. Here the box is confined to generic code that cannot
short-circuit at all today, and fusion removes it everywhere else.

**Today's fast paths:**

| Fast path | Decided by | Effect of this design |
|---|---|---|
| Unboxed worker return (`_worker`, tag + payload) | shape: at most one field per constructor | unchanged; already serves user ADTs (variant D) |
| `<-` short-circuit lowering | name (`is_maybe_type` / `is_result_type`) | removed; fusion replaces it |
| Unboxed C runtime reads | a fixed list of 8 externs | unchanged (runtime ABI) |
| Tuple scalar replacement | shape, tuples only | its name test (`sra_rest_plain`) becomes a `try` test |

A constructor with two or more fields is boxed even in hand-written code. Widening unboxed returns
helps every ADT, but it is separate work.

## 6. Diagnostics

Proposed wording. Each is reported at the `try`, the `let` or the `<-`, never at a later use.

```
no instance        `try` needs a type that can fail, and `Int` has no `Propagate` instance.
wrong block type   this `try` returns a `Result String _` failure, but the block returns `Int`.
                   Handle it here with `else` or `with`, or make the block return `Result String _`.
Maybe in Result    ... plus: to turn `Nothing` into an error, write `try e else Err(...)`.
with on Maybe      `with` converts an error, and `Maybe` has none. Use `else`.
unknown t          the existing ambiguity error.
effectful do-let   this `let` runs an effect. Bind it with `<-`.          (phase 3)
old fallible <-    `<-` no longer unwraps `Result`. Write `x <- try e`.   (phase 3)
```

## 7. Interaction with `let..else`

The two overlap. `let Just x = find(k) else Nothing` and `let x = try find(k) else Nothing` mean
the same thing.

- `let..else` matches **any refutable pattern** on any type (`let Cons h _ = xs else d`).
- `try` asks the **type** what success is, through `Propagate`, and needs no pattern.

The plan's Tier 2 proposed `let Ok x = e` with no `else` as the propagate form
(`docs/let-else-and-monadic-binding-plan.md` §2). `try` replaces that proposal, for two reasons.
A pattern binding without `else` looks like an ordinary binding, which is the invisible-mode
problem again. And today a refutable pattern without `else` is a non-exhaustive-match error;
turning that error into propagation would silently change the meaning of a mistake.

## 8. Compatibility and migration

Breaking at phase 3: a fallible `<-` needs `try`, and an effectful `do`-`let` becomes `<-`.

**Size.** A rough count of fallible binds (callee name looked up against `fn` signatures, ±50%):
576 certain across sprout_lang, uncharted-suns and repbit, an estimated 600–900 in total.
uncharted-suns is mostly `Maybe`, repbit mostly `Result`. Effectful `do`-`let`s are not counted.
Exact counts need the compiler, not grep.

**Codemod.** Add `try` after every propagating `<-`. That is always valid, because `<-` with a pure
right-hand side stays legal (§9 Q4). Rewrite every effectful `do`-`let` to `<-`. Check that golden
IR is unchanged after normalising names.

**Phases.**

0. Soundness fix, a separate PR. Store one "propagates" decision per bind, read it in codegen and
   `linear_check`, and reject a bind whose head becomes `Maybe`/`Result` only after the bind. The
   decision it stores is also the list the codemod needs.
1. Add `Step`, `Propagate`, `MapFailure` and their instances, plus `try`/`else`/`with` and fusion.
   Old fallible `<-` keeps working.
2. Run the codemod on sprout_lang, then on uncharted-suns and repbit.
3. Flip: a fallible `<-` is an error with the migration hint, and `do`-`let` must be pure.

## 9. Open questions

- **Q1. Where can `try` appear?** Proposed: binding positions only (a `let` or `<-` right-hand
  side, or a bare statement in `do`), which lower to `match`. Rust, Swift and Zig allow it in any
  expression, which needs a real early return in codegen.
- **Q2. Where does the failure go?** Proposed: the enclosing block, which is what `<-` does today.
  Fix §5.9's "returns from the enclosing function" to match.
- **Q3. Names.** Collisions in code (comments and strings excluded): `Step` 52 in sprout_lang and
  175 in uncharted-suns; `Continue` 52 (a constructor in `stdlib/repl.sprout`); `branch` 5;
  `Break`, `Propagate`, `MapFailure`, `map_failure` 0. `Step` needs another name.
- **Q4. `<-` with a pure right-hand side.** Allowed, error, or lint? Proposed: allowed, linted.
- **Q5. A binding `else` for `try`** (`try e else Err x -> …`), as `let..else` has. It is consistent
  with `let..else` and would cover `with`'s job, at more length.
- **Q6. Generic-code cost.** Specialisation would remove it. Out of scope.
- **Q7. Linear types.** Spec §5.8 forbids a consume after a fallible bind. `linear_check` keys that
  on the bind's type (`bind_short_circuits`); it must key on `try` instead.

## 10. Tests

- Parser: `try e`, `try e else fb`, `try e with f`; precedence against calls and `|>`.
- Typechecker, accepted: `Maybe`, `Result`, a user instance, generic `where Propagate t`, `Maybe` to
  `Result` through `else`, conversion through `with`, nested blocks.
- Typechecker, rejected: no instance; wrong family; wrong error type; `with` on `Maybe`; unknown
  `t`; and, at phase 3, an effectful `do`-`let` and a fallible `<-`.
- Runtime: both paths of every form, plus the nested-block semantics of Q2.
- Codegen: a fused `try` chain matches the hand-written IR and allocates no `Step`.
- Linear: a consume after a `try` is rejected (Q7).
- The phase-0 regression fixtures keep passing.

## 11. Spec and docs impact

- Spec §5.9: replaced by `try`, experimental at phase 1 and normative at phase 3.
- Spec §5.2.1 and §5.2.2: `try` in binding right-hand sides; `do`-`let` purity enforced. Their
  "monadic propagation remains planned" notes point here.
- Spec, prelude classes section: `Propagate`, `MapFailure`, `Step`. The note that "a built-in `?`
  propagation form" is future work becomes `try`.
- `docs/idiomatic-sprout.md`: `try` idioms, and pure `do` blocks become `let..in`.
- `docs/let-else-and-monadic-binding-plan.md`: Tier 2 is this document. Tier 3 (monad-generic
  propagation) is not pursued.
- `README.md`: none until phase 1 lands.
