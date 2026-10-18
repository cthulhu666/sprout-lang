# Specialising constrained functions at known instances — v0

**Status — proposal, not approved.** Non-normative. An optimisation only: no syntax, typing-rule,
evaluation-order or diagnostic change, so `spec-v0.md` is untouched.

## 1. Problem

A `where C a` function receives its class dictionaries as hidden parameters
(`lowering.sprout`, `build_hidden_for_constraints`) and calls every method through them. This holds
even when every caller passes the same concrete instance. `mutvec_sort` on `Int` runs about 2×
slower than a hand-specialised copy, because each comparison is an indirect `compare`
(`docs/sorting-v0.md` §Where the time goes, item 1).

Devirtualisation (`docs/devirtualization-v0.md`) already turns a method call at a known instance
into a direct call. It cannot reach inside a generic function: there the evidence is `EvForward`, a
hidden parameter, not `EvInstance`.

Second, smaller cost: inside a generic body, values of type `a` are rooted as heap values.
`type_kind.sprout` treats a type variable as possibly heap, so an `Int` passed through `a` is
over-rooted.

## 2. Goals and non-goals

**Goals**
1. For a call to a constrained function whose evidence is fully concrete, call a copy of the
   function with the types and evidence substituted. Devirtualisation then removes the dictionary
   calls inside that copy.
2. Keep the generic copy. Every call that is not specialised uses it, unchanged.
3. Make it measurable and switchable from one compiler build, through the existing opt harness
   (`opt_config.sprout`, `just bench-opt`).

**Non-goals**
- **Monomorphisation.** No copies made only to fix a type. A copy exists only to remove a
  dictionary. `docs/accessor-inliner-design-2026-07-11.md` rules out per-type copying, and this
  keeps that line. See §5 for why the line matters.
- **A new IR tier or a general optimiser.** The pass is `TypedExpr` → `TypedExpr`. DLE and CSE
  measured nil on real code (`docs/opt-passes-v0.md`), so nothing beyond this one pass is proposed.
- **New syntax.** No `SPECIALIZE`-style annotation in v0; see §8.
- **Rewriting at known instances** (e.g. `vec_sort` at `Int` choosing a primitive sort). This pass
  makes it possible; it is a follow-up.

## 3. Prior art

Every row was checked against the source named.

| language | generic code at runtime | how concrete copies are made | source |
|---|---|---|---|
| **GHC** | one copy, dictionaries as arguments | `-fspecialise` (on at `-O`): "Specialise each type-class-overloaded function defined in this module for the types at which it is called in this module"; also the `SPECIALIZE` pragma, which makes "a copy of an overloaded function with specific class dictionary arguments" and a rule redirecting calls to it | GHC User's Guide, *Optimisation*; *Pragmas* §SPECIALIZE |
| **Swift** | one copy, taking "a table of function pointers and a box containing `T`" | the optimiser specialises a call when the concrete type is known, but "can only perform specialization if the definition of the generic declaration is visible in the current Module" | `swift/docs/OptimizationTips.rst` |
| **Rust** | none | monomorphisation: the compiler "looks at all the places where generic code is called and generates code for the concrete types the generic code is called with" | *The Rust Book* §10.1 |

**Where they differ.** Rust must copy, because generic values differ in size. GHC and Swift share
Sprout's one-representation-for-all design and copy only as an optimisation, keeping the generic
version. Sprout belongs with GHC and Swift. The visibility limit both of them have (GHC needs
`INLINABLE` across modules; Swift needs the definition in the module) does not bite Sprout today:
`bundler.bundle_file` loads the entry file and all imports into one program, so every body is
visible.

## 4. Design

**Where it runs.** In `compiler.sprout`, `lower_checked`, before dictionary-passing lowering:

```
specialise → lowering.lower_program → run_opt_passes → ir_pipeline (unchanged)
```

Here evidence is still structured (`EvInstance` / `EvForward`), not yet hidden parameters. Every
path to IR goes through `lower_checked`, the `--phase use-ir-codegen` driver included.

**What gets a copy.** A `TCall` of a constrained top-level function whose `TDict` evidence is
`EvInstance` all the way down: no `EvForward`, `EvUnresolved` or `EvMissing` inside it.

**The copy.**
1. Substitute the concrete types for the function's quantified variables in every node's type.
2. Replace each `EvForward` that names the function's own constraints with the caller's concrete
   evidence. Method calls inside now carry `EvInstance`, which devirtualisation handles.
