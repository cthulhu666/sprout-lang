# Scalar stores through `MutVec` wrappers lower inline, with no barrier — v0

**Status — step 1 landed 2026-11-01; step 2 next.** Non-normative: an optimisation, with no syntax, typing or
evaluation-order change, so `spec-v0.md` is untouched. It is
[gc-generational-v0.md](gc-generational-v0.md) §14.8 step 6, and it replaces the store half of
[accessor-inliner-design-2026-07-11.md](accessor-inliner-design-2026-07-11.md).

## 1. Problem

Under `SPROUT_GC_MINOR=1` every `vector_mutset` runs the write barrier, whatever it stores. A
`MutVec Int` that has survived a collection is then remembered on its first write and rescanned
whole at every minor, though no slot holds a pointer. astar keeps two 10,000-element `MutVec Int`:
its pause rises 1.6× and its GC time 1.5× against mode off
([bench/results-2026-11-01-gc-minor-step5.md](../bench/results-2026-11-01-gc-minor-step5.md)).
That blocks flipping the default.

Filtering on the stored value in C removes it (astar GC 14.5 → 10.3 ms, mode off 9.8) but costs
digit_recognizer's 10.6M `Double` stores 14–16% CPU. Card marking was measured too. A counter in
a scratch runtime summed, over every minor of one `SPROUT_GC_MINOR=1` run, the length of each
remembered vector against 128 times the number of distinct 128-slot cards written since the last
minor:

| workload | minors | remembered vectors | slots rescanned | with cards |
|---|---|---|---|---|
| astar | 98 | 196 | 1,960,000 | 937,344 (48%) |
| mutvec_int | 352 | 329 | 329,000,000 | 188,776,064 (57%) |

Cards halve the rescan but do not remove it, because the waste is the scalars, not the
granularity.

The compiler knows the stored value's type, but not where the store happens. User code calls
`mutvec_set(v: MutVec a, i: Int, val: a)` (`stdlib/mutable.sprout`). Inside it `val` is an `a`,
so its one `vector_mutset` has to keep the barrier. The concrete type exists only at the caller.

## 2. Goals and non-goals

**Goals**
1. A call to a vector-store wrapper whose stored value is typed `Int`, `Bool`, `Char` or `Double`
   lowers to the inline guarded store, with no call and no barrier.
2. The same holds for a direct `vector_mutset` on any such value.
   Previously only `Vector Double` took the inline store.
3. Trap messages and evaluation order are unchanged.
4. Switchable from one compiler build, for the A/B.

**Non-goals**
- **Reads.** `mutvec_at` would need the vector's element type, which a wrapper's own nodes do not
  carry (§4.3). Reads have no barrier, so step 6 does not need them. Follow-up.
- **Pointer stores.** They keep the call and the barrier.
- **`vector_push` and `ref_write`**, the other two barrier sites (gc-generational-v0.md §7). A
  scalar `mutvec_push` into an old vector is still remembered. There is no inline push, so
  skipping that barrier needs a barrier-free runtime entry, which is a new builtin. Follow-up, if
  an append-heavy workload shows the cost.
- **Generic code.** A store of an `a` inside the user's own generic function keeps the barrier.
  Reaching it needs monomorphisation, which `specialisation-v0.md` §2 rules out.
- **A general inliner.** Only the wrapper shape below is expanded.

## 3. Prior art

[accessor-inliner-design-2026-07-11.md](accessor-inliner-design-2026-07-11.md) §3 compares GHC's
`INLINE` pragma, which inlines selected functions and keeps one shared copy, with Rust's
monomorphisation, which makes a copy per type. Its sources are primary. This design sits on the
GHC side. It differs from that proposal in how a wrapper is selected: by its shape, not by a list
of names. `bce.sprout` already selects wrappers this way for loop versioning (`wrapper_of`), and
that code is the precedent used here.

## 4. Design

### 4.1 Which calls expand

