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
  // Ultimate, because the LSP API ships only in commercial IDEs.
  //
  // But note what Ultimate does NOT give us: it carries the LSP *API* — its own bundled
  // plugins register providers with it — while declaring no `com.intellij.modules.lsp`
  // module, in either 2024.2.5 or 2025.1.7.2 (checked in product-info.json and across every
  // bundled XML descriptor). RubyMine 2025.2 declares it. Our optional <depends> keys on
  // that module, so the LSP half of the plugin never loads here and
  // SproutLspRegistrationTest can only report itself inert.
  //
  // Set SPROUT_IDE_HOME to an installed IDE that declares the module to make that test
  // real. The path stays out of the repo deliberately — it is local setup.
  intellijPlatform {
    val localIde = providers.environmentVariable("SPROUT_IDE_HOME").orNull
    if (localIde != null) {
      local(localIde)
    } else {
      create(
        IntelliJPlatformType.valueOf(providers.gradleProperty("platformType").get().let {
          if (it == "IU") "IntellijIdeaUltimate" else "IntellijIdeaCommunity"
        }),
        providers.gradleProperty("platformVersion").get(),
      )
    }
    pluginVerifier()
    testFramework(TestFrameworkType.Platform)
  }

  testImplementation("junit:junit:4.13.2")
}

// Tell the tests which IDE they are running against. SproutLspRegistrationTest asserts
// registration only when this is set, because the default platform cannot load the
// optional LSP descriptor at all and a failure there would say nothing about the plugin.
tasks.test {
  systemProperty("sprout.ide.home", providers.environmentVariable("SPROUT_IDE_HOME").orNull ?: "")
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
    // Commercial IDEs only, and deliberately NOT IntelliJ IDEA Community — measured, not
    // assumed. Verifying against Community reports `Package 'com.intellij.platform.lsp' is
    // not found` and FAILS the build. That problem is expected and harmless: the classes
    // referencing it are registered only in sprout-lsp.xml, which an IDE lacking the LSP
    // module never loads, so they are never classloaded. But the verifier cannot tell
    // "safely absent" from "will throw NoSuchClassError" — its own wording is "may be
    // caused by absence of optional dependency", leaving the judgement to the reader.
    //
    // A gate that is permanently red for a benign reason teaches people to ignore it. The
    // Community guarantee is enforced instead by `just plugin-split-check`, which asserts
    // the discriminator directly — only classes under dev.sprout.intellij.lsp may reference
    // that package — needs no IDE download, and runs in CI.
    ides {
      create(IntelliJPlatformType.IntellijIdeaUltimate, "2024.2.5")
      recommended()
    }
  }
}

tasks.test {
  useJUnit()
}
