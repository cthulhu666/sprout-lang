# Compiler Internals

Invariants and design constraints to know **before editing** `stdlib/compiler/` or `runtime/sprout_runtime.c`. Violating these produces subtle, hard-to-diagnose bugs. For diagnostic tools to use when things are already broken, see [debugging.md](debugging.md).

## GC ABI Invariants

### Strings travel as `i64` (GC Option C)

**Intentional design — do not "correct" it.**

Throughout the IR pipeline (`ast_to_ir.sprout` → `ir_lowering.sprout`), String values are represented as `i64` at the LLVM IR level — a raw pointer cast to an integer. The GC root-tracking table stores all heap roots as `i64`; making strings travel as `i64` throughout IR means every string slot is automatically compatible with the root table without `ptrtoint`/`inttoptr` casts at each GC-safe point.

`Char` is `i64` too, but for the opposite reason: it is an **immediate Unicode codepoint**, not a pointer — `'x'` lowers to `add i64 0, 120` via `IRConst`, exactly like `TInt`. Both types being `i64` is what makes this easy to get wrong; the distinction is load-bearing for every rooting decision below.

Implications for IR-pipeline edits:
- `type_kind.type_is_non_heap_scalar` classifies `Int`/`Bool`/`Char`/`Double` as scalars; String is heap, so a String SSA value is an `i64` heap handle that must be rooted across GC-triggering ops.
- String literals lower to a `str_ptr` global coerced to `i64`. Char literals do **not** — see above.
- String comparisons must coerce both operands back to `ptr` before calling `str_eq`/`str_compare` (see the `emit_ptr_comparison` lowering in `ir_lowering.sprout`).
- `str_concat`, `str_slice`, etc.: called with `i64` args, return `i64`.
- String globals size the LLVM array with `str_byte_len(s) + 1` — not `str_len`, which counts Unicode codepoints rather than UTF-8 bytes.

If you see `ll_ptr()` for a String in emitted IR, that is a regression. Canonical form is `ll_i64()`.

### Non-moving GC (mark-sweep)

**Foundational invariant — relied on by every rooting helper.**

Sprout's GC is **non-moving mark-sweep**.  Every heap object occupies a 16-byte-aligned slot inside a 1 MiB region (`SproutRegion`).  Each slot begins with an 8-byte inline header at `payload_ptr - 8`; the payload starts at `payload_ptr`.  Header layout (64 bits):

```
bits  0– 7  kind   (SproutHeapKind: FREE=0, OBJ=1, CLOSURE=2, …, CSTR=10; POISON=0xFF)
bit   8      color  (mark bit, toggled during the mark phase)
bits  9–13  (reserved)
bits 14–63  aux    (OBJ: (tag<<8)|arity; CSTR: byte length; CLOSURE: n_caps; TUPLE: word count; FREE/POISON: slot_bytes)
```

**CSTR byte length is the only length a byte-offset builtin may consult — never `strlen`.** Because every Sprout String is a headered CSTR block (arena strings, header-prefixed static literals from `ir_lowering.emit_str_global`, and interned strings all carry it), its byte length is an O(1) header read: `sprout_cstr_byte_len` in `runtime/sprout_runtime.c`, exposed to Sprout as `str_byte_len`. Any runtime function that indexes or scans from a **caller-supplied byte offset** must get the length from there. A single `strlen` — even just to bounds-check the offset — makes that call O(|s|), and a scanner that calls it once per position O(|s|²). This is not hypothetical: `str_slice_bytes` and `str_starts_with_at_byte` both opened with `strlen`, which made the lexer quadratic in file size (`lexer.try_ops` probes 13 operators at every token position, each one re-scanning the whole source) and cost ~37% of the test suite's CPU. The byte-offset API exists *specifically* to avoid whole-string walks; a `strlen` inside one silently cancels its reason to exist. Guarded by `tests/stdlib/test_byte_offset_cost.spr`, which pins cost as independent of string length rather than checking a wall-clock budget.

**OBJ arity is an ABI invariant.** The low 8 bits of an OBJ's aux (`SPROUT_OBJ_ARITY_MASK`, max `SPROUT_MAX_OBJ_ARITY` = 255) are the GC's *only* record of that object's payload size: `slot_bytes` sizes the slot from it and `sprout_heap_child_count_payload` scans exactly that many words. A wrong value there desyncs the sweep's slot walk instead of failing loudly, so every OBJ allocation must write its true field count. The split is single-sourced through `SPROUT_OBJ_ARITY_BITS`/`_MASK`/`SPROUT_MAX_OBJ_ARITY`; widening the arity field narrows the tag (aux is 50 bits, so 8 arity bits leave 42 for the tag). `ast_to_ir.max_boxed_arity()` mirrors the ceiling and rejects wider products at compile time.

