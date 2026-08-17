package dev.sprout.intellij.lsp

import com.intellij.openapi.fileChooser.FileChooserDescriptorFactory
import com.intellij.openapi.options.BoundConfigurable
import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.DialogPanel
import com.intellij.openapi.ui.TextFieldWithBrowseButton
import com.intellij.ui.dsl.builder.bindText
import com.intellij.ui.dsl.builder.panel
import javax.swing.JComponent

/** Settings → Tools → Sprout. */
class SproutConfigurable(private val project: Project) : BoundConfigurable("Sprout") {

  override fun createPanel(): DialogPanel {
    val settings = SproutSettings.getInstance(project)
    val detected = settings.resolve(project)

    return panel {
      row("Language server (sproutd):") {
        cell(fileField("Select sproutd", executable = true))
          .bindText(settings::sproutdPath)
          .comment(
            detected?.let { "Leave empty to use the detected ${it.sproutd.absolutePath}" }
              ?: "Not detected — build one with <code>just build-sproutd</code> in a Sprout checkout.",
          )
      }
      row("Stdlib root:") {
        cell(fileField("Select the stdlib directory", executable = false))
          .bindText(settings::stdlibRoot)
          .comment(
            detected?.let { "Leave empty to use the detected ${it.stdlib.absolutePath}" }
              ?: "The <code>stdlib/</code> directory of the same checkout.",
          )
      }
      row("Package roots:") {
        cell(com.intellij.ui.components.JBTextField())
          .bindText(settings::packageRoots)
          .comment(
            "Extra roots for dotted non-stdlib imports, separated by " +
              "<code>${java.io.File.pathSeparator}</code>. Without these, names from such a " +
              "module are reported as unknown.",
          )
      }
    }
  }

  private fun fileField(title: String, executable: Boolean): TextFieldWithBrowseButton {
    val field = TextFieldWithBrowseButton()
    val descriptor =
      if (executable) FileChooserDescriptorFactory.createSingleFileDescriptor()
      else FileChooserDescriptorFactory.createSingleFolderDescriptor()
    descriptor.title = title
    field.addBrowseFolderListener(title, null, project, descriptor)
    return field
  }

  override fun apply() {
    super.apply()
    // The command line is built when the server starts, so an existing server keeps the
    // old paths. Say so rather than leaving the user wondering why nothing changed.
    com.intellij.notification.NotificationGroupManager.getInstance()
      .getNotificationGroup("Sprout")
      .createNotification(
        "Sprout settings saved",
        "Restart the language server (or reopen the project) for new paths to take effect.",
        com.intellij.notification.NotificationType.INFORMATION,
      )
      .notify(project)
  }

  override fun getPreferredFocusedComponent(): JComponent? = null
}
