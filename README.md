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

fn check(name: String) -> Result String String =
  if string.length(name) > 0 then Ok(name) else Err("empty name")

fn greet(r: Result String String) -> String =
  match r with
  | Ok name -> string.concat("Hello, ", name)
  | Err e   -> string.concat("skipped: ", e)

fn main() -> Unit !{IO} =
  match env_get("USER") with
  | Just raw -> raw |> string.trim |> check |> greet |> print
  | Nothing  -> print("no USER set")
```

Running with `USER="  Ada  "` prints `Hello, Ada`. More in [`examples/`](./examples/).

## Highlights

- **Full type inference** — Hindley–Milner; most code needs no annotations.
- **Functional core** — algebraic data types, exhaustive pattern matching,
  immutable values, `Maybe`/`Result` for optionality and failure.
- **Typeclasses** — dictionary-passing (`Eq`, `Ord`, `ToString`, `Semigroup`,
  `Monoid`, …), including return-type dispatch.
- **Effects in types** — pure functions are unannotated; effectful ones carry
  `!{IO}` (with singleton effect variables `!{e}` for higher-order helpers).
  Open effect rows are not supported yet.
- **Zero-cost `wrap` newtypes**, first-class **tuples**, and function-local
  `where` blocks (normative v0).
- **Self-hosted compiler** — parser, typechecker, and codegen written in Sprout;
  native binaries via LLVM IR + clang; a small C runtime with a mark-sweep GC.
- **Beginner-friendly diagnostics** — `line:col` errors, exhaustiveness checks,
  unknown-name and missing-instance reporting.

**Experimental slices** (implemented, not yet normative v0): the module system,
typeclasses, records (`type User = { name: String }`), integer ranges (`a..b`),
`Char`/Unicode text, `stdlib.regex`, `do` notation for `Maybe`/`Result`/`IO`, and
`#@unstable`/`#@wip`-style declaration annotations. See the design drafts below.

## Documentation

- **[Language spec (v0, normative)](./docs/spec-v0.md)** — the stable core.
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
mise exec -- just build-stage1                                 # build the self-hosted compiler
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

The following are planned features not yet implemented. Each has a standard workaround.

**Boolean negation (`!expr`)**
`!` is used only for effect annotations (`!{IO}`). Negate by restructuring:
```sprout
# instead of: !is_valid(x)
if is_valid(x) then false else true
```

**Effectful list iteration (`list_each`, `list_for_each`)**
No built-in IO-effectful list traversal exists yet. Write a tail-recursive helper:
```sprout
fn print_all(items: List String) -> Unit !{IO} =
  match items with
  | Nil -> ()
  | Cons h t -> do print(h) ; print_all(t)
```

## Backlog

See [BACKLOG.md](./BACKLOG.md) for the current roadmap and follow-up work (the
"Design Roadmap" section holds the forward-looking priorities and V1 candidates).

## Contributing

See [AGENTS.md](./AGENTS.md) for project collaboration and change rules.

## License

Apache License 2.0 — see [LICENSE](./LICENSE) and [NOTICE](./NOTICE).
