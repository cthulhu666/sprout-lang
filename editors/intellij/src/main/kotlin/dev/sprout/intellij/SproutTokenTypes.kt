package dev.sprout.intellij

import com.intellij.psi.tree.IElementType
import com.intellij.psi.tree.TokenSet

class SproutTokenType(debugName: String) : IElementType(debugName, SproutLanguage)

/**
 * Token types exist for COLOURING, so they are finer-grained than the compiler's tokens
 * in two places, both deliberate:
 *
 *  - The compiler lexes `gfx.draw_frame` as a single identifier (its `is_ident_continue`
 *    admits `.`). Here the qualifier, the dot and the member are separate tokens, because
 *    `tree-sitter-sprout/queries/highlights.scm` colours a qualified name's head
 *    differently from its tail and a single token could not express that.
 *  - String interpolation is broken into literal chunks, `${`/`}` markers, and the
 *    ordinary tokens between them, so code inside a template is coloured as code.
 *
 * Neither changes what anything *means* — the compiler remains the only authority on
 * that. They are presentation splits.
 */
object SproutTokenTypes {
  val COMMENT = SproutTokenType("COMMENT")

  val KEYWORD = SproutTokenType("KEYWORD")

  // `module`, `import`, `as`, `alias`, `linear`, `record` are NOT keywords — they are
  // absent from the compiler's `is_keyword` and are ordinary identifiers the parser
  // interprets by position. See SproutLexer for how they are recognised, and why the
  // recognition is approximate.
  val SOFT_KEYWORD = SproutTokenType("SOFT_KEYWORD")

  val BOOLEAN = SproutTokenType("BOOLEAN")

  val IDENTIFIER = SproutTokenType("IDENTIFIER")
  val TYPE_IDENTIFIER = SproutTokenType("TYPE_IDENTIFIER")
  val QUALIFIER = SproutTokenType("QUALIFIER")

  val NUMBER = SproutTokenType("NUMBER")
  val STRING = SproutTokenType("STRING")
  val STRING_ESCAPE = SproutTokenType("STRING_ESCAPE")
  val CHAR = SproutTokenType("CHAR")
  val INTERP_MARKER = SproutTokenType("INTERP_MARKER")

  val OPERATOR = SproutTokenType("OPERATOR")
  val DOT = SproutTokenType("DOT")
  val COMMA = SproutTokenType("COMMA")
  val PAREN = SproutTokenType("PAREN")
  val BRACE = SproutTokenType("BRACE")
  val BRACKET = SproutTokenType("BRACKET")

  val COMMENTS = TokenSet.create(COMMENT)
  val STRINGS = TokenSet.create(STRING, STRING_ESCAPE, CHAR)
}
