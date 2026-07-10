# Module-qualified type identity — design (2026-07-10)

**Status:** IMPLEMENTED 2026-07-10 (branch `feat/module-qualified-type-identity`),
**including G6.** T7 correctness landed first; then G6 made identity opaque: `wrap
TypeId = String` with a `type_id_*` API (`_eq`/`_display`/`_is_concrete`/`_symbol`), and
`TConst`'s payload flipped from `String` to `TypeId` (~137 sites across 11 files) so the
type checker now *forbids* raw string surgery on identity — the containment G6/N2a were
approved for, which keeps a future generative identity (functors/PDTs) a representation
swap. Five regression tests green (shadow, control, concrete dispatch, eta/polymorphic
dispatch, TypeId API); self-compile is a fixed point; full suite + compile-examples +
smoke-shapes + bundle-smoke green.
**Normative status:** changes a core representation invariant of the type checker;
the spec's type-identity rules (§ type equality) are normative as of this change.

## Implementation summary (2026-07-10)

The fix threaded through **six** representations, each of which had independently
stripped the module prefix. The unifying rule: **dict keys and type identity keep the
raw dotted canonical name; only the emitted LLVM `__tc_` symbol is dot-sanitized; and
any concrete-vs-typevar *case* test must read the last name segment, not the
module-prefixed whole.**

1. **Checker identity** — `lookup_type_var` (`infer.sprout`), `build_ctor_map`,
   `concrete_type_str` (`@inst` read), and the unifier (`unifier.sprout:190`) compare
   exact dotted identity (`after_last_dot` deleted on the identity path).
2. **TDict head** — `type_to_typeexpr_with_prog_vars` keeps the dotted head; that
   TypeExpr becomes the injected TDict's constraint, hence the dispatch Evidence key.
3. **Three dispatch keyspaces in lockstep** — infer `@inst`, `resolve.sprout`'s
   Evidence producer, and lowering's `ctx_inst`/`inst_table` all key on the raw dotted
   head (`type_expr_head_name`/`first_concrete_head_str`/`constraint_key_*` no longer
   strip). A key sanitized in one but not another is a silent dispatch miss.
4. **LLVM symbol** — `mangle_type_expr` (`lowering.sprout`) dot-sanitizes *only* the
   emitted `__tc_{Class}_{head}_{method}` symbol (via `qualified_head`), keeping it a
   valid identifier while the keys stay dotted.
5. **Concreteness classification** — `head_is_concrete` (`resolve.sprout`) tests
   `starts_upper(after_last_dot(head))`: a qualified concrete type like `main.Maybe`
   starts with a lowercase module component and would otherwise read as a type
   variable, routing to `EvForward` → a null dict → SIGSEGV.

Regression tests: `tests/stdlib/compiler/test_type_name_collision_shadow.spr`
(ctor-map collision), `test_local_type_no_collision_control.spr` (positive guard),
`test_type_name_collision_instance_dispatch.spr` (runtime dispatch). **Known latent
hardening (not required by any failing case, verified by self-compile + suite):**
`resolve.sprout`'s and `lowering.sprout`'s `is_type_var_name` lack the `str_find(name,
".") == -1` dot-guard that `infer.is_lowercase_name` has — a dotted name is never a
type variable, so add the guard if a future dispatch shape ever surfaces it.
**Supersedes:** the deferred BACKLOG item "Bare-name type identity" and closes
fundamentals-review **W11/T7** (`unifier.sprout:190`), which cannot be fixed at the
unifier — see §1.
**Related:** `docs/iface-precompiled-modules-v1-draft.md`,
`docs/iface-arc-double-inference-handoff-2026-07-04.md` (this change is a prerequisite
for both — see §9).

---

## 1. Problem statement

Sprout keys **type identity by unqualified name**. Every type reference is collapsed
to its short name before it is ever compared:

- `lookup_type_var` (`infer.sprout:121-124`) resolves a source type name to
  `TConst(string.after_last_dot(name))` — the module prefix is discarded at the
  point of resolution.
