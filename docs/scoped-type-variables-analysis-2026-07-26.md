# Scoped Type Variables in Sprout — Analysis & Deferral (2026-07-26)

**Status:** analysis / no current demand. Non-normative. This doc records why
Sprout has no `ScopedTypeVariables` equivalent, what the feature would buy, and
the single concrete trigger that would justify revisiting it. It does **not**
propose an implementation.

## 1. The question

GHC's `ScopedTypeVariables` extension does two linked things:

1. Makes an explicit `forall a.` in a signature bind `a` **lexically** into the
   function body.
2. Lets a **type annotation inside the body** refer to that same `a`, rather
   than treating a body-level `a` as a fresh, unrelated variable.

Does Sprout have an equivalent? **No — and the feature does not currently
apply**, because both halves depend on machinery Sprout v0 lacks.

## 2. Why it does not apply today

- **No user-written `forall`, so there is no lexical binder to scope.** Sprout
  is classic Hindley–Milner with *implicit* quantification: `generalize` makes a
  value's free type variables universally quantified at definition, and
  `instantiate` replaces them with *fresh* unknowns at each use site
  (`docs/hm-typechecker.md:92-93`). You never write `forall a.` in source, so
  there is no lexical binder for anything to scope into.
- **No local type annotations, so there is nothing in a body to scope *to*.**
  `where` bindings are value-only — *local type annotations are not part of v0*
  (`docs/spec-v0.md:124`). Even `FnDecl` param/return annotations are "not yet
  validated" (`docs/spec-v0.md:309-312`). The second half of the feature — a
  body annotation reusing the signature's `a` — cannot even be written.
- Sprout *does* quantify a singleton **effect** variable `!{e}` in function
  types (`docs/spec-v0.md:549`), but that is a narrow, purpose-built effect-row
  binder, not general lexically-scoped type variables, and it is not
  user-scoped into bodies.

**Every use case below first requires local type annotations to exist.** Scoped
type variables are meaningless until `:: a` can be written somewhere in a body.
That prerequisite is the real gating decision, upstream of this one.

## 3. It was never deliberated on its own merits

