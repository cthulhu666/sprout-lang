# Specialising constrained functions at known instances — v0

**Status — approved 2026-10-31; M0 next. Its prerequisite, the `print` redesign, has landed
(§2).**
Non-normative. An optimisation only: no syntax, typing-rule or evaluation-order change, so
`spec-v0.md` is untouched. Diagnostics change in one place (§10).

## 1. Problem

A `where C a` function receives its class dictionaries as hidden parameters
(`lowering.sprout`, `build_hidden_for_constraints`) and calls every method through them. This holds
even when every caller passes the same concrete instance. Generic `mutvec_sort` on `Int` takes
120 / 224 ns per element at n=440 / 100k, against 55 / 109 for a hand-specialised copy: about 2×
(`bench/results-2026-10-18-native-runtime.md`; cause in `docs/sorting-v0.md` §Where the time
goes, item 1). The prelude's generic collection functions pay the same toll per call: `any` on a
4-element `Vec` costs 31.4 ns per element against 25.1 for `vec_any`, which has the same closures
and no dictionaries (`docs/collections-surface-v0.md` §4).

Devirtualisation (`docs/devirtualization-v0.md`) already turns a method call at a known instance
into a direct call. It cannot reach inside a generic function: there the evidence is `EvForward`, a
hidden parameter, not `EvInstance`.

Second, smaller cost: a parameter written as `a` is rooted as a heap value (`ann_to_irtype` in
`ast_to_ir.sprout`), so an `Int` passed through `a` is over-rooted.

Erasure costs on two more axes, which this pass does not target:

- **Representation.** A generic body cannot tell a `Double` from a heap pointer. B1's inline
  vector access fires only where `Vector Double` is written in the source, so not through
  `mutvec_at` (`docs/phase-d-numeric-fastpath-design-2026-07-11.md` §B1, *Reachability*), and a
  `LinVec Double` write stays a call (`BACKLOG.md`, "`LinVec Double` writes do not inline"). A
  generational write barrier must fire only on pointer stores (`docs/gc-generational-v0.md` §6),
  but a store of an `a` cannot be classed, so inside a generic body every store at a barrier
  site (`ref_write`, `vector_mutset`, `vector_push`; §7 there) would pay it.
- **Function values.** A combinator calls an unknown closure. So a `Maybe` that a lambda returns
  is boxed (`BACKLOG.md`, "A `Maybe` returned by a lambda is boxed"), and a function built on
  another combinator pays an indirect call per layer per element: `count` wraps its `pred` in a
  `fold` step, two calls where a hand-written loop makes one, 1.6× on a `Vec`
  (`docs/collections-surface-v0.md` §4). A copy of `count` still calls an unknown closure, and
  `any` and `member` still allocate their capturing step closure on every call.

M0 (§7) counts all three, so later designs start from numbers. Only the dictionaries are this
pass's.

## 2. Goals and non-goals

**Goals**
1. For a call to a constrained function whose evidence is fully concrete, call a copy of the
   function with the constrained type variables and the evidence substituted (§4).
   Devirtualisation then removes the dictionary calls inside that copy.
2. Unspecialised calls keep using the generic version. When no call to it is left,
   `dce.elim_unreachable` drops it (`ir_pipeline.sprout`, before `phase_b_program`).
3. Make it measurable and switchable from one compiler build, through the existing opt harness
   (`opt_config.sprout`, `just bench-opt`).

**Non-goals**
- **Monomorphisation.** No copies made only to fix a type or a representation in v0. A copy
  exists only to remove a dictionary. The accessor-inliner proposal
  (`docs/accessor-inliner-design-2026-07-11.md`, unapproved) also lists per-type copying as a
  non-goal; this keeps that line. See §5 for why the line matters.
- **Function values** (§1). Inlining a combinator or copying it per function argument needs its
  own design: size limits, code growth, `local`/`once` closures and their borrowed captures, and
  rooting. M0 counts the sites; it does not choose the lever.
- **A new IR tier or a general optimiser.** The pass is `TypedExpr` → `TypedExpr`. DLE and CSE
  measured nil on real code (`docs/opt-passes-v0.md`), so nothing beyond this one pass is proposed.
