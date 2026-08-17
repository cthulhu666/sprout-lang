// Standalone Gradle build, not part of any other build in this repo — the rest of the
// project is clang/LLVM driven and has no Gradle root to attach to.
plugins {
  id("org.gradle.toolchains.foojay-resolver-convention") version "1.0.0"
}

rootProject.name = "sprout-intellij"