3. Name the copy deterministically from the function name and the mangled evidence (reuse
   `lowering`'s `mangle_type_args` scheme), and point the call site at it.

**A worklist with a cache.** Key: (function, evidence). A copy's body may call other constrained
functions with evidence that is now concrete; those go on the worklist. Each key is copied once.

**Why it terminates.** `spec-v0.md` §7, polymorphic-recursion rule: a function with a `where`
clause binds its own name monomorphically inside its body. So a constrained function calls itself
only at its own type, no call loops back at a larger type, and the set of keys is finite. A test
must back this argument (§7 below), and the pass must decline, not hang, if a key repeats with
growing evidence.

**The rooting gain** comes for free. Inside a copy, types are concrete, so where `ast_to_ir` asks
`type_kind` (parameters, env slots, fields, call results) it gets `IRTScalar` for an `Int`.
Unconstrained generic functions called from the copy are not copied, and stay rooted as before.

**Lint.** Run `verify_dispatch` again on the specialised program: copies are where a wrong
instance could be substituted. A general `TypedExpr` type re-check is a follow-up, not part of v0.

## 5. Why copies follow dictionaries, not types

Copying for types too, so unconstrained callees also get exact rooting, is monomorphisation by
another name. It also loses the termination argument: `spec-v0.md` §7 allows polymorphic
recursion for a function with a complete, constraint-free signature, which can call itself at an
ever-larger type. Removing dictionaries is where the measured 2× is; the rooting gain inside
copies is a side effect, not the target.

## 6. Plan

- **M0 — measure first, change nothing.** A census pass (like `cse_census.sprout`) that counts
  calls to constrained functions with fully concrete evidence, distinct (function, evidence) keys,
  and the node growth copies would add (`typed_ast.node_count`). Run it over the bench corpus, the
  examples and the compiler. Go / no-go on those numbers. A small count means most of the gain is
  in a few places, and an annotation (§8) is the better tool.
- **M1 — the pass, behind a switch.** Add `Spec` to `opt_config.Pass`; `SPROUT_OPT_OFF=spec`
  disables it. A/B with `just bench-opt spec`. Target: the `mutvec_sort` on `Int` gap in
  `docs/sorting-v0.md`.
- **M2 — turn it on by default**, in its own change. This changes the compiler's own IR: reseed,
  then read the full golden IR diff before regenerating (`docs/gates.md` §Golden IR).

## 7. Tests

- An opt-harness fixture (`tests/opt_harness/`): a generic sort at `Int` emits a direct call to the
  `Ord Int` instance in the copy, and the generic copy still exists.
- The same function called both at a concrete instance and from a generic caller: both work.
- Superclass evidence (`Ord` brings `Eq`) and context instances (`Ord (Maybe Int)`).
- Termination: mutually recursive constrained functions; a constrained function calling a
  constraint-free, polymorphically recursive one.
- Same behaviour with the pass on and off: `just test` under `SPROUT_OPT_OFF=spec` as well as with
  it enabled.

## 8. Open questions

1. **Automatic or annotated?** v0 proposes automatic (GHC's `-fspecialise` model) because the
   program is whole and the evidence is explicit. If M0 shows large code growth, the alternative is
   a per-function opt-in annotation. That needs syntax, and a keyword, not a pragma sigil.
2. **Precompiled modules.** `docs/iface-precompiled-modules-v1-draft.md` would load interfaces
   without bodies. A copy needs the body, so that work would bring back GHC's and Swift's
   visibility limit. Decide then whether interfaces carry bodies of constrained functions.

## 9. Impact

- **Syntax, typing, evaluation order, diagnostics:** none.
- **ABI:** none. Generic copies keep their names and signatures. Copies are new internal symbols.
- **Compile cost:** the whole `TypedProgram` is in memory before `ir_pipeline`, so copies add their
  nodes to it. IR is still built one declaration at a time, so the IR side grows only with the
  largest copy. Time and `TypedExpr` memory grow with the number of copies, which M0 measures.
- **Docs on landing:** this doc's status; `docs/sorting-v0.md` option A; a row in the status
  table of `docs/typeclass-policy-v0.md` (beside "Dictionary cost at concrete instances"); a note
  beside §Concrete-instance devirtualization in `docs/compiler-internals.md`; and the `BACKLOG.md`
  entry "Specialise generic functions at concrete types", deleted when M2 lands.
