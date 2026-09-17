# Constructor namespacing and disambiguation (v0 design)

**Status:** PROPOSED — not designed to completion, not approved, nothing
implemented. This records the problem, what was measured, and the prior art, so
the decision starts from evidence rather than from instinct. Filed 2026-09-15;
§7 added 2026-09-17 after a defect made the import side urgent.

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

**Non-goals.** Changing what `(..)` means **on a declaration**. There it governs
*whether* a declaration publishes its constructors (spec §5.6.4); §1–6 are about
*how they are spelled*, and the two compose. §7 adds a second *position* for the
same marker, on the import, without touching the declaration-side meaning.

The ruling on whether `import M (T)` brings `T`'s constructors into scope was
listed here as out of scope; it has since been found already made (spec-v0 §3
*Imports*, landed 2026-08-29). See §7.1.

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
4. ~~How does this interact with the undecided `import M (T)` ruling?~~
   **Answered 2026-09-17:** that ruling was already made (§7.1). The live
   interaction is with §7.4's import-side marker, which needs the same
   `Type.Ctor` substrate A does but pays none of A's relocation cost — see §7.5
   for the decomposition that falls out.

## 7. Who decides how a constructor is spelled

Added 2026-09-17. §1–6 ask how a constructor is spelled *within* the module that
declares it. This section asks who decides how it is spelled in a module that
imports it. The two turn out to be one question — both are asking who owns the
unqualified namespace — which is why this lives here rather than in its own doc.

### 7.1 The ruling §3 deferred is already made

§3 listed "whether `import M (T)` brings `T`'s constructors into scope" as a
non-goal, pending a ruling. That ruling landed on 2026-08-29 in `78bd373f`.
spec-v0 §3 *Imports* is normative:

> The rule covers names that appear in no import list: a `(..)` type import
> brings the type's constructors with it, so importing two types whose
> constructors share a name collides.

The bundler implements exactly that (`selective_name_bindings` →
`type_bindings`). So the permissive reading is settled and this document should
stop describing it as open. What is *not* settled is whether the importer gets
any say, and that is a different question with a worse defect behind it.

### 7.2 The defect: an additive library change breaks an untouched dependent

Verified 2026-09-17 against `54d96761`, probes in full below.

A library publishes a sum type. A dependent imports it selectively, alongside
some unrelated type that happens to publish a constructor named `Box`:

```sprout
# dependent — note it never writes `Box`
import demo.boxsum (Holder)
import demo.lib (Shape)

fn area(s: Shape) -> Int =
  match s with
  | Circle r -> 3 * r * r
  | Square a -> a * a
```

The library then adds one variant. Nothing is removed or renamed:

```sprout
export type Shape (..) =
  | Circle Int
  | Square Int
  | Box Int        # the only change
```

The dependent, unmodified, stops compiling:

```
ERROR: bundle: `Box` is imported twice in this file, from two different symbols
(`demo.boxsum.Box` and `demo.lib.Box`) — a bare `Box` would silently mean the
last one. Import at most one, or import the modules whole and qualify.
```

**Adding a variant to a published sum type is a breaking change**, for dependents
that never mention the new variant and never mentioned the colliding name. The
dependent cannot defend itself: no syntax imports a type without its
constructors. Its only recourse is to stop importing one of the two modules
selectively.

Two properties combine to produce this, and each is separately defensible:

1. **Permissive import** — naming a type imports its constructors (§7.1).
2. **Eager clash checking** — two bindings of one name are rejected at the import
   line, whether or not the name is ever used.

Probe results, each compiled and run:

| probe | what it does | result |
|---|---|---|
| `p1` | `import demo.pub (Color)`, then bare `Red` | compiles — constructor arrives unlisted |
| `p2` | `import demo.pub (Color, Red, Green)` | compiles — listing constructors is also legal |
| `p3` | `import demo.sealed (Shade)`, then `Dark` | **rejected**, diagnostic cites spec §5.6.1 |
| `p6` | two `(..)` types imported; `Box` never written | **rejected** |
| `p7` | same, but one module imported whole with `as` | compiles — the only escape hatch |
| `p8` | library adds a variant; dependent untouched | compiles → **rejected** |

