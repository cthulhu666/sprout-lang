# Every String crossing into Sprout is headered — v0

**Status — approved; steps 1 and 2 landed (§7).** Runtime-internal; no change to the language, the spec
or the extern ABI.
Builds on `docs/runtime-invariant-confidence-v0.md`, which made every String headered.

## 1. Problem

A Sprout String is a `char*` to NUL-terminated bytes with an 8-byte header at `payload-8`
(`SPROUT_HEAP_CSTR`, aux = byte length). `str_eq` and `sprout_cstr_byte_len` read the kind byte
and, when it says CSTR, trust aux as the length. A String without a header is misread whenever
its byte at `s-8` happens to equal the CSTR kind: a wrong answer, not a crash.

Only two places write the header: `sprout_gc_alloc_cstr` (arena) and `intern_string`
(permanent). Every runtime path that hands Sprout a String must go through one of them, and
nothing checks that it does. Inside the runtime a headered String, a C literal, a `strerror()`
result and a malloc'd buffer all have type `char*`, and any of them becomes a Sprout value by a
cast to an integer. d9bcd11c fixed exactly this: `read_file` built
`sprout_make1(Err, (long long)(uintptr_t)"null path")`.

A static review on 2026-10-29 found no violation today. The rule holds by habit.

## 2. Goals and non-goals

Goals:
- A runtime change that turns a headerless `char*` into a Sprout value fails the build in CI,
  whatever cast spelling or translation unit it uses.
