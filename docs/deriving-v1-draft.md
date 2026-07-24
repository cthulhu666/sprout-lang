# `deriving` — automatic typeclass instance derivation — v1 draft

**Status:** shipped on `feat/deriving` (2026-06-09), with scope narrowed
2026-06-10 to Eq/Ord/ToString. Spec section in §8.6 of `docs/spec-v0.md`.

**Post-v1 addition (2026-07-24): `Enum`.** A fourth derivable class, greenfield
(never part of the reverted Serialize/Deserialize scope), added to kill
hand-written constructor↔Int tag boilerplate (surfaced by `loam.terrain`).
Restricted to nullary-only ADTs; synthesizes `ordinal : a -> Int` and
`from_ordinal : Int -> Maybe a` with declaration order as the source of truth.
`from_ordinal` is return-type-dispatched (the class variable is only in the
return), which the compiler's post-inference dispatch pass
(`resolve_dispatch_typed_expr`, validated by `tests/stdlib/test_return_type_dispatch.spr`)
handles as long as the target type is concrete at the call site — unlike the
reverted `Deserialize`, whose fragility predated that mechanism. Names follow
mainstream-industry convention (`ordinal`, as in Java/Kotlin/Scala 3) rather
than Haskell's `fromEnum`/`toEnum`. See spec §8.6 and `BACKLOG.md` for the
deferred breadth (`values`/`succ`/`pred`).

**Scope revision (2026-06-10):** the initial draft included `Serialize` and
`Deserialize` to unblock the iface PR. Reviewing the design surfaced three
problems with that scope: (a) S-expression format was hardcoded behind a
polymorphic-sounding class name ("serialize TO WHAT?"), conflating polymorphism
with format choice; (b) the `Deserialize` class shape forced an O(N²) parse via
`str_slice` composition; (c) the same shape exposed a typeclass dispatcher edge
case (prog-var-name sensitivity in the `first_concrete_typed_arg_str` fallback).
Rather than ship the wrong shape and migrate later, S/D were removed from v1.
Format-agnostic serialization (serde-style Serializer/Deserializer visitor
split) is now BACKLOG §5 as the eventual target design; in the meantime, iface
hand-writes per-type codecs.

**Origin:** the iface PR design (`docs/iface-precompiled-modules-v1-draft.md`)
needed instance generation for ~17 ADTs / ~65 variants across
`stdlib/compiler/{types,ast,source}.sprout`. Hand-writing that surface is
permanent maintenance debt that compounds with every AST churn. The project's
"robust language and tooling" goal made `deriving` the right answer rather than
a one-off codegen script — but the *content* of derivation (Eq/Ord/ToString vs.
S/D) was narrowed after design review.

## Implementation status (post-scope-revision)

| Component | Status | Notes |
|---|---|---|
| Parser support (`deriving (...)` clause) | shipped | hard keyword, clause between `(..)` and `=` |
| Codegen skeleton (`stdlib/compiler/deriving.sprout`) | shipped | constructor enumeration, AST helpers, `expand_deriving_decls` |
| Bundler integration | shipped | `validate_deriving_decls` runs first (eager errors), then `expand_deriving_decls`, before typecheck |
| `Eq` emitter | shipped, full scope | nullary + field-bearing + parametric |
| `ToString` emitter | shipped, full scope | nullary + field-bearing + parametric |
| `Ord` emitter | shipped, **full scope** | nullary + field-bearing + parametric; lexicographic chained compare |
| `Serialize` emitter | **reverted** | conflated polymorphism with format choice; see Scope revision |
| `Deserialize` emitter | **reverted** | same root cause; perf bug + dispatcher edge case were symptoms |
| Eager errors at deriving site (F1) | shipped | unknown class produces error at bundle time, not use site |
| Spec section in `docs/spec-v0.md` §8.6 | shipped | normative, 3 classes |

## Problem

