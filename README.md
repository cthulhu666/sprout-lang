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
  plus protocol helpers such as `http.sprout`
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
- `str_concat(a: String, b: String) -> String`
- `str_len(s: String) -> Int`
- `str_slice(s: String, start: Int, len: Int) -> String`
- `str_find(s: String, needle: String) -> Int` (`-1` when not found)
- `str_starts_with(s: String, prefix: String) -> Bool`
- `tcp_listen(port: Int) -> Int`
- `tcp_accept(listener: Int) -> Int`
- `tcp_read(conn: Int) -> String`
- `tcp_write(conn: Int, payload: String) -> IO Unit`
- `tcp_close(conn: Int) -> IO Unit`
- `tcp_close_listener(listener: Int) -> IO Unit`
- `tcp_echo_serve(port: Int, max_connections: Int) -> IO Unit`

String helpers currently use `str_*` global names; they are intended to move to a future module/namespace surface.

Standard library (Sprout source in `stdlib/prelude.sprout`):

- `map(list, fn) -> List`
- `fold(list, init, fn) -> value`
- `filter(list, predicate) -> List`
- `split_ints(s: String) -> List Int`

HTTP stdlib helpers (in `stdlib/http.sprout`):

- `parse_request_line(raw) -> Maybe RequestLine`
- `http_response(status, body) -> String`
- `http_ok(body) -> String`
- `http_bad_request() -> String`
- `http_echo_response(raw_request) -> String`

Load stdlib prelude explicitly:

- `python3 -m sprout.cli check --with-stdlib your_file.spr`
- `python3 -m sprout.cli run --with-stdlib your_file.spr`

Load HTTP helpers:

- `python3 -m sprout.cli check --with-http-stdlib your_file.spr`
- `python3 -m sprout.cli run --with-http-stdlib your_file.spr`
- Example HTTP echo server:
  `SPROUT_NET_MODEL=reactor python3 -m sprout.cli run --with-http-stdlib examples/http_echo_server.sprout`

## Native Backend (Early)

`sprout compile` currently supports a small subset:

- top-level `fn`, `type`, and top-level `let` (constant and runtime-initialized),
- `Int`/`Bool`/`String` typed params and returns, plus `IO Unit` function return,
- expressions: literals, vars, arithmetic, comparisons, `&&`, `||`, function composition (`f >> g` means `f(g(x))`), `if`, direct function calls, recursion,
- ADT constructor calls and `match` lowering (constructors with up to 2 fields),
- generic type variables are currently erased to runtime `i64` handles,
- first-order function values in params are supported (for patterns like `f: Int -> Int`),
- `print(...)` lowering for `Int`/`Bool`/`String`/ADT values,
- `print_int(...)` external call.

Networking builtins are available in interpreter and native (`sprout compile --native`) modes.

Interpreter runtime has a swappable server model selected by `SPROUT_NET_MODEL`:

- `reactor` (default): event loop / readiness-based echo server
- `blocking`: simple blocking accept/read/write loop

## Modules (v0)

Sprout now supports file-based modules with top-of-file headers:

- `module a.b.c`
- `import x.y.z`
- `import x.y.z as alias`
- `import x.y.z (name1, name2)`

Resolution:

- `import stdlib.http` resolves to `stdlib/http.sprout`
- loader checks importing file directory first, then current working directory
- import cycles are rejected

Current limitation:

- imported declarations are loaded into a shared global scope (no namespace qualification yet)
  except alias access via generated qualified names (`alias.symbol`)

Commands:

- LLVM IR output: `python3 -m sprout.cli compile input.spr -o out.ll`
- Native binary (requires `clang`): `python3 -m sprout.cli compile input.spr --native -o out_bin`

## Near-Term Plan

1. Lock v0 syntax and type-system scope.
2. Build parser and typechecker around the v0 spec.
3. Add golden tests for parsing, typing, and evaluation behavior.

## Contributing

See [AGENTS.md](./AGENTS.md) for project-specific collaboration and change rules.
