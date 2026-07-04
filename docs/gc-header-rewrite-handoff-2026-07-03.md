# GC header-rewrite handoff (2026-07-03)

**Status (updated 2026-07-04): PHASE 1 SHIPPED — PR #125 (branch
`gc-header-rewrite`, 5 commits). Central open question ANSWERED.** Current
sources of truth: the two `P1` GC items in `BACKLOG.md` (phase 1 done + phase 2
spec), the addendum in `docs/gc-profile-findings-2026-07-03.md` (before/after
numbers), and agent memory `project_gc_endstate_decision`. The sections below
are kept as design rationale; where they conflict with the above, the above
wins.

- **Phase 1 results** (compiler self-emit): peak footprint 420→322 MB (−23%),
  GC time −45% (locality), wall −21%; nqueens flat (vector-heavy, expected).
  Regression net: `tests/stdlib/test_gc_exact_size_objs.spr`.
- **Decision:** non-moving generational end state; Phase 2 = 1-word header +
  region/bump allocator + arena bounds-check (membership option 1), with ~4
  reserved GC bits (age/remembered/pinned) next to the color bits. Integer
  tagging (option 2) verified feasible but DEFERRED — it is the only path to
  moving/compacting and is orthogonal; field boxing (option 3) REJECTED
  (memory regression). Moving GC would additionally require an IR
  reload-after-trigger rewrite (roots are alloca-slot addresses; SSA uses
  never reload — `sprout_ir.sprout` documents this) regardless of tagging.
- **Write-barrier surface for the generational step:** `ref_write` +
  `vector_mutset` (the v1-draft "ref_write only" audit predates MutVec).
- **Scoping note:** with conservative bounds-check membership, precise
  scan-roots (the 2 codegen sites below) can be DEFERRED — range-testing
  aggregate words is safe. Phase 2 stays runtime-only, no seed refresh.
- The measured `miss_hop_frac` = 6.9%: the field_kinds scalar-skip idea is a
  minor win, not a lever — do not invest in it.

Tracked since 2026-07-04 (untracked by request during the design phase).

**Goal:** cut the per-live-object memory overhead (currently ~128 B for a
`Just(42)` that holds 16 B of data — an 8× tax) and, as a byproduct, delete the
`find_managed_ptr` hash table that dominates GC time.

**Isolation (OBSOLETE):** the `sprout_lang-gc-opt` worktree instruction no
longer applies — per Kuba (2026-07-03), work happens in the session's own
worktree. Phase 1 was done on branch `gc-header-rewrite` off master `ff9af93`
(which landed the 4.19M-bucket index resize as an explicit STOPGAP; Phase 2
deletes that table, and its ~32 MiB static cost, entirely).

---

## TL;DR — the decision and the recommended phasing (Phase 1 SHIPPED — PR #125; Phase 2 specced in BACKLOG)

The memory overhead has two independent sources; treat them as two phases so the
risky one (membership test) doesn't block the easy win:

- **Phase 1 — exact-size `SproutObj` (easy, runtime-only, ~2× memory, low risk).**
  Stop allocating a fixed 80 B blob for every ADT value; allocate `tag + arity`
  words. `Just(42)` payload 80 B → 16 B. Keeps `ManagedNode` and the hash table,
  so the conservative membership semantics are untouched. No `_`-field problem.
- **Phase 2 — inline the header + delete the table (hard; needs a membership
  solution).** Co-locate GC metadata with the payload so `find_managed_ptr`
  becomes pointer arithmetic and `g_heap_index` disappears. This removes the 48 B
  `ManagedNode` *and* the 296M-hop time cost — but it forces us to answer the
  central open question below.

Phase 1 delivers half the memory win with none of the Phase 2 risk. Ship it
first, measure, then decide Phase 2 scope.

---

## Evidence (all measured at shipped `-O2`, via `just gc-profile`)

