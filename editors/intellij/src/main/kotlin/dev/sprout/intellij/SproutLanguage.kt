package dev.sprout.intellij

import com.intellij.lang.Language
import com.intellij.openapi.fileTypes.LanguageFileType
import com.intellij.openapi.util.IconLoader
import javax.swing.Icon

object SproutLanguage : Language("Sprout") {
  override fun getDisplayName() = "Sprout"
  override fun isCaseSensitive() = true
}

object SproutIcons {
  val FILE: Icon = IconLoader.getIcon("/icons/sprout.svg", SproutIcons::class.java)
}

object SproutFileType : LanguageFileType(SproutLanguage) {
  // `.sprout` is the module extension; `.spr` is used by the test suite under tests/.
  // Both are the same language — nothing in the compiler distinguishes them.
  override fun getName() = "Sprout"
  override fun getDescription() = "Sprout source file"
  override fun getDefaultExtension() = "sprout"
  override fun getIcon() = SproutIcons.FILE
  override fun getCharset(file: com.intellij.openapi.vfs.VirtualFile, content: ByteArray) = "UTF-8"
}