**Boxed products are allocated arity-generically.** `IRMakeCtor` lowers to one `@sprout_alloc_obj(tag, nfields)` call followed by a `getelementptr`/`store` pair per field — the same shape `IRMakeTuple` uses against `@sprout_alloc_tuple_blob`. The returned slots are **uninitialized** while the header already advertises `nfields`, so a collection in that window would scan garbage. It cannot happen: collections trigger only from an allocation, and codegen emits nothing but stores between the call and the last field write. **Do not introduce an allocating call into that window.**

Per-region 1-bit slotmaps track live slot starts; `sprout_heap_lookup` does a binary search over the region table, verifies the slotmap bit, and rejects FREE-kind headers — giving exact membership in O(log region_count).  Large objects (slot > 4096 bytes) are stored as single-slot dedicated `malloc` blocks registered in the region table with `is_large=1`.

`sprout_gc_sweep` in `runtime/sprout_runtime.c` runs two passes: (1) scan all slots — live slots clear their color bit; dead slots release external storage and get a FREE header (or a POISON header in lineage mode, retaining the corpse with the slotmap bit set) — while **staging** each region's FREE slots onto the per-class freelists; (2) release every region with no live and no poison objects, dropping the staged freelist entries of each region it releases, and reopen one region if that released them all.

**The freelists are built during pass 1, not by a second walk.** The freelists must end up holding every FREE slot of every *surviving* region, and the obvious way to get that — walk the whole heap again once pass 2 has released the empty regions — costs a second full traversal per collection. That measured at ~27% of collector time and **~17% of total self-hosted compile time** (emitting IR for the 452 KB `ast_to_ir.sprout` walked 97M slots to classify them and 97M more to rebuild). What made building them in pass 1 unsafe is region release: a slot pushed for a region that pass 2 then hands back leaves the freelist pointing into memory the process no longer owns. Staging removes that hazard without the second walk — while a region is walked, each class it pushes to has its pre-region head saved once, so every entry pushed for region R is chained on top of that saved head and restoring the saved heads drops exactly R's entries (`fl_push_staged` / `fl_region_commit` / `fl_region_rollback`). Three things here corrupt the freelist *quietly* rather than crashing, so all three are pinned by `tests/stdlib/test_gc_freelist_reuse.spr`:

- **Slots freed in an earlier cycle must be re-listed.** The freelists are rebuilt from scratch each sweep, so pass 1 pushes from the already-FREE branch too, not only from the slots it frees this cycle. Listing only the latter leaks capacity permanently.
- **The rollback is load-bearing.** Skipping it survived 422 collections of a real compile before producing a dangling entry — "a test run passed" is not evidence for this one.
- **Pass 2 must not force-keep a region.** It releases every empty region unconditionally and reopens one only if that emptied the table (`kept_normal == 0`), whose `bump` is 0 and so needs no relisting. Retaining the last empty region instead — as it once did — leaves a region whose staged entries pass 1 already rolled back, so its slots are neither on a freelist nor above the bump pointer: unreachable capacity. Recovering that needed a third pass to relist exactly those regions, whose skip condition then had to mirror pass 1's commit decision *exactly* or a slot got listed twice and handed to two live objects. Releasing unconditionally makes the rollback predicate and the release predicate the same single condition; the cost is one 1 MiB `free`+`malloc` plus an 8 KiB slotmap `calloc`, only on a cycle where every normal region came out empty.

**`SPROUT_FL_VERIFY=1`** is the oracle for all of the above: on every collection it recomputes what a full post-release heap walk would yield and compares it against the staged freelists as a sorted `(class, payload)` multiset, aborting on the first disagreement — a missed slot, a doubly-listed slot, or an entry pointing into a released region. Each staged entry is checked with `region_find` **before** its next-pointer is dereferenced, because the dangling-entry case is precisely the one this exists to catch and reading it to discover that would be the use-after-free being hunted — on a heap where the block has gone back to the OS, that faults with no diagnostic and a signal indistinguishable from a rooting bug. A chain longer than the full walk is reported as a cycle rather than followed. It is O(heap) plus two sorts per collection, so it is **not** enabled job-wide in CI the way `SPROUT_GC_HDRCHECK` is (it triples a compile-heavy run); never leave it on in a timing run. Two gates cover it, and both halves are needed:

