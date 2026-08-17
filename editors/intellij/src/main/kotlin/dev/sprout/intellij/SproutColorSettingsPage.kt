package dev.sprout.intellij

import com.intellij.openapi.editor.colors.TextAttributesKey
import com.intellij.openapi.fileTypes.SyntaxHighlighter
import com.intellij.openapi.options.colors.AttributesDescriptor
import com.intellij.openapi.options.colors.ColorDescriptor
import com.intellij.openapi.options.colors.ColorSettingsPage
import javax.swing.Icon

/**
 * Settings → Editor → Color Scheme → Sprout. The preview text is real Sprout, not a
 * synthetic sample: it is the idiomatic shapes from `docs/idiomatic-sprout.md`, so the
 * page doubles as a demonstration that the lexer handles them.
 */
class SproutColorSettingsPage : ColorSettingsPage {
  override fun getDisplayName() = "Sprout"

  override fun getIcon(): Icon = SproutIcons.FILE

  override fun getHighlighter(): SyntaxHighlighter = SproutSyntaxHighlighter()

  override fun getAttributeDescriptors(): Array<AttributesDescriptor> = DESCRIPTORS

  override fun getColorDescriptors(): Array<ColorDescriptor> = ColorDescriptor.EMPTY_ARRAY

  override fun getAdditionalHighlightingTagToDescriptorMap(): Map<String, TextAttributesKey>? = null

  override fun getDemoText() = DEMO

  private companion object {
    val DESCRIPTORS = arrayOf(
      AttributesDescriptor("Comment", SproutColors.COMMENT),
      AttributesDescriptor("Keyword", SproutColors.KEYWORD),
      AttributesDescriptor("Contextual keyword//module, import, as, alias, linear, record", SproutColors.SOFT_KEYWORD),
      AttributesDescriptor("Boolean literal", SproutColors.BOOLEAN),
      AttributesDescriptor("Identifier", SproutColors.IDENTIFIER),
      AttributesDescriptor("Type and constructor", SproutColors.TYPE_IDENTIFIER),
      AttributesDescriptor("Module qualifier", SproutColors.QUALIFIER),
      AttributesDescriptor("Number", SproutColors.NUMBER),
      AttributesDescriptor("String", SproutColors.STRING),
      AttributesDescriptor("String escape", SproutColors.STRING_ESCAPE),
      AttributesDescriptor("Character", SproutColors.CHAR),
      AttributesDescriptor("Interpolation marker", SproutColors.INTERP_MARKER),
      AttributesDescriptor("Operator", SproutColors.OPERATOR),
      AttributesDescriptor("Dot", SproutColors.DOT),
      AttributesDescriptor("Comma", SproutColors.COMMA),
      AttributesDescriptor("Parentheses", SproutColors.PAREN),
      AttributesDescriptor("Braces", SproutColors.BRACE),
      AttributesDescriptor("Brackets", SproutColors.BRACKET),
      AttributesDescriptor("Bad character", SproutColors.BAD_CHARACTER),
    )

    val DEMO = """
      module app.demo

      import stdlib.string as string
      import stdlib.bits (bit_or)

      # A record, an ADT, and the deriving list.
      export type Level (..) deriving (Eq, ToString) =
        | Quiet
        | Loud Int

      export type alias Name = String

      export fn describe(level: Level, name: Name) -> String =
        let Loud volume = level else `${'$'}{name} is quiet`
        in `${'$'}{name} at ${'$'}{string.from_int(volume)}, mask ${'$'}{string.from_int(mask)}`
        where
          mask = bit_or(0xFF, 0b1010)

      fn main() -> Unit !{IO} =
        do
          let levels = [Quiet, Loud(11)]
          list_each(\l -> print(describe(l, "sprout")), levels)
          if 3.5 > 2.0 && 'x' != 'y' then print("ok\n") else ()
    """.trimIndent()
  }
}