### Time profile (why the table is the hotspot)
| workload | GC % of wall | cycles | avg probe len |
|---|---|---|---|
| compiler self-emit (`stdlib/compiler/compile_driver.sprout`) | **63%** (32.2s/51.4s) | 406 | **4.13** |
| `examples/nqueens.sprout` | 26% (2.2s/8.5s) | 33,256 | 0.30 |

Compiler op counts: `find_managed_ptr` 71.9M calls / **296.6M hops**; drain edges
70.8M; sweep visits 84.2M. `sample`: `sprout_gc_collect_with_reason` (inlines
mark/drain/sweep/find) is the largest leaf (~46% on-CPU).

### Causal test (proves chain length is the cost, not cache misses)
Changed **only** `g_heap_index[131071]` → `[4194301]`, nothing else, re-ran the
compiler profile:
| | 131071 | 4.19M |
|---|---|---|
| avg probe len | 4.13 | **0.74** |
| total hops | 296.6M | 53.5M |
| `gc_us` | 32.2s | **10.2s** (3.2×) |
| wall | ~51s | **24.0s** (2.1×) |

If the cost were cache misses on scattered `ManagedNode`s, `gc_us` would not have
moved. It dropped 3.2×. Chain length is causal. (This is why a *resize* would give
~2× speed — but it does nothing for memory, and the header rewrite subsumes it.)

### Memory accounting (the actual target)
- `ManagedNode` = **48 B** per object (`runtime/sprout_runtime.c:60`): `ptr, kind,
  aux_slots, marked, next, hash_next`. `ptr` is redundant under an inline header;
  `hash_next` disappears with the table.
- `SproutObj` = **80 B fixed** (`:34`): `tag + f0..f8`, regardless of arity. Max
  inline arity is 9.
- `Just(42)` = 48 + 80 = **128 B** for 16 logical bytes → **8×**.
- Fixed: `g_heap_index` = **1.0 MiB** static; `g_handle_table` = 16 KiB.
- Retention is **high-water-mark**: swept `SproutObj`s/`ManagedNode`s go to
  freelists (`g_sprout_obj_freelist`, `g_managed_node_freelist`), never returned
  to malloc. RSS reflects peak live set, not current.

---

## The enabler: this refactor is runtime-only

Codegen never GEPs into a `SproutObj`. It emits **calls**: `@sprout_make0..9` to
construct, `@sprout_field(handle, idx)` / `@sprout_tag(handle)` to read
(`stdlib/compiler/codegen.sprout:1177, 3705`; make/field/tag registered at
`:3340`+). The object layout is encapsulated in ~15 runtime C functions.

**Consequence:** as long as `sprout_field`/`sprout_tag`/`sprout_make*` keep their
`(i64, i64) → i64` contract, the emitted IR is byte-identical, the compiler is
untouched, and **there is no bootstrap-seed refresh and no codegen change**. The
only in-runtime cleanup is the spots that cast a handle to `SproutObj*` and read
`->fN` directly (e.g. `runtime:1726, 3405, 3415`). DoD: runtime change → GC-stress
+ full suite + example canary, but NOT the seed gate.

---

## Design: one 1-word header covers all 10 heap kinds

Per-kind metadata needs (what the GC must know to trace + free), from
`sprout_heap_child_count`/`_value` (`runtime:1023`, `:1052`):

| kind | trace children | variable metadata | self-describing? |
|---|---|---|---|
| OBJ | `arity` fields via tag→CtorMeta | **tag** | needs tag |
| CLOSURE | slots 1..n (slot 0 = code ptr, skip) | **n_caps** | needs count |
| TUPLE | `width` words | **width** | needs width |
| VECTOR | data[0..len] | — | len in payload |
| BUILDER | chunks[0..count] | — | count in payload |
| MAP | value,left,right | — | fixed 3 |
| REF | value | — | fixed 1 |
| BYTES/RANGE/CSTR | none | — | fixed 0 |

Only OBJ/CLOSURE/TUPLE carry variable metadata, always one small int → one word:

```
  63                              10   9 8 7        0
 ┌──────────────────────────────┬─────┬────────────┐
 │        aux  (54 bits)         │color│  kind (8)  │
 └──────────────────────────────┴─────┴────────────┘
   aux   = tag (OBJ) / n_caps (CLOSURE) / width (TUPLE); 0 otherwise
   color = GC mark state
```

Two layout variants, decided by sweep enumeration:
- **2-word header (16 B), keep malloc:** `{ info, next }`. Sweep walks the `next`
  chain (as today). `find_managed_ptr(p)` = `(Header*)p - 1`. Pragmatic; no new
  allocator. `Just(42)` → 16 B header + 8 B field = **24 B** (vs 128 B).
- **1-word header (8 B), region/bump alloc:** sweep walks arenas linearly, no
  `next`. `Just(42)` → **16 B** (OCaml-identical). Bigger (new allocator), and it
  also provides the arena bounds-check that Phase 2 needs (see below).

---

## ~~⚠ CENTRAL OPEN QUESTION~~ ANSWERED 2026-07-03: option 1 (arena bounds-check), tagging deferred — see status block. Original analysis kept below.

Deleting the hash table is not free, because `find_managed_ptr` is secretly a
**membership test**, not just a lookup. It is called on arbitrary i64s (object
fields, scan-roots, closure slots) and returns NULL for anything that isn't a live
allocation address — *safely, without dereferencing*. An inline header can't do
`p - 16` on an arbitrary integer (segfault / garbage read).

The problem is **`_` (type-variable) fields.** `CtorMeta.field_kinds`
(`runtime:86`, populated per-ctor by `stdlib/compiler/field_kinds`, emitted via
`@sprout_register_ctor` at `codegen.sprout:3198`) classifies each field:
`i`=Int `b`=Bool `s`=String/Char `p`=ADT/closure `_`=type-var. Under precise
tracing:
- `p` fields → definitely managed pointers → safe to deref header, **no test needed**.
- `i`/`b`/`s` fields → definitely not pointers → **skip** (also a free speed win;
  today the tracer tests *every* field via the hash — see note below).
- `_` fields → **unknown** at compile time (polymorphic: could be an unboxed Int
  or a boxed pointer). These are pervasive (List, Maybe, Dict over a tyvar) and
  are the *only* case that still needs a membership test.

Three ways to answer "is this `_` value a managed pointer?":
1. **Arena bounds-check** (Go/Immix style): "is the address inside the GC heap?"
   O(1) range test. Requires the region allocator → pairs with the 1-word variant.
   Still **conservative** (an Int equal to a heap address false-positives).
2. **Integer tagging** (OCaml `2n+1`): immediates carry a low-bit tag, so `_`
   self-identifies (`val & 1`). **Precise**, and the only path that enables a
   *moving/compacting* collector later. Cost: touches every integer arithmetic op
   in codegen. Largest change.
3. **Field boxing** (GHC): polymorphic `_` fields are always boxed, so `_` is
   always a pointer. Precise, no arithmetic change, costs an allocation per
   polymorphic scalar.

### Corrections to earlier framing (do not repeat these mistakes)
- **Regions do NOT enable compaction.** The arena bounds-check is conservative for
  `_` fields, same hazard as the hash today. Moving GC needs *precise* `_`
  identification → option 2 or 3, which is **orthogonal to regions**.
- **Sprout is already a conservative collector.** `find_managed_ptr` retains any
  object whose address a `_`-field integer numerically equals. This false-retention
  hazard exists in the *current* code; the header rewrite does not introduce it.
- **`field_kinds` is available but unused at trace time.** The tracer currently
  scans all `arity` fields through the hash (`sprout_gc_drain_marks` →
  `find_managed_ptr` per child). Consulting `field_kinds` to skip `i`/`b`/`s`
  fields is an independent, cheap win in *any* design — but measure the
  pointer-vs-scalar hop fraction first (add a counter); if most hops are `p`/`_`
  it buys little.

---

## Refactor surface

