# Cross-Language Design Lessons for Sprout

Status: **supporting design doc** — non-normative. `docs/spec-v0.md` is the normative source of truth.
This is the companion to `docs/haskell-lessons-learned.md`: a second, broader research round covering
the languages nearest to Sprout's niche (statically-typed, functional-first, strict, beginner-friendly)
and the languages that took the roads Haskell didn't.

Two rules carried over from the Haskell doc:

1. **Every claim is backed by a primary source** — a language reference/spec, an official blog post,
   a core-team talk, or the language author's own paper. URLs are inline. Where only a weaker source
   exists (a video talk, a design doc removed during a rewrite), that is **flagged in place**, not
   hedged over.
2. It is organized by **Sprout's open decision**, not by language, because the purpose is to inform
   the decisions in `BACKLOG.md` and the `*-v1-draft.md` docs — effects, coherence/dispatch, numeric
   types, generalization, versioning — rather than to catalog trivia.

---

## 0. Audit — where Sprout actually stands on the 13 Haskell traps

Verified against the current spec, compiler, prelude, and tests (2026-07). Verdict legend:
✅ avoided · ⚠️ partial / doc-vs-reality gap · 🕗 genuinely open (honestly hedged).

| # | Trap | Verdict | Evidence |
|---|------|---------|----------|
| 1 | Laziness by default | ✅ | Strict; `spec-v0.md` §6; no thunk primitive |
| 2 | `String = [Char]` | ✅ (with a nuance) | UTF-8 buffer, trap avoided. `byte_length` is **O(1)** for GC-managed strings (header `aux` stores it, `runtime/sprout_runtime.c` `str_byte_len`), O(n) `strlen` only for static literals; `length` (codepoints) is O(n) by design, as in Rust/Go/Swift |
| 3 | No effect tracking | ⚠️ | Only `!{IO}`+`!{e}` in contract; `!{FileIO,Net}` "future use"; pure→IO **not enforced on fn bodies** (`infer.sprout:4584`, `unifier.sprout:245-248`) |
| 4 | Orphan instances | ✅ | Overlaps *unconditionally rejected* `infer.sprout:3677-3684` (ahead of the doc's "pending") |
| 5 | Partial functions | ✅ | No head/tail; `Maybe` accessors; exhaustiveness sound (nested-product gap deferred) |
| 6 | `return` vs `pure` | ✅ | `pure` only, no `return`; `prelude.sprout:646` |
| 7 | `do` hard-wired to Monad | ✅ | Structural over IO/Maybe/Result, not a Monad dictionary; `spec-v0.md:779-782` |
| 8 | Record namespace pollution | ✅ | Per-record field scoping; `spec-v0.md` §5.6, `records-v0.md:186` |
| 9 | Monomorphism restriction | ⚠️ | Avoids Haskell's MR, but *does* use the ML value restriction — a special case; `infer.sprout:209-234` |
| 10 | Extension proliferation | ✅ | One spec, no pragmas; soft "experimental extension" split only |
| 11 | Numeric hierarchy | 🕗 | Zero classes today; `numeric-types-v1-draft.md` is a sound draft |
| 12 | Invisible dispatch | 🕗 | No call-site selection syntax; annotation-driven; `spec-v0.md:720-723` |
| 13 | First-class modules | 🕗 | Namespace-only; functors deferred; `module-qualified-type-identity-design-2026-07-10.md:164-168` |

**The #2/#3/#9 accuracy fixes were made in `haskell-lessons-learned.md` in the same change that added
this doc.** #2 turned out to be a *documentation* error, not a design gap: the earlier audit trusted a
stale `stdlib/string.sprout` comment ("O(bytes)… strlen"), but the runtime already makes `byte_length`
O(1) for GC-managed strings via a header field — only static literals (no header) and codepoint-count
(O(n) by design) remain non-O(1). #3 (effect claim overstated) and #9 (value restriction is a special
case) are the substantive corrections. Items #11/#12/#13 are genuinely undecided and correctly hedged.

---

## A. Effects in types — Sprout's flagship, and its biggest risk

Sprout's differentiator is effect sets on the arrow (`!{FileIO, Net}`). This is the section with the
most at stake, because the audit shows the feature is far less built than the Haskell doc implied, and
the prior art contains one strong cautionary tale directly in its path.

