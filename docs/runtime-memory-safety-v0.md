# Runtime memory safety — roadmap — v0

**Status — proposed.** Runtime and tooling only; no change to the language or the spec. Each stage
gets its own design note or PR; this doc fixes the order and what each stage must show.

## 1. Problem

One bug class keeps returning: **compiled code and the C runtime disagree about memory.** Its forms:

- a read of an object the GC already swept (a missed root);
- a read past the end of an object, or of a String without its header;
- a C definition whose signature differs from the `declare` the compiler emits
  (`_Bool`-vs-`i64` returns; `native_set_to_list` returning `Nil` under a CPR width-3 `sret`).

Each has been fixed one instance at a time. `docs/string-header-invariant-v0.md` closed one form,
for one type. This roadmap aims at the class.

## 2. What exists

| Net | Catches | Blind to |
|---|---|---|
| `SproutStr`, `runtime-word-casts` | headerless String into Sprout | every other kind |
| `SPROUT_GC_HDRCHECK` | header/slot desync, bad String exits | reads of swept slots |
| `SPROUT_GC_LINEAGE` | swept OBJ read through `sprout_tag` | other kinds, other reads |
| `SPROUT_GC_STRESS` | missed roots, as wrong output later | where the bad read was |
| `gc_safety_check.sh`, `ir_verify` | rooting rules, statically | what the rules do not model |
| `check_extern_signatures.sh` | one C symbol per `extern fn` | the types of either side |
| ASan (`just test-asan`) | bad reads **in C** | inline IR loads; arena chunks |

Measured 2026-10-31:
- Sprout IR carries no `sanitize_address`, so ASan instruments none of it. A function given the
  attribute is instrumented when clang compiles the `.ll` (a load gains `__asan_report_load_n`).
- Most heap reads from compiled code still run in C: ADT access is a call to `sprout_tag` /
  `sprout_field`, as are `str_eq` and the `*_unboxed` reads. Those are instrumented today.
