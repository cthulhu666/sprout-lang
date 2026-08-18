# tree-sitter-sprout

This directory is a scaffold for a future Sprout tree-sitter grammar.

The goal is to give `codebase-memory-mcp` a parser it can use to index `.sprout`
files for declarations, imports, exports, and symbol spans.

Current status (2026-08-18):

1. `grammar.js` covers all eight top-level declaration forms — `module`, `import`,
   `export`, `type`, `class`, `instance`, `fn`, `let` — plus common expression and
   pattern forms. It is deliberately conservative; see its header comment.
2. layout-sensitive tokens are external scanner hooks (`newline`, `indent`, `dedent`)
3. `src/scanner.c` implements them, and generated artifacts are checked in:
   `src/parser.c`, `src/grammar.json`, `src/node-types.json`
4. `queries/highlights.scm` and `queries/tags.scm` exist; `test/corpus/basic.txt`
   is the only corpus so far

Still open: indexer tests that confirm `.sprout` files produce graph nodes, and the
`codebase-memory-mcp` extension entry — both live outside this repo.

Sprout is indentation-sensitive in places, so a production grammar will need an
external scanner or an equivalent layout-token strategy.

The self-hosted parser in `stdlib/compiler/parser.sprout` is the reference for
syntax and precedence. (An earlier Python parser was removed in May 2026.)
