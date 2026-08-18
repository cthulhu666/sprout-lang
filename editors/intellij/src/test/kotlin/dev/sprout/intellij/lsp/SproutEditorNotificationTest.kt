package dev.sprout.intellij.lsp

import com.intellij.openapi.fileEditor.FileEditorManager
import com.intellij.testFramework.fixtures.BasePlatformTestCase

/**
 * Runs the provider itself, not just the decision it delegates to.
 *
 * [SproutConfigIssueTest] covers `diagnoseConfig` and [SproutLspDescriptorTest] covers the
 * registration, but neither would notice the provider throwing, returning null for a Sprout
 * file, or producing a panel with no text — the class of failure that let a wrong extension
 * point name ship once already. A fixture project has no Sprout checkout above it, so the
 * unconfigured state is the one it naturally reproduces.
 */
class SproutEditorNotificationTest : BasePlatformTestCase() {

  fun testAnUnconfiguredProjectGetsABanner() {
    val file = myFixture.configureByText("a.sprout", "module m\n\nfn f() -> Int = 1\n").virtualFile
    val data = SproutEditorNotificationProvider().collectNotificationData(project, file)
    assertNotNull(
      "No banner for a Sprout file in a project with no toolchain configured or detectable — " +
        "the state that reads, from the editor, as a language with no diagnostics.",
      data,
    )

    val editor = FileEditorManager.getInstance(project).getAllEditors(file).first()
    val panel = data!!.apply(editor)
    assertNotNull("The provider returned a function that produces no component.", panel)
    val text = (panel as com.intellij.ui.EditorNotificationPanel).text
    assertTrue(
      "The banner must name Sprout and the missing piece, not just appear. Got: $text",
      text.contains("Sprout") && text.contains("language server"),
    )
  }

  fun testANonSproutFileGetsNoBanner() {
    // The control. Without it, a provider that banners every file would pass the test above.
    val file = myFixture.configureByText("a.txt", "hello\n").virtualFile
    assertNull(SproutEditorNotificationProvider().collectNotificationData(project, file))
  }

  fun testAConfiguredProjectGetsNoBanner() {
    val settings = SproutSettings.getInstance(project)
    val checkout: java.io.File = java.nio.file.Files.createTempDirectory("sprout-checkout").toFile()
    val stdlib = java.io.File(checkout, "stdlib").apply { mkdirs() }
    val sproutd = java.io.File(checkout, "build/sproutd").apply {
      parentFile.mkdirs()
      writeText("#!/bin/sh\n")
      setExecutable(true)
    }
    settings.sproutdPath = sproutd.absolutePath
    settings.stdlibRoot = stdlib.absolutePath
    try {
      val file = myFixture.configureByText("b.sprout", "module m\n\nimport stdlib.math (pi)\n").virtualFile
      assertNull(
        "A configured project with only stdlib imports has nothing to report.",
        SproutEditorNotificationProvider().collectNotificationData(project, file),
      )

      // …and the second state: the toolchain is fine, but a dotted non-stdlib import cannot
      // resolve without a package root, which otherwise surfaces only as unknown names.
      val game = myFixture.configureByText("c.sprout", "module m\n\nimport loam.audio as audio\n").virtualFile
      assertNotNull(
        "No banner for an import that cannot resolve without a package root.",
        SproutEditorNotificationProvider().collectNotificationData(project, game),
      )
    } finally {
      settings.sproutdPath = ""
      settings.stdlibRoot = ""
      checkout.deleteRecursively()
    }
  }
}
