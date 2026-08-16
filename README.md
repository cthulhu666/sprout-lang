# Sprout

A statically typed, functional-first programming language aimed at strong safety
with beginner-friendly ergonomics. The compiler is **self-hosted** — written in
Sprout, it compiles itself and emits native binaries via LLVM IR + clang.

> **Status: v0 — experimental.** The self-hosting milestone is reached, but
> syntax and semantics of non-core features may still change.
> [`docs/spec-v0.md`](./docs/spec-v0.md) is the normative core; everything else is
> an implementation feature or experimental extension.

## A taste

Read a value, clean and validate it, then respond — with `|>` pipelines and the
two safety idioms `Maybe` (might be absent) and `Result` (might be invalid). No
exceptions, no null:

```sprout
module main
import stdlib.string
import stdlib.env as env

fn check(name: String) -> Result String String =
  if string.length(name) > 0 then Ok(name) else Err("empty name")

fn greet(r: Result String String) -> String =
  match r with
  | Ok name -> string.concat("Hello, ", name)
  | Err e   -> string.concat("skipped: ", e)

fn main() -> Unit !{IO} =
  match env.get("USER") with
  | Just raw -> raw |> string.trim |> check |> greet |> print
  | Nothing  -> print("no USER set")
```

Running with `USER="  Ada  "` prints `Hello, Ada`. More in [`examples/`](./examples/).

## Highlights

- **Full type inference** — Hindley–Milner; most code needs no annotations.
- **Functional core** — algebraic data types, exhaustive pattern matching,
  immutable values, `Int`/`Double` numerics, `Maybe`/`Result` for optionality
  and failure.
- **Typeclasses** — dictionary-passing (`Eq`, `Ord`, `ToString`, `Semigroup`,
  `Monoid`, …), including return-type dispatch.
