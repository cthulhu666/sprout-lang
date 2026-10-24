# A wildcard that drops a linear value is a leak (v0)

Status: approved 2026-10-24; implemented.
Closed the BACKLOG P2 entry "Decide whether a wildcard pattern over a linear value is a consume
or a leak". Amends spec-v0 §5.8.

## 1. Problem

Today these compile, and each drops a linear value without consuming it:

```sprout
type linear Conn = Conn Int
type linear Wrap = Wrap Conn

fn a(c: Conn) -> Int = match c with | _ -> 0          # the parameter
fn b() -> Int = match open(1) with | _ -> 0           # an unbound value
fn c(w: Wrap) -> Int = match w with | Wrap _ -> 0     # a linear field
fn d() -> Int = let _ = open(1) in 0                  # `let _` in a pure body
```

Use is syntactic (§5.8): the scrutinee is referenced once, so the binder rules see no leak.
With `Wrap TcpConnection`, shape `c` leaks a file descriptor and looks deliberate. Shape `d` is
also inconsistent: the same `let _ = open(1)` in a `do` block is rejected.

## 2. Goals and non-goals

Goals:
- Reject a `_` that can drop a linear value from an owned position.
- Keep every `_` that drops nothing linear: an arm that can only match constructors without
  linear fields, a non-linear field, anything matched through a borrow.
- One rule for `match`, `let..in`, `let..else`, `<-` and `try`.

Non-goals:
- A generic `drop`/`consume` function. A linear type's module consumes it by destructuring
  (`File _ -> ()` over an `Int` field stays legal); everyone else calls its API.
- Lambda and function parameters named `_` (a binder, not a pattern; unchanged).
- Runtime ownership of queued values (§5.8 "The limit this draws"; unchanged).

## 3. Prior art (verified)

- **Linear Haskell** (GHC proposal 0111, §5.4.2): "Linear wildcard patterns are disallowed."
  §4.1: consuming a value means "discriminating on its tag any number of times, then consuming
  its fields exactly once"; §4.5: an unrestricted field "does not need to be consumed". GHC
  9.12.2 rejects shapes a–c with `Couldn't match type 'Many' with 'One' arising from a
  non-linear pattern '_'`.
- **Austral** (spec): "Linear values which are not stored in variables cannot be discarded", and
  every union slot "must have a binding". It has no `_` pattern.
- **Rust** accepts all four, because it is affine: `_` "does not copy, move, or borrow the value"
  (Reference, Wildcard pattern), and the destructor runs at end of scope or statement. Sprout has
  no destructors (language-design-v0, memory-management stance), so nothing would run.

Sprout follows GHC: per constructor, per field.

## 4. The rule

A `_` pattern at an **owned** position is a leak when it can match a value that holds linear
content. "Holds linear content" is `type_mentions_linear`, the binder rules' containment test
(phantom positions excluded).

1. **Nested `_`** (a constructor field or tuple component): a leak iff that position's type
   mentions a linear type.
2. **A whole-arm `_`** (also `let _ =` and the synthetic `else` arm of `let..else`): a leak iff the
   scrutinee's type mentions a linear type, with one refinement. When the type is a non-linear
   ADT, the arm leaks only if some constructor it can still match has a field whose
   instantiated type mentions a linear type. A constructor is ruled out when an earlier arm is
   that constructor with only irrefutable sub-patterns (variable, `_`, unit, or a tuple of
   those). Anything else stays matchable. A linear type, a tuple, a record or a type variable
   gets no refinement: `_` over it leaks whenever it mentions a linear type.
3. A **borrowed** scrutinee (`expr_is_borrowed_binding`) is exempt: nothing is consumed.

Consequences:

| shape | today | new |
|---|---|---|
| `match c with \| _ -> 0`, `c: Conn` | accepted | rejected (2) |
| `match open(1) with \| _ -> 0` | accepted | rejected (2) |
| `let _ = (open(1), 2) in 0` | accepted | rejected (2) |
| `Wrap _`, field `Conn` | accepted | rejected (1) |
| `let _ = open(1) in 0` | accepted | rejected (2) |
| `(_, n)` over `(Conn, Int)` | accepted | rejected (1) |
| `Wrap _ <- m` in `do` | accepted | rejected (1) |
| a `try e` statement whose success value is linear | accepted | rejected (1) |
| `\| Just (File 1) -> … \| _ -> 0` | accepted | rejected (2): `_` can match a `Just` |
| `let A n k = t else B(0, 0)`, `t` of a linear type | accepted | rejected (2): write `else other -> …` |
| `\| Filled v -> … \| _ -> Break(Blank)` | accepted | accepted: `_` matches `Blank` only |
| `let (c, n) = try slot()`, a fused `Slot (Conn, Int)` | accepted | accepted: tuple is irrefutable |
| `let Just(f) = w else 0`, `w: Maybe File` | accepted | accepted: `_` matches `Nothing` |
| `File _ -> ()`, field `Int` | accepted | accepted |
| `PgConnection _ s` on a borrowed value | accepted | accepted (3) |

