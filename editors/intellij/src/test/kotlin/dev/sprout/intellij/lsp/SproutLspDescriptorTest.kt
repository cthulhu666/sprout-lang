package dev.sprout.intellij.lsp

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The optional LSP descriptor must register against the extension point names the platform
 * declares — checked as text, because that is the layer where the bug was.
 *
 * `<platformLspServerSupportProvider>` shipped for a day. It is not a compile error, not a
 * bytecode problem, and not something `verifyPlugin` asks about; it composes with
 * `defaultExtensionNs="com.intellij"` into an extension point that does not exist, so the
 * provider is silently never instantiated. Every LSP feature was dead while highlighting
 * kept working, and nothing in the build could tell.
 *
 * [SproutLspRegistrationTest] is the stronger check, but it is inert in an IDE that does
 * not declare `com.intellij.modules.lsp` — which the build's IntelliJ IDEA Ultimate does
 * not. This one has no such dependency: it reads the shipped resource and runs everywhere.
 *
 * The expected strings are literals on purpose. Deriving them from the API would make this
 * agree with whatever the code says, which is exactly the failure being guarded against.
 * They come from RubyMine 2025.2's own `intellij.platform.lsp.xml`:
 *
 *     <extensionPoint qualifiedName="com.intellij.platform.lsp.serverSupportProvider"
 *                     interface="com.intellij.platform.lsp.api.LspServerSupportProvider"
 *                     dynamic="true"/>
 */
class SproutLspDescriptorTest {

  private fun descriptor(): String =
    javaClass.classLoader.getResourceAsStream("META-INF/sprout-lsp.xml")
      ?.bufferedReader()?.readText()
      ?: error("META-INF/sprout-lsp.xml is not on the classpath — the optional LSP " +
        "descriptor is not being packaged, so no LSP extension can ever register.")

  @Test
  fun `registers the server support provider at the platform's extension point`() {
    val xml = descriptor()
    assertTrue(
      "sprout-lsp.xml must register <platform.lsp.serverSupportProvider>, which composes " +
        "with defaultExtensionNs=\"com.intellij\" into " +
        "com.intellij.platform.lsp.serverSupportProvider. Found instead:\n" +
        xml.lines().filter { it.contains("ServerSupportProvider") }.joinToString("\n"),
      xml.contains("<platform.lsp.serverSupportProvider"),
    )
  }

  @Test
  fun `does not register the extension point name that does not exist`() {
    val xml = descriptor()
    assertTrue(
      "sprout-lsp.xml registers <platformLspServerSupportProvider>, which resolves to " +
        "com.intellij.platformLspServerSupportProvider — an extension point no IDE " +
        "declares. The provider will never be instantiated and every LSP feature will be " +
        "silently dead.",
      !xml.contains("<platformLspServerSupportProvider"),
    )
  }

  @Test
  fun `declares the implementation class that exists`() {
    val xml = descriptor()
    val implementations = Regex("""implementation="([^"]+)"""").findAll(xml)
      .map { it.groupValues[1] }.toList()
    assertEquals(
      listOf(
        "dev.sprout.intellij.lsp.SproutLspServerSupportProvider",
        "dev.sprout.intellij.lsp.SproutEditorNotificationProvider",
      ),
      implementations,
    )
    // A tag pointing at a class that was renamed away fails the same way a wrong tag does:
    // silently, at registration time, in an IDE nobody is watching. Every tag, not just the
    // first — an unloadable second extension is exactly as quiet as an unloadable first.
    implementations.forEach { Class.forName(it) }
  }

  @Test
  fun `registers the banner that reports an unconfigured plugin`() {
    // The unconfigured case is otherwise reported only by a balloon, which was missed in a
    // real session and left a whole project reading as "this language has no diagnostics".
    // The EP name and interface are the platform's own, read out of app.jar:
    //
    //     <extensionPoint qualifiedName="com.intellij.editorNotificationProvider"
    //                     area="IDEA_PROJECT"
    //                     interface="com.intellij.ui.EditorNotificationProvider"/>
    val xml = descriptor()
    assertTrue(
      "sprout-lsp.xml must register <editorNotificationProvider>, or an unconfigured " +
        "plugin reports itself only through a balloon that auto-hides.",
      xml.contains("<editorNotificationProvider"),
    )
  }

  @Test
  fun `the unconfigured balloon is sticky`() {
    // A transient balloon is the exact signal that was missed. What it reports — no server
    // at all — has no other transient form, so it must survive until dismissed.
    val xml = descriptor()
    assertTrue(
      "The Sprout notification group must use STICKY_BALLOON. Found:\n" +
        xml.lines().filter { it.contains("notificationGroup") }.joinToString("\n"),
      xml.contains("""<notificationGroup id="Sprout" displayType="STICKY_BALLOON"/>"""),
    )
  }
}
