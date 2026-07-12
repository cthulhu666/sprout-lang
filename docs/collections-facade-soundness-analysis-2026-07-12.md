# Collections Façade Soundness Analysis

**Date:** 2026-07-12
**Status:** Supporting analysis — non-normative. Does not override `docs/spec-v0.md`.
**Scope:** The `Map`/`Dict`, `Vector`/`Vec`, `NativeSet`/`Set`, and `Ref` design in
`stdlib/prelude.sprout`, and the general "raw builtin handle + boxed newtype façade"
pattern it embodies.

---

## TL;DR

Sprout's collections use one pattern uniformly: a C-runtime handle (an untyped `i64`)
wrapped in a single-constructor Sprout `type` that carries the public API and typeclass
instances. This is the same shape as Rust's `Vec`/`RawVec` — a safe wrapper over a raw
core.

The verdict is **currently sound, structurally fragile — not broken**. The pattern is a
defensible bootstrapping choice, but Sprout has adopted its *form* ahead of the two
features that make it pay off: **module-level opacity** (so wrappers actually hide their
contents) and **zero-cost parametric newtypes** (so wrapping is free). As a result the
façade today is both **unenforced** (users can pry it open and forge it) and **boxed**
(each wrap heap-allocates). Neither causes incorrect behavior for `Dict`/`Set` today, but
`Vec`'s implied value semantics are already broken by the combination of mutation externs
and an unenforced effect system.

---

## 1. The pattern

Every collection follows the same two-layer shape:

```
export type Dict v = | Dict (Map v)        # prelude.sprout:61
export type Vec  a = | Vec  (Vector a)     # prelude.sprout:58
export type Set     = | Set  NativeSet     # prelude.sprout:64
```

- **Lower layer** — a C-runtime handle: `Map`, `Vector`, `NativeSet`, `Ref`. These are
  registered builtin type names (`infer.sprout:3170`, grouped with `Vector`/`Ref`) whose
  values are opaque `i64`s. Their operations are `extern fn` declarations
  (`prelude.sprout:1215–1253`).
- **Upper layer** — a single-constructor `type` that the user sees. It adds the
  value-parametric type (`v`, `a`), the ergonomic API (`dict_get`, `dict_set`, …), and the
  typeclass instances (`Eq`, `Semigroup`, `ToString`).

`Map` itself is a **persistent, path-copying, self-balancing AVL tree** keyed by interned
strings (`runtime/sprout_runtime.c`: `bst_insert_node` → `bst_balance` with left/right
rotations, per-node `height`+`size` fields). `map_get`/`map_set`/`map_remove` are therefore
**O(log n)**, not the O(n) the prelude comments claim (see §6, Known doc drift).

---

## 2. The wrapper is boxed, not zero-cost

`Dict`/`Vec`/`Set` are declared with `type`, which emits a boxed constructor. The IR for a
trivial wrap confirms it:

```llvm
define i64 @main.wrapit(i64 %m) {
  %t$0 = call i64 @sprout_make1(i64 11, i64 %m)   ; heap box, ctor tag 11
  ...
}
```

`sprout_make1` heap-allocates a one-field cell, plus GC push/pop rooting around it. So every
`dict_set`/`vec_*` call that re-wraps a result pays one allocation on top of the underlying
operation.

`wrap` *would* be zero-cost (`spec-v0.md:322–335`: "no boxing call for construction"), but
`wrap` is **monomorphic in v0** (`spec-v0.md:340`), so `Dict v` / `Vec a` — which need a
type parameter — are ineligible. The façade is stuck with boxing until `wrap` gains type
parameters (or `opaque` ships).

> **Correction of record:** an earlier informal claim that "the wrapper costs nothing at
> runtime / the optimizer erases it" was wrong. `type` constructors box; only `wrap` is
> zero-cost, and `wrap` cannot express a parametric wrapper today.

---

## 3. Soundness, in three separate axes

The pattern's weaknesses belong to three distinct notions of "soundness." Conflating them
obscures which are the pattern's own fault and which belong to the surrounding language.

### 3A. Type / memory soundness — holds; erasure is contained

The handle is an untyped `i64`; the value type parameter is **erased** at runtime. This is
safe in practice:

- The surface type system keeps `v` uniform per handle: `map_empty() -> Map v` is
  polymorphic and each concrete `Map Int` unifies `v` consistently, so well-typed code
  cannot insert an `Int` and read a `String`.
