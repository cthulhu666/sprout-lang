# Specialising constrained functions at known instances — v0

**Status — proposal, not approved.** Non-normative. An optimisation only: no syntax, typing-rule,
evaluation-order or diagnostic change, so `spec-v0.md` is untouched.

## 1. Problem

A `where C a` function receives its class dictionaries as hidden parameters
(`lowering.sprout`, `build_hidden_for_constraints`) and calls every method through them. This holds
even when every caller passes the same concrete instance. Generic `mutvec_sort` on `Int` takes
120 / 224 ns per element at n=440 / 100k, against 55 / 109 for a hand-specialised copy: about 2×
(`bench/results-2026-10-18-native-runtime.md`; cause in `docs/sorting-v0.md` §Where the time
goes, item 1).

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
  but a store of an `a` cannot be classed, so inside a generic body every store would pay it.
- **Function values.** A combinator calls an unknown closure, so a `Maybe` that a lambda returns
  is boxed (`BACKLOG.md`, "A `Maybe` returned by a lambda is boxed").

M0 (§7) counts all three, so the choice of lever follows the numbers.

## 2. Goals and non-goals

**Goals**
1. For a call to a constrained function whose evidence is fully concrete, call a copy of the
   function with the types and evidence substituted. Devirtualisation then removes the dictionary
   calls inside that copy.
2. Unspecialised calls keep using the generic version. When no call to it is left,
   `dce.elim_unreachable` drops it (`ir_pipeline.sprout`, before `phase_b_program`).
3. Make it measurable and switchable from one compiler build, through the existing opt harness
   (`opt_config.sprout`, `just bench-opt`).

**Non-goals**
- **Monomorphisation.** No copies made only to fix a type or a representation in v0. A copy
  exists only to remove a dictionary. `docs/accessor-inliner-design-2026-07-11.md` rules out
  per-type copying, and this keeps that line. See §5 for why the line matters.
- **A new IR tier or a general optimiser.** The pass is `TypedExpr` → `TypedExpr`. DLE and CSE
  measured nil on real code (`docs/opt-passes-v0.md`), so nothing beyond this one pass is proposed.
- **New syntax.** No `SPECIALIZE`-style annotation in v0; see §9.
- **Rewriting at known instances** (e.g. `vec_sort` at `Int` choosing a primitive sort). This pass
  makes it possible; it is a follow-up.
- **The native-runtime Phase 2 exit.** See §6.

## 3. Prior art

Every row was checked against the source named.

| language | generic code at runtime | how concrete copies are made | source |
|---|---|---|---|
| **GHC** | one copy, dictionaries as arguments | `-fspecialise` (on at `-O`): "Specialise each type-class-overloaded function defined in this module for the types at which it is called in this module"; also the `SPECIALIZE` pragma, which makes "a copy of an overloaded function with specific class dictionary arguments" and a rule redirecting calls to it | GHC User's Guide, *Optimisation*; *Pragmas* §SPECIALIZE |
| **Swift** | one copy, taking "a table of function pointers and a box containing `T`" | the optimiser specialises a call when the concrete type is known, but "can only perform specialization if the definition of the generic declaration is visible in the current Module" | `swift/docs/OptimizationTips.rst` |
| **Rust** | none | monomorphisation: the compiler "looks at all the places where generic code is called and generates code for the concrete types the generic code is called with" | *The Rust Book* §10.1 |
| **.NET CLR** | one copy per set of compatible instantiations | the JIT makes a copy per set. Instantiations are compatible if their compilation "gives rise to identical code and other execution structures (e.g. field layout and GC tables)": all reference types share one copy, each primitive type gets its own (on x86, "at least"), and structs share when they have "the same pattern of traced pointers" | Kennedy and Syme, *Design and Implementation of Generics for the .NET Common Language Runtime*, §4.1 |
| **MLton** | none | the whole-program `Monomorphise` pass "eliminates polymorphic values and datatype declarations by duplicating them for each type at which they are used". It terminates because SML has no polymorphic recursion | MLton guide, *Monomorphise* |

**Where they differ.** Rust must copy, because generic values differ in size. MLton copies the
same way, by a whole-program pass, which works only because SML lacks polymorphic recursion. GHC
and Swift share Sprout's one-representation-for-all design and copy only as an optimisation,
keeping the generic version. The CLR copies by representation, not by type: code is shared exactly
where it would be identical. The same section notes that a static scheme cannot specialise
polymorphic recursion; the CLR copes because it specialises lazily, at run time. Sprout belongs
with GHC and Swift. The visibility limit those two have (GHC needs `INLINABLE` across modules;
Swift needs the definition in the module) does not bite Sprout:
`bundler.bundle_file` loads the entry file and all imports into one program, so every body is
visible. The precompiled-modules draft keeps that true, since it serialises each module's parsed
AST (`docs/iface-precompiled-modules-v1-draft.md` §What gets serialized, item 2).