Sprout has no mechanism to generate typeclass instance bodies from ADT structure.
Every instance is hand-written, scaling linearly with (ADT count × class count).
This is acceptable for a handful of ADTs and 1-2 classes; it becomes load-bearing
maintenance as the language matures.

Concrete pressure points today:

- The iface PR needs `Serialize` and `Deserialize` for ~17 ADTs across
  `stdlib/compiler/{types,ast,source}.sprout` (~65 constructor variants).
  Hand-writing all of it is ~130 match arms of mechanical code, plus a similar
  surface for the parser side.
- `stdlib/compiler/` already contains hand-written `_eq`, `_compare`, and
  `to_string` helpers that exist because writing `instance Eq Foo` for every ADT
  is prohibitive. Those helpers fragment the type-class story and rot in
  lockstep with ADT changes.
- Future AST churn (effects redesign, new expression forms, GADTs) silently
  enlarges the boilerplate-to-keep-in-sync surface. No tool warns when a new
  constructor variant is added without a corresponding serializer arm.

## Goals (v1)

1. User opts in to automatic instance generation per `(class, type)` pair with
   **explicit, visible syntax** — no implicit derivation.
2. Generated instances are human-readable, debuggable Sprout source.
3. Mechanism scoped narrowly enough to fit the stage-0 self-hosted compiler.
4. Covers the iface PR's needs (`Serialize`, `Deserialize` on parametric,
   recursive, mutually-referential ADTs).
5. Future-extensible to user-defined deriving without breaking v1 source syntax.

## Non-goals (v1)

1. Lawful derivation — no proofs that derived instances satisfy class laws.
2. Per-variant overrides — deriving is whole-ADT or nothing.
3. Higher-kinded class derivation (`Functor`, `Applicative`, `Monad`) — defer.
4. Orphan-style deriving — adding instances for a type you don't own. Defer
   until a package ecosystem exists.
5. `deriving (Hash)` — deferred to `BACKLOG.md` §5 (polymorphic-keyed dicts);
   `Hash` typeclass itself is undesigned and has no in-language consumer today.
6. Compile-time user-defined handlers (the v2 trajectory) — v1 is closed-set,
   v2 will open it. See "v2 trajectory" below.

## High-level implementation overview

**Mechanism: direct per-class codegen, closed set.** For each derivable class,
the compiler hosts a corresponding emitter routine that takes an `ast.TypeDecl`
and produces an `ast.InstanceDecl`. The compiler invokes the appropriate
emitter(s) once per type carrying a `deriving (...)` clause, and splices the
resulting instances into the program before typechecking.

Internally, the emitters live in `stdlib/compiler/deriving.sprout` (new file).
Each emitter has the same Sprout-level signature:

```sprout
fn emit_eq_instance(td: ast.TypeDecl) -> ast.InstanceDecl
fn emit_ord_instance(td: ast.TypeDecl) -> ast.InstanceDecl
fn emit_to_string_instance(td: ast.TypeDecl) -> ast.InstanceDecl
fn emit_serialize_instance(td: ast.TypeDecl) -> ast.InstanceDecl
fn emit_deserialize_instance(td: ast.TypeDecl) -> ast.InstanceDecl
```

This shape is the **reference implementation that v2 will expose to user code**
(via class declarations carrying a `derive_impl` clause) — same function
signature, just moved from compiler-internal to class-declaration-level. The
codegen tool is the durable infrastructure investment; the closed-set v1
restriction is a per-version policy, not a fundamental shape.

### v2 trajectory

v1 ships closed: only the 5 classes named below can be derived. v2 (separate
design effort, not in this PR) opens the mechanism: class declarations may
carry a `derive_impl` clause naming a function with the same `ast.TypeDecl
-> ast.InstanceDecl` shape. User-defined classes become derivable; the
compiler-internal emitters either remain as built-ins or are migrated to
prelude. v1's `deriving (X)` syntax does not change; only the set of valid X
expands.

## v1 derivable classes

