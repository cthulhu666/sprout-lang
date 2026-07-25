# Concrete-instance devirtualization (v0)

Status: **LANDED** (increment 1 — single-block, empty-context instances). Follow-ups in `BACKLOG.md`.

## 1. Problem statement

A class-method call at a **statically-known concrete instance** is lowered identically to a
**polymorphic** one: the dispatch dictionary is materialized at run time and the call goes through the
generic `__cm_{Class}_{method}` wrapper. Verified on `from_ordinal(tag)` at `Enum Color`
(`stdlib/compiler/lowering.sprout`, `lower_dispatch_callee`):

```llvm
%t$0 = call @sprout_alloc_closure_env(8)   ; env wrapping ordinal-eta      ← DEAD (never used)
%t$1 = call @sprout_alloc_closure_env(8)   ; env wrapping from_ordinal-eta
%t$2 = call @__cm_Enum_from_ordinal_worker(i64 %tag, %t$0, %t$1)   ; generic wrapper
```

The generic worker then **loads a code pointer from `%t$1` and calls it indirectly** — landing at
`@__tc_Enum_Color_from_ordinal`, the concrete instance function that already exists. So each call at a
compile-time-known instance pays: two heap closure allocations (one entirely dead — only the dispatched
method's slot is used), plus an indirect call through the wrapper, to reach an identity known at compile
time. This is general: it hits **every** concrete `Eq`/`ToString`/`Enum` call, not one demo.

This is the direct residue of the scalar-replacement work (`docs/scalar-replacement-v0.md`): after tuple
SRA removed the rivers-demo `bake_tile` rgb tuple, the *remaining* per-tile allocation was **not** a
`Maybe` box (the `from_ordinal` `Maybe` is already CPR-unboxed to `{i64,i64}`) but these **two
`from_ordinal` dictionary closures**. Devirtualization removes them.

## 2. Goals and non-goals

**Goals.**
1. Lower a class-method call whose dispatch dictionary is a **fully-resolved concrete instance with no
   context constraints** to a **direct call** of the concrete `__tc_{Class}_{Type}_{method}` fn,
   dropping the runtime dictionary entirely — no `sprout_alloc_closure_env`, no `__cm_` indirection.
2. Value-neutral, ABI-preserving: reuse the already-emitted concrete instance functions; no new codegen,
   no representation change.

**Non-goals (hard fence).**
- **No new user syntax / semantics.** Purely an internal lowering optimization.
- **No new dictionary representation.** This is the fence that keeps it clear of the withdrawn
  `@fwd`/`@eta_fwd`/`@super` inert-rep direction (design-doc §19.1) — devirt only *retargets a call*,
  it does not introduce an alternate dict shape.
- **No devirt of polymorphic dispatch.** A forwarded/hidden dict (`EvForward`) is genuinely run-time
  and must stay so.

## 3. Prior art

Devirtualization of statically-monomorphic dictionary/vtable dispatch is standard: GHC specializes
`SPECIALISE`/auto-specialized class methods to concrete instances (`$fEqInt` call sites become direct);
Rust monomorphizes trait methods at concrete types (static dispatch, no vtable); Swift's optimizer
devirtualizes `witness_method` when the conforming type is known. The consensus is that a compile-time
known instance should call the concrete method body directly rather than construct-and-indirect through
a dictionary. This change is the same move, scoped to the cases the current evidence representation
makes unambiguous.

## 4. Implementation

One call-site rewrite in `stdlib/compiler/lowering.sprout`, gated on evidence shape. In `lower_expr`'s
`TCall` arm, `try_devirt_concrete` fires iff:

- the callee is an **unshadowed** `TVar` class method, and
- the args carry **exactly one** leading `TDict` whose evidence is
  `EvClasses [EvInstance _ key Nil]` — a **single class block**, a **concrete** `EvInstance`, with
  **empty `children`** (no context-constraint dicts), and
- `ctx_inst[key]` resolves the method to a concrete impl name.

On a hit it emits `TCall(TVar(impl_name, mt, …), expand_user_args(args), …)` — the concrete fn, the
original user args only, **every dictionary witness dropped**. The callee type is the method's
monomorphic type `mt` at the site, which (empty children ⇒ no dict params) is exactly the concrete fn's
signature. Anything else falls through to today's `__cm_` dictionary-passing path unchanged.

**Why the gate is sound.** Dropping every witness is correct **only** when the concrete fn takes just
the user args. `children == Nil` is that guarantee: a context-constrained instance
(`instance Eq (Maybe a) where Eq a`) carries the inner `Eq a` dict as non-empty `children`, so its
concrete fn expects that dict as a trailing param — the gate rejects it (falls back). The single-block
requirement rejects superclass-expanded dispatch (`Ord`, whose evidence carries the `Eq` super-block).

**Composition with CPR.** Because the retargeted callee is a real top-level fn, the match-site
Maybe/tuple CPR (`ast_to_ir.sprout`) routes it to that fn's `_worker` automatically — so
`match from_ordinal(tag) with …` calls `@__tc_…_from_ordinal_worker` returning `{i64,i64}` and the
`Maybe` stays unboxed. Devirt and CPR compose without either knowing about the other.

## 5. Scope (honest, verified)

Devirt fires for concrete calls to classes **without a superclass and instances without a context
constraint**: `Enum`, `Eq` (on flat types), `ToString`. It does **not** fire for:
- `Ord` (superclass `Eq` → not a lone block) — falls back, still correct;
- context-constrained instances (`Eq (Maybe a) where Eq a`) — falls back, inner dict preserved;
- polymorphic/forwarded dispatch — stays dictionary-passing (must).

Extending to multi-block (superclass) and inner-dict instances — passing the resolved inner/super dicts
as trailing args to the concrete fn instead of the eta-closures — is a documented follow-up.

## 6. Semantics / type-system / error impact

None. Value-neutral; no source, type, or diagnostic change. The typechecker and evidence are untouched;
only the lowering of an already-resolved concrete dispatch changes.

## 7. Tests

- `tests/stdlib/test_devirt_classmethods.spr` — behavior (value-neutral) across shapes: concrete `Enum`
  `from_ordinal`/`ordinal` (devirt), concrete no-context `Eq Int` (devirt, proves non-Enum), concrete
  context `Eq (Maybe Int)` (**must fall back**, inner dict preserved), concrete `Ord Int` (superclass
  → fall back), and a **polymorphic** consumer (forwarded dict, must not devirt).
- `tests/smoke_shapes/09_devirt_classmethod.spr` — IR shape: concrete `from_ordinal` emits no
  `sprout_alloc_closure_env` and a direct `__tc_` call.
- IR-verified gate on both sides (devirt vs fallback) per shape; `bake_tile` in `terrain_rivers_demo`
  is now **allocation-free** (tuple SRA + devirt); `opt --passes=verify` clean; the self-hosted reseed
  reaches a fixed point (the compiler devirt'ing its own `Eq`/`ToString` calls compiles itself).

## 8. Measured result

`terrain_rivers_demo` `bake_tile`: **2 closures + 1 tuple per non-river tile → 0 heap allocations**.
Combined with tuple SRA this closes the per-tile bake residue identified in
`docs/scalar-replacement-v0.md` §1.
