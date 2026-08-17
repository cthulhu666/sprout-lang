package dev.sprout.intellij.lsp

import com.intellij.execution.configurations.GeneralCommandLine
import com.intellij.notification.NotificationGroupManager
import com.intellij.notification.NotificationType
import com.intellij.openapi.options.ShowSettingsUtil
import com.intellij.openapi.project.Project
import com.intellij.openapi.vfs.VirtualFile
import com.intellij.platform.lsp.api.LspServerSupportProvider
import com.intellij.platform.lsp.api.ProjectWideLspServerDescriptor
import dev.sprout.intellij.SproutFileType

/**
 * Starts `sproutd --lsp` for Sprout files.
 *
 * This whole file is reachable only when `com.intellij.modules.lsp` is present — an
 * optional dependency, declared in `sprout-lsp.xml`. In an IDE without that module (an
 * IntelliJ IDEA open-source build, Android Studio) the plugin still loads and the language
 * layer still works; nothing here is classloaded.
 */
internal class SproutLspServerSupportProvider : LspServerSupportProvider {
  override fun fileOpened(project: Project, file: VirtualFile, serverStarter: LspServerSupportProvider.LspServerStarter) {
    if (!isSproutFile(file)) return

    val resolved = SproutSettings.getInstance(project).resolve(project)
    if (resolved == null) {
      // Loud, once per project, with a way to act on it. A server that silently fails to
      // start is indistinguishable from a language with no diagnostics.
      notifyUnconfigured(project)
      return
    }
    serverStarter.ensureServerStarted(SproutLspServerDescriptor(project, resolved))
  }

  private fun notifyUnconfigured(project: Project) {
    if (notified.contains(project.locationHash)) return
    notified.add(project.locationHash)
    NotificationGroupManager.getInstance()
      .getNotificationGroup("Sprout")
      .createNotification(
        "Sprout language server not found",
        "Could not find <code>build/sproutd</code> and <code>stdlib/</code> near this project. " +
          "Diagnostics are unavailable until they are configured.",
        NotificationType.WARNING,
      )
      .addAction(object : com.intellij.openapi.actionSystem.AnAction("Configure…") {
        override fun actionPerformed(e: com.intellij.openapi.actionSystem.AnActionEvent) {
          ShowSettingsUtil.getInstance().showSettingsDialog(project, SproutConfigurable::class.java)
        }
      })
      .notify(project)
  }

  private companion object {
    val notified = java.util.concurrent.ConcurrentHashMap.newKeySet<String>()
  }
}

internal fun isSproutFile(file: VirtualFile): Boolean =
  file.fileType == SproutFileType || file.extension == "sprout" || file.extension == "spr"

private class SproutLspServerDescriptor(project: Project, private val paths: SproutSettings.Resolved) :
  ProjectWideLspServerDescriptor(project, "Sprout") {

  override fun isSupportedFile(file: VirtualFile) = isSproutFile(file)

  override fun createCommandLine(): GeneralCommandLine {
    val settings = SproutSettings.getInstance(project)
    val command = GeneralCommandLine(paths.sproutd.absolutePath, "--lsp", paths.stdlib.absolutePath)
    // Extra package roots let dotted non-stdlib imports (a game's `loam.*`, say) resolve.
    // Without them every name from such a module is reported as unknown.
    for (root in settings.packageRootList()) {
      command.addParameters("--package-root", root)
    }
    return command
  }
}
