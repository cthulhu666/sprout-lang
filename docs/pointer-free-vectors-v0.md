# Pointer-free vectors — v0

**Status — steps 1–5 landed (§7).** Non-normative: an optimisation, with no syntax,
typing or evaluation-order change, so `spec-v0.md` is untouched. It preceded
[gc-generational-v0.md](gc-generational-v0.md) §14.8's flip of `SPROUT_GC_MINOR`, which has landed.

## 1. Problem

The collector treats every vector element as a possible pointer. `sprout_gc_drain_marks` passes
each one to `sprout_heap_lookup`, which accepts any word equal to a live object's payload address.
An `Int` or `Double` element with that bit pattern keeps the object, and everything it reaches,
alive.

This happens in a real program. The galaxy client keeps a `Bitset` (one `Vec Int` of 15,625
bitmask words) live for the whole run. Built from `65d4cf0d`, word 9343 is `0x200000291008`: six
inhabited ids, and also the exact address of a list cell from loading. That one word retained
95,144 objects, 97% of the steady heap (96.7k live, against 1.5k when the word misses). One
added allocation at startup (`ec57dc78`) shifted every later address by 16 bytes and the hit went
away; the program's code was otherwise unchanged. Found with a per-root, then per-element, mark
count in a scratch runtime.

Why it is likely here and not just possible: the arena starts at `0x2000_0000_0000` (2⁴⁵), so a
word with bit 45 set, nothing above it and a sparse low part is a candidate address. Sparse
bitmasks have exactly that shape.

Three more costs follow from the same blindness:

- **Mark time.** A 1M-element `MutVec Int` is looked up element by element at every major
  ([persistent-array-v0.md](persistent-array-v0.md) §6: "Pointer-free vectors … Not measured").
- **GENCHECK false positives.** Once scalar stores skip the barrier
  ([scalar-store-inline-v0.md](scalar-store-inline-v0.md) §4.2), an `Int` slot equal to a young
  object's address is an edge the verifier reports as a missed barrier.
- **Benchmarks that depend on layout.** The step-4 and step-5 galaxy rows in
  `bench/results-2026-11-01-gc-minor*.md` measured the leaked 95k objects.

## 2. Goals and non-goals

**Goals**
1. A vector whose element type is a non-heap scalar (`type_kind.type_is_non_heap_scalar`: `Int`,
   `Bool`, `Char`, `Double`) is marked without looking at its elements.
2. This holds for vectors built through annotated, constraint-free generic functions, such as
   `vec_map` returning `Vec Int`, not only for direct calls.
3. Where the element type is not known, nothing changes: the vector is scanned as today. A gap
   costs precision, never safety.

**Non-goals**
- Object fields, tuples and closure captures. They are also scanned conservatively; ctor field
  kinds (`@.cfkinds`) already exist for objects. A separate, measured change.
- Moving the arena base to make collisions rarer. That is a mitigation, not a fix.
- Calls through class dictionaries: there the callee is unknown, so the vector stays scanned.
- Constrained creating functions with `SPROUT_OPT_OFF=spec` (§4.4).

## 3. Prior art

Every precise system takes the fact from the static type at allocation or instantiation.