- Under `SPROUT_GC_HDRCHECK=1`, every String that leaves the runtime is checked, in O(1),
  whatever type it lands in (`Err`'s payload is a type variable).
- No cost with HDRCHECK off.

Non-goals:
- Changing the extern ABI. Exported runtime functions keep their `long long` signatures.
- Changing compiled code. Literals are already headered (`ir_lowering.sprout`).
- Defeating deliberate bypasses (`memcpy` into a word, union punning, arithmetic that moves an
  address into a non-address integer type). The threat is an accidental cast, which is what
  d9bcd11c was.

## 3. Prior art

Every runtime below funnels a C string into the language through a copying constructor. They
differ in whether the type system enforces it.

| Runtime | Handle type | Constructor | Enforced by |
|---|---|---|---|
| OCaml | `value` = `intnat` | `caml_copy_string` | convention; out-of-heap pointers unsupported since 5.0 |
| Erlang NIF | `ERL_NIF_TERM`, an integer | `enif_make_string` | convention: "opaque type", API-only |
| CPython | `PyObject*`, distinct struct | `PyUnicode_FromString` (copies) | C type |
| Go cgo | `string` vs `*C.char` | `C.GoString` (copies) | Go type |
| Lua | none: values cross a stack | `lua_pushstring` (copies) | no handle exists |

Sources: ocaml.org/manual/5.2/intfc.html §22.4.4, §22.2.3; erlang.org/doc/apps/erts/erl_nif.html;
docs.python.org/3/c-api/unicode.html; pkg.go.dev/cmd/cgo; lua.org/manual/5.4/manual.html §4.1.

Sprout is in OCaml's position: the handle is an integer, so a C type alone cannot guard the
conversion. The design below types the source as CPython does, and checks the conversion with
the compiler's own view of the code.

## 4. Design

Three layers. The cast check (§4.3) is the one that closes the d9bcd11c class; the type (§4.1)
makes it precise; the exit check (§4.2) is the runtime backstop.

### 4.1 `SproutStr`, a type for headered Strings

```c
typedef struct { char* hdr_p; } SproutStr;
```

A struct, not a pointer typedef. Clang rejects assigning a `char*`, a literal or a `void*` to it,
and rejects `(SproutStr)raw`, as hard errors. A `struct Tag*` typedef only warns on `char*`
before Clang 22, and CI's Windows image ships LLVM 20; it accepts `void*` and explicit casts
silently.

Only `sprout_str_alloc` and `sprout_str_intern` construct one directly, plus the exit
`sprout_str_of_word_in` (§4.2). The other writers get theirs from those two:
`sprout_str_dup_slice`, `sprout_str_dup`, `sprout_str_adopt` and `sprout_str_register`. The
`char*` names (`sprout_gc_alloc_cstr`, `intern_string`, `sprout_gc_adopt_cstr`,
`dup_managed_cstr`, `register_cstr`) are thin wrappers over them until step 4.
A `SproutStr` is never NULL: a new writer given NULL calls `sprout_fail`. The wrappers
`intern_string(NULL)` and `register_cstr(NULL)` still return NULL until their callers move (§8).
The cast check (§4.3) rejects any other construction: a compound literal, an initializer list, a
write to `hdr_p`, or a pointer cast between `SproutStr*` and a pointer to another type but `void`.

Raw-buffer helpers keep `char*`. Today `alloc_cstr` (malloc) and `sprout_gc_alloc_cstr` differ
by one name segment, as do `dup_cstr` and `dup_managed_cstr`. After this they differ by type.

A String parameter typed `const char*` is never sent back to Sprout. About 40 exports take one
(`env_get`, `regex_*`, `tcp_*`, `repl_*`, `analysis_*_in_source`), and none re-emits it today. A
function that must re-emit one takes the parameter as `long long`, which matches the `i64` the
compiler already declares, and rebuilds the String with `sprout_str_of_word(long long)`. That
macro checks the header under HDRCHECK. A `const char*`→`SproutStr` constructor would let a
literal through.

### 4.2 One exit, checked under HDRCHECK

```c
static inline long long sprout_str_word_in(SproutStr s, const char* caller);
#define sprout_str_word(s) sprout_str_word_in((s), __func__)
```

The only conversion from a String to a Sprout value. Under HDRCHECK it checks that the kind byte
at `s.hdr_p - 8` is CSTR, and on failure aborts with the caller's name. The check is O(1). It
does not compare aux with `strlen`: exits like `map_nth_key` and `intern_string` returns hand
back existing, possibly long Strings, and an O(n) check there makes HDRCHECK mode quadratic. That
already happened once, and failed CI at a 389x ratio (`sprout_cstr_byte_len`'s comment). The
aux-versus-`strlen` check stays in `str_byte_len`.

The reverse, `sprout_str_of_word(long long)` (a macro over `sprout_str_of_word_in`), builds a
`SproutStr` from a word and runs the same check. It casts an integer to a pointer, which rule 1
does not see, so it is not an exit; the writer list of rule 3 names it.

Non-String pointers have their own exit, `sprout_ptr_word(const void*)`, for object handles,
vectors, closures, maps, tasks and channels. All of these are `static inline` in
`runtime/sprout_word.h`, a header shared by `sprout_runtime.c`, `sprout_scheduler.c` and
`sprout_poll.c`. The check (§4.3) exempts the two exits by that `file:name` only. It is shared
because the scheduler and poller TUs cast too (`sprout_scheduler.c` returns
`(long long)(intptr_t)` for scopes, tasks and channels). The header also holds `SproutHeapKind`,
`sprout_hdr_of` and `sprout_hdr_kind`, moved out of `sprout_runtime.c`.

With HDRCHECK off an exit costs one load and one branch. `sprout_gc_hdrcheck_on` caches its answer
in `g_sprout_hdrcheck`, which the header's inline `sprout_hdrcheck` reads, calling the function
only while the value is -1 (not read yet). The environment is read lazily, on the first query,
because tests `setenv` in `main`.

Some casts are not Sprout values at all. `arena_contains`, `region_find` and `region_find_slow` do
address arithmetic, and the epoll backend tags a token's low bit for the kernel. These functions
are listed in `runtime/ADDRESS_CASTS` as `file:name`, each with a reason, and reviewed the same way
as `APPROVED_BUILTINS`. An entry whose cast sits in one platform's `#ifdef` branch carries that
platform's tag: the AST holds only the host's branches, so the entry is checked on that host and
ignored elsewhere.

An address must not become a word, whatever route it takes out of the function: a return, an
out-param, a field store, a call argument or a global. So `uintptr_t` and `intptr_t` are the
address types, and the check enforces two rules:
- every pointer-to-integer cast in a listed function produces a `uintptr_t`;
- nowhere outside the exits may an address-typed value be converted to a signed integer of 64
  bits or more (`long`, `long long`, `int64_t`), whether the conversion is explicit or implicit.

A `uintptr_t` stays an address until it reaches a comparison, arithmetic or a pointer. A listed
function also may not return a 64-bit integer or take a pointer to one. The kqueue timer id used
to be the Task pointer, written out as a `long long`. It is now a `long long` counter.

A third exit, `sprout_addr(const void*) -> uintptr_t`, was rejected. It would hand out addresses
under the address type, and an exit has to produce the word type. The rules leave one route open:
arithmetic that moves an address into a non-address type, e.g. `(size_t)(a - 0)`, and from there
into a word. That takes intent, not an accident, so it stays with the other deliberate bypasses in
§2's non-goals.

### 4.3 The cast check, on clang's AST

`just runtime-word-casts` runs `clang -fsyntax-only -Xclang -ast-dump=json` over every TU in
`runtime/`, and fails on any of:

- a cast whose `castKind` is `PointerToIntegral` outside the bodies of `sprout_str_word_in` and
  `sprout_ptr_word` and the `ADDRESS_CASTS` functions (§4.2), or one inside a listed function
  that does not produce `uintptr_t`;
- an address-typed value converted to a signed 64-bit integer outside the exits (§4.2);
- a call to `sprout_ptr_word` whose argument, with pointer-to-pointer casts and parentheses
  stripped, has at any layer the type pointer to `char`, `signed char` or `unsigned char`, any
  qualifiers (`"x"`, `(void*)"x"`, `s.hdr_p`);
- outside `sprout_str_alloc`, `sprout_str_intern` and `sprout_str_of_word_in`: an initializer
  list or compound literal of type `SproutStr`, and any use of `hdr_p` other than a read (an
  assignment, `&`, `++`, a compound assignment). `s.hdr_p[i] = c` and `memcpy(s.hdr_p, …)` read
  the field and pass (§4.1);
- outside the same three: a `BitCast`, implicit or explicit, between a pointer to `SproutStr`
  (typedefs resolved, any depth) and a pointer to any type but `SproutStr` or `void`, in either
  direction. `*(SproutStr*)&raw` would build a String from a `char*`; `*(char**)&s = raw` would
  write `hdr_p`. `void*` is exempt, so `malloc` into a `SproutStr*` and `memcpy` between two
  `SproutStr` pass.

A text match cannot do this. The tree spells pointer-to-word casts at least four ways:
`(long long)(uintptr_t)p`, `(uintptr_t)p` stored into tuple words, `(long long)(intptr_t)p`, and
a bare `(long long)p`, which clang accepts silently at equal width. Meanwhile `(long long)len`
and its kin are integer casts, and only types can tell the two apart. The AST has the types.
CI's Linux job already installs clang (`.github/workflows/ci.yml`).

Every pointer-to-integer conversion needs an explicit cast. Since Clang 15, an implicit one is a
hard error (`-Wint-conversion`), whether it is a return, an initializer or an argument, and no
build flag here turns that off. So rule 1 also sees an export that would return a `char*` as its
`long long`. No such export exists today: every runtime function that returns `char*` is
`static`.

Cost, measured on macOS: the dump of `sprout_runtime.c` is 132 MB and takes about 1 s, mostly the
system headers. The scheduler and poller dumps are 8 MB and 4 MB.

After this, d9bcd11c's line fails the check: it is a `PointerToIntegral` cast outside an exit.
Rewritten as `sprout_ptr_word("null path")` it fails the second rule. And `sprout_str_word`
needs a `SproutStr`, which a literal cannot become.

Not seen: a zero-initialised `static SproutStr` (it has no initializer list), `memcpy` or a union
into a `SproutStr`, a `char*` laundered through a `void*` variable before `sprout_ptr_word`, and a
`char*` laundered through `void*` into a `SproutStr*`.

### 4.4 Exported signatures stay `long long`

The machine ABI would allow a `SproutStr` return. A one-pointer struct comes back in x0
(AAPCS64, Apple arm64) or rax (SysV x86-64, MS x64), the same register as a pointer. But on SysV
x86-64, clang types that return as `ptr` in LLVM IR, while the compiler declares `i64`. On arm64
and MS x64 it is `i64`. `scripts/link_whole_program.sh` links both into one module, and
`llvm-link` accepts the mismatch without an error.

Measured with a two-module probe at `opt -O2`:
- With a matching `ptr` declare, the call inlines and folds to a constant.
- With an `i64` declare, it stays a call.

Exported functions therefore build a `SproutStr` and return `sprout_str_word(s)`.

### 4.5 ASan as a second net

`just build-stage2-asan` builds an ASan compiler and runs nothing (`BACKLOG.md`). Run with the
suite under it, `sprout_hdr_of` reading `payload-8` of a malloc'd or stack buffer hits the
buffer's left redzone: a deterministic report at the first consumer, naming the allocation. A
literal is best-effort only. ASan gives globals trailing redzones and no leading ones, so `lit-8`
is reported only when an instrumented global happens to sit just before the literal. For
literals, the deterministic net is `sprout_str_word` under HDRCHECK. ASan also misses libc's
static buffers (`strerror`) and paths no test exercises. So it complements §4.3 rather than
replacing it, and it catches every other out-of-bounds read in the runtime.

## 5. Rejected

- **A sweep-time check of `s` fields.** Ctor field codes mark String fields `s`, but a
  type-variable field is `_` (`Err`, `Just`, `Cons`). Records are not registered at runtime, and
  tuples and vectors carry no codes. It would have missed d9bcd11c.
- **Typed sinks** (`typedef struct { long long w; } SproutWord;` for `sprout_make*`, the handle
  macros and the GC local push). That would make d9bcd11c a compile error with no external tool.
  But the sinks also take objects, so it needs an object-to-word constructor. If that takes
  `void*`, a `char*` passes it, and the hole is back. If it takes a distinct type for each heap
  kind, every allocator and every sink call site in the runtime changes. Both designs check the
  static type at the call; a `char*` first assigned to a `void*` passes either. §4.3 gets that
  guarantee without changing the sinks.
- **A text grep for raw casts.** It cannot tell pointer casts from integer casts (§4.3).
- **Checking only at the consumers** (`str_eq`, `str_byte_len` under HDRCHECK). That exists
  today. It needs the 1-in-256 byte match, and fires far from the producer.

## 6. Scope (measured 2026-10-29)

These counts are grep estimates; step 1's baseline replaces them with the check's own count. The
cast count is a lower bound: it misses `uintptr_t` array stores, which §4.3 catches.

- 62 entry points hand Sprout a String-carrying value: 49 `extern fn`s, 10 `analysis_*`
  intrinsics and 3 `_unboxed` functions.
- 45 static helpers build String-carrying values.
- The header writers have 72 call sites in `sprout_runtime.c`, not counting their 11 prototype
  and definition lines.
- About 69 String-to-word casts in 42 functions, and 72 more pointer-to-word casts that are not
  Strings.

## 7. Plan

Each step is its own commit and keeps every gate green.

1. ~~**The check, red on a mutant.**~~ Landed: `just runtime-word-casts` (rule 1; rules 2 and 3
   landed with step 2), `just test-runtime-word-casts`, `docs/gates.md` §Runtime word casts.
   - Add `runtime-word-casts` with a baseline file listing today's violations, keyed
     `file:function:count`. Line numbers are not used, because the runtime is append-mostly
     (AGENTS.md, Docs & Spec 6). It fails on any violation not in the baseline, on any count
     that rises, and on any entry that no longer occurs, so the list can only shrink, as
     `stdlib-reference` works.
   - Add `runtime/ADDRESS_CASTS` (§4.2), whose entries stay.
   - Prove it catches the bug: a test copies the runtime, plants
     `return (long long)(uintptr_t)"x";` in an exported String producer, and expects the check to
     fail.
   - Green on the current tree before it joins `ci-fast-gates`.
2. ~~**`SproutStr` and the exits.**~~ Landed: `runtime/sprout_word.h`, the `sprout_str_*` writers
   in `sprout_runtime.c` (the `char*` names wrap them), rules 2 and 3 with mutant cases in
   `scripts/test_runtime_word_casts.sh`, and `tests/c_runtime/str_writers.c` (a headerless
   `SproutStr` aborts at both exits, a writer given NULL or an arena String fails).
3. **Convert, one module per commit.** ~~fs~~ (landed: `read_file`, `write_file`, `fs_list_dir`,
   `fs_read_bytes`, `stdin_read_bytes`, `read_int_lines`, and `sprout_err1_text`, which builds the
   `FsError` payloads and is shared with http, crypto and tcp;
   `tests/c_runtime/fs_io_strings.c`), net, http, ~~crypto~~ (landed: `crypto_sha256`,
   `crypto_hmac_sha256`, `crypto_base64_encode`, `crypto_base64_decode`, `crypto_bytes_xor`,
   `crypto_random_bytes`; `crypto_base64_decode` takes the String as a word),
   ~~bytes~~ (landed: `bytes_empty`, `bytes_slice`, `bytes_append`, `bytes_singleton`,
   `bytes_from_utf8`, `bytes_to_utf8`, and the `bytes_builder_*` constructors;
   `tests/c_runtime/bytes_crypto.c`, also under `SPROUT_GC_STRESS=1`), ~~terminal~~ (landed:
   `term_read_key`, `term_key_utf8`, `term_read_line`, `term_read_avail`, `term_input_failed`;
   `tests/c_runtime/term_strings.c`), ~~strings~~ (landed: `int_to_string`, `double_to_string`,
   `char_to_str`, `str_concat`, `string_concat_many`, `string_join_newlines`, `str_slice`,
   `str_slice_bytes`, `str_split_lines`, `split_words`, and `sprout_err_string_result`, which
   builds the `Err(String)` of the analysis entry points; `tests/c_runtime/str_strings.c`, also
   under `SPROUT_GC_STRESS=1`), json, analysis, maps. The scheduler's entries went with its
   generation-checked handles; poll has none. Each commit shrinks the baseline.
   `sprout_interned_word` (an interned String as a word) sits beside `sprout_str_intern` for the
   later groups.
   `SPROUT_GC_HDRCHECK=1 just test` includes `tests/stdlib/test_byte_offset_cost.spr`, so the cost
   of a new exit check shows up. The `test` recipe does not set HDRCHECK itself; CI sets it in the
   job environment.
4. **Finish.** Delete the `char*` writer names and the empty baseline. `ADDRESS_CASTS` stays.
5. **ASan in CI.** Make `build-stage2-asan` run the C runtime tests and the suite (§4.5).
6. **`check-approved-builtins` sees every builtin Sprout can call.** Its regex matches only
   `^long long name(`, so `_Bool str_eq` and the `SproutUnboxed2 *_unboxed` builtins are
   invisible to it. Matching every non-static C definition would instead flag cross-TU internals
   (`sprout_poll_init`, `scheduler_park_on_fd`, `sprout_roots_new`). Build the set from the Sprout
   side: every stdlib `extern fn`, plus the runtime declares in `ir_lowering.sprout`.
7. **Stale comments.** `s` is String only, and Char is an `i64` codepoint (`i`), compared with
   `icmp`, never `str_compare`. Fix every comment that calls Char a String:
   - the `CtorMeta` `field_kinds` comment;
   - in `sprout_ir.sprout`: the IRStrCompare comment ("String/Char ordering", "treats Char as a
     single-codepoint String"), `'s' (String AND Char)` and `'s'=String/Char`;
   - in `ir_lowering.sprout`: the two "String/Char handles" comments on the `str_compare` path.

   The last two files are under `stdlib/compiler/`, so this step reseeds.

Gates per step: `just c-runtime-test`, `SPROUT_GC_HDRCHECK=1 just test`, `just ci-fast-gates`,
`just linux-smoke`, `just check-approved-builtins`. Only step 7 reseeds.

## 8. Contract changes

- **`register_cstr`.** It returns any arena String it is given unchanged, and calls `free()` on
  anything else, so a literal or an interned String passed to it would be freed. The new writer
  takes only an owned malloc'd buffer and drops the arena branch. The premise is that no caller
  passes an arena String. All 11 callers pass malloc'd buffers today: getline, malloc, the JSON
  parser, `sprout_json_extract_string`, the regex error path and http. A C test pins that the new
  writer rejects an arena String. The `register_cstr` wrapper keeps the arena branch until step
  4 deletes it.
- **`intern_string(NULL)` and `register_cstr(NULL)`** fail instead of returning NULL (§4.1).
  Each caller's NULL branch is checked for what it meant before the change lands.
- **The sqlite FFI shim** (`examples/twelve_dollar_server/src/sqlite_ffi.c`) is outside
  `runtime/`. It passes raw `""` and sqlite text to `str_concat`, which works because
  `str_concat` uses `strlen`. The check does not cover it. Moving it to the shared exits is
  that example's own change.
