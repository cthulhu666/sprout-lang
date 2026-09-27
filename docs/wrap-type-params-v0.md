# Parameterized `wrap` — a guide

`wrap Name p1 p2 … = T` binds type parameters on a zero-cost distinct type. The
normative rules are spec §5.6.1; this document is how to choose between the shapes
and what bites. Every pattern below is a runnable file in `examples/`, kept honest
by `just compile-examples-stage1`.

## The two kinds of parameter

One question decides which you have: **does the right-hand side mention it?**

| | example | runtime | what it buys |
|---|---|---|---|
| **stored** | `wrap Sorted a = Vec a` | `Sorted Int` *is* a `Vec Int` | the wrap is generic over a payload |
| **phantom** | `wrap Angle u = Double` | `Angle Deg` *is* a `Double` | the parameter separates types and nothing else |

Both are erased. A phantom parameter is not "a field that happens to be unused" —
there is no field. `fn degrees(d: Double) -> Angle Deg = Angle(d)` compiles to
`ret i64 %p$d`, and no `@sprout_register_ctor` is emitted for a wrap constructor.

## Writing the index tags

An index needs a named type per case, and the house form for a marker type is the
one the prelude uses for `DivByZero` — the constructor shares the type's name:

```sprout
type Deg = | Deg
type Rad = | Rad
```

Not `DegTag`: the suffix is invented, and nothing in the repo uses it. Three
alternatives do **not** work, in increasing order of danger. `type Deg` with no
body is a parse error. A constructor cannot be used in type position, so one
`type AngleUnit = | Deg | Rad` cannot supply two indices (`unknown type Deg`);
Sprout has no kind promotion. And `type alias Deg = Unit` **compiles while
silently destroying the distinction** — aliases are transparent, so `Angle Deg`
and `Angle Rad` become the same type and mixing them is accepted.

The tag values are free, since nothing ever constructs one, but the declaration is
not quite nothing: each tag emits one `@sprout_register_ctor` call at startup and
its name in rodata. Negligible, and worth knowing before indexing by fifty tags.

## The four patterns

**An invariant the type carries** — `examples/wrap_sorted_vec.sprout`.
`wrap Sorted a = Vec a`, built by `sort_of` and by `merge`, which preserves the
order it is given. `smallest` and `largest` become index reads instead of scans,
and a binary search is correct by construction — outside the module, on rule 2's
condition. The stored parameter is what lets one `Sorted` serve every element type.

**Units** — `examples/wrap_phantom_units.sprout`. `wrap Angle u = Double` with `Deg`
and `Rad` tags, plus `Length` over `Km`/`Au`. The alternative is to put the unit in
the parameter *name* (`fovy_deg`, `radius_km`), which works for exactly as long as
every caller reads the name.

**Typed handles** — `examples/wrap_typed_handles.sprout`. `wrap Handle k = Int` over
a foreign system's slot indices. One `slot_of` serves every kind, and a downstream
module can index `Handle` with a tag type this module never heard of — *naming*
`Handle Mesh` needs only the exported type, while *building* one at a new tag needs
`(..)` or a kind-generic constructor the library provides.

**Protocol state** — `examples/wrap_session_states.sprout`. `wrap Session s = Int`
with `LoggedOut`/`LoggedIn`; each transition is a function whose signature names the
states it joins, so `read_secret` is unreachable to a caller outside the module
without logging in. Outside is the operative word — rule 2 below is what makes it
true, and inside the declaring module `Session(0)` is a `Session LoggedIn`.

## Parameterized wrap, or a family of monomorphic ones?

This is the decision people get wrong, and the answer is not "parameterize". A
family of `wrap Texture = Int` / `wrap Shader = Int` declarations is **stronger** at
the construction site: `Texture(7)` can only ever be a texture, whereas `Handle(7)`
is polymorphic in its kind and fits any of them. Collapsing such a family into one
parameterized wrap with an exported constructor *weakens* the guarantee it existed
for.

Parameterize when at least one of these holds:

- **The payload varies.** A stored parameter is the only way to be generic over it.
- **Kind-generic operations exist.** `slot_of : Handle k -> Int` is written once;
  seven monomorphic wraps need seven unwrappers.
- **The kind set is open.** Callers can add tag types; they cannot add alternatives
  to a family you enumerated.
- **The index changes on one value.** A state machine needs `Session LoggedOut →
  Session LoggedIn`; two unrelated wraps cannot share an operation.

Otherwise keep the separate wraps. `stdlib/compiler/source.sprout` (six `wrap X =
String`) and `stdlib/tui/widgets/container.sprout` (`Cols`/`Rows` over one
`List layout.Dimension`) are both closed sets with no kind-generic operations, and
are right as they stand.

## Four rules that bite

All four use the units example's vocabulary: `wrap Angle u = Double` with tags `Deg`
and `Rad`, `to_radians : Angle Deg -> Angle Rad`, and `sine : Angle Rad -> Double`.

**1. The bare constructor pins nothing.** `Angle` has type `forall u. Double ->
Angle u`, so it satisfies *any* index — `sine(Angle(0.0))` compiles, though nothing
says those are radians. The guarantee comes from smart constructors being the only
way in, not from the parameter existing.

**2. Omit `(..)`, or the index is advisory.** Without it the constructor stays
module-private and your smart constructors are a real chokepoint. With it, any
caller writes `Angle(0.0)` at whatever index inference wants.

**3. A bound value is monomorphic.** `let a = Angle(0.0)`, then using `a` at two
indices — `sine(a)` and `to_radians(a)` — is rejected with `Call type mismatch: Type
mismatch: main.Deg vs main.Rad`. The first use fixes the index, which is what makes
the pattern hold together once values flow through named bindings.

**4. Every use supplies every parameter.** Dropping the `u` from the unwrapper,
`fn angle_value(a: Angle) -> Double`, fails with `Constructor main.Angle: Type
mismatch: main.Angle vs main.Angle $t2924`. The bare tyvar is inherited from the ADT
path; its number counts allocations across the whole program, so unrelated code
shifts it.

## Limits

- **`deriving` constrains phantom parameters.** `wrap Angle u = Double deriving (Eq)`
  yields `Eq u => Eq (Angle u)`, so comparing two `Angle Deg` fails with `No instance
  of Eq for main.Deg` — the common case, since index tags exist only to be named.
  Write an explicit `instance` until this is fixed (`BACKLOG.md`).
- **An uppercase parameter name is accepted and shadows the type it names.**
  `wrap Metres Int = Int` means `wrap Metres a = a`, and compiles. Use lowercase
  parameter names (`BACKLOG.md`).
- **No type-level naturals.** An index is a type, so a phantom can track *which*
  unit or state, not *how many* elements or parameters.
- **A phantom index is not linearity.** It constrains *which operations* accept a
  value; spec §5.8's `linear` constrains *how many times* it may be used. Use-after-
  close needs linearity — a `type linear Conn` with a `consuming` close, which is how
  `sprout-postgres` does it, and it needs no index. "Don't call this before that"
  needs the index. A resource wanting both guarantees states both.