## 4. Design

**Where it runs.** In `compiler.sprout`, `lower_checked`, before dictionary-passing lowering:

```
specialise → lowering.lower_program → run_opt_passes → ir_pipeline (unchanged)
```

Here evidence is still structured (`EvInstance` / `EvForward`), not yet hidden parameters. Every
path to IR goes through `lower_checked`, the `--phase use-ir-codegen` driver included.
`compile_full` bypasses it but emits no IR.

**What gets a copy.** A `TCall` of a constrained top-level function whose `TDict` evidence is
`EvInstance` all the way down: no `EvForward`, `EvUnresolved` or `EvMissing` inside it.
Existential evidence is forwarded per match arm (`resolve.sprout`), so this filter excludes it.

**The copy.**
1. **Rewrite the declaration, not only node types.** Three consumers read the written signature,
   not the inferred types:
   - `lower_fn_decl` adds hidden parameters from the declaration's `where` constraints;
   - `ann_to_irtype` takes a parameter's rooting kind from its written type;
   - `verify_dispatch` derives truth from the written parameters and `where` clause.

   So the copy substitutes the written parameter and return types and has no constraints.
2. Substitute the concrete types in every node's `types.Type`, the `TDict` constraint field
   included.
3. Replace each `EvForward` with the caller's concrete evidence, **by key**, not by position.
   Forward keys are `typed_ast.dict_slot_key(class, type_args)` (`resolve.sprout`, `forwarded_slot`),
   superclass-expanded in `build_hidden_for_constraints` order. Recurse into `EvInstance` children
   and into `TMethodRef` evidence.
4. Name the copy deterministically from the function name and the mangled evidence (reuse
   `lowering`'s `mangle_type_args` scheme), and point the call site at it.

**A worklist with a cache.** Key: (function, evidence). A copy's body may call other constrained
functions with evidence that is now concrete; those go on the worklist. Each key is copied once.

**Why it terminates.** A constrained function cannot call itself, or a member of its binding group,
at a larger type:
- a self-call sees the provisional, ungeneralised scheme (`self_binding_scheme`, `infer.sprout`);
- a binding group is inferred against the members' shared, ungeneralised schemes
  (`register_fn_decl_scheme`, `typecheck_fn_group`).

A cache alone is no backstop: growing evidence is a new key every time. So the pass also declines
a key when the same function is already on the current worklist path with evidence that is a strict
subterm of the new one. This is a structural check, not a numeric cap.

**The rooting gain** follows from step 1. A parameter rewritten to `Int` gets `IRTScalar`, so
`ir_rooting` keeps it out of `heap_origin` and does not root it as a call operand
(`op_exposes_operands`). Call results already use inferred types (`ret_irtype`), so step 2 covers
them. Unconstrained generic functions called from the copy are not copied, and stay rooted as
before.

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

Copying by representation is a third line. Every Sprout value fills the same 64-bit slot, and an
unconstrained generic body only passes and stores a value of type `a`. So a copy at a concrete
type should change the code only through each type variable's class: heap or scalar (rooting,
CPR's scalar paths) and `Double` (B1's inline access). The follow-up must check that claim against
every type-directed decision in `ast_to_ir`. Copies keyed by a class per type variable
(heap, `Double`, other scalar) have a finite key space, so they terminate under polymorphic
recursion. This is the CLR's compatibility rule (§3) applied at compile time. It would reach the
representation axis of §1 without monomorphising. It is not part of v0: M0 counts what it would
reach and cost, and a follow-up decides.

## 6. Relation to the native-runtime plan

`docs/native-sprout-runtime-v0.md` lists specialisation in Phase 2. Its exit is the
**Int-specialised** merge sort within 1.3× of C. This pass brings generic code down to that
hand-specialised level; it does not move the exit number. The plan names the work that targets
it, the `vector_mutset` and `sprout_tag` profile, so that comes first. This pass is the win for
generic code, including Sprout code that Phase 3 moves out of C, provided that code compares
through `Ord`/`Eq`.

## 7. Plan

