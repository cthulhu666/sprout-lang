# Linear Types — M4.1 Scoping (Syntax + AST + `types.Type` + pretty-printer)

Status: **DECIDED — per-declaration; implementation in progress**. Prepared 2026-08-01.

> **Decision (2026-08-01, Kuba):** attach linearity **per-declaration**, notation
> `type linear Name = …` (contextual marker between `type` and the name, mirroring
> `type alias`). This deviates from the parent plan's per-use `linear τ` straw-man; the
> deviation was approved because per-declaration is M5's own endpoint (linear
> constructors), keeps linearity out of type inference (the plan's #1 flagged risk), and
> needs no lexer change. Trade-off knowingly accepted: no context-dependent linearity
> (linear *view* of otherwise-normal data), which is off-roadmap and addable later.
>
> **Representation (2026-08-01, Kuba):** the AST field is a `Multiplicity` enum
> (`Unrestricted | Linear`), **not** a `Bool`. Usage discipline is inherently non-binary
> (cf. Idris 2 QTT 0/1/ω, Linear Haskell One/Many); the enum seeds the two cases M4.1 acts
> on and extends to Affine/Relevant/Erased later without re-touching the field type or
> re-auditing construction sites. A `Bool` would force that churn twice. Defined in
> `ast.sprout` (`export type Multiplicity`).
>
> **Pretty-printer (M4.1 scope refinement):** the `linear` render prefix is **deferred to
> M4.2**. Under per-declaration the multiplicity lives in the env (a `@linear:` sentinel),
> not in `types.Type`, so `type_to_string : Type → String` cannot reach it — and M4.1
> (accept, don't enforce) has no consumer that prints a type. The first real consumer is
> M4.2's enforcement diagnostics ("linear value used twice"). Adding an env-threaded
> renderer now would be dead code.
>
> **Failing test (RED, verified 2026-08-01):** `tests/stdlib/test_linear_type_decl.spr`.
> On the seed baseline it fails with `Constructor File: Type mismatch: File vs linear
> $t2186` — `type linear File` mis-parses as *type `linear`, param `File`*. A marker-free
> control of the same file PASSES, isolating the delta to the marker. Goes GREEN when the
> parser recognizes the marker.

Original scoping (retained below for the prior-art survey + rejected-option record).

---

Parent plan: [`gc-rooting-model-c-plan-2026-06-02.md`](./gc-rooting-model-c-plan-2026-06-02.md) §Milestone 4.
Backlog anchor: `BACKLOG.md` "Model C GC-rooting plan" item.

This doc scopes **M4.1 only**: make the language *accept* linear-type annotations, carry
them through AST + `types.Type`, and render them in the pretty-printer. **The checker
accepts but does not enforce** consume-exactly-once — enforcement is M4.2 (use-count
tracking), M4.3 (match-arm convergence), M4.4 (call propagation).

M4.1's deliverable is deliberately tiny. Its *only* hard decision is the one the parent
plan left open as "notation TBD": **where does `linear` attach?** That fork drives every
edit below, so it must be settled before implementation.

---

## 1. Problem statement

The Model C plan's endgame (M5) makes GC rooting a structural theorem by giving the
internal Sprout-IR *linear* heap types (`Heap τ`, `Rooted τ`). Before that, M4 ships
linear types as a **user-facing** feature — independently useful for file handles,
channels, and capability tokens (consume-once resources). M4.1 is the syntactic +
representational groundwork.

## 2. Goals / non-goals

**Goals (M4.1):**
- A surface notation for "this is a linear type", parsed into the AST.
- The linearity property is available at the `types.Type` / inference layer.
- `type_to_string` renders it (error messages / hover).
- Checker *accepts* it without error; existing programs are unaffected.

**Non-goals (deferred to later M4 sub-milestones / M5):**
- Any *enforcement* of use-once (M4.2+).
- Match-arm convergence checking (M4.3).
- Linear-argument call propagation (M4.4).
- **Higher-order / function-arrow linearity** — explicitly out of scope per the parent
  plan (§"What's intentionally NOT in this plan"); Linear Haskell shipped this incomplete.
- **Polymorphism over linear types** — when a type variable is instantiated to a linear
  type, must the generic body treat it linearly? Austral needed a type-parameter *universe*
  system for exactly this. This is an M4.4-ish design item; M4.1 may start conservative
  (forbid linear types flowing through polymorphic positions) but does not solve it.

## 3. Prior-art survey (all primary-sourced)

| Language | Linearity attaches to… | Notation | Relevance to Sprout M4 |
|---|---|---|---|
| **Austral** | the **type declaration** | `record Pos: Linear is …` | ★ First-order, use-exactly-once; ~600-line checker whose four control-flow rules *are* M4.2–4.4. |
| **Linear Haskell** (GHC `-XLinearTypes`) | the **function arrow** | `a %1 -> b` (`⊸`) | Higher-order — Sprout explicitly **defers** this. |
| **Idris 2** (QTT) | the **binder** | `(1 x : a) -> …` (0 / 1 / ω) | Binder-level quantities; inference-heavy. |
| **Clean** | the **type** (uniqueness — a dual) | `*World` | Uniqueness ≠ linearity; auto-propagated through arrows. |
| **Rust** | nothing (affine default) | move semantics; `Copy` opts out | Affine (≤1 use), *not* linear (=1 use). |

Sources (verified against primary docs):
- GHC LinearTypes: <https://downloads.haskell.org/ghc/latest/docs/users_guide/exts/linear_types.html>
- Austral intro: <https://borretti.me/article/introducing-austral>
- Idris 2 multiplicities: <https://idris2.readthedocs.io/en/latest/tutorial/multiplicities.html>
- Clean 2.2 language report: <https://clean.cs.ru.nl/download/doc/CleanLangRep.2.2.pdf>

**Reading of the survey:** the two poles that bear on Sprout's decision are **Austral**
(attach at the *type declaration*, first-order) and **Linear Haskell** (attach at the
*function arrow*, higher-order). The M4 plan is first-order, use-once, match-arm
convergence, call propagation — i.e. structurally Austral. The plan's own straw-man
`linear τ` phrasing implies a *per-use* annotation, which is closer to Idris/Linear-Haskell
binding annotations and is the point this doc reopens.

## 4. The one decision: attachment model

### Option A — Per-declaration (Austral-style) — **recommended**

Mark linearity once, at the type's declaration; every value of that type is linear
everywhere. Sprout already has the exact syntactic mechanism: `type alias Name = …`
treats `alias` as a **contextual identifier after `type`** (not a reserved word — see
`parser.sprout:1916` `is_alias_type_decl`). So:

```
type linear File = File Int            # ADT
type linear Pos = (x: Int, y: Int)     # record
```

`linear` is recognized as a contextual identifier immediately after `type` — **no lexer
change, no new reserved word, no risk to existing identifiers.**

Why recommended:

1. **It is what M5 requires.** M5 makes `Heap τ` / `Rooted τ` linear — those are linear
   *type constructors*, i.e. linearity-by-construction of every value. That is inherently a
   per-*declaration* property. Per-declaration is not a deviation from the plan; it is the
   plan's own endpoint. The straw-man's per-use phrasing is the part that's misaligned
   with M5.
2. **It removes linearity from inference.** Linearity becomes a static property looked up
   from the type constructor, never unified or inferred — directly sidestepping the parent
   plan's single biggest flagged risk (HM does not infer linearity). *Caveat:* this removes
   it from *unification*; the polymorphism-over-linear-types interaction (§2 non-goals)
   remains, but is deferrable.
3. **The motivating resources are inherently always-linear.** Files, channels, capability
   tokens, and the M5 IR types are linear *by nature* — per-use annotation would be pure
   noise on every mention of them.
4. **`types.Type` barely changes for M4.1.** Linearity of a `types.Type` is a lookup on its
   head `TypeId` against the declaration environment. **No new `types.Type` variant, no
   per-value flag, no unifier change** for M4.1. The semantic bit isn't even needed until
   M4.2 enforcement.

What Option A **gives up** (state plainly, it's a real cost): it **cannot express
context-dependent linearity** — a linear *view* of otherwise-normal data (e.g. the classic
in-place mutable-array-update pattern, where an array is linear only while being updated).
Every value of a linear-declared type is linear everywhere. For the stated motivators this
costs nothing, but it forecloses that pattern without a later, separate mechanism.

### Option B — Per-use (`linear τ`, the plan straw-man)

Annotate linearity at each type-expression position (binding/param/return):

```
fn close(f: linear File) -> Unit = …
let h: linear Handle = open(path)
```

- More flexible: the same underlying type can be linear in one position, unrestricted in
  another (enables the in-place-update pattern Option A forecloses).
- But: needs a real `types.Type` representation and **unifier semantics** for
  linear-vs-nonlinear, i.e. it re-introduces the inference dimension the plan flags as the
  top risk.
- Misaligned with M5 (which wants linear *constructors*, not per-use annotations).

### Invasiveness (honest numbers, `stdlib/compiler/`)

Raw match-site counts are **not** a landslide — the case for A is architectural, not "fewer edits":

| | Per-declaration (A) | Per-use (B) |
|---|---|---|
| Lexer | **no change** (contextual `linear` after `type`) | add `linear` keyword (`lexer.sprout:32-51`) |
| Parser dispatch | 1 site, mirrors `is_alias_type_decl` (`parser.sprout:1911`) | `parse_type_atom` + `match_keyword` (`parser.sprout:252`) |
| New AST surface | linearity bit on `TypeDecl`/`RecordDecl` — **36** existing match sites if added as a constructor field | new `TypeExpr` variant `TypeLinear` — **19+** exhaustive `TypeExpr` match sites |
| `types.Type` | none for M4.1 (env-lookup by `TypeId`) | new variant/flag **+ unifier semantics** |
| `iface_codec` | 1 bit per type decl | serialize new `TypeExpr` variant |
| Inference risk | none in M4.1 (lookup, not inference) | re-opens the flagged HM-linearity risk |

Note the 36 vs 19: adding a *field* to the widely-matched `TypeDecl`/`RecordDecl` touches
more destructure sites than a new `TypeExpr` variant would. Two ways to avoid that churn
under Option A, decided at implementation time:
- **A-field:** add the `Bool` field; mechanically update 36 destructures (delegable, purely
  additive `_`/`false`). Most explicit.
- **A-sidetable:** collect linear type names into the declaration environment during decl
  processing without changing constructor arity. Less churn, slightly less explicit.

The A-vs-B decision does **not** depend on this sub-choice; it's an M4.1 implementation detail.

## 5. Syntax & semantics impact

- **Option A:** one new decl shape (`type linear …`), contextual keyword. No effect on any
  existing syntax. Semantics: a static "is-linear" property attached to a `TypeId`.
- **Option B:** new prefix in every type-expression position. Semantics: linearity carried
  per type occurrence, with unification rules.

## 6. Type-system impact

- **A (M4.1):** none beyond a lookup table. Inference untouched. (Enforcement lands M4.2+.)
- **B (M4.1):** requires `types.Type` to represent linearity and the unifier to have a
  policy on it, even to merely "accept."

## 7. Error-message impact

`type_to_string` (`types.sprout:401`) / `type_to_string_aux` (`types.sprout:381`) render
the marker. Under A, rendering can consult the linear-set when printing a `TConst`/`TApp`
head; under B, a `TLinear`-like wrapper prints its prefix directly.

## 8. Compatibility / migration

Both options are backward-compatible (new syntax only). Option A additionally guarantees no
identifier breakage: `linear` stays a plain identifier everywhere except immediately after
`type` (grep confirms zero non-comment uses of the bare word `linear` in Sprout sources).

## 9. Tests added/updated (M4.1)

- Parser: `type linear …` (A) / `linear τ` in signatures (B) parses; round-trips through
  `type_to_string`.
- Parser negative: malformed marker position is a clean diagnostic.
- Checker: a program using the marker type-checks unchanged (accept-but-don't-enforce);
  an existing non-linear program is byte-identical in emitted IR.
- Coverage: the touched files (`parser.sprout`, `infer.sprout`/`type_from_ast`,
  `types.sprout`) each gain at least one new case per the coverage-gap rule.

## 10. Docs/spec

- `docs/spec-v0.md`: add a linear-types section marked **experimental** (enforcement
  pending). Normative status: syntax stabilized in M4.1, semantics (enforcement) in M4.2+.
- This doc becomes the design record; update on the §4 decision.

---

## Concrete edit anchors (surface map, for whichever option is chosen)

*Type-expression surface (per-use / Option B):*
- `TypeExpr` ADT — `ast.sprout:8-13` (add `TypeLinear TypeExpr`).
- Type-expr parser leaf — `parse_type_atom` `parser.sprout:252-262`; arrow/cont
  `parser.sprout:186-200`.
- Lexer keyword list — `is_keyword` `lexer.sprout:32-51`.
- AST→Type — `type_from_ast` `infer.sprout:111-119`; reverse `type_to_typeexpr`
  `infer.sprout:1216`.
- Exhaustive `TypeExpr` match sites to extend: `collect_te_vars` `infer.sprout:179`,
  `validate_te` `infer.sprout:3946`, `type_expr_head_name`/`unify_type_expr`/
  `substitute_type_expr` `resolve.sprout:650/662/693`, `type_expr_is_non_heap_scalar`
  `type_kind.sprout:38`, plus `iface_codec.sprout` encode/decode.

*Declaration surface (per-declaration / Option A):*
- `Decl` ADT — `TypeDecl`/`RecordDecl` `ast.sprout:142-143`.
- Decl dispatch — `parse_type_decl_or_record` `parser.sprout:1911`; ADT body
  `parse_type_decl` `parser.sprout:1583`; record body `parse_record_type_decl`
  `parser.sprout:1742`; contextual-keyword precedent `is_alias_type_decl`
  `parser.sprout:1916`.
- Declaration environment population — `register_type_decl` (`infer.sprout:4311`,
  `typecheck_decl`) + `register_type_decl_raw` (pre-scan, `infer.sprout:4256`) for ADT/wrap;
  `register_record_fields` (`infer.sprout:4315-4317`) for records.

**Storing the linear-set — sentinel-key idiom (no env threading).** The scheme env already
holds per-decl metadata as sentinel-keyed entries, e.g. `"@arity:" ++ name`
(`infer.sprout:4239`). Store linearity the same way: a linear `Name` registers
`"@linear:" ++ Name`. "Is `TypeId t` linear?" = `dict_get("@linear:" ++ type_id_name(t),
env)`. **No new env parameter, no threading-signature changes, no `types.Type` variant.**

## Implementation sequence (per-declaration, TDD-first)

1. **[DONE] Failing test.** `tests/stdlib/test_linear_type_decl.spr`, RED verified against a
   marker-free control (delta isolated to the marker).
2. **Parser — recognize the marker.** `parse_type_decl_or_record` (`parser.sprout:1911`):
   add `is_linear_type_decl` (mirror `is_alias_type_decl` `:1916`, peek `tok_at(i+1) ==
   "linear"`), consume the `linear` ident, thread an `is_linear` bool into
   `parse_type_decl` / `parse_record_type_decl`. Compose with `type linear alias`? Out of
   scope — `linear` + `alias` combos rejected for M4.1.
3. **AST — carry the field.** [DONE] Added `export type Multiplicity (Unrestricted |
   Linear)` and a `Multiplicity` field on `TypeDecl` / `RecordDecl`, inserted **before**
   the trailing `SourcePos` (preserving the "pos is last" invariant; `decl_pos` updated).
   **Actual footprint: 50 occurrences across 12 files**, of which only 6 are construction
   sites; the other 44 are single-`_` insertions. The 6 mechanical peripheral files were
   delegated to a Sonnet subagent; the delicate files (parser, infer, bundler, iface_codec)
   stayed here. Compiler exhaustiveness + the stage-2 build guarantee none are missed.
4. **Register — populate the linear-set.** [DONE] `mark_type_multiplicity(name, mult, env)`
   sets `@linear:<name>` when `Linear`; called inside `register_type_decl_raw` (covers both
   ADT passes) and at the record-registration site. `type_name_is_linear` is the lookup.
5. **Interface round-trip.** [DONE — not in original plan] `iface_codec` encodes the
   multiplicity as a quoted tag between `deriving_classes` and `pos`, and decodes it
   symmetrically, so a `type linear` imported across a module boundary stays linear.
6. **Pretty-printer.** [DEFERRED to M4.2] See decision block above — no M4.1 consumer.
7. **Accept-don't-enforce check.** [DONE] `tests/stdlib/test_linear_type_decl.spr` compiles
   and runs through stage-2 (a linear record field is read twice — accepted; M4.2 will
   revisit). RED→GREEN verified.
8. **DoD gates (compiler-source change).** fmt + lint → `refresh-seed` (delete stale stage
   binaries first) → full `just test` → smoke shapes → bundle smoke → bootstrap fixed-point
   → example canary. Reseed BEFORE `just test` (compiler-source ordering).

**Gotcha hit:** a do-block `let` accepts only simple-var bindings — `let (mult, i2b) = …`
(tuple destructure) is rejected as "refutable `let` in a do block requires an `else`". Split
into two single-value helpers (`linear_marker_mult` + `skip_linear_marker`).

**Deferred to M4.2+ (not this milestone):** use-count enforcement, match-arm convergence,
call propagation, `wrap linear`, polymorphism-over-linear-types, spec section promotion from
experimental → normative.

*Semantic layer (both):*
- `types.Type` ADT — `types.sprout:49-54`.
- Pretty-printer — `type_to_string` `types.sprout:401`, `type_to_string_aux`
  `types.sprout:381`.
