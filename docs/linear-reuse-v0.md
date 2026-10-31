# In-place reuse of a consumed linear cell (v0)

**Status:** implemented, 2026-10-23 (struck items in §6 and §7 are done).
No normative spec change: reuse is not observable.

## 1. Problem

A linear type wrapping a `LinVec` allocates a new box on every write:

```
type linear Grid = Grid Int (LinVec Bool)

fn grid_set(i: Int, x: Bool, g: Grid) -> Grid =
  match g with
  | Grid n v -> Grid(n, linvec.set(i, x, v))
```

Measured on master (stage-1, `-O2`), a sieve doing one `grid_set` per strike against the same
sieve on a bare `LinVec`:

| sieve | n | time | objects | GC cycles |
|---|---|---|---|---|
| bare `LinVec` | 500,000 | 13 ms | 2 | 1 |
| `Grid` wrapper | 500,000 | 235 ms | 1,033,918 | 253 |
| bare `LinVec` | 5,000,000 | 45 ms | 2 | 1 |
| `Grid` wrapper | 5,000,000 | 20,197 ms | 11,192,871 | 2,736 |

10× the size costs 86× the time. A collection runs about every 4,000 wrapper allocations, and
each one rescans the live vector, so cycles and work per cycle both grow with n. The rescan half
is the P2 entry *GC trigger is object-count-blind*, which reuse does not fix in general (any
allocation beside a large live array pays it). Reuse removes it for this shape by removing the
allocation.

`g` is owned and linear: nothing else holds the cell, and after the match nothing can name it.
The new `Grid` has the same shape. Its fields can be written into the old cell.

### 1.1 Rebuilding a tree

A binary search tree insert rebuilds the path from the root to the new key. In a linear tree
the old path has no other reference, so each rebuilt node can be written into the cell it
replaces. [`examples/tree_set.sprout`](../examples/tree_set.sprout) keeps a class's distinct
scores this way.

The same insert, 1,000,000 inserts of LCG keys mod 1,000,000, then a sum (`-O2`, best of 5):

| tree | compiler | time | objects | GC cycles |
|---|---|---|---|---|
| linear | master | 1,288 ms | 24,984,952 | 85 |
| linear | reuse | 549 ms | 632,392 | 6 |
| not linear | reuse | 1,285 ms | 24,984,952 | 85 |

The 632,392 objects left are one new node per distinct key. A non-linear tree is shared, so it
must copy the path; it costs the same on both compilers.

A step that changes constructor reuses its cell too: a state machine kept as one value
allocates nothing per step. `tests/linear_reuse_smoke/csv.spr` tests this.

## 2. Goals and non-goals

Goals:
- An owned linear parameter, matched and rebuilt on one path with a constructor of the same type
  and the same field count (§4.2 defines the count), reuses its cell: no allocation, no GC
  trigger. A rebuilt tree path (§1.1) and a state machine then allocate nothing per step.
- Decided at compile time from the types. No runtime check, no slow path.
- Generalises `LinVec`'s rule ("a linear value is updated in place") to every boxed linear type.

