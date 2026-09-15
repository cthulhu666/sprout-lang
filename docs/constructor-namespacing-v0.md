# Constructor namespacing and disambiguation (v0 design)

**Status:** PROPOSED — not designed to completion, not approved, nothing
implemented. This records the problem, what was measured, and the prior art, so
the decision starts from evidence rather than from instinct. Filed 2026-09-15.

## 1. Problem

A constructor name lives in the flat value namespace of its module. Two types in
one module cannot both declare a constructor called `Ok`, so one of them must
invent a different name. Module qualification does not help — the collision is
*inside* one module.

The cost is invisible in the code, because it was paid upstream in the naming.
Counting constructor-name collisions across `stdlib/`, `ide/` and `testsupport/`
finds almost none — 8 of 652 distinct names (1.2%), none of them within a single
module, and all 8 already separated by module qualification. That number means
only that the constraint was obeyed, not that it was cheap.

## 2. What it actually cost

Measured over `stdlib/`, `ide/` and `testsupport/` — 307 types, 585 sum-arm
constructors (the 652 above additionally counts record and `wrap` constructors,
which are named after their type and so cannot collide this way):

- **394 constructors (67%) carry a prefix or suffix shared by every sibling** —
  a namespace spelled by hand. `types.Type` is `TVar TConst TApp TFunc TThunk
  TTuple`; the `T` *is* `Type.`. `sprout_ir.IROp` prefixes `IR` onto all 50+ of
  its constructors. `ast.Decl` suffixes `Decl` onto all nine; `ast.Expr`
  suffixes `Expr` onto all 21.
- Dropping that hand-rolled namespace makes **20 names collide inside a single
  module** — distinct concepts the flat namespace forced apart:

  | module | name | types that wanted it |
  |---|---|---|
  | `compiler.infer` | `Ok` | **14** |
  | `compiler.infer` | `Err` | **13** |
  | `compiler.compiler` | `Ok` / `Fail` | 4 each |
  | `compiler.typed_ast` | `Ok` / `Err` | 3 each |
  | `compiler.ast` | `Var` `Int` `Bool` `String` `Char` `Unit` `Tuple` | `Expr` vs `Pattern` |
  | `compiler.ast` | `Record` | `Decl` vs `Expr` |
  | `json` | `Cons` / `Nil` | `JsonArray` vs `JsonObject` |
  | `tui.event` | `Left` / `Right` | `Key` vs `MouseButton` |

`infer.sprout` alone has fourteen result types, each forced to invent a unique
prefix: `InferOk`, `CallOk`, `GroupOk`, `FinishOk`, `DischargeOk`, `AnnoOk`,
`CompOk`, `TypedDeclOk`, `InstanceMethodOk`, `AliasesOk`, …

Method, to reproduce: collect each type's constructors, strip the longest
word-boundary prefix (else suffix) shared by *all* of them — that shared affix is
the hand-rolled namespace, whether or not it is named after the type — then group
the remainders per module and report any claimed by two types.

## 3. Goals and non-goals

**Goals.** Let two types in one module declare the same constructor name. Keep
use sites no longer than today's. Keep the rule teachable — a beginner-friendly
language cannot afford a naming rule with three carve-outs.

**Non-goals.** Changing what `(..)` means. `(..)` governs *whether* a
declaration publishes its constructors (spec §5.6.4); this is about *how they
are spelled*, and the two compose. Also not in scope: the separate ruling on
whether `import M (T)` brings `T`'s constructors into scope.

## 4. Prior art

Verified against primary sources, 2026-09-15.

| Language | Constructors namespaced by type? | Escape hatch |
|---|---|---|
| **Rust** | Yes — "referenced by a path from the enumeration name" | `use Examples::*`, which "Creates aliases to all variants" |
| **Swift** | Yes — `CompassPoint.west` | Leading dot where the type is known: "you can drop the type when setting its value" |
| **Scala 3** | Yes — "`Color.Red` … members of `Color`s companion object" | `import Planet.*` |
| **F#** | **No by default** — "the case identifiers can be used without qualifying them" | Opt-in per type via `[<RequireQualifiedAccess>]` |
| **OCaml** | No — flat in the enclosing scope, later shadows earlier | Type-directed disambiguation |
| **Haskell** | No — module namespace | `T(..)` in import/export lists |