- The one "unsafe-looking" extern, `vector_get_direct(v, i) -> a !{IO}`, actually
  **bounds-checks**: `if (index < 0 || index >= v->len) tcp_fail(...)`
  (`runtime/sprout_runtime.c:6187`). It traps on out-of-bounds rather than reinterpreting
  arbitrary memory. "Direct" means it returns `a` rather than `Maybe a`, not that it is
  unchecked.

Residual risk: any front-end unsoundness (non-rigid tyvars / no value restriction, tracked
in the fundamentals review) could let two incompatible `v`-views alias one handle. That is
the type checker's problem, not the façade's.

**Attribution:** the pattern works as intended here.

### 3B. Encapsulation — unenforced (verified)

The wrapper constructor is fully exported, and the prelude is auto-imported wholesale, so
user code can both destructure and forge a `Dict`:

```sprout
fn steal_raw(d: Dict Int) -> Map Int =
  match d with | Dict raw -> raw          -- pry open: compiles

fn forge() -> Dict Int =
  Dict(map_set(map_empty(), "x", 99))     -- forge from raw handle: compiles
```

Both compile and run. There is no `opaque` / abstract-type mechanism in the language yet
(reserved but unbuilt). The abstraction boundary is **convention, not enforcement**.

This is benign *today* because `Dict`/`Set` hold no invariant beyond "wraps a handle," and
`map_set` re-interns keys regardless of how the handle was built. The real cost is
**scalability**: the moment a wrapper carries an invariant (`SortedVec`, `NonEmptyList`,
`ValidatedEmail`), this pattern gives it zero protection — any user can forge an illegal
state. A façade that *looks* like encapsulation but isn't is worse than an obviously raw
type, because it invites reliance it cannot support.

**Attribution:** the pattern's own weakness (missing language feature: opacity).

### 3C. Referential transparency — `Vec` is the live casualty

The runtime exposes in-place mutation: `vector_mutset(v, i, val) -> Unit !{IO}` and
`ref_write(r, val) -> Unit !{IO}` (`prelude.sprout:1222, 1253`). These are tagged `!{IO}`,
which should quarantine them — but the effect tag is **not enforced at the function
boundary**:

```sprout
fn pure_bad(v: Vector Int) -> Unit =   -- claims no effect
  vector_mutset(v, 0, 777)             -- calls an !{IO} extern; compiles anyway
```

This compiles with no rejection. Because `Vec a` wraps the same mutable `Vector`, and a
user can hold both a `Vec(v)` and mutate `v`, the persistent value-semantics illusion of
`Vec` is not protected: two `Vec` values can alias one buffer and one can change under the
other.

`Dict` and `Set` escape this because their public APIs use only persistent operations
(`map_set`/`native_set_insert` return new handles). `Vec` is the single collection whose
implied contract the hole actually breaks.

**Attribution:** primarily the language's fault (unenforced effects, independently tracked).
The pattern *compounds* it because the mutation externs are reachable through the same
visibility leak as 3B.

---

## 4. Pros and cons

### Pros (real credit)

1. **Minimal, uniform runtime ABI.** Everything is `i64`. The GC, closures, and calling
   convention never distinguish `Dict` from `Vec` from `Ref`. For a self-hosting bootstrap
   compiler this uniformity is a large, genuine simplification (same rationale as
   bitcast-through-`i64` for `Double`).
2. **Instances attach to runtime-opaque handles.** A bare `Map` has no ADT identity and so
   cannot carry an `Eq`/`ToString` instance. The wrapper gives the typeclass system
   something to dispatch on. This is the load-bearing reason the wrapper exists at all.
3. **Total-function API.** `dict_get -> Maybe v`, not a partial lookup. Absence lives in the
   type; no null, no exception.
4. **A seam for annotations.** Complexity docs, effect tags, and future refinement attach to
   the wrapper functions rather than being smeared across call sites.

### Cons

1. **Boxing cost with no compensating enforcement** — the sharpest one. Each re-wrap is a
   `sprout_make1` allocation + rooting. You pay for boxing to buy an abstraction the
   language does not enforce; a `wrap` would give the identical (non-)enforcement for free
   but cannot be parameterized.
