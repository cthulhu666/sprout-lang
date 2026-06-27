# Runtime Review — `runtime/sprout_runtime.c` (2026-06-27)

Full-subsystem audit of the 7143-line C runtime. Method: baseline automation
(GC safety linter, `-Wall -Wextra -Wconversion`, clang static analyzer,
`just test-stress`, `just cpr-differential-check`) + seven parallel
subsystem reviews tiling the whole file + manual verification of every
reachable finding.

**Follow-up status (same branch):** F1 (`tcp_fail`/`sprout_builtin_fail_detail`
`noreturn`) and F-NET-1 (global `SIGPIPE` ignore) are **FIXED** with a C-runtime
regression test (`tests/c_runtime/sigpipe_ignored.c` + a `noreturn` source
guard in `run.sh`). All other findings remain open recommendations.

Coverage (all line ranges read): 206–480 (signals), 480–1400 (GC core),
1593–3260 (process/IO), 3259–4310 (strings), 4310–5440 (crypto/TLS),
5440–6690 (AVL map / NativeSet / bytes), 6777–7143 (networking).

## Verdict

The runtime is **largely well-hardened** — non-moving mark/sweep GC, AVL map,
NativeSet, process spawning, string allocation, and TLS certificate/hostname
verification are all sound, and clang's analyzer reports **zero** true
memory-safety bugs on normal paths once `tcp_fail` is understood as
non-returning. The exceptions are concentrated in the two newest/least-mature
subsystems:

- **1 High:** the TCP socket builtins kill the process on `SIGPIPE` (F-NET-1).
- **Several Medium:** two reachable OOB reads in UTF-8 string handling sharing
  one root cause (F2, F3), a suspected GC silent-untrace (F4), missing socket
  timeouts and `EINTR` retry (F-NET-2/3), and no pinned minimum TLS version
  (F-TLS).

## Baseline (all green)

- GC safety linter: 263 functions, 0 issues. Caveat: the linter under-reports —
  `scripts/gc_safety_check.sh:133` clears all tracked vars if any `SPROUT_HANDLE(`
  precedes the GC call, so its silence is not proof.
- `clang -Wall -Wextra -Wconversion -Wsign-conversion -Wshadow`: 1 warning
  (`size_t`→`CFIndex` at :5150, see F-TLS-2).
- `just test-stress` (SPROUT_GC_STRESS=1): 4 gated tests PASS. Coverage is
  narrow — only ctors/match/closures/ir_rooting run under stress.
- `just cpr-differential-check`: OK (only allowlisted declare divergences).

## Static-analyzer result — the key diagnostic move