- `just test-stress` sets it alongside `SPROUT_GC_STRESS=1`, which maximises the *number* of collections — but forces small heaps, where pass 2 barely releases a region. Its `FL_VERIFY_SKIP` list turns the oracle off per-file where the combination is disproportionate: `test_ir_codegen_ctors` alone runs 180s under stress and 434s with the oracle, and since it is the serial critical path of that recipe (all 15 other files finish in under 2s) it set the whole CI step from ~300s to 883s. Adding a stress file with a large heap costs minutes there, and the step prints nothing until every file has finished — so it reads as a hang rather than as slowness. Check the step duration when you add one.
- `just test-freelist-verify` (in `ci-fast-gates`) runs it at the default threshold over multi-region heaps that die wholesale, which is what exercises release and rollback. Measured with a release counter: `test_gc_region_release.spr` drives **14** region releases against `test_gc_freelist_reuse.spr`'s 1.

Both test files are **workloads, not assertions** — their Sprout-level checks were mutation-tested against three broken sweeps and all three still reported "passed". A freelist defect is not observable from Sprout as a wrong value, so the detection is entirely `SPROUT_FL_VERIFY`. Do not treat a green `just test` as covering this.

**Object age — header bits 9–13.** The gap between the colour bit and aux (which `sprout_hdr_make` shifts to bit 14) holds a saturating 5-bit count of collections an object has survived, bumped in the same store that clears the colour bit in pass 1, and reset whenever a slot is re-initialised. Nothing in the collector's behaviour depends on it today; it exists so the generational question can be measured, and it is the field a generational collector would reuse. **`SPROUT_GC_AGEPROF=1`** reports, per run, how much marking is spent on objects that already survived a cycle (the ceiling on what a minor collection could skip) plus the mutation traffic a write barrier would carry — `mut_calls` is deliberately separate from `ptr_stores` so a zero cannot be misread as "free" when it means "never called". Unlike `-DSPROUT_GC_PROFILE` it needs no special build and does not distort timings. Calibrated by `just gc-ageprof-check` against two workloads with known answers (`tests/stdlib/test_gc_age_retain_{all,none}.spr`); the runtime also aborts if the age histogram disagrees with `g_managed_heap_count`. Findings and the resulting recommendation: [docs/gc-generational-v0.md](gc-generational-v0.md) — note the headline is that the nursery's payoff is specific to self-hosted compilation (97% ceiling) and near-zero on the HTTP server (13%).

Because objects never move, the address of a live heap object is **stable for the entire program lifetime**.

**Load-bearing invariant — registration/adoption paths must never trigger GC.** Functions that register or adopt an already-allocated object into the managed set (e.g. `register_cstr` and its callers) run with earlier, not-yet-rooted objects held in registers: a string builder registers freshly-built strings back-to-back while still holding the previous ones unrooted. The contract "registration itself never collects" is therefore load-bearing — **never add a `sprout_gc_maybe_collect*` call (or any operation that can) to a registration/adoption path**, or those in-flight unrooted objects get swept mid-sequence. Allocation paths may collect; registration paths may not.

Implications for codegen / IR design:

- The "push the alloca holding an `i64` heap-address; never reload" pattern (used by `IRRoot` in `stdlib/compiler/ir_rooting.sprout`) is correct: the `i64` stored at the alloca remains a valid heap pointer for the entire function lifetime.
- If GC ever becomes moving (copying, compacting, generational), every root push must be paired with a re-load *after* its trigger op, and every heap-typed SSA use after a trigger must source from the reload — a sweeping rewrite affecting `ast_to_ir.sprout`, `ir_lowering.sprout`, and `ir_rooting.sprout`. This is not currently planned.

### Region arena and address→region lookup

Regions are carved from a contiguous **reservation** of address space (`mmap(PROT_NONE)`, default 4 GiB via `SPROUT_GC_ARENA_MB`), with each 1-MiB chunk committed by `mprotect` on first use. A reservation costs no physical memory; RSS still tracks committed chunks only. This makes `region_find` a shift plus one array load instead of a binary search over the sorted region table — worth ~5–7% on a self-hosted compile, where 47 live regions made the search ~5.6 iterations deep.

Three properties are load-bearing if you touch `region_find` or `sprout_heap_lookup` in `runtime/sprout_runtime.c`:

