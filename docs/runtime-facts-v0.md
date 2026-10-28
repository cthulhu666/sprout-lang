# Runtime facts v0: one list of runtime-function facts, checked against the C

Status: **Proposed.** Compiler-internal. No syntax, typing or spec change.

## 1. Problem

The compiler needs facts about runtime functions: whether a call can trigger a collection,
whether it returns, whether it has an unboxed variant, and whether it can change a vector's
length. It does not read the runtime's C, so each fact is a hand-written list of names, in the
pass that needs it:

| list | fact | file |
|---|---|---|
| `is_nonallocating_read` | cannot trigger GC | `ir_rooting.sprout` |
| `is_nonallocating_unboxed_read` | same, for the `_unboxed` reads | `ir_rooting.sprout` |
| `is_noreturn_extern` | never returns | `ir_rooting.sprout` |
| `unboxed_maybe_extern_name` | has an `_unboxed` variant | `ast_to_ir.sprout` |
| the eight `i128` `decl` lines in `ir_header` | the `_unboxed` variants' signatures | `ir_lowering.sprout` |
| `safe_io_externs` | `!{IO}` but cannot change a vector's length | `bce.sprout` |
| `op_triggers_gc`'s arms for `IRStrEq`, `IRStrCompare`, `IRPrintStr`, `IRGetTag`, `IRGetField` | their C callees cannot trigger GC | `ir_rooting.sprout` |

Each entry is a claim about a C body, read by hand. Only the `declare` lines are checked against
C (`just cpr-declare-check`). A wrong entry is a use-after-free or a dropped bounds check, and no
test fails. A runtime function added or removed must be found in each list by hand: when
`linvec_set`, `linvec_push` and `linvec_truncate` left the runtime, nothing flagged their entries
in `is_nonallocating_read`.

The lists are also short. Rooting trusts 13 runtime functions not to trigger GC (the five vector
functions and the eight `_unboxed` reads). Reading the C finds 45 (§4.2). A call to any of the
other 32 roots every live heap value around it for nothing. §4.5 measures what trusting them buys.

## 2. Goals and non-goals

Goals:

- One hand-edited module, `stdlib/compiler/runtime_facts.sprout`, holds every fact in §1. The
  lists are deleted, and `op_triggers_gc` asks the module about the C symbols its ops lower to.
- A check derives the facts from the runtime's C and fails when the module claims one the C does
  not support, on every platform it runs on. It also prints the facts the C supports that the
  module does not claim.
- A name the module says nothing about keeps today's conservative answer: it may trigger GC, may
  return, has no unboxed variant, and may change a vector's length.

Non-goals:

- Generating the module. §9 says why.
- Inferring facts for Sprout functions. `ir_rooting.compute_alloc_summary` already does this over
  the call graph. It bottoms out on runtime facts, which now come from one checked module.
- Lowering decisions: `recognize_vector_op`, `recognize_string_builtin`, `is_call_site_intrinsic`
  and `is_bits_intrinsic` say what the compiler expands inline, not what a C body does. §7 leaves
  them for a separate change.
- Names the compiler emits or keys on for other reasons: `ast_to_ir` emitting calls to
  `char_to_string` and `list_append`,
  `ir_lowering.extern_skip_names`, BCE's pseudo-callees.
