# Sprout Tree-sitter Support Brief

This document captures the minimum work needed for `codebase-memory-mcp` to
index Sprout source reliably.

It is an implementation brief, not a normative language spec.

> **Status (2026-08-18): partially delivered.** Deliverables 1–4 below exist in
> `tree-sitter-sprout/` — `grammar.js` (all eight top-level declaration forms, plus
> common expression and pattern forms), the layout external scanner (`src/scanner.c`),
> the generated `src/parser.c` / `src/grammar.json` / `src/node-types.json`, both query
> files, and a small corpus. The grammar took approach **1** below (external scanner)
> and is deliberately conservative. Deliverables 5–6 — indexer tests and the CBM
> `.sprout` extension entry — are still open and live outside this repo.

## Why This Is Needed

`codebase-memory-mcp` supports a fixed set of tree-sitter grammars. Sprout was
not one of them, so `.sprout` files were skipped by the graph indexer.

For Sprout support, CBM needs a real parser for Sprout syntax, not just an
extension mapping.

## What The Grammar Must Cover First

The first grammar version should prioritize declaration discovery over perfect
full-surface fidelity.

Required top-level forms:

1. `module`
2. `import`
3. `export`
4. `type`
5. `class`
6. `instance`
7. `fn`
8. `let`

Required declaration structure:

1. function names and parameters
2. type names, type parameters, and constructors
3. record declarations and record fields
4. class and instance constraints
5. export markers for values, types, and constructor groups

Required expression forms for indexing and symbol recovery:

1. identifiers and qualified names
2. literals: int, string, char, bool, unit
3. calls and lambdas
4. `if`
5. `match`
6. `do`
7. tuples, lists, records, and field access
8. binary and unary operators used by the current parser

## Sprout-Specific Parser Constraints

Sprout is not a brace-delimited language.
The current hosted parser uses layout-sensitive handling for some constructs,
especially:

1. `do` blocks
2. `match` branches
3. local `where` bindings

That means a Sprout tree-sitter grammar will likely need one of these
approaches:

1. an external scanner that tracks indentation and layout tokens,
2. a deliberately weaker initial grammar that parses declaration headers and
   treats bodies conservatively,
3. a two-stage indexer path where structural discovery is separated from full
   expression parsing.

## Recommended First Milestone

Build a grammar that is good enough to answer these CBM queries:

1. which files define functions, types, classes, and instances
2. what names are exported
3. which declarations are top-level entry points
4. what modules and imports exist

That first milestone does not need perfect type inference or full expression
validation. It does need stable source spans for declaration names.

## Suggested Deliverables

1. `tree-sitter-sprout/grammar.js`
2. `tree-sitter-sprout/src/parser.c` or generated parser artifacts
3. `tree-sitter-sprout/src/node-types.json`
4. a small corpus of real Sprout files from `stdlib/` and `examples/`
5. indexer tests that confirm `.sprout` files produce graph nodes
6. a CBM extension entry for `.sprout`

## Compatibility Notes

Sprout already has a self-hosted parser and a rich AST in
`stdlib/compiler/parser.sprout` and `stdlib/compiler/ast.sprout`. That is useful for
validation, but CBM cannot consume it directly.

So the practical path is:

1. use the current parser as the reference implementation,
2. define a tree-sitter grammar that matches its surface syntax,
3. feed that grammar into CBM,
4. verify the indexed nodes against the current parser's declaration inventory.
