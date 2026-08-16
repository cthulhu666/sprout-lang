# Debugging Tools

Tools and protocols to reach for **when something is broken** in the Sprout compiler or bootstrap pipeline. For invariants to know before editing codegen, see [compiler-internals.md](compiler-internals.md). For the stage pipeline and trust chain, see [bootstrap-chain.md](bootstrap-chain.md).

## Diagnostic Phases (`--phase` CLI option)

| Phase | What it does |
|---|---|
| `scan-info <stdlib> <file>` | Calls `bundler.scan_source_info` and prints `module:`, `export:`, `ctor:` lines. Diagnoses module-name extraction bugs without running the full bundler pipeline. |
| `dump-qualify <stdlib> <file>` | Runs full collection + qualify and prints original→qualified name mapping per module, plus `ctx: EMPTY` or `ctx: populated` for each. A `ctx: EMPTY` line means `build_resolve_ctx` failed to find the module's path in `all_symbols` — all names stay unqualified, triggering the `[assert]` error. |
| `bundle <stdlib> <file>` | Runs the full bundle phase and prints qualified decl names. Gold standard for detecting qualify-stage regressions; output must be identical between stage-0 and stage-1 (verified by `tests/stdlib/test_bundler.spr`). |
| `effects <stdlib> <file>` | Prints one line per declaration — declared effect vs the effect its body was inferred to perform — plus a summary. Reports, never rejects: the v0 checker does not enforce the effect rules (spec §7). Read the count as a **lower bound**; nothing writes an inferred effect back to the env, so a mis-declared callee is flagged but its callers are not until it is annotated. Calibrated by `just effect-report-smoke` against `tests/effects/canaries.spr`; see `docs/effect-enforcement-v0.md`. |

An unrecognised phase is an error. It used to fall through to the default source
check and exit 0, so a typo ran something else and looked like it had worked.

**Troubleshooting `BundleErr("[assert] qualify_decl: FnDecl starts with '.'...")`:**
1. Run `--phase scan-info` to confirm `scan_source_info` returns a valid non-empty module name.
2. If the name looks correct, run `--phase dump-qualify` to find which module has `ctx: EMPTY`.

## Typeclass dictionary dispatch (`SPROUT_TRACE_DISPATCH`)

**Reach for this when a program SIGSEGVs or corrupts inside typeclass-generic
code** (a `where Ord k` / `Eq a` function, `to_string`, `++`/`mconcat`) — the
symptom of a *mis-resolved dictionary*: the wrong instance is threaded for a
constraint, so a value is dereferenced through the wrong type's dict. Dispatch is
unenforced, so this is a runtime crash, not a type error (see
[retro-dict-dispatch-soundness-2026-07-13.md](retro-dict-dispatch-soundness-2026-07-13.md)).

Set the env var and recompile; the compiler prints one line per constrained call
site to **stderr** (compile-time, not the program's output). Zero cost when unset.

```
SPROUT_TRACE_DISPATCH=1 ./build/compile_driver_bin_stage1 --emit-ir stdlib prog.spr >/dev/null
```

```
[dispatch] callee=vec_sort_by class=Ord var=$t680 $t677->Tuple2 $t680->Int path=precise-just -> Ord Int
```

Read it as: constraint `Ord` on var `$t680` (which resolved to `Int`) picked the
`Ord Int` dict via the `precise-just` branch. The `<var>-><resolved>` map shows
each constraint var's concrete type — an element-vs-key mismatch (e.g. the dict
head is `Ord Tuple2` while the key var resolved to `Int`) is the smoking gun.
Path tags ending in `(guess)` mark the order-dependent heuristics
(`scan_prog_to_fresh_for_instance`, `first_concrete_arg`) — a `(guess)` on a
constraint that should have resolved precisely is the soundness-hole signature.
The trace covers the parametric (`C k`) constraint arm of `inject_constrained_fn_dicts`.

