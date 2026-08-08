# Test visibility (v0)

**Status: SHELVED — investigation only, no decision taken, nothing implemented.**

This document records what was learned while investigating how a Sprout test can
reach a module-private name. It surveys prior art (verified against primary
sources), enumerates the options with their measured costs, and names the one
concrete blocker that any file-based option must clear. It deliberately stops
short of a recommendation: see §9 for what remains open.

Backlog entry: "No test-only visibility, so a test oracle must be `export`ed and
ships in every consumer's IR" (`BACKLOG.md`, Double math section).

## 1. Problem statement

`stdlib/math.sprout` exports four functions that **no non-test code calls**:

```
412: export fn sqrt_strided(x: Double) -> Double
578: export fn exp_strided(x: Double) -> Double
676: export fn ln_strided(x: Double) -> Double
777: export fn cbrt_strided(x: Double) -> Double
```

Their only consumer is `tests/stdlib/test_math_wide_reduction.spr`. They are the
slow, obviously-correct stride-ladder implementations that the O(1) bit-access
reductions are differentially tested against — the mechanism that caught the
subnormal `ln` divergence and, after its input set was widened to non-finite
values, the `sqrt(NaN)` regression.

The technique works. The cost is that it permanently widens `stdlib.math`'s
public API with four functions that are not for users.

**The cost is not only cosmetic.** Exported functions are emitted into the IR of
any importing module *even under a selective import list*:
`tests/smoke_shapes/10_double_math.spr` imports exactly
`(sqrt, cbrt, exp, ln, pow)` and still carries 1136 lines of oracle IR, ~7% of
the file. Measured blast radius today is that one file — no shipped example
imports the Double `stdlib.math`; `examples/astar.sprout` imports
`stdlib.math.int` — so the cost is currently near-nil. It grows with every
consumer.

**Moving the oracles into the test file does not work.** They call
`sqrt_iter` / `cbrt_iter` / `exp_series` / `ln_series`, which are private.
Duplicating those into the test would make the oracle actively worse: a
legitimate series change would then read as a reduction divergence — a false
alarm in the one test meant to be trusted. Sharing the series and varying only
the reduction is the entire point of the oracle.

## 2. Goals and non-goals

**Goals**

- A test can call a module-private name without that name becoming public.
- Test code does not enter a normal library or application build — neither its
  IR nor its dependencies (a test needs `stdlib.test`; a library must not).
- The mechanism is enforced structurally, not by a lint or convention that can
  be forgotten.

**Non-goals**

- A general-purpose visibility hierarchy (`internal`, `protected`, package
  scope). That is a much larger language change and is not what this problem
  needs.
- Test *discovery* or a test-runner redesign. `just test` already globs
  `tests/**/*.spr` and works.
- Removing the `TestState`/`run_suite` duality in `stdlib/test.sprout` — a
  separate backlog item.

## 3. How Sprout visibility works today

This is the crux: **visibility in Sprout is not a type-system property.** It is
resolved by name-qualification in the bundler, before typechecking. Verified
2026-08-08 against the sources below.

1. **The parser throws `export` away.** `parser.sprout:1728` calls
   `parse_decl_body(tokens, skip_export(tokens, i))`; `skip_export`
   (`parser.sprout:1730`) matches the keyword and advances past it. `export`
   never reaches the AST.

2. **The bundler recovers it by scanning raw text.**
   `bundler.scan_source_info` (`bundler.sprout:261-263`) performs a line-based
   scan of the source and returns `(mod_name, exported_names, ctor_type_names)`.
   Those names are stored in `ParsedModule` (`bundler.sprout:23-26`) and then
   split in `ModuleSymbols` (`bundler.sprout:34`) into `value_locals` /
   `type_locals` / `class_locals` versus `exported_vals` / `exported_types` /
   `exported_cls`.

3. **Enforcement is name-binding, not a check.** `apply_one_import`
   (`bundler.sprout:666`) injects only a module's *exported* names into the
   importer's `ResolveCtx`. A private name is never rejected — it is simply
   never bound, so a reference to it fails as an unknown variable.