Non-goals (v0):
- A constructor of another type (Lean's relaxed pass). The lowering would be the same, but no
  measured program needs it.
- Constructors with different field counts. The arity byte is the collector's only record of how
  many words to scan (`sprout_heap_child_count_payload`), so a smaller result would need it
  rewritten and a larger one would overrun the slot.
- Scrutinees other than a parameter: a `let`-bound or pattern-bound owned linear value, a call
  result.
- A `with` update of an owned linear record (BACKLOG P3 *In-place reuse for a linear record's
  `with` update*). It reaches little today: `with` cannot read its own base, since
  `p with (x = p.x + 1)` uses `p` twice, and cannot replace a linear field (spec §5.8).
- Non-linear values. Uniqueness is unknown without a runtime count, which Sprout does not keep.
- An explicit guarantee such as Koka's `fip`. A `linear-reuse` smoke counts allocations instead.
- A new write-barrier design. A reused cell is a barrier site for the generational collector
  (`docs/gc-generational-v0.md` §14.4), which this design only feeds: the op carries a flag
  and lowering calls `sprout_gc_write_barrier` (§4.3). It does not decide how the barrier works.

## 3. Prior art

Sources were read in full: the papers, the language references and the compiler source.

| Language | How uniqueness is known | Reuse rule | Constructor match | Source |
|---|---|---|---|---|
| Koka (Perceus) | at run time, by checking the reference count | a matched cell that is dead in a branch yields a reuse token; a same-size allocation in that branch consumes it; a shared cell gives a NULL token, so it allocates | same byte size, any constructor | Perceus TR v4 §2.4–2.6; `ParcReuse.hs` |
| Koka (FP², `fip`) | syntactic linearity of the function body, checked statically | a `match!` on an owned variable gives a size-k credit; under `fip` a constructor may only be built from a credit | same size, any constructor | MSR-TR-2023-19 §2.2 |
| Lean 4 | at run time, by checking the reference count | `reset` at the first point the matched value is dead, then `reuse` at a later constructor; a shared value makes `reuse` allocate | same size; same type first, other types only in a relaxed second pass | arXiv 1908.05647 §5; `ResetReuse.lean` |
| Clean | uniqueness types, statically | a compiler option reuses a unique argument's space for a result of the same type, "under certain conditions" | same type (conditions not specified) | Clean Language Report 2.2 §9 |
| Futhark | uniqueness types, statically | `a with [i] = v` updates in place | arrays only | language reference §3.11 |
| Rust | ownership, statically | no guarantee that a consumed `Box` is reused; `Box::map`, nightly only, reuses "if possible" | — | `std::boxed::Box` |
| Linear Haskell | linearity is a contract on the callee, not a guarantee of no aliasing | none: in-place updates are library primitives only | — | arXiv 1710.09756 §2.2, §6.3 |
| Idris 2 | quantitative types; reuse from linearity is listed as an open question | the RefC backend reuses a cell when a run-time `isUnique` check passes | same constructor | `RefC.idr` |

Where they diverge:
- The reference-counted systems (Koka, Lean, Idris RefC) check at run time and keep a slow path.
- Clean is the one static-uniqueness system documented to reuse user constructor cells, and it
  requires the same type.
- Sprout already proves uniqueness statically for linear types, so it needs neither the check
  nor the slow path. v0 takes Lean's first pass and Clean's rule: the same type. Same
  constructor only (Idris RefC) would leave every constructor change allocating, and the
  allocation-free form would be one constructor with an `Int` state field, against guidelines §3.

Pitfalls the sources name, and how v0 meets them:
- **Never reuse a borrowed value** (Beans §5.2; FP²'s `bmatch` gives no credit). v0 takes a token
  only from an owned parameter.
- **At most one reuse per token per path.** Lean tracks `alreadyFound` for the same reason. See
  §4.2.
- **Holding a cell for reuse retains it.** Perceus measured 11% more memory on one benchmark.
  Here the old fields stay reachable until the rebuild, which they mostly are anyway.

## 4. Design

### 4.1 Where the facts come from

`ast_to_ir` sees no linearity today. `test_borrow_erasure.spr` pins that "lowering never READS
the mode", from the M5 feasibility decision (`linear-ir-m5-feasibility-2026-08-07.md`). Reuse
needs exactly two facts:
1. The type is linear, as `linear_check` decides it: `ast.ctors_multiplicity(mult, ctors)`, which
   also counts a constructor bound (`where linear a`) that leaves the declared multiplicity
   `Unrestricted`. `TPassThrough(TypeDecl …)` already reaches `build_ctor_table`, which drops the
   multiplicity. `CtorInfo` gains `linear: Bool` and the name of its type, which the same-type
   test in §4.2 needs.
2. The parameter is owned. `translate_user_fn` has the `ast.Param` modes in hand and passes
   only the names. It passes the SSA names of its owned parameters (`TxEnv.owned`), held
   beside the token. The set belongs to one function: a lambda body gets an empty one (§4.2).

This relaxes the erasure invariant to: **lowering reads the mode only to pick a reuse token.** A
`borrowing` parameter of a linear ADT that is matched and rebuilt now emits different IR from an
owned one. That is the point. The test changes to say so, and keeps byte-identity for every
other case: `consuming` vs default, `once`, `local`, and `borrowing` without a rebuild.

### 4.2 Pairing a token with a constructor

A token is minted for one arm of a `match` when all of these hold:
- the scrutinee's SSA name is an owned parameter's (`TxEnv.owned`). The test is on the SSA
  name, not the source name: a name shadowed by a pattern binding resolves through `captures`
  first (`ast_to_ir` `TVar`), so in `match h with | c -> match c with …` the inner scrutinee is
  `%p$h`, and it mints only if `h` is owned. An alias that resolves to an owned parameter's SSA
  (`| c -> match c …`, `let x = g in match x …`) mints too: `linear_check` has consumed the
  parameter into the alias, so the alias is the only live name;
- the arm's pattern is a `ConstructorPattern` of a heap constructor. The tag is tested (or the
  constructor is the only one), so the cell's constructor is known. A `_` arm, a `VarPattern` arm
  (`| other -> other`) and a `let..else` fallback mint none: for those the cell's constructor,
  and so its arity, is unknown. A `wrap` constructor (`CtorInfo.tag == -1`) mints none: its
  scrutinee is the inner value, not a cell.

`let Grid n v = g in …` is covered: `ast.let_bind_match` turns it into a `match` with one
`ConstructorPattern` arm before typing.

**Field count** is the physical arity, `CtorInfo.arity`: the source fields plus one hidden word
per witness slot of a constrained existential (`register_ctor_one`). The `IRMakeCtor`'s arg list
has the same count, since dictionary expansion emits the witnesses as args. The source count is
wrong: it pairs `Plain Int Int` with `Poly a (a -> Int) where Show a` and writes three words into
a two-word cell.

The token is the scrutinee's SSA name, its type, the arm's constructor and its field count. It
lives in one `Ref` in the translation environment, so there is one token at a time:
- The first `IRMakeCtor` on the arm's path whose constructor has the token's type and field count
  (at least 1; nullary constructors are interned) takes it and becomes
  `IRReuseCtor dst token ctor tag args barrier`.
- A `match` nested in an arm mints its own token in place of the outer one. The outer token comes
  back at the nested match's join, under the join rule below. Each cell is still reused at most
  once.
- An `if`, a `match` or any other branching construct saves the token's state and translates each
  branch from that state. After the join the token is **taken if any branch took it**, so a rebuild
  after the join allocates. Restoring the saved state instead would let a branch and the code
  after it reuse one cell twice.
- `translate_lambda` gives the body a fresh token `Ref` and an empty owned set
  (`closure_tx_env`). A constructor inside a closure, which may run 0..n times, never takes an
  outer token. The owned set matters as much: a lambda parameter `g` resolves to `%p$g`, the
  outer parameter's SSA name, so `\(borrowing g: Grid) -> match g with …` inside
  `fn f(g: Grid)` would mint a token on a borrowed cell under the outer set. A lambda's own
  owned parameters get no reuse in v0. CPR workers and the global initialiser also get an empty
  set.
- An immediately-applied lambda (`let`, `where`, `x |> (\p -> …)`, `(\p -> …)(a)`) is inlined by
  `translate_call`: its parameters are bound to the argument SSAs and the body is translated in
  the enclosing env, without `closure_tx_env`. The body runs once, on the path that reached it,
  so it shares the token stack and an outer token may be taken inside it. A non-owned (`borrowing`,
  `once`, `local`) parameter is bound to the lender's SSA, which can be an owned parameter's, so
  `inline_tx_env` takes the arguments of such parameters out of the owned set for the body.
  Emptying the set would also lose `let j = 1 in match g with …`, which is sound.

The failure modes are asymmetric. A branching construct that misses its save treats its branches
as one sequence: the second branch finds the token taken and allocates, which is conservative. A
missed reset in `translate_lambda`, or a borrowed alias left in the owned set by the inlined
lambda path, would be unsound. Each is one call site, and each gets a test.

### 4.3 The op

`IRReuseCtor <dst> <token> <from-tag> <ctor> <tag> <args> <barrier>`, where `from-tag` is the
matched constructor's tag, so the lowering knows whether the header changes, and `barrier` says
whether a field may hold a heap value:
- **Lowering.** `dst` is the token's value. Each field is stored through the same GEP and
  `store` that `lower_stores` emits for `IRMakeCtor`. For the token's own constructor the header
  is untouched. For another constructor the header word is loaded, its low 14 bits (kind, colour,
  age, REMEMBERED) kept, and the new aux `(tag << 8) | arity` ORed in at bit 14: the encoding of
  `sprout_obj_write_tag`, so both get a `SYNC` comment. Between collections the colour bit means
  *old* (`docs/gc-generational-v0.md` §14.4), and bit 13 (REMEMBERED) says the cell is already in
  the remembered set. The mask keeps both, so a reused cell keeps its generation and is not
  recorded twice. The barrier call follows the stores and the header rewrite.
- **Write barrier.** A reused cell can be old while the new field values are young. When
  `barrier` is set, lowering ends the op with `call void @sprout_gc_write_barrier(ptr <cell>)`.
  It is a plain call, not an inline header test, because an op that opens a basic block would
  break downstream phis (see `IRApplyClosure`); the test runs inside the runtime function.
  `ast_to_ir` clears the flag only when every field is an `Int`, `Bool`, `Char` or `Double`
  (`CtorInfo.scalar_only`). A type-variable field or a dictionary witness slot counts as heap.
  The call does not allocate, so it is not a GC trigger.
- **Rooting.** Not a GC trigger. Its uses are the token and the args. The token is therefore live
  from the match to the rebuild and is rooted across every trigger in between, so the collector
  cannot sweep the cell and write a freelist link into word 0. That costs a root push and pop
  where a call that may collect sits between the match and the rebuild, where today the
  parameter dies after the field loads. The allocation it replaces pushes and pops roots for its
  args anyway, and §1.1's times include the cost.
- **Ordering.** The args are SSA values computed before the op, and every field load of the old
  cell sits in the arm preamble, so no read can see a field already overwritten. A collection
  between the match and the op sees the old header with the old fields. The op is not a trigger,
  so none sees the new header with old fields.
- **Why the GC accepts another constructor.** `sprout_gc_drain_marks` looks up every field word
  with `sprout_heap_lookup`, and the arity byte, which sizes the scan, is unchanged.
- **Other passes.** Every exhaustive match over `IROp` gains an arm. CPR, SRA and TCO rewrites
  treat it as `IRMakeCtor`, except that it does not allocate.

### 4.4 Premises (Design Change Process: a check relaxed)

A cell is reused only if nothing else can reach it. Each premise below is today's behaviour of
`linear_check`, and each gets a test:
1. An owned linear parameter has no other reference at entry: a borrowed value cannot be passed
   to an owned parameter, a linear value cannot be used twice, and a generic parameter cannot be
   instantiated at a linear type without a `where linear` bound (`linearity-bound-v0.md`).
   Covered by existing `type_error` conformance cases, cited in the test. The premise is about
   the SSA name, and an inlined lambda's `borrowing` parameter is a second name for the lender's
   SSA: `inline_tx_env` removes it (§4.2), with IR and behaviour tests.
2. After the match, the arm cannot name the parameter again (use-once). Existing.
3. No closure captured it before the match: capture of a linear binding is rejected (M4.2), and a
   move into a one-shot closure consumes it. Existing.
4. The runtime never constructs a linear constructor, and never reads its fields. No
   `SPROUT_RUNTIME_CTORS` entry (`runtime/sprout_runtime.c`) names a linear constructor, and a
   `SYNC` comment there records the premise. The runtime does hold linear cells: `__chan_send`
   (`stdlib/chan.sprout`) hands one to the scheduler until `__chan_recv`. That is a move, after
   which the sender cannot name it.

The known hole in `linear_check` (BACKLOG P3 *a field unwrapped from a borrowed linear value*)
concerns the inner value of a `wrap`, not the cell of a boxed linear ADT. The wildcard leak
that sat beside it is closed (`docs/linear-wildcard-v0.md`). A future hole that lets an owned
linear cell alias would now corrupt memory, where before it only broke a resource discipline.
The reuse smoke and the premise tests are the guard.

## 5. Impact

- **Syntax, typing, diagnostics:** none.
- **Semantics:** none observable.
- **Compatibility:** no source change. IR changes only for functions that match and rebuild an
  owned linear parameter. `TcpConnection` and `Task` are built only from handles and never
  rebuilt (`net.sprout`, `task.sprout`), so the expected golden IR diff is empty.
- **CPR workers:** a result type whose constructors have at most one field gets an unboxed tier-2
  worker, and a tail constructor there is an unboxed return, not an allocation. A one-field linear
  wrapper may already be allocation-free on that path, and reuse covers the boxed path. `Grid` has
  two fields and gets no worker.

## 6. Tests

- ~~`just linear-reuse-smoke`: the `Grid` sieve, a CSV splitter and a tree insert allocate 0
  objects per step (`SPROUT_DEBUG_ALLOC`), like `just linvec-smoke`.~~ `scripts/linear_reuse_smoke.sh`.
- ~~An IR test~~ (`tests/stdlib/compiler/test_linear_reuse_ir.spr`, every bullet, plus a nested
  match whose outer token returns at the join):
  - an owned match-and-rebuild emits `IRReuseCtor` and no `sprout_alloc_obj`;
  - a `borrowing` parameter allocates;
  - two rebuilds on one path reuse once;
  - one rebuild in each `if` branch reuses in both;
  - a rebuild in one `if` branch and another after the join gives exactly one `IRReuseCtor`;
  - a rebuild inside a lambda allocates, including a lambda whose `borrowing` parameter has the
    outer owned parameter's name;
  - an inlined lambda (`(\x -> …)(g)`, `g |> \x -> …`) whose `borrowing` parameter is an owned
    parameter allocates and leaves the lender's cell, constructor included, unchanged; a `let`
    body or `let h = g` alias over an owned parameter still reuses;
  - a constrained existential constructor whose source count equals another constructor's
    allocates (§4.2 field count);
  - a type linear only through a `where linear` bound reuses;
  - a type's only constructor (no tag test) reuses, both from a `match` and from a
    `let Grid n v = g in …`;
  - a `wrap linear` constructor pattern takes no token;
  - a self-tail-recursive loop that reuses in its body reuses on every iteration (`IRTcoBack`
    stores `dst` into the parameter slot);
  - a borrowed value matched under a shadowing pattern (`match h with | c -> match c with …`)
    allocates;
  - a `_` arm, a `| other -> other` arm and a `let..else` fallback take no token;
  - another constructor of the same type and field count reuses, with the header rewrite;
  - a constructor with a different field count, or of another type, allocates.
- ~~A behaviour test~~ (`tests/stdlib/test_linear_reuse.spr`, also in `just test-stress`): values
  read back after a reused write are correct, and a later `match` sees the new constructor, including when a collection runs between the match and the rebuild. The
  call between them must allocate, or no collection can run there: it runs under
  `SPROUT_GC_STRESS=1`, and once more under `SPROUT_GC_HDRCHECK=1` and `SPROUT_GC_LINEAGE=1` for
  the header rewrite.
- ~~Time, measured once, since an allocation count cannot see added root traffic.~~ §1.1's
  table.
- ~~`test_borrow_erasure.spr` is updated as in §4.1.~~
- Gates: `rooting-cost-gate` and `scc-cost-gate` (the compiler gains a pass), and the golden IR.

## 7. Docs

- ~~spec §5.8: one non-normative sentence saying an owned linear value's cell may be reused.~~
- ~~`persistent-array-v0.md` §5.3: point *In-place reuse* here.~~
- ~~`compiler-internals.md`: the op and the rooting rule.~~
- ~~BACKLOG: delete the P3 entry, and file cross-type reuse and non-parameter scrutinees.~~