- **New syntax.** No `SPECIALIZE`-style annotation in v0; see §9.
- **Rewriting at known instances** (e.g. `vec_sort` at `Int` choosing a primitive sort). This pass
  makes it possible; it is a follow-up.
- **The native-runtime Phase 2 exit.** See §6.

**Prerequisite, met: `print` does not depend on the static type.** A copy substitutes concrete
types (§4, step 2). While `print` chose its lowering from the argument's static type, a `Char`
reaching it through a type variable printed its codepoint, so a specialised copy printed `z`
where the generic version printed `122`, and §8's on/off test failed. `print` is now a prelude
function over `ToString` (`docs/print-dispatch-v0.md`): a generic body that prints needs the
constraint, and every copy prints what the generic version prints.

**Obligation: every type-directed choice made after the pass must agree with the generic
version.** Choices made at inference do not move. `==` on a type variable is already a
dictionary call when the pass runs (`check_eq` in `infer.sprout` emits `TBinary` only when
`is_primitive_eq_type` holds of the operand type at inference), so a copy devirtualises it to the
instance method (`__tc_Eq_Int_eq`), never to `icmp`; comparisons and negation are the same.
`++`'s `str_concat`/`list_append` peephole keys on `__cm_Semigroup_append`, which devirtualisation
already renames, so a copy does not reach it either. What does read a copy's new types:
- CPR worker routing, which checks `is_maybe_type` on a result type;
- B1, which inlines a vector access where `type_is_vector_double` holds;
- rooting kinds, from `ann_to_irtype` on written parameter types and `ret_irtype` on call results.

Each is correct in a copy only if its fast path computes what the generic path computes. §8
tests each.

## 3. Prior art

Every row was checked against the source named.

| language | generic code at runtime | how concrete copies are made | source |
|---|---|---|---|
| **GHC** | one copy, dictionaries as arguments | `-fspecialise` (on at `-O`): "Specialise each type-class-overloaded function defined in this module for the types at which it is called in this module"; also the `SPECIALIZE` pragma, which makes "a copy of an overloaded function with specific class dictionary arguments" and a rule redirecting calls to it | GHC User's Guide, *Optimisation*; *Pragmas* §SPECIALIZE |
| **Swift** | one copy, taking "a table of function pointers and a box containing `T`" | the optimiser specialises a call when the concrete type is known, but "can only perform specialization if the definition of the generic declaration is visible in the current Module" | `swift/docs/OptimizationTips.rst` |
| **Rust** | none | monomorphisation: the compiler "looks at all the places where generic code is called and generates code for the concrete types the generic code is called with" | *The Rust Book* §10.1 |
| **.NET CLR** | one copy per set of compatible instantiations | the JIT makes a copy per set. Instantiations are compatible if their compilation "gives rise to identical code and other execution structures (e.g. field layout and GC tables)": all reference types share one copy, each primitive type gets its own (on x86, "at least"), and structs share when they have "the same pattern of traced pointers" | Kennedy and Syme, *Design and Implementation of Generics for the .NET Common Language Runtime*, §4.1 |
| **MLton** | none | the whole-program `Monomorphise` pass "eliminates polymorphic values and datatype declarations by duplicating them for each type at which they are used". It terminates because SML has no polymorphic recursion, and because the pass may drop unused constructors from datatype declarations | MLton guide, *Monomorphise* |

**Where they differ.** Rust must copy, because generic values differ in size. MLton copies the
same way, by a whole-program pass, which terminates only because SML lacks polymorphic recursion
and unused constructors are dropped. GHC and Swift share Sprout's one-representation-for-all
design and copy only as an optimisation, keeping the generic version. The CLR copies by
representation, not by type: code is shared exactly where it would be identical. The same
section notes that a static scheme cannot specialise polymorphic recursion; the CLR copes because
it specialises lazily, at run time. Sprout belongs with GHC and Swift. The visibility limit those
two have (GHC needs `INLINABLE` across modules; Swift needs the definition in the module) does
not bite Sprout: `bundler.bundle_file` loads the entry file and all imports into one program, so every body is
visible. The precompiled-modules draft keeps that true, since it serialises each module's parsed
AST (`docs/iface-precompiled-modules-v1-draft.md` §What gets serialized, item 2).

