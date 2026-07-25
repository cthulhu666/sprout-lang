# Concrete-instance devirtualization (v0)

Status: **LANDED**. Increment 1 covered single-block, empty-context instances; increment 2 extended to
superclass-expanded dispatch (`Ord`) and context-constrained instances (`Eq (Maybe a) where Eq a`).

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
- the args carry **exactly one** leading `TDict` whose evidence is `EvClasses blocks`, and
- among `blocks`, the one whose fully-resolved **concrete** `EvInstance` *provides `mname`*
  (`ctx_inst[key]` resolves the method) — call its context evidence `children`, and
- `consume_inner_dicts(children)` contains no unresolved sentinel.

On a hit it emits `TCall(TVar(impl_name, mt, …), expand_user_args(args) ++ consume_inner_dicts(children), …)`
— the concrete fn, the original user args, plus the instance's **own context dicts** as trailing args.
The user-arg `TDict` witness and any **sibling superclass blocks** are dropped. Anything else
(EvForward/polymorphic, unresolved inner dict, >1 leading TDict) falls through to the `__cm_` path.

**Why the gate is sound.** The concrete fn `__tc_{Class}_{Type}_{method}` takes user args + one dict
per the instance's own context constraints (its `children`) — **never** superclass dicts. Superclass
dicts exist only in the generic `__cm_` wrapper for its internal super-method access; a concrete instance
*body* resolves super methods concretely (verified: `__tc_Ord_Rank_compare` whose body calls `eq` still
takes only `(a, b)` — no Eq dict). So the concrete fn's arity is exactly `user_args + |children|`, and
passing `consume_inner_dicts(children)` matches it by construction. The **dispatch block is identified
by method presence** (`compare` lives only in the `Ord` block, not the `Eq` super block), which both
picks the right block and skips sibling supers. `opt --passes=verify` catches an arity/type mismatch;
a dict-*ordering* mistake would not (all dicts are `i64`), so a multi-constraint value test
(`compare` on `(Int, String)`) is the guard that the inner dicts land in `inst_constraints` order.

**Composition with CPR.** Because the retargeted callee is a real top-level fn, the match-site
Maybe/tuple CPR (`ast_to_ir.sprout`) routes it to that fn's `_worker` automatically — so
`match from_ordinal(tag) with …` calls `@__tc_…_from_ordinal_worker` returning `{i64,i64}` and the
`Maybe` stays unboxed. Devirt and CPR compose without either knowing about the other.

## 5. Scope (honest, verified)

Devirt fires for any concrete class-method call whose dispatch instance is fully resolved:
- **no superclass, no context** (`Enum`, `Eq`/`ToString` on flat types): direct call, **all dicts
  dropped** — the full win;
- **superclass** (`Ord`): the sibling super block is dropped; `__tc_Ord_Int_compare(a, b)` takes no
  dicts, so **2 closures → 0**;
- **context-constrained** (`Eq (Maybe a) where Eq a`): the resolved inner dict is forwarded as a
  trailing arg; the **outer** wrapper + dict are dropped (the inner remains a closure — recursively
  devirt'ing it would be monomorphization, out of scope);
- **combined** (`Ord (Maybe a) where Ord a`): both — sibling super block dropped, inner Ord dict
  forwarded.

It does **not** fire for polymorphic/forwarded dispatch (`EvForward` — genuinely run-time, must stay
dictionary-passing) or when an inner dict is unresolved. Deeper wins still open: recursively devirt'ing
a concrete inner dict (monomorphization) is deliberately out of scope.

## 6. Semantics / type-system / error impact

None. Value-neutral; no source, type, or diagnostic change. The typechecker and evidence are untouched;
only the lowering of an already-resolved concrete dispatch changes.

## 7. Tests

- `tests/stdlib/test_devirt_classmethods.spr` — behavior (value-neutral) across every shape: concrete
  `Enum` `from_ordinal`/`ordinal`, no-context `Eq Int`, superclass `Ord Int` (**2→0**), a **user `Ord`
  whose `compare` uses `eq`** (proves the super method resolves concretely, so no Eq dict is needed),
  context `Eq (Maybe Int)`, combined `Ord (Maybe Int)`, and the **multi-child ordering guard**
  `compare` on `(Int, String)` with the *second* component deciding (a swapped inner dict would run an
  Int through the String dict — caught here, invisible to `opt --passes=verify`). Plus a **polymorphic**
  consumer (forwarded dict, must not devirt).
- `tests/smoke_shapes/09_devirt_classmethod.spr` — IR shape: concrete `from_ordinal` emits no
  `sprout_alloc_closure_env` and a direct `__tc_` call.
- IR-verified gate per shape (direct `__tc_` vs `__cm_` fallback); `opt --passes=verify` clean; the
  self-hosted reseed reaches a fixed point — the compiler devirt's its own `Eq`/`Ord`/`ToString` calls
  (including multi-child instances) and still compiles itself.

## 8. Measured result

`terrain_rivers_demo` `bake_tile`: **2 closures + 1 tuple per non-river tile → 0 heap allocations**.
Combined with tuple SRA this closes the per-tile bake residue identified in
`docs/scalar-replacement-v0.md` §1.