- **Keep the inlined footprint small.** `sprout_heap_lookup` is inlined into the mark loop and the root scan *only while it stays small*. Putting the arena fast path and the binary search in one function pushed it past the inliner's threshold and cost more than the O(1) lookup saved (astar +7%, and the compiler's win cut from ~14% to 4–6%). The search lives out of line in `region_find_slow` for this reason.
- **The arena is an optimisation, never a correctness dependency.** `region_find_slow` searches a table that contains arena regions too, so *either* branch answers correctly for any address. Large objects (`slot_bytes > SPROUT_LARGE_THRESHOLD`, possibly several MiB, so not indexable by one shift) and any region opened when the arena is unavailable or exhausted stay `malloc`'d and take the search. `SPROUT_GC_ARENA_MB=0` disables the arena outright, which is how `just gc-arena-check` exercises the fallback.
- **An arena chunk must never reach `free()`.** Release paths test `arena_contains(base)` and recycle the chunk (with `madvise`, so RSS still falls) instead.

`just gc-arena-check` asserts the fast path is actually taken (`arena_regions > 1`, `overflow_regions == 0`), that disabling works, and that a deliberately undersized arena produces a *mixed* state where both lookup paths are live at once. That gate exists because a failed or mis-sized reservation silently reverts to the search while every other test still passes. Rationale, measurements, and the regressions found while building it: [gc-arena-lookup-v0.md](gc-arena-lookup-v0.md) §12.

### Closure layout: the header's `aux` carries the arity as well as the capture count

A closure env is a heap block with the code pointer at slot 0 and captures at slots 1..n. Its GC header word packs `kind` in bits 0–7 and `aux` in bits 14–63, and for `SPROUT_HEAP_CLOSURE` that 50-bit `aux` holds **two** fields: the capture count in the low 32 bits and the closure's **arity** — how many arguments one application consumes — above it. Same idiom as `SPROUT_HEAP_OBJ`'s `(tag << 8) | arity`, and chosen for the same reason: it adds a field without moving a payload slot, so the capture indices, the `(n_caps + 1) * 8` size formula, and the GC child walk all keep their existing shape. `sprout_closure_aux` / `sprout_closure_ncaps` / `sprout_closure_arity_of` in `runtime/sprout_runtime.c` are the only places that know the split.

**The arity is load-bearing, not diagnostic.** A Sprout function type does not determine a value's calling convention: `\(x, y) -> …` is one two-parameter closure and `\x -> \y -> …` is two one-parameter ones, and both have type `Int -> Int -> Int`. So a call site applying a *value* cannot know statically how many arguments one application may consume. Every `IRApplyClosure` therefore emits a `@sprout_closure_arity_check(handle, n_args)` call ahead of the indirect call; without it, over-application returned the closure handle reinterpreted as the result type (exit 0, no diagnostic) and under-application dereferenced a register nothing had written (SIGSEGV). Calls to a statically-known callee lower through `translate_general_call`, are checked at compile time, and never reach the runtime guard.

Two constraints if you touch this:

- **The split is duplicated in emitted IR.** `ir_lowering.sprout` writes the arity through `@sprout_alloc_closure(size, arity)`, and both files carry SYNC comments. Changing the bit split in one place silently mis-reads every closure's arity.
- **The check must stay call-shaped, not branch-shaped.** An inline compare-and-branch would open a new basic block mid-op, and `lower_op` renders ops inside a block whose label the builder has already fixed — any downstream `IRPhi` would then name a predecessor that is no longer the real one.

`SPROUT_CLOSURE_ARITY_ANY` (all arity bits set) marks a closure the check waves through. Only the unresolved-dict poison thunk uses it: that thunk declares no user parameters yet is applied with the method's arguments, so a truthful arity would make the guard fire first and replace its located message with a generic mismatch.

### Type-aware GC rooting — the `ir_rooting` pass

**Intentional design — do not root non-heap scalars.**

Rooting is a dedicated pass over the IR (`ir_rooting.insert_roots`), not a set of
per-call-site helpers. It computes per-op liveness and inserts `IRRoot` ops so that
every heap SSA value stays reachable across GC-triggering ops. Type-awareness comes
from classifying which SSA values are heap:

- `op_triggers_gc` — which ops are GC-safe points (allocations, calls, etc.).
- `op_produces_simple_heap` — which op *results* are heap values that must be tracked. Scalars (`Int`/`Bool`/`Char`, via `type_kind.type_is_non_heap_scalar`) are excluded; an `IRCall` result is rooted unless its carried return `IRType` is `IRTScalar`.
- `compute_heap_origin` / `roots_across` — track the heap-origin set and compute, for each op, the values that must be rooted across it (live-after ∪ heap operands the op exposes).

**Why it matters:** rooting every `Int`-returning expression emits pointless
`alloca i64; store; sprout_gc_push_i64_root; …; sprout_gc_pop_roots(1)`. Profiling
N-queens showed 67% of CPU time in GC root calls, ~50% pure waste from Int args.
Type-aware rooting gave a measured **1.5–2.7× speedup** (N=12: ~1.5 s → 928 ms).