- `build_ctor_map` (`infer.sprout:358-361`) keys constructor sets by
  `after_last_dot(name)`.
- The unifier compares two type constants by
  `after_last_dot(a) == after_last_dot(b)` (`unifier.sprout:190`).
- The pattern recurs across `infer.sprout`, `lowering.sprout`, and `codegen.sprout`:
  **58 `after_last_dot` calls total, of which ~37 sit on the type/ctor identity path**
  by a keyword bucket — *not yet audited line-by-line into identity vs. display*. The
  identity-vs-display partition is itself part of the migration work (§5 Change C);
  the count is an upper bound on the identity sites, not a verified figure.
- **Instance dispatch is a third subsystem on the identity path** (beyond the unifier
  and `build_ctor_map`): instance markers are keyed `"@inst:{Class}:{head}"` where the
  head is stripped by `concrete_type_str` (`infer.sprout:1112-1114`,
  `TConst name -> after_last_dot(name)`), and registration strips identically via
  `lookup_type_var` — both bare, in lockstep by design (comment at `:1109-1111`).
  Two same-short-named types with separate instances therefore collide here too.

**Consequence — a soundness hole.** Two types that share a short name across modules
become **one type identity**: distinct runtime layouts, indistinguishable to the
checker. Unifying the two is silently accepted, so a value of one layout can be used
where the other is expected → memory corruption. Today the exhaustiveness pass is the
only thing that *incidentally* catches some instances (it saw the ctor-set of the
wrong type).

**Reachability — not a compiler-internal curiosity.** The bundle that carries a
program's decls collapses everything into one namespace, so the collision fires
whenever a single bundle contains two type decls with the same short name:

1. A **normal single-module program** that redefines a prelude/stdlib type name (the
   prelude is bundled in) — the user's `Maybe`/`Result`/`Node`/etc. silently unifies
   with the stdlib type of the same short name.
2. **Tooling/compiler bundles** co-importing sibling modules with a shared short name
   — the confirmed `sproutd` case: `stdlib.compiler.Diagnostic` (report entry) vs
   `stdlib.compiler.compiler.Diagnostic` (`DiagError | DiagWarning`) collapsed in
   `build_ctor_map`, producing a false "Non-exhaustive match on Diagnostic". Patched
   by *renaming* one type (`ReportEntry`) — the instance was resolved, the root cause
   was not.

**Why T7 cannot be fixed at the unifier (the closed dilemma).** The distinguishing
information — the module prefix — is destroyed *upstream* of `unifier.sprout:190`.
Therefore, whatever we do at line 190:

- **If qualified `TConst`s reach it** (they do: `deriving` mints `main.A`; imported
  module schemes and bundled decls carry FQNs), then switching to exact string match
  would *reject* a same-type value that was spelled bare elsewhere via
  `lookup_type_var` → **breaks valid programs and self-compile.**
- **If everything is already bare-collapsed** before it arrives (via the 37 upstream
  `after_last_dot` sites), then exact match is a no-op and the collision persists
  because both sides are already the bare `Foo` → **fixes nothing.**

Either branch, line 190 is the wrong place. The fix must be upstream, where the prefix
still exists. This is precisely why T7 was flagged "risky."

## 2. Root cause

The compiler **conflates two operations that a sound nominal type system keeps
separate**:

1. **Name resolution (scoping).** Given surface syntax (`C`, or `stdlib.foo.C`),
   determine *which declared type it denotes*, using the current module + imports +
   prelude. Inherently scope-sensitive: bare `C` in module `main` denotes `main.C`.
   Resolution is legitimately *lenient* — bare names, imports, and shadowing all map
   to the right declaration.
2. **Type identity (equality).** Given two *already-resolved* types, decide whether
   they are the same. This must compare **globally-unique identity**, never a short
   name. Identity is *strict*.

Today both are collapsed into one move (`after_last_dot`): resolution "resolves" by
throwing the module prefix away, and identity "compares" by throwing it away again.
Resolution destroys exactly the information identity needs. Sprout has **lenient
identity and no real resolution pass** — the inverse of a sound design.

