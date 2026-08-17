package dev.sprout.intellij

import com.intellij.lang.ASTNode
import com.intellij.lang.ParserDefinition
import com.intellij.lang.PsiBuilder
import com.intellij.lang.PsiParser
import com.intellij.lexer.Lexer
import com.intellij.openapi.project.Project
import com.intellij.psi.FileViewProvider
import com.intellij.psi.PsiElement
import com.intellij.psi.PsiFile
import com.intellij.psi.TokenType
import com.intellij.psi.tree.IElementType
import com.intellij.psi.tree.IFileElementType
import com.intellij.psi.tree.TokenSet
import com.intellij.extapi.psi.PsiFileBase

/**
 * A FLAT parser: the file becomes one root node over the lexer's tokens, with no tree
 * structure above them.
 *
 * This is not a stubbed-out placeholder for a real Sprout parser — a real one is
 * explicitly a non-goal (`docs/intellij-plugin-v0.md` §4), because a hand-maintained
 * Kotlin parser would be a second authority on Sprout's syntax. Its purpose is narrower:
 * several editor features (the commenter, brace matching, extend-selection) reach for the
 * PSI of the file they act on, and a language file type with no ParserDefinition has none.
 * A flat tree is enough for all of them, and honest about knowing nothing about structure.
 */
class SproutParserDefinition : ParserDefinition {
  override fun createLexer(project: Project?): Lexer = SproutLexer()

  override fun createParser(project: Project?): PsiParser = SproutParser()

  override fun getFileNodeType(): IFileElementType = FILE

  override fun getCommentTokens(): TokenSet = SproutTokenTypes.COMMENTS

  override fun getStringLiteralElements(): TokenSet = SproutTokenTypes.STRINGS

  override fun createElement(node: ASTNode?): PsiElement =
    throw UnsupportedOperationException("Sprout has no composite PSI elements")

  override fun createFile(viewProvider: FileViewProvider): PsiFile = SproutPsiFile(viewProvider)

  companion object {
    val FILE = IFileElementType(SproutLanguage)
  }
}

private class SproutParser : PsiParser {
  override fun parse(root: IElementType, builder: PsiBuilder): ASTNode {
    val marker = builder.mark()
    while (!builder.eof()) builder.advanceLexer()
    marker.done(root)
    return builder.treeBuilt
  }
}

class SproutPsiFile(viewProvider: FileViewProvider) : PsiFileBase(viewProvider, SproutLanguage) {
  override fun getFileType() = SproutFileType
  override fun toString() = "Sprout file"
}

/**
 * `#` line comments — Sprout has no block comment form, so the block methods return null
 * and the platform hides the corresponding action rather than inserting something invalid.
 */
class SproutCommenter : com.intellij.lang.Commenter {
  override fun getLineCommentPrefix() = "#"
  override fun getBlockCommentPrefix(): String? = null
  override fun getBlockCommentSuffix(): String? = null
  override fun getCommentedBlockCommentPrefix(): String? = null
  override fun getCommentedBlockCommentSuffix(): String? = null
}

/**
 * Brace matching over the lexer's tokens. Sprout is layout-sensitive rather than
 * brace-delimited, so these three pairs are the whole surface: tuple/call parens, list
 * brackets, and record braces.
 */
class SproutBraceMatcher : com.intellij.lang.PairedBraceMatcher {
  override fun getPairs(): Array<com.intellij.lang.BracePair> = PAIRS

  override fun isPairedBracesAllowedBeforeType(lbraceType: IElementType, next: IElementType?): Boolean =
    next == null ||
      next == TokenType.WHITE_SPACE ||
      next == SproutTokenTypes.COMMENT ||
      next == SproutTokenTypes.COMMA ||
      next == SproutTokenTypes.PAREN ||
      next == SproutTokenTypes.BRACKET ||
      next == SproutTokenTypes.BRACE

  override fun getCodeConstructStart(file: PsiFile?, openingBraceOffset: Int) = openingBraceOffset

  private companion object {
    // The lexer gives one token type per bracket FAMILY rather than per side, so the
    // matcher cannot distinguish `(` from `)` by type. Pass the same type for both ends:
    // the platform then pairs by character, which is what is wanted here.
    val PAIRS = arrayOf(
      com.intellij.lang.BracePair(SproutTokenTypes.PAREN, SproutTokenTypes.PAREN, false),
      com.intellij.lang.BracePair(SproutTokenTypes.BRACKET, SproutTokenTypes.BRACKET, false),
      com.intellij.lang.BracePair(SproutTokenTypes.BRACE, SproutTokenTypes.BRACE, true),
    )
  }
}
