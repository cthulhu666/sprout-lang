package dev.sprout.intellij

import com.intellij.psi.TokenType
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The lexer is the one place this plugin restates something the compiler already knows, so
 * these tests pin the restatement against the compiler's actual rules
 * (`stdlib/compiler/lexer.sprout`) rather than against what looks plausible.
 *
 * Deliberately a plain JUnit test with no IDE fixture: the lexer touches no platform
 * service, and keeping it fixture-free means it runs in milliseconds and cannot fail for
 * reasons unrelated to lexing.
 */
class SproutLexerTest {

  private fun tokens(text: String): List<Pair<String, String>> {
    val lexer = SproutLexer()
    lexer.start(text, 0, text.length, 0)
    val out = mutableListOf<Pair<String, String>>()
    while (lexer.tokenType != null) {
      val type = lexer.tokenType!!
      if (type != TokenType.WHITE_SPACE) {
        out += type.toString() to text.substring(lexer.tokenStart, lexer.tokenEnd)
      }
      lexer.advance()
    }
    return out
  }

  private fun types(text: String) = tokens(text).map { it.first }
  private fun texts(text: String) = tokens(text).map { it.second }

  /** Covers the whole buffer exactly once, with no gap and no overlap. */
  private fun assertCoversBuffer(text: String) {
    val lexer = SproutLexer()
    lexer.start(text, 0, text.length, 0)
    var expected = 0
    while (lexer.tokenType != null) {
      assertEquals("token starts where the previous one ended, in <$text>", expected, lexer.tokenStart)
      assertTrue("token must not be empty, in <$text>", lexer.tokenEnd > lexer.tokenStart)
      expected = lexer.tokenEnd
      lexer.advance()
    }
    assertEquals("the lexer must consume the whole buffer, in <$text>", text.length, expected)
  }

  // --- keywords vs contextual words -------------------------------------------------

  @Test
  fun `the nineteen keywords are keywords`() {
    val kws = "export fn let type class instance where match with do if then else in extern deriving wrap"
    assertTrue(types(kws).all { it == "KEYWORD" })
  }

  @Test
  fun `true and false are booleans, not bare keywords`() {
    // They ARE in the compiler's is_keyword, but colouring them as literals is the point
    // of a separate token type.
    assertEquals(listOf("BOOLEAN", "BOOLEAN"), types("true false"))
  }

  @Test
  fun `contextual words are soft keywords only at the start of a line`() {
    assertEquals(listOf("SOFT_KEYWORD", "QUALIFIER", "DOT", "IDENTIFIER"), types("import stdlib.string"))
    // Mid-line, the same word is an ordinary identifier — which is what the compiler
    // thinks it is everywhere, since `import` is absent from is_keyword.
    assertEquals(listOf("IDENTIFIER", "OPERATOR", "IDENTIFIER"), types("x = import"))
  }

  @Test
  fun `a contextual word after only whitespace on the line is still a soft keyword`() {
    assertEquals(listOf("SOFT_KEYWORD", "TYPE_IDENTIFIER"), types("  record Foo"))
  }

  // --- identifiers, types, qualifiers ----------------------------------------------

  @Test
  fun `an uppercase name is a type or constructor`() {
    assertEquals(listOf("TYPE_IDENTIFIER"), types("Maybe"))
  }

  @Test
  fun `a qualified name splits into qualifier, dot and member`() {
    // The compiler lexes this as ONE identifier token, because its is_ident_continue
    // admits `.`. Splitting is a presentation choice; see SproutTokenTypes.
    assertEquals(listOf("QUALIFIER", "DOT", "IDENTIFIER"), types("gfx.draw_frame"))
    assertEquals(listOf("gfx", ".", "draw_frame"), texts("gfx.draw_frame"))
  }

  @Test
  fun `a trailing dot is not a qualifier`() {
    assertEquals(listOf("IDENTIFIER", "DOT"), types("x."))
  }

  // --- numbers ----------------------------------------------------------------------

  @Test
  fun `radix literals are single number tokens`() {
    assertEquals(listOf("NUMBER"), types("0xFF"))
    assertEquals(listOf("NUMBER"), types("0b1010"))
    assertEquals(listOf("NUMBER"), types("0X1f"))
    assertEquals(listOf("NUMBER"), types("0B01"))
  }

  @Test
  fun `a radix prefix with no digits degrades exactly as the compiler's lexer does`() {
    // scan_radix_end returns Nothing, so `0x` is the int 0 followed by the identifier x.
    assertEquals(listOf("NUMBER", "IDENTIFIER"), types("0x"))
    assertEquals(listOf("0", "x"), texts("0x"))
  }

  @Test
  fun `a float needs a digit after the dot`() {
    assertEquals(listOf("NUMBER"), types("3.5"))
    assertEquals(listOf("3.5"), texts("3.5"))
    // `1..5` is an int, the range operator, an int — NOT a malformed float.
    assertEquals(listOf("NUMBER", "OPERATOR", "NUMBER"), types("1..5"))
    assertEquals(listOf("1", "..", "5"), texts("1..5"))
  }

  // --- comments, strings, chars ----------------------------------------------------

  @Test
  fun `a hash comment runs to end of line only`() {
    assertEquals(listOf("COMMENT", "IDENTIFIER"), types("# note\nx"))
  }