| Class | Source | Method |
|---|---|---|
| `Eq` | existing at `stdlib/prelude.sprout:292` | `eq :: a -> a -> Bool` |
| `Ord` | existing at `stdlib/prelude.sprout:300` (superclass `Eq a`) | `compare :: a -> a -> Int` |
| `ToString` | existing at `stdlib/prelude.sprout:296` | `to_string :: a -> String` |
| `Serialize` | **defined in this PR** | `serialize :: a -> String` |
| `Deserialize` | **defined in this PR** | `deserialize :: String -> Maybe a` |

`Serialize`/`Deserialize` are added to `stdlib/prelude.sprout` as part of this
PR. Their method signatures are designed so the pair round-trips losslessly:
for any derivable type `a` and any value `x: a`,
`deserialize(serialize(x)) == Just(x)`.

`Hash` is **explicitly excluded** from v1; see Non-goals §5 and
`BACKLOG.md` §5.

## Syntax

A `deriving` clause appears on a `type` declaration, between the optional `(..)`
constructor-export marker and the `=` sign:

```sprout
export type Color (..) deriving (Eq, Ord, ToString) =
  | Red
  | Green
  | Blue

export type Tree a (..) deriving (Eq, ToString, Serialize, Deserialize) =
  | Leaf
  | Node (Tree a) a (Tree a)
```

Grammar: `deriving` `(` ClassName ( `,` ClassName )* `)`. The class-name list
must be non-empty and parenthesized. Whitespace and line breaks inside the
parentheses are allowed.

`deriving` is a **hard keyword** — reserved everywhere in source, not a valid
identifier in any position. Verified no existing source uses it as an
identifier (one comment-only occurrence at `stdlib/compiler/codegen.sprout:3339`,
not affected).

There is **no separated retrofit form** in v1 (e.g., no top-level
`deriving (Eq) for SomeType` declaration). All derivation is co-located with
the type declaration. Orphan-style deriving is deferred until a package
ecosystem exists.

## Semantics

### Generated instance shape

For each `(class, type)` pair in the deriving clause, the compiler synthesizes:

- One `ast.InstanceDecl` declaring the instance.
- One implementation per class method, generated by recursive structural walk of
  the type's constructors.
- A `where` clause carrying any per-type-parameter constraints needed to make
  the body type-check.

### Parametric handling — auto-inferred constraints

For each type parameter `a` in the type being derived, the compiler walks every
constructor's field types looking for a use of `a`. If `a` appears in any field
type, the generated instance carries `where C a` (where C is the class being
derived). If `a` appears in *no* field, no constraint is added for it.

```sprout
type Foo a (..) deriving (Eq) = | Bar Int | Baz a

# Generates (conceptually):
instance Eq (Foo a) where Eq a {
  fn eq(left, right) = ...
}

type Phantom a (..) deriving (Eq) = | P Int

# Generates: no constraint on `a`; it is phantom in the body.
instance Eq (Phantom a) {
  fn eq(left, right) = ...
}
```

### Self-reference

When the type being derived appears in its own constructor fields (directly or
transitively), the generated instance does **not** carry a self-constraint
(would be circular). The recursive call in the generated body resolves via
normal instance lookup at use time:

```sprout
type Tree a (..) deriving (Eq) = | Leaf | Node (Tree a) a (Tree a)

# Generates:
instance Eq (Tree a) where Eq a {
  fn eq(left, right) = match (left, right) with
    | (Leaf, Leaf) -> true
    | (Node l1 v1 r1, Node l2 v2 r2) ->
        eq(l1, l2) && eq(v1, v2) && eq(r1, r2)
    | _ -> false
}
```

The constraint `where Eq a` (not `where Eq a, Eq (Tree a)`) is correct because
once the instance resolver has `Eq a`, the synthesized `instance Eq (Tree a)`
itself provides `Eq (Tree a)`; the recursive call discharges via the instance
being defined.

### Cross-type chaining