`bce.sprout` already recognises two kinds of declaration:
- `is_projection` matches a pure one-parameter field read, such as `mutvec_raw`'s
  `match v with | MutVec raw -> raw`.
- `wrapper_of` matches an unconstrained function whose body is one `vector_get_direct` or
  `vector_mutset` call. Each argument must be an operand that `bce.operand_ok` accepts: a
  parameter, a small literal, a sum or product of those, or a projection of a parameter. Every
  parameter must be used exactly once, in order.

`mutvec_set` passes.

The new pass rewrites a call `f(args)` when three things hold:
- `f` is such a wrapper, and its shape is a store.
- No binder in scope shadows `f`. The pass tracks lambda parameters, `let`, match and do
  binders, as `bce.sprout` does with `ctx.locals` and `dce.sprout` with its `bound` set.
- After the arguments are substituted (`bce.apply_wrapper`), the stored operand's type passes
  `type_is_non_heap_scalar`.

The call becomes the direct `vector_mutset(...)` built from the substituted shape. Nothing else is
rewritten.

The stored operand is the caller's own expression only when the wrapper passes a bare parameter
there. `subst` keeps the wrapper's node type on a sum or a projection. So a generic wrapper that
stores `cell_val(c)` keeps its type `a`, and it does not expand even at a `MutVec Int` call site.
That is conservative, not unsound.

**Evaluation order.** Each argument still runs once, left to right. That holds because the wrapper
uses every parameter once, in order. The extra steps the expansion adds are projection calls, which
are pure field reads.

### 4.2 Lowering

In `ast_to_ir.try_vector_intrinsic`, a `VecSet` takes the inline store when its value argument
passes `type_is_non_heap_scalar`. This is the rule `bce.shape_converts` already applies in `$fast` clones.

`IRVecSetD` is already a plain `store i64`, so no IR op and no runtime entry is added. `VecGet`
keeps its `Vector Double` test: a read's soundness depends on the element type, and a false
positive there would leave a heap value unrooted (the comment above `vec_read_inlinable`).

**Why the value's type is enough.** A store creates an old-to-young edge only if the word stored is
a heap pointer. A value whose static type is the prelude's `Int`, `Bool`, `Char` or `Double` is
never a heap pointer. A module's own `type Int` canonicalises to `<module>.Int`, so it does not
pass the test.

This rests on the premise that static types are true. An `extern fn` that misstates its return
type breaks that premise, as it already breaks rooting. That is a known gap, filed in `BACKLOG.md`.

**GENCHECK false positive.** The verifier and the mark treat every vector slot as a possible
pointer. An `Int` slot whose value happens to equal a young object's address is a fake edge. Today
the barrier remembers that vector, and the minor follows the fake edge. After this change it does
not, so the verifier can report a missed barrier on a scalar vector. That is a false positive,
because the edge is not a pointer. The chance is a value matching a live young payload address
exactly. Removing the false positive needs an element kind in the vector header, which is out of
scope.

**Trap messages.** The C function fails with `vector_mutset: null vector` or
`vector_mutset: index out of bounds`, and `tests/bce_fixtures/expected/*` pins the second. The
inline guard (`vec_guard`) has two panic blocks carrying the C texts, in the C order. `panic` and
`sprout_fail` print the same way, so the trap output does not change. The `Vector Double` read
traps carry the C texts too.

### 4.3 Why no type substitution

An inlined wrapper body keeps the callee's own type variables: the expanded `mutvec_raw(g)` is
typed `Vector <tv>`, not `Vector Int`. A read would have to substitute types over the body
(`types.match_type_vars`, plus `infer.apply_subst_typed_expr`, which is not exported). A store
needs none of that when the wrapper passes the value as a bare parameter, as `mutvec_set` does.
The argument is then the caller's own expression, with the caller's concrete type.

### 4.4 Where it runs

The pass goes in `ir_pipeline.compile_program_streaming`, before `dce.elim_unreachable`. That
puts it at the one point both entry points share. Running before DCE means a wrapper with no calls
left gets dropped, and running before `bce.version_loops` means BCE sees the direct extern.

