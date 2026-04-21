# tree-sitter-sprout

This directory is a scaffold for a future Sprout tree-sitter grammar.

The goal is to give `codebase-memory-mcp` a parser it can use to index `.sprout`
files for declarations, imports, exports, and symbol spans.

Current status:

1. grammar shape is sketched in `grammar.js`
2. layout-sensitive tokens are represented as external scanner hooks
3. the scanner implementation is not finished yet

Sprout is indentation-sensitive in places, so a production grammar will need an
external scanner or an equivalent layout-token strategy.

The hosted Python parser in `sprout/parser.py` remains the reference for
syntax and precedence.
