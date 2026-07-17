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
The right-hand sides are pure, and every `else` and the body unify to one result
type.

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

## Keep effects at the edges

Leave the functional core pure; carry `!{IO}` only where a function actually does
IO, and push that to the drivers at the edge of the call graph. A pure transform
returns a value; its already-in-IO caller does the printing.

---

For the reasoning behind totality, `wrap`, effects, and data-last, see
[guidelines.md](./guidelines.md).