## 3. Goals and non-goals

**Goals**

- G1. Type identity is **globally unique and module-qualified**; two same-short-named
  types from different modules are distinct and correctly fail to unify.
- G2. **Name resolution is a distinct concern from identity comparison.** A dedicated
  resolution step maps every surface type reference to a canonical identity, once;
  every downstream consumer compares that identity exactly.
- G3. **Delete all `after_last_dot` on the identity path** (~37 sites pending the
  identity-vs-display audit — G3 includes producing that audited partition as a
  deliverable). Bare-name stripping survives only where it is genuinely a *display*
  concern (diagnostics text), never an *identity* concern.
- G4. No regression in self-compile; the change reaches a bootstrap fixed point.
- G5. Lay the identity foundation the iface arc requires for sound separate
  compilation (§9).
- G6. **(DONE — see Status.)** **Identity is an opaque, resolver-minted
  token** — `wrap TypeId = String` (zero-cost distinct wrapper). Consumers compare via `type_id_eq` and
  render via `type_id_display` only; **no consumer destructures or does string surgery
  on it.** This is what makes the representation swappable (FQN string → stamp →
  structured functor/path) without a consumer re-migration, keeping N2a's door open.

**Non-goals**

- N1. Not a syntax change — no new surface syntax for qualifying types (bare `C`
  still works in source; resolution handles it).
- N2. Not structural typing or type abstraction/sealing (OCaml-style) — identity stays
  nominal; opacity of `wrap` (the value-level feature) is orthogonal and already
  correct within a module.
- N2a. **Functors / parametrized modules and path-dependent types are neither adopted
  nor foreclosed.** Sprout has neither today, which is *why* a canonical FQN is a
  total, injective key for type identity (one declaration ⟹ one type; §4 divergence
  note). Those two features are the only things that force *generative* identity a name
  cannot express — OCaml stamps a fresh id per functor application; Scala carries a
  prefix per path. This design must **not** make adding them later hard. It does not,
  and in fact moves toward them: the resolver + opaque-comparison indirection it
  introduces is the exact layer generativity needs. The one requirement that keeps the
  door open is **G6** below (opaque identity) — so that switching the identity
  *representation* from an FQN string to a stamp / structured path is a change to the
  `TypeId` type and the resolver alone, never a re-migration of consumers. Prior art
  backs this: OCaml's `Ident.t`, Rust's `DefId`, GHC's `Unique`, Scala's `Symbol` are
  all **opaque** to consumers (compared via `same`/`==`), which is precisely what lets
  OCaml hide generative stamps behind the same interface a name scheme would use.
- N3. Not the iface artifact format itself (that is the iface-arc docs); this change is
  its prerequisite, delivered independently.
- N4. Not interning to integer ids in v1 (see §5 "phasing") — FQN strings are the v1
  identity; interning is an optional later optimization.

## 4. Prior-art survey

> Every row is verified against a primary source (language reference or compiler
> source/dev-guide), per AGENTS.md §Design Change Process. Consensus is stated with the
> divergences that matter for our choice.