- **M0 — measure first, change nothing.** A census pass (like `cse_census.sprout`) with one count
  per axis of §1:
  - **Dictionaries:** calls to constrained functions with fully concrete evidence, and distinct
    (function, evidence) keys. Separately, the calls devirtualisation cannot finish: methods with
    their own constraint, and calls into context instances.
  - **Representation:** calls to unconstrained generic top-level functions where some type
    variable is instantiated to a non-heap scalar (`type_kind.type_is_non_heap_scalar`), and of
    those, to `Double`. Distinct keys counted two ways: (function, class per type variable), as
    §5's representation copies would make, and (function, concrete types), as monomorphisation
    would. Functions that call themselves at another type are counted apart, since
    monomorphisation cannot copy them.
  - **Function values:** calls that pass a lambda or a top-level function name for a parameter of
    function type, and distinct (function, argument) pairs.
  - **Growth:** the node growth (`typed_ast.node_count`) each set of copies would add.

  Run it over the bench corpus, the examples and the compiler. Go / no-go per axis:
  - **Dictionaries:** a small count means most of the gain is in a few places, and an annotation
    (§9) is the better tool.
  - **Representation:** few keys, in a handful of accessors, point to inlining those
    (`docs/accessor-inliner-design-2026-07-11.md`). Many keys with bounded growth point to
    representation copies. Type keys far above class keys mean monomorphisation pays many copies
    for the same rooting and B1 gain; what it adds is a per-type layout (`Vec3` as three `f64`s),
    which needs its own case.
  - **Function values:** a count dominated by a few prelude combinators points to inlining those
    before the CPR router runs.

  The counts show reach, not time. Each lever still needs its own A/B before it lands.
- **M1 — the pass, behind a switch.** Add `Spec` to `opt_config.Pass`; `SPROUT_OPT_OFF=spec`
  disables it. A/B with `just bench-opt spec` and with `bench/native_runtime/bench.sh`. Target:
  generic `mutvec_sort` near the Int-specialised row. Gates: self-compile time within its noise
  floor (2.2% in the 2026-10-18 baseline; it is the GC-heaviest workload), and
  `just rooting-cost-gate`, which prices the compiler's own compile.
- **M2 — turn it on by default**, in its own change. From here the compiler is built by this pass,
  so a bug in it miscompiles the compiler that must build the fix. Require a 2-step bootstrap fixed
  point with the pass on (`docs/debugging.md` §2-Step Bootstrap Protocol); `SPROUT_OPT_OFF=spec` is
  read at compile time and is the way back. Then reseed, and read the full golden IR diff before
  regenerating (`docs/gates.md` §Golden IR).

## 8. Tests

- M0: a census fixture with known counts on each axis: a call at concrete evidence, an
  unconstrained function instantiated at `Int` and at `Double`, a function that calls itself at
  another type, and a combinator given a lambda.
- An opt-harness fixture (`tests/opt_harness/`): a generic sort at `Int` emits a direct call to the
  `Ord Int` instance in the copy, with no dictionary parameter and an `Int` parameter unrooted.
- The generic version dropped when every call is specialised, and kept when a generic caller
  remains.
- Superclass evidence (`Ord` brings `Eq`) and context instances (`Ord (Maybe Int)`): correct, and
  the context-instance limit of §4 visible in the IR.
- Termination: mutually recursive constrained functions; a constrained function calling a
  constraint-free, polymorphically recursive one; a constructed case of growing evidence, declined.
- Same behaviour with the pass on and off: `just test` under `SPROUT_OPT_OFF=spec` as well as with
  it enabled.

## 9. Open question

**Automatic or annotated?** v0 proposes automatic (GHC's `-fspecialise` model) because the program
is whole and the evidence is explicit. If M0 shows large code growth, the alternative is a
per-function opt-in annotation. That needs syntax, and a keyword, not a pragma sigil.

## 10. Impact

- **Syntax, typing, evaluation order, diagnostics:** none.
- **ABI:** none. Generic versions keep their names and signatures. Copies are new internal symbols.
- **Compile cost:** the whole `TypedProgram` is in memory before `ir_pipeline`, so copies add their
  nodes to it. IR is still built one declaration at a time, so the IR side grows only with the
  largest copy. Time and `TypedExpr` memory grow with the number of copies, which M0 measures.
- **Docs on landing:** this doc's status; `docs/sorting-v0.md` option A; a row in the status
  table of `docs/typeclass-policy-v0.md` (beside "Dictionary cost at concrete instances"); a note
  beside §Concrete-instance devirtualization in `docs/compiler-internals.md`; Phase 2 in
  `docs/native-sprout-runtime-v0.md`; and the `BACKLOG.md` entry "Specialise generic functions at
  concrete types", deleted when M2 lands.
