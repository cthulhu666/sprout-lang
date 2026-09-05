# Idiomatic Sprout

A short guide to the shapes idiomatic Sprout code takes — how to stay flat,
declarative, and free of boilerplate. Companion to
[style-guide-v0.md](./style-guide-v0.md) (how code looks) and
[guidelines.md](./guidelines.md) (design principles).

## Flatten "unwrap-or-default" with `let..else`

Instead of nesting `match` expressions to peel off `Maybe`/`Result` values, use
refutable `let` bindings. Each `<pat> = <e> else <fallback>` short-circuits the
whole block to its `else`; the happy path lands unindented at the bottom.

```sprout
# Nested — the shape to avoid:
fn build(ret_te: Maybe TypeExpr, ret_ty: Type, theta: Dict Type) -> Dict Type =
  match ret_te with
  | Nothing -> theta
  | Just te ->
      match match_te(te, ret_ty, theta) with
      | Nothing     -> theta
      | Just theta1 -> theta1

# Idiomatic — two guard lines, flat:
fn build(ret_te: Maybe TypeExpr, ret_ty: Type, theta: Dict Type) -> Dict Type =
  let Just te     = ret_te                      else theta
      Just theta1 = match_te(te, ret_ty, theta) else theta
  in theta1
```

A refutable pattern *requires* `else`; an irrefutable binding must not have one.
Refutability is judged against the pattern's **type**, not its shape, so a
`wrap` or single-constructor ADT pattern is irrefutable and takes no `else` —
including in a `do` block, where `Cents(c) <- earn()` destructures directly:

```sprout
fn take_home() -> Int !{IO} =
  do
    Cents(c) <- earn()
    c - 50
```

The right-hand sides are pure, and every `else` and the body unify to one result
type.

When the failure case needs the *value* it failed on — the common "format the
error from its payload" shape — write a **binding-else** whose residual pattern
names that value. It replaces the classic rightward staircase where each `Err`
arm re-reads its own payload:

```sprout
# Nested — each level re-matches to reach its own error payload:
fn validate(req: Request) -> Result String Session =
  match require_int(req, "id") with
  | Err msg -> Err(msg)
  | Ok id ->
      match require_str(req, "kind") with
      | Err msg -> Err(`kind: ${msg}`)
      | Ok kind -> Ok(session(id, kind))

# Idiomatic — the residual pattern binds the payload right at the `else`:
fn validate(req: Request) -> Result String Session =
  let Ok id   = require_int(req, "id")    else Err msg -> Err(msg)
      Ok kind = require_str(req, "kind")  else Err msg -> Err(`kind: ${msg}`)
  in Ok(session(id, kind))
```

The residual (`Err msg`) is a *full* pattern spliced into the fallback arm: it
binds `msg` for the handler, and a bare variable (`else other -> …`) would bind
the whole failing value instead. A constant `else` and a binding-`else` are told
apart by the `->`. The RHS must still be pure, but the body after `in` may be a
`do` block — so a pure validation gate can guard an effectful action.

## Reach for a combinator on a single `Maybe`/`Result`

`let..else` (above) shines for a *chain* of binds. For a *single* value, the
prelude combinators are shorter than a two-arm `match`:

```sprout
# unwrap-or-default — the value, or a fallback:
maybe_with_default(dflt, dict_get(key, d))   # vs. match … | Just v -> v | Nothing -> dflt

# transform the payload (Functor `map`):
map(trim, dict_get(key, d))                  # Maybe String -> Maybe String

# chain another fallible step (Monad `and_then`):
and_then(parse_int, dict_get("port", d))     # Maybe String -> Maybe Int
```

