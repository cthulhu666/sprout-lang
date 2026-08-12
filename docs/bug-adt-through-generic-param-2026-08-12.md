# BUG: CPR worker ABI mismatch on a type-variable result

*(Filed as "an ADT value is corrupted when passed through a generic type parameter". No value is
corrupted — see §"Root cause" — but the original title is kept here for searchability.)*

**Status: FIXED 2026-08-12.** The call-site routers now decline a callee whose declared result has no
known representation; §"The fix as landed" records what changed.
`tests/conformance/run/adt_through_generic_param.{spr,out}` is out of `XFAIL` and passing.

**Severity:** high. Any polymorphic function that carries a user ADT is affected, which includes most
of the prelude's list/vec combinators. It is a wrong-behaviour bug, not a diagnostic gap — the compiler
accepts the program and the failure appears at runtime.

**Found by:** an agent working in the `uncharted-suns` game repo, which hit it through `list_fold`.

## The repro

Six lines, no prelude, no imports, no stdlib. `ident` does nothing but return its argument:

```sprout
type Box = Full Int | Empty

fn ident(x: a) -> a = x

fn direct() -> Int =
  match Full(7) with
  | Full v -> v
  | Empty -> 0            # => 7        correct

fn through_generic() -> Int =
  match ident(Full(7)) with
  | Full v -> v
  | Empty -> 0            # => runtime error: non-exhaustive match
```

Identical match, identical value. The only difference is one hop through a polymorphic parameter.

Build path — the same one `just test-conformance-run` uses:

```sh
build/compile_driver_bin_stage1 --emit-ir stdlib tests/conformance/run/adt_through_generic_param.spr > t.ll
clang t.ll runtime/*.c -framework Security -framework CoreFoundation -o t.bin
./t.bin        # prints 7, then: runtime error: non-exhaustive match
```

## What survives the hop and what does not

> **Corrected 2026-08-12 after the root-cause investigation below.** The original table was compiled
> from probes that mostly matched the value *indirectly*; two of its rows are wrong, and the true
> trigger is not "has a payload" but the shape of the **call site**. Superseded rows are struck.

| passed through a generic parameter, then matched **directly** on the call | result |
|---|---|
| `Int`, `Double` | survives — never routed |
| record (`(has: Bool, v: Int)`) | survives — a record match has no ctor-anchored arms, so it is never routed |
| ~~nullary constructor (`Nothing`, `Empty`) — survives~~ | **also broken** — reads two words off the end of a 0-field object |
| any ctor of arity ≤ 1 — `Just x`, `Full x`, `Empty`, single-ctor `Only Int` | **corrupted** |
| ctor of arity ≥ 2 (`Two Int Int`) | survives — the router only handles width 2, so it stays boxed |
| any of the above **let-bound before the match** | survives — the boxed path is correct |

The single-constructor case (`type One = Only Int`) does pin one half of the diagnosis: it fails too,
so this is **not** about match exhaustiveness or a missing default arm. But the value itself is *not*
wrong — see below. `direct()` and `through_generic()` build byte-identical heap objects.

## What has been ruled out

- **NOT a GC rooting bug.** `docs/debugging.md` §"GC rooting-bug oracle" names `non-exhaustive match`
  as the signature of an unrooted heap value. That oracle does not fire here: the failure is
  deterministic at the default GC threshold *and* under `SPROUT_GC_STRESS=1`. Please do not spend time
  down that path — it was the first thing tried.
- **NOT the do-bind.** `stdlib/prelude.sprout:132` (`list_fold_go`) uses `next <- step(init, x)`, and
  the documented `Maybe`/`Result` wrapper-stripping made that the obvious suspect. It is not: a
  hand-written generic fold using ordinary application and no effect row fails identically.
- **NOT a stdlib or prelude issue.** The repro is a bare `.spr` with a locally declared type, which
  gets no prelude at all.
- **NOT higher-order-specific.** No lambda or function-valued argument is required; `ident` is enough.

## Where it bites in practice

