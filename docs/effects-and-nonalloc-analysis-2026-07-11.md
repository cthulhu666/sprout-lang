# Effects × non-allocation inference — interaction analysis (2026-07-11)

Written after landing Phase D B2 (#162) + the IRCallUnboxed2 reach extension (#163). Those two PRs
elide GC roots around a *hand-maintained allow-list* of non-allocating externs. The durable
replacement is to have the compiler *infer* non-allocation. This note analyses how that inference
relates to Sprout's existing (early-stage) effect system — and, importantly, where it must **not**
lean on it. It is analysis + a proposed first step, not an approved design.

## 1. Ground truth — Sprout's effect system today (all verified in source)

- **Representation exists and is richer than v0 uses.** `types.sprout`:
  `Effect = EffectPure | EffectIO | EffectRow (List String) | EffectVar String`; `TFunc Type Type
  Effect`; `Scheme (List String) (List String) Type Effect` (quantifies type *and* effect vars). So
  rows and effect-polymorphism scaffolding are present; v0 only ever uses the `IO` label.
- **Local effect inference exists.** `infer.sprout` threads a per-expression `Effect` (the `InferOk`
  4th field) and has an effect-combine lattice (`~L381`): `IO` absorbs, `Pure` is identity. So the
  effect *of an expression* is computed by combining sub-expressions.
- **But there is NO interprocedural propagation.** A `FnDecl`'s scheme effect is
  `effect_from_maybe_labels(effects_maybe)` (`infer.sprout:257`) — **the annotation only**, default
  pure. The inferred body effect is never reconciled into the scheme. ⇒ **unannotated = pure by
  default, regardless of body.**
- **Effects are NOT enforced.** Empirically verified: `fn pure_leak(x: Int) -> Int = print_int(x)`
  (declared pure, body calls the `!{IO}` builtin `print_int`) compiles with **zero** errors. The
  checker has no effect unification / mismatch / rejection. The declared-vs-inferred effect is never
  reconciled.

**Summary:** effects are parsed, represented, and *locally* combined, but not propagated
interprocedurally and not enforced. It is scaffolding, not a working effect discipline.

## 2. The trap — allocation is NOT the IO effect (do not ride the IO row)

It is tempting to model "allocates" as another effect label and let the existing combine propagate
it. **This is unsound by construction.** IO-purity ≠ allocation-freedom:

- `vector_get` is `EffectPure` (`-> Maybe a`, no `!{IO}`) yet **allocates** the `Just`/`Nothing` box.
- `IRMakeCtor`, `IRMakeTuple`, `IRMkClosure`, `str_concat`/`str_slice`/`str_from_int`, boxed
  `vector_get` — all allocate, all IO-pure.

The current inference labels every one of these `EffectPure` because it was never asked to track
allocation. If an `alloc` effect naively rode that combine, each would be a false "non-allocating" →
a dropped GC root → use-after-free. **The IO effect system under-approximates allocation on purpose;
alloc leaf-facts cannot come from it.**

## 3. The correct framing — lift `op_triggers_gc`, don't extend the IO row

The allocation leaf-facts already exist, in the right place: `ir_rooting.op_triggers_gc` — an
**exhaustive, no-`_`-catch-all, fail-loud, `SPROUT_GC_STRESS`-verified** per-*op* classification of
what can trigger GC. B2 (#162/#163) already extended it to peek callee names for a verified extern
allow-list.

So the design is: **lift `op_triggers_gc` from per-op to per-function via interprocedural
propagation.** Compute a per-function "may-trigger-GC" summary by a call-graph fixpoint; a Sprout
function is non-allocating iff every op in its body is a non-trigger *and* every callee's summary is
non-triggering. `op_triggers_gc` then consults the summary for `IRCall`/`IRCallUnboxed2` to a Sprout
function, replacing the hand-lists.

This shares the **dataflow shape** of effect inference (Koka models allocation as an inferred effect,
`alloc<h>` — see reading list), which is why "Koka-style" is apt. But its **base facts come from the
proven GC oracle, not the fragile IO subsystem.** Non-allocation is a *sibling* of IO-effect
inference — same family, different (and safety-critical) grounding.

### Why this is the endgame lever
It collapses the whole idiomatic-read chain the two PRs could not. Traced from `bench/unboxed_read`:
`mutvec_raw` (field extract, non-trigger) + `vector_get_unboxed` (non-trigger leaf) ⟹
`mutvec_get_worker` is non-allocating ⟹ the caller's per-read root around `mutvec_get_worker(v,i)`
(a Sprout `IRCall`, the *dominant* remaining cost) disappears. #163 removed the worker-internal root;
the fixpoint removes the caller root — the rest of the ~idiomatic MutVec/Map win.

## 4. Keep it decoupled from IO enforcement

IO and alloc are different kinds of thing and must not be bundled:

| | IO effect | allocation |
|---|---|---|
| audience | user-facing (`!{IO}` in signatures) | internal optimization property |
| acquisition | declared (should be enforced) | inferred (must be sound) |
| failure mode of a wrong answer | missed/incorrect type error | **use-after-free** |

Enforcing IO (reconciling declared vs inferred, rejecting `pure_leak`) is a real semantics change with
a migration cost — the stdlib very likely harbors pure-annotated functions that call IO (the system
never checked). That is a **separate** soundness project. Coupling a memory-safety property to the
unenforced IO machinery would import fragility into the one path that must never be wrong. Full
Koka-style unification (one row carrying both labels) is a legitimate *long-term* direction, but it is
not "a little work" and not where to start.

## 5. Non-negotiable discipline for the inference

Inherit `op_triggers_gc`'s scars:

- **Conservative default = "assume it triggers GC."** Anything the fixpoint cannot *prove*
  non-allocating stays a trigger: unknown/external callee, a function still being resolved mid-fixpoint
  (recursion/SCC), closure/indirect application (`IRApplyClosure`), FFI. "Not proven allocating" must
  **never** collapse to "non-allocating."
- **Exhaustive, fail-loud** classification preserved (a new IR op / call shape is a compile error until
  classified — the P11-2e UAF scar).
- **`SPROUT_GC_STRESS=1` is the gate**, exactly as for #162/#163. Default-green tests are false.
- Compiler-source DoD: reseed + fixed point, full suite, stress, examples, canary.

## 6. Proposed smallest valuable step (needs approval before any code)

A per-function **may-trigger-GC summary** computed over the call graph from `op_triggers_gc`, consumed
in the rooting pass for `IRCall`/`IRCallUnboxed2` to Sprout functions. Scope guard: Sprout-defined
callees only (externs already handled by the allow-lists); conservative default everywhere else. Treat
as **B2-level delicate**: same stress oracle, same seed/DoD gates. This removes the
`mutvec_get_worker`-call root identified as the real remaining idiomatic cost, and it does **not**
require touching the IO effect system or the checker.

Open question for the design proper: does the summary live as a standalone rooting-pass analysis, or
as a genuine internal `alloc` effect inferred alongside (but grounded independently of) IO? The former
is smaller and lower-risk; the latter is the step toward the Koka model. Recommend starting standalone.

## 7. Reading list

- **Leijen, "Koka: Programming with Row-Polymorphic Effect Types"** (MSFP 2014) — inferring allocation
  as an effect (`alloc<h>`/`read<h>`/`write<h>`), HM-style effect-row inference.
  [arXiv:1406.2061](https://arxiv.org/abs/1406.2061) ·
  [MSR PDF](https://www.microsoft.com/en-us/research/wp-content/uploads/2016/02/koka-effects-2013.pdf)
  · [Koka book](https://koka-lang.github.io/koka/doc/book.html)
- **Talpin & Jouvelot, "The Type and Effect Discipline"** (LICS 1992) — foundational constraint-based
  type-and-effect inference.
- **Choi et al., "Escape Analysis for Java"** (OOPSLA 1999) — the closest practical interprocedural
  allocation analysis; connection-graph per-method summaries reused across call sites — the summary
  machinery a fixpoint would mirror.
  [PDF](https://faculty.cc.gatech.edu/~harrold/6340/cs6340_fall2009/Readings/choi99escape.pdf)
- **"Type, Ability, and Effect Systems"** (2025 survey) — modern breadth.
  [arXiv:2510.07582](https://arxiv.org/html/2510.07582)
