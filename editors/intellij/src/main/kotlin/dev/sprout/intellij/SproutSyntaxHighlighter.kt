package dev.sprout.intellij

import com.intellij.lexer.Lexer
import com.intellij.openapi.editor.DefaultLanguageHighlighterColors as D
import com.intellij.openapi.editor.HighlighterColors
import com.intellij.openapi.editor.colors.TextAttributesKey
import com.intellij.openapi.editor.colors.TextAttributesKey.createTextAttributesKey
import com.intellij.openapi.fileTypes.SyntaxHighlighter
import com.intellij.openapi.fileTypes.SyntaxHighlighterBase
import com.intellij.openapi.fileTypes.SyntaxHighlighterFactory
import com.intellij.openapi.project.Project
import com.intellij.openapi.vfs.VirtualFile
import com.intellij.psi.TokenType
import com.intellij.psi.tree.IElementType

/**
 * Colour assignments follow `tree-sitter-sprout/queries/highlights.scm`, which is the
 * repo's existing authority on how Sprout should look. Three things it does not cover,
 * because a tree-sitter query over that grammar had no node for them, are added here:
 * `#` comments, `${…}` interpolation, and float literals.
 *
 * Every key falls back to a platform default, so the plugin inherits whatever colour
 * scheme the user already has rather than imposing one.
 */
object SproutColors {
  val COMMENT = key("SPROUT_COMMENT", D.LINE_COMMENT)
  val KEYWORD = key("SPROUT_KEYWORD", D.KEYWORD)
  val SOFT_KEYWORD = key("SPROUT_SOFT_KEYWORD", D.KEYWORD)
  val BOOLEAN = key("SPROUT_BOOLEAN", D.KEYWORD)
  val IDENTIFIER = key("SPROUT_IDENTIFIER", D.IDENTIFIER)
  val TYPE_IDENTIFIER = key("SPROUT_TYPE", D.CLASS_NAME)
  val QUALIFIER = key("SPROUT_QUALIFIER", D.INSTANCE_FIELD)
  val NUMBER = key("SPROUT_NUMBER", D.NUMBER)
  val STRING = key("SPROUT_STRING", D.STRING)
  val STRING_ESCAPE = key("SPROUT_STRING_ESCAPE", D.VALID_STRING_ESCAPE)
  val CHAR = key("SPROUT_CHAR", D.STRING)
  val INTERP_MARKER = key("SPROUT_INTERP", D.TEMPLATE_LANGUAGE_COLOR)
  val OPERATOR = key("SPROUT_OPERATOR", D.OPERATION_SIGN)
  val DOT = key("SPROUT_DOT", D.DOT)
  val COMMA = key("SPROUT_COMMA", D.COMMA)
  val PAREN = key("SPROUT_PAREN", D.PARENTHESES)
  val BRACE = key("SPROUT_BRACE", D.BRACES)
  val BRACKET = key("SPROUT_BRACKET", D.BRACKETS)
  val BAD_CHARACTER = key("SPROUT_BAD_CHARACTER", HighlighterColors.BAD_CHARACTER)

  private fun key(name: String, fallback: TextAttributesKey) = createTextAttributesKey(name, fallback)
}

class SproutSyntaxHighlighter : SyntaxHighlighterBase() {
  override fun getHighlightingLexer(): Lexer = SproutLexer()

  override fun getTokenHighlights(tokenType: IElementType): Array<TextAttributesKey> =
    ATTRIBUTES[tokenType]?.let { arrayOf(it) } ?: EMPTY

  private companion object {
    val EMPTY = emptyArray<TextAttributesKey>()

    val ATTRIBUTES: Map<IElementType, TextAttributesKey> = mapOf(
      SproutTokenTypes.COMMENT to SproutColors.COMMENT,
      SproutTokenTypes.KEYWORD to SproutColors.KEYWORD,
      SproutTokenTypes.SOFT_KEYWORD to SproutColors.SOFT_KEYWORD,
      SproutTokenTypes.BOOLEAN to SproutColors.BOOLEAN,
      SproutTokenTypes.IDENTIFIER to SproutColors.IDENTIFIER,
      SproutTokenTypes.TYPE_IDENTIFIER to SproutColors.TYPE_IDENTIFIER,
      SproutTokenTypes.QUALIFIER to SproutColors.QUALIFIER,
      SproutTokenTypes.NUMBER to SproutColors.NUMBER,
      SproutTokenTypes.STRING to SproutColors.STRING,
      SproutTokenTypes.STRING_ESCAPE to SproutColors.STRING_ESCAPE,
      SproutTokenTypes.CHAR to SproutColors.CHAR,
      SproutTokenTypes.INTERP_MARKER to SproutColors.INTERP_MARKER,
      SproutTokenTypes.OPERATOR to SproutColors.OPERATOR,
      SproutTokenTypes.DOT to SproutColors.DOT,
      SproutTokenTypes.COMMA to SproutColors.COMMA,
      SproutTokenTypes.PAREN to SproutColors.PAREN,
      SproutTokenTypes.BRACE to SproutColors.BRACE,
      SproutTokenTypes.BRACKET to SproutColors.BRACKET,
      TokenType.BAD_CHARACTER to SproutColors.BAD_CHARACTER,
    )
  }
}

class SproutSyntaxHighlighterFactory : SyntaxHighlighterFactory() {
  override fun getSyntaxHighlighter(project: Project?, virtualFile: VirtualFile?): SyntaxHighlighter =
    SproutSyntaxHighlighter()
}