Anything generic that carries an ADT. `list_fold` is where it usually shows up first, because a
first-match / accumulate-a-`Maybe` fold is such a natural thing to write:

```sprout
list_fold(\ (acc, x) -> if pred(x) then Just(x) else acc, Nothing, xs)   # crashes on the first Just
```

~~Note the trap in that shape: **nullary constructors survive, payload-carrying ones do not.** A fold
over input that usually finds nothing returns `Nothing` happily and passes its tests, then dies the
first time it actually finds something.~~

**That advice is inverted and was removed 2026-08-12.** The `Nothing` case is *also* broken: the
mis-lowered worker reads two words off the end of the 0-field `Nothing` singleton. Measured against
the prelude (`tests/…` probe, `import stdlib.test`), a first-match fold over `[1, 2, 3]` looking for
`> 99` — the case that finds nothing at all — is the one that aborts first. The real trap is the
opposite: **a probe that let-binds the result before matching proves nothing**, because let-binding
is exactly what makes the bug disappear.

**Workaround for downstream code (verified).** Bind the result first; the boxed path is correct:

```sprout
let
  hit = list_fold(step, Nothing, xs)
in
match hit with          # ← safe: `hit` is a TVar scrutinee, not a call
| Just v -> …
| Nothing -> …
```

## Root cause (verified 2026-08-12, read off the emitted IR + source)

**No value is corrupted.** The heap object is intact end to end. This is a **caller/callee ABI
disagreement on the unboxed (CPR) return convention**: the call site and the worker it calls disagree
about what the two returned words *mean*.

Read the two `_worker` definitions side by side — same source shape, one generic, one monomorphic:

```llvm
define { i64, i64 } @ident_worker(i64 %x) {        ; fn ident(x: a) -> a       — WRONG
  %t$0 = load i64, ptr (gep %x, 0)                 ;   slot 0 := obj[0]  (the PAYLOAD)
  %t$1 = load i64, ptr (gep %x, 1)                 ;   slot 1 := obj[1]  (PAST THE OBJECT)
  ret { %t$0, %t$1 }
}

define { i64, i64 } @ident_mono_worker(i64 %x) {   ; fn ident_mono(x: Box) -> Box — CORRECT
  %t$0 = call i64 @sprout_tag(i64 %x)              ;   slot 0 := the TAG (from the header)
  ; … per-ctor test chain; on the Full arm:
  %t$3 = call i64 @sprout_field(i64 %x, i64 0)     ;   slot 1 := field 0
  ret { %t$0, %t$3 }
}
```

The call site always assumes the second (ADT) convention — it compares slot 0 against ctor tags and
binds slot 1 as the payload. So for the generic callee it reads the **payload as the tag** and a word
**past the end of the allocation** as the payload.

### The defect, in one line

`ast_to_ir.sprout:7724` — `translate_tail_catchall` treats an empty `result_adt_ctors` list as *proof*
that the worker returns a tuple:

```sprout
match result_adt_ctors with
| Nil ->
    # No ADT ctors ⇒ a TUPLE worker: ADT workers are guaranteed a non-empty ctor list by the
    # routing invariant (wrap/Bool excluded upstream) …
    translate_tail_tuple_repack(…)
```

That inference does not hold for a **polymorphic** function. The list comes from
`worker_source_for` → `adt_ctors_of_type(typed_expr_type(fbody), adt_index)` → `type_head_name`
(`ast_to_ir.sprout:7119`), which returns `Nothing` for anything that is not a `TConst`/`TApp`. A
result type of `a` is a type variable, so the head lookup misses, the ctor list comes back `Nil`, and
the ADT worker silently takes the tuple path — whose convention is "all fields", i.e. `{obj[0], obj[1]}`.

Note the comment three lines above it, which still describes the guard this branch replaced:

> "Fail loudly at compile time instead; routing guarantees this is a width-2 ADT that SHOULD resolve
> (wrap/Bool are excluded upstream), so an empty list here is an internal invariant break."

The loud failure that would have caught this was repurposed into Tuple-CPR's silent success path.