The pass is gated by a new `opt_config` switch, `Wsi` (wrapper store inline), on by default and
turned off with `SPROUT_OPT_OFF=wsi`. With the switch off, the
inline store still applies to direct calls (§4.2), since that rule needs no new pass.

The recognisers move out of `bce.sprout` into a small module that both passes import. They are
not copied.

## 5. Impact

- **Syntax, typing, diagnostics:** none. The trap texts are unchanged (§4.2).
- **Spec:** not specified there, and not needed: it is an optimisation.
- **Bootstrap:**
  - The compiler calls no `mutvec_*` (checked by grep of `stdlib/compiler/`), so its own IR does
    not change through this pass.
  - The §4.2 rule can change the IR of a direct scalar `vector_mutset` that the compiler bundles.
  - The pass's own source changes the compiler either way. So: reseed, and run golden IR.
- **Golden IR:** every example that stores scalars through `MutVec` changes, astar among them.
  Each diff should show the call replaced by the guard blocks and an inline store, and nothing
  else.
- **An existing test changes.** In `tests/stdlib/compiler/test_bce.spr`, the check
  "copy: $chk keeps the checked store" expects `@vector_mutset` in `copy$chk`, which is a
  `Vector Int` store. Under §4.2 that store is still checked, but inline. The check becomes a
  test for the guard (`icmp uge` and `vpanic_`). The "generic" check (`x: a` keeps the call)
  holds as it is.
- **Docs and backlog:**
  - a `Wsi` row in `opt-passes-v0.md` and the `opt_config` arms;
  - the `BACKLOG.md` entry "`LinVec Double` writes do not inline", which says
    `recognize_vector_op` keys on `Vector Double`;
  - gc-generational-v0.md §14.8 step 6, which points here;
  - `compiler-internals.md`.

## 6. Tests

1. **IR shape.** Each case is a fixture in `tests/b1_fixtures/`, checked by
   `scripts/b1_gate.sh` (`just b1-gate`). `fixture_b1_nondouble.spr` is a read, and it still
   holds.
   - `mutvec_set` on `MutVec Int`, `Bool`, `Char` and `Double` has no `call` to `vector_mutset`
     or to the wrapper.
   - On `MutVec String` the call remains.
   - In a generic `fn f(v: MutVec a, x: a)` the call remains.
   - A user wrapper of the same shape expands.
   - A wrapper that uses a parameter twice does not expand.
   - A local binder named like a wrapper (`let mutvec_set = …` or a lambda parameter) does not
     expand.
   - A generic wrapper whose stored operand is a projection does not expand at a `MutVec Int`
     call site.
2. **Behaviour.**
   - Writes through the expanded form read back correctly.
   - An out-of-range `mutvec_set` on a `MutVec Int` prints
     `runtime error: vector_mutset: index out of bounds`.
   - A null vector prints `vector_mutset: null vector`.
3. **GC.** `just gc-gen-check` passes; its `vector_mutset` site test stores a `String`, so it
   still exercises the barrier. `just test` passes under `SPROUT_GC_MINOR=1
   SPROUT_GC_GENCHECK=1`.
4. **Measurement.** `bench/gc_minor/ab.sh` at default settings, every workload, N = 6. Expected:
   - astar's GC time and pause back inside the spread;
   - mutvec_int's GC time about halved again (scratch filter: 1,300 → 575 ms);
   - digit_recognizer's CPU no worse, and probably better, since its `Double` stores stop being
     calls.

   Then the flip decision of §14.8 step 4.

## 7. Steps

1. ~~The §4.2 lowering rule with split trap blocks; the `test_bce.spr` check; tests 1 (direct
   calls) and 2.~~
2. Shared recognisers module; the expansion pass behind `Wsi`; the rest of tests 1 and 3;
   `compiler-internals.md`.
3. Test 4, and the results file.