| Compiler | Identity carrier | Uniqueness key | Resolution a separate phase? | Primary source |
|---|---|---|---|---|
| **GHC (Haskell)** | `TyCon`'s `Name` (bundles `Unique`, `OccName`, `NameSort`); `External`/`WiredIn` names also carry the defining `Module`. | A `Unique` (unwrapped `Int`). Equality of both `Name` and `TyCon` runs *solely* through the `Unique` (`cmpName`, `getUnique`); a deterministic `Unique` is allocated per `(Module, OccName)` "original name". | **Yes.** The renamer maps surface `RdrName` (`Unqual`/`Qual`/`Orig`/`Exact`) → `Name`; identity is only ever tested afterward, by `Unique`, never by re-resolving the name. | `GHC.Types.Name`, `GHC.Core.TyCon` (Eq/Ord/`Uniquable` instances), `GHC.Types.Name.Reader` |
| **Rust (rustc)** | Nominal type = `TyKind::Adt(&AdtDef, …)`; `AdtDef` wraps a `DefId`. | `DefId = (CrateNum, DefIndex)`. `CrateNum` distinguishes same-named types across crates; `DefIndex` within a crate. Comparison is by `DefId`, not name (structs are "defined by their name … not carried within the type"). Source-stable form is `DefPath`/`DefPathHash`. | **Yes.** `rustc_resolve` maps surface paths → `DefId`s before type checking; identity comparison later is keyed on the resolved `DefId`s. | rustc-dev-guide: `hir.html`, `ty-module/generic-arguments.html`, `name-resolution.html` |
| **OCaml** | A type is *named* by a `Path.t` (`Pident`/`Pdot`/`Papply`); identity is decided by the underlying `Ident.t`. | A unique integer **`stamp`** minted per binding (`create_local` increments a global counter). `Ident.same` compares stamps, so equal text + different stamp = distinct types. `Global of string` (persistent cross-unit ids) falls back to name comparison. | **Yes.** `Env.lookup_type : Longident.t → Path.t * decl` resolves; stamp-based `Ident.same`/`Path.same` and by-path `Env.find_type` are distinct later steps. | `ocaml/ocaml` `typing/{ident.ml,ident.mli,path.mli,env.mli}` |
| **Scala 3 (Dotty)** | Nominal type = `TypeRef` (a `NamedType`) = `prefix` + `designator`; the designator is a `Symbol` post-resolution. | `Symbol` identity by reference, backed by `val id: Int` (`hashCode = id`), **not** name — "multiple symbols can share the same name but possess different identities." Owner chain (to the root package) gives structural qualification; named types are hash-consed so equality collapses to `eq`. | **Yes.** Surface name → `Denotation` → `Symbol` (prefix/scope lookup); identity compares resolved symbol designators afterward, never re-consulting names. | `scala/scala3` `compiler/…/core/{Symbols,Denotations,Types,Names,SymDenotations}.scala` |

**Confirmed consensus.** All four production compilers represent a nominal type's
identity as a **globally-unique reference — a `Unique` / `DefId` / stamped `Ident` /
`Symbol`-id — assigned by a resolver**, and perform name→identity lookup as a
*separate, earlier* phase from identity comparison. **None compare types by
unqualified name string.** Sprout is the outlier: it skipped the resolver and let
inference improvise identity from short names (`after_last_dot`).

**Divergence that informs our choice.** The four use an in-memory *integer* id
(`Unique`/`DefIndex`/`stamp`/`Symbol.id`), but each also has a *stable, serializable*
form that is effectively a qualified original name — GHC's `(Module, OccName)`, Rust's
`DefPath`/`DefPathHash`, OCaml's `Global of string`. **Our v1 "canonical FQN string as
identity" is exactly that stable form**; interning it to an integer (N4) is the
in-memory optimization the others apply, deferrable until profiled. We do *not* need
OCaml's per-binding stamps or Scala's owner chains — those exist to give *fresh*
identity to functor instantiations and path-dependent types, which Sprout has not; an
FQN is a total, unique key for our flat module namespace.

## 5. High-level implementation overview (for approval before editing)

Three coupled changes, staged so each is independently testable and bootstrap-safe.

**Overarching principle (G6) — opaque identity.** Introduce `wrap TypeId = String` in
`types.sprout`. `TypeId` *is* a canonical FQN string under the hood in v1 (zero-cost),
but only two operations may touch it: `type_id_eq(a, b) -> Bool` (identity comparison)
and `type_id_display(t) -> String` (short-name rendering for diagnostics, the sole
sanctioned `after_last_dot` site). The **resolver is the only minter** of a `TypeId`.
No other code destructures it or splits it on dots. This boundary is what makes the
representation swappable later (N2a): a functor/PDT future changes `TypeId` and the
resolver, not the ~37 consumers.