If `Outer` derives `Serialize` and `Outer` contains a field of type `Inner`,
`Inner` must also have a `Serialize` instance (derived or hand-written).
Resolution falls out of normal instance lookup — no special "deriving dependency
graph" is needed because synthesized instances are real `ast.InstanceDecl`
nodes participating in normal class resolution.

### Compilation order

Derived instances enter the instance environment in a single batch, before
typechecking begins. The compilation pipeline runs:

1. **Parse phase** — all source parsed; ASTs (including TypeDecl `deriving (...)`
   clauses) built.
2. **Synthesis phase** — for every TypeDecl with a non-empty deriving list, the
   compiler invokes the appropriate emitters and produces `ast.InstanceDecl`
   nodes. All synthesized instances are appended to the program's declaration
   list together.
3. **Field-class validation phase** — for each synthesized instance, the
   compiler walks the constructor field types and confirms a class instance
   exists (synthesized or hand-written) for every required `(class, field_type)`
   pair. Missing instances emit the F1 error.
4. **Typecheck phase** — normal typechecking proceeds with the augmented
   instance environment.

This two-phase model (synthesize first, then validate) is what allows
mutually recursive `deriving` on `type A = ... B ...` and `type B = ... A ...`
to both find each other: both instances are present in the environment by the
time field-class validation runs on either. A single-pass model (validate as
you synthesize) would falsely reject mutually-recursive derivations because
the second instance is not yet emitted when the first is checked.

### Per-class derivation rules

#### `Eq`
- Two values equal iff same constructor and all corresponding fields equal.
- Fields compared field-by-field with `eq`.
- Requires `Eq fty` for every field type `fty` appearing in any constructor.

#### `Ord`
- Constructors ordered by source declaration order (first declared is least).
- Within the same constructor, fields compared lexicographically left-to-right
  with `compare`.
- Requires `Ord fty` for every field type; implies `Eq fty` via Ord's superclass.

#### `ToString`
- Multi-field: `"CtorName(field1_str, field2_str, ...)"`.
- Nullary: `"CtorName"`.
- Requires `ToString fty` for every field type.

