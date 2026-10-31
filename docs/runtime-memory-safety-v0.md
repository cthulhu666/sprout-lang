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

**Landed:** `just runtime-warnings` (`docs/gates.md` §Runtime warnings), in `ci-fast-gates`. The
`-Werror=switch` sentence below is still open.

A gate compiles `runtime/*.c` with `-Wall -Wextra -Werror` and runs in `ci-fast-gates`. Gate only:
user builds (`analysis_service_driver.sprout`) compile the runtime themselves, and a
warning a newer clang adds must not break them. The gate applies `-Werror` only on the clang major
CI pins, and warns on any other, so a newer local clang cannot fail it. Fold in `-Werror=switch`
from **C exhaustiveness for heap-kind dispatch** when that entry lands.
**Done when:** the gate fails on a planted unused variable and passes on master.

### Stage 2 — move pure builtins into Sprout

The only stage that shrinks the surface instead of checking it. Sprout code cannot write a header,
miss a root or mistype a signature: the compiler does all three, once, for every function. The
moves run beside the other stages, one builtin per PR. It comes before the checks because it is the prevention, and every builtin moved is one fewer
signature for stage 6. It does not replace them: 97 of the 168 builtins stay C whatever it
achieves, and stages 3 and 5 report a rooting bug a move exposes at the bad read.

`runtime/APPROVED_BUILTINS` holds 168 names. By name prefix (2026-10-31):

| Group | Count | Fate |
|---|---|---|
| OS and effects: `fs_`, `tcp_`, `term_`, `proc_`, scheduler, `analysis_`, … | 82 | stay C |
| Compiler ABI: `sprout_alloc_*`, `sprout_tag`, `sprout_gc_*`, `ref_*` | 15 | stay C |
| Data-structure primitives: `bytes_*`, `vector_*`, `map_*` | 40 | per item |
| Pure helpers: `str_*`, `char_*`, number/JSON format, `regex_*`, `crypto_*` but random | 31 | per item |

**Rule: no move may be slower.** Each move benchmarks the public function on unchanged master
and on the branch, at least 15 rounds each, interleaved one row per round as in
`bench/results-2026-10-18-native-runtime.md`. Spread is (median − min) / min, as there. A move
lands only if, for every row, the branch's median ≤ master's median × (1 + master's spread). A
compiler-called builtin also adds a self-compile row, and every row covers the slowest input the
Sprout form has, not only a typical one. Otherwise the builtin stays, and the numbers become its recorded reason. Each move deletes the
C function, its `extern fn` and its `APPROVED_BUILTINS` line, and ports or deletes its calls in
`tests/c_runtime/`.

#### Inventory (2026-10-31)

Each of the 71 is a primitive other code builds on, a builtin the compiler emits itself (`str_eq`,
`str_concat`, `str_compare`, `string_concat_many`, `int_to_string` with 272 call sites in
`stdlib/compiler/`, the `_unboxed` twins), or a helper. The helpers fall into three tiers.

**Movable now**, with existing builtins only, cheapest first:

| Builtin | Sprout form | C lines |
|---|---|---|
| `regex_is_match` | `regex_find_match` found something (see notes) | 12 |
| `regex_escape` | byte scan, `str_slice_bytes`, `string_concat_many` | 17 |
| `bytes_builder_u16_be`, `_u32_be` | `stdlib.bits` + `bytes_builder_byte` | 34 |
| `bytes_builder_byte` | `bytes_builder_bytes(bytes_singleton(v))` | 10 |
| `crypto_hmac_sha256` | `crypto_sha256`, `bytes_append`, `crypto_bytes_xor` | 41 |
| `json_stringify` | match on `Json`, byte-scan escape | ≈ 167 |
| `str_starts_with` | `str_starts_with_at_byte(s, 0, p)` | 7 |
| `split_words`, `str_split_lines` | byte scan, `str_slice_bytes` | 69 |
| `char_to_str` | `char_to_string(char_from_codepoint(cp))` | 23 |
| `vector_empty`, `bytes_empty`, `bytes_builder_empty` | one call to another builtin | 3–6 each |

Notes:
- `regex_find_match` does more than `regex_is_match`: two codepoint counts up to the match end, a
  `Match` and a `Just`. It also panics on a `regexec` error other than no-match, where
  `regex_is_match` returns False. A late match in a long text is its slowest row.
- `json_stringify` writes floats with `%.17g`; `double_to_string` keeps the first of `%.15g` to
  `%.17g` that reads back. So its output changes (`0.1` prints `0.1`, not `0.10000000000000001`).
- `bytes_builder_u16_be` and `_u32_be` have no caller in this repo but are used downstream.
- The seed declares `bytes_builder_u16_be`, `_u32_be`, `bytes_builder_byte`, `str_starts_with`,
  `split_words`, `str_split_lines`, `char_to_str`, `vector_empty`, `bytes_empty` and
  `bytes_builder_empty`: moving any of them needs a reseed.

**Blocked on a missing primitive.** Each is the only kind of new builtin this stage may propose
(AGENTS.md §Builtin vs Stdlib), ranked by what it unlocks:

1. An O(1) bounds-checked byte read on a `String`. Today every Sprout byte scan copies the String
   into `Bytes` first (`str_char_at_byte` was removed). Unlocks `str_len`, `str_char_at` and its
   twin, `str_slice` and `str_find`, all called from the compiler, and makes the scans above
   copy-free.
2. `unsafe_pure` for the prelude: a compiler change (`source.unsafe_pure_users` lists only
   `stdlib.linvec`), not a builtin. Unlocks `vector_set`, `vector_append`, `vector_concat`,
   `vector_from_list`, `vector_get` and its twin.
3. `bytes_concat_many(List Bytes)`, with `Builder` turned into a Sprout type (it is one of the
   compiler's built-in types in `bundler.sprout`). Replaces the 7 builder builtins with 1.
4. A byte buffer with inline byte access (`docs/byte-buffer-v0.md`, a proposal). Unlocks base64,
   `crypto_bytes_xor` and `crypto_sha256`, measured at 22–55× C without it.
5. An unchecked Bytes→String, private to stdlib, for `bytes_to_utf8`, `char_to_string` and
   `int_to_string`. A safety hole if it leaks: a String must be valid UTF-8 with no NUL.
6. Int↔Char cast intrinsics, for `char_from_codepoint`.

**Stays C**, with its reason:
- `regex_validate`, `regex_find_match`: libc's regex engine. `regex_replace_all_literal` for now:
  a Sprout loop over `regex_find_match` would recompile the pattern per match. All five `regex_*`
  move together if a Sprout engine passes the rule: [regex-engine-v0.md](regex-engine-v0.md).
- `double_to_string`: the first of `%.15g` to `%.17g` that reads back, over libc `snprintf` and
  `strtod`.
- `map_*`: the AVL tree behind `Dict` and `Set` (`dict_get` has 332 compiler call sites). A Sprout
  port "keeps its cost and adds Sprout's" (`docs/native-sprout-runtime-v0.md` §3.4).
- The representation primitives and the compiler-emitted builtins listed above.

Steps:
1. ~~Inventory~~ — above.
2. **Move the movable helpers**, one per PR, under the rule. Absorbs **Reimplement
   `json_stringify` in Sprout**.
3. **Propose the primitives** in the order above, each with its own design note.

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
