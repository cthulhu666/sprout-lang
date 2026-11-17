### 5.6 ADT declaration

```sprout
type Maybe a =
  | Nothing
  | Just a
```

A constructor carries at most **255 fields**; exceeding it is a compile error
naming the constructor. Records share the ceiling (§5.6.3). The limit is the
width of the runtime object header's field-count byte, not a design rule; it is
far above any practical product type.

Every type name referenced in a constructor field, record field, or type alias
RHS must be either:

- A locally-declared type (in the same module bundle).
- A built-in primitive type (`Int`, `Double`, `Bool`, `String`, `Char`, `Unit`,
  `Bytes`).
  > `IntRange` was in this list until 2026-08-19. It is now an ordinary type
  > declared in the prelude (§8.3), so it satisfies the *first* bullet instead —
  > and since the prelude is unconditional (§3.1), every file has it locally
  > declared.
  >
  > Until 2026-08-20 this note described a special case for a file that received
  > no prelude: the bundler admitted the prelude's exported *type names* so a
  > signature mentioning `Maybe` would not be rejected, while `a..b` still lowered
  > to a `call i64 @range_up` with no `define` and no `declare` — invalid IR,
  > rejected by the IR parser as `error: use of undefined value '@range_up'`. The
  > admit-the-names workaround is gone with the condition that motivated it. The
  > dangling-call shape survives only behind the explicit `no_prelude` opt-out
  > (§3.1); it is NOT the extern case described above, since an extern always gets
  > a `declare` emitted and so really does survive to link time.
- A lowercase type variable (e.g. `a`, `b`) bound by the enclosing type
  declaration's parameter list.

Referencing an undeclared uppercase type name is a compile error at definition
time:

```
type-validation: unknown type name `Baz` in declaration `Foo`
```

Forward references between mutually recursive ADTs within the same module are
allowed — validation runs after all type names in the module have been
registered.

**Positions validated in this version:** `TypeDecl` constructor fields,
`RecordDecl` field types, `AliasDecl` RHS, `WrapDecl` inner type.  `ClassDecl`
method signatures, `InstanceDecl` constraint types, and `FnDecl` param/return
type annotations are not yet validated (tracked in BACKLOG.md).

**A one-constructor type may not name its constructor after another type in
scope.** Without `alias`, `type Pairs = List (String, Int)` declares a new type
with one constructor named `List` taking a tuple. It reads as an alias (§5.6.2)
and is not one. So it is a compile error when a type has **exactly one**
constructor, that constructor's name differs from the type's own, and the name
resolves to a type in that module:

- written bare: a primitive type, a runtime type (`Vector`, `Map`,
  `Ref`, `Builder`), a prelude type, a type of the same module, or one imported
  by name — unless a class the module declares or imports has that name, which
  hides the primitive, runtime or prelude type;
- written qualified: a type an import alias exports (`json.Json` after
  `import stdlib.json as json`).

```
type `Pairs` has one constructor, `List`, which is also the name of a type in scope: this declares a new sum type, not an alias. Write `type alias Pairs = …` for an alias or `wrap Pairs = …` for a distinct type; to keep the sum type, rename the constructor
```

The suggested fix keeps the type's parameters: `type Box a = List a` is told
`type alias Box a = …`. A leading `|` does not exempt it. A constructor named
after its own type (`type Box = | Box Int`), a type with several constructors
(`| JsonArray JsonArray` in `stdlib/json.sprout`), and a bare name that is a type
only in another module (`JsonArray` after `import stdlib.json as json`) are all
unaffected.
A constructor named after a record or a `wrap` (`| Circle Circle` beside
`type Circle = (label: String)`) is a second declaration of that name and is
rejected (§5.1).

#### Existential constructors

A constructor may bind an **existential** type variable with a constructor-level
`exists` prefix. The variable scopes over the constructor's fields but is
**absent from the type head**, so constructing a value *hides* the field's type:

```sprout
type Boxed = | exists a. Boxed a

# Each element's type (Int / String / Bool) is hidden behind `Boxed`, so this
# heterogeneous list typechecks as `List Boxed`:
let row = [Boxed(1), Boxed("hi"), Boxed(true)]
```

**Construction (pack).** `Boxed(v)` accepts a value of any type; the bound
variable is instantiated to that type and then hidden — it does not appear in the
result type `Boxed`.

**Match (unpack).** Matching `Boxed x` binds `x` to a fresh **abstract** type,
distinct from every concrete type and from every other unpacked existential.
`x` may be moved, ignored, or re-packed, but it **must not be used at a concrete
type or unified with a different existential** — doing so is a compile error:

```
existential type escapes its scope: a hidden `exists`-bound type cannot be used
at a concrete or a different hidden type
```

So `match b with | Boxed x -> x` returned at a concrete type, `x + 1`, and
merging two separately-unpacked existentials are all rejected; re-packing
(`Boxed x -> Boxed(x)`) and ignoring (`Boxed _ -> ...`) are allowed.

The hidden type must also not **outlive the unpack**. It may not appear in the
match arm's result type, in the type of any binding made outside the arm, or in
the type of the enclosing declaration, whether that type was written or
inferred. The arm is checked as it ends, and the error is reported at the
constructor pattern:

```
existential type escapes its scope: `f` has a type hidden by `Shape`, and it would
outlive this match arm (through the arm's result or a binding from outside the arm).
Use it inside the arm, or re-pack it.
```

