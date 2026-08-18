package dev.sprout.intellij.lsp

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.io.File

/**
 * The banner's decision, tested as a pure function over values — no IDE fixture, same shape
 * as [SproutSettingsTest].
 *
 * Both cases here were observed in a real session on 2026-08-18: opening
 * `~/GameDev/uncharted-suns` in RubyMine produced no diagnostics and no navigation at all,
 * because the toolchain sat in an unrelated tree that [SproutSettings.detectFrom] cannot
 * reach and nothing had been configured. Configuring the two paths would then have exposed
 * the second case, since every `loam.*` import needs a package root.
 */
class SproutConfigIssueTest {

  /** Only nullness matters to the decision, so no filesystem is involved. */
  private val toolchain = SproutSettings.Resolved(File("/sprout/build/sproutd"), File("/sprout/stdlib"))

  private val gameFile = """
      module game.audio_state

      import stdlib.test (TestState)
      import loam.audio as audio
      import loam.mixer (Mix, unity)

      fn play() -> Unit = audio.load_sound("x")
  """.trimIndent()

  @Test
  fun `no toolchain outranks everything else`() {
    // Reported first because it is the stronger failure: without a server, the package-root
    // question cannot even arise. Naming the lesser one would send the user to fix the
    // wrong field.
    assertEquals(SproutConfigIssue.ToolchainMissing, diagnoseConfig(null, listOf("/game"), gameFile))
    assertEquals(SproutConfigIssue.ToolchainMissing, diagnoseConfig(null, emptyList(), "module m\n"))
  }

  @Test
  fun `a configured project with roots has no issue`() {
    assertNull(diagnoseConfig(toolchain, listOf("/game"), gameFile))
  }

  @Test
  fun `stdlib-only imports need no package root`() {
    val src = "module m\n\nimport stdlib.math (pi)\nimport stdlib.compiler.parser as parser\n"
    assertNull(diagnoseConfig(toolchain, emptyList(), src))
  }

  @Test
  fun `dotted non-stdlib imports without a root are reported`() {
    assertEquals(
      SproutConfigIssue.PackageRootsMissing(listOf("loam.audio", "loam.mixer")),
      diagnoseConfig(toolchain, emptyList(), gameFile),
    )
  }

  @Test
  fun `an undotted import resolves under the stdlib root`() {
    // module_name_to_path sends a name with no dot to <stdlib_root>/<name>.sprout, so it is
    // not evidence of a missing package root.
    assertEquals(emptyList<String>(), unresolvableImports("module m\n\nimport prelude\n"))
  }

  @Test
  fun `a commented-out import is not an import`() {
    // collect_imports has been broken by exactly this before; see the Sprout-side fix.
    val src = "module m\n\n# import loam.audio as audio\n  # import loam.mixer\n"
    assertEquals(emptyList<String>(), unresolvableImports(src))
  }

  @Test
  fun `the scan stops at the first declaration`() {
    // Bounds the scan to the header, so an `import`-shaped line inside a multi-line backtick
    // template cannot masquerade as one. Safe because no file in stdlib, examples, tests or
    // uncharted-suns places an import after a declaration — measured, not assumed.
    val src = """
        module m

        import stdlib.math (pi)

        fn doc() -> String = `
        import loam.smuggled
        `
    """.trimIndent()
    assertEquals(emptyList<String>(), unresolvableImports(src))
  }

  @Test
  fun `a repeated import is named once`() {
    val src = "module m\n\nimport loam.mixer (Mix)\nimport loam.mixer (Voice)\nimport loam.audio\n"
    assertEquals(listOf("loam.mixer", "loam.audio"), unresolvableImports(src))
  }

  @Test
  fun `an aliased import is stripped to its module name`() {
    assertEquals(listOf("loam.audio"), unresolvableImports("module m\n\nimport loam.audio as audio\n"))
  }

  @Test
  fun `a selective import is stripped to its module name`() {
    assertEquals(listOf("loam.mixer"), unresolvableImports("module m\n\nimport loam.mixer (Mix, unity)\n"))
    // No space before the parenthesis is still the same import.
    assertEquals(listOf("loam.mixer"), unresolvableImports("module m\n\nimport loam.mixer(Mix)\n"))
  }
}