**Change A — trust the resolution the bundler ALREADY does (verified 2026-07-10).**
The doc originally scoped this as "build a resolver." Investigation showed the
resolver **already exists and is correct**: `bundler.qualify_type_name`
(`bundler.sprout:795-809`) rewrites every type reference in every annotation to its
canonical name, with the right precedence — `ctx_type_locals` (current module) first,
then imported/unqualified types, i.e. **local-shadows-prelude is already implemented.**
Empirically (`--phase dump-qualify` on the collision probe): a local `Maybe` reference
resolves to `main.Maybe`, while the prelude's own `Maybe` stays `Maybe` — because the
prelude has no module header (`module_name = ""`, `bundler.sprout:24`), so its
canonical identity simply *is* the unqualified `"Maybe"`. The two are already distinct
strings (`"main.Maybe"` ≠ `"Maybe"`) *before* anyone strips.

So Change A is not "build a resolver" — it is **stop `lookup_type_var`
(`infer.sprout:124`) from discarding the bundler's already-correct qualification.**
The canonical identity is present at both declaration and reference sites today; the
sole defect is the `after_last_dot` strips that throw it away. This shrinks the change
dramatically and removes the highest-risk piece (a new resolution pass) entirely.
Corollary — this resolves **open question #1** in part: importless/bare files and the
prelude get the empty-module canonical form (their bare name *is* their identity),
which is self-consistent as long as nothing strips.

**Change B — `TConst` carries the opaque canonical identity (`TypeId`).**
`TConst`'s payload becomes a `TypeId` (per G6, `wrap TypeId = String` whose string is
the canonical FQN), rather than a bare name. Construction sites that mint `TConst` for
a declared type obtain the `TypeId` from the resolver. Built-in primitives (`Int`,
`Bool`, `String`, …, `types.sprout:54-59`) map to a small closed set of pre-resolved
`TypeId`s (they have no module) that the resolver never resolves against a user module.
Interning the FQN to a unique integer (N4) — or, further out, replacing it with a
structured identity for functors/PDTs (N2a) — is then a change to the `TypeId`
representation and the resolver *only*, because consumers touch it solely through
`type_id_eq`/`type_id_display` (G6). Whether v1 changes `TConst`'s *shape*
(`TConst TypeId`) or keeps `TConst String` with a `TypeId`-discipline convention is an
implementation detail resolved in Change C's audit; the shape change is preferred
because the type checker then *enforces* the opacity boundary rather than trusting it.

**Change C — exact-match everywhere; delete identity-path `after_last_dot`.** Three
subsystems migrate to canonical identity, and they **must move in lockstep** — a
registration side keyed FQN against a lookup side keyed bare is a silent miss, this
project's recurring failure mode (cf. the `vec_sort` forwarded-`Ord` miscompile and
the tyvar-identity/dict-resolution arc):

1. **Unifier** — `unifier.sprout:190` becomes `if a == b`.
2. **Constructor map** — `build_ctor_map` + the ctor/exhaustiveness lookups key by FQN
   (they already move together per the `infer.sprout:355` comment).
3. **Instance dispatch** — `concrete_type_str` (`infer.sprout:1112`) stops stripping,
   and the ~6 `"@inst:{Class}:{head}"` lookup sites (`infer.sprout:827,1307,1371,1391,
   1405,1564`) key on the FQN head — in the *same* step registration stops stripping
   (registration flows through `lookup_type_var`, so Change A already makes it produce
   FQN). This is a soundness *gain*, not merely a refactor: distinct same-named types
   get distinct instance heads, closing the collision the `@inst` namespace has today.

`after_last_dot` remains only for **display** (`ctor_display` at `infer.sprout:2051`,
diagnostic rendering) where the user should see the short name. The audited
identity-vs-display partition (G3) is produced as the first artifact of this change.

**Sequencing within the change (each a bootstrap fixed point):**

1. Stop `lookup_type_var` (`infer.sprout:124`) stripping — return the bundler's
   already-canonical name. With the downstream `after_last_dot` sites *still in place*,
   behavior is unchanged (canonical name produced, then re-stripped downstream), so
   this is a green-self-compile no-op that can be validated in isolation. (Since Change
   A turned out to be a strip-removal, not a new pass, this step and step 2 may
   collapse into one small change — decide when implementing.)