### Why the routing invariant is not actually enforced

The Tier-2 router (`unboxed_maybe_match_target`, `ast_to_ir.sprout:3375`) decides to call
`@<name>_worker` from **the call site alone**: the callee is top-level, is not shadowed, and the
match arms are all simple width-2 ctor-anchored patterns (`tier2_worker_shape`). It never consults the
callee's *declared* result type. `collect_worker_callees` scans on the same shape core — so the two
sides agree on *whether* to workerize (the C1-divergence guard holds) and still disagree on the
*convention*, which nothing checks.

This also explains every row of the corrected table: arity ≥ 2 and records never satisfy the
width-2 ctor-anchored arm shape, so they are never routed and stay boxed; a let-bound scrutinee is a
`TVar`, not a `TCall`, so the router declines.

### Blast radius

Exactly the shape `match <direct call to a top-level fn whose declared result is a type variable> with
<ctor-anchored arms of arity ≤ 1>`, plus its `translate_tail_chain` sibling (a workerized fn whose
*tail* is a call to such a fn — `ast_to_ir.sprout:7433`). Every other use of the value is boxed and
correct: field reads, `Eq`, passing it on. Confirmed live in the prelude — `list_fold` returns `b`, so
`match list_fold(…) with | Just … | Nothing …` routes to a mis-lowered `@list_fold_worker`.

### It is not only a crash — it silently returns wrong answers, and it reads out of bounds

With `type Box = Full Int | Empty` (tags `Full`=0, `Empty`=1), one function returns three different
kinds of wrong depending only on the *payload value*, because the payload is being read as the tag:

| `match ident(Full(v)) with \| Full w -> w \| Empty -> -1` | slot 0 = `v` is read as the tag | observed |
|---|---|---|
| `v = 1` | `1` = tag `Empty` | returns **`-1`** — silently wrong, no crash |
| `v = 7` | matches no tag | `runtime error: non-exhaustive match` |
| `ident(Empty)` (0-field) | two words read past the object | returns **`0`** instead of `999` — silently wrong |

`sprout_obj_alloc_arity` (`runtime/sprout_runtime.c:1418`) returns the arity unchanged in normal
builds, so a 1-field object owns exactly one word: the `obj[1]` load is a genuine read past the
allocation, not in-bounds slack. That makes this a memory-safety defect as well as a wrong-answer one.

### Suggested fix direction (not implemented — needs approval per `AGENTS.md` §Collaboration 5)

1. **Gate workerization on a concrete result type.** Both `collect_worker_callees` and the Tier-2
   router must decline a callee whose *declared* result type does not resolve to a concrete, indexed
   ADT. The router has no access to `decls` today, so the natural shape is a precomputed
   eligible-callee set threaded to both — the same shared-oracle pattern the file already uses to keep
   collection ⊇ routing.
2. **Restore the loud failure.** `translate_tail_catchall`'s `Nil` arm should take the tuple path only
   when the body's type *is* a tuple (`scalar_tuple_width` says so) and hard-error otherwise, so a
   future convention gap is a compile error rather than an out-of-bounds read.
3. **Record why a "correct generic worker" is impossible.** Without monomorphization a single
   `@ident_worker` would have to serve both an ADT instantiation (`{tag, field0}`) and a tuple one
   (`{f0, f1}`); the conventions genuinely conflict, so declining to workerize is the only sound fix at
   this representation. Worth a line in `docs/scalar-replacement-v0.md` so nobody attempts that branch.

### Performance price of declining (measured 2026-08-12)

**Scope: zero currently-working call sites.** A census of `bootstrap/compile_driver.ll` (the compiler
+ prelude bundle, ~345k lines, the largest Sprout program there is) finds **279 workers, none of them
mis-lowered**: 258 use the `sprout_tag` ADT convention and the rest return statically-known tags from
`translate_tail_unary_ctor`. No `@list_fold_worker` / `@list_map_worker` / `@filter_worker` is
generated at all. The compiler does not miscompile itself, and the fix de-optimizes nothing in it.
The gate only ever fires on call sites that are **broken today**.