**Invariant:** when no source-level type is available, root conservatively (treat the value as heap). A spurious extra root is harmless; a missing root corrupts the heap. Do not treat `TVar` as non-heap — it may resolve to a heap type in monomorphized code.

**The same policy applies to top-level `let` globals.** A runtime-computed `let` gets its storage slot registered as a *permanent* root in `@__sprout_init_globals` (`IRRegisterGlobalRoot`), and `ast_to_ir.global_root_ops` gates that registration on `type_is_non_heap_scalar` of the initializer's type. This gate was added later than the SSA-value one: the global path previously registered **every** `let` unconditionally, so the invariant above held for SSA values and silently did not hold for globals — `stdlib/math.sprout` alone contributed 33 permanent roots, every one an arithmetic `Double`. A permanent root is worse than a transient one: it is walked on every collection for the life of the process, and it feeds raw arithmetic bit patterns to a conservative scan. Const-eligible lets (`eval_const_expr_ir`: `TInt`/`TBool`/`TUnit` literals) never had this problem — they become `private constant` and are never stored to.

Regression tests: `tests/stdlib/compiler/test_scalar_global_no_root.spr` (globals — the four scalar types unrooted, a boxed ctor still rooted, and per-let discrimination in a mixed module); `tests/stdlib/compiler/test_ir_call_result_rooting.spr` and `test_ir_tuple_result_rooting.spr` (SSA values).

### GC safety linter

`just gc-safety-check` lints `runtime/sprout_runtime.c` for `const char*`/`char*` parameters live across `sprout_gc_maybe_collect_threshold()` calls. Run after editing any C builtin that allocates heap strings. Use `just gc-safety-check --strict` to fail on any finding; the default mode warns only.

## CPR extern ABI: width=2 is direct, width=3 is sret

**Intentional design — do not remove the sret branch.**

The CPR (Constructed Product Return) path unboxes calls to C-runtime externs that
return a small ADT (`Maybe`/`Result`/`List`/…) instead of allocating a boxed value,
per `cpr_width_for_type_expr`. The two widths use **different LLVM calling
conventions** on the C boundary, and they are not interchangeable:

- **Width=2** (16-byte `SproutUnboxed2`, e.g. `Maybe X`): direct register return.
  Both LLVM's `declare { i64, i64 } @X_unboxed(...)` and Clang's lowering of the
  matching C struct return agree on this — arm64 Darwin returns in x0/x1, SysV in
  rax/rdx. No special-casing needed.
- **Width=3** (24-byte `SproutUnboxed3`, e.g. `List X`): Clang lowers a 24-byte
  struct return to the **sret** convention — `void @X_unboxed(ptr sret(...), ...)`
  — not direct multi-register return. A width-3 extern **must** be declared and
  called with the sret first-argument form to match; `emit_extern_decl_keys` emits
  the sret declare, and the call site allocates a `{i64,i64,i64}` slot, passes it
  as the sret first arg, and loads the result back.

**Why this matters:** the LLVM-to-LLVM path (a Sprout-defined width-3 worker
calling another Sprout-defined width-3 worker) stays on the direct path — LLVM is
internally consistent with itself, so no sret is needed there. Only the
**LLVM-to-C boundary** (a Sprout caller calling a `runtime/sprout_runtime.c`
extern) needs sret for width=3, because that boundary must match what Clang
actually emits for the C struct-return ABI.

**Consequences of getting this wrong:** the mismatch is silent — a width-3 extern
declared/called the direct-return way does not fail to link or verify; it just
returns garbage at runtime (LLVM's `{i64,i64,i64}` register-return convention and
Clang's sret convention disagree on which registers/memory hold the result, so the
values are simply wrong). This was found via `native_set_to_list`, which had been
on the CPR allowlist but was never exercised by Sprout code — `set_to_list` on a
non-empty set silently returned `Nil` until the sret branch was added.

**How to apply:**

- Adding a new width-3 extern to `is_cpr_extern_allowlisted` needs no extra work —
  `emit_extern_decl_keys` and `emit_worker_cpr_call` detect width=3 automatically
  and route it through the sret path.
- Do not remove the sret branch in `emit_extern_decl_keys` — doing so silently
  breaks every width=3 extern the same way `native_set_to_list` broke.
- Do not add sret to the width=2 path — it works today via direct return; adding
  sret there is unnecessary and risks regressing the working case.

## Bool-returning externs must be defined `long long`, never `_Bool`

**ABI invariant — sibling of the CPR width mismatch above, and silent the same way.**