#### `Serialize`
- S-expression form: `"(CtorName field1_ser field2_ser ...)"`.
- Strings escaped with C-style backslash convention (`"`, `\`, newline).
- Nullary: `"(CtorName)"`.
- Requires `Serialize fty` for every field type.

#### `Deserialize`
- Parses S-expressions emitted by `Serialize`.
- Returns `Just x` on successful parse, `Nothing` on malformed input.
- Round-trip property: `deserialize(serialize(x)) == Just(x)` for every value of
  every derivable type.
- Requires `Deserialize fty` for every field type.

### Codegen ordering

Multiple derivable classes on the same type are emitted in source-listed order.
Synthesis order across types does not matter — instance lookup is global, and
field-class requirements are validated during synthesis, not at use.

## Error messages

All derivation errors fire at the `deriving` site (the F1 eager strategy). The
error identifies the offending field, its containing constructor, and the
action the user can take.

**Missing field-class instance:**

```
ERROR at stdlib/compiler/types.sprout:46 — cannot derive `Serialize` for `Scheme`:
  field of type `Type` (in constructor `Scheme`, position 3) has no `Serialize` instance.
  Add `deriving (Serialize)` to the declaration of `Type`, or define one manually.
```

**Unknown class in deriving clause:**

```
ERROR at <path>:<line> — cannot derive `Foo`: no derivation rule for class `Foo`
  in this compiler version.
  v1 derivable classes: Eq, Ord, ToString, Serialize, Deserialize.
```

**Missing superclass requirement** (e.g., `deriving (Ord)` without `Eq`):

```
ERROR — cannot derive `Ord` for `Foo`: requires `Eq Foo`, which is not declared
  or derived. Add `Eq` to the deriving clause.
```

**Phantom field type** (typo for an undeclared type, e.g., `Bzytes` instead of
`Bytes`): currently produces "no `Serialize` instance for `Bzytes`," which is
technically correct but may mask the underlying typo. The companion BACKLOG
item "strict type-name validation in TypeDecl field/constraint positions"
(`BACKLOG.md` §1, P1) addresses this independently; landing it improves
deriving's diagnostics without requiring a deriving change.

**Overlapping instance** — if a user hand-writes `instance Eq Foo` *and* declares
`type Foo (..) deriving (Eq) = ...`, the deriving codegen fails:

```
ERROR — cannot derive `Eq` for `Foo`: an instance `Eq Foo` is already defined at
  <path>:<line>. Remove either the hand-written instance or `Eq` from the
  deriving clause.
```

## Compatibility / migration

`deriving` is purely additive — no existing source uses the keyword as an
identifier. Existing manually-written typeclass instances continue to work
unchanged.

After `deriving` lands, the iface PR adds `deriving (Serialize, Deserialize)`
(and optionally `Eq`, `ToString` for debug/test purposes) to ~17 ADTs across
`stdlib/compiler/types.sprout`, `ast.sprout`, and `source.sprout`. The
migration is ~17 single-line edits plus a bootstrap-seed refresh.

Hand-written instances that would become redundant (manual `_eq` helpers in
stdlib/compiler) can be migrated in a follow-up PR; deriving v1 does not block
their continued existence.

## Tests

`tests/stdlib/test_deriving.spr` (new file) with one fixture per (class,
ADT-shape) combination. For each of the 5 classes, cover:

- Nullary constructor (`type T = | A`)
- Single-field constructor (`type T = | A Int`)
- Multi-field constructor (`type T = | A Int Bool String`)
- Multiple constructors (`type T = | A | B Int | C String`)
- Parametric (`type T a = | A a`)
- Recursive (`type T = | Leaf | Node T T`)
- Mutually recursive (`type A = | MkA B` and `type B = | MkB A`)
- Cross-type chain (`type Outer = | Wrap Inner`, both derived)

Plus integration tests:

- All 5 classes on a complex real ADT exercising every shape.
- Round-trip property: `deserialize(serialize(x)) == Just(x)` for ~10
  hand-built values per shape.
- Error path: derived class missing a field-class instance produces the F1
  error with expected wording.

Parser tests at `tests/stdlib/compiler/test_parser.spr`:

- `deriving (X)` accepted between `(..)` and `=`.
- `deriving ()` rejected (empty class list).
- Missing parens (`deriving X`) rejected.
- `deriving` as identifier (e.g., `let deriving = 1`) rejected.

## Spec impact

`docs/spec-v0.md` gains a new **Deriving** section: syntax, semantics of the
derivable classes, error conditions, and a version-scoped list of derivable
classes ("In this version of the language, the derivable classes are: ...").
The version-scoping language is the spec's first explicit "language version"
marker; see open question §1 for how to organize it.

`docs/style-guide-v0.md` gains a note: prefer `deriving (...)` over
hand-written instances when the derivation rule matches the intended semantics;
reserve hand-written instances for cases where the derivation rule is
inappropriate (rare in practice for the v1 classes).

## Phased implementation plan

One feature PR on `feat/deriving`, internally commit-phased rather than split
into separate PRs (the surface is cohesive and a half-implemented deriving has
no useful intermediate state for bisecting).

| # | Phase | Effort |
|---|---|---|
| 1 | Parser: `deriving` keyword + clause grammar; extend `ast.Decl::TypeDecl` to carry `List String` of class names | ~2-3 days |
| 2 | Class definitions: add `Serialize` and `Deserialize` to `stdlib/prelude.sprout` *and* hand-write instances for `Int`, `Bool`, `String`, `Char`, `List a`, `Maybe a`, `Vec a`, `Dict v` (each is a serializer + a recursive-descent parser); settle escape rules here (not deferred to phase 7); bootstrap-seed refresh | ~2-3 days |
| 3 | Codegen skeleton: create `stdlib/compiler/deriving.sprout` with constructor enumeration, field-type extraction, parameter-usage analysis; empty emitters returning placeholder InstanceDecl | ~2 days |
| 4 | `ToString` emitter — simplest, establishes patterns; all 8 ADT-shape fixtures | ~2 days |
| 5 | `Eq` emitter; tests | ~2 days |
| 6 | `Ord` emitter; tests | ~2 days |
| 7 | `Serialize` emitter (string escaping, recursive composition); tests | ~3 days |
| 8 | `Deserialize` emitter (parser is the sharpest part); full round-trip tests with `Serialize` | ~4-5 days |
| 9 | Compiler integration: implement the two-pass model from §Semantics→Compilation order — synthesis phase (invoke emitters for every deriving clause, splice all instances at once) followed by field-class validation phase (F1 errors on misses). Mutually-recursive derivations must work | ~3-4 days |
| 10 | Spec + style-guide updates; AGENTS.md if needed | ~1-2 days |
| 11 | DoD verification: refresh bootstrap seed, run examples, full test suite, smoke shapes, bundle smoke, GC safety lint | ~1 day |

**Total: ~22-28 days (~4-5 weeks)** of focused work.

## Open questions

1. **Spec language-version organization.** Deriving introduces the first
   "v1 of the language supports X" sentence into `docs/spec-v0.md`. Two options:
   (a) add a "Versioned features" appendix listing derivable classes (and
   future version-scoped features as they accrue); (b) inline "as of this
   version" notes on each version-scoped feature. (a) centralizes for ease of
   audit; (b) keeps context next to definitions. Likely (a).
2. **Bootstrap timing for parser phase.** Phase 1 extends `ast.Decl::TypeDecl`'s
   shape, requiring the 2-step bootstrap protocol (`docs/debugging.md`) for the
   seed refresh. Worth a pre-validation that the protocol works smoothly on
   this branch before phase 1 starts.
3. **`Serialize` escape rules.** Strings in serialized output need escape rules
   for `"`, `\`, and newlines. C-style backslash escaping (`\"`, `\\`, `\n`) is
   more human-readable; length-prefixed (`5:hello`) is easier to parse. Likely
   C-style; **must be settled during phase 2** (not phase 7 as originally
   sketched) because the hand-written `String` Serialize/Deserialize instances
   in phase 2 are the first users of the convention — phases 4-8 inherit the
   choice.
4. **Records.** v1 syntax targets `type` declarations (sums-of-products).
   `record` declarations are a separate decl form and are out of scope for v1.
   Verified `stdlib/compiler/*.sprout` (the iface PR's target modules)
   contain zero `record` declarations, so the v1 restriction does not block
   the iface PR. Adding deriving to records is a follow-up if a downstream
   consumer needs it; noted in the spec section to avoid the assumption that
   it works.
5. **`Serialize`/`Deserialize` instances for primitive and stdlib types.** The
   PR must ship hand-written instances for `Int`, `Bool`, `String`, `Char`,
   `List a where Serialize a`, `Maybe a where Serialize a`, `Vec a where
   Serialize a`, and `Dict v where Serialize v`. These are the building blocks
   every derived instance composes through. Settle exact list during phase 2.

## References

- `docs/iface-precompiled-modules-v1-draft.md` — consuming PR; original
  motivation for this design.
- `BACKLOG.md` §5, P1 — polymorphic-keyed dicts; motivates `Hash` deferral.
- `BACKLOG.md` §1, P1 — strict type-name validation in TypeDecl field positions;
  improves deriving's phantom-type diagnostics independently.
- `docs/spec-v0.md` — gains a new **Deriving** section as part of this PR.
- `docs/style-guide-v0.md` — gains a note on preferring derived instances.
- `docs/debugging.md` §2-Step Bootstrap Protocol — required for the parser
  change in phase 1.
- Prior design discussion: see commit log on `feat/deriving` for the decision
  record (mechanism A with v1 closed / v2 open trajectory, 5-class scope,
  syntax shape, parametric auto-inference, F1 errors).