**Per-site price: one heap allocation per call.** Measured on the monomorphic equivalent of the shape
a generic callee loses (`fn step(n: Int) -> MB`, 20M iterations, `clang -O2` — the level `justfile`
uses), unboxed vs. the let-bound form, which is exactly the IR the fix produces:

| | allocations | GC cycles | wall |
|---|---|---|---|
| worker (CPR, today) | **0** | 1 | 0.01 s |
| boxed (post-fix) | **20 000 000** | 4 883 | 0.33 s |

≈ **16 ns per call** for the box + its sweep. The 33× ratio is an artifact of the microbenchmark: with
the allocation gone, LLVM folds the whole loop to arithmetic, so the unboxed column is measuring
almost nothing. Real code amortizes this against whatever else the loop does — treat 16 ns/call as the
number and the ratio as an upper bound.

Note that this is *not* a regression against correct behaviour: the only correct way to write the
affected shape today is the let-binding workaround, which already generates the boxed IR. And per
`AGENTS.md` §Builtin vs Stdlib #6 a microbenchmark is not a measured bottleneck — restoring CPR for
generic callees (convention-tagged worker symbols + a runtime unbox helper) needs a real workload
showing it matters.

## The fix as landed (2026-08-12)

Every router gates on `cpr_result_known`, so a callee with no known result representation is never
routed and its worker is never emitted. Four sites had to agree — the fourth is easy to miss:

| site | `ast_to_ir.sprout` | why it matters |
|---|---|---|
| Tier-2 ADT router | `unboxed_maybe_match_target` | the reported shape |
| tuple router | `unboxed_tuple_match_target` | a generic callee instantiated at a tuple |
| do-bind router | `do_bind_unbox_target` | `x <- f(…)`; this one had **no** shape check at all, only `set_member(name, top_level)` |
| emission scan | `collect_worker_callees` | `translate_tail_chain` chains worker-to-worker through this set, so an un-gated entry here reintroduces the same mismatch on the chain path |

The flag rides on the name-keyed dict that was already threaded everywhere: `fn_arities` widens from
`Dict Int` to `Dict FnInfo` (`arity` + `cpr_result`), computed once in `build_fn_arities` from the
declared result type. Widening the value type rather than adding a parameter kept ~200 call sites
untouched. The mutual-TCO pre-pass keeps its own plain `Dict Int` (`pb_arity_of`) — it carries no CPR
facts and should not pretend to.

`translate_tail_catchall`'s `Nil` arm no longer infers "no ctor list ⇒ tuple": it takes the tuple path
only when `scalar_tuple_width` confirms a tuple and hard-errors otherwise, so a future router/emitter
disagreement is a compile error rather than an out-of-bounds load.

Cost, measured: generic combinators are boxed again — one allocation per call, ~16 ns at `-O2`. Zero
call sites that currently work are affected (a census of `bootstrap/compile_driver.ll` found 279
workers, none mis-lowered). Restoring CPR for generics is BACKLOG `P1`.

## Definition of done

1. `tests/conformance/run/adt_through_generic_param` passes and is **removed from `XFAIL`**. The gate
   is self-healing: it goes red with `UNEXPECTED PASS` once fixed, so removing the line is forced.
2. Per `AGENTS.md` §Code and Testing #2, add coverage for the neighbours the repro implies and the
   fixture does not cover: a single-constructor ADT with a payload, a **nullary** ctor returned through
   a generic and matched directly (silently wrong today, and the row the original report got backwards),
   a payload value that collides with a valid tag (the silent-wrong-answer case), and an ADT
   round-tripped through `list_fold` / `list_map` / `filter`. An IR-shape test asserting that a
   tyvar-result fn emits **no** `@<name>_worker` would pin the fix at the right layer.
3. ~~Worth checking whether the same corruption is observable *without* a match.~~ **Answered:** the
   value is never damaged, so no non-match use is affected; but silent wrong answers *are* reachable
   through the match itself whenever the payload happens to equal a valid ctor tag (table above).