Phase 1 (exact-size SproutObj):
- `sprout_make0..9` / `sprout_alloc_obj_raw` / `sprout_init_obj` (`:712`–`:743`):
  allocate `8 + arity*8` bytes instead of `sizeof(SproutObj)`. `sprout_field`
  offset math is unchanged (`tag@0`, `f0@8`, `fN@8+8N`) — only the allocation size
  shrinks, so accesses stay in bounds (codegen only reads idx < arity).
- Freelist: `g_sprout_obj_freelist` (single, fixed-80) → per-size-class freelists
  indexed by arity 0..9. Free path (`sprout_gc_free_payload`, OBJ case ~`:1210`)
  needs arity at free time — get it from the tag via `find_ctor`.

Phase 2 (inline header + delete table):
- ~10 allocation sites prepend the header (unify into one
  `alloc(kind, aux, payload_bytes)`): obj, closure_env (`:746`), vector, bytes,
  builder, tuple_blob (`:915`), range, ref, cstr.
- `find_managed_ptr` → pointer arithmetic + the chosen membership test.
- Delete `g_heap_index`, `sprout_managed_index_insert`/`_remove`,
  `sprout_managed_ptr_hash`, `ManagedNode.hash_next`, `ManagedNode.ptr`.
- `sweep` (`:1214`): walk header `next` chain (2-word) or arenas (1-word).
- `sprout_heap_child_*`, `sprout_gc_free_payload`, `sprout_tag`, `sprout_field`:
  read metadata from the header.
- Make scan-roots precise (2 sites, both unboxed aggregates): `push_scan_root`
  (`codegen.sprout:887`, `val_is_tuple`) and `register_scan_root` (`:3319`, struct
  globals). The LLVM struct type at the emit site already encodes which words are
  pointers, so a precise root map is derivable — needed so the tracer never derefs
  a non-pointer aggregate word.

---

## Verification plan (TDD-first)
1. Add profiler counters for **max** probe length + pointer/scalar hop split
   (extends PR #120 instrumentation) → baseline the worst case before coding.
2. `SPROUT_GC_STRESS=1` is the correctness oracle (memory
   `project_gc_stress_oracle`): a rehash/header bug = missed mark = UAF. Default
   greens are false confidence. Run `just test-stress`.
3. Full suite (`mise exec -- just test`), example canary (tuples, factorial,
   maybe_map, typeclass_collections_demo, fizzbuzz — compile AND run), all example
   compile. Runtime-only ⇒ NO seed refresh, NO codegen change.
4. Re-run `just gc-profile` on the compiler + nqueens; confirm memory (RSS via
   `/usr/bin/time -l`) and, for Phase 2, that `find_managed_ptr` hops → 0.

---

## Rejected alternatives
- **Dynamic resize of `g_heap_index`** — proven 2× compiler speed, ~1 day, but
  ZERO memory change and thrown away once the table is deleted. It's a speed
  band-aid, not the memory fix Kuba asked for.
- **Inline-header-on-malloc without a membership solution** — awkward: leaves `_`
  fields with no safe validation. Either commit to a membership option (1/2/3
  above) or don't delete the table.
- **"Regions enable compaction"** — false (see corrections).

## References
- Profiler + findings: PR #120 (merged to master), branch
  `gc-profile-instrumentation`; `just gc-profile <file>`;
  `docs/gc-profile-findings-2026-07-03.md`.
- Memory: `project_gc_profile_findings_2026_07`, `project_gc_stress_oracle`,
  `project_root_once_coalescing_landed`, `project_cpr_width3_sret_abi` (unboxed
  tuple ABI — relevant to scan-roots), `feedback_refresh_seed_before_test_for_compiler_changes`
  (N/A here: runtime-only, no seed).
- Key files: `runtime/sprout_runtime.c` (structs `:34`/`:60`, index `:140`/`:578`,
  trace `:1023`/`:1052`, sweep `:1214`, alloc `:712`+); `stdlib/compiler/codegen.sprout`
  (field/make `:1177`/`:3340`, scan-roots `:887`/`:3319`, ctor reg `:3198`);
  `stdlib/compiler/field_kinds`.