Initial clang analyzer run: **145 warnings**. After *experimentally* annotating
`tcp_fail` `noreturn` (see F1): **16 warnings**, of which **all 16 are false
positives** (15 temp-root-pool `StackAddressEscape`, 1 base64 `size==0⟺len==0`
correlation the analyzer can't model). The 129 eliminated warnings were phantom
use-after-free / double-free / null-deref reports caused solely by the analyzer
assuming execution continues past `tcp_fail`. **This annotation was
subsequently applied** (F1, see Findings) — the 16/16-false-positive analyzer
run is now reproducible on the committed tree.

---

## Findings (by severity)

### F-NET-1 — HIGH [FIXED]: TCP socket writes kill the process on SIGPIPE

Fixed: added a `__attribute__((constructor)) sprout_ignore_sigpipe_ctor` that
calls `signal(SIGPIPE, SIG_IGN)` before `main` (constructor, not
`sprout_set_argv`, so it holds regardless of codegen entry point). Regression:
`tests/c_runtime/sigpipe_ignored.c` (asserts disposition is `SIG_IGN` and a
broken-pipe `write` returns `EPIPE` instead of dying). Note this also revives
the runtime's own `tcp_write_all` → `TcpWriteFailed` error path, which was dead
code without the fix.


- `runtime/sprout_runtime.c:7086` (`tcp_write`), `:7101` (`tcp_write_all`)
- `send()` is called with flags `0`, sockets carry no `SO_NOSIGPIPE`, and
  SIGPIPE is ignored only in narrow scopes: temporarily in the `proc_run` poll
  loop (`:1729`, restored `:1765`) and in the analysis-service path (`:2330`,
  guarded). It is **never** ignored globally.
- Trigger (normal, non-adversarial): a peer closes/half-closes, the program then
  `send()`s — e.g. `tcp_echo_serve` writes the echo at `:7137` after the client
  disconnects → process receives default `SIGPIPE` → terminated. Any socket
  server/client written in Sprout dies when its peer drops.
- Fix: `signal(SIGPIPE, SIG_IGN)` once at runtime init, or `MSG_NOSIGNAL` on
  every `send` / `SO_NOSIGPIPE` per socket.

### F2 — MEDIUM: OOB read on truncated trailing UTF-8 sequence

- `sprout_utf8_char_width` (`:3571`) returns the declared width (2/3/4) for a
  valid lead byte and only `tcp_fail`s on a structurally invalid one. Callers
  advance by that width *before* checking the continuation bytes fit before NUL.
- Trigger: a String whose content ends mid-codepoint (last byte `0xF0`, then the
  `'\0'`). `sprout_utf8_codepoint_count` (`:3583`) does `i += 4`, steps 3 bytes
  past the NUL, then `s[i]` reads adjacent heap until it hits a zero byte.
- Reachability CONFIRMED: `read_file` (`:1492`) returns file bytes as a String
  with **no** `utf8_validate` (validation exists only at `:6286`/`:6342` for the
  bytes→string path). A truncated/binary file → `str_len`/`str_slice`/
  `str_char_at` OOB read.
- Fix: clamp width to bytes-remaining-before-`'\0'` (or validate continuation
  bytes) before advancing/`memcpy`. Affects 3580-3598, 3706/3713, 3887-3895,
  3911-3919, 3991-3999, 4155-4156. (Or: validate UTF-8 in `read_file`.)

### F3 — MEDIUM: `str_char_at_byte` family has no upper bound on `byte_pos`

- `str_char_at_byte` (`:3984`), `str_char_width_at_byte` (`:4009`),
  `str_char_at_byte_unboxed` (`:3905`) check only `byte_pos >= 0`, then read
  `s[pos]` as the end sentinel. If `pos > strlen(s)`, `s[pos]` is OOB.
- The sibling `str_starts_with_at_byte` (`:4034`) *does* guard `if (pos > len)`
  — the family is inconsistent; the documented "lexer only advances by width"
  precondition (comment 3981-3983) is unenforced for other callers.
- Reachability CONFIRMED: `str_char_at_byte` is a public `extern fn`
  (`stdlib/prelude.sprout:983`). Any Sprout code calling
  `str_char_at_byte(s, huge)` reads OOB.
- Fix: add the same `strlen` upper-bound guard as `str_starts_with_at_byte`.
  Shares F2's root cause (trust in offsets + `'\0'` sentinel).

### F4 — MEDIUM (suspected): `find_ctor`→0-children silently untraces live OBJ

- `sprout_heap_child_count`, `:964-966`: for `SPROUT_HEAP_OBJ`, child count is
  `meta == NULL || meta->arity < 0 ? 0 : meta->arity`. A *live* OBJ with an
  unregistered tag (or `arity < 0`) traces 0 children → its field objects go
  unmarked → swept → dangling field → UAF. Fails silently-unsafe.
- Conditional: only fires if a codegen/registration path lets a live OBJ reach
  mark with a missing tag (normal operation registers every ctor). Defense.
- Fix: `tcp_fail` on `meta == NULL` for a live OBJ (fail loud, not silent-unsafe).

### F-NET-2 — MEDIUM: no socket timeouts (hang DoS)

- `accept` (`:7023`), `recv` in `tcp_read` (`:7040`) and `tcp_read_exact`
  (`:7064`): no `SO_RCVTIMEO`/`SO_SNDTIMEO` on listener/accepted/connected
  sockets (contrast the timeout-armed HTTP paths at `:5334`/`:5558`).
- Trigger: a client connects to `tcp_echo_serve` and sends nothing; `tcp_read`
  blocks forever; the single-threaded accept loop (`:7133`) stalls.
- Fix: `setsockopt(..., SO_RCVTIMEO/SO_SNDTIMEO, ...)` on accepted/connected fds.

### F-NET-3 — MEDIUM: `recv`/`send` not retried on EINTR

- `:7040, :7064, :7086, :7101`. A blocking `recv`/`send` interrupted by any
  signal returns -1/EINTR; in `tcp_read` (`:7041→7043`) and `tcp_write`
  (`:7087`) that calls `tcp_fail`, aborting the whole process; in the `_all`
  variants it returns a spurious error.
- Fix: `do { n = recv(...); } while (n < 0 && errno == EINTR);`.

### F-TLS-1 — MEDIUM: no minimum TLS protocol version pinned

- `:5350`–`:5397`. `SSLSetProtocolVersionMin[Max]` is never called, so the
  SecureTransport context accepts the legacy floor (TLS 1.0). A MITM can force a
  deprecated protocol on any `https://` request.
- Fix: after `SSLSetIOFuncs`, `SSLSetProtocolVersionMin(ctx, kTLSProtocol12);`.
- NOTE: cert chain + hostname verification are otherwise CORRECT in both the
  default and custom-CA (`SPROUT_HTTP_CA_CERT`) paths — verified, no "accept any
  cert" path, verify-result is never ignored.

### F1 — HIGH LEVERAGE [FIXED]: mark `tcp_fail` `noreturn`

Fixed: annotated `tcp_fail` (decl + def) and `sprout_builtin_fail_detail`
`noreturn`. Analyzer dropped 145 → 16 warnings (all 16 remaining are false
positives). Regression: a source-grep guard in `tests/c_runtime/run.sh` asserts
the annotation is present (matches the file's existing source-assertion pattern;
a `noreturn` annotation has no runtime-observable behavior to test directly).


- `:307` (decl), `:3472` (def). `tcp_fail` ends in `exit(1)` and never returns,
  but lacks `__attribute__((noreturn))` while the adjacent `sprout_abort_match`
  (`:308`) has it.
- Impact: the compiler/analyzer believe execution continues past every
  `tcp_fail` (~100 call sites), which buries real bugs under ~129 false
  positives and leaves the contract unexpressed.
- Recommended fix: annotate both decl and def (trivially correct — matches
  `sprout_abort_match`). Also annotate `sprout_builtin_fail_detail` (`:2222`),
  which always `tcp_fail`s, clearing the 2846/2906/2980/3048 phantom
  double-frees. NOTE: a runtime-C change does NOT need refresh-seed (seed is IR
  from `.sprout`); committing it needs the example canary + full `just test`
  per DoD.

### Low / robustness

- **F-NET-4** (`:7045-7047`): `tcp_read` NUL-terminates raw `recv` bytes and
  registers as `SPROUT_HEAP_CSTR` — embedded NULs truncate; `recv==0` (peer
  closed) is indistinguishable from a real empty read. Use a `BytesVal`+length
  (as `tcp_read_exact` does) for binary. Low.
- **F-TLS-2** (`:5150`): `size_t cert_len` → `CFIndex` (signed) at
  `CFDataCreate`; a ≥2GiB CA file becomes a negative length → UB. Local file,
  Low. Bound-check `cert_len <= LONG_MAX`.
- **Signal handler** (`:290`, `:297-299`): `sigaltstack`/`sigaction` returns
  unchecked — silent install failure regresses to silent SIGSEGV. Add checks.
- **Signal handler** re-entrancy: a *different* signal type (SIGBUS during
  SIGSEGV handling) re-enters the handler once → messy double output, not
  infinite. Add `static volatile sig_atomic_t in_handler;` guard or
  `SA_RESETHAND`.
- **Stack-overflow classifier** (`:228-234`): upper bound `a <= g_stack_hi` is
  loose — a wild pointer in the stack address range is mislabeled "stack
  overflow". Wrong-diagnostic only. Tighten to `a < g_stack_lo`.
- **GC-tuning env vars** (`:397-401, 421-423, 431-434, 450-453`): `strtoll`/
  `strtod` ERANGE not detected → silent garbage clamp accepted on overflow.
  `errno = 0;` + `|| errno == ERANGE`.
- **sproutd cmd** (`:3100, :3131`): single-quoted paths not escaped; a path
  containing `'` breaks out of `sh -lc`. Operator-controlled. Escape `'`→`'\''`
  or pass argv directly.
- **fd double-close** (`:2381-2382`) on `fdopen` failure — benign
  (single-threaded), still incorrect. Delete the redundant closes.
- **`BSTNode.size`/`bst_size`** are `int` (`:5835`) while `map_size` returns
  `long long` — silent overflow at ≥2³¹ entries. Widen or document the cap.
- **`register_ctor`** (`:3247`) doesn't validate arity ≤ 9 (SproutObj has
  f0..f8); a hostile arity>9 aborts cleanly at `:1041`, not corruption. Add the
  check for defense-in-depth.