2. Flip `build_ctor_map` + the ctor/exhaustiveness lookups **and instance-head
   registration/lookup** (`concrete_type_str` + the `@inst:` sites) to FQN keys
   together — all three must agree in the same step, or dispatch silently misses.
3. Flip the unifier to exact match and remove the remaining identity-path strips.
   Run the collision fixtures (§10) — now rejected — plus full self-compile.
4. Warn-mode blast-radius survey **before** step 3's flip is hard-error: existing
   fixtures/examples that redefine a prelude type name trip it; each is either a real
   latent shadow bug to fix or a fixture to adjust (W3/W5 rollout pattern).

## 6. Syntax and semantics impact

- **Surface syntax: none.** Bare type names in source continue to work; resolution
  maps them. (N1.)
- **Semantics:** two type declarations with the same short name in different modules
  are now *distinct types*. A program that relied on the accidental collision (e.g.
  silently treating its own `Node` as the stdlib `Node`) is now correctly rejected —
  this is the point, and is a **breaking change for such programs** (§8).
- **Shadowing:** a module-local type that shares a prelude short name shadows the
  prelude type *by resolution* (local decl wins), and is a *distinct identity* — no
  longer a silent unification. Interaction with the importless/bare-file prelude
  policy (`BACKLOG.md:61`) is called out in §8.

## 7. Type-system and error-message impact

**Type system.** Identity strictness is the only conceptual change; inference and
generalization *structure* are untouched. Two clarifications on the dict subsystem,
since "untouched" would be an overclaim:

- **`@fwd` dict *forwarding* is genuinely untouched** — it keys on `TVar` names
  (`unifier.sprout:182-188`, `find_fwd_tdict_in_args`), **not** `TConst`, so canonical
  identity does not perturb it (verified; the skolemization hazard the fundamentals
  review flagged does not apply here).
- **Instance *head* dispatch IS on the identity path and migrates in lockstep**
  (§5 Change C.3): the `"@inst:{Class}:{head}"` head moves from bare to FQN on both
  registration and lookup together. This *strengthens* soundness — same-short-named
  types stop sharing an instance slot — but it is the highest-risk part of the change
  and the first thing to test (§10). It must not be described as untouched.

`deriving`-minted types (`main.A`) already carry FQNs and become *correct* rather than
accidentally-matching.

**Error messages.**

- The primary new/changed diagnostic is the type-mismatch at `unifier.sprout:190`,
  which must render **short names for readability but disambiguate on collision** —
  e.g. `Type mismatch: Node (defined in main) vs Node (defined in stdlib.prelude)`
  rather than the current `Type mismatch: Node vs Node` (which would be baffling).
  This requires the message to carry both the FQN (for the module note) and the short
  name (for the head).
- The false "Non-exhaustive match on <T>" class (the sproutd symptom) disappears —
  ctor sets are keyed by the right identity.

## 8. Compatibility / migration notes

- **Programs relying on the collision break** (by design). The blast-radius survey
  (§5.4) enumerates them across stdlib + examples + compiler self-compile; each is
  fixed on-branch before the hard-error flip.
- **Bootstrap.** This is a `stdlib/compiler/` change → seed refresh required (DoD
  9/§4). Expect **multiple** seed refreshes across the staged sequence (§5). If any
  step changes the parser surface, use the 2-step bootstrap (docs/debugging.md).
  Memory `project_strip_module_prefix_bootstrap_trap` warns that new string-allocating
  helpers in infer can trigger binary-level GC issues — the resolver's table build
  must reuse the existing bundle walk, not add a parallel allocating pass.
- **Importless/bare files** (`BACKLOG.md:61`): a bare file gets no prelude, so its
  types have no collision surface with the prelude; but its own types resolve under
  `module main` (or the importless default). Confirm the resolver assigns a coherent
  canonical module to importless decls (`project_importless_selfcontained_loudfail`).