The axis that matters is not "namespaced or not" but **whether the short form is
reachable at the use site**. Swift namespaces *and* infers the leading dot, so
namespacing costs nothing where it is used. Rust and Scala 3 namespace without
it — and `use Enum::*` / `import Planet.*` appear at the top of nearly every file
that matches on one, which is users unqualifying it back by hand after paying the
migration.

OCaml's disambiguation has a documented wart: with nothing to go on it "picks the
last defined type amongst all locally valid choices" and "sticks to this choice,
even if it leads to an ulterior type error". Their manual recommends annotations
over relying on it.

Sources: [Rust Reference §Enumerations](https://doc.rust-lang.org/reference/items/enumerations.html),
[The Swift Programming Language §Enumerations](https://docs.swift.org/swift-book/documentation/the-swift-programming-language/enumerations/),
[Scala 3 Reference §Enums](https://docs.scala-lang.org/scala3/reference/enums/enums.html),
[F# Language Reference §Discriminated Unions](https://learn.microsoft.com/en-us/dotnet/fsharp/language-reference/discriminated-unions),
[OCaml Manual §1.4 The core language](https://ocaml.org/manual/5.4/coreexamples.html),
[Haskell 2010 §5.2](https://www.haskell.org/onlinereport/haskell2010/haskellch5.html).

## 5. Two designs

### A. Namespace constructors under the type, with contextual resolution

`Color.Red` at the declaration; `.red` or a bare `Red` where the expected type is
known. Buys **discoverability** — `Color.` completes in the IDE, which is a
motivation the name-pressure evidence does not speak to.

Costs, specific to Sprout:

- A **second** qualification level. Constructors are already module-qualified, so
  `ast.RecordDecl` becomes `ast.Decl.RecordDecl`.
- Records and `wrap` name their constructor after the type: `type P = (x: Int)`
  declares `P`. Under the rule that is `P.P`, so it needs a carve-out.
- Migration across **29,722 use sites** over 834 distinct constructor names
  (`stdlib/`, `ide/`, `testsupport/`, `examples/`, `tests/`; declarations and
  comments excluded). 12,670 of those are already module-qualified and would
  take the second level on top.
- **Without contextual resolution it makes use sites worse**, not better —
  `| infer.InferOk x ->` becomes `| infer.InferResult.Ok x ->`. Leading-dot
  inference is therefore a requirement, not a follow-up, and it means propagating
  an expected type into *pattern* position.

### B. Keep the flat namespace, add type-directed disambiguation

OCaml's model. Fourteen types in one module may all declare `Ok`/`Err`;
`match r with | Ok x ->` resolves from the scrutinee's type, and `Ok(v)` in a
function returning `CallResult` resolves from the expected type.

- Gets the **whole measured win** in §2.
- **Zero migration.** `InferOk` keeps working and merely stops being necessary;
  a type sheds its hand-rolled prefix when someone touches it.
- No second qualification level, no `P.P` carve-out.
- Reuses machinery that exists: the expected type at a construction site and the
  scrutinee type at a match site are both computed today. **Record fields are
  already disambiguated exactly this way** — `infer` keys them as
  `@rec:<Type>:<field>` — so this applies the existing rule to constructors.
- Does **not** buy discoverability; there is no `Color.` to complete.

**Diverge from OCaml on one point:** reject an unresolvable ambiguity rather than
picking the last-defined type. Strictly safer, costs no correct program, and
avoids the wart OCaml documents against itself.

B's hard edge: in `match e with | Ok x ->` where `e`'s own type is still a
metavariable, there is nothing to disambiguate against and an annotation is
required. This is OCaml's situation exactly.

## 6. Open questions

1. ~~Which motivation dominates?~~ **Answered 2026-09-15: both.** Name pressure
   *and* IDE discoverability. B cannot deliver the second, so the target is A —
   which makes question 2 blocking rather than optional. B stays on the table
   only as a cheaper first step if A is deferred: it is forward-compatible,
   since disambiguation is what makes A's short form usable anyway.
2. If A: is leading-dot inference in scope from the start? The prior art says a
   no here lands Sprout in the Rust/Scala bucket where users re-unqualify by hand.
3. If B: how far does the expected type have to be pushed before the common cases
   resolve without annotations? Worth prototyping against `infer.sprout`'s
   fourteen result types, which are the worst case in the tree.
4. Either way, how does this interact with the undecided `import M (T)` ruling
   (`BACKLOG.md`, §6 Modules and Packaging)?