`p6` is the proof that the check is eager: the file names only the two types and
uses neither constructor. `p3` is the control — constructor hiding still works
and its diagnostic is good.

### 7.3 Who controls publish, and who controls spelling

Verified against primary sources 2026-09-17.

| Language | Producer controls **publish** | Producer controls **spelling** | Consumer controls **spelling** |
|---|---|---|---|
| **Haskell 2010** | Yes — `T` vs `T(..)` in the export list (§5.2) | **No** | Yes — `T(..)`, `hiding`, `qualified` (§5.3.1–5.3.2) |
| **PureScript** | Yes — export list | **No** | Yes — `T(..)`, `T(C1)`, `as`, `hiding` |
| **Rust** | Coarse — variants inherit the enum's visibility; no per-variant modifier | **No** | Yes — `use E::*`, `use E::V`, or path |
| **Scala 3** | Yes — cases are companion members | **No** | Yes — `import Color.*` |
| **F#** | Yes | **Yes** — `[<RequireQualifiedAccess>]` | Coarse — `open` is all-or-nothing |

Four of five give spelling entirely to the consumer, and Haskell 2010 §5.3.2
states the principle outright:

> The ability to exclude the unqualified names allows full programmer control of
> the unqualified namespace

`qualified` is a keyword on the **import**. No exporting module in the table
except F#'s can require qualified use by its clients.

F# is not a counter-example so much as a different constraint. Its only import
form is `open`, which is all-or-nothing, so the consumer has no lever and the
producer is the only party left holding one. The Rust row is the same shape from
the other side: Rust gives producers *less* publish granularity than anyone —
"Enum variants in a `pub` enum are also public by default", with no per-variant
modifier — and nobody misses it, because `use` gives consumers everything at the
other end.

**Publish and spelling are separate powers that sit at opposite ends of the
import.** Sprout currently gives the producer both: `(..)` decides publication,
and having decided it, the constructors land flat in every importer's namespace
with no importer-side control at all. That is the asymmetry behind §7.2.

### 7.4 Proposal: one marker, two positions

Restore the Haskell arrangement Sprout diverged from when it relocated `(..)`
onto the declaration.

**On a declaration, `(..)` means "these constructors are public."** Unchanged.
This is the abstraction boundary — a `wrap` or record that hides its constructor
to hold an invariant keeps doing so (`p3`), and nothing in §7.3 suggests moving
that power.

**On an import, `(..)` means "flat in my file."**

```sprout
import demo.lib (Shape)              # the type only; no bare constructors arrive
import demo.lib (Shape(..))          # all of them, unqualified
import demo.lib (Shape(Circle))      # just this one — PureScript's `Bar(Bar)`
```

A constructor that is public is always reachable as `Shape.Circle`, which is what
makes the first line usable rather than merely safe.

This resolves §7.2 structurally for any importer that wants it: nothing flat
enters the namespace, so a new variant cannot collide. The importer chooses; the
library cannot impose ceremony on files it does not own, and cannot break them by
growing.

**The everyday case is untouched, and not by a carve-out.** `Maybe`, `Result` and
`List` are ambiently in scope rather than imported, so there is no import line on
which to write a marker:

```sprout
fn find(xs: List Int) -> Maybe Int = Just(3)      # no import, no change, ever
```

Ubiquity is what earns a flat name, and the prelude is the only thing in the
language that is genuinely ubiquitous. Everything else is a domain type in
someone's codebase, which is exactly the population that wants `Shape.Circle`.

This does not depend on the prelude's current irregularity. `stdlib/prelude.sprout`
has no `module` header and declares `export type Maybe a =` *without* `(..)`, yet
`Just` and `Nothing` are in scope everywhere — the exemption `BACKLOG.md` has filed
against it. Closing that entry would add `(..)` to the prelude's *declarations*; it
adds no import line to user code, because the prelude is injected rather than
imported. The argument above is about import-side markers, so it survives either
way.

