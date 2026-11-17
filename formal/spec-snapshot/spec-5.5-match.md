### 5.5 Match expression

```sprout
match m with
| Just x -> x
| Nothing -> 0
```

Patterns are checked top-to-bottom; first match wins.

A `match` must be **exhaustive**: the branch patterns must cover every value of
the scrutinee type, or it is a compile error. Coverage is checked per field
position ("column"): a constructor is covered when, for each of its fields, the
union of the sub-patterns appearing at that position covers the field's type;
`Bool` requires both `true` and `false` (or a catch-all); `Unit` requires the
unit pattern; the unbounded scalar domains (`Int`, `String`, `Char`) are covered
only by a catch-all. Because coverage is checked one column at a time,
non-exhaustiveness that arises only from a *combination* of field values —
e.g. `(true, true) | (false, false)` on `(Bool, Bool)` — is not yet rejected in
v0 (a full usefulness matrix is future work); everything else is.

Unreachable top-level branches are a compile error when they are
shadowed by an earlier `_` or variable pattern, repeat an earlier top-level
literal pattern, repeat an earlier top-level constructor branch that already
covers that constructor completely, or appear after all constructors of an ADT
have already been covered. More advanced nested-pattern subsumption is not part
of the v0 contract.

Tuple expressions use comma-separated elements inside parentheses:

```sprout
(x, y, z)
```

`(x)` remains ordinary grouping, not a 1-tuple.

The unique value of `Unit` is written `()`:

```sprout
()
```

Tuple patterns are positional and arity-sensitive:

```sprout
match pair with
| (x, y) -> x
```

`()` is the unit pattern and matches only the `Unit` value.

Composition operators desugar to `rcompose` and `lcompose` from the prelude:

- `f >> g` desugars to `rcompose(f, g)`, meaning `\x -> g(f(x))` — apply `f` first, then `g`
- `f << g` desugars to `lcompose(f, g)`, meaning `\x -> f(g(x))` — apply `g` first, then `f`

They associate to the right, so `f >> g >> h` means `\x -> h(g(f(x)))`.
`rcompose` and `lcompose` are first-class functions and can be passed as values.
So is the prelude's `identity(x: a) -> a where linear a`, which returns its argument,
linear or not. `map(identity, xs)` is `xs`, `filter_map(identity, xs)` drops the
`Nothing`s and `traverse(identity, xs)` is `sequence(xs)`.

The pipe operator is a binary operator that threads a value into the final
argument position of the expression on its right:

- `value |> f` means `f(value)`
- `value |> g(a, b)` means `g(a, b, value)` — with no placeholder, the piped value
  is appended as the final argument (data-last convention).
- `value |> g(a, _)` means `g(a, value)` — a `_` placeholder in the piped call
  fills at the hole's position instead of the final position (§5.3), the explicit
  way to thread a value into a non-final argument. Append and hole-fill coincide
  only when the hole is last.

It associates to the left, so `x |> f(a) |> g(b)` means `g(b, f(a, x))`.

