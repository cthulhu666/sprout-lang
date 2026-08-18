package dev.sprout.intellij.lsp

/**
 * Why a Sprout file is not being analysed, when the reason is configuration rather than the
 * code.
 *
 * Both cases are silent by construction, which is why they are worth naming. Neither
 * produces a diagnostic: the first stops the server from ever starting, and the second
 * starts one that cannot see half the project.
 */
sealed interface SproutConfigIssue {

  /**
   * No `sproutd` + `stdlib/` pair is configured or detectable, so
   * [SproutLspServerSupportProvider.fileOpened] returns before starting anything. The file
   * gets no diagnostics, no hover and no navigation — indistinguishable, from the editor,
   * from a language server that simply has nothing to say.
   */
  data object ToolchainMissing : SproutConfigIssue

  /**
   * A server runs, but [modules] are dotted non-`stdlib` names, and `resolve_module_path`
   * (`stdlib/compiler/module_loader.sprout`) resolves those *only* under an extra package
   * root. With none configured, every name from them reads as unknown — a wall of errors
   * about the code, whose actual cause is an empty settings field.
   */
  data class PackageRootsMissing(val modules: List<String>) : SproutConfigIssue
}

/**
 * What, if anything, stops [source] from being analysed. `null` means nothing does.
 *
 * Pure, over values rather than a Project, so the decision is testable without an IDE
 * fixture — the same shape as [SproutSettings.detectFrom], and for the same reason: this is
 * the logic most likely to be quietly wrong.
 */
fun diagnoseConfig(
  toolchain: SproutSettings.Resolved?,
  packageRoots: List<String>,
  source: CharSequence,
): SproutConfigIssue? {
  // Ordered by which failure dominates: with no server running, whether a package root is
  // set changes nothing, and naming it would send the user to the wrong field.
  if (toolchain == null) return SproutConfigIssue.ToolchainMissing
  if (packageRoots.isNotEmpty()) return null
  val modules = unresolvableImports(source)
  return if (modules.isEmpty()) null else SproutConfigIssue.PackageRootsMissing(modules)
}

/**
 * Dotted, non-`stdlib` module names imported by [source], in first-appearance order and
 * without duplicates. These are exactly the imports that need a package root.
 */
fun unresolvableImports(source: CharSequence): List<String> {
  val found = LinkedHashSet<String>()
  for (line in source.lineSequence()) {
    val trimmed = line.trim()
    if (trimmed.isEmpty() || trimmed.startsWith("#")) continue
    // Imports precede declarations in every file across stdlib, examples, tests and
    // uncharted-suns, so stopping here bounds the scan to the header — which is what keeps
    // an `import`-shaped line inside a multi-line backtick template from counting.
    if (DECLARATION_START.containsMatchIn(line)) break
    val module = importedModule(trimmed) ?: continue
    if (module.contains('.') && !module.startsWith(STDLIB_PREFIX)) found.add(module)
  }
  return found.toList()
}

/**
 * The module name in an `import` line, or null if the line is not one.
 *
 * Stops at whitespace or `(`, which covers all three written forms: plain,
 * `… as alias`, and a selective `… (Name, Other)` with or without a space.
 */
private fun importedModule(trimmed: String): String? {
  if (!trimmed.startsWith(IMPORT_KEYWORD)) return null
  val rest = trimmed.substring(IMPORT_KEYWORD.length)
  if (rest.isEmpty() || !rest[0].isWhitespace()) return null
  return rest.trimStart().takeWhile { !it.isWhitespace() && it != '(' }.ifEmpty { null }
}

private const val IMPORT_KEYWORD = "import"

/** Only `stdlib.*` and undotted names resolve without an extra root; see `module_name_to_path`. */
private const val STDLIB_PREFIX = "stdlib."

/** Anchored at column 0, where Sprout's top-level declarations begin. */
private val DECLARATION_START =
  Regex("""^(fn|type|export|class|instance|alias|record|extern|derive)\b""")