- The arena is reserved with `mmap(PROT_NONE)` (`arena_init_once`) and committed per chunk
  (`arena_chunk_alloc`). ASan tracks no allocation there, and nothing poisons it. An overflow
  region (`open_new_region`'s `malloc` fallback) is one block of many slots, so it has redzones
  only at its ends. Only a large object, `malloc`'d alone, has redzones around it.
- The runtime compiles with 0 warnings under `-Wall -Wextra` (local clang), and nothing enforces it.

## 3. Stages

Ordered by cost over reach, except stage 2, which runs beside the rest. Each stage names its
acceptance check; a stage without one is not done.

### Stage 1 — runtime warnings as errors

A gate compiles `runtime/*.c` with `-Wall -Wextra -Werror` and runs in `ci-fast-gates`. Gate only:
user builds (`analysis_service_driver.sprout`) compile the runtime themselves, and a
warning a newer clang adds must not break them. The gate applies `-Werror` only on the clang major
CI pins, and warns on any other, so a newer local clang cannot fail it. Fold in `-Werror=switch`
from **C exhaustiveness for heap-kind dispatch** when that entry lands.
**Done when:** the gate fails on a planted unused variable and passes on master.

### Stage 2 — move pure builtins into Sprout

The only stage that shrinks the surface instead of checking it. Sprout code cannot write a header,
miss a root or mistype a signature: the compiler does all three, once, for every function. The
inventory comes right after stage 1; the moves run beside the other stages, one builtin per PR.
It comes before the checks because it is the prevention, and every builtin moved is one fewer
signature for stage 6. It does not replace them: 97 of the 168 builtins stay C whatever it
achieves, and stages 3 and 5 report a rooting bug a move exposes at the bad read.

`runtime/APPROVED_BUILTINS` holds 168 names. By name prefix (2026-10-31):

| Group | Count | Fate |
|---|---|---|
| OS and effects: `fs_`, `tcp_`, `term_`, `proc_`, scheduler, `analysis_`, … | 82 | stay C |
| Compiler ABI: `sprout_alloc_*`, `sprout_tag`, `sprout_gc_*`, `ref_*` | 15 | stay C |
| Data-structure primitives: `bytes_*`, `vector_*`, `map_*` | 40 | per item |
| Pure helpers: `str_*`, `char_*`, number/JSON format, `regex_*`, `crypto_*` but random | 31 | move |

Steps:
1. **Inventory.** For each of the 71 in the last two groups, record: primitive (stays, other code
   builds on it) or helper; the primitives a Sprout version needs; and whether those exist
   (`bytes_builder_*`, `str_slice_bytes`, `unsafe_pure` from `docs/unsafe-primitives-v0.md`). A
   missing primitive is the only new builtin this stage may propose, under AGENTS.md §Builtin vs
   Stdlib, and only when it replaces more than one helper.
2. **Move helpers, cheapest first.** Each move deletes the C function and its `APPROVED_BUILTINS`
   line, keeps every test green, and measures the Sprout version against the C one on the
   workloads that call it. A regression is stated in the PR; a large one keeps the builtin, with
   the measurement as its justification. Absorbs **Reimplement `json_stringify` in Sprout**.
3. **Keep what must stay**, with a recorded reason: libc's regex engine, shortest round-trip
   `Double` formatting and the hashes are likely candidates, each decided by measurement.

**Dead runtime functions** removes C nothing calls, beside this stage.
**Done when:** every builtin in the last two groups is moved, or listed with its reason to stay.

### Stage 3 — poison and quarantine the GC arena

Runtime only: no compiler edit, no reseed. Under ASan:

- **Quarantine.** A swept slot is not pushed to the free list for N cycles; it stays poisoned and
  unused. Without this the collection that frees a slot hands it straight back
  (`sprout_alloc_obj_raw` collects just before it allocates), allocation unpoisons it, and a stale
  read passes or fails by allocation order. `SPROUT_GC_LINEAGE` is deterministic only because it
  never recycles; this keeps that property for every kind, bounded by N instead of leaking.
- **Quarantine is a header state, not a list.** The sweep writes a QUARANTINE kind with a
  countdown in aux, counts it down each cycle, and turns it FREE and pushes it at zero. With no
  side list, nothing points into a region after it is released. Two FREE invariants must learn
  the state: `sprout_fl_verify_against_full_walk` expects every FREE slot on a chain, so a
  QUARANTINE slot is not FREE; and Pass 2 releases a region with no live and no poisoned slots, so
  a quarantined slot counts like a lineage corpse (`poison_count`) and holds its region.
  `sprout_heap_lookup` rejects QUARANTINE as it rejects FREE.
- **Poison points.** The sweep (`fl_push_staged`), the vector tail carve
  (`sprout_vec_release_inline_tail`), chunk release (`arena_chunk_release`) and the arena past each
  region's bump. Unpoison at allocation and at chunk reuse (`open_new_region`).
- **Exempt readers.** The header and the free-list link (payload word 0) of a free slot are read by
  design: the sweep, the allocator, `sprout_heap_lookup`, the HDRCHECK walk and
  `sprout_fl_verify_against_full_walk`. Those reads go through `no_sanitize("address")` helpers, so
  the whole payload stays poisoned and a stale read of field 0 is caught. A `no_sanitize` helper
  cannot inline into an instrumented caller, so `sprout_heap_lookup` becomes a call in ASan builds.

**Done when:** a past rooting fix, reverted, is reported by ASan at the read site; and a planted
over-read inside an arena slot is reported.

### Stage 4 — poison freed task stacks

`sprout_ctx_destroy` keeps up to 16 stacks in `g_ctx_cache` for reuse, so a stale scan root into a
dead task's stack reads mapped memory. Under ASan, poison the stack on destroy and unpoison it on
reuse in `sprout_ctx_create`. This is the oracle **A missing frame pop has no executable oracle**
asks for. It also clears stale shadow before a cached stack is reused, which stage 5 needs.
**Done when:** deleting the exit pops in `ir_rooting` makes `test_frame_slot_rooting.spr` fail
under ASan.

### Stage 5 — ASan over inline IR loads

The compiler takes a flag that adds `sanitize_address` to every `define` it emits. `just test-asan`
builds the suites with it. Off by default: emitted IR and the golden snapshots do not change. This
reaches what stage 3 cannot: loads the compiler emits inline rather than as runtime calls.

Limits:
- **Green stacks are best-effort.** ASan's runtime intercepts `swapcontext` on glibc targets and
  clears the shadow of the target stack on each switch, so stack redzones of a parked task's frames
  vanish on Linux. macOS has no such interceptor; stage 4's unpoison on reuse is what prevents
  stale-redzone reports there.
- **Use-after-return off.** This stage adds `detect_stack_use_after_return=0` to `asan_opts`
  (today `detect_leaks=0:halt_on_error=1`): fake frames of dropped tasks are never reclaimed.
- **Stack depth.** Instrumented frames are larger, against 1 MiB task stacks.

**Done when:** `test-asan` is green with the flag on and every emitted `define` carries the
attribute; a hand-written `.ll` with the attribute and an out-of-bounds load is reported through
`_test-stdlib`'s link step; `asan-self-compile` and `test-stdlib-asan-compiler` show no new stack
overflows; and the clang-time and CI-time deltas are measured.

### Stage 6 — one source for the extern interface

**Compare each `extern fn`'s C definition against the emitted `declare`** first: derive the
expected C signature from each `extern fn` and fail on a mismatch. Then generate the C prototypes
from the declarations, so a mismatch cannot compile. Adjacent, not merged:
`docs/runtime-facts-v0.md` checks claims about C bodies, not their signatures.
**Done when:** reintroducing the `_Bool` return fails the gate.

### Stage 7 — measure, then decide

Each gets one measuring run; adopted only if it finds something the stages above do not.

- clang static analyzer over `runtime/*.c`: count of findings, and how many are real.
- libFuzzer + ASan on the C parsers that remain: `utf8_validate`, `base64_decode_bytes`, the
  regex builtins, the `sprout_json_extract_*` helpers. It reaches paths no test runs.
- `test-asan` in `just dod`: local cost against finding a failure before push.

## 4. Rejected

- **Typed handles for every heap kind**, as `SproutStr` did for Strings. Every allocator and call
  site changes (`string-header-invariant-v0.md` §5). Stages 3–5 catch the subset of misuse that
  runs under a test and lands in poisoned or redzone memory, for all kinds at once; the typed
  handle stays the total check for Strings only.
- **`-Werror` in user builds.** Ties user builds to the warning set of whatever clang they have.
- **Instrumenting compiled code first.** It is a compiler edit with a reseed and a golden cycle,
  and without poisoning it reaches no arena read; poisoning alone already covers every read that
  goes through the runtime.