**Rejected: a producer-side qualified-only marker** (`export type Shape
(qualified)`, F#'s `[<RequireQualifiedAccess>]`). It was the first shape proposed
here and it is wrong for Sprout. Once a constructor is public, how a consumer
spells it in their own file is a property of the consumer's namespace; the
producer cannot know what else is in it. Adopting it would also add a third state
to a marker that was just made to mean one thing (`74927ba3`), and would copy
F#'s workaround for a lever Sprout already has.

### 7.5 How this relates to §5's two designs

§7.4 and §5-A both introduce `Type.Ctor`, and they need it for different reasons:
A to let two types in one module share a constructor name, §7.4 to let an
importer skip the flat namespace. Landing either gives the other its substrate.

The relationship to A's cost is the useful part. A's headline expense is
migration across **29,722 use sites**, which is the cost of *relocating*
constructors out of the module namespace. §7.4 relocates nothing — `Type.Ctor`
becomes *additionally* valid, the flat spelling stays legal, and only files that
opt in at the import change. That suggests the affordable decomposition:

1. `Type.Ctor` as additional syntax for any public constructor. Additive, no
   migration, immediately useful to the IDE (`Shape.` completes).
2. Import-side `(..)` per §7.4. Migration bounded by §7.6.
3. Type-directed resolution (§5-B) and leading-dot inference — the thing that
   makes both A and §7.4 short at the use site.
4. Relocation (§5-A proper) only if 1–3 leave a reason for it.

### 7.6 Migration

Measured 2026-09-17 over `stdlib/`, `ide/`, `tests/`, `examples/`, `testsupport/`.
179 types are declared `(..)`. 80 selective imports name one. Of those, **60 sites
across 40 files** use the constructors bare and would need `(..)` added to their
import line.

The split matters more than the total:

- **37 are records**, where the constructor name *is* the type name —
  `import stdlib.http_server (Route)`, then `Route(path = …)`. Writing `Route(..)`
  to obtain `Route` is ceremony that reads like a mistake, and `Route.Route` is no
  better. Records likely want a rule of their own: a record's constructor is its
  type, so naming the type should name it.
- **23 are sums** with distinct constructor names —
  `import stdlib.http (HttpError)`, then `HttpTimeout`. Here the marker informs
  the reader, which is the case the design is for.

An earlier claim in `BACKLOG.md` that no file relied on the permissive behaviour
is dead: it was measured against the scheme-environment path, which was retired
on 2026-08-18, and nothing has enforced the strict reading since.

### 7.7 Independent of all of the above: narrow the eager clash check

§7.2 needs two properties to fire, and the second one — eager checking — can be
narrowed on its own, today, with no syntax change and no migration.

`78bd373f` added the eager check against a real silent failure, recorded in
spec-v0 §3 *Imports*: "a name bound twice kept the last binding, so which symbol
a bare name meant depended on import order". That failure is about names the
programmer **wrote on an import line**. The check can stay exactly there:

1. Two names a file **listed** collide → error at the import, as today.
2. Two constructors that **arrived** implicitly collide → not an error until the
   name is mentioned. Haskell 2010 §5.5.2: "It is not an error for there to exist
   names that cannot be so resolved, provided that the program does not mention
   those names."
3. A **listed** name outranks an **arrived** one → resolved, no error.

Rule 3 is the load-bearing one: what the programmer wrote outranks what they did
not, and that is stable under library growth, because a library can only ever add
*arrived* constructors. It never resolves from how many candidates are in scope —
the property that makes OCaml's last-defined rule fragile (§4).

This fixes §7.2 for every type, including ones whose importers never adopt §7.4,
and nothing becomes silent: every mention of a genuinely ambiguous name is still
an error, now reported at the expression that is ambiguous rather than at two
import lines that do not mention it.

### 7.8 Open questions

1. Records. Should naming a record type in an import list bring its constructor,
   given they share a name — a carve-out, or is `Route(..)` acceptable? 37 of the
   60 migration sites turn on this.
2. Is `hiding` wanted? It is the one consumer lever in §7.3 that does **not** fix
   §7.2 — the dependent still breaks, it just repairs in one line. PureScript and
   Scala manage on the positive forms alone. Recommend deferring.
3. Ordering. §7.7 is free and independent; §7.5 step 1 is additive. Is there a
   reason not to land both before deciding §7.4?
