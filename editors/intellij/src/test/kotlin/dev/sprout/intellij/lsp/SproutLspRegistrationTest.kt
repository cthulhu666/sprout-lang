package dev.sprout.intellij.lsp

import com.intellij.openapi.extensions.ExtensionPointName
import com.intellij.testFramework.fixtures.BasePlatformTestCase

/**
 * The plugin's LSP provider must be registered at the extension point the platform
 * actually declares.
 *
 * This exists because the plugin shipped registering `<platformLspServerSupportProvider>`,
 * which under `defaultExtensionNs="com.intellij"` composes to
 * `com.intellij.platformLspServerSupportProvider` — an extension point no IDE declares.
 * The real name, read out of RubyMine's own `intellij.platform.lsp.xml`, is:
 *
 *     <extensionPoint qualifiedName="com.intellij.platform.lsp.serverSupportProvider"
 *                     interface="com.intellij.platform.lsp.api.LspServerSupportProvider"/>
 *
 * The consequence was total and silent: the provider was never instantiated, `fileOpened`
 * never ran, no server started, so there were no diagnostics, no hover and no navigation —
 * while syntax highlighting kept working, because the language layer registers against
 * different extension points in a different descriptor. Nothing in the build noticed.
 * `verifyPlugin` passed against 8 IDE builds: an extension tag that resolves to no
 * extension point is not an API misuse, so it is not the verifier's question to ask.
 *
 * READ THIS BEFORE TRUSTING A GREEN RUN. The build's IDE is IntelliJ IDEA Ultimate, and
 * neither 2024.2.5 nor 2025.1.7.2 declares `com.intellij.modules.lsp` — checked in their
 * `product-info.json` and across every bundled XML descriptor. RubyMine 2025.2 does. So in
 * this IDE the optional descriptor never loads and this test can only report that fact,
 * not the registration. [SproutLspDescriptorTest] is the gate that runs everywhere; see
 * `BACKLOG.md` for making this one real by testing against an IDE that provides the module.
 */
class SproutLspRegistrationTest : BasePlatformTestCase() {

  fun testProviderIsRegisteredWhenThePlatformProvidesLsp() {
    val ep = ExtensionPointName<Any>(LSP_SERVER_SUPPORT_PROVIDER_EP)
    val registered = ep.extensionList.map { it.javaClass.name }

    // The optional descriptor loads only where `com.intellij.modules.lsp` is declared, and
    // the default build platform does not declare it. That is a property of the test IDE,
    // not a defect, so it must not fail the build — but it must be visible, because a
    // silent pass here reads as verified registration.
    //
    // Keyed on SPROUT_IDE_HOME rather than on probing for the module: a module alias is
    // not a plugin, so it does not appear in PluginManagerCore.plugins, and the LSP API
    // classes ARE present in Ultimate, which makes Class.forName a false positive too.
    // Whether the run was pointed at a real IDE is the one thing known for certain.
    if (System.getProperty("sprout.ide.home").isNullOrEmpty()) {
      println(
        "SproutLspRegistrationTest: INERT — the default build platform declares no " +
          "com.intellij.modules.lsp, so the optional LSP descriptor did not load and " +
          "registration cannot be observed. Set SPROUT_IDE_HOME to an IDE that provides " +
          "it (RubyMine does) to assert for real. Extension point holds: $registered",
      )
      return
    }

    assertTrue(
      "SproutLspServerSupportProvider is not registered at " +
        "$LSP_SERVER_SUPPORT_PROVIDER_EP. Registered there: $registered",
      registered.contains("dev.sprout.intellij.lsp.SproutLspServerSupportProvider"),
    )
  }

  /**
   * The control. The language layer lives in the MAIN descriptor, so this passing while the
   * check above stays inert tells us the plugin really is loaded here and only the optional
   * half is missing. If this ever fails, no other assertion in this file means anything.
   */
  fun testLanguageLayerIsRegistered() {
    val fileType = com.intellij.openapi.fileTypes.FileTypeManager.getInstance()
      .getFileTypeByExtension("sprout")
    assertEquals(
      "The .sprout file type is not registered, so the plugin's own plugin.xml did not " +
        "load here and nothing else in this file can be trusted.",
      "Sprout",
      fileType.name,
    )
  }

  private companion object {
    const val LSP_SERVER_SUPPORT_PROVIDER_EP = "com.intellij.platform.lsp.serverSupportProvider"
  }
}
