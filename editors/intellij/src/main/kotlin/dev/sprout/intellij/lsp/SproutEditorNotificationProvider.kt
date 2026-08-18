package dev.sprout.intellij.lsp

import com.intellij.openapi.fileEditor.FileDocumentManager
import com.intellij.openapi.fileEditor.FileEditor
import com.intellij.openapi.options.ShowSettingsUtil
import com.intellij.openapi.project.Project
import com.intellij.openapi.vfs.VirtualFile
import com.intellij.ui.EditorNotificationPanel
import com.intellij.ui.EditorNotificationProvider
import java.util.function.Function
import javax.swing.JComponent

/**
 * A banner across the top of a Sprout file that is not being analysed because the plugin is
 * not configured.
 *
 * A balloon already reported the toolchain case and was missed — which is what balloons do:
 * they fire once, off to the side, and auto-hide. The observed result was a whole project
 * read as "the language has no diagnostics". A banner is attached to the file that cannot be
 * analysed and stays until the cause is gone, so the absence can no longer be mistaken for
 * an answer. The balloon is kept as well; the two are complementary, not redundant.
 *
 * Registered in `sprout-lsp.xml`, not the language descriptor. An IDE without
 * `com.intellij.modules.lsp` never starts a server, so it has nothing to report and no
 * settings page to send the user to.
 */
internal class SproutEditorNotificationProvider : EditorNotificationProvider {

  override fun collectNotificationData(
    project: Project,
    file: VirtualFile,
  ): Function<in FileEditor, out JComponent?>? {
    if (!isSproutFile(file)) return null
    val settings = SproutSettings.getInstance(project)
    // Only the import header is read, and only when no package root is set; a cached
    // document avoids touching disk for a file that is, by definition, open in an editor.
    val source = FileDocumentManager.getInstance().getCachedDocument(file)?.charsSequence ?: ""
    val issue = diagnoseConfig(settings.resolve(project), settings.packageRootList(), source) ?: return null

    return Function { editor ->
      EditorNotificationPanel(editor, EditorNotificationPanel.Status.Warning).apply {
        text = describe(issue)
        createActionLabel("Configure…") {
          ShowSettingsUtil.getInstance().showSettingsDialog(project, SproutConfigurable::class.java)
        }
      }
    }
  }

  private fun describe(issue: SproutConfigIssue): String = when (issue) {
    SproutConfigIssue.ToolchainMissing ->
      "Sprout: no language server configured, so this file gets no diagnostics or navigation. " +
        "Set the sproutd binary and stdlib directory of a Sprout checkout."

    is SproutConfigIssue.PackageRootsMissing ->
      "Sprout: no package root configured, so ${summarise(issue.modules)} cannot be resolved " +
        "and every name from them will be reported as unknown."
  }

  /** Enough modules to recognise the project, not so many that the banner wraps. */
  private fun summarise(modules: List<String>): String {
    val shown = modules.take(MAX_MODULES_SHOWN).joinToString(", ")
    val rest = modules.size - MAX_MODULES_SHOWN
    return if (rest > 0) "$shown and $rest more" else shown
  }

  private companion object {
    const val MAX_MODULES_SHOWN = 3
  }
}