`map` and `and_then` work over any `Functor`/`Monad` — `Maybe`, `Result`, and
`List` — so the same two names cover every container; `Result` adds
`result_map_error`/`result_with_default`. Still `match` directly when you branch
on the shape instead of threading the payload onward (see "Match the producing
call directly").

## Transform collections with combinators, not index loops

Work on elements directly with the prelude's combinators. They pass you the
element, so there is no index arithmetic and no per-element unwrapping. Multi-
argument steps are written `\ (a, b) -> …`.

```sprout
# Pure transforms:
let louder = vec_map(\n -> n + 1, nums)
let big    = vec_filter(\n -> n > 2, nums)
let total  = vec_fold(\ (acc, n) -> acc + n, 0, nums)

# Effectful traversal (bridge a Vec through vec_to_list):
list_each(\name -> print(name), names)
list_each(\name -> print(name), vec_to_list(items))
```

When the per-element action needs more than one expression, name a helper and pass
a one-expression lambda:

```sprout
fn greet(name: String) -> Unit !{IO} = do
  print("welcome,")
  print(name)

list_each(\n -> greet(n), names)
```

Reach for `range_each` only when you genuinely need the index — a numeric loop, or
writing into a `MutVec` by position:

```sprout
range_each(\i -> print(to_string(i * i)), range(1, n))
```

Indexed writes are for when you know the size. When you *don't* — the count is
discovered as you go — start empty and push, rather than guessing a capacity and
inventing an overflow policy at the call site:

```sprout
import stdlib.mutable as mut
...
do
  log <- mut.mutvec_empty()
  list_each(\body -> mut.mutvec_push(log, body), bodies)   # grows as needed
```

## Build a list with a comprehension

A comprehension is for *building a list*. It reads in the order the data flows —
source, filter, shape — and it elaborates to the same fold you would have written
by hand, so there is nothing to trade off on speed or stack depth.

With one source and one transform, either form is fine; pick whichever the
surrounding code already reads like. The comprehension pulls ahead as soon as
there is more than one thing going on:

```sprout
# Filter and transform: said once, in one place.
[n * 2 for n in ns if n > 0]
list_map(\n -> n * 2, list_filter(\n -> n > 0, ns))     # same thing, read inside-out

# Destructuring: the binder names the parts, so the body has no accessors.
[name for (name, age) in people if age > 18]

# Two sources: the clear win — no nested map, no concat.
[(x, y) for x in xs, y in ys]

# A range source needs no conversion.
[i * i for i in 1..n]
```

Three places to *not* reach for one:

- **You are chaining.** A comprehension is not a pipe stage, so it breaks a `|>`
  chain in half. If the value is already flowing through `|>`, keep using
  combinators.
- **You want effects, not a list.** A comprehension builds a value; running an
  action per element is `list_each`. Building a list you then throw away is the
  tell.
- **The pattern can fail.** A generator pattern must be irrefutable, and this is
  deliberate: skipping non-matching elements is a *decision*, so Sprout makes you
  write it. Use `list_filter_map` and say which elements survive.

```sprout
[x for Just x in maybes]                              # rejected: `Just x` can fail
list_filter_map(\m -> m, maybes)                      # the idiom — drops Nothing
```

## Chain transforms with `|>`

The prelude is data-last — the collection is always the final argument — so a
linear sequence of transforms reads top-to-bottom with `|>`. `x |> f` is `f(x)`,
and a partially-applied data-last function threads the value into its last slot:

```sprout
let total =
  nums
  |> vec_filter(\n -> n > 1)
  |> vec_sum

let clean = raw |> vec_reverse |> vec_to_list
```

Use `|>` for linear chains of pure transforms; a plain call reads better for a
single step, and a `let` reads better when an intermediate name aids understanding.

## Match the producing call directly

To handle every case of a `Maybe`/`Result`, match the call in place — no
intermediate binding:

```sprout
match find(key) with
| Just v  -> use(v)
| Nothing -> fallback()
```

## Pick the accessor that matches what you know

A `<-` bind on a `Maybe`/`Result` **propagates the failure out of the enclosing
function**, so it is only legal where that function returns the same shape
(spec §5.9). In a function that returns a plain value, say what you actually mean:

```sprout
# You know the index is in range (fixed layout, freshly sized buffer, loop bound).
x <- mutvec_at(v, i)                 # no Maybe box; fails loudly if you were wrong

# You have a sensible fallback.
let x = vec_get_or(i, 0.0, v)
let c = mutmatrix_at_or(m, r, col, 0.0)

# You want to handle both cases here.
match mutvec_get(v, i) with
| Just x  -> use(x)
| Nothing -> recover()

# You want the failure to propagate — only in a Maybe/Result-returning function.
x <- mutvec_get(v, i)
```

Reaching for `mutvec_get` and binding it was the common shape, and in a
`-> Double` numeric kernel it was silently wrong: on `Nothing` the function
returned the `Nothing` box read as a `Double`. `mutvec_at` is also faster — it
allocates no `Maybe` per read, which dominates in a hot loop.

To run a fallible call for its effect and **continue** regardless, use it as a bare
statement; `_ <- e` does *not* mean that, it still propagates:

```sprout
import stdlib.fs as fs
...
do
  fs.write_text(path, contents)   # run it, discard the Result, continue
  _ <- fs.write_text(path, more)  # propagates on Err — needs a Result-returning fn
```

## Match lists by shape with `[…]` patterns

`List` is an ordinary ADT (`Nil` / `Cons head tail`), but match it with list
literals rather than raw constructors — the literal shows the length at a glance.
The parser desugars `[…]` to the same `Cons`/`Nil` chains, so the sugar is pure
readability.

```sprout
match xs with
| []         -> 0               # empty
| [x]        -> x               # exactly one element
| [a, b]     -> a * 10 + b      # exactly two
| [a, b | _] -> a + b           # two or more; a tail after `|` matches the rest
```

The `|` separates fixed leading positions from the rest: everything to its left
matches elements by position, and a *name* to its right binds the remainder of
the list (which may be `Nil`). So `[a, b | rest]` matches any list of length ≥ 2,
with `rest` bound to what follows the first two, and `[x | rest]` is the plain
head-and-tail split — length ≥ 1, `rest` bound to everything after the first
element. Without `|`, the pattern is exact-length: `[a, b]` matches two-element
lists only.

## Collapse a trivial `do` block

A `do` block earns its keep only when it *sequences* — two or more effectful steps,
or a bind whose value feeds a later expression. When the whole block is a single
bind that is immediately returned, the block *is* the call:

```sprout
# Ceremony:
fn body(ch: Chan Int) -> Int !{IO} =
  do
    v <- chan_recv(ch)
    v

# Idiomatic — the function is the call:
fn body(ch: Chan Int) -> Int !{IO} = chan_recv(ch)
```

`chan_recv` returns a bare `Int !{IO}` — the `!{IO}` is an *effect*, not a value
wrapper to peel off — so binding and returning it unchanged adds nothing. (This
reduction is exact when the returned value is a bare effectful type; when it is a
`Maybe`/`Result`, check the intended short-circuit before collapsing.)

## Build strings with `++` and backtick templates

Append with `++`; interpolate with backtick templates, which evaluate real
expressions inside `${…}`. (Double-quoted strings are plain text.)

```sprout
let greeting = "hello, " ++ name ++ "!"
let msg = `processed ${done} of ${total}`
```

**Both are idiomatic — choose on readability.** Embedding a value is not on its
own a reason to prefer `${…}`: `"total: " ++ int_to_string(total)` is good style,
not a form awaiting modernisation.

A template is **not** an allocation win — it costs three more allocations than an
equivalent `++` chain, at every size, because it builds a `List String` of the
parts first. What it buys back is copying, which only pays off once the result is
large:

- **result under ~1 KB** (diagnostics, labels, keys) — `++` is faster, up to 2×
- **result over ~3 KB** — the template is faster, 1.4× rising to ~9× at 13 KB
- **accumulating in a loop** — use neither; both are O(n²) per iteration.
  Collect into a `List String` and call `string.join` / `string_concat_many` once.

Numbers, method, and the reasoning: **[string-building-v0.md](./string-building-v0.md)**
(re-runnable via `just bench-string-concat`).

Backtick templates may span multiple physical lines — the newlines are literal
content. This is the idiomatic way to embed a multi-line block (a shader, a
query, an HTML fragment) instead of a `"…\n" ++ "…\n"` chain:

```sprout
let vertex_shader =
  `#version 330
in vec3 pos;
void main() { gl_Position = vec4(pos, 1.0); }
`
```

## Make illegal states unrepresentable

Encode invariants in types rather than runtime checks or boolean flags, and return
`Maybe`/`Result` for failure instead of panicking — every stdlib function is total.

```sprout
type Visibility = Public | Private              # not is_public: Bool
fn vec_get(index: Int, vec: Vec a) -> Maybe a   # not "trust me it's in range"
```

## Distinguish same-typed values with `wrap`

When two values share a representation (`String`, `Int`, …) but mean different
things, `wrap` each so the type checker keeps them apart. It is zero-cost.

```sprout
wrap FilePath   = String
wrap StdlibRoot = String
fn compile(path: FilePath, root: StdlibRoot) -> ...   # the arguments can't be swapped
```

## Hide a type behind an interface with existentials

When a collection must hold values of *different* types touched only through a
shared interface — a heterogeneous "render these" list, a registry of handlers, an
interface value that closes over private state — box them with an existential.
`(any C)` hides one value that has a `C` instance; the explicit `exists … where`
prefix generalizes it to a hidden type that spans several fields or carries several
constraints. Unpacking dispatches through the witness packed at construction, so
each element behaves as its own concrete type would.

```sprout
type Cell   = | Cell (any ToString)                # ≡ | exists a. Cell a where ToString a
let row     = [Cell(42), Cell("hi"), Cell(true)]   # one flat list, mixed element types
type View s = (state: s, step: s -> s, render: s -> String)  # a parametric record names the parts
type Widget = | exists s. Widget (View s)                    # `exists s` hides the state type
```

Reach for this only when the types genuinely differ, cannot be restructured into
per-type columns (struct-of-arrays), and the set is open to new instances. See
`examples/existential_render.sprout` and `examples/existential_widget.sprout`.
(Experimental — spec §5.6, design in `docs/gadts-v0.md`.)

## Keep effects at the edges

Leave the functional core pure; carry `!{IO}` only where a function actually does
IO, and push that to the drivers at the edge of the call graph. A pure transform
returns a value; its already-in-IO caller does the printing.

## Let the compiler enforce "release it exactly once"

A resource with an explicit release — a socket, a file handle — is
**acquire → use N times → release once**. Declare the handle `type linear`, take
it `borrowing` in the operations that only read or write it, and `consuming` in
the one that releases it. Then forgetting the release is a compile error rather
than a leak found in production.

```sprout
# stdlib.net, the shipped example:
export fn write_all(conn: borrowing TcpConnection, payload: Bytes) -> … !{IO} = …
export fn close(conn: consuming TcpConnection) -> Unit !{IO} =
  match conn with
  | TcpConnection handle -> tcp_close(handle)
```

A session then reads like ordinary code — no block, no reference type:

```sprout
fn fetch(host: String, port: Int) -> Unit !{IO} =
  match connect(host, port) with
  | Ok conn ->
      do
        send_or_fail(conn, request)   # borrows
        report(conn, "response")      # borrows again
        close(conn)                   # the single consuming use
  | Err _ -> term_write("connect failed\n")
```

Two things to know when writing the operations themselves. A **`consuming`
function must destructure the value** — calling a `borrowing` accessor on it only
borrows, leaving nothing consumed, and the compiler will say so. And an argument
in a `borrowing` position must be a **variable**: `report(connect(…), …)` is
rejected, because that connection would never be released.

Reach for this when a type has a release operation. For plain data with no
release, `linear` only gets in the way — see
[spec-v0.md §5.8](./spec-v0.md).

---

For the reasoning behind totality, `wrap`, effects, and data-last, see
[guidelines.md](./guidelines.md).
