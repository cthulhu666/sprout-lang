# Sprout

Sprout is an experimental, statically typed, functional-first programming language focused on being "Haskell for ordinary people": strong type safety, predictable semantics, and approachable syntax.

## Status

Early bootstrap stage. The repository currently contains design docs and initial scaffolding.

## Docs

- [Specification v0 (Normative)](./docs/spec-v0.md)
- [Language Design v0](./docs/language-design-v0.md)
- [Language Design Best Practices (Research Notes)](./docs/language-design-best-practices.md)
- [HM Typechecker Guide (Human-Friendly)](./docs/hm-typechecker.md)

## Repository Layout

- `docs/` design and process documents
- `examples/` sample source files
- `sprout/` implementation (`tokenizer`, `parser`, `typechecker`, `cli`)
- `tests/` parser and typechecker tests
- `tests/conformance/` executable language behavior fixtures (`run`, `parse_error`, `type_error`, `runtime_error`)
- `stdlib/` language-level standard library source (`prelude.sprout`)
- `mise.toml` pinned local toolchain (`python`, `just`)
- `justfile` common project commands

## Tooling (mise + just)

This repo uses [`mise`](https://mise.jdx.dev/) to pin tools and [`just`](https://github.com/casey/just) as task runner.

1. Install tools from `mise.toml`:
   `mise install`
2. Run commands through mise:
   `mise exec -- just test`

Common tasks:

- Parse file: `mise exec -- just parse examples/fizzbuzz.sprout`
- Typecheck file: `mise exec -- just check examples/fizzbuzz.sprout`
- Run file: `mise exec -- just run examples/fizzbuzz.sprout`
- Run tests: `mise exec -- just test`
- Emit LLVM IR: `mise exec -- just compile examples/factorial.sprout /tmp/factorial.ll`
- Build native binary (clang): `mise exec -- just compile-native /tmp/prog.spr /tmp/prog`

## Builtin Helpers (v0)

Runtime builtins (host-implemented):

- `print(x) -> IO Unit`
- `print_int(x: Int) -> Int` (prints and returns `x`, useful for native backend subset)
- `read_lines(path: String) -> List String`
- `parse_int(s: String) -> Int`
- `split_words(s: String) -> List String` (comma/whitespace separated)

Standard library (Sprout source in `stdlib/prelude.sprout`):

- `map(list, fn) -> List`
- `fold(list, init, fn) -> value`
- `filter(list, predicate) -> List`
- `split_ints(s: String) -> List Int`

Load stdlib prelude explicitly:

- `python3 -m sprout.cli check --with-stdlib your_file.spr`
- `python3 -m sprout.cli run --with-stdlib your_file.spr`

## Native Backend (Early)

`sprout compile` currently supports a small subset:

- top-level `fn` declarations only (no top-level `let`/`type` yet),
- `Int`/`Bool` typed params and returns,
- expressions: literals, vars, arithmetic, comparisons, `if`, direct function calls, recursion.

Commands:

- LLVM IR output: `python3 -m sprout.cli compile input.spr -o out.ll`
- Native binary (requires `clang`): `python3 -m sprout.cli compile input.spr --native -o out_bin`

## Near-Term Plan

1. Lock v0 syntax and type-system scope.
2. Build parser and typechecker around the v0 spec.
3. Add golden tests for parsing, typing, and evaluation behavior.

## Contributing

See [AGENTS.md](./AGENTS.md) for project-specific collaboration and change rules.