- `runtime/APPROVED_BUILTINS` and `scripts/check_extern_signatures.sh`.
- A `no_prelude` file whose top-level name matches a runtime symbol. Its define is bare, so the
  facts would be looked up for it, but it cannot link: the runtime defines the same symbol, and
  BACKLOG tracks the collision ("A `no_prelude` file whose top-level name matches a C runtime
  symbol dies with `duplicate symbol` at link").

## 3. Prior art

At a foreign-function boundary, other languages take the fact from a declaration the programmer
writes, and trust it:

- **OCaml:** `external … [@@noalloc]` on a C function that "doesn't allocate, doesn't raise
  exceptions, and doesn't release the domain lock" (manual, "Interfacing C with OCaml"). The
  compiler records it as `prim_alloc: bool` ("Does it allocates or raise?",
  `typing/primitive.mli`).
- **GHC:** a `foreign import ccall unsafe`, during which "garbage collection will never occur"
  (GHC user's guide, FFI chapter).
- **Rust:** an `extern "C"` function declared `-> !` never returns (reference, "Never type").

Inside one module, where the body is visible, LLVM derives such facts instead. Its
`function-attrs` pass walks the call graph bottom-up (`docs/Passes.html`) and sets `noreturn` in
`addNoReturnAttrs` (`llvm/lib/Transforms/IPO/FunctionAttrs.cpp`).

The Linux kernel's objtool sits between the two. It keeps a hand-written list of functions that
never return (`tools/objtool/noreturns.h`: "Yes, this is unfortunate. A better solution is in the
works."), and warns "`%s() missing __noreturn in .c/.h or NORETURN() in noreturns.h`" when a call
is followed by unreachable code (`validate_reachable_instructions`, `tools/objtool/check.c`). The
check finds a missing entry, never a wrong one.

This design keeps the declaration, as the FFI languages do, and checks it against the body, as
LLVM could. Unlike objtool, the check is aimed at wrong entries, which are the unsafe ones; a
missing entry costs only precision, and the check lists those too.

## 4. Design

### 4.1 The module and the check

`stdlib/compiler/runtime_facts.sprout` is ordinary compiler source: one `Set` per fact, and the
`_unboxed` symbols with their parameter counts for `ir_header`'s `declare` lines. An entry goes in
by a reviewed edit. Compiling a program needs neither clang nor the runtime sources.

`tools/check_runtime_facts.sprout`, run by `just runtime-facts-check`:

1. Compiles each `runtime/*.c` to LLVM IR with `clang -S -emit-llvm -O0 -Xclang
   -disable-O0-optnone`, once per configuration (§4.3), and runs `opt -passes=mem2reg`. Clang
   does the preprocessing, so macros and `#if` arms are resolved. Plain `-O0` output keeps every
   local and parameter in a stack slot and reloads it at each use, so two uses of one pointer are
   different values; `mem2reg` makes them one, which the length rule needs. `-O0` output carries
   `optnone`, under which `mem2reg` does nothing, hence the flag. The check uses bootstrap's clang
   floor (`scripts/check_clang_cpr.sh`).
2. Reads the three files as one program: the runtime calls across them, so a callee defined in
   another file is defined, not external. An `internal` define is keyed by its file, since two
   files may each have a static of the same name.
3. Derives the facts (§4.2).
4. Fails if any entry in the module is not derived, in any configuration, naming the entry and
   the reason (for a GC fact, the call path that reaches the collector).
5. Prints the derived facts the module lacks, as candidates. These never fail the check.

### 4.2 Rules

A runtime function is a `define` without `internal` linkage. The rules read the IR by structure:
a call's callee is the first `@name(` or `%name(` after the return type, whatever the type's
parentheses say (`call i32 (ptr, ptr, ...) @fprintf` is a direct call), and a field access is a
`getelementptr` matched on its source type and indices, whatever its flags (clang emits
`getelementptr inbounds nuw` for struct fields).

**Runs other code.** Both the GC and the vector-length rule turn on one question: can the call
run code the check cannot see? A function runs other code if it can reach:

- an indirect call (a closure or callback pointer), or inline assembly;
- an external function (declared, not defined) not on the check's library list.

`llvm.*` intrinsics are not externals: they lower to instructions or to a library routine
(`llvm.memcpy` to `memcpy`) and run no other code. They count only for the vector-length rule.

The library list holds libc and OS functions that never run Sprout code and then return to their
caller: `malloc`, `memcpy`, `fprintf`, `strlen`, `getenv` and the like, about 70 today. The premise
is "and then return": `exit` runs the `atexit` handlers, one of which is `sprout_gc_collect`, but
nothing resumes after it (premise 7). `swapcontext`, `makecontext` and `SwitchToFiber` switch
green threads and run other tasks, so they are not on the list. Nor is any function that takes a
function pointer (`qsort`, `pthread_create`, `signal`, `atexit`, `SSLSetIOFuncs`). A missing
library name makes its callers run other code, which costs precision and never soundness. With
`_FORTIFY_SOURCE` (on by default on macOS) clang emits `__memset_chk` and `__memcpy_chk` for
`memset` and `memcpy`; the list holds both spellings.

**Escaping functions.** A function whose address escapes (passed to a call, stored, or named in a
global initializer, which includes `llvm.global_ctors` and `llvm.global_dtors`) can be entered
from outside any call the check follows: a signal, another thread, an exit handler, a TLS
callback. Each escaping function must itself be one that cannot trigger GC and runs no other
code, or appear on the check's list of reviewed exceptions. A new escaping function fails the
check until it is one or the other. The rule finds handlers by their address, not by the
registering call, so a handler stored into a `struct sigaction` field is found too. The
exceptions today, each with the reason it is sound:

- `sprout_gc_collect`, passed to `atexit`: it runs inside `exit`, after which nothing resumes
  (premise 7).
- `task_trampoline` and `pump_loop`, passed to `sprout_ctx_create`: they run Sprout code, but are
  entered only by a context switch, and `swapcontext`/`SwitchToFiber` already count as running
  other code at every call that can switch.
- `sprout_scheduler_init`, a constructor: it creates the pump's context (`sprout_ctx_create`,
  which reaches `makecontext`) without switching to it, and runs before `main`, when there is no
  Sprout frame to root.
- `sprout_ignore_sigpipe_ctor`, a constructor: it calls `signal`, which is off the library list
  because it takes a handler; it installs `SIG_IGN` and runs before `main`.

The handlers that pass the rule as it stands include `sprout_crash_handler`,
`term_winch_handler`, `dns_resolver_thread`, `tls_read_func`/`tls_write_func`, the `atexit` and
destructor reporters, and the cleanups `scheduler_set_park_cleanup` stores. Two `atexit` handlers
pass only through the library list: `sprout_close_analysis_service` (`fclose`, `waitpid`) and
`term_restore_on_exit` (`tcsetattr`).

**Cannot trigger GC** unless it can reach `sprout_gc_collect` or `sprout_gc_collect_with_reason`,
or runs other code.

**Never returns** is a fixpoint. For this rule a block ends at its first call to `exit`, `_exit`,
`abort` or a function already found never to return; what follows the call is dead. A function
never returns when every path from its entry reaches such a call. Clang emits `unreachable` after
a call only when the callee carries `noreturn`, so a caller of an unannotated wrapper around
`exit` gets the call followed by `br`; cutting the block handles both. LLVM's `noreturn`
attribute is not enough on its own: it is also set on a body that ends in `unreachable`. On the
current runtime the set is `panic`, `sprout_fail`, `sprout_abort_match` and the six `repl_*`
stubs, which all call `sprout_fail`.

**Has an unboxed variant** when the runtime defines `<name>_unboxed` returning `i128`
(`SproutUnboxed2` is `unsigned __int128`) with `<name>`'s parameter count. x86_64 clang ≤ 21
lowers `__int128` as `{ i64, i64 }` (`ci.yml`); the clang floor in §4.1 rules that out.
`ast_to_ir` still requires the call's type to be a `Maybe` and its arms to be simple, as
`maybe_unboxable` and `all_arms_simple_maybe` do now. `ir_header` emits a `declare` for each
entry, so the routing and the declarations cannot disagree.

**Cannot change a vector's length** unless it runs other code or can reach a function that writes
`VectorVal.len`. The rule follows calls, as the GC rule does. The runtime passes vectors as
`long long` handles, so `f(long long h) { vector_push(h, 0); }` compiles to a bare
`call @vector_push(i64 %0, …)` with no `VectorVal` in sight; only the call gives it away. `len`
is field 0, at offset 0. A function writes `len` when it has:

- a `store` to `getelementptr %struct.VectorVal, ptr %b, i32 0, i32 0`, or to a vector base
  itself;
- a call to `llvm.memcpy.*`, `llvm.memmove.*`, `llvm.memset.*`, their libc names or their
  `__*_chk` names, whose destination is a vector base or an `inttoptr`;
- a `store` at a constant offset in `[0, 8)`, or a non-constant offset, from an `inttoptr` of
  any integer or from a vector base, unless it goes through a field GEP of a struct other than
  `VectorVal`. This catches a word-0 write through a raw handle (`((long long*)h)[0] = n`), which
  names no type.

A *vector base* is the base pointer of any `getelementptr %struct.VectorVal` in the function,
followed through `phi`, `select` and all-zero GEPs. A base is identified by where it comes from:
every `inttoptr` of one integer, and every load from one stack slot, is the same base. `mem2reg`
does not give that on its own: `SPROUT_GC_PUSH_I64_LOCAL` takes a local's address to root it, so
the local stays in memory and is reloaded after each allocation, and two casts of one handle stay
two values.

A header write at a negative offset is not a write to `len`: `sprout_hdr_write` writes
`(char*)v - 8` for every allocation, and counting it would make every allocator change lengths.

The walk does not enter `sprout_gc_collect_with_reason`. That is the rule's one exemption, and it
rests on premise 11: the collector writes only objects that are already dead (word 0 of a freed
payload, headers at −8) and runs no Sprout code. Without it every function that may collect would
change lengths, since `sprout_gc_sweep` treats a dead payload as a `VectorVal` under HDRCHECK and
then writes its word 0. With it, `ref_new`, which may collect, keeps lengths.

An awk prototype of the GC and a simpler length rule, over the current runtime, agrees with every
hand-written entry but one:

- Cannot trigger GC: the five vector functions and the eight `_unboxed` reads. Can: `vector_get`
  (it builds a `Just`), `ref_new`, `vec_make_filled`, and `task_yield`, `__task_await` and
  `__chan_send` (they switch tasks).
- 32 more declared externs cannot trigger GC, among them `str_len`, `str_byte_len`,
  `str_starts_with_at_byte`, `str_find`, `ref_read`, `ref_write` and `map_size`.
- Of the `!{IO}` vector externs, `vector_push` and `vector_truncate` change a length;
  `vector_mutset` and `vector_get_direct` do not.
- `vec_make_filled` writes `out->len = n` on the vector it creates, so it does not keep lengths,
  yet `safe_io_externs` lists it. The entry is dead: `vec_make_filled` is pure
  (`stdlib/mutable.sprout`), and `note_callee_kind` accepts a pure extern before it consults the
  list. Stage 4 drops it.

It also showed the two ways a check goes wrong:

- A varargs call read as an indirect call makes every function trigger.
- Treating every external as safe passes `swapcontext`, so `task_yield` comes out as
  non-triggering while it runs other tasks.

The prototype predates the escaping-function rule, the precise length rule and `mem2reg`; the
check's own fixtures (premise 1) replace it.

### 4.3 Platforms and configurations

An entry must hold for every runtime build a program can link with.

- **Configurations.** The default flags, `-DSPROUT_GC_PROFILE` and `-DSPROUT_GC_AGEPROF_STORES`
  (the justfile's profiling recipes). The check derives each and requires every entry in all of
  them.
- **Platforms.** A host compiles only its own `#if` arms, so the check runs on each platform for
  that platform's arms. The module is the same everywhere, and it must pass everywhere:
  - Linux: in the `test-gates` job, through `ci-fast-gates`' `GATES` list; the required `test`
    verdict fails when it does.
  - macOS: in the pre-commit hook, and in the `macos` job. The job is advisory (`ci.yml`), so an
    entry false only on macOS, committed from a Linux host, fails a check that does not block a
    merge, until the job is required.
  - Windows: the runtime does not compile there yet (`ci.yml`, the `windows` job's W1–W3
    milestones). Its arms (`SwitchToFiber` in `sprout_context.h`, `WSAPoll` in `sprout_poll.c`)
    join the check at W3.

A candidate becomes an entry only after the check passes on Linux and macOS.

### 4.4 Consumers

| today | after |
|---|---|
| `is_nonallocating_read`, `is_nonallocating_unboxed_read` | `runtime_facts.cannot_trigger_gc` |
| `is_noreturn_extern` | `runtime_facts.never_returns` |
| `unboxed_maybe_extern_name` | `runtime_facts.unboxed_symbol` |
| `ir_header`'s eight `i128` `decl` lines | built from `runtime_facts.unboxed_symbols` |
| `safe_io_externs` | `runtime_facts.keeps_vec_lengths` |
| `op_triggers_gc`'s five arms | `runtime_facts.cannot_trigger_gc` on `str_eq`, `str_compare`, `print_str`, `eprint_str`, `sprout_tag`, `sprout_field` |

Each consumer asks where it asks today: `ir_rooting` in `calls_nonallocating_extern`,
`op_triggers_gc` and the noreturn-block check, `ast_to_ir` in the CPR routers beside
`callee_name_shadowed`, `bce` in `note_callee_kind` after `tables.fn_names`. The IR does not
change shape. `IRCall` and `IRCallUnboxed2` keep their callee strings, and the facts are looked
up by C symbol. A user `extern fn` that redeclares a runtime name must have an alpha-equal scheme
(`infer.sprout`, the extern duplicate check) and binds the same C symbol, so the same facts apply.

A lookup applies only to a name the program does not define. Today `op_raw_callee` consults the
list before the callee is resolved against the program's functions, so a bare-named Sprout
define matching an entry (a prelude function, or a `no_prelude` top level) would be taken for the
C function. Only the link's `duplicate symbol` error stops that, and `--emit-ir`, golden IR and
the IDE never link. Each consumer already holds the program's function names (`summary`,
`tables.fn_names`) and checks them first.

`bce` sees Sprout names. `eprint` has a C symbol, a bridge for old codegen that
nothing emitted today calls (the seed and the golden IR have no `@eprint(`). `ast_to_ir` lowers
`eprint` and `print_str` to `eprint_str` and `print_str`, and `note_callee_kind` maps each name to
those symbols before the lookup, through one function beside that lowering, so a loop that prints
stays versioned.

### 4.5 Measurement

Trusting the 31 candidates other than `panic` (which the noreturn exemption already covers), on a
scratch branch off `c6cd5942`, macOS:

| | before | after |
|---|---|---|
| `sprout_gc_pop_roots` calls, golden IR (75 programs) | 12,404 | 11,671 (−5.9%) |
| same, the compiler | 21,901 | 21,500 (−1.8%) |
| same, `examples/sentry_api.sprout` | 1,241 | 979 (−21%) |
| compiler compiling itself (s, 6 runs each) | 3.68–4.14 | 3.73–3.96 |
| `json.parse`, 216 KB × 50, median of 8 / of 10 runs (ms) | 619 / 647 | 554 / 590 |

The two IRs differ only in root pushes, pops and the stack saves of root frames. The JSON
parser's root pops halve (386 → 182); `json.sprout` calls `str_starts_with_at_byte`,
`str_byte_len`, `str_find` and `str_len` at 60 sites. So the gain is about 10% on string scanning
(JSON, HTTP, parsers) and nothing on the compiler. Stage 2 records the benchmark under `bench/`.

## 5. Stages

Each stage is one PR, and each moves its lists into the module unchanged before anything else.

1. **Module, check, GC and noreturn facts.** Add the module, the check with its GC, never-returns
   and escaping-function rules and their fixtures, and
   `just runtime-facts-check` in `ci-fast-gates`' `GATES` list, the `macos` job and the
   pre-commit hook. The hook today exits unless a `.sprout`/`.spr` file is staged and leaves
   heavier validation to CI; it gains an arm for staged `runtime/*.c` or module edits, and its
   policy comment says so. Move `is_nonallocating_read`, `is_nonallocating_unboxed_read`,
   `is_noreturn_extern` and `op_triggers_gc`'s five arms into the module, behind the
   program-defines-it guard (§4.4). The IR does not change.
2. **Trust the candidates.** Add the GC fact-check build (premise 10), then add to
   `cannot_trigger_gc` every candidate the check passes on Linux and macOS that is not in
   `never_returns` (the noreturn exemption covers those). Golden IR changes:
   fewer roots around those calls. The diff is read in full before regenerating (AGENTS.md DoD
   #12), and `rooting-cost-gate` should improve or hold. `bench/` gets the JSON benchmark and its
   results.
3. **Unboxed variants.** Move `unboxed_maybe_extern_name` and the `decl` lines. The IR does not
   change. `cpr-declare-check` stays.
4. **Vector length.** Add the length rule to the check, with its fixtures and the length oracle
   in the fact-check build (premise 10). Only once the check accepts every current entry less the
   dead `vec_make_filled` (§4.2), move `safe_io_externs`, add the `print`/`eprint` mapping (§4.4)
   and a BCE test that a loop calling `print` stays versioned. If the check rejects an entry, the
   stage stops there and the rule is revisited. The IR does not change. Stages 1–3 do not depend
   on this rule, and it serves the stage with the least payoff, so it lands last and alone.

## 6. Impact

- **Syntax, typing, diagnostics:** none.
- **Compatibility:** none for programs. Golden IR changes in stage 2. The seed changes at every
  stage.
- **Cost on runtime changes.** A runtime edit that makes an entry false fails the pre-commit hook
  (nine `clang -O0` compiles, one per file and configuration) and `test-gates` (Linux), naming
  the entry and the path; the fix is to drop the entry or change the C. An edit that only adds
  candidates costs nothing: they are printed, not required. An entry added or dropped is a
  compiler-source edit and goes through the seed gate.
  `docs/gates.md` gets the check and this cost.
- **Docs that name the lists**, updated at the stage that moves each:
  `docs/compiler-internals.md` (§Type-aware GC rooting and the CPR section),
  `docs/unsafe-primitives-v0.md`, `docs/growable-mutvec-v0.md`, `docs/byte-buffer-v0.md`,
  `docs/persistent-array-v0.md`, `docs/ranges-v0.md`,
  `docs/phase-d-numeric-fastpath-design-2026-07-11.md`, `docs/unboxed-adt-returns-v1-draft.md`,
  `docs/bounds-check-elimination-v0.md` (which lists the members of `safe_io_externs` without
  naming it, so a search for the identifier misses it). Stage 1 also fixes
  the comment on `IRCallUnboxed2` in `sprout_ir.sprout`, which still says the regex and terminal
  variants allocate; both were deleted.
- **BACKLOG:** stage 4 closes "Runtime-function facts are hand-written, unchecked name lists".

## 7. Later: lowering recognizers

`recognize_vector_op` (`VecOp`), `recognize_string_builtin` (`StringBuiltin`),
`is_call_site_intrinsic` and `is_bits_intrinsic` are the remaining name lists. They decide what
`ast_to_ir` expands inline (`print_str`, `eprint`, `to_double`, the bits ops, `unsafe_pure`, the B1 vector
accesses), which is a compiler choice, not a fact about C. Folding them into one `Intrinsic` ADT
and one parse would leave one place for those names too. That is a separate design, and so is
inlining more runtime calls as IR ops (`vector_length` as a guard plus `IRVecLenD`).

## 8. Premises and tests

Each entry is trusted because the check passed, so each premise the check rests on has a test.

1. **The check reads calls and stores correctly.** Unit tests over fixtures written in C and
   compiled through the check's own pipeline (§4.1), not hand-written `.ll`, which would be SSA
   already and pass while real `-O0` output is missed: a direct call, a varargs call, an indirect
   call, inline assembly, a call to `swapcontext`, recursion, a callee in another file, two
   files with a static of one name, a function reaching `sprout_gc_collect` through three levels,
   an escaping function stored into a struct field, an unannotated wrapper around `exit` whose
   caller then returns, and `len` writes through a field-0 GEP, the base pointer, an `i8` offset,
   an `inttoptr`, `memset` (which is `__memset_chk` on macOS), a handle forwarded as `i64` to
   `vector_push`, a raw word-0 write through a handle, an address-taken local reloaded after a
   call, and two casts of one handle. A header write at offset −8 must not count, and a
   sweep-shaped function (a dead payload treated as a `VectorVal`, then written at word 0) must
   pass only through the collector exemption.
2. **An unknown callee may trigger GC.** `test_ir_rooting.spr` already roots across calls to `foo`
   and `bar`, and Test 21 (an unknown `_unboxed` name stays a trigger). They stay.
3. **Moving a list keeps its meaning.** Each move stage checks the module's set equals the deleted
   list (less `vec_make_filled`, §4.2), and T17–T19, Test 20 and Test 22 stay. T18
   (`vector_get` roots) stays as the case that tells a boxed read from its `_unboxed` variant.
   Test 28's comment calls `panic` an unknown extern; it stays out of `cannot_trigger_gc`, so the
   test keeps its meaning, and the comment is
   corrected.
4. **A green-thread switch runs other code.** A check test that `task_yield` and `__task_await`
   can trigger GC, and a `SPROUT_GC_STRESS=1` run of a program whose task yields while holding a
   heap value.
5. **The library list is right.** The IR cannot say which parameter is a function pointer: with
   opaque pointers every pointer is `ptr`. So the check fails any call to a library-list function
   that passes `ptr @<function>`, which is where a callback-taker shows itself. A test also
   checks that every entry is declared in some configuration, so a stale or misspelt name is
   noticed. Whether an entry could take a callback from a call the runtime does not make yet is
   left to review of the list.
6. **One OS thread touches the Sprout heap.** `dns_resolver_thread` is the only other thread and
   writes only its own request; the escaping-function rule checks it. The facts assume no
   collector runs concurrently. A multi-core runtime must revisit them.
7. **No Sprout frame resumes after a function that never returns.** `ir_rooting`'s noreturn-block
   exemption already rests on this, and so does `exit` on the library list. "Task-boundary panic
   isolation" in BACKLOG would break it: an unwound task's spawner resumes. That entry must remove
   the exemption, or restrict `never_returns` to functions that end the process, before it lands.
   Both entries say so.
8. **`-O0` IR stands for the shipped build.** The check reads `-O0`; programs link the runtime at
   `-O0` or `-O2`. Optimization inlines, removes calls and adds calls to library routines such as
   `memcpy`; it does not add a call to the collector or an indirect call the source lacks. `just
   test-stress` and `just task-io-smoke`, which link the runtime at `-O2` and run under
   `SPROUT_GC_STRESS=1`, are where a counterexample would surface.
9. **The facts hold on each platform the check runs on.** Linux, required, and macOS (§4.3).
10. **A wrong entry is caught at run time.** Stress runs cannot see one: they collect at
    allocations, and the danger is an entry whose call collects anyway. A fact-check build, from a
    compiler flag, brackets every call to a `cannot_trigger_gc` entry with a counter the collector
    reads, and the collector aborts when it is non-zero, except for the `"atexit"` collection:
    every entry can reach `sprout_fail`, whose `exit` runs `sprout_gc_collect`, and that
    collection resumes nothing. The same build emits `llvm.trap` before the `unreachable` after
    each call to a `never_returns` entry. From stage 4 it also re-reads `len` after each call to
    a `keeps_vec_lengths` entry inside a versioned loop and traps when it differs from the length
    the loop was versioned on; without that, a wrong length entry drops a bounds check silently.
    `just test-stress` uses the build. The counter is a runtime global; adding it needs approval
    (AGENTS.md, Builtin vs Stdlib).
11. **The collector writes no live vector's length.** The length rule's one exemption (§4.2)
    rests on it: a collection writes only dead objects and runs no Sprout code. The check fails if
    `sprout_gc_collect_with_reason` is missing, so a renamed collector cannot widen the exemption
    unnoticed, and the sweep-shaped fixture (premise 1) passes only through it. At run time, the
    length oracle under `SPROUT_GC_STRESS=1` sees any collection inside a `keeps_vec_lengths`
    call, such as `ref_new`'s, that changes a live length.

Every stage runs `just test`, `just ci-fast-gates` and `just test-stress`.

## 9. Rejected: generating the module

The first version of this design generated the module from the C instead of checking it. It
loses on three counts:

- **No host can produce it.** An entry must hold on every platform, and a host derives only its
  own `#if` arms. Generating on macOS would commit macOS facts; generating on Linux needs a
  container on every runtime change; committing the intersection needs both. A generated module
  also needs a second gate for precision (derived equals committed) and a list of exclusions for
  facts true on one platform only. Checking needs one rule, committed ⊆ derived, run wherever.
- **The gain is narrow.** §4.5: the candidates buy about 10% on string scanning and nothing on
  the compiler. Regenerating on every runtime change pays for precision no consumer measures.
- **An edit is reviewable.** Adding an entry is a one-line diff with a check that names why it
  holds. A regenerated module changes as a side effect of unrelated C edits.

`[@@noalloc]`-style annotations on Sprout `extern fn` declarations were also rejected: they are
the same claims, unchecked, spread over the prelude and the stdlib modules.