### A1. PureScript removed effect-rows-in-types — the central cautionary tale
- **Claim:** PureScript is the one language that shipped a row-of-effects type (`Eff (e :: ROW) a`) as
  its *default* effect mechanism, then **removed** it in 0.12 for a monomorphic `Effect a` with no row.
- **Primary source:** purescript-eff README —
  https://github.com/purescript-deprecated/purescript-eff/blob/master/README.md :
  > "This differs from `Eff` by removing the row of effect types. **This decision was made as getting
  > the effect rows to line up was sometimes quite tricky, without providing a great deal of benefit.**"
  Corroboration (0.12.0 release notes): https://github.com/purescript/purescript/releases/tag/v0.12.0
- **FLAG:** that one sentence is the *only* official rationale; there is no 0.12 migration guide in
  `purescript/documentation` and no fuller Phil-Freeman write-up located. Richer reasons often repeated
  (weak exception tracking, confusing unification errors) are third-party, not primary.
- **Sprout implication:** The decisive contrast with Koka (A2) is that PureScript threaded rows
  *manually through a monad*, whereas Koka *infers* them. The lesson is **not** "effect rows fail" — it
  is "**effect rows fail unless inference carries the weight.**" Sprout today does neither: it tracks
  `!{IO}` but does not infer/enforce it on function bodies. Closing that inference+enforcement gap is
  the precondition for scaling `!{…}` beyond `IO`.

### A2. Koka — the reference design: rows + HM inference, no union operator, no subtyping
- **Claim:** Encode effects as an unordered row on the arrow `(τ…) -> ε τ`, inferred by
  Hindley-Milner; **reject** both an effect-union operator `∪` and effect-subtyping (each makes
  unification undecidable); allow **duplicate labels** to keep unification principal and to type effect
  *elimination* (handlers).
- **Primary source:** Leijen, *Koka: Programming with Row-polymorphic Effect Types*, MSFP 2014 —
  https://www.microsoft.com/en-us/research/wp-content/uploads/2016/02/koka-effects-2013.pdf
  > §2.1 "We write function types as (τ1,…,τn) → ε τ … inferred automatically … based on
  > Hindley-Milner style inference … polymorphic effects through row-polymorphism using duplicate labels."
  > §2.3 "One possible design choice is to have a ∪ operation … Unfortunately, this quickly gets us in
  > trouble during type inference … Another design choice is to introduce subtyping … can make type
  > inference undecidable … The approach we advocate … is the use of row-polymorphism."
  > §2.4 "Enabling duplicate labels is crucial … it enables principal unification without needing extra
  > constraints … and … precise types to effect elimination forms (like catching exceptions)."
- **Sprout implication:** If Sprout ever generalizes `!{…}` beyond singletons, adopt row-polymorphism
  semantics with inference; do **not** model `!{A}` combination as set-union-with-subsumption in the
  checker. If handlers are ever added, a naive `Set` of labels cannot type "handle the innermost `Exn`,
  leaving an outer one" — the row needs multiplicity/duplicate semantics. Decide the internal row model
  before the surface `!{…}` set-sugar locks it in.

### A3. Koka's convenience layer: named effect aliases; and its admitted weaknesses
- **Claim (copy):** Provide a canonical empty effect and a few named aggregate aliases —
  `total = <>`, `pure = <exn,div>`, `io` on top — rather than forcing users to spell each primitive.
  **Claim (limits):** divergence/termination inference is self-described "limited and syntactically
  fragile"; general `ctl` handlers must capture the stack, so only a tail-resumptive fast path
  (`fun`/`val`) is cheap.