- **Effects in types, and they are checked** — pure functions are unannotated;
  effectful ones carry `!{IO}` (with singleton effect variables `!{e}` for
  higher-order helpers). A body that performs IO under a pure signature is a
  compile error, so a missing `!{IO}` means the compiler verified the function
  does none. Over-declaring is fine — the rule is *inferred ⊑ declared*, not
  equality. Open effect rows are not supported yet. Aborting is *not* an effect:
  `panic` is pure, so an unreachable-by-invariant arm does not make its function
  effectful ([why](docs/effect-enforcement-v0.md#6-is-panic-an-effect-decided-no)).
- **Zero-cost `wrap` newtypes**, first-class **tuples**, and function-local
  `where` blocks (normative v0).
- **Self-hosted compiler** — parser, typechecker, and codegen written in Sprout;
  native binaries via LLVM IR + clang; a small C runtime with a mark-sweep GC.
- **Beginner-friendly diagnostics** — `line:col` errors, exhaustiveness checks,
  unknown-name and missing-instance reporting.

**Experimental slices** (implemented, not yet normative v0): the module system,
typeclasses, records (`type User = (name: String)` — parametric, with `p.x` access
and `p with (name = …)` update), integer ranges (`a..b`),
`Char`/Unicode text, `stdlib.regex`, `do` notation for `Maybe`/`Result`/`IO`,
existential constructors (`| exists a. Cell a where ToString a`, and its `(any C)`
sugar — trait objects / heterogeneous collections), and
`#@unstable`/`#@wip`-style declaration annotations. See the design drafts below.

## Documentation

- **[Language spec (v0, normative)](./docs/spec-v0.md)** — the stable core.
- **[Idiomatic Sprout](./docs/idiomatic-sprout.md)** — how to write clean, flat, idiomatic code.
- [Language design](./docs/language-design-v0.md) · [design best practices](./docs/language-design-best-practices.md)
- [Style guide](./docs/style-guide-v0.md) · [stdlib/compiler guidelines](./docs/guidelines.md)
- [HM typechecker guide](./docs/hm-typechecker.md)
- [Builtins reference](./docs/builtins-reference.md) — host builtins + collections.
- [Toolchain, build & implementation status](./docs/development.md)
- [Debugging](./docs/debugging.md) · [Bootstrap chain](./docs/bootstrap-chain.md)
- [Backlog & roadmap](./BACKLOG.md)
- Design drafts: [effect system](./docs/effect-system-v0-plan.md) ([v1](./docs/effect-system-v1-draft.md)) · [int ranges](./docs/int-ranges-v1-draft.md) · [char & text](./docs/char-text-v1-draft.md) · [sequencing sugar](./docs/sequencing-sugar-v1-draft.md)

## Quick start

Requires [`mise`](https://mise.jdx.dev) (pins the toolchain) and `clang`.

```
mise install
mise exec -- just bootstrap-from-seed                          # build the self-hosted compiler (stage-1)
mise exec -- just compile-native examples/fizzbuzz.sprout /tmp/fizzbuzz
/tmp/fizzbuzz
```

Full toolchain, tasks, commands, and the native-backend subset live in
[docs/development.md](./docs/development.md). Run the test suite with
`mise exec -- just test`.

## Repository layout

- `docs/` — design and process documents
- `examples/` — sample source files
- `stdlib/` — standard library (`prelude.sprout`) plus protocol helpers
- `stdlib/compiler/` — self-hosted compiler (`parser`, `typechecker`, `codegen`, `compile_driver`)
- `runtime/` — C runtime and GC (`sprout_runtime.c`)
- `tests/` — test suites (native Sprout tests under `tests/stdlib/`)
- `tests/conformance/` — executable behavior fixtures (`run`, `parse_error`, `type_error`, `runtime_error`)
- `bootstrap/` — committed LLVM IR seed for stage-1 bootstrap
- `mise.toml` / `justfile` — pinned toolchain and common commands

Two v0 rules to know: top-level `let` bindings must be pure, and effectful work
lives in functions, with `main` as the entrypoint.

## Not Yet Supported (Common Gotchas)

A few naming and syntax gotchas, each with the idiomatic form to use instead.

**Boolean negation is `!expr`, not `not`**
Use the prefix `!` operator to negate a `Bool`; prefix `-` negates a number.
The *word* `not` is not an operator and is a parse error.
```sprout
if !is_valid(x) then handle_error() else proceed()   # ! negates a Bool
let below = -temperature                              # - negates a number
# `not is_valid(x)` does NOT parse — use `!is_valid(x)`
```

**An instance head takes type variables, not concrete arguments**
`instance C (List Int)` is rejected; the head must be a type constructor applied to
*distinct* type variables (spec §8.5, following Haskell 2010 §4.3.2). Instance
selection keys on the head constructor alone, so a `List Int` instance would also
answer for `List String` — and run its `Int` code on it. To get an instance at one
specific argument type, give that instantiation a type of its own.
```sprout
instance Summable (Vec Int) { … }        # rejected: `Int` is a concrete type
instance Summable (Vec a) { … }          # ok — a distinct type variable

type IntVec =                            # the idiom for a specific element type
  | MkIntVec (Vec Int)
instance Summable IntVec { … }           # ok — `IntVec` is its own head
```

**No multi-module user programs outside `stdlib/`**
The module loader (`module_name_to_path`) resolves only `stdlib.<name>` imports and
single-segment dotless names. Any *other* dotted import (e.g. `import myapp.util`)
silently resolves to nothing — there is no error at import time; the symbols simply
never bind, surfacing later as an `Unknown variable` at the use site. Keep a user
program in a single file (or contribute the shared code under `stdlib/`).

## Partial Application with `_`

Leave arguments as holes with `_`, and a call becomes a function of the holes —
in **any** position, not just left-to-right:

```sprout
add(_, 1)              # \x -> add(x, 1)   — increment
add(10, _)             # \x -> add(10, x)
map(add(_, 100), xs)   # add 100 to every element

# multiple holes -> a multi-argument function, filled left-to-right
add3(_, _, 3)          # \(a, b) -> add3(a, b, 3)
```

A `_` binds to the innermost enclosing call, so in `f(g(_), 3)` the hole belongs
to `g`. Operator sections (`_ * 2`) are not yet supported — write `\x -> x * 2`.

## Iteration Combinators

The prelude provides effect-polymorphic iteration combinators — use these instead
of hand-rolling counter recursion. Each takes an effect-polymorphic step (`!{e}`),
so the same function serves both pure and `!{IO}` code, and all follow the
data-last argument convention (the collection is the final argument).

```sprout
range_each(f: Int -> Unit !{e}, r: IntRange) -> Unit !{e}          # imperative loop
range_fold(step: b -> Int -> b !{e}, init: b, r: IntRange) -> b !{e}
list_each (f: a -> Unit !{e}, xs: List a)  -> Unit !{e}            # traverse for effect
list_fold (step: b -> a -> b !{e}, init: b, xs: List a) -> b !{e}
```

`IntRange` is inclusive of both ends, so a half-open `[0, n)` loop is `range(0, n - 1)`
**only when `n >= 1`**. Beware `n == 0`: `range(0, -1)` iterates *descending* over
`[0, -1]` (start > end flips the step to −1), running the body on `0` and `-1` instead
of zero times. Guard a possibly-empty loop: `if n == 0 then () else range_each(f, range(0, n - 1))`.
(The hand-rolled `if i >= n` loop handled `n == 0` for free; a half-open range helper is
tracked in BACKLOG.)

Because inline multi-statement `do`-lambdas do not yet parse, lift a multi-line step
body into a named helper and pass it via a single-expression lambda:

```sprout
fn print_line(items: Vec String, i: Int) -> Unit !{IO} =
  match vec_get(i, items) with | Just s -> print(s) | Nothing -> ()

range_each(\i -> print_line(items, i), range(0, vec_length(items) - 1))
```

## Backlog

See [BACKLOG.md](./BACKLOG.md) for the current roadmap and follow-up work (the
"Design Roadmap" section holds the forward-looking priorities and V1 candidates).

## Contributing

See [AGENTS.md](./AGENTS.md) for project collaboration and change rules.

## License

Apache License 2.0 — see [LICENSE](./LICENSE) and [NOTICE](./NOTICE).