## 4. Design

**Where it runs.** In `compiler.sprout`, `lower_checked`, which receives the resolved program from
`compile_phase_check_with_roots`, before dictionary-passing lowering:

```
specialise → lowering.lower_program → run_opt_passes → ir_pipeline (unchanged)
```

Here evidence is still structured (`EvInstance` / `EvForward`), not yet hidden parameters. Every
path to IR goes through `lower_checked`, the `--phase use-ir-codegen` driver included.
`compile_full` bypasses it but emits no IR.

**What gets a copy.** A `TCall` of a constrained top-level function whose `TDict` evidence is
`EvInstance` all the way down: no `EvForward`, `EvUnresolved` or `EvMissing` inside it.
Existential evidence is forwarded per match arm (`resolve.sprout`), so this filter excludes it.

**The substitution domain.** A copy substitutes only the **constrained variables**: those that
occur in a type argument of the declaration's `where` constraints, superclasses included. Their
instances are what the evidence fixes, so they are the key. Every other variable stays as
written, in the signature and in every node type. `fold(step: local b -> a -> b, init: b, xs: f a)
-> b where Foldable f` substitutes `f` only: `fold(f, 0, xs)` and `fold(g, [], ys)` at
`Foldable List` share one copy, and `init` stays heap-rooted in it. Substituting `b = Int` from
the first caller would make `init` a scalar, never rooted (`params_heap_origin` in
`ir_rooting.sprout` skips `IRTScalar`), and the second caller's list unrooted across `g`'s
allocations.

**The copy.**
1. **Rewrite the declaration, not only node types.** Three consumers read the written signature,
   not the inferred types:
   - `lower_fn_decl` adds hidden parameters from the declaration's `where` constraints;
   - `ann_to_irtype` takes a parameter's rooting kind from its written type;
   - `verify_dispatch` derives truth from the written parameters and `where` clause.

   So the copy substitutes the constrained variables in the written parameter and return types
   and has no constraints. Modes survive: `local` and `once` are part of a parameter's function
   type, which substitution leaves in place, and `where linear a` is its own `FnDecl` field, not a
   constraint, so dropping `where` constraints does not touch it.
2. Substitute the constrained variables in every node's `types.Type`, and in each `TDict`'s
   constraint, which is an `ast.TypeConstraint` and so needs the same substitution on `TypeExpr`.
3. Replace each `EvForward` with the caller's concrete evidence, **by key**, not by position.
   Forward keys are `typed_ast.dict_slot_key(class, type_args)` (`resolve.sprout`, `forwarded_slot`),
   superclass-expanded in `build_hidden_for_constraints` order. Recurse into `EvInstance` children
   and into `TMethodRef` evidence.
4. Name the copy deterministically from the function name and its constraints' type arguments
   after substitution, mangled by `lowering`'s `mangle_type_args`, which takes that
   `List ast.TypeExpr`. Point the call site at it.

**A worklist with a cache.** Key: (function, instances of its constrained variables). A copy's
body may call other constrained functions with evidence that is now concrete; those go on the
worklist. Each key is copied once. Each worklist entry records the key that produced it, so a
key's **path** is the chain of producing keys back to a call in the original program.

**Why it terminates.** The premise: a constrained function cannot call itself, or a member of its
binding group, at a larger type.
- a self-call sees the provisional, ungeneralised scheme (`self_binding_scheme`, `infer.sprout`);
- a binding group is inferred against the members' shared, ungeneralised schemes
  (`register_fn_decl_scheme`, `typecheck_fn_group`).

It is pinned by `tests/conformance/type_error/polymorphic_recursion_constrained.spr`, and
`BACKLOG.md` P2 **Allow polymorphic recursion for a CONSTRAINED declaration** would remove it.
A cache alone is no backstop: growing evidence is a new key every time. So the pass also declines
a key when the same function is on its path with instances that are a strict subterm of the new
ones. This is a structural check, not a numeric cap. While the premise holds, no input reaches
the backstop and no test can; it gets its test in the change that lands that BACKLOG entry, which
may not land without it.