- **`wrap` types** already have correct same-module opacity; qualifying identity makes
  their cross-module opacity correct too (closes the one hole T7 named for wrap).

## 9. Why now — convergence with the iface arc

This is not a T7 side-quest; it is the **foundation the iface arc stands on**. The
double-inference handoff's own guard-rail (`iface-arc-double-inference-handoff`,
§"Why the iface arc is the right fix") warns that whole-program bundling is what
currently guarantees cross-module coherence "for free," and that peeling modules out
re-opens separate-compilation correctness. A `.iface` for `stdlib.foo` must state
`stdlib.foo.C` as a **globally-unique** thing; the instant separate compilation stops
bundling every decl into one namespace, bare-name identity collapses. So:

- Module-qualified identity (this doc) is a **prerequisite** for the iface arc's PR 2+
  (loading modules instead of re-inferring) to be *sound*, not merely faster.
- Recommended order: land this identity change first (or jointly), then item #3's
  double-inference elimination consumes the new representation as its first client.

## 10. Tests added / updated

- **Collision rejection (the T7 regression, DoR).** A fixture bundling two type decls
  that share a short name across modules, each used at its own layout, must be
  **rejected** post-fix and (verified) silently mis-unify pre-fix. Model on the
  existing `tests/stdlib/compiler/test_diagnostic_name_collision.spr` (co-imports two
  compiler modules). Because the fix is upstream of the unifier, the failing test is
  at **program/check level**, not a unifier unit test.
- **Single-module shadow.** A user program redefining a prelude type name (`Node`,
  `Maybe`-shaped) — must be a *distinct* type; a cross-assignment must be rejected.
- **Positive guards.** Same-type unification across a bare reference and a qualified
  reference of the *same* declared type still succeeds (the case exact-match must not
  break); `deriving`-minted `main.A` round-trips.
- **Instance dispatch (highest-risk, §5 Change C.3).** An `instance` on a user type
  still resolves after the FQN migration (registration and lookup agree) — a positive
  test per class of head (`TConst` head, `TApp` head like `Vec a`). Plus a *collision*
  test: two same-short-named types, each with its own instance of one class, dispatch
  to their respective instances rather than sharing a slot. This is the regression
  guard for the lockstep requirement — a bare/FQN mismatch would make it fail to
  resolve.
- **Exhaustiveness.** The sproutd-shaped co-import no longer yields a false
  non-exhaustive error.
- **Bootstrap fixed point** at each staged step (§5).

## 11. Spec / docs updates

- Add a normative **type-identity** subsection to `docs/spec-v0.md`, worded about the
  **property, not the representation** (so it does not constrain a future functor/PDT
  design at the spec level, N2a): *two types are equal iff they have the same canonical
  identity assigned by resolution; name resolution maps surface references to that
  identity; bare names are a resolution convenience, not an identity.* The spec states
  that identity is canonical and resolver-assigned — **not** that it is an FQN string
  (the FQN is the v1 representation, an implementation choice, not a normative one).
- Update `BACKLOG.md`: retire the "Bare-name type identity" deferred item and W11/T7,
  pointing at this doc; note the iface-arc dependency edge.
- Refresh the memory `project_bare_name_type_identity_collision` on landing.

## 12. Open questions (resolve before / during implementation)

1. **Canonical module for importless/bare files** — what FQN prefix do their decls
   get? (`main`? a synthetic module?) Determines the shadow semantics in §8.
2. **Primitive identity set** — the exact closed set of pre-resolved single-token
   identities (`Int`, `Bool`, `String`, `Char`, `Double`, `Unit`, and the built-in
   type constructors `List`/`Maybe`/`Result`/`Vec`/…). These must never be resolved
   against a user module.
3. **Interning (N4)** — defer FQN→int interning to a follow-up, or fold it in now for
   the `str_eq`-per-unification cost? (Profile-gated; likely defer.)
4. **Empirical bug size** — the §5.1 instrumentation number (how often bare vs
   qualified identity diverges in a real self-compile) sizes the actual exposure and
   should be recorded here before the hard-error flip.
