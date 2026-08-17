package dev.sprout.intellij

import com.intellij.lexer.LexerBase
import com.intellij.psi.TokenType
import com.intellij.psi.tree.IElementType

/**
 * Hand-written lexer for highlighting. Mirrors `stdlib/compiler/lexer.sprout`:
 *
 *  - keywords: exactly the 19 in that file's `is_keyword`, no more
 *  - comments: `#` to end of line
 *  - numbers: decimal, `0x`/`0X` hex, `0b`/`0B` binary, and `<digits>.<digits>` floats
 *  - strings `"…"`, char literals `'c'`, backtick templates with `${…}` interpolation
 *  - operators: the two-char set, then the single-symbol set
 *
 * It is NOT a parser and makes no attempt to be one — see [SproutParserDefinition].
 */
class SproutLexer : LexerBase() {
  private companion object {
    /** Exactly `is_keyword` in stdlib/compiler/lexer.sprout. Keep in lockstep. */
    val KEYWORDS = setOf(
      "export", "fn", "let", "type", "class", "instance", "where", "match", "with",
      "do", "if", "then", "else", "in", "true", "false", "extern", "deriving", "wrap",
    )

    /**
     * Contextual words the parser reads positionally. They are plain identifiers to the
     * compiler, so highlighting them is a presentation choice, not a language fact.
     */
    val SOFT_KEYWORDS = setOf("module", "import", "as", "alias", "linear", "record")

    /** `try_ops` in the compiler's lexer, longest-first so `->` beats `-`. */
    val TWO_CHAR_OPS = listOf(
      "->", "<-", "==", "!=", "<=", ">=", "&&", "||", ">>", "<<", "|>", "++", "..",
    )

    const val SINGLE_SYMBOLS = "()=,:+-*/<>\\!{}[]|"

    const val STATE_NORMAL = 0
    const val STATE_TEMPLATE = 1

    /** `STATE_INTERP + n` means inside `${…}` with n+1 unclosed braces. */
    const val STATE_INTERP = 2
  }

  private var buffer: CharSequence = ""
  private var bufferEnd = 0
  private var tokenStart = 0
  private var tokenEnd = 0
  private var currentToken: IElementType? = null
  private var lexerState = STATE_NORMAL

  override fun start(buffer: CharSequence, startOffset: Int, endOffset: Int, initialState: Int) {
    this.buffer = buffer
    this.bufferEnd = endOffset
    this.tokenStart = startOffset
    this.tokenEnd = startOffset
    this.lexerState = initialState
    advance()
  }

  override fun getState() = lexerState
  override fun getTokenType() = currentToken
  override fun getTokenStart() = tokenStart
  override fun getTokenEnd() = tokenEnd
  override fun getBufferSequence() = buffer
  override fun getBufferEnd() = bufferEnd

  override fun advance() {
    tokenStart = tokenEnd
    if (tokenStart >= bufferEnd) {
      currentToken = null
      return
    }
    if (lexerState == STATE_TEMPLATE) lexTemplateBody() else lexCode()
  }

  // ---------------------------------------------------------------------------

  /**
   * Lookahead past the end of the buffer yields NUL — written as an escape, never as a raw
   * byte: a literal NUL in a source file makes git classify the file as binary, which
   * silently costs the diff.
   *
   * NUL rather than a space because it satisfies none of the predicates lookahead asks
   * about — not whitespace, not an identifier character, not a digit. A space IS
   * whitespace, so lookahead at the buffer's end could match something that is not there.
   */
  private fun charAt(i: Int): Char = if (i < bufferEnd) buffer[i] else '\u0000'

  private fun emit(type: IElementType, end: Int) {
    currentToken = type
    tokenEnd = end.coerceAtMost(bufferEnd)
  }

  private fun isIdentStart(c: Char) = c.isLetter() || c == '_'
  private fun isIdentPart(c: Char) = c.isLetterOrDigit() || c == '_'

  // ---------------------------------------------------------------------------

  private fun lexCode() {
    val c = charAt(tokenStart)

    when {
      c.isWhitespace() -> {
        var i = tokenStart
        while (i < bufferEnd && buffer[i].isWhitespace()) i++
        emit(TokenType.WHITE_SPACE, i)
      }

      c == '#' -> {
        var i = tokenStart
        while (i < bufferEnd && buffer[i] != '\n') i++
        emit(SproutTokenTypes.COMMENT, i)
      }

      c == '"' -> lexQuoted('"', SproutTokenTypes.STRING)
      c == '\'' -> lexQuoted('\'', SproutTokenTypes.CHAR)

      c == '`' -> {
        // Opening backtick. Templates may span lines, so the body is a separate state
        // rather than a scan bounded by the newline.
        lexerState = STATE_TEMPLATE
        emit(SproutTokenTypes.STRING, tokenStart + 1)
      }

      c.isDigit() -> lexNumber()

      isIdentStart(c) -> lexIdentifier()

      c == '.' -> {
        if (charAt(tokenStart + 1) == '.') {
          emit(SproutTokenTypes.OPERATOR, tokenStart + 2)
        } else {
          emit(SproutTokenTypes.DOT, tokenStart + 1)
        }
      }

      else -> lexSymbol(c)
    }
  }