  @Test
  fun `a string escape does not terminate the string`() {
    assertEquals(listOf("STRING"), types(""""a\"b""""))
  }

  @Test
  fun `an unterminated string stops at the newline`() {
    // Otherwise one stray quote colours the rest of the file as a string.
    assertEquals(listOf("STRING", "IDENTIFIER"), types("\"oops\nx"))
  }

  @Test
  fun `char literals lex as chars`() {
    assertEquals(listOf("CHAR", "OPERATOR", "CHAR"), types("'x' != 'y'"))
  }

  // --- templates and interpolation -------------------------------------------------

  @Test
  fun `interpolated code inside a template is lexed as code`() {
    // The whole point of the template state machine: `name` must not be part of a string.
    assertEquals(
      listOf("STRING", "STRING", "INTERP_MARKER", "IDENTIFIER", "INTERP_MARKER", "STRING", "STRING"),
      types("`hi ${'$'}{name}!`"),
    )
  }

  @Test
  fun `a record literal inside an interpolation does not end the interpolation`() {
    // Brace depth is why: the first `}` closes the record, the second the interpolation.
    val src = "`v=${'$'}{f({a: 1})}`"
    assertCoversBuffer(src)
    assertEquals(
      listOf(
        "STRING", "STRING", "INTERP_MARKER",
        "IDENTIFIER", "PAREN", "BRACE", "IDENTIFIER", "OPERATOR", "NUMBER", "BRACE", "PAREN",
        // This `}` closes the interpolation, not the record — depth tracking is what
        // tells them apart. Get it wrong and the closing backtick below lexes as code.
        "INTERP_MARKER",
        "STRING",
      ),
      types(src),
    )
  }

  @Test
  fun `a template may span lines`() {
    val src = "`line one\nline two`"
    assertCoversBuffer(src)
    assertTrue(types(src).all { it == "STRING" })
  }

  @Test
  fun `an escape inside a template is highlighted separately`() {
    assertTrue(types("`a\\nb`").contains("STRING_ESCAPE"))
  }

  // --- operators --------------------------------------------------------------------

  @Test
  fun `two-character operators win over single characters`() {
    assertEquals(listOf("OPERATOR"), types("->"))
    assertEquals(listOf("OPERATOR"), types("<-"))
    assertEquals(listOf("OPERATOR"), types("|>"))
    assertEquals(listOf("OPERATOR"), types("++"))
    assertEquals(listOf("OPERATOR"), types("=="))
    assertEquals(listOf("OPERATOR"), types(">="))
  }

  @Test
  fun `a lambda backslash is an operator`() {
    assertEquals(listOf("OPERATOR", "IDENTIFIER", "OPERATOR", "IDENTIFIER"), types("\\x -> x"))
  }

  @Test
  fun `brackets braces and parens get their own types`() {
    assertEquals(listOf("PAREN", "PAREN", "BRACKET", "BRACKET", "BRACE", "BRACE"), types("()[]{}"))
  }

  @Test
  fun `an effect annotation lexes without bad characters`() {
    assertTrue(types("!{IO}").none { it == "BAD_CHARACTER" })
  }

  // --- whole-buffer invariants ------------------------------------------------------

  @Test
  fun `the lexer consumes every byte of a realistic module`() {
    val src = """
      module app.demo

      import stdlib.string as string

      export type Level (..) deriving (Eq) =
        | Quiet
        | Loud Int

      export fn describe(l: Level) -> String =
        match l with
        | Quiet -> "quiet"
        | Loud v -> `loud at ${'$'}{string.from_int(v)}`

      fn main() -> Unit !{IO} =
        do
          list_each(\x -> print(describe(x)), [Quiet, Loud(0xFF)])
          if 3.5 > 2.0 then print("ok\n") else ()
    """.trimIndent()
    assertCoversBuffer(src)
    assertTrue("no bad characters in idiomatic Sprout", types(src).none { it == "BAD_CHARACTER" })
  }

  @Test
  fun `an empty buffer produces no tokens`() {
    assertEquals(emptyList<String>(), types(""))
  }

  /**
   * Hand-written fixtures only prove the lexer handles what its author thought of. This
   * runs it over real stdlib and compiler sources — tens of thousands of tokens nobody
   * wrote with this lexer in mind — and asserts the two properties that must hold
   * everywhere: it consumes the whole file, and idiomatic Sprout contains no character
   * the lexer cannot classify.
   *
   * Missing files fail rather than skip. A silent skip here would turn the strongest
   * check in the suite into a no-op, which is precisely how it would rot.
   */
  @Test
  fun `real Sprout sources lex cleanly`() {
    val repoRoot = java.io.File("../..").canonicalFile
    val sources = listOf(
      "stdlib/prelude.sprout",
      "stdlib/string.sprout",
      "stdlib/json.sprout",
      "stdlib/compiler/lexer.sprout",
      "stdlib/compiler/parser.sprout",
      "stdlib/compiler/lsp_driver.sprout",
    ).map { java.io.File(repoRoot, it) }

    for (file in sources) {
      assertTrue("expected to find $file — the check is worthless without it", file.isFile)
      val text = file.readText()
      assertCoversBuffer(text)

      val bad = tokens(text).filter { it.first == "BAD_CHARACTER" }
      assertTrue(
        "${file.name}: lexer could not classify ${bad.size} character(s): " +
          bad.take(10).joinToString { "'${it.second}'" },
        bad.isEmpty(),
      )
    }
  }
}
