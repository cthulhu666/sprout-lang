package dev.sprout.intellij.lsp

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

/**
 * Toolchain detection decides whether a user gets diagnostics or a warning balloon, and it
 * is the part of the LSP layer most likely to be quietly wrong — so it is a pure function
 * over a directory and tested without an IDE fixture.
 */
class SproutSettingsTest {

  @get:Rule
  val tmp = TemporaryFolder()

  /** A checkout with both halves present, and sproutd actually executable. */
  private fun checkout(root: File): File {
    File(root, "stdlib").mkdirs()
    val build = File(root, "build").apply { mkdirs() }
    val sproutd = File(build, "sproutd")
    sproutd.writeText("#!/bin/sh\n")
    sproutd.setExecutable(true)
    return root
  }

  @Test
  fun `detects a checkout in the starting directory`() {
    val root = checkout(tmp.newFolder("repo"))
    val found = SproutSettings.detectFrom(root)
    assertEquals(File(root, "build/sproutd"), found?.sproutd)
    assertEquals(File(root, "stdlib"), found?.stdlib)
  }

  @Test
  fun `walks up to find a checkout above the project directory`() {
    val root = checkout(tmp.newFolder("repo"))
    val nested = File(root, "examples/deep").apply { mkdirs() }
    assertEquals(File(root, "build/sproutd"), SproutSettings.detectFrom(nested)?.sproutd)
  }

  @Test
  fun `stops after the bounded number of levels`() {
    // Unbounded, this walk climbs to the filesystem root on every miss — once per opened
    // file. The bound is the reason it does not.
    val root = checkout(tmp.newFolder("repo"))
    var deep = root
    repeat(SproutSettings.MAX_WALK_UP + 2) { deep = File(deep, "d").apply { mkdirs() } }
    assertNull(SproutSettings.detectFrom(deep))
  }

  @Test
  fun `half a checkout is not a hit`() {
    // A sproutd with no stdlib beside it, or the reverse, is worse than nothing: paired
    // with someone else's stdlib it produces confidently wrong diagnostics.
    val onlyBinary = tmp.newFolder("only-binary")
    File(onlyBinary, "build").mkdirs()
    File(onlyBinary, "build/sproutd").apply { writeText(""); setExecutable(true) }
    assertNull(SproutSettings.detectFrom(onlyBinary))

    val onlyStdlib = tmp.newFolder("only-stdlib")
    File(onlyStdlib, "stdlib").mkdirs()
    assertNull(SproutSettings.detectFrom(onlyStdlib))
  }

  @Test
  fun `a non-executable sproutd is not a hit`() {
    val root = tmp.newFolder("unbuilt")
    File(root, "stdlib").mkdirs()
    File(root, "build").mkdirs()
    File(root, "build/sproutd").writeText("")
    assertNull(SproutSettings.detectFrom(root))
  }

  @Test
  fun `detection finds nothing in an unrelated tree`() {
    assertNull(SproutSettings.detectFrom(tmp.newFolder("unrelated")))
  }

  @Test
  fun `package roots split on the platform separator and drop blanks`() {
    val settings = SproutSettings()
    settings.packageRoots = listOf("/a", "", "  ", "/b").joinToString(File.pathSeparator)
    assertEquals(listOf("/a", "/b"), settings.packageRootList())
  }

  @Test
  fun `no package roots means no arguments`() {
    assertEquals(emptyList<String>(), SproutSettings().packageRootList())
  }
}
