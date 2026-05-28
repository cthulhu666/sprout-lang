# Haskell Design Mistakes: Evidence-Backed Retrospective

Design notes for Sprout. Each item describes a Haskell decision that later evidence showed to be wrong,
and notes the Sprout implication where relevant.

---

## 1. Lazy evaluation by default

**The claim:** Pervasive laziness makes space complexity non-local and produces space leaks that are
invisible until production.

**Evidence:**

- GHC 8.0 (2016) shipped `-XStrict` and `-XStrictData` as language extensions — opt-ins that make
  individual modules strict-by-default. The fact that GHC added a "make it work like a normal language"
  escape hatch is an admission that the default is wrong for most code.
- `Data.Map` (lazy) was supplemented by `Data.Map.Strict` specifically because lazy maps caused
  accumulating thunks in production programs. The `containers` package now recommends the Strict variant
  in nearly all cases; the lazy variant exists for compatibility.
- The `deepseq` package — which exists solely to force full evaluation of a structure — has thousands of
  reverse dependencies on Hackage. A utility that would be unnecessary in a strict language became
  load-bearing infrastructure.
- GHCi's `:set +s` flag (allocation profiling) routinely surprises beginners by showing that "pure"
  Haskell programs allocate gigabytes due to thunk chains for what looks like a simple fold.
- Idris 2 (Edwin Brady, 2020) explicitly chose strict-by-default after Idris 1 was lazy. Brady cited
  the difficulty of reasoning about Haskell's space behavior as a motivation.
- Neil Mitchell's "Shake" build system (a well-known Haskell success story) has documented blog posts
  on fighting space leaks in what should have been simple accumulation patterns.

**Sprout implication:** Sprout is strict by default. No decision needed.

---

## 2. `String = [Char]`

**The claim:** Encoding the default string type as a singly-linked list of boxed characters is
catastrophically bad for performance and fragments the ecosystem.

**Evidence:**