- **Primary source:** paper §2.2 (aliases; "The total effect represents the absence of any effect");
  §2.7 ("The current analysis is quite limited and syntactically fragile but seems to work well enough
  in practice … we prefer a predictable analysis with clear rules"); book §3.4.3
  https://koka-lang.github.io/koka/doc/book.html ("operations declared as `fun` are much more efficient
  than general `ctl` operations … a performance cost very similar to virtual method calls").
- **Sprout implication:** Give Sprout `!{}` (= total/pure) and a small alias vocabulary; decide
  deliberately whether non-termination is an effect Sprout tracks (Koka splits `div`/`exn` out of
  "pure"). If Sprout only *tracks* effects and never *handles* them, the stack-capture performance trap
  never arises — a real point in favor of tracking-without-handlers for v1.

### A4. Effect-mismatch diagnostics are UNSOLVED in the primary literature
- **Claim:** The most-cited practical pain of effects-in-types — unreadable errors when an effect row
  fails to unify — is not addressed by the reference design.
- **Primary source:** Koka papers/book do not evaluate effect-unification error quality; the manual's
  "Named and Scoped Handlers" section is literally `Todo.` (book §3.4.13,
  https://koka-lang.github.io/koka/doc/book.html). PureScript's own verdict (A1) was "tricky to line up."
- **Sprout implication:** This is where Sprout must do **original** design work; it cannot copy a
  published solution. It aligns exactly with `effect-system-v1-draft.md` §8 (diagnostics as a headline
  deliverable) — the research says §8 is the whole ballgame, not a cleanup afterthought. Study
  Unison's ability-check failures (A6) as the nearest working model.

### A5. OCaml 5 — the "handlers without effect types" baseline
- **Claim:** OCaml 5 (2022) added algebraic effect *handlers* to a mainstream ML but deliberately does
  **not** track effects in types; an unhandled effect raises at runtime.
- **Primary source:** OCaml Manual, *Effect handlers* — https://ocaml.org/manual/5.4/effects.html :
  > "Unlike languages such as Eff and Koka, effect handlers in OCaml do not provide effect safety; the
  > compiler does not statically ensure that all the effects performed by the program are handled." API
  > still unstable: "The Effect interface may change in incompatible ways in the future."
- **Sprout implication:** OCaml unblocked concurrency fast with zero static guarantee. *Tracking* — what
  Sprout attempts — is precisely the hard part OCaml declined. Decide consciously whether the tracking
  cost is worth it, and note that shipping handlers first (untracked) and tracking later is a viable
  incremental path a mainstream language actually took.

### A6. The strongest alternatives skip effect-typing entirely — Elm, Roc, Idris
- **Elm — effects-as-data:** side effects are `Cmd`/`Sub` *values* handed to a runtime; the core stays
  pure, testable, and replayable **without** effect types. Guide: https://guide.elm-lang.org/effects/
  ("**command** the runtime system to make an HTTP request … **subscribe** to the current time").
- **Roc — platforms provide effects:** "all Roc functions are pure … they return descriptions of
  effects to run, in the form of **Tasks** … handed off … to an effect runner outside the program."
  https://www.roc-lang.org/functional and https://www.roc-lang.org/faq . Zero effect annotations; the
  cost is that a signature does not reveal what a function does.
- **Idris — effects as a library:** fine-grained composable effects live in userland (`Effects`/`Eff`)
  atop dependent types, motivated by monad transformers composing badly. Brady, ICFP 2013 —
  https://www.type-driven.org.uk/edwinb/papers/effects.pdf .
- **Unison — abilities, tracked (closest sibling):** `I ->{A} O`; "calls to functions requiring
  abilities `{A1,A2}` must be in a context where at least the abilities … are available, otherwise the
  typechecker will complain." https://www.unison-lang.org/docs/language-reference/abilities-and-ability-handlers/
  This is essentially Sprout's `!{FileIO}` shipped in a real language — evidence the surface is viable.
- **Frank — ambient ability (deepest answer to "coloring"):** functions *are* handlers; effect
  polymorphism is implicit, "propagating an ambient ability inwards" with **no effect variables in
  source.** Lindley/McBride/McLaughlin, POPL 2017 — https://arxiv.org/abs/1611.09259 .
- **Sprout implication:** Elm and Roc prove you can get a pure, replayable core *without* paying the
  effect-annotation tax at all. Sprout's `effect-system-v1-draft.md` should state, explicitly, why
  effect *visibility in signatures* earns its inference/annotation weight over Roc's annotation-free
  purity. If it does keep effect types, Frank's ambient-ability inference is the primary-sourced
  technique for not forcing every higher-order combinator to carry an explicit effect variable.

---

## B. Coherence & dispatch — an active soundness gap, and the design template to close it

`retro-dict-dispatch-soundness-2026-07-13.md` documents that dictionary dispatch currently *silently
guesses a wrong dict* → runtime SIGSEGV, because type-variable identity is name-based. The prior art
gives both the enforcement template and the policy choices.

### B1. Rust vs Swift — a controlled experiment on static coherence
- **Rust (static coherence, ergonomic cost):** the *orphan rule* forbids an impl unless the trait or a
  type is local, guaranteeing no cross-crate conflicts; the documented cost is newtype wrapping. Rust
  Reference https://doc.rust-lang.org/reference/items/implementations.html + RFC 1023
  https://rust-lang.github.io/rfcs/1023-rebalancing-coherence.html .
- **Swift (declined it, soundness cost):** allows retroactive conformances and pays with runtime UB —
  SE-0364: "if multiple modules declare the same conformance … it is indeterminate which definition …
  will 'win'" → fixed years later with a warning + explicit `@retroactive`.
  https://github.com/swiftlang/swift-evolution/blob/main/proposals/0364-retroactive-conformance-warning.md
- **Sprout implication:** For a safety-first language, Rust's tradeoff (static coherence, ergonomic
  cost) is the template; Swift shows that deferring coherence bakes in an *unsoundness* that is very
  hard to close afterward — which is exactly the failure mode Sprout's dispatch retro already hit.

### B2. PureScript forbids orphans outright (stronger than Haskell's warning)
- **Primary source:** https://github.com/purescript/documentation/blob/master/language/Type-Classes.md
  > "in PureScript, they are forbidden. Any attempt to define an orphan instance … will mean that your
  > program does not pass type checking." / "Without global uniqueness, you risk operating on data with
  > incompatible instances … keys disappear from your map."
- **Sprout implication:** Sprout's compiler already hard-rejects overlaps (audit #4) — this validates
  being ahead of the Haskell doc's "pending." Forbidding orphans as a *type error* (not a warning) is
  the primary-validated strong stance.

### B3. Idris — the other coherent philosophy: named, explicitly-selected implementations
- **Primary source:** https://idris2.readthedocs.io/en/latest/tutorial/interfaces.html
  > `[myord] Ord Nat where …`; `sort testList` uses the default, `sort @{myord} testList` explicitly
  > selects the reversed ordering.
- **Sprout implication:** This is the direct answer to open item #12 (invisible dispatch). The two
  coherent options are **global uniqueness** (Rust/PureScript — "no ambiguity ever") vs **explicit
  visible selection** (Idris — "ambiguity resolved by the programmer at the call site"). Roc keys
  abilities to opaque types for uniqueness-by-construction (https://www.roc-lang.org/faq). Sprout should
  pick one philosophy deliberately rather than leaving disambiguation to load-bearing type annotations.

### B4. Three ML languages independently regret built-in polymorphic equality/compare
- **OCaml:** `Stdlib` — "Equality between functional values raises `Invalid_argument`. Equality between
  cyclic data structures may not terminate"; `==` on non-mutable types is "implementation-dependent."
  https://ocaml.org/manual/5.1/api/Stdlib.html . This drove Jane Street's `Base` to *not expose* it
  and become a wholesale stdlib replacement — https://github.com/janestreet/base .
- **SML:** the `''a` equality-type variable is a designer-acknowledged wart — HOPL IV *History of SML*:
  "Harper and MacQueen suggested that polymorphic equality be removed … a significant clean-up";
  `real` was dropped from equality types because IEEE NaN makes `x = x` false.
  https://smlfamily.github.io/history/SML-history.pdf .
- **F#:** HOPL IV *Early History of F#* — "the whole generic comparison feature could likely have been
  omitted from F#, or greatly constrained." https://fsharp.org/history/hopl-final/hopl-fsharp.pdf .
- **Sprout implication:** Derive `Eq`/`Ord` per-type through the *one* coherence mechanism chosen in
  B1–B3 — **never** a built-in polymorphic compare. This also pre-settles `numeric-types-v1-draft.md`
  §7.1 (NaN breaks the `Ord`/`Eq` contract): keep `Numeric` total-ordered and give `Double` a
  `PartialOrd`, rather than a magic float-aware universal compare.

### B5. The enforcement layer: a typed-IR dictionary verifier
- **Claim:** The dispatch retro's own top recommendation (item 1) — elaborate dictionaries to explicit
  terms and type-check the elaboration — is GHC's Core-lint idea, and it is the enforcement layer any
  coherence policy needs to make dispatch soundness a *compile* error rather than a runtime SIGSEGV.
- **Source:** `docs/retro-dict-dispatch-soundness-2026-07-13.md` §4 item 1 (internal); the technique is
  GHC's `-dcore-lint`.
- **Sprout implication:** Whichever coherence policy Sprout picks (B1–B3), pair it with a Core-lint-style
  verifier on the typed IR. It retroactively guards every dispatch fix already made and converts the
  entire "wrong-dict" bug family into type errors.

---

## C. Generalization — the value restriction is the consensus, not a wart

### C1. SML — the value restriction is a documented success
- **Claim:** Wright's syntactic value restriction (generalize only when the definiens is a syntactic
  value) replaced SML'90's baroque "imperative type variables" for near-total simplicity gain at
  negligible practical cost.
- **Primary source:** HOPL IV *History of SML* §4.4 —
  https://smlfamily.github.io/history/SML-history.pdf : "Andrew Wright cut the Gordian knot … it was
  found not to be a significant limitation in practice." Footnote 43: "Wright examined over 200,000
  lines of existing SML code and found only 31 η-expansions were required." The recorded real cost:
  parser/monadic *combinator composition* violates it "and there is no natural work-around."
- **Sprout implication:** Sprout already uses the value restriction (audit #9) — this is the right
  default, not a Haskell-MR-style wart. Budget for the combinator-composition pain up front.

### C2. OCaml — it's a soundness necessity; adopt the *relaxed* form + good `'_weak` diagnostics
- **Primary source:** OCaml Manual, *Polymorphism and its limitations* —
  https://ocaml.org/manual/5.4/polymorphism.html : "This distinction between weakly and generic
  polymorphic type variable protects OCaml programs from unsoundness and runtime errors." Names the
  *relaxed* value restriction (generalization in covariant positions).
- **Sprout implication:** A strict language with mutable refs needs the value restriction for soundness.
  Adopt OCaml's *relaxed* variant so abstract combinator types stay polymorphic without η-expansion, and
  invest in `'_weak`-style diagnostics — the confusing part for users is the error message, not the rule.

---

## D. Numeric types — the draft is strong; three additions

`numeric-types-v1-draft.md` already surveys Haskell/Swift/Rust/Scala and correctly steals Swift's
"`Numeric` excludes division." Three primary-sourced additions:

### D1. F# — resolve numeric dispatch at compile time to dodge the witness-passing perf cliff
- **Primary source:** HOPL IV *Early History of F#* §9.4 —
  https://fsharp.org/history/hopl-final/hopl-fsharp.pdf : "by inlining, the constraint would be resolved
  according to the types available at point of use. This allows overloaded arithmetic to integrate
  neatly with Hindley-Milner type inference." (Contrast: type classes "normally implemented via
  witness-passing, which can cause situations where smaller changes to code give significant changes in
  performance.")
- **Sprout implication:** Directly answers the draft's open §7.5 ("routing `+` through `Additive`
  dispatch adds overhead"). Monomorphize/inline numeric dispatch so integer-heavy inner loops don't pay
  a dictionary cost — F# proved this composes with HM inference.

### D2. F# units of measure — erased type-level dimensional analysis
- **Primary source:** MS Learn —
  https://learn.microsoft.com/en-us/dotnet/fsharp/language-reference/units-of-measure : "Units of
  measure are used for compile-time unit checking but are not persisted in the run-time environment.
  Therefore, they do not affect performance."
- **Sprout implication:** An additive, zero-runtime-cost safety win worth considering once the numeric
  hierarchy lands: model units as erased type-level parameters (a free abelian group over base
  dimensions), checked in inference, dropped at codegen. One honest tradeoff: no runtime reflection on
  units.

### D3. Rust — explicit conversion, and *fallible* narrowing via `TryFrom`
- **Primary source:** Rust Reference, numeric casts —
  https://doc.rust-lang.org/reference/expressions/operator-expr.html (no implicit numeric coercion);
  `TryFrom`/`TryInto` for checked narrowing.
- **Sprout implication:** Sprout already requires explicit `to_double` (audit #11, good). Extend the
  model with a *fallible* conversion for narrowing (`Int`→`u8` etc.) that returns `Maybe`/`Result`,
  rather than a single lossy cast — precision loss should be a visible, deliberate choice.

---

## E. Versioning & spec governance (open item #10, longer horizon)

### E1. Rust editions — breaking changes without splitting the ecosystem
- **Primary source:** RFC 2052 https://rust-lang.github.io/rfcs/2052-epochs.html ("Editions do not split
  the ecosystem nor do they break existing code … a crate dependency graph may involve several different
  editions simultaneously") + Edition Guide
  https://doc.rust-lang.org/edition-guide/editions/index.html ("crates in one edition must seamlessly
  interoperate with those compiled with other editions").
- **Sprout implication:** The crucial property is that *all editions lower to one internal
  representation* — it is not N incompatible dialects. If Sprout ever evolves syntax/semantics post-1.0,
  design an edition-like opt-in boundary with a single-IR interop guarantee *before* accumulating
  breaking-change debt.

### E2. Standard ML — a formal spec is a success; freezing it is a self-inflicted decline
- **Primary source:** HOPL IV *History of SML* §6, §9.4 —
  https://smlfamily.github.io/history/SML-history.pdf : SML "set a precedent by being a language whose
  design included a formal definition with an associated metatheory," yet "The fact that the design of
  Standard ML has been frozen in time probably contributed to a decline in its popularity" — root cause:
  "the Definition was a physical book … the TEX sources were not available … there was to be no further
  evolution." Even frozen, "the specification of overload resolution is not precisely defined, which
  leads to incompatibilities between implementations."
- **Sprout implication:** A machine-checkable reference semantics is SML's best legacy and worth
  Sprout's investment — but decouple the *artifact* from *governance*: keep spec sources open, versioned,
  and owned by a maintaining body with an explicit evolution process. A formal spec must be a living,
  forkable artifact, never a sealed book.

---

## F. Free wins worth copying (cheap, high-payoff, mostly serve the beginner-friendly goal)

- **Elm — compiler errors as a first-class feature.** Czaplicki: the specific, friendly messages
  "required no significant changes to the type inference algorithm and imposed no noticeable performance
  cost." https://elm-lang.org/news/compiler-errors-for-humans . Elm maintains an error-message-catalog
  (programs → expected errors). *Implication:* for a beginner-friendly language this is plausibly the
  highest-ROI investment there is — code shown as-written, a one-line "what's wrong" above the snippet, a
  hint below, with a dedicated error test corpus.
- **Elm — the honest hedge.** The guide advertises "No runtime errors **in practice**" — the qualifier
  is deliberate. https://guide.elm-lang.org/ . *Implication:* state precisely what Sprout guarantees
  (exhaustive match, no null, `Maybe`/`Result` over partiality) and name the escape hatches; do not
  overclaim "zero."
- **Idris — totality as opt-in, not a ban.** `partial` annotation + module-level `%default total`; only
  total functions run at type-check time. https://idris2.readthedocs.io/en/latest/reference/pragmas.html .
  *Implication:* the workable form of Sprout's "no partial functions" is loud opt-in partiality, and the
  definition of "total" must admit productive corecursion, not just termination.
- **F# — computation expressions (à-la-carte builder).** "unlike … do-notation in Haskell, they are not
  tied to a single abstraction … A builder class does not need to implement all of the methods."
  https://learn.microsoft.com/en-us/dotnet/fsharp/language-reference/computation-expressions .
  *Implication:* this is exactly the Haskell doc's #7 recommendation ("desugar to the weakest sufficient
  interface") shipped and proven — Sprout's `do` (already structural, not Monad-wired) is on this path;
  generalize by having each context supply only the operations it needs.
- **F# — typed format strings.** `printf` specifiers are typed values; arg mismatches/arity are compile
  errors. https://learn.microsoft.com/en-us/dotnet/fsharp/language-reference/plaintext-formatting .
- **F# — active patterns.** User-definable pattern extractors so `match` works on abstract types without
  leaking representation. https://learn.microsoft.com/en-us/dotnet/fsharp/language-reference/active-patterns .
  Cost lesson: make the extractor result allocation-free from the start (F# had to retrofit struct-options).
- **Rust — the `?` operator.** `Result` needs propagation sugar to be ergonomic; RFC 243 evolved
  `try!` → `?` with "no magic … behind the scenes." https://rust-lang.github.io/rfcs/0243-trait-based-exception-handling.html .
  *Implication:* Sprout has `let..else`; a postfix `?`-equivalent for `Result`/`Maybe` is worth weighing.
- **Rust — integer overflow is a program error.** Panic in debug, wrap in release, explicit
  `wrapping_*`/`checked_*`/`saturating_*`. RFC 560 https://rust-lang.github.io/rfcs/0560-integer-overflow.html .
  *Implication:* relevant to `int-overflow-policy-decision.md`; a beginner-safe language has grounds to be
  *stricter* than Rust (checked in all builds) with explicit opt-outs.
- **Swift — value semantics as the default.** Structs/enums copy on assignment; "prefer structures
  because they're easier to reason about."
  https://github.com/swiftlang/swift-book/blob/main/TSPL.docc/LanguageGuide/ClassesAndStructures.md .
  *Implication:* validates Sprout's value-typed `String` and functional-first stance at mainstream scale.
- **Swift — the implicitly-unwrapped-optional regret.** SE-0054 reclassified IUO as "a transitional
  technology" and removed it as a first-class type because unchecked-ness *propagated through types*.
  https://github.com/swiftlang/swift-evolution/blob/main/proposals/0054-abolish-iuo.md .
  *Implication:* if Sprout ever offers force-unwrap, keep it local and non-propagating; better, prefer the
  `let..else`/refutable-binding ergonomics Sprout already has.
- **PureScript / Idris / Roc — strict-by-default is the argued consensus.** PureScript: "laziness … comes
  with an unavoidable overhead"
  (https://github.com/purescript/documentation/blob/master/language/Differences-from-Haskell.md);
  Idris 2: "eager evaluation for more predictable performance"
  (https://github.com/idris-lang/Idris2/blob/main/docs/source/faq/faq.rst); Roc: strict + reference
  counting enables in-place mutation of immutable data (Feldman, "Outperforming Imperative with Pure
  Functional Languages", https://www.youtube.com/watch?v=vzfy4EKwG_Y — **FLAG:** talk only; not in Roc's
  written docs). *Implication:* Sprout's strict default is corroborated by three independent recent
  languages — cite them rather than merely asserting the choice.
- **Roc — prefer a named domain union over reflexive `Maybe`-wrapping.** "Roc does not have `null` …"
  and the FAQ argues `artist : [Loading, Loaded Artist]` documents intent better than `Maybe Artist`.
  https://www.roc-lang.org/faq . *Implication:* with cheap tag unions, teach users to reach for a
  domain-specific union first; it survives model changes better.

### Coverage flags (weaker-than-ideal sources, per rule 1)
- Elm's no-typeclasses rationale and the 0.19 kernel-removal rationale: the *policy* is primary-sourced
  (guide), the *reasoning* is a conference talk / distributed discourse, not one canonical author post.
- Roc's abilities/coherence detail: the authoritative design doc was removed during the ongoing Zig
  compiler rewrite; only the FAQ fragment is currently primary. Treat deeper coherence rules as in flux.
- Roc's strict-evaluation rationale: talks only, not in written docs (flagged inline above).
- OCaml modular implicits (the "diagnosis succeeded, shipping failed" story): status confirmed via a
  core-team post on discuss.ocaml.org (https://discuss.ocaml.org/t/the-status-of-modular-implicits/6680)
  — a maintainer statement, not the compiler manual.

---

## Meta-observations (extending the Haskell doc's closing lesson)

The Haskell doc's meta-lesson: *a wrong default plus an opt-out fix = permanent ecosystem cost.* The
cross-language round adds two, each strongly multiply-sourced:

1. **Decide the ad-hoc-polymorphism / coherence mechanism before v0.** OCaml's modular implicits have
   stalled ~12 years (never in the compiler); F#'s SRTP-as-ersatz-typeclass is its "most trouble-prone
   feature"; Elm's magic `comparable`/`number` variables can't be user-extended (the worst of both
   worlds). Bolting coherent overloading on afterward reliably fails. This directly raises the stakes on
   Sprout's open #12 (dispatch visibility) and #13 (modules vs typeclasses) — and on the *active*
   dispatch-soundness bug in §B.

2. **Strict-by-default is now the argued cross-language consensus.** PureScript, Idris 2, and Roc each
   chose it with a stated performance/predictability rationale (§F). Sprout is firmly on the right side;
   the choice should be cited, not merely asserted.

A third, narrower observation specific to Sprout's flagship: **effect rows are only worth their weight
when inference carries it** (§A1 vs §A2). PureScript's manual-monad rows were removed; Koka's inferred
rows succeeded. Sprout's effect model must become *inferred and enforced* on ordinary function bodies
before `!{…}` grows past `IO` — otherwise it inherits PureScript's "tricky to line up, little benefit"
verdict without Koka's payoff.