**Loud precise-miss net.** The `scan_prog_to_fresh_for_instance(guess)` branch (the
PR #176 soundness hole) is now a hard error rather than a silent guess: a resolution
that falls to it reports `ambiguous typeclass dispatch: … refusing to guess a
dictionary`. This branch is corpus-dead (a full `SPROUT_TRACE_DISPATCH` sweep shows
0 hits), so the error should never fire on correct code — it exists to catch a
future regression (e.g. a tyvar-identity change that reintroduces the name-mismatch)
at compile time instead of miscompiling. `first_concrete_arg(guess)` is deliberately
spared (its guess is the legitimate concrete-constructor case). Escape hatch: set
`SPROUT_DISPATCH_STRICT_OFF=1` to revert to the legacy guess if you hit a false
positive.

**Automated guard.** The dict-passing verifier (`verify_dispatch.sprout`, run in
the check phase, `compiler.sprout`) turns this class of bug into a **compile
error**: it re-derives each constraint var's type from the callee's SOURCE
signature (its written params + `where`-clause, matched against the concrete arg
types — independent of the resolver) and rejects a call whose injected dictionary
head disagrees. Default-fatal; **escape hatches:**

- `SPROUT_VERIFY_DISPATCH_OFF=1` — downgrade a finding to a warning (for an
  uncovered false positive in the wild; please file it).
- `SPROUT_VERIFY_DISPATCH_STATS=1` — print `verified=N skipped=M mismatched=K`
  per compile. `verified` counts checked calls; `skipped` are the legitimately
  unverifiable ones (polymorphic/forwarded dict, no source signature, callee not
  a plain name). A resolver bug shows up as `mismatched>0` with a located error.

**Scope (phase 1 + phase 2a)** — it catches mis-resolution where a constraint
variable is fixed to a concrete type by either **the call's value arguments**
(phase 1 — e.g. #176's projected key `k = Int`) **or the call-site return type**
(phase 2a — `verify_call`/`build_theta_ret` match the callee's declared return
`TypeExpr` against the concrete `TCall` type, so return-type dispatch is now in
scope). It deliberately does NOT flag (skips, never a false alarm):

- forwarded/polymorphic dicts inside a generic function (the #141 shape — the
  constraint's truth is still a type variable);
- a constraint var pinned by neither the params nor the return (genuinely
  underdetermined → skip);
- class-method return dispatch via `TMethodRef` — the sig table is `TFnDecl`-based
  and the walker keys `TVar` callees, so method-ref return dispatch is uncovered;
- a *lowering*-discard bug (the historical `++`/`mconcat` null-fill), where the
  resolved dict is correct but dropped during IR emission — a lowering fault a
  post-resolve pass structurally cannot see.

The last (lowering-discard) motivates the pending IR-level **phase 2b** (BACKLOG
"Dispatch Soundness & Diagnostics" item 1).

## `just llvm-where <ll_file> <line>` — map an error line to its Sprout function

When `opt --passes=verify` (or clang) reports a malformed-IR error at line N of a large `.ll` file, this tool walks the file up from line N to the nearest enclosing `define` and prints the Sprout qualified name.

```
$ just llvm-where build/stage2.ll 130912
line 130912 in: @stdlib.compiler.codegen.emit_fn
```

Lambda wrappers also include the source comment header:

```
$ just llvm-where bootstrap/compile_driver.ll 263090
line 263090 in: @__sprout_lambda_1680_vec_sum_by  (header: ; __sprout_lambda_1680_vec_sum_by (in vec_sum_by))
```

Lines before the first `define` (module-level constant table):

```
$ just llvm-where bootstrap/compile_driver.ll 42
line 42 in: <module-level section — before first define>
```

**When to use it:** any time `opt --passes=verify` or `clang` emits an error with a raw line number and the IR file is too large to inspect manually. The `just refresh-seed` and `_build-stage` recipes both run `opt --passes=verify` before linking; failures there typically look like:

```
error: unable to create block named 'entry'
```

or phi/insertvalue type mismatches. Run `just llvm-where <ll_file> <N>` to identify the offending Sprout function, then search for it in the compiler source.

**Implementation:** `scripts/llvm_diag.sh` — single-pass awk; O(n) in file size, no temp files.

## Stack-overflow panic + backtrace

Compiled Sprout programs catch native stack overflow and report it instead of
dying with a silent `SIGSEGV`. The runtime installs its crash handler on an
**alternate signal stack** (`sprout_install_crash_handlers` in
`runtime/sprout_runtime.c`), which is what lets the handler run *after* the
thread stack is exhausted — without it the handler re-faults and the process
exits 139 with no output. On overflow you get:

```
[sprout] fatal: stack overflow - unbounded or too-deep recursion
0   prog   ... sprout_crash_handler
1   ...    ... _sigtramp
2   prog   ... stdlib.compiler.lexer.tokenize_from + 800
3   prog   ... stdlib.compiler.lexer.tokenize_from + 800
...
```

The repeated frame names the **recursing function** — the usual culprit is a
self-recursive function that the code generator failed to turn into a TCO loop
(it overflows only for large inputs, so small smoke tests pass). This is the
first thing to read when a large program crashes at exit 139; it pinpoints in
one run what an `lldb` backtrace cannot (the overflow faults mid-prologue, so
`lldb` typically can't unwind the blown stack at all).

**Notes:**
- macOS symbolises frames from the symbol table automatically; on **Linux**,
  named (vs bare-address) frames require linking the program with `-rdynamic`.
- Detection compares the fault address against the stack bounds captured at
  startup; a genuine wild-pointer fault falls through to a generic
  `[sprout] SIGSEGV` message plus the same backtrace.
- Regression: `just stack-overflow-smoke` (`tests/stack_overflow_smoke/`).

## Typed-codegen TCO gate (`just tco-runtime-smoke`)

The typed-codegen flip has landed: `--emit-ir` (the default) *is* typed codegen,
so the standalone flip meters (`tco-diff`, `flip-smoke`, `flip-readiness`) are
retired. The surviving guard for the "self-tail-recursion regresses only at
runtime scale" bug class — which the parity corpus structurally cannot catch (it
runs only small files) — is:

- **`just tco-runtime-smoke`** — compiles a deep tail-recursive fixture via
  `--use-ir-codegen` and requires it to *run to completion*. A non-TCO'd typed
  build either exhausts the GC root pool or overflows the native stack (one frame
  per iteration). This is a hard CI gate. When it fails, the runtime's
  stack-overflow panic names the recursing culprit on stderr (see
  `sprout_install_crash_handlers`).

The end-to-end "typed-built compiler self-compiles to a fixed point" property the
old `flip-readiness` checked is now covered by `just verify-bootstrap-fixed-point`
(the seed *is* typed-codegen output) plus `just build-stage2` + the stage-2 tests.

## 2-Step Bootstrap Protocol

**When this applies:** the committed seed (`bootstrap/compile_driver.ll`) predates a parser change. `just refresh-seed` calls `just bootstrap-from-seed` internally, which rebuilds the binary from the committed seed. If that seed has the old parser, the rebuilt binary cannot parse source files that use the new syntax — a catch-22.

**Why the protocol works:** by temporarily reverting the files that *use* new syntax (but not `parser.sprout` itself), you can run the current stage-1 binary (which has the old parser and can still parse old syntax) to compile a `compile_driver.sprout` that includes the new parser in its output IR. That IR becomes the updated seed, which has the new parser baked in.

**Steps:**

1. Temporarily revert the files that *use* the new syntax to old syntax on disk. Do **not** revert `parser.sprout`:
   ```
   git checkout master -- stdlib/compiler/compile_driver.sprout stdlib/compiler/lowering.sprout stdlib/prelude.sprout
   ```
   Adjust the file list to match what you changed.

2. Emit intermediate IR using the current stage-1 binary (which can parse the old syntax):
   ```
   ./build/compile_driver_bin_stage1 --emit-ir stdlib stdlib/compiler/compile_driver.sprout > /tmp/intermediate.ll
   ```

   > **Verify success before proceeding:** check that `/tmp/intermediate.ll` starts with `; Generated by sprout ir_lowering.sprout`.
   >
   > Current compilers report source errors on **stderr** and exit **nonzero**, so `set -e` or a `$?` check catches a failure here. Keep checking the first line anyway: in a 2-step bootstrap the binary doing the emitting is deliberately an *older* stage, and any stage built before 2026-08-07 writes errors to **stdout and exits 0** — which would put the error text into `/tmp/intermediate.ll` and then into the seed. The first-line check is the one indicator that works for both generations.

   Copy the verified output to the seed:
   ```
   cp /tmp/intermediate.ll bootstrap/compile_driver.ll
   ```

3. Restore the HEAD files reverted in step 1:
   ```
   git checkout HEAD -- <files reverted in step 1>
   ```

4. Run `just refresh-seed` — now succeeds because the seed contains the updated parser:
   ```
   scripts/memwatch.sh 4096 1 -- just refresh-seed
   git add bootstrap/compile_driver.ll
   ```

## Builtin-Removal Bridge Protocol

**When this applies:** removing a `long long`/`SproutUnboxed2` builtin from
`runtime/sprout_runtime.c` that the **committed seed** (`bootstrap/compile_driver.ll`)
still calls. This is the builtin-removal analogue of the 2-Step Bootstrap Protocol
above — that one covers new *syntax*; this one covers *removed runtime symbols*.

**Why it's a catch-22:** `just bootstrap-from-seed` links the old seed against the
new runtime. If the old seed still calls the removed builtin, linking fails with
`Undefined symbols: _<builtin>` — and a failed bootstrap can delete the existing
`build/compile_driver_bin_stage1`, removing your only working binary.

**The fix — a transitional bridge runtime** that temporarily holds BOTH the old
(being-removed) builtins and any new ones the new source needs:

1. Prefer to make the change additively first: refresh the seed BEFORE removing old
   builtins, whenever the ordering allows it. (Removing first is the mistake that
   triggers this protocol.)
2. If already removed: temporarily re-add the old C functions (keep the new ones)
   → `just bootstrap-from-seed` now links (the old seed's symbols resolve) →
   produces a bridge stage-1.
3. Emit the new seed with the bridge binary — it compiles the new *source* from
   disk, and the new source doesn't call the old builtins, so the new seed won't
   reference them:
   ```
   ./build/compile_driver_bin_stage1 --emit-ir stdlib stdlib/compiler/compile_driver.sprout > /tmp/seed.ll
   ```
   Verify: first line is `; Generated by sprout codegen.sprout` (or the current
   codegen header), `grep -c '<old_builtin>' /tmp/seed.ll` == 0, and
   `grep -c '<new_builtin>' /tmp/seed.ll` > 0.
4. `cp /tmp/seed.ll bootstrap/compile_driver.ll`; remove the bridge C functions
   again; `rm build/compile_driver_bin_stage1`; `just bootstrap-from-seed` now
   links (new seed + final runtime). Then `just refresh-seed` to fixed point.

**Two traps:**

- Remove **every** re-added bridge function, not just the obvious one. Leftover
  bridge functions are dead (the seed has 0 references, so `just test` passes
  green) — nothing local catches them except `check-approved-builtins`.
- `check-approved-builtins` is a **CI gate**, not part of `just test`. Run it
  locally for any runtime change: `mise exec -- just check-approved-builtins`. It
  fails if a `long long name(...)`/`SproutUnboxed2 name(...)` in
  `runtime/sprout_runtime.c` has no `runtime/APPROVED_BUILTINS` entry (or vice
  versa) — a leftover bridge function trips it.

## GC rooting-bug oracle (`SPROUT_GC_STRESS`)

**Reach for this when a typed-codegen program SIGSEGVs, aborts with
`non-exhaustive match`, or corrupts a value intermittently** — the signature of an
unrooted (or under-rooted) heap value being swept mid-use. `just test` /
`ir_runtime_parity.sh` run only at the **default GC threshold**, where a
use-after-free rooting bug can pass by timing luck (a false green); there is no
stress pass in the default suite.

**The oracle:** `SPROUT_GC_STRESS=1` (runtime `g_gc_stress`) collects on *every*
allocation, collapsing the timing window and turning a latent rooting bug into a
deterministic crash.

```
SPROUT_GC_STRESS=1 ./myprog
```

`SPROUT_GC_LINEAGE=1` (combine with `SPROUT_GC_STRESS=1`) poisons swept object
payloads (tag → sentinel) and stashes the free backtrace in the corpse; the next
read of a poisoned pointer aborts and dumps that backtrace — the alloc that
triggered the fatal collection, i.e. the unrooted-live-across site. In-process,
exact keying, no `lldb` needed. Build the runtime `-O1 -g` for readable frames.
This supersedes `just gc-trace`'s `lldb`-based free-trace below for most cases (its
history-keying is unreliable); `just gc-trace` remains for cases `SPROUT_GC_LINEAGE`
doesn't cover (it only poisons `SPROUT_HEAP_OBJ`; closures/vectors free via
`free()` directly).

**Diagnosis flow — is it a rooting bug or a value miscompile?**

```
SPROUT_GC_DISABLE=1 ./myprog   # true no-collect mode
```

`SPROUT_GC_DISABLE=1` truly disables the collector — unlike
`SPROUT_GC_THRESHOLD=<huge>`, which does **not** disable it (it still fires with
reason "threshold"; relying on a huge threshold as a substitute for "GC off" is a
known misdiagnosis trap). Crash under `SPROUT_GC_STRESS=1` **and** pass under
`SPROUT_GC_DISABLE=1` ⇒ a rooting bug, not a value miscompile. If it crashes under
both, or under neither, look elsewhere (value miscompile or an unrelated fault).

**Method for a typed-vs-direct-era divergence:** build the failing file both ways,
link, run each under `SPROUT_GC_STRESS=1`. If one path passes and the other
crashes, diff the two emitted ILs for the offending function — the passing path is
the reference for what rooting the failing path is missing.

**Tooling:** `just test-stress` runs a curated set of rooting-exercising typed
tests under `SPROUT_GC_STRESS=1` (wired into CI); `ir_rooting.op_triggers_gc` /
`op_produces_simple_heap` / `op_exposes_operands` are exhaustive over `IROp` (no
`_` catch-all), so a new IR op is a compile error until its GC properties are
classified — this prevents the silent-default class of rooting bug that hid
earlier gaps.

## String-invariant oracle (`SPROUT_GC_HDRCHECK`) — and why local green ≠ CI green

`SPROUT_GC_HDRCHECK=1` asserts, on every `str_byte_len` call, that a CSTR header's
recorded byte length equals `strlen` of the payload (`runtime/sprout_runtime.c`
`str_byte_len`). Since the header stores an explicit length, the two can only differ when
a String contains an **interior NUL** — so this is the executable form of the
`docs/spec-v0.md` invariant *"A `String` value is always valid UTF-8 and contains no NUL
byte"*. A violation aborts with, e.g.:

```
[sprout] HDRCHECK: str_byte_len aux=53 strlen=50
```

**It is OFF by default and ON in CI.** That asymmetry has already produced one wasted
review cycle: a fully green local run — `just test`, `task-io-smoke`, `c-runtime-test`,
`gc-safety-check`, examples, canary, golden IR, fixed point — followed by a red CI on this
assertion alone (PR #66, 2026-08-11). **Any change that alters what a `String` may hold, or
that routes external bytes through one, must be run locally under
`SPROUT_GC_HDRCHECK=1` before pushing.**

Reading it as a diagnosis rather than a flake: the abort names a producer, not a consumer.
`aux > strlen` means some builtin built a String from raw bytes without validating —
the W2 R2 class (`tcp_read*`, `proc_run`, `term_read_line`, `env_get`, `argv_get`,
`stdin_read_bytes`; see BACKLOG). The fix belongs at the producer, which per decision D4
means returning `Bytes` and letting `bytes_to_utf8` be the single validating choke point —
*not* making the downstream consumer tolerate the invalid value, which merely moves the
violation out of sight.

Minimal repro needing no sockets — **this one no longer reproduces, and that is the point:**

```
proc_run(["printf", "a\000b"])   # was: byte_length 3, length 1
SPROUT_GC_HDRCHECK=1 ./prog      # was: HDRCHECK: str_byte_len aux=3 strlen=1
```

`proc_run` returns `Bytes` since the R2 reduction landed (2026-08-11), so this aborts nothing.
Kept here because the *shape* is the lesson, and because what replaced it is worth knowing:
**there is no longer a cheap NUL-bearing repro among the remaining producers**, so do not go
looking for one and conclude the class is closed.

- `env_get` / `argv_get` cannot carry a NUL at all — the OS delimits both with NUL, so the byte
  cannot reach the builtin. They can still mint a String from **invalid UTF-8**, which HDRCHECK
  does *not* catch: it compares the header's `aux` against `strlen`, and a bad lead byte leaves
  those equal. That case surfaces later as `builtin str_utf8: invalid UTF-8 lead byte` from
  whichever walker touches the value first, arbitrarily far from the producer.
- `term_read_line` truncates at the NUL when it reads (measured: input `a\0b\n` yields
  `byte_length 1`), so it loses data rather than producing an inconsistent header. Silent data
  loss, no abort.

So HDRCHECK's coverage of this class is narrower than it looks: it is a reliable detector of the
NUL half and blind to the invalid-UTF-8 half. Treat `str_utf8` panics from unexpected places as
the same diagnosis with a different signal.

## Debugging self-hosted compiler-source miscompiles

Applies when the **stage-1 compiler compiling `stdlib/compiler/` source itself**
aborts (e.g. `runtime error: non-exhaustive match`) and the backtrace is
misleading or the fault is otherwise hard to localize.

**Rebuild the offending IR at `-O0 -g` for accurate frames.** A backtrace captured
against an `-O2`-optimized self-compile binary can be misleading: `clang -O2` can
place the symbol for the function *ending just before* the real culprit at the
crash address (`fn + 0` resolves to the wrong function — the next one in the
binary). Regenerate the self-hosted IR at the point of interest (e.g.
`./build/compile_driver_bin_stage1_or_stage2 --emit-ir stdlib
stdlib/compiler/compile_driver.sprout > /tmp/self.ll`) and link/run it at
`-O0 -g`; frames then point at the actual crashing function.

**Culprit-vs-victim isolation.** When several functions share one code path (e.g.
several call sites all routed through the same worker-emission or repack logic)
and only one of them is at fault, don't assume the function *named* in the crash
backtrace is the bug — it may be a **victim** reading corrupted state that a
sibling function (the **culprit**) wrote earlier. To isolate: exclude one suspect
function from the shared routing/transform at a time (e.g. drop it from the
"wanted" set that triggers the shared code path) and re-run; if the crash moves or
disappears, that excluded function was the culprit, not the one in the backtrace.
Combine with `SPROUT_GC_DISABLE=1` (see the GC rooting-bug oracle above) to first
rule out a rooting bug before chasing a value-miscompile hypothesis.

## Debugging compiled programs (DWARF + lldb)

Compiled Sprout programs support source-level debugging via LLVM DWARF metadata and `lldb` (or `gdb`).
Debug info is opt-in; release builds are unchanged.

**Building a debug binary**

```
mise exec -- just build-debug myprog.spr ./myprog_dbg
```

This compiles `myprog.spr` and links with `-g -O0` for LLVM-level debug info. (The
`--debug` flag is currently a no-op: Sprout source-level DWARF is not emitted by the
typed IR pipeline — a re-addable follow-up. lldb still resolves the generated LLVM IR.)
The resulting binary can be loaded directly into `lldb`:

```
lldb ./myprog_dbg
```

**Starting a debug session**

```
(lldb) b myprog.spr:10              # break at line 10 of myprog.spr
(lldb) run                      # start the program
(lldb) bt                       # print Sprout backtrace
(lldb) n                        # step to next instruction
(lldb) s                        # step into a call
(lldb) continue                 # resume execution
```

**Launching under lldb directly** (one-liner):

```
mise exec -- just debug-run myprog.spr
```

This compiles with debug info and opens `lldb` in one step.

**Inspecting values with the LLDB helper script**

Load `tools/sprout.lldb` for convenience aliases:

```
(lldb) command source tools/sprout.lldb
(lldb) br set -n "main.fact"        # dots confuse b's parser; use br set -n
(lldb) run
(lldb) register read x0        # first arg as raw i64 (Int = decimal value)
(lldb) call sprout_debug_adt($x0)   # print ADT constructor name + fields (depth 4)
(lldb) call sprout_debug_int($x0)   # print an Int/Bool value
```

`sprout_debug_adt` uses the per-constructor `field_kinds` table (populated at startup)
to decode each field:

```
Cons(42, Cons(1, Nil))
Just("hello")
True
```

**What works**

- Breakpoints by source file and line: `b myprog.spr:N` — line numbers match the original `.spr` file exactly
- Breakpoints by function name: `br set -n "main.fact"` — the `b` shorthand misparses dots; `br set -n` with quotes is required
- Sprout-attributed backtraces in `bt`: frame names are qualified Sprout function names (`main.add`, `main.main`)
- Instruction-level `n` and `s`
- `call sprout_debug_adt($x0)` / `call sprout_debug_int($x0)` for value inspection

**Known limitations**

- **User-module functions only**: stdlib and prelude functions do not carry debug metadata. `bt` shows "source not available" for any stdlib frame, which is expected — stdlib sources are not distributed with binaries.
- **ADT function bodies**: `fr v` (frame variables) is not available; inspect via `register read` and `sprout_debug_adt`.