`extern fn f(…) -> Bool` lowers to `declare i64 @f(…)`. The caller therefore reads the **full 64-bit** return register and treats any nonzero value as `true` (Sprout's `Bool` is an `i64`; `b == true` lowers to `icmp eq i64 %b, 1`). A C definition returning `_Bool` does **not** satisfy that contract: on x86-64 SysV, `_Bool` is returned in `AL` with the upper 56 bits of `RAX` **undefined**. Clang emits `sete %al` and leaves whatever the previous call returned in the high bytes, so `false` reads back as a nonzero `i64` — as `true`.

**So: any function reached through `extern fn … -> Bool` returns `long long` 0/1.** `str_eq` is the one legitimate `_Bool` in the runtime, because it is lowered specially (`declare i1 @str_eq(ptr, ptr)` + `zext`, see `IRStrEq` in `ir_lowering.sprout`) and its declaration matches its definition.

**Why this class of bug hides so well.** Four builtins (`str_starts_with_at_byte`, `str_starts_with`, `regex_is_match`, `term_is_interactive`) were `_Bool` for their entire history:

- **Platform-conditional.** arm64 leaves the register clean in practice, so no macOS run can catch it. It is live only on x86-64 — which is what CI and the release artifacts build.
- **Input-conditional.** It bites only where the function falls through to a comparison. An early `return 0` compiles to `xorl %eax, %eax`, which zeroes all of `RAX`, so the *same function* is correct for some inputs and wrong for others.
- **It hid itself from the audit gate.** the `check-approved-builtins` justfile recipe greps for `^long long <name>(`, so a `_Bool` definition was invisible to `runtime/APPROVED_BUILTINS` too — the ABI mismatch and the missing allowlist entry had one cause.

It surfaced only when a new test called `str_starts_with_at_byte` with the first inputs in the repo's history that returned false via the comparison path.

**How to apply:** when adding a builtin used as `extern fn … -> Bool`, write `long long` and return `? 1 : 0`. `tests/stdlib/test_bool_extern_abi.spr` asserts every such builtin returns a *canonical* Bool (`b == true || b == false`), which detects a dirty register regardless of which code path produced it — add new Bool externs there.

## Tuple-CPR and intra-function tuple SRA (scalar replacement)

Design + status: `docs/scalar-replacement-v0.md` (Appendix B, LANDED). Distinct from the
extern-CPR path above — these workers are **Sprout-defined and Sprout-called**.

- **Tuple-return CPR.** `match f(args) with (a,b[,c]) ->` over a top-level fn returning a
  scalar 2-/3-tuple routes to `@f_worker` returning the fields by value
  (`IRCallUnboxed2`/`IRRetUnboxed2` at width 2; `IRCallUnboxed3`/`IRRetUnboxed3` at width 3).
  **Width 3 returns `{i64,i64,i64}` DIRECTLY — no sret.** The sret warning above is about the
  LLVM-to-C boundary; a tuple worker never crosses it (both sides are Sprout-emitted LLVM,
  which is internally self-consistent). Adding sret here would be wrong.
- **Single width oracle.** `scalar_tuple_width(t) -> Maybe Int` is consulted by the router, the
  worker's declared return type, the repack tail, and the worker-chain — so the call-site op and
  the worker's `ret` type are derived from one number and cannot diverge (a mismatch fails
  `opt --passes=verify` loudly, never a silent wrong-registers return).
- **Intra-function SRA (do-block-localized).** `let x = <producer>; …; match x with (tuple-pat)`
  is scalar-replaced: the tuple never allocates, an `if`-producer merges fields via N per-field
  phis, and fn producers become width-w workers. Worker-collection and `translate_do` share the
  shadow-free `sra_core_eligible` oracle (translation adds a shadow gate ⇒ translation ⊆
  collection, so every worker called is emitted). Soundness = a **default-deny** escape check
  (`sra_escape_ok` over the exhaustive `compute_free_vars`): `x` may escape nowhere but the one
  consuming scrutinee. A `sra_rest_plain` guard bars a Maybe/Result do-bind in the continuation
  (those reset the SRA map), confining the change to `translate_do`.

## Concrete-instance devirtualization

Design + status: `docs/devirtualization-v0.md` (LANDED). A related but distinct optimization in
`lowering.sprout` (the dictionary-passing pass), not `ast_to_ir.sprout`.

- **What.** A class-method call whose dispatch dictionary is a **statically-known concrete instance**
  is lowered to a **direct call** of the concrete `__tc_{Class}_{Type}_{method}` fn, dropping the
  runtime dictionary — no `sprout_alloc_closure`, no generic `__cm_` wrapper indirection. Before,
  every concrete class-method call built a dict of eta-closures (one per method, most dead) and called
  the generic wrapper, which then dispatched *indirectly* to the concrete fn.