The `try` row: a non-final `try e` statement is `_ <- try e` (`ast.try_statement_steps`), which
`try` fusion turns into a `_` under the success constructor. Dropping a linear success value is a
leak, so it is rejected.

## 5. Diagnostics

- Whole value: "`_` drops a linear value of type `Conn` without consuming it. Bind it and pass it
  on, or match its constructor and consume its linear fields."
- Field: "`_` drops a linear field of type `Conn`. Bind it, as in `Wrap c`, and consume it."
  The example names the constructor written in the pattern, with `c` at the field and `_`
  at its siblings (`Pair c _`). A tuple component has no constructor: "`_` drops a linear
  field of type `Conn`. Bind it to a name and consume it."
- The do-block `let _ = e` message ("linear value '_' is never used") becomes the whole-value
  message, so both `let _` forms read the same.

Positions, by form:
- a written `_` in a `match` arm or pattern: the `_`;
- `let..else`'s synthetic arm: the `let` (it has no `_` of its own);
- a fused `try`: the `try`, where fusion places every pattern;
- do-block `let _ = e` and `_ <- e`: `e`, as today.

## 6. Implementation

- **`infer.sprout`**: the checker's `env` has no constructor list per type. `@ctor:<name>`
  carries arity only, and `@stores:<T>` flattens every constructor's fields into one tuple. Add a
  `@ctors:<T>` marker listing `T`'s constructor names, in `register_ctors_for_type`, beside the
  `@stores:`/`@phantom:` families and keeping their invariant (no free type variables).
- **`linear_check.sprout`**:
  - `wildcard_drop(pat, t, env) -> Maybe (SourcePos, String)`: rule 1 over the pattern tree,
    field types from `ctor_field_types_at`, every `ast.Pattern` arm listed (guidelines §2a).
  - `arm_consumed` calls it when the scrutinee is owned. `arm_sets` threads the constructors
    ruled out by earlier arms and applies rule 2 to a whole-arm `_`.
  - Constructor binds in `do` (`Wrap _ <- m`) arrive as a match over a temporary
    (`ast.do_pat_steps`), so `arm_consumed` covers them. `lin_do_bind_checked` sees only `_`,
    variables and tuples of those. It calls `wildcard_drop` for a tuple; a bare `_ <- e` keeps
    its existing check, with the new message.
  - The do-block `let _` path reports the new message.
  - The comment over `lin_do_bind_checked` that calls `Wrap _` "the separate open question …
    filed in BACKLOG" is rewritten.

Measured before deciding (a throwaway probe of the strict variant over sprout_lang, uncharted-suns,
repbit and sprout-postgres): no owned site drops a linear value. The strict variant's only hits
were two `Slot` instances in tests, which rule 2 accepts, and sprout-postgres' five borrowed
`PgConnection _ session` matches, which rule 3 accepts. No migration is expected; the full suite
and the downstream builds confirm it.

## 7. Tests

- `type_error/` fixtures (`.spr`, `.err`, `.fixed.spr`, `#~^ ERROR` at the §5 position) for each
  rejected row of §4, plus a `where linear a` field.
- An accepted conformance fixture for each accepted row, plus `Just _ -> 0 | Nothing -> 1` over
  `Maybe Int`, and the `Slot (Conn, Int)` fused `try` with the `test_try.spr` instance.
- Unit tests for the `@ctors:` marker beside the existing `@stores:` ones.
- Linear grid (`scripts/gen_linear_grid.sh`), three columns over each row's owned linear value:
  `wildarm` rejects a whole-arm `_`, `wildfield` rejects `(_, n)` over `(x, 0)`, and `wildok`
  accepts `(y, _)`. The generator's header lists the rows where a column does not apply.

## 8. Spec and docs

- spec-v0 §5.8: a **Wildcard** rule beside Leak/Reuse, absorbing the `_ <- e` sentence under
  **Discarded result** and replacing "Two shapes remain accepted and are **not** guaranteed
  leak-free"; state the borrow exemption and the per-constructor refinement.
- BACKLOG: delete the P2 entry.
- `docs/persistent-array-v0.md`: "a wildcard that drops an array loses it" now says the wildcard
  is rejected.