**The rooting gain** follows from step 1, for constrained variables only. A parameter typed by
one and rewritten to `Int` gets `IRTScalar`, so `ir_rooting` keeps it out of `heap_origin` and
does not root it as a call operand (`op_exposes_operands`). Call results already use inferred
types (`ret_irtype`), so step 2 covers them. Unconstrained generic functions called from the
copy are not copied, and stay rooted as before.

**Lint.** Run `verify_dispatch` again on the specialised program: copies are where a wrong
instance could be substituted. It checks only copies with rewritten signatures; a copy still
written with `where` would be skipped as `MissingDeferred`. A general `TypedExpr` type re-check is a
follow-up.

**Limits.** The gain stops where devirtualisation stops:
- `try_devirt_concrete` needs exactly one leading `TDict` (`sole_leading_ev_classes`), so a method
  with its own constraint, which takes two, still dispatches dynamically;
- instance bodies are not copied, and a context instance (`Ord (List a) where Ord a`) takes its
  context dictionaries as hidden parameters (`generate_one_instance_fn`), so sorting `List Int` or
  `(Int, Int)` gains nothing inside `compare`.

## 5. Why copies follow dictionaries, not types

Copying for types too, so unconstrained callees also get exact rooting, is monomorphisation by
another name. It also loses the termination argument: `spec-v0.md` §7 allows polymorphic
recursion for a function with a complete, constraint-free signature, which can call itself at an
ever-larger type. Removing dictionaries is where the measured 2× is; the rooting gain inside
copies is a side effect, not the target.

Copying by representation is a third line. Every Sprout value fills the same 64-bit slot, so a
copy could be keyed by a class per type variable instead of a type: heap, `Double`, other scalar,
and "erased" for a call made from inside the uncopied generic body, which keeps calling the
generic version. The key space is finite, so it terminates under polymorphic recursion. It is
coarser than the CLR's rule (§3), which keeps each primitive type apart where this merges `Int`,
`Bool` and `Char`. Three things stand in its way, and a follow-up must settle them:

- **A class has no written type.** §4's copy rewrites the written signature, because
  `ann_to_irtype` reads a parameter's written type. A class-keyed copy needs a per-parameter kind
  override instead, with node types left as `a`. Substituting a representative type (`Int` for
  every scalar) is wrong: each type-directed choice in §2 would then see `Int` where the program
  has `Char`.
- **Field kinds do not change.** A constructor's field kinds come from its declaration
  (`fk.ctor_field_kinds_string`), so a field written `a` is rooted as heap in every copy. A copy
  of `list_map` at `Int` gains little. Changing field kinds needs datatype copying, as MLton does.
- **BCE's `LinVec` premise.** A copy of a `stdlib.linvec` body at `Double` would put an inline
  `vector_mutset` where BCE assumes none (`bce.sprout`, the note above `safe_io_externs`). Such
  copies must exclude `stdlib.linvec.*`, or BCE's summary must first learn `unsafe_pure`.

It is not part of v0: M0 counts what it would reach and cost, and a follow-up decides.

## 6. Relation to the native-runtime plan

`docs/native-sprout-runtime-v0.md` lists specialisation in Phase 2. Its exit, the
**Int-specialised** merge sort within 1.3× of C, was met on 2026-10-18 without this pass. This
pass brings generic code down to that hand-specialised level; it does not move the exit number.
It is the win for generic code, including Sprout code that Phase 3 moves out of C, provided that
code compares through `Ord`/`Eq`.

## 7. Plan

