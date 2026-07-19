# Multi-repo packaging & cross-package coherence — v0 design draft

**Status:** experimental design draft. **Non-normative** — records a recommended
direction and its evidence; it does not amend `docs/spec-v0.md`. Implementation
is future work, gated on the approval step in [§10](#10-implementation-overview-gated-on-approval).

**Origin:** reviving `sprout-postgres` (a Postgres client library that lives in a
separate repo and depends on this one) surfaced that the compiler has **no
concept of an external package**. `module_loader.module_name_to_path` resolves
only `stdlib.*` and bare dot-less names against a single `stdlib_root`; every
`import postgres.wire`-style app module resolves to `Nothing`. That local
symptom ("Category A") is the degenerate, single-package case of a general gap:
Sprout has no story for a production setup where a library, an application, and
other libraries each live in their own repo and version independently.

**Related in-repo work this builds on:**
- `docs/iface-precompiled-modules-v1-draft.md` — the `.iface`/`.bc` typed-artifact
  seam (TASTy/TTC-style), the separate-compilation substrate a package system reuses.
- `docs/module-qualified-type-identity-design-2026-07-10.md` + the canonical
  tyvar/binding identity campaign — package-qualified identity is their next extension.
- `docs/haskell-lessons-learned.md` §4 (orphan instances) — the coherence stance
  this doc formalizes into a cross-version rule.

---

## 1. Problem statement

A production-grade Sprout deployment is multi-repo: an application repo depends
on library repos (`sprout-postgres`, others), which transitively depend on more
libraries, all versioned independently. The compiler must be able to:

1. **Resolve** modules across package boundaries (not just under one `stdlib_root`).
2. **Version** those dependencies with a real resolver and a lockfile.
3. Preserve **typeclass coherence** — the "one instance per (class, type)" property
   Sprout is committed to — *across independently-versioned packages*, which is
   where the single-version-world assumption that makes coherence trivial breaks.
4. Do all of this **reproducibly** and with a shared compiler/stdlib version,
   since name-mangling and ABI are compiler-version-specific (the bootstrap-seed
   fingerprint).

The hard part is #3. #1, #2 and #4 are the generic package-manager machinery every
language builds; they are comparatively mechanical *once the identity and coherence
rules are fixed*. This doc leads with the semantics constraint and lets the
machinery follow from it.

## 2. Goals and non-goals

**Goals**
- A package identity model that keeps typeclass coherence sound across repos.
- Deterministic, reproducible builds: manifest + resolver + lockfile with integrity hashes.
- Git-based sourcing (no hosted registry required to start).
- Reuse the `.iface`/`.bc` artifact cache for per-package separate compilation.
- Beginner-friendly failure modes: conflicts are *loud, explicit, and early*, never silent.

**Non-goals (v0)**
- A hosted package registry / index. Git tags are the sourcing mechanism; a
  registry is a later, separate concern once an ecosystem justifies it.
- Cross-machine distribution of compiled artifacts. Ship source, build locally,
  cache artifacts locally (consistent with the `.iface` draft's v1 non-goals).
- Multiple *incompatible* versions of one package silently coexisting in a build
  (see the decision in [§5.2](#52-versioning-single-version-selection)).
- Changing the coherence rule for the single-package case (that stays as-is).

## 3. Prior-art survey

Eight ecosystems, every load-bearing claim cited to a **primary source** (language
reference / official package-manager docs / normative spec). Two axes structure the
survey: **(A) is coherence compiler-enforced?** and **(B) can two versions of one
package coexist in a build?**

| Language | (A) Coherence enforced? | (B) Multi-version in one build? | Failure mode of its choice |
|---|---|---|---|
| **Rust** | Yes — orphan rule + no-overlap | Yes; v1/v2 are **distinct types** | "same name, different type" + runtime downcast failures |
| **Go** | N/A (structural interfaces) | No within a major; `/v2` = different module path | breaking change ⇒ new import path everywhere |
| **Elm** | N/A (no typeclasses) | No — one **exact** version, enforced semver | API-shape semver only (not behavioral) |
| **OCaml** | By construction (explicit modules) | Safe (no global instances) | no ergonomic implicit dispatch |
| **Haskell** | **No** — orphans only *warned* | one per solved plan; Stackage curates | silent incoherence; libraries mutually unusable |
| **Scala** | **No** — import/lexically scoped | one per classpath (latest-wins eviction) | resolution depends on imports; runtime `LinkageError` |
| **Java** | N/A (nominal interfaces) | No on a classpath; OSGi via classloaders | "JAR hell"; nearest-wins is silent/surprising |
| **Python** | N/A (dynamic) | No per environment; venvs isolate | decade of silent broken installs pre-resolver |
| **npm** | No | **Yes**, unrestricted, distinct runtime copies | duplicate-package runtime failures (e.g. two React) |

### 3.1 The coherent-with-typeclasses pole — Rust

- Coherence is enforced: *"A trait implementation is considered incoherent if either
  the orphan rules check fails or there are overlapping implementation instances."*
  and *"a trait implementation is only allowed if either the trait or at least one of
  the types … is defined in the current crate."*
  — Rust Reference, *Trait implementation coherence / Orphan rules*
  (https://doc.rust-lang.org/reference/items/implementations.html)
- Multiple semver-incompatible versions coexist, and their types are **distinct**:
  *"the types and items are considered different by the Rust compiler, even if they
  have the same name."* A cross-version downcast *"will fail at runtime."*
  — The Cargo Book, *Version-incompatibility hazards*
  (https://doc.rust-lang.org/cargo/reference/resolver.html)
- **Lesson:** typeclasses + duplicate versions *is* achievable, but the price is the
  "expected `PgValue`, found `PgValue` (different versions)" trap — precisely the
  beginner-hostile error Sprout wants to avoid.

### 3.2 The single-version poles — Go, Elm

- Go, Minimal Version Selection keeps *"only the newest version of any listed
  module"*; incompatible majors coexist only as **different module paths**:
  *"Starting with major version 2, module paths must have a major version suffix
  like `/v2`."* — Go Modules Reference
  (https://go.dev/ref/mod). Interfaces are structural (no instances), so no coherence
  concern — Go Spec (https://go.dev/ref/spec).
- Elm mechanically enforces semver and pins exactly one version: *"Elm automatically
  enforces semantic versioning by comparing API changes"* (github.com/elm/compiler
  `docs/elm.json/package.md`); an application's `elm.json` uses *"exact versions, so
  your elm.json file doubles as a 'lock file'"* (`docs/elm.json/application.md`).
  Caveat (verified): the diff checks the *public API surface*, not runtime behavior.
- **Lesson:** one version per package ⇒ a type has one identity ⇒ coherence costs
  almost nothing. This is the corner beginner-friendly languages deliberately choose.

### 3.3 The permissive-coherence cautionary tales — Haskell, Scala

- Haskell/GHC does **not** enforce global coherence. Orphans are *permitted*, only
  `-Worphans`-warned (https://downloads.haskell.org/ghc/latest/docs/users_guide/using-warnings.html).
  The Haskell Wiki states the composition hazard: *"If two instances for the same
  class/type pair are in scope, then you cannot describe in Haskell 98 which instance
  to use … you have to ensure that they are never imported together"*
  (https://wiki.haskell.org/Orphan_instance). And `INCOHERENT` is documented to return
  *"an arbitrary surviving candidate"* — a program type-checks while silently using an
  unintended instance
  (https://downloads.haskell.org/ghc/latest/docs/users_guide/exts/instances.html).
- The ecosystem patched *version* conflicts via curation — Stackage is *"a distribution
  of compatible Haskell packages … chosen at a version to make the set self-consistent"*
  (https://www.stackage.org/) — but curation does **not** fix *instance* incoherence.
  The two are orthogonal; only one got a fix.
- Scala 3 givens are import-scoped by design (`import A.given` required separately):
  the same (type, class) can resolve to different implementations in different scopes
  (https://docs.scala-lang.org/scala3/reference/contextual/given-imports.html). On the
  JVM classpath a fully-qualified name is one class; sbt evicts to latest-wins and, from
  1.5.0, errors on incompatible versionScheme
  (https://www.scala-sbt.org/1.x/docs/Library-Management.html).
- **Lesson:** permissive coherence produced silent, hard-to-diagnose failures for
  decades; neither language enforces the property. Direct evidence for enforcing
  coherence at *compile time* rather than via warnings or curation.

### 3.4 The decades-in-industry giants — Java, Python

- **Java.** Class identity is the pair `(binary name, defining loader)`, and a loader
  returns one class per name — so a flat classpath holds one version ("JAR hell")
  (JVMS §5.3, https://docs.oracle.com/javase/specs/jvms/se17/html/jvms-5.html). Maven
  institutionalizes single-version selection by tree depth: *"Maven picks the 'nearest
  definition' … if two are at the same depth, the first declaration wins"*
  (https://maven.apache.org/guides/introduction/introduction-to-dependency-mechanism.html)
  — topology/order-driven, so the *older* version can silently win; `<dependencyManagement>`
  is the manual override. Multi-version is possible only via per-bundle classloaders
  (OSGi: *"multiple versions of the same class … loaded in the same VM"*,
  https://docs.osgi.org/specification/osgi.core/8.0.0/framework.module.html) or by
  **renaming** (Maven Shade relocation,
  https://maven.apache.org/plugins/maven-shade-plugin/examples/class-relocation.html) —
  both heavyweight, both fork identity per conflict. The module system (Jigsaw) moved the
  *opposite* way from multi-version: split packages are a hard resolution failure —
  *"resolution fails if two or more modules export the same package to a module that reads
  both"*
  (https://docs.oracle.com/en/java/javase/17/docs/api/java.base/java/lang/module/Configuration.html;
  the JEP 261 page itself returned HTTP 403 and its verbatim text is UNVERIFIED, but the
  normative rule is confirmed by this API spec).
- **Python.** One version per environment: *"At most, one Distribution for a project is
  possible in a working set"* (https://packaging.python.org/en/latest/glossary/). Multiple
  versions are pushed to isolated venvs — *"each with their own independent set of packages
  … isolated from the base environment"* (https://docs.python.org/3/library/venv.html). The
  decade-long pain was **not** the model but the *absence of a real resolver*: pip could
  *"install a package which does not satisfy the declared requirements of another installed
  package"* until pip 20.3 (2020) added backtracking that *"will no longer install a
  combination of packages that is mutually inconsistent … it will refuse"*
  (https://pip.pypa.io/en/stable/user_guide/#changes-to-the-pip-dependency-resolver-in-20-3-2020).
  Versioning is PEP 440; reproducibility via pinning/lockfiles (`pip freeze`, pip-tools,
  Poetry) (https://peps.python.org/pep-0440/,
  https://packaging.python.org/en/latest/tutorials/managing-dependencies/).
- **Lesson:** the two highest-volume production ecosystems both converge on
  **single-version-per-build**, and their most expensive lesson is that doing it
  **silently/implicitly** (Maven nearest-wins; pip pre-2020) is the real trap. Every
  mature correction made conflicts **loud, checked, explicit** (Jigsaw no-split-packages;
  pip 20.3 resolver).

### 3.5 The anti-pattern — npm; the sidestep — OCaml

- npm nests `node_modules` and installs *"separate copies when version requirements
  conflict"* (https://docs.npmjs.com/cli/v10/configuring-npm/folders). Unrestricted
  multi-version with **no static identity** yields the canonical failure — duplicate
  React: *"the `react` import … needs to resolve to the same module … If these resolve
  to two different exports objects, you will see this warning"*
  (https://react.dev/warnings/invalid-hook-call-warning). This is version-qualified
  identity *without* coherence, deferred to runtime.
- OCaml passes implementations explicitly via functors/first-class modules
  (https://ocaml.org/manual/5.2/moduleexamples.html,
  https://ocaml.org/manual/5.2/firstclassmodules.html) — no orphan/coherence problem by
  construction; "modular implicits" is not shipped (absent from the 5.2 manual).

### 3.6 Convergent conclusion

Across all eight: **every ecosystem lands on single-version-per-build.** Multi-version
coexistence appears only as (a) an anti-pattern with runtime failures (npm), (b) something
requiring *distinct static identity* + heavyweight machinery, paying the cost at the type
boundary (Rust, OSGi, shading), or (c) pushed out to isolation (Python venvs, Go `/v2`).
Separately, the decades-long *pain* was never the single-version choice — it was making it
**silent**. The universal correction is **loud, checked, explicit** conflict handling.

> **Sourcing note.** One widely-repeated argument against orphans — "two `Set`s ordered by
> different `Ord` instances corrupt each other" — could not be tied to a primary source. The
> *mechanism* is verified (Haskell Wiki global-uniqueness statement + GHC `INCOHERENT`
> arbitrary-candidate wording); the specific data-corruption anecdote is **excluded as
> unverified.**

## 4. Sprout's forcing constraint

Sprout is statically typed, has typeclasses, enforces coherence, and targets
beginner-friendliness. That combination is the *most demanding* corner of the survey:

- Python could survive silently installing a wrong version for a decade **because it is
  dynamically typed** — nothing checked cross-package consistency at build time.
- Sprout has the opposite obligation: two versions of a package carrying "the same"
  instance is a **soundness** hazard, not merely a runtime surprise.

Therefore the lesson the giants paid for in production translates, for Sprout, into a
*hard requirement*, not an optimization: **single-version selection with a real, loud
resolver from day one.**

## 5. Design decisions

### 5.1 Coherence: enforce at compile time (strict orphan rule)

Keep and formalize the strict orphan rule (an `instance (C, T)` is legal only in the
package defining `C` or the package defining `T`), extended to hold **across** packages.
Rust is the success case; Haskell/Scala are the cautionary permissive ones. This is the
cross-version generalization of the stance already in `docs/haskell-lessons-learned.md` §4.

### 5.2 Versioning: single-version selection

Resolve exactly one version of each package identity for the whole build. Chosen over
Rust-style version-qualified identity because:
- It keeps coherence sound *cheaply* — one version ⇒ one type identity ⇒ one instance.
- It avoids Rust's "same name, different type" beginner trap ([§3.1](#31-the-coherent-with-typeclasses-pole--rust)).
- It is what Java, Python, Go, and Elm all converged on ([§3.2](#32-the-single-version-poles--go-elm), [§3.4](#34-the-decades-in-industry-giants--java-python)).

**Alternative considered and rejected for v0: version-qualified identity (Rust).** It
works and is sound, but imports the beginner-hostile error and has zero positive precedent
among the mainstream giants (Java and Python both refused it, coping via isolation). Left
on record; revisitable if a concrete need for in-build multi-version emerges.

### 5.3 Resolver: real and loud from day one

A resolver that computes one mutually-consistent version set and **errors explicitly** on
unsatisfiable constraints — never "nearest wins," never "install and hope." This is the
single most-supported lesson from Java + Python ([§3.4](#34-the-decades-in-industry-giants--java-python)).
For a coherent typeclass language it is mandatory, because a wrong-version silent success
is a soundness bug.

### 5.4 Incompatible majors: distinct package identity, made explicit

An incompatible major upgrade is modeled as a **distinct package identity**, surfaced
explicitly (Go's `/v2` path convention), not silently multi-versioned within one build.
This preserves §5.2 while giving a sanctioned path for a graph that genuinely needs two
majors — at the cost, as in Go, of an explicit identity change the author sees.

## 6. Semantics & type-system impact

- **Package-qualified identity.** Type identity is currently *module*-qualified
  (`postgres.url.PgConnectConfig`). Across repos, module names collide (two packages may
  each declare `module utils.string`). **Package identity must factor into type / instance /
  mangled-name identity.** This is the direct extension of
  `docs/module-qualified-type-identity-design-2026-07-10.md` and the canonical-identity
  campaign, not new territory. **Category A is the degenerate single-package case of this
  generalization** — fixing identity properly dissolves it rather than patching it.
- **Resolution.** `module_loader.module_name_to_path` (today: single `stdlib_root`,
  `stdlib.*`-or-bare only) generalizes to resolve a `(package, module)` pair against a
  resolved dependency set. Per the standing rule *do not widen `module_name_to_path`'s scope
  ad hoc* — this is a designed replacement, not a scope creep, and requires its own approval.
- **Coherence checking** becomes package-graph-aware: the orphan rule is checked against
  package-of-definition, and single-version selection guarantees at most one candidate
  instance per (class, type) in the resolved graph.

## 7. Error-message impact

- **Version conflict** → a located, explicit "no consistent version set" error naming the
  conflicting requirements (pip-20.3 style), never a silent pick.
- **Cross-package orphan** → "instance `(C, T)` may only be defined in package `<owner-of-C>`
  or `<owner-of-T>`", naming both owners.
- **Package/module identity collision** → loud, up-front (Jigsaw's no-split-packages
  translated: two packages claiming the same module identity is a resolution error).
- **Missing package** → distinguish "package not in manifest" from "module not in package"
  (today a dotted app import silently yields `Nothing` — the Category A footgun to remove).

## 8. Distribution & build model

- **Ship source** (git tags), not `.bc` — LLVM bitcode is not stable across LLVM versions
  and is target-specific. Consistent with the `.iface` draft choosing text for stability and
  scoping cross-machine distribution out of v1.
- **Build locally, cache artifacts.** Reuse the `(.iface, .bc)` cache keyed on
  **content-hash + compiler-fingerprint** from `docs/iface-precompiled-modules-v1-draft.md`.
  Per-package separate compilation falls out of machinery already being built. Downstream
  builds consume a dependency's `.iface`, not its re-bundled source.
- **Sourcing = git-based** (resolve tags; nothing to host/secure/index; already using ssh-git
  for Codeberg). A registry is deferred.
- **Manifest** per package: name, version, dependency constraints, exported module list,
  **required compiler version**.
- **Lockfile** pinning exact versions + integrity hashes for every transitive dependency —
  reproducibility and supply-chain integrity are table stakes for "production."

## 9. Toolchain unification & compatibility/migration

- **One compiler + one stdlib/prelude for the whole graph.** The prelude is an implicit shared
  dependency of every package, and mangling/ABI are compiler-version-specific (the seed
  fingerprint). A naive "each repo pins its own `mise` toolchain" is *unsafe* — two packages
  built by different compiler versions cannot be safely linked. The compiler version is
  resolved graph-wide, like any dependency, and recorded in the lockfile.
- **Migration.** There is no existing Sprout package ecosystem to break; `sprout-postgres` is
  the first real client and the natural v0 pilot. The `stdlib.*` namespace becomes the
  built-in "package" the compiler ships; user packages are additional resolved roots. The
  Category A fix is the first user-visible slice.
- **Tooling across boundaries.** `fmt` / `test` / LSP operate per-package and consume
  dependency `.iface` artifacts rather than re-bundling source (otherwise every downstream
  build pays the full transitive cost the `.iface` work exists to remove).

## 10. Implementation overview (gated on approval)

Phased; **each phase requires its own design sign-off before editing** per AGENTS.md
§Design Change Process #4. Semantics precede mechanics — #1–#2 are prerequisites; building
the resolver/manifest first would mean rebuilding them after the identity model lands.

1. **Package-qualified identity** (extends module-qualified-type-identity). Make package
   identity part of type/instance/mangled-name identity. Dissolves Category A.
2. **Cross-package resolution** — generalize module resolution to `(package, module)` against
   a resolved dependency set; loud errors for missing package vs missing module.
3. **Manifest + resolver + lockfile** — single-version selection, explicit-conflict resolver,
   integrity-hashed lockfile, graph-wide compiler-version resolution.
4. **Git sourcing + artifact cache** — fetch by tag; reuse `(.iface,.bc)` cache per package.
5. **Cross-package coherence enforcement** — orphan rule checked against package-of-definition
   over the resolved graph.
6. **Tooling** — per-package `fmt`/`test`/LSP consuming dependency `.iface`.

## 11. Tests to add (per phase, TDD)

- Resolution: app importing a second-package module resolves; a dotted import of an absent
  package **fails loudly** (regression for the Category A silent-`Nothing`).
- Identity: two packages each declaring `module utils.string` do not collide; their same-named
  types are distinct and the collision (if claimed as the same identity) is a loud error.
- Resolver: an unsatisfiable diamond **errors explicitly** (no silent nearest-wins).
- Coherence: a cross-package orphan instance is **rejected** with an owner-naming message.
- Reproducibility: a locked graph rebuilds to identical artifacts (content-hash keyed).

## 12. Open questions

1. Exact surface syntax for the manifest and for the explicit-major identity (`/v2`-analog).
2. Whether the `stdlib.*` namespace is modeled as a distinguished built-in package or as an
   ordinary resolved package with a reserved name.
3. Granularity of the compiler-version constraint (exact seed fingerprint vs a compat range).
4. Whether behavioral-semver drift (Elm's documented gap) warrants any tooling beyond
   API-shape diffing.

## 13. Normative status

This draft is **non-normative**. It recommends a direction and records its evidence. Turning
any part of it into implemented behavior requires the per-phase approval in §10, and any
resulting syntax/semantics/diagnostics changes must be reflected in `docs/spec-v0.md` (the
normative source) at that time.