2. **False sense of encapsulation** (§3B).
3. **`Vec`'s value semantics rest on an unenforced effect system** (§3C).
4. **String-only keys.** The runtime AVL orders by `strcmp` on interned strings; there is no
   `Map k v`. The spec defers this explicitly (`spec-v0.md:569–573`: "`Hash` waits on
   polymorphic-keyed dicts"). Non-string keys require an injective serialization to
   `String`.

---

## 5. Prior-art comparison

| Language | Same pattern? | Encapsulation enforced by | Cost |
|---|---|---|---|
| **Rust** (`Vec`/`RawVec`) | Exactly this | Module privacy: raw pointer field is private; the `unsafe` core is sealed | Zero-cost (monomorphized) |
| **Haskell** (`newtype` + export list) | Yes | `module M (Dict) where` exports the type but not the constructor → abstract | Zero-cost (`newtype` erased) |
| **OCaml / SML** (abstract type in signature) | Yes | `.mli`/`sig` declares `type t` with no definition → opaque outside the module; `Map.Make(K)` functor supplies key ordering | Zero-cost |
| **Scala 3** (`opaque type`) | Yes | Opacity scoped to the defining object; erased elsewhere | Zero-cost |
| **Sprout** (`type` newtype façade) | Yes | **Nothing** — constructor exported, prelude auto-imported | **Boxed** (allocation per wrap) |

Two axes worth drawing out:

- **Rust's `Vec`/`RawVec` is the exact mirror.** A safe wrapper over a raw, unsafe core
  (`RawVec` owns the pointer + capacity). The difference is entirely the two things Rust has
  and Sprout hasn't built: module privacy (raw field genuinely unreachable) and
  monomorphization (wrapper zero-cost). Sprout adopted the *shape* before the *enforcement
  mechanisms*, so it currently gets the liabilities without the two main payoffs.
- **Polymorphic keys are a dictionary-threading problem.** Haskell's `Data.Map` is
  `Ord k`-keyed; OCaml's `Map.Make` functor-parameterizes over the comparator. Both thread
  the key's *comparison capability* — which in Sprout is literally a typeclass dictionary.
  A generic `Map k v` would require passing an `Ord k` dictionary into the map ops, i.e. into
  C. The String-only design exists precisely to hardcode `strcmp` and avoid that. The spec's
  deferral of `Hash`/poly-dict is the same tradeoff acknowledged.

---

## 6. Known doc drift (separate follow-up)

The `Dict` prelude complexity comments are **stale**. They describe an older array-backed
implementation:

- `prelude.sprout:494` — `dict_get`: "O(n) … the native map is a linear key/value array"
- `prelude.sprout:499` — `dict_set`: "O(n)"
- `prelude.sprout:915` — `dict_append_entries`/`dict_from_list`: "O(n²)"

The runtime is a balanced AVL tree, so the true costs are **O(log n)** for get/set/remove
and **O(m log n)** for bulk build. The `Set` comments directly below
(`prelude.sprout:552`, "persistent AVL tree") are correct and describe the *same* `bst_*`
machinery — so the two comment sets now contradict each other. Worth a one-commit fix.

---

## 7. Recommendations (ordered by value/effort)

1. **Parameterize `wrap`** (lift the v0 monomorphism restriction). This alone turns
   `Dict`/`Vec`/`Set` from boxed to zero-cost with **no API change**. Highest leverage.
2. **Build `opaque`** (already reserved in the design notes) so the façade's encapsulation
   becomes real — a prerequisite before anyone builds an invariant-carrying wrapper.
3. **Enforce effects** — independently tracked; it is what makes `Vec`'s value semantics
   honest.
4. **Design an `Ord k`-keyed `OrdMap` in stdlib** (not the runtime), dispatching comparison
   through the ordinary typeclass mechanism (the Haskell/OCaml path), when polymorphic keys
   become a priority.
5. **Fix the stale `Dict` complexity comments** (§6).

---

## Appendix: how the claims were verified (2026-07-12 session)

- **Direct usability of raw handles:** a program using `Map Int` + `map_empty`/`map_set`/
  `map_get` directly compiled and ran (printed `2`).
- **Boxing:** IR emit of `fn wrapit(m: Map Int) -> Dict Int = Dict(m)` shows
  `call i64 @sprout_make1(i64 11, i64 %m)`.
- **Pry/forge:** the `steal_raw`/`forge` program in §3B compiled.
- **Unenforced effects:** the `pure_bad` program in §3C compiled (216 IR defines, no
  rejection).
- **Bounds check:** read of `runtime/sprout_runtime.c:6187`.
- **AVL runtime:** read of `bst_insert_node`/`bst_balance`/`bst_get` in
  `runtime/sprout_runtime.c`.