4. **Privates still land in the bundle.** `qualify_all_modules`
   (`bundler.sprout:1059`) qualifies **all** `decls` of every visited module,
   unfiltered by `exported`. A private function is renamed with its module
   prefix, typechecked, and lowered into the importer's IR even when nothing
   calls it.

Point 4 is why **co-locating tests in the library file cannot work**. If tests
lived in `stdlib/math.sprout`, every importer would carry the test bodies and
the `stdlib.test` dependency into its own binary. Dead code is not dropped at
the module boundary; it is dropped much later, if at all, by LLVM.

Two secondary facts constrain the design space:

- **The visibility model is binary.** No `export` = module-private; `export` =
  visible to every importer. There is no `internal`, no friend list, no package
  scope.
- **There is no attribute syntax.** No `@foo` / `#[foo]` in the lexer;
  "annotation" in Sprout means *type* annotation. Any marker-based design must
  add a keyword, which changes the reserved-word set — a breaking change.

## 4. What any solution must deliver

Three requirements. The options differ almost entirely in how many they get for
free rather than in how well they solve any one of them.

| # | Requirement | Question it answers |
|---|---|---|
| 1 | **Marker** | How do we know this code is a test? |
| 2 | **Exclusion** | How is it kept out of a normal build? |
| 3 | **Access** | How does it reach module-private names? |

## 5. Prior-art survey

Every row below was verified against a primary source on 2026-08-07/08. Quotes
are verbatim.

### 5.1 Java, classpath era (`src/main/java` + `src/test/java`)

Java's answer has three layers, and only the middle one does any work.

**The language does nothing.** The JLS has no notion of a test.
Package-private access is granted by *package name*, not by directory, so a
`src/main/java` class referencing a test class in the same package is legal
Java. Nothing in the language forbids it.