- **Gate (`try_devirt_concrete`).** Fires iff the leading (and only) `TDict` is `EvClasses blocks` and
  one block is a fully-resolved **concrete** `EvInstance` *providing the method* (`ctx_inst[key]` has
  `mname`). It retargets to that concrete fn and passes `consume_inner_dicts(children)` — the instance's
  **own context dicts** — as trailing args, dropping the user-arg witness and any **sibling superclass
  blocks**. Soundness hinge: a concrete instance fn's arity is exactly `user_args + |children|` — it
  never takes superclass dicts (those live only in the `__cm_` wrapper; a concrete body resolves supers
  concretely), so the block is found *by method presence* (which also skips the super blocks) and the
  trailing dicts match by construction. Covers `Enum`/`Eq`/`ToString` (all dicts dropped), `Ord`
  (super block dropped, 2→0), and context-constrained/combined instances (inner dict forwarded).
  `EvForward`/polymorphic and unresolved inner dicts fall back. `opt --passes=verify` catches an arity
  mismatch; a dict *ordering* bug would not (all `i64`), so a multi-constraint value test guards order.
- **Composes with CPR.** The retargeted callee is a real top-level fn, so the match-site Maybe/tuple
  CPR routes it to that fn's `_worker` — the returned `Maybe`/tuple stays unboxed. This is what makes
  the rivers-demo `bake_tile` fully allocation-free (tuple SRA + devirt).

## Whole-program passes: scan `decls` AND read `env`

**Any pass that derives a fact by scanning `decls` must also recover that fact
from `env`.** The two compile entry points assemble a program differently, and a
pass that only walks `decls` silently sees an empty vocabulary on one of them:

- **File / `--phase check` / `compile_full_ir`** — `bundler.bundle_file` inlines
  the prelude and every import as real AST nodes, so `prog.decls` holds every
  `TypeDecl`/`ClassDecl`. A decl-scan sees everything.
- **REPL / LSP / analysis service** — `compiler.compile_source_with_cache` →
  `checker.check_program_with_env` parses only the session source. Imports arrive
  as an env of `(name, Scheme)` pairs plus `@`-prefixed markers, never as decls.
  A decl-scan sees nothing.

`infer.class_names_from_env` and `infer.type_names_from_env` are the reference
implementations: each scans `dict_entries(env)` for its marker family
(`@class:`, `@type:`) and folds the recovered names into the decl-derived set.

This has bitten twice. A `where ToString a` constraint using a prelude class was
rejected in the REPL because the class set was empty; then the type-name
validation pass rejected `Vec`, `Dict` and `Result`, making 11 of 27 top-level
stdlib modules unloadable there. Both were invisible to `just test`, which
exercises the bundling path. Write the regression test against
`compile_source_with_cache` — see `tests/stdlib/compiler/test_repl_type_vocabulary.spr`
and `docs/repl-env-type-vocabulary-v0.md`.

The failure mode is silent by construction: `module_loader.load_module` turns a
module's `CheckErr` into an empty pair list, so the error surfaces far from its
cause as `Unknown variable: <module>.<name>`.

### The stronger form: don't re-decide, consume an authority

The rule above is necessary but not sufficient, and the third instance proved it.
`module_loader` was **not** missing information when it prefixed an extern with the
import alias — it pattern-matched `ExternFnDecl` and simply decided differently
from `bundler.add_decl_to_symbols`, which drops externs. Reading a fact from `env`
as well as `decls` does not help when two sites disagree about what the fact
*means*. `import stdlib.bits` then `bit_or(3, 5)` failed in the REPL, while
`bits.bit_or` typechecked there and failed to compile — no spelling worked.

**So: a question about a module's surface has one definition, and every site
consumes it.** `ast.decl_value_scopes` / `ast.NameScope` is that definition for
"which names does an importer see, and under what spelling"; `ScopeGlobal` is the
extern rule from spec-v0 §"Externs are outside the module system", including its
transitive reach. `bundler` and `module_loader` both derive their behaviour from
it rather than asserting it, and
`tests/stdlib/compiler/test_module_surface_agreement.spr` fails if they ever drift
apart again. See `docs/module-surface-authority-v0.md`.

