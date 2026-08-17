package dev.sprout.intellij.lsp

import com.intellij.openapi.components.PersistentStateComponent
import com.intellij.openapi.components.Service
import com.intellij.openapi.components.State
import com.intellij.openapi.components.Storage
import com.intellij.openapi.components.service
import com.intellij.openapi.project.Project
import com.intellij.openapi.project.guessProjectDir
import java.io.File

/**
 * Where to find a Sprout toolchain.
 *
 * No release ships `sproutd`, a stdlib tree, or a macOS build, so the plugin cannot
 * download a server — it has to be pointed at a checkout. Rather than demand configuration
 * up front, [detect] walks up from the project directory looking for the pair that must
 * travel together: a built `build/sproutd` and the `stdlib/` it was built against.
 */
@Service(Service.Level.PROJECT)
@State(name = "SproutSettings", storages = [Storage("sprout.xml")])
class SproutSettings : PersistentStateComponent<SproutSettings.State> {

  data class State(
    var sproutdPath: String = "",
    var stdlibRoot: String = "",
    /** Extra roots for non-stdlib dotted imports, `File.pathSeparator`-joined. */
    var packageRoots: String = "",
  )

  private var state = State()

  override fun getState() = state
  override fun loadState(loaded: State) {
    state = loaded
  }

  var sproutdPath: String
    get() = state.sproutdPath
    set(value) { state.sproutdPath = value }

  var stdlibRoot: String
    get() = state.stdlibRoot
    set(value) { state.stdlibRoot = value }

  var packageRoots: String
    get() = state.packageRoots
    set(value) { state.packageRoots = value }

  fun packageRootList(): List<String> =
    state.packageRoots.split(File.pathSeparatorChar).map { it.trim() }.filter { it.isNotEmpty() }

  /** Configured or detected paths, or null when neither yields a usable pair. */
  fun resolve(project: Project): Resolved? {
    val configured = Resolved(File(state.sproutdPath), File(state.stdlibRoot))
    if (configured.isUsable()) return configured
    val start = project.guessProjectDir()?.let { File(it.path) } ?: return null
    return detectFrom(start)
  }

  data class Resolved(val sproutd: File, val stdlib: File) {
    fun isUsable() = sproutd.isFile && sproutd.canExecute() && stdlib.isDirectory
  }

  companion object {
    /**
     * A Sprout checkout is never far above a project root, so the walk is bounded — an
     * unbounded one climbs to `/` on every miss, once per opened file.
     */
    const val MAX_WALK_UP = 6

    /**
     * Walk up from [start] looking for the pair that must travel together: a built
     * `build/sproutd` and the `stdlib/` it was built against. Finding one without the
     * other is not a hit — a stale binary pointed at someone else's stdlib is worse than
     * no server, because its diagnostics would be confidently wrong.
     *
     * Takes a [File] rather than a Project so it is testable without an IDE fixture.
     */
    fun detectFrom(start: File): Resolved? {
      var dir: File? = start
      repeat(MAX_WALK_UP) {
        val here = dir ?: return null
        val candidate = Resolved(File(here, "build/sproutd"), File(here, "stdlib"))
        if (candidate.isUsable()) return candidate
        dir = here.parentFile
      }
      return null
    }

    fun getInstance(project: Project): SproutSettings = project.service()
  }
}
