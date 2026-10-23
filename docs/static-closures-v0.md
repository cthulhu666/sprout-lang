# Static closures for functions with no captures (v0)

Status: implemented, 2026-10-23. Not normative: modes, layout and allocation are below the spec.

## 1. Problem statement

Every closure value is a heap block from `sprout_alloc_closure`, even one with nothing to put in
it. A lambda that captures nothing (`\f -> f.atk`), a top-level function used as a value (an eta
wrapper) and a class method passed as a dictionary argument (`Eq Int`'s `eq`, `Foldable List`'s
`fold_values`, also an eta wrapper) all allocate
an 8-byte env per evaluation, then get a GC root while live.

They are most of the closures. In the committed seed, 855 of the 1,213 `sprout_alloc_closure`
sites have no captures; 34 of the 72 golden IR snapshots contain one. In `bench/gc_roots`,
`turn_score` builds 11 closures per call and 8 of them capture nothing.

This came out of a BACKLOG entry, *Non-escaping combinator closures allocate*, which proposed
stack-allocating closures at `local` slots. Measured first (§7), that lever is worth ~0 on the
bench the entry was filed against; the capture-free case is worth −6.6%.

## 2. Goals and non-goals

Goals:
- A closure with no captures is a static constant: no allocation, no GC root, no GC trigger.
- No change to semantics, the closure ABI, the runtime or the spec.

Non-goals:
- Stack-allocating capturing closures at `local` slots (§7: no measured win; it needs a scan
  root, has a self-tail-call hazard, and leans on the trusted `__task_fork` extern).
- Sharing one eta wrapper per referenced function. Each reference site mints its own wrapper
  today; that is a code-size question, filed separately (§9).
- Dropping or inlining the apply-site arity check (§7: ~2% of wall time at most).

## 3. Prior art

Every source below is compiler source or the language reference.

| language | capture-free function value | source |
|---|---|---|
| GHC | top-level closures "should have no free variables" and are allocated "in static space"; a closed local lambda gets there by let-floating (`Note [Floating to the top]`), an optimisation, not a rule. The GC never copies a non-heap closure (`evacuate`, `!HEAP_ALLOCED_GC(q)`). | `compiler/GHC/StgToCmm/Bind.hs` `cgTopRhsClosure`; `rts/sm/Evac.c` |
| OCaml (closure, non-flambda) | a function set whose env is unused becomes `Uclosure(clos, [])`, emitted as a `Const_closure` symbol in static data. | `middle_end/closure/closure.ml` `close_functions`; `asmcomp/cmmgen.ml` `transl_expr` |
| Go | a function literal that captures nothing is not a closure (`IsClosure`: `len(ClosureVars) > 0`) and is "converted to global". | `cmd/compile/internal/ir/func.go`; `walk/closure.go` `walkClosure` |
| Rust | a non-capturing closure coerces to a function pointer. | Reference, *Closure types*, `r-type.closure.non-capturing` |

They agree on the rule: no captures, no per-evaluation allocation. Sprout's version is the OCaml
and Go one, decided at lowering by the capture list, not by an optimisation pass. In Sprout a
static closure keeps its header, because the apply-site arity check reads it (§4).

## 4. Design

**Representation.** `IRMkClosure r code_fn arity Nil` keeps its op. An empty capture list
is the whole condition, read through one helper, `sprout_ir.closure_is_static(caps)`, used by
`ir_lowering` and `ir_rooting`. A separate `IRStaticClosure` op was the other choice: it says the
fact in the op, but every exhaustive match over `IROp` would gain an arm for something the
operands already state, and the two ops would lower identically except at one line.

**Lowering** (`ir_lowering`). The string-literal scan, `walk_ops_for_strs`, also emits each
static closure's global, once per program: a set of emitted code functions is threaded through
both the batch and the streaming path, since a global is module-wide. The global is

```llvm
@__sprout_sclo.<code_fn> = private unnamed_addr constant { i64, ptr } { i64 <hdr>, ptr @<code_fn> }
```

and each site becomes one instruction:

```llvm
%r = ptrtoint ptr getelementptr inbounds (i8, ptr @__sprout_sclo.<code_fn>, i64 8) to i64
```

The payload is the code pointer at offset 8, exactly where a heap env puts it, so
`IRApplyClosure` and `sprout_closure_arity_check` work unchanged. `<hdr>` is the CLOSURE header:
kind 2, `n_caps` 0, the arity in `aux` above bit 32. The poison thunk's `SPROUT_CLOSURE_ARITY_ANY`
sets bit 63, and `Int` traps on overflow, so the header is computed as its signed 64-bit pattern.
This is the third copy of the header split (runtime, `IRMkClosure`, here); it gets the same SYNC
comment and one test that reads a static closure's arity back through the runtime check.

**Rooting** (`ir_rooting`). A static `IRMkClosure` is not a GC trigger and does not produce a
heap value. So it is never rooted, and values live across it no longer need roots on its account.
A phi of a static and a heap closure is heap by the existing rule (`any_pair_in_heap`).