- **M0 — measure first, change nothing.** A census phase, `--phase spec-census`. It runs where
  this pass runs, on the whole resolved program before dictionary lowering
  (`compiler.compile_phase_check_with_roots`). `--phase bind-census` is not a model: it covers
  only the entry file's declarations and stops after typecheck. `cse_census` runs after lowering,
  where the evidence is already hidden parameters.

  **Keys.** A key is the function and its call's evidence trees, which fix exactly the
  constrained variables' instances (§4). Callees come from the program's own constrained
  `TFnDecl`s, and a call counts only when it carries one dictionary per constraint. A local
  named like a constrained function is skipped twice over: the census tracks locally bound
  names, as `verify_dispatch`'s `locals` does, and only a reference to the global receives
  dictionaries. A function calls itself at another type when matching its instantiated type
  against its scheme (`types.match_type_vars`) leaves one of its own type variables bound to
  something other than itself under `tyvar_identity`.

  **Closure.** §4's worklist makes copies of copies: a copy's body calls further functions at
  evidence or classes that are concrete only after substitution. The census walks that closure
  without rewriting and reports direct and closed numbers. Growth uses the closed set.

  **What it needs exported:** `cpr_result_lowerable` (private in `ast_to_ir.sprout`) and a
  per-declaration node count; `typed_ast` exports only the program total (`node_count`).

  Counts, per axis of §1:
  - **Dictionaries:** calls with fully concrete evidence, and distinct keys, direct and closed.
    Separately, the calls devirtualisation cannot finish: methods with their own constraint, and
    calls into context instances.
  - **Representation:** for each closed (function, class per type variable) key, the gain sites
    in the body: parameters and call results typed by a type variable that would become scalar,
    and vector accesses B1 would inline. Calls alone overstate it, since field kinds do not change
    (§5). Also distinct (function, concrete types) keys, as monomorphisation would make, and the
    functions that call themselves at another type, which monomorphisation cannot copy.
  - **Function values:** calls that pass a lambda, a top-level function, a method (`TMethodRef`)
    or a partial application for a parameter of function type. Per site, the callee's size, how
    many closure layers it adds over the instance method, and whether the argument's result could
    be unboxed (`cpr_result_lowerable`). Reported for the function-values design (§2); no
    threshold here.
  - **Growth:** closed node growth per set of copies.
  - **Hotness:** each count split by whether the site is inside a self-tail-recursive function,
    the loop shape BCE versions. A static proxy, not a profile.

  Run it over the bench corpus, the examples and the compiler. The bench corpus decides gain; the
  compiler, whose self-compile is the cost gate, decides growth. Thresholds, fixed at approval
  and not moved after the run:
  - **Dictionaries:** the gain is measured (§1). Automatic (§9) if closed growth on the compiler
    is at most 5% of its nodes; otherwise opt-in.
  - **Representation:** a follow-up design if the bench corpus has at least 10 hot gain sites
    outside the accessor-inliner proposal's initial allow-list (`mutvec_get`, `mutvec_raw`,
    `mutvec_set`, `mutmatrix_get`, `vec_get_or`); otherwise that inliner is enough.
    Monomorphisation enters only for a per-type layout (`Vec3` as three `f64`s), since class keys
    reach the same rooting and B1 gain.

  The counts show reach, not time. Each lever still needs its own A/B before it lands.
- **M1 — the pass, behind a switch.** Add `Spec` to `opt_config.Pass` and to
  `pass_implemented`. `SPROUT_OPT_OFF` is read today in `run_opt_passes`, after lowering, where
  this pass has already run; `lower_checked` reads the config once and passes it to both. `report_opt_delta`
  prints `removed=`, and `bench/optpasses/bench.sh` counts a pass as fired only when that is
  non-zero; this pass adds nodes, so both learn `added=`. A/B with
  `just bench-opt spec` and with `bench/native_runtime/bench.sh`. Target: generic `mutvec_sort`
  near the Int-specialised row. Gates: self-compile time within its noise floor (2.2% in the
  2026-10-18 baseline; it is the GC-heaviest workload), `just rooting-cost-gate`, which prices
  the compiler's own compile, and the emitted binary size of the bench corpus and the compiler,
  within 10%.