- The `text` package (Bryan O'Sullivan, 2009) was created specifically to provide a performant string
  type. Its README benchmarks show 20–40× throughput improvements over `String` for typical text
  processing. It has thousands of reverse dependencies — one of the most depended-upon packages on
  Hackage — because it is the de facto replacement for the language's built-in type.
- A typical Haskell project uses at minimum four distinct string representations: `String` (legacy),
  `Text` (unicode text), `ByteString` (raw bytes), and `Builder` (efficient concatenation). Each exists
  because the baseline was wrong.
- `OverloadedStrings` (a GHC extension) was added so that string literals can inhabit types other than
  `String`. It is enabled by virtually every non-trivial project. The fact that an extension is required
  to make the literal syntax work with the correct type is a clear design smell.
- GHC's own documentation for `Data.String` includes the note: "In Haskell, `String` is an alias for
  `[Char]`. This gives relatively poor performance. Using `Data.Text` is strongly recommended instead."
  (Emphasis in the original.) The standard library documents its own type as a performance antipattern.
- The Haskell Foundation's 2021 goals document lists "String types" as one of the top pain points for
  new users and production deployments alike.

**Sprout implication:** Sprout's `str` is a value-typed, GC-managed byte sequence with O(1) length.

---

## 3. No effect tracking — the IO binary

**The claim:** Haskell's type system distinguishes `IO a` from `a` but cannot express finer-grained
effect distinctions (reads-file vs. writes-network vs. reads-config), so all effectful code collapses
into a single opaque bucket.

**Evidence:**

- The ecosystem produced at least seven major, incompatible effect-system libraries to fill this gap:
  `mtl`, `transformers`, `fused-effects`, `polysemy`, `effectful`, `eff`, and `cleff`. Each has
  hundreds of reverse dependencies, and none has become the standard — because the language itself
  provides no answer. This fragmentation is a direct consequence of the missing feature.
- Alexis King's "Effects for Less" (ZuriHac 2021) — a widely cited talk by the author of `eff` —
  opens with an explicit analysis of why every existing solution is broken in a different way, and
  concludes that no satisfactory answer exists within Haskell's current constraints.
- Oleg Kiselyov's "Extensible Effects" paper (2013) framed the problem formally: the monad transformer
  approach has O(n) performance overhead per effect layer and cannot express effect polymorphism
  cleanly. The paper spawned the free-monad and freer-monad lineage, but both have known performance
  cliffs.
- `rio`, a "standard library replacement" by FP Complete, bundles an opinionated effect-via-ReaderT
  solution because the standard approach is considered inadequate for production use.

**Sprout implication:** Sprout's `!{IO}` effect set in types is a direct response. The goal is for
fine-grained effect sets (e.g., `!{FileIO, Net}`) to be expressible and checkable without a library.

---

## 4. Orphan instances allowed

**The claim:** Allowing typeclass instances to be defined outside both the typeclass module and the
type module (orphan instances) breaks global coherence and creates dependency-order-sensitive behavior.

**Evidence:**

- GHC emits `-Worphans` (orphan instance warning) by default. GHC's own warnings system flags this as
  a problem in every file that contains one.
- The naming pattern `foo-orphans` (e.g., `aeson-orphans`, `json-orphans`) is a recognized Hackage
  convention — packages whose only purpose is to provide orphan instances that two library authors
  cannot agree to include upstream. This is ecosystem debt made visible.
- The Haskell Wiki's "Orphan instance" page states: "Orphan instances can cause problems if two
  packages define the same orphan instance, making them incompatible." There is no compiler mechanism
  to resolve the conflict; the user must restructure dependencies.
- Rust's coherence rules explicitly forbid orphan implementations. The Rust Reference documents the
  "orphan rule" as a deliberate design decision, citing Haskell's experience.
- GHC's `OVERLAPPING`/`OVERLAPPABLE`/`INCOHERENT` instance pragmas exist partly to manage conflicts
  that arise from orphans. The fact that `INCOHERENT` exists — which tells the compiler "pick any
  matching instance arbitrarily" — is a documented escape hatch from a correctness property that
  should have been guaranteed.

**Sprout implication:** Coherence decisions are pending, but the Haskell evidence argues for strict
orphan prohibition.

---

## 5. Partial functions in Prelude

**The claim:** Functions like `head`, `tail`, `fromJust`, and `read` are typed as total (returning `a`,
not `Maybe a`) but throw runtime exceptions on invalid input, contradicting the purity guarantee.

**Evidence:**

- The `safe` package provides safe, total alternatives to these functions (`headMay`, `tailMay`, etc.)
  and has hundreds of reverse dependencies — a package whose sole purpose is to make the standard
  library correct.
- HLint, the standard Haskell static analyzer, flags uses of `head`, `tail`, `fromJust`, `read`, and
  `init` with warnings in its default configuration. This means the main community linting tool treats
  the Prelude's own exports as antipatterns.
- `Data.List.NonEmpty.head` (from `base`) is a total version of `head` that requires a proof of
  non-emptiness in the type. It was added to `base` (the standard library) in GHC 8.0 (2016) — an
  admission that the original Prelude function should not exist as designed.
- The Haskell 2010 report itself documents `head` as: "Extract the first element of a list, which
  must be non-empty." The "must be" is enforced nowhere — it is a moral obligation on the caller with
  no type enforcement.

**Sprout implication:** Pattern match exhaustiveness is checked; `Maybe` and `Result` are idiomatic
returns for fallible operations. No partial functions in stdlib.

---

## 6. `return` means `pure`, not "return from function"

**The claim:** Naming the monadic lift operation `return` creates lasting confusion and cannot be
corrected without breaking backwards compatibility.

**Evidence:**

- The Applicative-Monad Proposal (AMP), implemented in GHC 7.10 (2015), made `Applicative` a
  superclass of `Monad` and introduced `pure` as the canonical name. GHC 7.10 was released 25 years
  after Haskell 1.0 (1990). The proposal explicitly notes that `return` must remain as an alias
  forever due to backwards compatibility — the mistake cannot be undone.
- The AMP migration guide warns that `return` and `pure` are now aliases but that new code should use
  `pure` — meaning the standard library ships a deprecated function as a permanent resident.
- Every Haskell tutorial written after 2015 must explain that `return` does not return from a function,
  and that `pure` is the same thing but better named. The confusion has been documented in beginner
  surveys and forum posts continuously since Haskell's introduction.
- Simon Peyton Jones acknowledged in a 2013 Haskell Symposium talk that the naming was "an unfortunate
  choice" made before the relationship between applicatives and monads was well understood.

**Sprout implication:** Sprout has no `return` keyword in this sense; function return is syntactically
the last expression, not a named operation.

---

## 7. `do`-notation hard-wired to `Monad`

**The claim:** `do`-notation desugars to `>>=` and `>>`, which require `Monad`, even when the
expression only needs `Functor` or `Applicative`. This over-constrains and makes the notation less
general than it should be.

**Evidence:**

- `ApplicativeDo` (GHC 8.0, 2016) was added as an extension to allow the compiler to use applicative
  operations when monadic ones are not required. It is opt-in and documented to have edge cases where
  it changes the meaning of existing code — which is why it cannot be the default.
- McBride & Paterson's "Applicative Programming with Effects" (JFP, 2008) introduced the applicative
  interface and noted that many uses of `do`-notation in practice only require applicative structure.
  Haskell had `do` before `Applicative` was formalized, and the mismatch was never corrected.
- The expression `do { x <- f; return (g x) }` compiles fine but has type `Monad m => m b`, requiring
  a monad dictionary lookup at runtime, even though the equivalent `fmap g f` requires only `Functor`
  and carries no dictionary overhead. The over-constraint is a real performance issue in hot paths.

**Sprout implication:** Sprout's `do` is currently IO-specific. If the notation is generalized, the
lesson is to desugar to the weakest sufficient interface, not always the strongest.

---

## 8. Record field names pollute the module namespace

**The claim:** All record field names in a module share a flat namespace, causing name collisions
between records and requiring heavyweight accessor libraries for basic field access.

**Evidence:**

- The `lens` library by Edward Kmett has 700+ reverse dependencies and `optics` has hundreds more.
  Both exist primarily to make record field access and update composable — a problem that should not
  require a library at all.
- Multiple GHC proposals for fixing records were rejected or stalled: TDNR (Type-Directed Name
  Resolution) was rejected in 2013; several successor proposals failed similarly. The community spent
  over a decade debating the fix.
- `OverloadedRecordDot` (GHC 9.2, 2022) finally gave Haskell basic dot-access syntax (`x.field`).
  This is 32 years after Haskell 1.0. Every mainstream language had this from day one.
- `OverloadedRecordUpdate` (also GHC 9.2) remains experimental and is not enabled by default because
  the implementation is not yet sound in all cases. Record update still requires the `{field = val}`
  syntax with all the namespace collision problems intact.

**Sprout implication:** Sprout's record access design should prioritize namespace isolation from the
start, not as an afterthought.

---

## 9. The monomorphism restriction

**The claim:** A rule that prevents top-level bindings without type signatures from being polymorphic
bites nearly every beginner and has no obvious benefit in modern Haskell.

**Evidence:**

- GHCi disabled the monomorphism restriction by default in GHC 7.8 (2014). The interactive REPL uses
  a different default than the compiler — a tacit admission that the default is wrong for human-facing
  use.
- `GHC2021` (the pragmatic extension set introduced in GHC 9.2) does not include
  `NoMonomorphismRestriction`, meaning even the "modern Haskell" language set preserves the wart for
  compatibility.
- Virtually every Haskell style guide (Tikhon Jelvis's, Google's Haskell style guide, the Kowainik
  guide) recommends either disabling it or always providing top-level type signatures — the latter
  being the workaround that makes the restriction irrelevant but not absent.
- The Stack Overflow question "What is the monomorphism restriction?" has been viewed over 50,000
  times, consistently ranking as one of the top Haskell questions. The confusion is not a beginner
  outlier; it is a structural feature of the language's learning curve.

**Sprout implication:** Sprout should have a uniform polymorphism rule with no special cases for
binding position.

---

## 10. GHC extension proliferation — no stable modern standard

**The claim:** The gap between the official Haskell standard and what the community actually uses is
so large that "Haskell" is effectively an ill-defined language.

**Evidence:**

- GHC 9.8 ships approximately 130 language extensions. A non-trivial project typically enables 10–20
  of them in its `.cabal` default-extensions field.
- The last official Haskell standard is Haskell 2010, published 14 years ago (as of 2024). It
  predates GADTs, TypeFamilies, ScopedTypeVariables, LambdaCase, TupleSections, and a dozen other
  features that are considered basic modern Haskell.
- `GHC2021` was introduced in GHC 9.2 (2022) as an acknowledged workaround: a named set of
  commonly-used extensions that the community can target without listing them individually. The
  existence of a community-defined language variant distinct from the official standard is an
  institutional admission that the standard failed.
- When Haskell code is shared between projects, the first debugging step is often "do you have the
  right extensions enabled?" — a coordination overhead that does not exist in languages with a
  coherent standard.

**Sprout implication:** Sprout should have exactly one language version at any point in time, with
deliberate, versioned deprecation cycles for breaking changes.

---

## 11. Numeric typeclass hierarchy over-engineered

**The claim:** Eight separate typeclasses for numeric operations (Num, Real, Integral, Fractional,
Floating, RealFrac, RealFloat, Enum) make writing generic numeric code difficult without providing
proportional expressiveness benefits.

**Evidence:**

- The `Numeric.Prelude` package on Hackage is a complete replacement for Haskell's numeric hierarchy,
  motivated by the claim that the standard hierarchy has the wrong decomposition. It has been
  maintained since 2007 — 17+ years of parallel numeric infrastructure as a protest against the
  standard.
- `fromIntegral :: (Integral a, Num b) => a -> b` is one of the most-searched Haskell functions
  because converting between numeric types requires understanding three typeclasses (the source, the
  target, and the coercion). In every other language, this is a cast or automatic promotion.
- Adding two values of type `Int` and `Double` requires an explicit `fromIntegral` — there is no
  numeric promotion, and the typeclass hierarchy is the reason: `Num Int` and `Num Double` are
  unrelated instances with no conversion path in the class itself.
- The `Num` class requires `*`, `+`, `-`, `abs`, `signum`, `fromInteger`, and `negate` — but not
  division. `Integral` has `div` and `mod`; `Fractional` has `/`. A simple calculator implementation
  requires touching four typeclasses for four arithmetic operations.

**Sprout implication:** Sprout's numeric type design (see `numeric-types-v1-draft.md`) should minimize
the number of concepts a user must internalize to write generic numeric code.

---

## 12. Implicit typeclass dispatch with no call-site annotation

**The claim:** There is no syntax to indicate which typeclass instance is being used at a call site,
making dispatch resolution invisible and forcing users to mentally re-run the constraint solver.

**Evidence:**

- `TypeApplications` (GHC 8.0, 2016) was added partly to resolve dispatch ambiguities that arise at
  call sites. `show @Int 3` explicitly selects the `Int` instance of `Show`. Without it, expressions
  like `show 3` require a type annotation somewhere in scope to resolve — a silent action at a
  distance.
- The "defaulting" rules for numeric literals (Haskell 2010 §4.3.4) are a separate mechanism that
  silently selects `Integer` when a numeric literal's type is ambiguous. Two separate implicit
  resolution mechanisms (defaulting and typeclass inference) interact and regularly produce surprising
  behavior.
- `read . show` is an idiom that requires both `Show a` and `Read a` to be the same type, but the
  compiler cannot verify this without a type annotation — so `(read . show) True :: Bool` works, but
  `read (show True)` fails to compile without the annotation. The dispatch invisibility converts a
  trivial operation into a puzzle.
- Rust's trait system provides `<T as Trait>::method(x)` syntax for explicit disambiguation. This was
  a deliberate design choice informed by Haskell's experience.

**Sprout implication:** Consistent with the "explicit is default" policy, Sprout should make instance
selection visible when it matters, not invisible always.

---

## 13. No first-class modules

**The claim:** Haskell's module system is a namespace tool, not an abstraction tool. You cannot
parameterize a module over another module, forcing typeclasses to serve a role they were not designed
for.

**Evidence:**

- OCaml has had functors (parameterized modules) since its inception. The ML module system allows
  expressing "a data structure parameterized over a comparison function" as a module-level abstraction,
  with concrete performance implications (no dictionary passing) and clearer semantics.
- Backpack (GHC 8.2, 2017) was an attempt to add ML-style module signatures to Haskell. Seven years
  later, its adoption is minimal — fewer than 50 packages on Hackage use it — because it integrates
  poorly with the existing typeclass-centric ecosystem and Cabal's build model.
- Typeclasses are frequently described as "poor man's modules" in academic literature (Wadler &
  Blott, 1989; Harper & Stone, 2006). The analogy is imperfect: typeclasses require globally unique
  instances (coherence), while ML modules can have multiple instantiations of the same signature.
  Using typeclasses for module-level abstraction forces global coherence onto problems that don't need it.
- The `Data.Map` vs `Data.HashMap` vs `Data.IntMap` situation — three separate modules with almost
  identical APIs but no shared abstraction — is a direct consequence. An ML functor would express
  "a map parameterized over a key ordering" once; Haskell requires three concrete implementations.

**Sprout implication:** Module parameterization design is an open question. The evidence suggests
that first-class modules and typeclasses fill different roles and should coexist rather than one
substituting for the other.

---

## Meta-observation

The single strongest pattern across these 13 items: **the mistake was a wrong default, and the fix
was an opt-out**. Lazy evaluation, String-as-list, monomorphism restriction, partial functions in
Prelude, and the do/Monad coupling all share this structure. GHC added extensions, packages, or
pragmas to undo the default — which means the cost of the original choice was paid by the entire
ecosystem, and the fix is still not universal.

The lesson for Sprout: **defaults are permanent in practice even when they are not permanent in
theory.** Get the default right at language birth. The cost of a correct default is zero; the cost
of retrofitting is the entire ecosystem.