- **Perf**: GC mark worklist is `free`d and re-grown from 1024 every cycle
  (`:1090-1093`); keep it allocated across cycles (reset `len=0`).

## Analyzer false positives dispositioned (do NOT act on these)

- `recv` fd==-1 (`:7040`): FP — `g_conn_used[conn]==1 ⟹ g_conn_fd[conn]>=0`
  (cross-array invariant the analyzer can't see).
- UAF (`:7045`): FP — `free(buf)` is in the `n<0` branch followed immediately by
  `tcp_fail`; the use at `:7045` is unreachable on that path.
- `CFDataCreate` leak (`:5150`): FP — `cf_data` is `CFRelease`d on all paths.
- 19 `StackAddressEscape` (vector/bst/bytes builtins): FP — every flagged
  function pops exactly what it pushes on every return path; the analyzer
  models the push but not `SPROUT_GC_POP_LOCALS`.
- base64 `out[j++]` null-deref (`:6769`): FP — `out` is NULL only when
  `size==0`, which happens only when `len==0`, when the loop never runs.
- The 65 null-deref + 32 null-arg warnings are the OOM-aborts-by-design stance,
  not bugs (base64_decode and the worklist realloc do check NULL — fine).

## Design observations (not bugs)

- **Manual shadow-root stack in C builtins.** Many builtins (vector_*, bst_*,
  bytes_*) hand-balance `SPROUT_GC_PUSH_*_LOCAL`/`SPROUT_GC_POP_LOCALS` instead
  of the `cleanup`-attribute `SPROUT_HANDLE`. All sites are balanced today, but
  the manual idiom is error-prone: a future early `return` without a matching
  pop silently creates a dangling GC root. The file's own comment (787-789)
  positions handles as the C-builtin mechanism. Migrating these would make the
  class structurally impossible. No action required now.

## Not exhaustively verified

- `SPROUT_HEAP_BUILDER` chunk marking: the bytes-builder shares chunks by
  pointer, so correctness depends on the GC marking builder chunks as live
  `BytesVal*`. Sweep at `:1167` suggests managed children are walked, but the
  specific builder-chunk mark path was not inspected end-to-end.
- Stress coverage is narrow (4 tests). F2/F3 warrant a fuzz test over
  `str_len`/`str_slice`/`str_char_at_byte` with truncated-UTF-8 content and
  out-of-range offsets.