- **M2 — turn it on by default**, in its own change. From here the compiler is built by this pass,
  so a bug in it miscompiles the compiler that must build the fix. Require a 2-step bootstrap fixed
  point with the pass on (`docs/debugging.md` §2-Step Bootstrap Protocol). `SPROUT_OPT_OFF=spec`
  changes what a compiler emits; it cannot repair a seed the pass miscompiled, so the way back is
  reverting the seed commit. `just refresh-seed` and `just verify-bootstrap-fixed-point` refuse to
  run while `SPROUT_OPT_OFF` is set, or a pass-off seed gets committed. Then reseed.

  **Golden IR.** `print` is a `ToString`-constrained function, so every snapshot that prints moves,
  and the diff is too large to read (`docs/gates.md` §Golden IR, "When the diff is too large to
  read"). The checks instead: with `SPROUT_OPT_OFF=spec` the IR is byte-identical to the pre-change
  snapshots; with the pass on, every `define` removed is a generic version whose callers all moved
  to copies, and every `define` added is a copy name; and every corpus file compiles and runs
  under both, with the same stdout and exit status.

## 8. Tests

- M0: a census fixture with known counts on each axis: a call at concrete evidence whose copy
  makes a further concrete call (closed vs direct); an unconstrained function instantiated at
  `Int` and at `Double`; a function that calls itself at another type; a local that shadows a
  top-level function; a combinator given a lambda, a method and a partial application; and one
  site of each inside a self-tail-recursive loop.
- **The substitution domain:** one constrained function called at one instance with two different
  instantiations of an unconstrained variable (`fold` at `Foldable List` with an `Int` and a
  `List` accumulator), sharing one copy, run under `SPROUT_GC_STRESS=1`.
- For each type-directed choice in §2's obligation (CPR routing, B1, rooting kinds), the generic
  version and a copy give the same result on the same input.
- An opt-harness fixture (`tests/opt_harness/`): a generic sort at `Int` emits a direct call to the
  `Ord Int` instance in the copy, with no dictionary parameter and an `Int` parameter unrooted.
- The generic version dropped when every call is specialised, and kept when a generic caller
  remains.
- Superclass evidence (`Ord` brings `Eq`) and context instances (`Ord (Maybe Int)`): correct, and
  the context-instance limit of §4 visible in the IR.
- Modes survive the copy: a `local` parameter still lends, and a `where linear a` function still
  accepts a linear argument.
- Termination: mutually recursive constrained functions; a constrained function calling a
  constraint-free, polymorphically recursive one. The growing-evidence backstop is tested when
  its premise goes (§4).
- Same behaviour with the pass on and off: `just test` under `SPROUT_OPT_OFF=spec` as well as with
  it enabled.

## 9. Open question

**Automatic or annotated?** v0 proposes automatic (GHC's `-fspecialise` model) because the program
is whole and the evidence is explicit. If M0 shows large code growth, the alternative is a
per-function opt-in annotation. That needs syntax, and a keyword, not a pragma sigil.

## 10. Impact

- **Syntax, typing, evaluation order:** none.
- **Diagnostics:** a `verify_dispatch` finding is fatal (`resolve_checked` in `compiler.sprout`).
  One raised inside a copy is reported against the original function and the call site that
  produced the copy, not the mangled copy name. `--phase lower` and later phases show copies by their mangled
  names, as they show `__tc_`/`__cm_` names today.
- **ABI:** none. Generic versions keep their names and signatures. Copies are new internal symbols.
- **Compile cost:** the whole `TypedProgram` is in memory before `ir_pipeline`, so copies add their
  nodes to it. IR is still built one declaration at a time, so the IR side grows only with the
  largest copy. Time and `TypedExpr` memory grow with the number of copies, which M0 measures.
- **Docs on landing:** this doc's status; `docs/sorting-v0.md` option A; a row in the status
  table of `docs/typeclass-policy-v0.md` (beside "Dictionary cost at concrete instances"); a note
  beside §Concrete-instance devirtualization in `docs/compiler-internals.md`; Phase 2 in
  `docs/native-sprout-runtime-v0.md`; and the `BACKLOG.md` entry "Specialise generic functions at
  concrete types", deleted when M2 lands.

## 11. Alternatives

- **Caller:** hand-written copies under their own names (`mutvec_sort_ints`, `docs/sorting-v0.md`
  §Options). Callers must know to use them, and every generic function wants one per type.
- **Function:** concrete twins in the prelude (`vec_any` beside `any`), the status quo for
  collections. Two spellings per operation, and only where someone wrote the twin.
- **Compiler** (chosen): one pass covers every constrained function, written or not yet written.
