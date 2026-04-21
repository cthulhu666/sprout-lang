# Parser Library Direction

This document records the intended direction for a future self-hosted parser
library.

It is a design note, not a normative language spec.

## Goal

Provide enough reusable parsing infrastructure to support a Sprout-implemented
lexer/parser for the compiler pipeline without overcommitting early to a large
general-purpose parser-combinator framework.

## Near-Term Recommendation

Build the first parser support as compiler-oriented tooling modules rather than
as a broad public stdlib parsing package.

Initial bias:

1. start with `stdlib.string` scanning helpers and compiler-local source cursor
   utilities,
2. add a narrow parser abstraction with explicit parser state and
   location-aware `Result` failures,
3. add only the combinators needed by the bootstrap lexer/parser,
4. keep the first implementation easy to debug against the existing hosted
   parser.

## Why Not Start With A Full Megaparsec-Style Library

A full Megaparsec-style library is attractive, but it is the wrong first move
for Sprout right now.

Reasons:

1. the project does not yet know the final internal compiler artifact boundary,
2. the first self-hosted parser needs predictable control flow and explicit
   state more than rich abstraction,
3. a large combinator surface would add API/design commitment before the
   compiler slice proves what is actually needed,
4. parser performance and diagnostics should be measured on a concrete lexer
   and parser before generalizing heavily.

## Recommended Shape

The first parser library should look more like:

1. `SourceCursor` or equivalent explicit source-position state,
2. token and diagnostic ADTs,
3. `Parser a = State -> Result ParseError (a, State)` in spirit, even if the
   concrete encoding differs,
4. a small set of helpers such as `map`, `and_then`, `or_else`, `optional`,
   `many`, `peek`, `expect`, and token/character matching helpers,
5. error values that always retain line and column context.

The first version should remain compiler-facing and experimental.

## Placement

Prefer a compiler/tooling namespace first, not general stdlib placement.

Examples:

1. `stdlib.compiler.source`
2. `stdlib.compiler.token`
3. `stdlib.compiler.lexer`
4. `stdlib.compiler.parser_core`

Once the abstraction is proven useful outside compiler work, a narrower,
stable subset can be promoted into a more general parsing library.

## Immediate Next Steps

1. land the foundational string/character classification helpers,
2. add a source-cursor module,
3. define token and parse-diagnostic types,
4. build the bootstrap lexer against those utilities,
5. only then decide whether broader combinators should be promoted or kept
   compiler-local.

## CBM Support Note

If Sprout is to be indexed by `codebase-memory-mcp`, a separate tree-sitter
grammar or equivalent parser bridge is required. See
[`docs/tree-sitter-sprout-support.md`](/Users/cthulhu/Dev/lang/sprout_lang/docs/tree-sitter-sprout-support.md)
for the minimum surface area that grammar needs to cover.
