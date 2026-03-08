# Sprout

Sprout is an experimental, statically typed, functional-first programming language focused on being "Haskell for ordinary people": strong type safety, predictable semantics, and approachable syntax.

## Status

Early bootstrap stage. The repository currently contains design docs and initial scaffolding.

## Docs

- [Specification v0 (Normative)](./docs/spec-v0.md)
- [Language Design v0](./docs/language-design-v0.md)
- [Language Design Best Practices (Research Notes)](./docs/language-design-best-practices.md)

## Repository Layout

- `docs/` design and process documents
- `examples/` sample source files
- `sprout/` implementation scaffold (lexer/parser/interpreter/CLI placeholders)

## Near-Term Plan

1. Lock v0 syntax and type-system scope.
2. Build parser and typechecker around the v0 spec.
3. Add golden tests for parsing, typing, and evaluation behavior.

## Contributing

See [AGENTS.md](./AGENTS.md) for project-specific collaboration and change rules.