**The build tool makes the name unresolvable.** The classpath dependency is
deliberately one-directional
([Maven dependency mechanism](https://maven.apache.org/guides/introduction/introduction-to-dependency-mechanism.html)):

| Maven scope | compile CP | runtime CP | test CP |
|---|---|---|---|
| `compile` | ✓ | ✓ | ✓ |
| `test` | ✗ | ✗ | ✓ |

> "This scope indicates that the dependency is not required for normal use of
> the application, and is only available for the test compilation and execution
> phases."

`compiler:testCompile` outputs to `${project.build.testOutputDirectory}`
(`target/test-classes`), which is never placed on the main compile classpath.
Gradle has the same shape: `test`'s compile classpath includes `main`'s output;
`main`'s compile classpath has no path to `test`'s output. A `src/main/java`
file naming a test class fails with `cannot find symbol` — an ordinary
missing-name error, not a policy check.

**Packaging drops it.** `maven-jar-plugin` packages `target/classes` only, so
test classes are absent from the shipped artifact.

### 5.2 Java, JPMS era (Java 9+)

When Java gained real module identity, the same-package trick stopped being the
documented path.

**Qualified exports are a first-class friend mechanism.** From
[JLS SE 21 §7.7.2](https://docs.oracle.com/javase/specs/jls/se21/html/jls-7.html):

```java
exports com.example.foo.internal to com.example.foo.probe;
```

> "For a qualified directive, the `public` and `protected` types in the package,
> and their `public` and `protected` members, are accessible solely to code in
> the modules specified in the `to` clause. **The modules specified in the `to`
> clause are referred to as _friends_ of the current module.** For an
> unqualified directive, these types and their members are accessible to code in
> any module."

Note the spec's own worked example is an `.internal` package exported to a
`.probe` module — i.e. a test-access scenario.

**Split packages are a resolution failure.** The precise rule, from the
[`java.lang.module`](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/lang/module/package-summary.html)
spec's list of resolution-failure conditions:

> "Two or more modules export a package with the same name to a module that
> 'reads' both. This includes the case where a module M containing package p
> 'reads' another module that exports p to M."

This is narrower than a blanket ban on two modules containing a same-named
package — the JLS §7.7 imposes no such ban; the constraint lives in resolution.
But the second sentence is exactly the test case: a test module containing
`com.foo` that reads a main module exporting `com.foo` **is** a failure. Which
is why Maven Surefire's
[documented JPMS example](https://maven.apache.org/surefire/maven-surefire-plugin/examples/jpms.html)
gives main and test *separate* module descriptors (`module test { requires main; }`)
rather than reusing the package.

**Access for white-box tests moved to command-line flags**, not to source. From
the [`java`](https://docs.oracle.com/en/java/javase/21/docs/specs/man/java.html)
and [`javac`](https://docs.oracle.com/en/java/javase/21/docs/specs/man/javac.html)
tool references:

| option | documentation |
|---|---|
| `--add-exports M/p=target` | "Updates module to export package to target-module, regardless of module declaration." |
| `--add-opens M/p=target` | "Updates module to open package to target-module, regardless of module declaration." |
| `--add-reads M=target` | "Updates module to read the target-module, regardless of the module declaration." |
| `--patch-module M=file` | "Overrides or augments a module with classes and resources in JAR files or directories." javac adds: "to inject additional classes into the module, **such as when testing**." |

The lesson is where the mechanism ended up. `exports … to` exists and is the
"right" language-level answer, but no build tool asks you to write
`exports com.foo to my.tests` in a shipped `module-info.java` — that would bake
test structure into the production artifact. The access is granted at
*invocation time* by the build tool instead.

### 5.3 Rust

Rust solves marker, exclusion and access with two orthogonal features that
compose.

**Access falls out of the module tree.** From the
[Rust Reference, Visibility and Privacy](https://doc.rust-lang.org/reference/visibility-and-privacy.html):

> "If an item is private, it may be accessed by the current module and its
> descendants."

A `mod tests` nested inside the module under test is a *descendant*, so it sees
its parent's private items with no special rule at all.

**Exclusion is conditional compilation.** From
[The Book, ch. 11-03](https://doc.rust-lang.org/book/ch11-03-test-organization.html):

> "The `#[cfg(test)]` annotation on the `tests` module tells Rust to compile and
> run the test code only when you run `cargo test`, not when you run
> `cargo build`. This saves compile time when you only want to build the library
> and saves space in the resultant compiled artifact because the tests are not
> included."

and explicitly on the question at hand:

> "There's debate within the testing community about whether or not private
> functions should be tested directly, and other languages make it difficult or
> impossible to test private functions. Regardless of which testing ideology you
> adhere to, Rust's privacy rules do allow you to test private functions."

**The two-tier split is deliberate.** Integration tests live in `tests/`:

> "In Rust, integration tests are entirely external to your library. They use
> your library in the same way any other code would, which means they can only
> call functions that are part of your library's public API."

So Rust offers *both*: in-tree unit tests with private access, and out-of-tree
integration tests restricted to the public API. `#[cfg(test)]` is the direct
answer to §3 point 4 — it is the "don't emit this" mechanism Sprout lacks.

### 5.4 Go

Go's mechanism is filename-based and needs no visibility tier at all.

From [`cmd/go`](https://pkg.go.dev/cmd/go), Test packages:

> "'Go test' recompiles each package along with any files with names matching the
> file pattern "\*_test.go"."

> "Test files that declare a package with the suffix "_test" will be compiled as
> a separate package, and then linked and run with the main test binary."

So a `_test.go` file declaring `package foo` is compiled *into* `foo` and sees
its unexported identifiers; one declaring `package foo_test` is a separate
package restricted to the exported API — the same two-tier split as Rust, keyed
on the package clause instead of on directory.

The `export_test.go` convention builds on this. Verified verbatim from
[`src/fmt/export_test.go`](https://raw.githubusercontent.com/golang/go/master/src/fmt/export_test.go)
in the Go standard library:

```go
package fmt

var IsSpace = isSpace
var Parsenum = parsenum
```

Because the filename ends in `_test.go`, this file exists **only** during
`go test`. It re-exports unexported identifiers under exported aliases for the
external `fmt_test` package to use, and the aliases do not exist in the shipped
package. This is the closest prior art to Sprout's exact problem: the oracle
stays private, and a build-mode-conditional file grants access.

Go also has a **path-based visibility rule** worth recording, since it is the
only mainstream example of "the directory is the access modifier":

> "Packages in or under a directory named "internal" are importable only by code
> in the directory tree rooted at the parent of "internal"."

> "For example, a package .../a/internal/b/c can be imported only by code in the
> directory tree rooted at .../a."

### 5.5 Summary

| language | marker | exclusion | access |
|---|---|---|---|
| Java (classpath) | directory (`src/test/java`) | one-directional classpath, build tool | same package name |
| Java (JPMS) | separate module descriptor | module path, build tool | `exports … to` (friends), or `--patch-module` / `--add-exports` at invocation |
| Rust | `#[cfg(test)]` attribute | conditional compilation, in the compiler | child module sees parent's privates |
| Go | filename suffix `_test.go` | file-set selection, in the build tool | same package clause; `export_test.go` re-export aliases |

Two observations across all four:

- **Nobody puts test access in the shipped artifact.** Java's `exports … to`
  is the one language-level friend mechanism, and it is *not* what build tools
  use for tests.
- **Exclusion is always upstream of visibility.** In every case the test code is
  removed from the build before any access question is asked. Sprout currently
  has no such stage — §3 point 4.

## 6. Options for Sprout

### A. Mirrored `test/` tree, same module name, two roots

A test file at (say) `tests/stdlib/math.spr` declares `module stdlib.math`; the
loader accepts two files for one module name when both roots are present.

- *Marker*: free — root membership.
- *Exclusion*: **already true today.** `just test` compiles each `.spr` with
  `stdlib_root` only and no `--package-root` (`justfile:293`), so nothing
  outside `stdlib/` is reachable from a library build.
- *Access*: the two files *are* one module, so privates are visible with no new
  tier and no pairing rule.

This is Java's classpath model, and it collapses three requirements into one.
It is also the shape JPMS declares a resolution failure (§5.2), and Java's
answer to wanting it anyway was an explicit compiler flag. See §7 for the
blocker.

### B. Mirrored `test/` tree, distinct module name, structural rule

`tests/stdlib/math.spr` declares `module test.stdlib.math` and is granted
private access to `stdlib.math` by a structural rule ("a module at
`test.<X>` may see `<X>`'s privates").

Keeps module identity one-name-one-file. Costs an explicit pairing rule and a
new privileged-access path in `apply_one_import`.

### C. `export … to <module>` — qualified exports / friends

Direct port of JPMS §7.7.2. Two-token grammar extension on a prefix the parser
already skips; enforcement point is `apply_one_import`, which already filters by
exported-name set and would need that set keyed by importer.

Cheapest to implement of the access-granting options. But it writes the test
module's name into `stdlib/math.sprout` — trading four bogus exports for a
permanent source-level reference to the test tree. Note §5.5: no mainstream
build tool uses the language-level friend mechanism for tests.

### D. Go-style `export_test` companion file

A file that joins the module only in test builds and re-exports privates under
aliases. Needs no new visibility tier — only a "this extra file joins the module
during test builds" mechanism, which is the same loader change as option A, but
with the second file explicitly *additive* rather than a peer.

### E. Marker keyword (`test fn`, `test { … }`)

Follows the `export` precedent exactly — a skipped one-token prefix. Delivers
only requirement 1: exclusion still needs new bundler filtering and access still
needs a new tier. Adds a reserved word, a breaking change.

### F. Naming convention (`fn test_…`)

Free marker, no keyword, unenforceable, and steals a name prefix. Weakest.

### Rejected outright

- **Tests co-located in the library file** — killed by §3 point 4: every
  importer would carry the test bodies and the `stdlib.test` dependency.
- **Status quo (export the oracles)** — works, costs ~7% IR in one file today,
  grows with each consumer.

## 7. The blocker for any file-based option (A and D)

```sprout
export fn resolve_module_path(name, stdlib_root, extra_roots) -> Maybe String =
  match module_name_to_path(name, stdlib_root) with
  | Just path -> Just(path)          # every stdlib.* name lands here
  | Nothing -> try_extra_roots(name, extra_roots)
```

`module_loader.sprout:164`. `module_name_to_path` (`:148`) returns `Just` for
**every** `stdlib.*` name, so `extra_roots` is consulted only for dotted
non-stdlib names (the multi-repo package case). A test file declaring
`module stdlib.math` would be unreachable by name: the resolver short-circuits
on the library file and never looks further.

The comment above it states the constraint outright:

> "Pure — no filesystem existence check — so only the first extra root is
> consulted; multi-root disambiguation needs IO and is deferred."

So options A and D require exactly that deferred work: `resolve_module_path`
goes from `Maybe String` (first match wins) to a root-ordered *set* with real
existence checks, making it effectful. Downstream, `ParsedModule` /
`collect_modules` / `any_has_module_name` (`bundler.sprout:27-28, 484, 524`) all
assume one file per module name, and the `--emit-iface` path (`justfile:130`)
keys on module identity too.

**The sting:** that same change is what would make a *library* build able to see
the test half. So "nothing may import from the test tree" is **not** structural
under A or D — it is paid for by discipline about which roots go into the list.
Exactly as in Java. The invariant to write down and gate is:

> The test root appears in `extra_roots` only for a test-entry compile.

`extra_roots` is populated from exactly one place today — `--package-root` on
the driver CLI (`compile_driver.sprout:338, 344, 355`) — so that invariant is
currently cheap to hold and cheap to audit.

## 8. Impact sketch (not a plan)

Recorded so the next pass does not re-derive it.

- **Syntax**: options A, B, D add none. C adds `export <name> to <module>`.
  E adds a keyword.
- **Type system**: none. Visibility is resolved before typechecking (§3).
- **Diagnostics**: two new error classes for A/D — "module `X` found in two
  roots but the second is not a test root", and duplicate-definition across the
  two files. Note the typechecker currently does **not** catch a duplicate
  module-level `let`; it fails only at link time with
  `redefinition of global`. Any option that merges two files into one module
  makes that latent gap user-visible.
- **Compatibility**: `tests/stdlib/compiler/` already mirrors `stdlib/compiler/`,
  but filenames do not (`test_bundler.spr` vs `bundler.sprout`), and multi-aspect
  tests such as `test_math_wide_reduction.spr` have no single library
  counterpart. A mirrored scheme needs an answer for both.
- **Interaction**: the iface / precompiled-module arc depends on module
  identity; A changes that model and should not be decided independently of it.

## 9. Open questions — what is NOT decided

1. **Which option.** Direction was previously stated as A, but that was before
   §7 (the resolver blocker) and §5.4 (Go's `export_test.go`, i.e. option D)
   were known. D achieves the same result with an additive file rather than a
   change to module identity, and should be compared against A directly.
2. **Whether the problem is worth the cost now.** Measured blast radius is one
   file at ~7%. The status quo is defensible until a second consumer appears.
3. **Whether to keep the oracles at all** once the reductions are stable, or to
   pin their outputs as golden constants instead — which would dissolve the
   problem rather than solve it, at the cost of the property that makes the
   oracle valuable (it re-derives the answer independently rather than
   remembering it).
4. **JPMS adoption in the wider Java ecosystem** is *unverified*. The
   impression that most libraries ship as automatic modules rather than writing
   `module-info.java` has not been checked against a source and should not be
   weighted until it is. If true, it is evidence that heavyweight
   module-identity machinery earns its keep only when something forces the
   issue.

## 10. Where to resume

Start at §9 question 1: compare option A against option D on the single axis of
§7 — whether the second file is a *peer* (A, changes module identity) or an
*addition* (D, does not). Both need the same `resolve_module_path` change; only
A needs the `ParsedModule` / iface identity model to change with it.
