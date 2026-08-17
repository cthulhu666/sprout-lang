import org.jetbrains.intellij.platform.gradle.IntelliJPlatformType
import org.jetbrains.intellij.platform.gradle.TestFrameworkType

plugins {
  id("java")
  id("org.jetbrains.kotlin.jvm") version "2.1.20"
  id("org.jetbrains.intellij.platform") version "2.18.1"
}

group = providers.gradleProperty("pluginGroup").get()
version = providers.gradleProperty("pluginVersion").get()

repositories {
  mavenCentral()
  intellijPlatform { defaultRepositories() }
}

dependencies {
  // Compiled against IntelliJ IDEA **Community** on purpose. The language layer must run
  // in every IntelliJ-based IDE, and building against Community makes that structural: a
  // paid-only API would fail to compile here rather than fail to load in someone's
  // Community install. The LSP layer, which genuinely needs a commercial IDE, is added in
  // a later milestone and switches this to Ultimate.
  intellijPlatform {
    create(
      IntelliJPlatformType.IntellijIdeaCommunity,
      providers.gradleProperty("platformVersion").get(),
    )
    pluginVerifier()
    testFramework(TestFrameworkType.Platform)
  }

  testImplementation("junit:junit:4.13.2")
}

kotlin {
  // 21 for the whole supported range: the platform has required a Java 21 runtime since
  // 2024.2, which is this plugin's sinceBuild.
  jvmToolchain(21)
}

intellijPlatform {
  pluginConfiguration {
    name = providers.gradleProperty("pluginName")
    version = providers.gradleProperty("pluginVersion")
    description =
      """
      Sprout language support: syntax highlighting, comment handling, and — in
      commercial JetBrains IDEs — diagnostics from the Sprout compiler over LSP.
      """.trimIndent()

    ideaVersion {
      sinceBuild = providers.gradleProperty("pluginSinceBuild")
      // Left unset deliberately; see gradle.properties.
      untilBuild = provider { null }
    }

    vendor {
      name = "Sprout"
      url = "https://github.com/cthulhu666/sprout-lang"
    }
  }

  pluginVerification {
    ides { recommended() }
  }
}

tasks.test {
  useJUnit()
}