`ScopedTypeVariables` appears exactly once in the repo's design record:
`docs/haskell-lessons-learned.md` §10 ("GHC extension proliferation — no stable
modern standard", lines 237–258). There it is cited only as *evidence* that even
"basic modern Haskell" is fragmented behind opt-in flags — Haskell 2010 predates
it, `GHC2021` exists to paper over the gap. The Sprout implication drawn is a
**meta-decision** (line 257): *"Sprout should have exactly one language version
at any point in time."*

So the recorded stance is against the **delivery mechanism** (à-la-carte
extensions), not a reasoned position on lexically-scoped type variables
themselves. The *semantics* question — "should signature type variables be
visible in bodies?" — remains **open and undecided**; only the *packaging*
question is settled (if the capability ever lands, it must be one uniform
always-on behavior, never a flag).

## 4. What signature-tyvars-in-body actually buys

Four use-case categories, which collapse into **two** motivations. For each, the
honest test is whether plain HM inference could already solve it — because that
is whether scoping *adds* expressive power.

| # | Use case | Genuine gain? | Sprout relevance |
|---|----------|---------------|------------------|
| 1 | Pin a **return-polymorphic** value that has no argument to drive inference (`mempty`/`minBound`/`read`/`Proxy`/`sizeOf (undefined :: a)`) | **Yes** — inference genuinely cannot solve it | HIGH *in principle* (Sprout has return-position dict resolution) |
| 2 | Name an **existential/GADT** hidden type from a pattern match, to annotate in the branch | **Yes** — impossible otherwise | None (no GADTs/existentials) |
| 3 | Give an explicit type to a local `where`/`let`/lambda binding that must agree with the outer variable (worker/wrapper, accumulators, polymorphic recursion) | Mostly ergonomic; load-bearing only for polymorphic recursion / forced monomorphism | MEDIUM (this is the spot Sprout's missing local annotations would first bite) |
| 4 | Type-application / `Proxy` passing to select an instance | Same root as #1 | Low/hypothetical |

**The two real motivations:** (a) *there is no value-level information to infer
the type from* (#1, #2, #4), and (b) *you want to write the type down anyway*
(#3). Only (a) is a genuine expressiveness gain.

The canonical flagship is #1's `sizeOf (undefined :: a)`: `undefined` carries no
type, there is no other occurrence to unify against, so the annotation naming the
signature's `a` is the only anchor. It cannot be rewritten to avoid the feature.

## 5. Empirical finding — zero current demand in Sprout

We audited `stdlib/` and `stdlib/compiler/` for the two workaround signatures:
return-polymorphic methods used in isolation, and witness/proxy arguments
carried purely to fix a type. Result: **no code needs to pin a return-poly type
inside a body and cannot.**

- **`mconcat` (`stdlib/prelude.sprout:646-647`)** is the most at-risk function —
  it seeds a fold with `empty()`, typed `-> t` (return-only, the `mempty`
  shape). It resolves with **no annotation** because two sibling occurrences flow
  the type in: `acc ++ x` forces the accumulator, and `xs : f a` fixes the
  element type. Inference reaches it. This is category #3, not #1.
- **`empty()` (`prelude.sprout:622`)** and **`from_ordinal : Int -> Maybe a`
  (`prelude.sprout:365`)** are genuinely return-polymorphic, but every use has a
  sibling constraint, and `from_ordinal` is only ever *machine-generated* by the
  deriving pass with the concrete return type spelled out
  (`stdlib/compiler/deriving.sprout:373`) — no hand-written body pins it.
- The **`witness` threading in `stdlib/compiler/ast_to_ir.sprout:4982`** is *not*
  a workaround — it is the compiler's dictionary-passing plumbing (the checker
  resolves the `Semigroup` instance and lowering threads it as the trailing
  argument). It is the *implementation* of instance resolution, the opposite of
  a user reaching for a witness because they cannot annotate.
- The one literal `dummy:` argument
  (`stdlib/compiler.sprout:220`, `analysis_session_create`) is a placeholder
  *value* for an FFI call, not a type witness.

**Why the absence is structural, not coincidental:** ordinary application code
flows types *up* (a value's type is inferred from how it is used), which is
exactly why `mconcat` sidesteps the feature. Two current properties actively
suppress demand: no local annotations means code must be shaped so inference
flows; and deriving emits return-poly instances with concrete types, so the
compiler never leans on body-level pinning.

## 6. What kind of library would need it

Scoped type variables become load-bearing in exactly one situation:
**a function computes something *from* a type parameter without ever producing a
value of that type in a position inference can see** — information flows *down*
from the type to a value (a size, tag, count, representation, or a
freshly-decoded/generated value), against the usual up-flow.

Library categories that hit it:

1. **FFI / low-level memory layout** (`Storable`-style `sizeOf`/`alignment`/`Ptr`
   arithmetic, C-struct bindings). The purest case.
2. **Reflection / type-level programming** (`Proxy`, `natVal`/`symbolVal`,
   fixed-size vectors, units-of-measure).
3. **GADT / existential interpreters** (typed ASTs, tagless-final EDSLs).
4. **Serialization / codecs** (`binary`'s `Get a`, JSON `parseJSON :: Value -> Parser a`,
   DB row decoders). Borderline — often solved by feeding decoded fields straight
   to a constructor; bites only for length-prefix / size / tag-dispatch helpers.
5. **Generics-driven value production** (`Arbitrary`'s `arbitrary :: Gen a`,
   generic `def`, enum bounds).

Most of these need *far more* than scoped type variables to exist (raw memory,
type-level naturals + kinds, GADTs). Scoped type variables are connective tissue
that matters only *once you are already in that world* — which is precisely why
Haskell made it an extension rather than a base feature.

## 7. Judgment for Sprout

Given Sprout's stated goals (`AGENTS.md`: "strong safety with beginner-friendly
ergonomics"), three of the five categories (FFI-struct marshalling, type-level /
reflection, GADTs) are advanced corners Sprout may deliberately never chase — and
if it doesn't, the demand never materializes.

**The one realistic trigger is category #4: a binary serialization / codec
library.** Sprout already has the seed — a `Bytes` type (`stdlib/bytes.sprout`)
and a return-polymorphic decoder shape (`from_ordinal`). A future `Decode` /
`Serialize` class with `decode : Bytes -> Maybe a` is where a length-prefix or
tag-dispatch helper would first need to pin a type from the enclosing signature
with no sibling occurrence to drive it.

**Deferral rationale:** no current demand; the feature has no home in the
codebase today. Revisit only when (a) local type annotations land (the hard
prerequisite, `spec-v0.md:124`) **and** (b) a return-polymorphic method is
consumed in isolation — realistically, when someone writes a binary codec
library for Sprout. Until then this stays a solution waiting for a problem that
Sprout's current design happens to prevent.

## 8. References

- `docs/spec-v0.md:124` (no local type annotations), `:309-312` (FnDecl
  annotations unvalidated), `:549` (effect-var quantification).
- `docs/hm-typechecker.md:92-93` (implicit generalize/instantiate).
- `docs/haskell-lessons-learned.md:237-258` (§10 extension proliferation — the
  only prior mention).
- `stdlib/prelude.sprout:365` (`from_ordinal`), `:622` (`empty`), `:646-647`
  (`mconcat`).
- `stdlib/compiler/deriving.sprout:373` (generated `from_ordinal`).
- `stdlib/compiler/ast_to_ir.sprout:4982` (Semigroup `witness` = dict-passing).