The related question — "what does this module export?" — had the same shape and the
same fix. `parser.skip_export` discards the `export` keyword and `skip_visibility`
the `(..)` marker before either reaches the AST, so two independent raw-text line
scanners recovered them (`bundler.scan_source_info`, `repl.gather_exported_names`),
and the REPL's copy keyed on a `export ` line prefix — which an `extern fn` line
never has, so no extern was ever offered as a completion, from any module including
the prelude. `parser.scan_module_surface` is now the one definition. It is a token
scan rather than a parse (callers want a surface far more often than an AST) but
decides by CALLING the parser's own predicates, which is what removes the
text-prefix ordering hazard: the old scanner had to test `export type linear `
before `export type ` or it read the marker word `linear` as the type's name.

**When a fact is consumed by the parser and dropped, expect it to reappear as
re-derivation somewhere with worse tools.** Both of these did.

## Env-path type names are SHORT, and the marker families depend on it

On the env path a type is named by its short name — a module is checked with its
header stripped, so its own declarations are bare, and `prefix_pairs` qualifies
an aliased import's binding *keys* without touching the types inside their
schemes. **Every `@`-marker family is keyed on that short name:**

| marker | keyed by | read at |
|---|---|---|
| `@linear:<TypeName>` | short type name | `linear_check.head_type_name` |
| `@inst:<Class>:<head>` | short type head | typeclass dispatch in `infer` |
| `@class:`, `@type:` | short name (readers apply `after_last_dot`) | `infer` |

So **introducing module-qualified type names on this path breaks marker lookups
rather than failing loudly.** A canonical-naming attempt made an imported linear
type read as `stdlib.net.TcpConnection`, missing `@linear:` and reporting the
unrelated `` `borrowing` is only allowed on a parameter of a linear type ``; the
`@inst:` breakage would have been worse, since a module-load probe does not
exercise dispatch. If you need qualified names here, every marker family has to
move with them — treat that as the scope, not as a follow-up.

This is why an alias-qualified annotation (`bytes.Utf8Error`) is resolved by
*dropping* the known alias rather than by qualifying the scheme to match it.
A prefix that is not an import alias is left verbatim, preserving T7. See
`docs/repl-env-type-vocabulary-v0.md` §11.1.

## Driver diagnostic contract: stderr + nonzero exit

Anything in `stdlib/compiler/*_driver.sprout` that reports a problem must obey two
rules. Both are gated by `just diagnostic-stream-smoke` (fixtures in
`tests/diagnostic_stream/`).

**1. Diagnostics go to stderr; stdout is the artifact.** stdout carries LLVM IR
(`--emit-ir`, `--use-ir-codegen`), an encoded iface (`--emit-iface`), or documented
status lines (`--check-iface`). Callers redirect it to a file. A diagnostic on
stdout therefore does not merely look untidy — it *becomes* the artifact. Until
2026-08-07, `compile_driver` reported source errors via `print`, so the documented
dev loop in AGENTS.md

```
compile_driver_bin_stage1 --emit-ir stdlib f.spr | clang -x ir - runtime/*.c
```

turned a Sprout type error into a *clang* error quoting the Sprout error back:

```
error: expected top-level entity
    1 | 10:23: ERROR: check: Unknown variable: sprout_dss_undefined in function main
```

Use `report_error` (in `compile_driver.sprout`), never a bare `print`, for an error.
Bare `eprint` is correct only for a warning, which is a diagnostic but not a failure.

**2. A failed run exits nonzero.** Every `run_*` returns `Unit`, so the status is
carried by a write-1-only `Ref Int` threaded from `main` and returned as `main`'s
result — hence `fn main() -> Int !{IO}`. A `Ref` rather than a return value avoids
restructuring `run_batch`'s recursion into a fold, and write-1-only means a batch
keeps reporting every bad file while the status still survives to `main`.

**Why this went unnoticed for so long, which is the more useful lesson.** The whole
negative-test surface is *exit-status-blind by construction*: `_test-reject` runs the
driver as `2>&1 || true` and greps the combined text, because a fixture asserts a
specific *diagnostic*, and a bare nonzero status cannot distinguish the expected
rejection from a different one. That is the right design for those fixtures — but it
means 244 passing suites said nothing whatsoever about exit status, and
`just loud-fail-smoke` (which *did* check status) was structurally incapable of
firing and sat red on master unnoticed. When a signal is deliberately ignored
everywhere, add a gate that checks it *specifically*; do not assume broad coverage
implies it.

**Editing an older stage.** `_build-stage` checks both streams and captures the exit
status rather than letting `set -e` abort, so the seed bootstrap still works when
`in_bin` is a pre-2026-08-07 stage that reported on stdout and exited 0. Keep that
property if you touch it — it is what lets the seed be refreshed *across* a change
to the diagnostic contract without hand-building a compiler.