**Runtime: unchanged.** The GC finds objects with `sprout_heap_lookup`, which answers NULL
outside the heap regions, so a static closure reached from a root or a heap field is skipped. It
is never marked, swept or written. Static string literals rely on the same rule today
(`emit_str_global`). A callee that roots a closure parameter roots a static one harmlessly.

**Who produces one.** `translate_lambda` with no captures, the eta wrapper for a top-level
function used as a value, the intrinsic eta wrapper, and the unresolved-dictionary poison thunk.
None of them is mutated after it is built.

## 5. Gate impact: `rooting-cost-gate`'s closure arm

The arm counts closures allocated per element and relies on each `list_member` call building its
`Eq String` dictionary as a fresh closure. Its own comment says it "goes blind if dictionaries stop
being built per call". This change does that. The arm still passes, at ~30 closures per element
against a floor of 18, but what it counts is now the capturing closures left on that path, not the
comparisons.

Options:
- **(a) Count applications instead of allocations.** Add a `closure_apply` counter to the
  `SPROUT_DEBUG_ALLOC` report, bumped in `sprout_closure_arity_check` behind
  `g_debug_alloc_enabled`, and point the arm at it. It counts the comparisons themselves rather than
  a side effect of them, so a dictionary that stops allocating does not blind it again. Cost: one
  predictable branch on every closure apply. It is a runtime change but adds no builtin.
- **(b) Retire the arm.** The 2026-10-04 regression it caught (closures 1212 → 2382 per element)
  read flat on objects and map, so nothing else would catch it again.
- **(c) Re-floor on what still allocates.** Nothing on that path still does.

Taken: (a). Measured with it (stage 2): 211 → 212 applications per element at 2× block length,
floor 105, growth budget unchanged at 125/100.

## 6. Tests

Behaviour does not change, so only the IR test can be red first; the rest are guards.
- `tests/stdlib/compiler/test_static_closure_lowering.spr` (IR, red on master: 20 of 23): a
  capture-free lambda, an eta-wrapped top-level function and a method closure emit
  `@__sprout_sclo.` and no `sprout_alloc_closure`, through the streaming and the batch path, and
  take fewer root pushes than a capturing twin. A capturing lambda still allocates. One global per
  code function; the arity-ANY header is the signed pattern.
- `tests/stdlib/test_static_closures.spr` (behaviour): a static closure stored in a list and in a
  record, read back after forced collections; run as a task; mixed with capturing closures in one
  list; returned through a phi of a static and a heap closure.
- Arity: `tests/closure_arity_smoke/over_apply_static.spr`, run by `just closure-arity-smoke`,
  over-applies a static closure and fails with the runtime check's message, which proves the
  header's arity reads back.
- The poison thunk (arity ANY) keeps `tests/stdlib/test_unresolved_dict_poison.spr` green.
- Golden IR: 34 snapshots change. Read them normalised before regenerating.

## 7. Measurements

Apple M3 Pro, `clang -O2`, interleaved runs, medians. The table below was measured before the
change, on IR rewritten by hand to the shape of §4, with the root pushes for static closures left
in. The implementation, which also drops them, measured 1.143 s → 1.041 s (−8.9%) on the same bench,
old compiler against new, identical checksums.

`bench/gc_roots` (6 runs each, identical checksums):

| variant | time | vs base |
|---|---:|---:|
| base | 1.164 s | — |
| A: static capture-free closures | 1.087 s | −6.6% |
| A + stack envs for the 6 capturing closures at `local` slots | 1.086 s | ~0 beyond A |
| A + stack envs, arity check deleted (unsafe, a bound only) | 1.062 s | −2% beyond that |

Stack envs removed the allocator's work from the profile (`slot_bytes` 63 → 22 samples,
`sprout_alloc_closure` 13 → 0) but not from the wall time. The arity check shows ~9% of
top-of-stack samples, yet deleting it saves ~2%.

Compiler, `--emit-ir stdlib stdlib/compiler/infer.sprout`, with the seed rewritten the same way (855
sites): 1.40 s → 1.355 s, −3%. The output is byte-identical (same md5).

## 8. Spec and docs

- No spec change: allocation is not observable.
- `docs/compiler-internals.md` §Closure layout: static closures, the header's third copy, the
  `sprout_heap_lookup` rule they rely on.
- `docs/gates.md` and `scripts/rooting_cost_gate.sh`: the closure arm, per §5.
- `docs/local-closures-v0.md` §11 *Not a speed feature yet*: the stack-env measurement.
- `BACKLOG.md`: that entry becomes *Capturing closures at `local` slots allocate*, with §7's
  numbers, at P3.

## 9. Follow-ups

Both filed in `BACKLOG.md`:
- *One eta wrapper per reference site*: one per `(function, arity)` would do.
- *Capturing closures at `local` slots allocate*: only worth revisiting if an inlining change lets
  LLVM see through the env. The out-of-line arity check blocks that today.