The error names the binding that escaped. When the hidden type escapes inside a
named type that wraps it (a record `Holder a` with a field `a -> Int`), it
names every binding of the pattern that shares the hidden type, joined by `or`.

The check runs again once the declaration's deferred field reads are settled. A
field read on a value whose type is not yet known (`o.cell`, where `o` is a lambda
parameter) is typed at the end of the declaration, and that can bind an outer type
to the hidden one after the arm has ended.

Omitting the annotation is not an exception, and neither is routing the value
through something that is not itself a declaration —

```
fn unbox(b: Boxed) = match b with | Boxed x -> x       # rejected: x is the arm's result
let leaked = match Boxed("hi") with | Boxed x -> x     # rejected: x is the arm's result
[(x, f) for Shape x f in shapes]                       # rejected: the head is the arm's result
```

and a type variable created outside the arm counts as outside: a `Ref` made
before the arm, a fold accumulator, and a lambda parameter all have one type for
every iteration, so storing a hidden-typed value in one is rejected. Because a
local `let` is monomorphic, so is a lambda bound by `let` outside the arm and
applied to the unpacked value:

```sprout
type Shown = | exists a. Shown a where ToString a

fn two(x: a, n: Int) -> String where ToString a = to_string(x) ++ int_to_string(n)

# rejected: `g`'s parameter type is fixed to the hidden type
fn go_outside(s: Shown) -> String =
  let g = \z -> two(z, 9)
  in match s with | Shown v -> g(v)

# accepted: `g` is bound inside the arm
fn go_inside(s: Shown) -> String =
  match s with | Shown v -> let g = \z -> two(z, 9) in g(v)
```

The hidden type is one static type, but a match arm runs once per value, and each
run unpacks a different hidden type. If it could reach a type that outlives the
arm, two unpacks would meet at that one static type: a function from one
element's hidden type would be applied to another element's value. This is the
per-unpack freshness the rule above depends on.

**Runtime.** Zero cost — the same tagged representation as an ordinary
constructor; the hidden type is erased.

**Constrained existentials via `any C`.** A constructor field written `(any C)`
hides a value of any type that has an instance of the class `C`, and makes that
instance available on the unpacked value:

```sprout
type Shown = | Shown (any ToString)     # ≡ hides a value with a ToString instance

let cells = [Shown(42), Shown("hi"), Shown(true)]   # : List Shown

fn render(s: Shown) -> String =
  match s with
  | Shown x -> to_string(x)             # dispatches on the packed witness
```

Construction resolves the class instance at the value's concrete type and **packs
its dictionary** into the constructor; constructing with a type that has no
instance is rejected at the construction site with the usual "No instance of `C`
for `T`" error (§8). Matching binds the value at the abstract existential type
(as above), but the packed dictionary is in scope, so a call to a `C` method on it
dispatches through the packed witness — each element renders as its own concrete
type would. A **multi-method** class packs one witness per method, and a class
**with superclasses** packs its transitive-superclass methods too, so an inherited
(superclass) method also dispatches on the unpacked value. A method of class `C` on the
unpacked value, whether called, passed as a value, or required by an instance context
(`to_string([x])` needs `ToString` for `x`), is accepted only when the constructor packs
`C` or a class with `C` as a superclass; otherwise it is rejected with "No instance of `C`
for the unpacked existential value".

**Explicit `exists … where` prefix.** The same feature is also spelled with an
explicit constructor-level `exists` prefix and a trailing **`where`** constraint
clause; `(any C)` is single-field sugar for it:

```sprout
type Shown  = | exists a. Shown a where ToString a     # ≡ Shown (any ToString)
type Q      = | exists a. Q a where ToString a, Eq a   # several constraints on one var
type Bag    = | exists a. Bag (List a) where Describe a # hidden var nested in a field
```

The prefix scopes over **all** the constructor's fields, so it additionally
expresses a hidden variable spanning several fields (shared hidden state) and a
constrained variable that appears only nested inside a compound field. The `where`
keyword matches every other constraint site in the language; `=>` is not used. A
`where` clause is valid only on an existential: each constraint applies its class to
one variable bound by the `exists` prefix (a constraint applies its class to exactly
one type, §8.5), or is `linear` on one (§5.8) — otherwise it is a parse error (it must
not constrain a head parameter).

The packed dictionaries belong to the constructor pattern, so a field may be matched
by any pattern: a variable, `_`, a tuple or a nested constructor
(`| Boxed((x, n)) -> take(x) + n`). Each constrained variable keeps its own
dictionary, including two variables of one constructor. The constructor pattern may
itself be nested, in a tuple or another constructor, in any form that matches
(`match`, `let`, `<-`, a comprehension generator): each unpack binds its own
dictionaries (`| (Shown x, Shown y) -> …`).

**Runtime.** The packed dictionary is the class's method function-pointer(s)
stored as hidden constructor fields (traced by the GC); a method call on the
unpacked value forwards them. No new runtime mechanism — the existing
dictionary-passing path is redirected into the heap value.

**Known gaps.** An *ambiguous* existential construction whose type is undetermined
(e.g. `Bag([])` — an empty container) is reported as an opaque constructor-arity
mismatch rather than a located "ambiguous" error. Full GADTs (index refinement)
are out of scope. Design and staging: `docs/gadts-v0.md`.