  private fun lexSymbol(c: Char) {
    val two = if (tokenStart + 1 < bufferEnd) buffer.subSequence(tokenStart, tokenStart + 2).toString() else ""
    if (two in TWO_CHAR_OPS) {
      emit(SproutTokenTypes.OPERATOR, tokenStart + 2)
      return
    }

    // A `}` may close an interpolation rather than a record literal, so brace depth is
    // tracked while inside `${…}`. Without this the rest of the template would be lexed
    // as code.
    if (lexerState >= STATE_INTERP) {
      if (c == '{') {
        lexerState += 1
        emit(SproutTokenTypes.BRACE, tokenStart + 1)
        return
      }
      if (c == '}') {
        if (lexerState == STATE_INTERP) {
          lexerState = STATE_TEMPLATE
          emit(SproutTokenTypes.INTERP_MARKER, tokenStart + 1)
        } else {
          lexerState -= 1
          emit(SproutTokenTypes.BRACE, tokenStart + 1)
        }
        return
      }
    }

    val type = when (c) {
      '(', ')' -> SproutTokenTypes.PAREN
      '{', '}' -> SproutTokenTypes.BRACE
      '[', ']' -> SproutTokenTypes.BRACKET
      ',' -> SproutTokenTypes.COMMA
      else -> if (SINGLE_SYMBOLS.indexOf(c) >= 0) SproutTokenTypes.OPERATOR else TokenType.BAD_CHARACTER
    }
    emit(type, tokenStart + 1)
  }

  private fun lexIdentifier() {
    var i = tokenStart
    while (i < bufferEnd && isIdentPart(buffer[i])) i++
    val text = buffer.subSequence(tokenStart, i).toString()

    val type = when {
      text == "true" || text == "false" -> SproutTokenTypes.BOOLEAN
      text in KEYWORDS -> SproutTokenTypes.KEYWORD
      text in SOFT_KEYWORDS && isFirstOnLine(tokenStart) -> SproutTokenTypes.SOFT_KEYWORD
      // A name followed by `.` is a qualifier: an import alias or a module path segment.
      charAt(i) == '.' && isIdentStart(charAt(i + 1)) -> SproutTokenTypes.QUALIFIER
      text.first().isUpperCase() -> SproutTokenTypes.TYPE_IDENTIFIER
      else -> SproutTokenTypes.IDENTIFIER
    }
    emit(type, i)
  }

  /**
   * True when only whitespace precedes [offset] on its line.
   *
   * Looking backwards in the buffer rather than tracking a flag keeps the lexer's state
   * small enough to be *correct under incremental relexing*: IntelliJ restarts a lexer
   * mid-document from a saved [getState], so any fact not encoded in that int must be
   * recoverable from the text. "Am I at the start of a line" is.
   */
  private fun isFirstOnLine(offset: Int): Boolean {
    var i = offset - 1
    while (i >= 0) {
      val c = buffer[i]
      if (c == '\n') return true
      if (!c.isWhitespace()) return false
      i--
    }
    return true
  }

  private fun lexNumber() {
    var i = tokenStart
    val marker = charAt(i + 1)
    if (buffer[i] == '0' && (marker == 'x' || marker == 'X' || marker == 'b' || marker == 'B')) {
      val isHex = marker == 'x' || marker == 'X'
      var j = i + 2
      while (j < bufferEnd && isRadixDigit(buffer[j], isHex)) j++
      // A prefix with no digits after it is not an error in the compiler's lexer either:
      // `0x` falls back to the int `0` followed by the identifier `x`.
      if (j > i + 2) {
        emit(SproutTokenTypes.NUMBER, j)
        return
      }
      emit(SproutTokenTypes.NUMBER, i + 1)
      return
    }

    while (i < bufferEnd && buffer[i].isDigit()) i++
    // A float needs a digit after the dot: `1..5` is an int, `..`, an int — not a float.
    if (charAt(i) == '.' && charAt(i + 1).isDigit()) {
      i++
      while (i < bufferEnd && buffer[i].isDigit()) i++
    }
    emit(SproutTokenTypes.NUMBER, i)
  }

  private fun isRadixDigit(c: Char, hex: Boolean): Boolean =
    if (hex) c.isDigit() || c in 'a'..'f' || c in 'A'..'F' else c == '0' || c == '1'

  /**
   * A `"…"` or `'…'` literal. An unterminated one stops at the newline so a stray quote
   * cannot colour the remainder of the file as a string.
   */
  private fun lexQuoted(quote: Char, type: IElementType) {
    var i = tokenStart + 1
    while (i < bufferEnd) {
      val c = buffer[i]
      if (c == '\n') break
      if (c == '\\' && i + 1 < bufferEnd) {
        i += 2
        continue
      }
      if (c == quote) {
        i++
        break
      }
      i++
    }
    emit(type, i)
  }

  private fun lexTemplateBody() {
    val c = charAt(tokenStart)

    if (c == '`') {
      lexerState = STATE_NORMAL
      emit(SproutTokenTypes.STRING, tokenStart + 1)
      return
    }
    if (c == '$' && charAt(tokenStart + 1) == '{') {
      lexerState = STATE_INTERP
      emit(SproutTokenTypes.INTERP_MARKER, tokenStart + 2)
      return
    }
    if (c == '\\' && tokenStart + 1 < bufferEnd) {
      emit(SproutTokenTypes.STRING_ESCAPE, tokenStart + 2)
      return
    }

    var i = tokenStart
    while (i < bufferEnd) {
      val ch = buffer[i]
      if (ch == '`' || ch == '\\') break
      if (ch == '$' && charAt(i + 1) == '{') break
      i++
    }
    emit(SproutTokenTypes.STRING, i)
  }
}