| System | How it knows a block holds no pointers |
|---|---|
| Go | `mallocgc` sends `typ == nil \|\| !typ.Pointers()` to the noscan allocators. Generics compile one copy per GC shape, and `int` and `float64` never share a shape with pointers ([malloc.go](https://github.com/golang/go/blob/master/src/runtime/malloc.go), [§Gcshapes](https://github.com/golang/proposal/blob/master/design/generics-implementation-dictionaries-go1.18.md)). |
| GHC | `ByteArray#` "is not scanned for pointers"; `Data.Vector.Unboxed` picks the representation per element type through the `Unbox` class ([GHC.Prim](https://hackage.haskell.org/package/ghc-prim/docs/GHC-Prim.html), [vector](https://hackage.haskell.org/package/vector/docs/Data-Vector-Unboxed.html)). |
| Swift | Generic type metadata holds one metadata pointer per type parameter; the value witness flags include `IsNonPOD` ([TypeMetadata.rst](https://github.com/swiftlang/swift/blob/main/docs/ABI/TypeMetadata.rst), [MetadataValues.h](https://github.com/swiftlang/swift/blob/main/include/swift/ABI/MetadataValues.h)). |
| OCaml | Ints are tagged (`Is_long(x)` is `x & 1`), so the GC never takes one for a pointer. Under `FLAT_FLOAT_ARRAY` (a configure option, on by default), `caml_array_make` builds a flat float array (`Double_array_tag` ≥ `No_scan_tag`) when the initial value is a boxed float ([mlvalues.h](https://github.com/ocaml/ocaml/blob/trunk/runtime/caml/mlvalues.h), [array.c](https://github.com/ocaml/ocaml/blob/trunk/runtime/array.c)). |
| Boehm GC | The caller chooses `GC_malloc_atomic`, a promise of no pointers. A page hit by a near miss is blacklisted: still used, but only for small pointer-free objects ([gc.h](https://github.com/ivmai/bdwgc/blob/master/include/gc/gc.h), [gcdescr.md](https://github.com/ivmai/bdwgc/blob/master/docs/gcdescr.md)). |

Sprout's integers are untagged, so OCaml's route is closed, and generics are erased, so the type is
gone inside a generic body. Go's route fits: copy the code per shape, narrowed to the one fact
needed here.

## 4. Design

### 4.1 Runtime

- **The flag.** Bit 49, the top bit of a vector header's aux field (header bit 63), is `NOSCAN`.
  The aux field's other use for vectors, the inline element count, is at most 508 (9 bits). Every
  reader of a vector's aux goes through one mask helper: `slot_bytes`,
  `sprout_alloc_vector_sized`, `sprout_vec_release_inline_tail` (which today clears all of aux and
  must keep the bit) and the HDRCHECK sweep check.
- **Marking.** `sprout_heap_child_count_payload` answers 0 for a `NOSCAN` vector. Its only callers
  are the drain (which a minor's remembered set also feeds) and the GENCHECK verifier, so both skip
  the elements.
- **Setting it.** The compiler emits it inline: a load of the header word at `payload - 8`, an
  `or` with bit 63, a store. `ir_lowering.lower_reuse_header` already writes header words this way,
  with a SYNC comment to `sprout_obj_write_tag`; this one gets a SYNC comment to the runtime's
  mask. No new runtime entry. A call (`sprout_vec_noscan`) would keep the bit's position in C
  alone, but it adds a builtin and a call per creation to save one SYNC comment.
- **Inheriting it.** `vector_set`, `vector_append` and `vector_concat` produce a vector of their
  input's element type, so the result copies the bit (`concat` requires both inputs).
  `vector_set` on an empty vector returns before its copy, so the bit is written before that
  return. `vector_push` and `vector_mutset` write the same object.
- **The barrier.** `vector_mutset` and `vector_push` skip `sprout_gc_on_store` on a `NOSCAN`
  vector: such a vector has no pointer to record. This removes the barrier from scalar stores made
  in generic code, which [scalar-store-inline-v0.md](scalar-store-inline-v0.md) cannot reach.
  AGEPROF's store count (`sprout_ap_on_store`) still runs, so its numbers stay comparable.

### 4.2 Emission

In `ast_to_ir`, a call to a creating extern (`vector_empty`, `vector_from_list`,
`vec_make_filled`) whose result type is `Vector T`, with `T` a non-heap scalar, is followed by
the flag write on the result. Each of the three allocates a fresh vector (checked in
`runtime/sprout_runtime.c`), so the write touches no other value. This alone covers a direct call
at a concrete type.

### 4.3 Scalar copies

The stdlib calls the creating externs only inside generic wrappers (`vec_from_list`,
`mutvec_new`, `mutvec_empty`, `linvec.new`, …), where `T` is a type variable. So §4.2 needs those
bodies at concrete types.

A pass, **scalar copies** (module `scalar_copies`, `opt_config` pass `Scalar`, on by default),
runs in `compiler.lower_checked` right after `specialise_checked` and before
`lowering.lower_program`, on the resolved program. Both IR entry points (`--emit-ir` and the
analysis service) pass through it. There a node type names its variables through the env's
`@eta_fwdvar` markers, which `infer` emits for every annotated function, constrained or not.

1. **Candidates.** A top-level function with no constraints and a written type on every parameter
   and on the result. A constrained function has dictionaries a copy could not supply before
   lowering, so it is a candidate only as a `specialise` copy, which has none. An unannotated
   function has no markers, so the pass cannot name its variables; it is opaque.
2. **Instantiation.** At a call or a value reference to a candidate, the callee's written
   signature is matched against the node's type, parameters and result
   (`scalar_copies.bind_te`). The result counts: `mutvec_empty() -> MutVec a` binds `a` only
   there. The match yields bindings by written name, which is what `spec_keys.sub_type` reads.
   It is not `verify_dispatch.match_te`: that one compares heads only and fails when a written
   variable meets the same inference variable twice, which is every call at an enclosing
   function's own variable (`fn twin(x: a, y: a)` called inside a generic body).
3. **Which variables matter.** Type variable `a` of a candidate `f` is *creating* when `f`'s body
   calls a creating extern at element type `a`, or calls or references a candidate whose creating
   variable is instantiated to `a`. A fixpoint over the call graph. One function,
   `scalar_copies.site_key`, decides whether a site has a key, so this analysis and the
   retargeting in point 5 cannot disagree.
4. **Keys.** A key is `f` plus a non-heap scalar for each creating variable, in signature order.
   The copy is named `f$<T1>_<T2>…` (`vec_map$Int`). `$` cannot appear in a source name, module
   qualification is a dot prefix, and `specialise`'s copies are `…$spec_…`, so no name collides.
   A call or a value reference with every creating variable at a scalar is retargeted to the copy;
   any other instantiation keeps the original. Only unconstrained references reach this step:
   `infer.value_ref_eta` already eta-expands a constrained function in value position.
5. **Copies.** A copy is `f` with those variables substituted in its written signature and node
   types. Retargeting uses `site_key` too, so a site is rewritten exactly when the analysis
   admitted its key. The pass has its own walker: `specialise.make_copy` is private and keyed by evidence. It
   reuses the exported `spec_keys.sub_type`, `sub_param` and `Subst`. A copy's body is walked like
   the program, which admits the keys it calls. Keys are finite (candidates × four scalars per
   creating variable): a recursive call at a larger type such as `Vec a` is not a scalar
   instantiation and keeps the original, so the worklist terminates with no growth guard. The
   copies go through `verify_dispatch.verify_added`, as `specialise`'s do; for a constraint-free
   copy it checks only its calls to constrained callees.
6. **Clean-up.** `dce.elim_unreachable`, inside `ir_pipeline.compile_program_streaming`, drops an
   original with no reference left.

A function with a constraint and a creating variable (`mutvec_sort_by`) is copied twice in
sequence: `specialise` removes the dictionary, then this pass
copies that copy at the scalar. Each copy carries its instantiation in its node types, so the
order loses no key.

**Why not inside `specialise`.** One key of evidence plus scalar types would give one copy
mechanism and one copy per such function. The pass was kept apart while `specialise` was off by
default, and its §2 makes monomorphisation a non-goal. `specialise` is now on by default, so
merging the two key types is a follow-up; `SPROUT_OPT_OFF=spec` still leaves this pass running.
**Why not after lowering.** `make_copy`'s substitution works on the resolved program, and whether
type-variable identity survives lowering is unproven.

**Relation to [specialisation-v0.md](specialisation-v0.md) §5.** This is type-keyed copying
narrowed to one fact. Of §5's three obstacles to copying by representation, the first two do not
apply: the type substituted is the real one, and `Vec`'s field is a heap pointer either way. The
third does: a copy of a `stdlib.linvec` body could put a `vector_mutset` where `bce` assumes none.
Today's `linvec` creators contain no store; `just linvec-smoke` fails if a copied creator
(`linvec.new$Int`, `from_vec$Double`, …) has a `store` that is neither to a stack slot nor the
NOSCAN flag write, or a `call` outside an allowlist of creating externs and runtime helpers. It
is an allowlist because a store can be lowered inline or sit behind a wrapper (`linvec.set`).

**A copy is not only a renamed body.** `ast_to_ir` keys several decisions on concrete types that
a generic body never had: B1's inline `f64` access through `vector_get_direct` on a
`Vector Double` (in `sort_merge_all`, a creator; `mutvec_sort` reaches its copy through `specialise`'s copy,
since every public sort is constrained), the unboxed path for a `Maybe`-returning
extern scrutinee (`vec_filter$Double` reads through `vector_get`), and CPR worker plans read from
the signature's return type. A copied parameter typed `Int` is also rooted as a scalar. These are
tested today only on hand-annotated fixtures, so §6.3 runs each creating stdlib function at `Int`
and `Double` against its generic result.

### 4.4 What stays scanned

- A creating function reached through a class dictionary that `specialise` does not resolve (see
  the next item). With a concrete dictionary, `specialise` copies the caller and the instance
  method at `Int` calls `vec_from_list$Int`, so that vector is flagged.
- A constrained creating function that `specialise` does not copy (a call whose dictionary is not
  concrete, or any call under `SPROUT_OPT_OFF=spec`): `mutvec_sort`'s scratch buffer,
  `sort.sorted`'s result, `foldable_to_vec`. `scalar_copies` copies only constraint-free
  functions, so it does not catch these. The user's own vectors from `mutvec_new`,
  `mutvec_empty` or `vec_from_list` are still flagged.
- A creating function without a full written signature, and every caller that reaches the
  creator only through one.
- A creating extern used as a value (`list_map(vector_from_list, xss)`): it goes through an
  eta wrapper with no element type. Its generic wrapper (`vec_from_list`) is retargeted instead.
- A vector `read_int_lines` builds in C (it has no Sprout caller).
- With `SPROUT_OPT_OFF=scalar`, everything but direct calls (§4.2).

Each is scanned exactly as today.

### 4.5 The premise, and its tests

Skipping a vector's elements is safe only if a `NOSCAN` vector never holds a heap pointer. Once it
is flagged, neither the mark nor GENCHECK looks at its elements, so a broken premise is a
use-after-free that the GC's own verifier cannot see. It rests on five things:

1. **Static types are true.** An `extern fn` that misstates its type breaks this, as it already
   breaks rooting (BACKLOG **Compare each `extern fn`'s C definition against the emitted
   `declare`**). Two cases matter: an extern over a vector type that stores into it, and an
   extern typed to return a scalar whose C body returns a heap pointer. Today 18 declarations take
   or return a vector type: 7 in the prelude, 5 in `stdlib/mutable.sprout`, 3 in
   `stdlib/linvec.sprout` (redeclarations), `proc_run_raw` and `analysis_session_eval` over
   `Vec String`, and `analysis_session_diagnostics` over a `Vec` of
   tuples. Test: `scripts/check_extern_signatures.sh` fails on
   an extern over a vector type outside an allowlist, and on an extern returning
   `Int`/`Double`/`Char`/`Bool` whose C definition returns `sprout_ptr_word` or `sprout_str_word`.
2. **Inheriting ops keep the element type.** True of the three signatures in §4.1. Test: each
   one, on a flagged input, returns a flagged output that still reads back correctly after a
   forced collection.
3. **No Sprout operation reinterprets `Vector a` as `Vector b`.** There is no coercion extern
   (checked: `stdlib/unsafe.sprout` has only `unsafe_pure`). A `wrap` type over a scalar is not
   a non-heap scalar to `type_is_non_heap_scalar`, so `Vec Age` stays scanned.
4. **The copy pass flags only at scalar instantiations.** Test: `Vec String` built through
   `vec_map` and through a user generic builder is not flagged (IR check), and survives a
   collection under `SPROUT_GC_STRESS=1` with every string intact.
5. **No pass merges two creating calls of different element types.** A flagged vector's identity
   is now observable. A future CSE (`opt_config.Cse`, not implemented) keyed on call text would
   share one `vector_empty()` between `Vector Int` and `Vector String`, and `vector_append` would
   carry the bit to the strings. Such a pass must key on the result type too, or skip the
   creating externs. Test: a body that calls `vector_empty()` at both types, appends a string to
   the second, and reads it back after a forced collection under `SPROUT_GC_STRESS=1`.

## 5. Impact

- **Syntax, typing, diagnostics:** none.
- **Runtime:** the header bit, its mask helper, and four functions that copy or read it. No new
  entry.
- **Compiler:** a new pass and module, an `opt_config` pass on by default, and an `ast_to_ir`
  change. Reseed; golden IR changes for every example that builds a scalar vector.
- **Code size:** copies spread to every candidate caller that instantiates a creating variable,
  at most four per creating variable. Gated in §6 item 5; measured in §6.1.
- **Docs:** `compiler-internals.md` (the flag, its inline write and its premise),
  `opt-passes-v0.md` (the pass), `persistent-array-v0.md` §6 (the row becomes a link here),
  `scalar-store-inline-v0.md` §4.2 (the GENCHECK false positive is removed). `BACKLOG.md`: merging
  the scalar key into `specialise` once it is on by default.

## 6. Tests

1. **C runtime** (`tests/c_runtime/`): a `NOSCAN` vector whose element equals a garbage object's
   address does not keep it (the galaxy case, built by hand); the same vector without the flag
   does. Inheritance through set, append and concat, and `vector_set` on an empty flagged
   vector. A push that moves the elements out of line keeps the bit
   (`sprout_vec_release_inline_tail` rewrites aux), and the vector then survives a sweep under
   `SPROUT_GC_HDRCHECK=1`. The barrier is skipped on a flagged `vector_mutset`.
2. **IR shape** (b1-gate style fixtures): the flag write follows a direct `vector_from_list` at
   `Int` and at `Double`; `vec_map` at `Int`, `mutvec_new` at `Double` and `mutvec_empty()` at
   `Int` call copies that flag; `list_map(vec_from_list, xss)` at `Int` passes the copy; a generic
   annotated helper that passes `vec_from_list` as a value is copied when called at `Int`;
   `Vec String` and an unannotated helper do not flag; the instance method at `Int` of a
   vector-building class and `specialise`'s copy of a constrained creator call the `Int` copy.
   Emitted again with `SPROUT_OPT_OFF=spec`, a constrained creator and a dictionary-dispatched
   builder keep the original, call no copy, are not flagged and have no copy defined (§4.4).
3. **Behaviour** (`tests/stdlib/`): the premise tests of §4.5 under `SPROUT_GC_STRESS=1`, and one
   run per creating stdlib function at `Int` and `Double` (`mutvec_new` then `mutvec_sort`, a `MutVec Int` built by `mutvec_empty` and push, `vec_filter`, `vec_slice`, `vec_map`,
   `linvec.from_list`) with the result compared against `SPROUT_OPT_OFF=scalar`.
4. **The suite** under `SPROUT_GC_MINOR=1 SPROUT_GC_GENCHECK=1`, under `SPROUT_GC_HDRCHECK=1` as
   CI runs it, and `just linvec-smoke`. Once more with `SPROUT_OPT_OFF=spec`, without the double copy.
5. **Measurement and gates.** `bench/gc_minor/ab.sh`, default settings, N = 6, with GC time for
   mutvec_int, astar and digit_recognizer, and galaxy's live set built from both `65d4cf0d` and
   master. Gates, as [specialisation-v0.md](specialisation-v0.md) §7 M1 set them: self-compile
   time within its 2.2% noise floor, `just rooting-cost-gate`, and the emitted binary size of the
   bench corpus and the compiler within 10%; plus the self-compile's node count within 10%.

### 6.1 Results of item 5 (step 4b, 2026-11-04)

Arm OFF is the same source compiled with `SPROUT_OPT_OFF=scalar`; its IR lacks only the copies.
Medians of 6 runs per arm, default GC settings. The machine was loaded (load average 10–65 on 11
cores), so wall clock is noisy; CPU time and instructions retired are the robust columns.

| workload | GC ms, MINOR=0 OFF → ON | GC ms, MINOR=1 OFF → ON | CPU s, MINOR=0 OFF → ON |
|---|---|---|---|
| mutvec_int | 9,277 → 589 (−94%) | 1,424 → 445 (−69%) | 3.45 → 0.51 |
| astar | 17.9 → 5.4 | 26.7 → 5.8 | 0.04 → 0.02 |
| digit_recognizer | 12.2 → 7.3 | 37.0 → 11.7 | 0.49 → 0.47 |
| galaxy_client | 181 → 150 | 174 → 144 | 11.1 → 9.9 |

Only mutvec_int's ranges do not overlap; the other three are short runs (astar under 0.1 s) or
noisy. Collection counts are equal in both arms, and outputs match.

**Galaxy's live set.** Built from `65d4cf0d`, the steady live set is 96.7k objects (§1). At HEAD it
is about 1.5k in both arms: the word misses at today's layout, so the live set cannot show the
fix. The fix holds by construction: the `Bitset`'s `Vec Int` is now built by `vec_from_list$Int`,
its scratch buffer by `mutvec_new$Int`, and both are flagged, so no element is read as a pointer at
any layout.

**Gates.**
- Self-compile: instructions retired +0.15% (16 interleaved rounds; per-round paired delta median
  0.00%). CPU and wall medians moved between −2.4% and +7.6% across subsets of 48 rounds, so they
  cannot resolve 2.2% under this load. Max RSS 277.5 MB vs 277.4 MB.
- `just rooting-cost-gate`: within budget.
- Binary size: the four workloads and the compiler within ±0.5% (compiler `__TEXT` identical).
- Self-compile node count: +26 nodes, +0.018% (`[scalar] keys=3 nodes=26`).

## 7. Steps

1. ~~Runtime: the flag, its mask helper, marking, inheritance, barrier; C tests.~~ Landed
   (`tests/c_runtime/vec_noscan.c`).
2. ~~Emission (§4.2) for direct calls; IR tests; reseed; golden IR.~~ Landed (`IRVecNoscan`,
   `just noscan-gate`, `tests/stdlib/test_vec_noscan.spr`).
3. ~~`check_extern_signatures.sh` gains premise 1's two rules.~~ Landed (`just check-extern-signatures`,
   [gates.md](gates.md) §Extern signatures).
4. The scalar-copy pass; IR and behaviour tests; reseed; golden IR; the measurements of §6 item 5.
   - ~~4a: `scalar_copies` as a dry run.~~ Landed (`test_scalar_copies.spr`,
     `SPROUT_TRACE_SCALAR=1`).
   - ~~4b: the copies, retargeting, `verify_added`, the flag on by default, and flipping
     `opt_config.implemented(Scalar)`.~~ Landed (`test_scalar_copies.spr`,
     `tests/stdlib/test_vec_noscan_copies.spr` in `just test-stress`'s list, the copy and dce
     fixtures of `just noscan-gate`, the store check of `just linvec-smoke`). Measured in §6.1.
5. ~~Back to [gc-generational-v0.md](gc-generational-v0.md) §14.8: re-run the A/B and decide the
   flip.~~ Landed: [results-2026-11-04-gc-minor-pfv.md](../bench/results-2026-11-04-gc-minor-pfv.md);
   `SPROUT_GC_MINOR` is on by default.
